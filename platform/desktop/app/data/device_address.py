"""The address of a board typed by hand, made into the URL of its own port.

No Qt here. The board serves its port on 8765 (device/intake/stream.py), so
"192.168.43.20" means http://192.168.43.20:8765.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from urllib.parse import urlsplit

DEVICE_PORT = 8765
ADDRESS_KEY = "device_address"  # in the app's settings store (app/settings.py)
EMPTY = "Type the board's address first, for example 192.168.43.20."
NOT_AN_ADDRESS = "That is not an address. Use the board's address on the network, for example 192.168.43.20."
NO_LINK = {"url": "", "reachable": None, "message": "", "usb": []}
NO_BOARD = "No board to tell: connect one by its address or over USB first."
COMMAND_TIMEOUT_S = 6.0


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


def post_command(url: str, name: str, timeout: float = COMMAND_TIMEOUT_S) -> tuple[bool, str]:
    """POST {"command": name} to the board's own /command (device/intake/stream.py).
    Returns (whether the board did it, one sentence in the board's own words)."""
    request = urllib.request.Request(url.rstrip("/") + "/command", data=json.dumps({"command": name}).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
    try:
        try:
            with urllib.request.urlopen(request, timeout=timeout) as reply:  # noqa: S310  an http URL made by device_url
                body = json.loads(reply.read() or b"{}")
        except urllib.error.HTTPError as e:  # the board refuses with a 4xx and says why
            body = json.loads(e.read() or b"{}")
    except (OSError, ValueError) as e:
        return False, f"The board at {url} did not answer: {e}"
    if not isinstance(body, dict) or body.get("ok") is False or "error" in body:
        why = body.get("error") if isinstance(body, dict) else None
        return False, f"The board refused: {why or 'it did not say why'}."
    now = body.get("state") or body.get("mode")
    said = f'The board took "{name}"' + (f": it is now {now}." if now else ".")
    return True, said + (f" {body['note']}." if body.get("note") else "")
