"""The address of a board typed by hand, made into the URL of its own port.

No Qt here. The board serves its port on 8765 (device/intake/stream.py), so
"192.168.43.20" means http://192.168.43.20:8765.
"""

from __future__ import annotations

from urllib.parse import urlsplit

DEVICE_PORT = 8765
ADDRESS_KEY = "device_address"  # in the app's settings store (app/settings.py)
EMPTY = "Type the board's address first, for example 192.168.43.20."
NOT_AN_ADDRESS = "That is not an address. Use the board's address on the network, for example 192.168.43.20."
NO_LINK = {"url": "", "reachable": None, "message": "", "usb": []}


def device_url(text) -> tuple[str, str]:
    """(url, "") for a usable address, else ("", why not). Takes an address, a name,
    either with a port, or a whole http URL; a path after it is dropped."""
    text = str(text or "").strip()
    if not text:
        return "", EMPTY
    if "://" not in text:
        text = "http://" + text
    try:
        parts = urlsplit(text)
        host, port = parts.hostname, parts.port
    except ValueError:  # a port that is not a number, or brackets that do not close
        return "", NOT_AN_ADDRESS
    if parts.scheme != "http" or not host or parts.username or any(c.isspace() for c in text):
        return "", NOT_AN_ADDRESS
    if port == 0:
        return "", NOT_AN_ADDRESS
    if ":" in host:  # IPv6
        host = f"[{host}]"
    return f"http://{host}:{port or DEVICE_PORT}", ""
