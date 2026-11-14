"""Turning a phone's scan (USDZ) into the file the 3D view reads (GLB), with Blender and no window.

    ok, reason = convert("scan.usdz", "model.glb")

Blender runs as a separate process with a time limit. `Converter` runs conversions on one background
thread, one at a time, and reports each step, so the scan store can move a scan's `model.status` through
`converting` to `ready` or `failed` (docs/scan-and-log-plan.md, 5.4 and C2).

Checked on this code's own tests with a stand-in for Blender. NOT yet run against a real Blender on the
laptop this was written on (it had none), and not against a real Object Capture file (unverified).
"""

import glob
import os
import queue
import shutil
import subprocess
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from scan_glb import BadGlb, bounds, file_format  # noqa: E402

BLENDER_SCRIPT = os.path.join(HERE, "blender_usdz_to_glb.py")
DEFAULT_TIMEOUT_S = 300.0
NO_BLENDER = ("Blender is not installed on this laptop. Install it from blender.org, or set EFFENTE_BLENDER "
              "to where it is, then convert again")


def find_blender(env=None):
    """Where Blender is, or None. In this order: the environment variable EFFENTE_BLENDER, `blender` on
    the PATH, then the usual place on macOS, then the usual place on Windows."""
    env = os.environ if env is None else env
    named = (env.get("EFFENTE_BLENDER") or "").strip()
    if named:
        return named if os.path.isfile(named) else None  # a wrong setting is not silently replaced
    found = shutil.which("blender", path=env.get("PATH"))
    if found:
        return found
    candidates = ["/Applications/Blender.app/Contents/MacOS/Blender"]
    for root in (env.get("ProgramFiles"), env.get("ProgramW6432")):
        if root:  # newest version first
            candidates += sorted(glob.glob(os.path.join(root, "Blender Foundation", "Blender*", "blender.exe")),
                                 reverse=True)
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def _last_message(text):
    """The reason our Blender script gave, if it gave one: its lines start with 'effente: '."""
    for line in reversed((text or "").splitlines()):
        line = line.strip()
        if line.startswith("effente: "):
            return line[len("effente: "):]
    return ""


def convert(source, target, timeout_s=DEFAULT_TIMEOUT_S, blender=None, env=None):
    """Convert the USDZ file `source` into the GLB file `target`. Returns (True, "") or (False, reason),
    where the reason is a plain sentence for a person. `target` is only ever a whole file: Blender writes
    beside it and the result is moved into place.

    `blender` is the program to run: a path, or a list holding a command and its first arguments (the tests
    use that for a stand-in). Left out, it is looked for with find_blender()."""
    if not os.path.isfile(source):
        return False, "the scan file is missing"
    command = blender if blender is not None else find_blender(env)
    if not command:
        return False, NO_BLENDER
    command = list(command) if isinstance(command, (list, tuple)) else [command]
    part = target + ".part.glb"  # Blender adds ".glb" to a name that does not end with it
    _remove(part)
    os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
    args = command + ["--background", "--factory-startup", "--python-exit-code", "1",
                      "--python", BLENDER_SCRIPT, "--", os.path.abspath(source), os.path.abspath(part)]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # Windows: no console window flashes up
    try:
        # stdin from nowhere: with no console (a windowed app, or the tests) Windows refuses to start a
        # process that would inherit the missing one ("The handle is invalid").
        done = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=timeout_s,
                              env=None if env is None else dict(env), creationflags=flags)
    except subprocess.TimeoutExpired:
        _remove(part)
        return False, f"the conversion took longer than {int(timeout_s)} seconds and was stopped"
    except OSError as e:
        _remove(part)
        return False, f"Blender could not be started: {e.strerror or e}"
    output = (done.stdout or b"").decode("utf-8", "replace") + "\n" + (done.stderr or b"").decode("utf-8", "replace")
    if done.returncode != 0:
        _remove(part)
        return False, _last_message(output) or f"Blender could not convert this file (it ended with code {done.returncode})"
    if not os.path.isfile(part):
        return False, _last_message(output) or "Blender finished but wrote no model"
    with open(part, "rb") as f:
        head = f.read(4)
    if file_format(head) != "glb":
        _remove(part)
        return False, "Blender finished but what it wrote is not a GLB file"
    os.replace(part, target)
    return True, ""


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def describe(glb_path):
    """What the scan's `model` holds once it is ready: {"bytes": ..., "bounds": {...}}. Raises BadGlb."""
    return {"bytes": os.path.getsize(glb_path), "bounds": bounds(glb_path)}


class Converter:
    """Conversions on one background thread, one at a time, in the order they were asked for.

        converter = Converter(on_change)
        converter.submit("scan-...", "scans/scan-.../scan.usdz", "scans/scan-.../model.glb")

    on_change(scan_id, status, details) is called from the worker thread:
        "converting", {}
        "ready",      {"bytes": 9120344, "bounds": {"min": [...], "max": [...]}}
        "failed",     {"reason": "a plain sentence"}
    A scan already waiting or being converted is not queued twice."""

    def __init__(self, on_change, convert=convert, timeout_s=DEFAULT_TIMEOUT_S):
        self._on_change = on_change
        self._convert = convert
        self._timeout_s = timeout_s
        self._jobs = queue.Queue()
        self._pending = set()
        self._lock = threading.Lock()
        self._thread = None
        self._stopped = False

    def submit(self, scan_id, source, target) -> bool:
        """Queue one conversion. False if this scan is already queued or running, or after stop()."""
        with self._lock:
            if self._stopped or scan_id in self._pending:
                return False
            self._pending.add(scan_id)
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="scan-convert", daemon=True)
                self._thread.start()
        self._jobs.put((scan_id, source, target))
        return True

    def busy(self) -> bool:
        with self._lock:
            return bool(self._pending)

    def stop(self, wait_s=5.0):
        """No more conversions are started. One that is running is left to finish (or to hit its time limit)."""
        with self._lock:
            self._stopped = True
            thread = self._thread
        self._jobs.put(None)
        if thread is not None:
            thread.join(wait_s)

    def _run(self):
        while True:
            job = self._jobs.get()
            if job is None:
                return
            scan_id, source, target = job
            with self._lock:
                if self._stopped:
                    self._pending.discard(scan_id)
                    continue
            try:
                self._report(scan_id, "converting", {})
                ok, reason = self._convert(source, target, timeout_s=self._timeout_s)
                if ok:
                    try:
                        self._report(scan_id, "ready", describe(target))
                    except (BadGlb, OSError) as e:
                        self._report(scan_id, "failed", {"reason": f"the converted model cannot be read: {e}"})
                else:
                    self._report(scan_id, "failed", {"reason": reason})
            except Exception as e:  # one bad scan must not stop the ones after it
                self._report(scan_id, "failed", {"reason": f"the conversion stopped with an error: {e}"})
            finally:
                with self._lock:
                    self._pending.discard(scan_id)

    def _report(self, scan_id, status, details):
        try:
            self._on_change(scan_id, status, details)
        except Exception:
            pass  # the store's own error; the next conversion still runs


if __name__ == "__main__":  # python3 platform/scan/scan_convert.py scan.usdz model.glb
    if len(sys.argv) != 3:
        sys.exit("usage: scan_convert.py scan.usdz model.glb")
    ok, why = convert(sys.argv[1], sys.argv[2])
    print(f"ready: {describe(sys.argv[2])}" if ok else f"failed: {why}")
    sys.exit(0 if ok else 1)
