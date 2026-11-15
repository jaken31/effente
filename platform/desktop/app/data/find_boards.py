"""Look for effente boards this laptop could read over WiFi, with no address typed.

Two ways, and no Qt here:

- A board plugged in by USB is asked (through adb) for its own WiFi address.
- The addresses near this laptop's own, and near the address last connected to,
  are tried on the board's port (8765). Whatever answers `/latest` with a
  `device_id` is a board, named as it names itself.

"Near" is the 254 addresses that share the first three numbers. On a phone
hotspot that is the whole network. On a large one (a university's) it is a small
part, on purpose: trying every address there would take minutes and look like an
attack to whoever runs it. A board elsewhere on such a network is found only
through USB, or by typing its address.
"""

from __future__ import annotations

import ipaddress
import json
import socket
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from . import paths
from .device_address import DEVICE_PORT, device_url

CONNECT_S = 0.4  # how long one address gets to take the connection
READ_S = 2.0  # how long a board that took it gets to answer /latest
WORKERS = 64
NETWORK, USB = "network", "usb"
# 192.0.0.0/29 is what a phone hotspot with no IPv4 of its own hands out: nothing else is on it.
_NOT_A_LAN = ipaddress.ip_network("192.0.0.0/29")


def board(address: str, how: str, status: dict | None = None, serial: str = "") -> dict:
    """One row of what was found. `answers` is whether its port answered from this laptop;
    the id, machine and mode are the board's own words, "" when it did not answer."""
    status = status or {}
    return {
        "address": address,
        "how": how,
        "serial": serial,
        "answers": bool(status),
        "device_id": str(status.get("device_id") or ""),
        "machine_id": str(status.get("machine_id") or ""),
        "mode": str(status.get("mode") or ""),
    }


def _usable(text) -> ipaddress.IPv4Address | None:
    """`text` as an IPv4 address on an ordinary local network, else None."""
    try:
        ip = ipaddress.ip_address(str(text))
    except ValueError:
        return None
    if ip.version != 4 or not ip.is_private or ip.is_loopback or ip.is_link_local or ip in _NOT_A_LAN:
        return None
    return ip


def laptop_addresses() -> list[str]:
    """This laptop's own addresses on its networks. Sends nothing."""
    found = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))  # picks the interface; a UDP connect sends no packet
            found.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        found += [info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)]
    except OSError:
        pass
    out = []
    for a in found:
        if _usable(a) and a not in out:
            out.append(a)
    return out


def nearby(own: list[str], last: str = "") -> list[str]:
    """The addresses to try: those sharing the first three numbers with each of this laptop's
    own, then with `last` (an address typed before, when it is numbers). Never the laptop's own."""
    bases = [_usable(a) for a in own]
    url, _ = device_url(last)
    if url:
        bases.append(_usable(url.split("//", 1)[1].rsplit(":", 1)[0]))
    out, seen = [], set(own)
    for ip in bases:
        if ip is None:
            continue
        for host in ipaddress.ip_network(f"{ip}/24", strict=False).hosts():
            text = str(host)
            if text not in seen:
                seen.add(text)
                out.append(text)
    return out


def probe(address: str, port: int = DEVICE_PORT, connect_s: float = CONNECT_S, read_s: float = READ_S) -> dict | None:
    """The status of the board at `address`, or None when nothing there is a board.
    Something else that happens to use the port does not count: it has no device_id."""
    try:
        socket.create_connection((address, port), timeout=connect_s).close()
        with urllib.request.urlopen(f"http://{address}:{port}/latest", timeout=read_s) as resp:
            latest = json.loads(resp.read(1_000_000))
    except (OSError, ValueError):
        return None
    status = latest.get("status") if isinstance(latest, dict) else None
    if not isinstance(status, dict) or not isinstance(status.get("device_id"), str) or not status["device_id"]:
        return None
    return status


def wifi_addresses(text: str) -> list[str]:
    """The WiFi addresses in the output of `ip -4 -o addr show` on a board (interfaces named wl...)."""
    out = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[1].startswith("wl") and parts[2] == "inet":
            address = parts[3].split("/")[0]
            if _usable(address):
                out.append(address)
    return out


def usb_boards(run=None, adb: str | None = None) -> list[tuple[str, str]]:
    """(serial, WiFi address) of each board on USB that is on a WiFi network.
    Empty without adb. `run` and `adb` are for tests."""
    usb_link = paths.usb_link_module()
    adb = adb or usb_link.find_adb()
    run = run or usb_link.run_adb
    if adb is None:
        return []
    out = []
    for serial in usb_link.attached(run([adb, "devices"]) or ""):
        for address in wifi_addresses(run([adb, "-s", serial, "shell", "ip", "-4", "-o", "addr", "show"]) or ""):
            out.append((serial, address))
    return out


def find(last: str = "", found=lambda b: None, stop: threading.Event | None = None, *,
         own=None, usb=None, look=probe, port: int = DEVICE_PORT) -> int:
    """Look both ways, calling `found(board)` for each board as it turns up (from worker
    threads). Returns how many nearby addresses were tried. `stop` set ends it early.
    A board on USB is listed even when its WiFi address does not answer from here: it
    is then on another network than this laptop. `own`, `usb` and `look` are for tests."""
    stop = stop or threading.Event()
    seen = set()
    for serial, address in (usb_boards() if usb is None else usb):
        if stop.is_set():
            return 0
        seen.add(address)
        found(board(address, USB, look(address, port), serial))
    addresses = [a for a in nearby(laptop_addresses() if own is None else own, last) if a not in seen]

    def one(address):
        if stop.is_set():
            return
        status = look(address, port)
        if status is not None and not stop.is_set():
            found(board(address, NETWORK, status))

    if addresses:
        with ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="effente-find") as pool:
            list(pool.map(one, addresses))
    return len(addresses)
