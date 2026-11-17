"""Find effente devices on the laptop's local network, for the pairing window shown at start.

A device serves its own data on port 8765 (device/intake/stream.py). `find()` asks every address
of the laptop's local network for `GET /latest` on that port, this laptop itself (a board on
USB with `adb forward`), the WiFi address a board on USB says it has, and the addresses near the
one last connected to. What answers with a device's status is a device: the same test as the
Device page's Find boards (app/data/find_boards.py `probe`), so both find the same boards.

Limits, as built:
  - Only the 254 addresses that share the laptop's first three numbers (a /24) are asked, whatever
    the network's real size. A device elsewhere on a larger network is found only when it is on
    USB too, or near the address last connected to.
  - A network that keeps its clients apart (many guest and campus WiFis) lets nothing answer.
  - IPv4 only.
On 16 Nov 2026 it found the real board on a university WiFi, through the address last connected
to (the board was in another /24 than the laptop). The USB way is UNVERIFIED on a real board.

Standard library and Qt only.
"""

from __future__ import annotations

import ipaddress
import threading
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, Signal

from . import find_boards, scans

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


def candidates(last: str = "") -> list:
    """Every address to ask: this laptop first, then each local network's, then those near `last`
    (the address last connected to, app/data/device_address.py ADDRESS_KEY). Never the laptop's own."""
    own = set(scans._interface_addresses())
    hosts = [THIS_LAPTOP]
    for network in networks():
        hosts += [str(h) for h in network.hosts() if str(h) not in own]
    seen = own | set(hosts)
    return hosts + [h for h in find_boards.nearby([], last) if h not in seen]


def usb_addresses() -> list:
    """The WiFi address of each board on USB, as the board says it (through adb). Empty without adb."""
    try:
        return [address for _serial, address in find_boards.usb_boards()]
    except Exception:  # noqa: BLE001  an adb that fails is no board on USB
        return []


def probe(host: str, port: int = DEVICE_PORT) -> dict | None:
    """The device at this address, or None when nothing answers as one. A device is whatever gives
    a status with a device id under "status" on `/latest` (device/intake/stream.py `latest_json`):
    find_boards.probe reads it, the one place that knows the reply's shape."""
    status = find_boards.probe(host, port, CONNECT_TIMEOUT_S, READ_TIMEOUT_S)
    if status is None:
        return None
    ids = {k: status[k] for k in ("machine_id", "device_id") if isinstance(status.get(k), str) and status[k]}
    mode = status.get("mode")
    return {"url": f"http://{host}:{port}", "host": host, "mode": mode if isinstance(mode, str) else "", **ids}


def find(hosts=None, port: int = DEVICE_PORT, last: str = "") -> list:
    """The devices that answer, in the order of `hosts`. By default those are the WiFi addresses of
    the boards on USB, then `candidates(last)`. Takes a few seconds."""
    if hosts is None:
        hosts = list(dict.fromkeys([*usb_addresses(), *candidates(last)]))
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


def host_of(url) -> str:
    """The address in a device's URL as `probe` gives it: "10.0.0.7" of "http://10.0.0.7:8765". "" for None."""
    return str(url).split("//", 1)[-1].rsplit(":", 1)[0] if url else ""
