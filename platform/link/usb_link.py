"""Pick up a board plugged into this laptop by USB, with no address to type.

By hand this is two steps (device/app/README.md): `adb forward tcp:8765 tcp:8765`, then
start the platform with `--device http://localhost:8765`. This does both by itself and
keeps doing them: every CHECK_S it asks adb which boards are attached, forwards a free
local port to each board's port 8765, and runs a DeviceLink (device_link.py) on it. A
board that is unplugged has its link stopped; plugged in again, it gets a new one.

Anything adb lists counts as a board, a phone with USB debugging included: its link
says once that the device is not reachable and keeps trying, which sends nothing.

Needs `adb` on this laptop (macOS: `brew install android-platform-tools`). Without it
this says so once and does nothing.

Run against the board on 14 Nov 2026 (macOS, UNO Q on USB, the board watching): it was
found and forwarded, and 11 records and the model reached the receiver in 10 seconds.
Unplugging and plugging in again is UNVERIFIED on the board: that path was run only
against a stand-in for adb (tests/test_usb_link.py).

Standard library only.
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import threading
from pathlib import Path

from device_link import DeviceLink

DEVICE_PORT = 8765  # the device's own port (device/intake/stream.py)
CHECK_S = 2.0
ADB_TIMEOUT_S = 10.0  # the first call also starts adb's server
# Where adb is when it is not on PATH, as for an app started from the launcher on macOS.
ADB_PLACES = ("/opt/homebrew/bin/adb", "/usr/local/bin/adb", "~/Library/Android/sdk/platform-tools/adb",
              "~/Android/Sdk/platform-tools/adb")
NO_ADB = ("adb is not installed on this laptop, so a board on USB is not picked up by itself. "
          "Install it (macOS: brew install android-platform-tools), or start with --device URL.")


def find_adb() -> str | None:
    found = shutil.which("adb")
    if found:
        return found
    for place in ADB_PLACES:
        path = Path(place).expanduser()
        if path.is_file():
            return str(path)
    return None


def attached(text: str) -> list[str]:
    """The serials in `adb devices` output that are ready (state "device", not "offline" or "unauthorized")."""
    out = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == "device":
            out.append(parts[0])
    return out


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run_adb(command: list[str]) -> str | None:
    """The command's output, or None when it failed."""
    try:
        done = subprocess.run(command, check=False, capture_output=True, text=True, timeout=ADB_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


class UsbLink:
    """Watches adb for boards and links each one to the platform. start() runs it on a thread.

    `adb` is the program (found by itself when not given). `run`, `make_link` and `port`
    are there for tests."""

    def __init__(self, platform_url: str, adb: str | None = None, check_s: float = CHECK_S, log=None,
                 run=run_adb, make_link=DeviceLink, port=free_port):
        self.platform = platform_url
        self.adb = adb or find_adb()
        self.check_s = check_s
        self.log = log or (lambda m: print(f"usb link: {m}", flush=True))
        self.links: dict[str, DeviceLink] = {}  # by the board's adb serial
        self.ports: dict[str, int] = {}  # the local port forwarded to each board
        self._run, self._make_link, self._port = run, make_link, port
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> UsbLink:
        if self.adb is None:
            self.log(NO_ADB)
        elif self._thread is None:
            self._thread = threading.Thread(target=self.run, name="effente-usb-link", daemon=True)
            self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(ADB_TIMEOUT_S * 2)
            self._thread = None
        for serial in list(self.ports):
            self._drop(serial)

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                self.check()
            except Exception as e:  # noqa: BLE001  one odd answer from adb must not end the watch
                self.log(f"{type(e).__name__}: {e}")
            self._stop.wait(self.check_s)

    def check(self) -> None:
        """Ask adb once: link boards that came, stop the links of boards that went."""
        listed = self._run([self.adb, "devices"])
        if listed is None:
            return  # adb did not answer: keep what there is
        serials = attached(listed)
        for serial in [s for s in self.ports if s not in serials]:
            self.log(f"board {serial} unplugged")
            self._drop(serial)
        for serial in serials:
            port = self.ports.get(serial) or self._port()
            # Asked again every time: adb forgets a forward when the board drops off for a moment.
            if self._run([self.adb, "-s", serial, "forward", f"tcp:{port}", f"tcp:{DEVICE_PORT}"]) is None:
                continue
            if serial not in self.ports:
                self.ports[serial] = port
                self.log(f"board {serial} on USB: reading it at http://127.0.0.1:{port}")
                self.links[serial] = self._make_link(f"http://127.0.0.1:{port}", self.platform, log=self.log).start()

    def _drop(self, serial: str) -> None:
        link = self.links.pop(serial, None)
        if link is not None:
            link.stop()
        port = self.ports.pop(serial, None)
        if port is not None:
            self._run([self.adb, "-s", serial, "forward", "--remove", f"tcp:{port}"])  # fails when unplugged: fine
