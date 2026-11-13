"""The language model on this laptop: llama.cpp's `llama-server` as a child process.

The report agent (device/agent, `--backend local`) asks a model server on this
site for its report. This module starts one on 127.0.0.1:8081 with a model
file, says what state it is in, and stops it. It never starts a second server:
if the port already answers, that server is used and left alone.

No Qt here. `ModelServer.status()` asks the port, so it can take up to
PROBE_TIMEOUT_S; call it off the Qt thread when polling. One lock guards the
child, so `start`, `stop` and `status` may be called from different threads.

The flags are from `llama-server --help` (build 11429): `-m`, `--host`,
`--port`, `--offline`. UNVERIFIED: that a server still loading its model
answers HTTP 503. No model file was at hand to load one; a missing and a
broken model file were run for real, and both exit with code 1.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

HOST = "127.0.0.1"
PORT = 8081
URL = f"http://{HOST}:{PORT}"  # device/agent's default --llm-url
BINARY = "llama-server"
PROBE_TIMEOUT_S = 1.0
STOP_TIMEOUT_S = 5.0

SIZES = ("small", "large")
DEFAULT_SIZE = "small"
DEFAULT_FOLDER = "~/effente-models"

READY = "ready"  # the port answers with a model
LOADING = "loading"  # started, the model is not loaded yet
STOPPED = "stopped"  # nothing answers and this app has not started one
FAILED = "failed"  # this app started one and it exited
NOT_INSTALLED = "not_installed"
MODEL_MISSING = "model_missing"

# Requests go straight to the laptop itself, whatever proxy the environment names.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
# llama-server starts its log lines with a clock and a level: "0.00.625.026 E ".
_LOG_PREFIX = re.compile(r"^\d+(\.\d+)+\s+[A-Z]\s+")


def model_file(folder, size: str) -> Path:
    """The model file a size means: small.gguf or large.gguf in the folder."""
    if size not in SIZES:
        size = DEFAULT_SIZE
    return Path(folder or DEFAULT_FOLDER).expanduser() / f"{size}.gguf"


@dataclass(frozen=True)
class Status:
    state: str
    message: str  # a plain sentence for the Agents page
    ours: bool = False  # this app started the server and will stop it
    model: str = ""  # the file name this app started it with


def probe(url: str = URL, timeout: float = PROBE_TIMEOUT_S) -> tuple[str, str]:
    """Ask the model server for its models. Returns (READY | LOADING | STOPPED, why).
    STOPPED covers everything a report cannot run on, with the reason as a sentence."""
    url = url.rstrip("/")
    try:
        with _OPENER.open(f"{url}/v1/models", timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        if e.code == 503:
            return LOADING, f"The model server at {url} is still loading its model. Try again in a moment."
        return STOPPED, f"The program at {url} is not a model server (it answered HTTP {e.code})."
    except (TimeoutError, urllib.error.URLError, OSError) as e:
        reason = getattr(e, "reason", e)
        if isinstance(reason, TimeoutError) or isinstance(e, TimeoutError):
            return STOPPED, f"The model server at {url} did not answer within {timeout:g} seconds."
        return STOPPED, f"No model server is running at {url}."
    except ValueError:
        return STOPPED, f"The program at {url} is not a model server (its answer was not JSON)."
    listed = body.get("data") if isinstance(body, dict) else None
    if not isinstance(listed, list):
        return STOPPED, f"The program at {url} is not a model server (it lists no models)."
    if not listed:
        return STOPPED, f"The model server at {url} has no model loaded."
    return READY, f"The model server at {url} is running."


class ModelServer:
    """One llama-server child on `host:port`. `start(model)`, `status()`, `stop()`.

    `binary` is the program's name or path; a test passes a fake.
    """

    def __init__(self, binary: str = BINARY, host: str = HOST, port: int = PORT):
        self.binary = binary
        self.host = host
        self.port = port
        self.url = f"http://{host}:{port}"
        self._child: subprocess.Popen | None = None
        self._output = None  # the child's output, kept for the reason it stopped
        self._model: Path | None = None
        self._problem: Status | None = None  # why the last start did nothing
        self._was_ready = False  # the child answered at least once
        self._lock = threading.RLock()

    # ---- start and stop ---------------------------------------------------

    def start(self, model) -> Status:
        """Start llama-server with `model`, unless one already answers on the port.
        Returns at once: the model loads afterwards, and `status()` says when it is ready."""
        with self._lock:
            return self._start(Path(model).expanduser())

    def _start(self, model: Path) -> Status:
        self._problem = None
        if self._alive():
            return self._status()
        state, _ = probe(self.url)
        if state != STOPPED:
            self._forget_child()
            return self._status()  # one is already there: use it, do not start another
        path = shutil.which(self.binary)
        if path is None:
            self._problem = Status(
                NOT_INSTALLED, f"{self.binary} is not installed on this laptop. Install llama.cpp, then start again.")
            return self._problem
        if not model.is_file():
            self._problem = Status(MODEL_MISSING, f"The model file {model} is missing.")
            return self._problem
        self._forget_child()
        output = tempfile.TemporaryFile()
        try:
            self._child = subprocess.Popen(
                [path, "-m", str(model), "--host", self.host, "--port", str(self.port), "--offline"],
                stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT)
        except OSError as e:
            output.close()
            self._problem = Status(FAILED, f"{self.binary} did not start: {e.strerror or e}.")
            return self._problem
        self._output = output
        self._model = model
        self._was_ready = False
        return Status(LOADING, f"Loading {model.name}. A large model takes a while.", True, model.name)

    def stop(self) -> None:
        """Stop the server this app started. One that was already running is left alone."""
        with self._lock:
            child = self._child
            if child is not None and child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=STOP_TIMEOUT_S)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=STOP_TIMEOUT_S)
            self._forget_child()
            self._problem = None

    # ---- state ------------------------------------------------------------

    def owns_server(self) -> bool:
        """Whether a server this app started is running."""
        return self._alive()

    def status(self) -> Status:
        with self._lock:
            return self._status()

    def _status(self) -> Status:
        if self._alive():
            state, _ = probe(self.url)
            name = self._model.name if self._model else "the model"
            if state == READY:
                self._was_ready = True
                return Status(READY, f"Running with {name}.", True, name)
            return Status(LOADING, f"Still loading {name}. A large model takes a while.", True, name)
        if self._child is not None:  # started here, and it exited
            return Status(FAILED, self._why_it_stopped())
        state, why = probe(self.url)
        if state == READY:
            return Status(READY, "Running. It was started outside this app, so this app will not stop it.")
        if state == LOADING:
            return Status(LOADING, why)
        if self._problem is not None:
            return self._problem
        return Status(STOPPED, "Not running." if why.startswith("No model server") else why)

    def _alive(self) -> bool:
        return self._child is not None and self._child.poll() is None

    def _why_it_stopped(self) -> str:
        code = self._child.returncode
        line = ""
        if self._output is not None:
            try:
                self._output.seek(0)
                text = self._output.read()[-4000:].decode("utf-8", errors="replace")
                lines = [" ".join(_LOG_PREFIX.sub("", x.strip()).split()) for x in text.splitlines() if x.strip()]
                line = lines[-1] if lines else ""
            except (OSError, ValueError):
                pass
        said = f": {line}" if line else "."
        what = "stopped" if self._was_ready else "failed to start"
        return f"{self.binary} {what} (exit code {code}){said}"

    def _forget_child(self) -> None:
        if self._output is not None:
            self._output.close()
        self._child = self._output = self._model = None
