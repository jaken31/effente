"""Find effente devices on the laptop's local network, for the pairing window shown at start.

A device serves its own data on port 8765 (device/intake/stream.py). `find()` asks every address
of the laptop's local network for `GET /latest` on that port, and this laptop itself (a board on
USB with `adb forward`). What answers with a device's status is a device.

Limits, as built:
  - Only the 254 addresses that share the laptop's first three numbers (a /24) are asked, whatever
    the network's real size. A device elsewhere on a larger network is not found.
  - A network that keeps its clients apart (many guest and campus WiFis) lets nothing answer.
  - IPv4 only.
UNVERIFIED on a real board: run only against a stand-in server in the tests.

Standard library and Qt only.
"""

from __future__ import annotations

import ipaddress
import json
import socket
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, Signal

from . import scans

DEVICE_PORT = 8765  # device/intake/stream.py
CONNECT_TIMEOUT_S = 0.4  # a laptop on the same WiFi answers a connect well inside this
READ_TIMEOUT_S = 2.0
WORKERS = 64
THIS_LAPTOP = "127.0.0.1"  # a board on USB, forwarded with adb


def networks() -> list:
    """The local networks this laptop is on, as /24 networks, the one it routes from first."""
    found = [a for a in [scans.lan_address(), *scans._interface_addresses()] if scans._local(a)]
    out = []
    for address in found:
        network = ipaddress.ip_network(f"{address}/24", strict=False)
        if network not in out:
            out.append(network)
    return out


def candidates() -> list:
    """Every address to ask: this laptop first, then each local network's, without the laptop's own."""
    own = set(scans._interface_addresses())
    hosts = [THIS_LAPTOP]
    for network in networks():
        hosts += [str(h) for h in network.hosts() if str(h) not in own]
    return hosts


def probe(host: str, port: int = DEVICE_PORT) -> dict | None:
    """The device at this address, or None when nothing answers as one. A device is whatever gives
    its status on `/latest` with a machine id or a device id (device/intake/stream.py)."""
    try:
        socket.create_connection((host, port), timeout=CONNECT_TIMEOUT_S).close()
        with urllib.request.urlopen(f"http://{host}:{port}/latest", timeout=READ_TIMEOUT_S) as reply:
            status = json.loads(reply.read(1_000_000))
    except (OSError, ValueError):
        return None
    if not isinstance(status, dict):
        return None
    ids = {k: status[k] for k in ("machine_id", "device_id") if isinstance(status.get(k), str) and status[k]}
    if not ids:
        return None
    mode = status.get("mode")
    return {"url": f"http://{host}:{port}", "host": host, "mode": mode if isinstance(mode, str) else "", **ids}


def find(hosts=None, port: int = DEVICE_PORT) -> list:
    """The devices that answer, in the order of `hosts` (default `candidates()`). Takes a few seconds."""
    hosts = candidates() if hosts is None else list(hosts)
    if not hosts:
        return []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        return [d for d in pool.map(lambda h: probe(h, port), hosts) if d]


class Finder(QObject):
    """Runs `find()` off the window's thread. `found` carries the devices of the newest search only:
    a search still running when the next one starts is dropped."""

    found = Signal(list)

    def __init__(self, parent: QObject | None = None, find=find):
        super().__init__(parent)
        self._find = find
        self._search = 0
        self.searching = False

    def start(self) -> None:
        self._search += 1
        self.searching = True
        search = self._search

        def run():
            try:
                devices = self._find()
            except Exception:  # noqa: BLE001  an odd network must not end the app: it found nothing
                devices = []
            if search == self._search:
                self.searching = False
                self.found.emit(devices)  # queued to the window's thread by Qt

        threading.Thread(target=run, name="effente-find-devices", daemon=True).start()

    def cancel(self) -> None:
        """Drop the search that is running: its result is not sent."""
        self._search += 1
        self.searching = False
