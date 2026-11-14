"""ReceiverHost: the platform's receiver, and optionally the simulated device or
the link to a real device's own port, running inside the desktop app's process.

Both are the existing classes, used as they are: platform/server's `Receiver`
already serves from a background thread (`start()`/`stop()`), and
platform/sim's `Sim.run(keys)` takes its keyboard as an argument, so a queue
of keys stands in for the terminal and lets the app press keys and quit it.
"""

from __future__ import annotations

import io
import queue
import threading
from pathlib import Path

from . import paths


class _KeyQueue:
    """What `Sim.run` reads keys from, in place of a terminal."""

    active = False  # not a terminal: the sim prints one status line per message, to a sink

    def __init__(self):
        self.keys = queue.Queue()

    def read(self, timeout: float) -> str:
        try:
            return self.keys.get(timeout=timeout)
        except queue.Empty:
            return ""


class _LastLine(io.TextIOBase):
    """Keeps the sim's output from the terminal; remembers the last line for errors."""

    def __init__(self):
        self.last = ""

    def write(self, text):
        lines = [x for x in text.splitlines() if x.strip()]
        if lines:
            self.last = lines[-1]
        return len(text)


class SimHandle:
    """The running simulated device, as `DashboardState.sim()` hands it to pages."""

    def __init__(self, sim, keys: _KeyQueue, err: _LastLine):
        self._sim = sim
        self._keys = keys
        self._err = err

    def key(self, k: str) -> None:
        """Press one of the sim's keys (platform/sim/README.md): 1-6, h, v, m, t, p. Acted on within 50 ms."""
        if isinstance(k, str) and len(k) == 1 and k.lower() != "q":  # q would quit it; stop() does that
            self._keys.keys.put(k)

    def status(self) -> str:
        return self._sim.status()

    @property
    def last_message(self) -> str:
        """The sim's newest message to its operator, such as a sensitivity it took from the platform."""
        return self._err.last


class ReceiverHost:
    """Starts the receiver in this process, and the simulated device when `with_sim`.

    `data_dir` defaults to platform/server/data, where platform/report.py reads
    the device log; with the sim running, its `--log` and `--model-file` go
    there, as platform/run.py does. `port` 0 picks a free port; `.port` says
    which after `start()`. `device_url` is a real device's own port
    (http://DEVICE:8765): platform/link reads it and feeds this receiver.
    """

    def __init__(self, data_dir=None, port=8000, with_sim=False, case="normal", host="0.0.0.0",
                 sim_args=(), log=None, device_url=None):
        self.data_dir = Path(data_dir) if data_dir else paths.DEFAULT_DATA_DIR
        self.host = host
        self._port = port
        self.with_sim = with_sim
        self.case = case
        self.sim_args = list(sim_args)
        self.log = log or (lambda message: None)
        self.receiver = None
        self.sim = None  # SimHandle while the sim runs
        self.device_url = device_url
        self.link = None  # DeviceLink while it runs
        self._sim_thread = None
        self._keys = None

    @property
    def port(self) -> int:
        return self.receiver.port if self.receiver else self._port

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        """Start serving. Raises OSError when the port is taken."""
        if self.receiver is not None:
            return
        server = paths.server_modules()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.receiver = server.Receiver(self.host, self._port, str(self.data_dir), str(paths.WEB_DIR), log=self.log)
        self.receiver.start()
        if self.with_sim:
            self._start_sim()
        if self.device_url:
            self.link = paths.link_module().DeviceLink(self.device_url, self.url, log=self.log).start()

    def _start_sim(self) -> None:
        sim_device = paths.sim_modules()[0]
        args = sim_device.parse_args([
            "--url", self.url,
            "--case", self.case,
            "--log", str(self.data_dir / "device.jsonl"),
            "--model-file", str(self.data_dir / "model.json"),
            *self.sim_args,
        ])
        out, err = _LastLine(), _LastLine()
        sim = sim_device.Sim(args, out=out, err=err)
        self._keys = _KeyQueue()
        self.sim = SimHandle(sim, self._keys, err)
        self._sim_thread = threading.Thread(target=sim.run, args=(self._keys,), name="effente-sim", daemon=True)
        self._sim_thread.start()

    def stop(self) -> None:
        """Stop the sim and the device link, then the receiver. Safe to call twice."""
        if self.link is not None:
            self.link.stop()
            self.link = None
        if self._sim_thread is not None:
            self._keys.keys.put("q")
            self._sim_thread.join(timeout=5)
            self._sim_thread = None
            self.sim = None
        if self.receiver is not None:
            self.receiver.stop()
            self.receiver = None
