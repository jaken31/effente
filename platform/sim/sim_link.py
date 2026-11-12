"""HTTP link from the simulated device to the platform (CONTRACT.md 1, 2, 3).

Standard library only. Every call has a short timeout and never raises:
network trouble comes back as a LinkResult with ok False.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

TIMEOUT_S = 0.8


@dataclass
class LinkResult:
    ok: bool  # the platform answered 200 with "ok": true
    reachable: bool  # the platform answered at all
    body: dict = field(default_factory=dict)
    error: str = ""


class Link:
    def __init__(self, base_url: str, device_id: str, timeout: float = TIMEOUT_S):
        self.base = base_url.rstrip("/")
        self.device_id = device_id
        self.timeout = timeout

    def _call(self, method: str, path: str, payload=None) -> LinkResult:
        data = None if payload is None else json.dumps(payload).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            # The platform is up but refused the request (400 and the like).
            try:
                body = json.loads(e.read() or b"{}")
            except (ValueError, OSError):
                body = {}
            msg = body.get("error", "") if isinstance(body, dict) else ""
            return LinkResult(False, True, body if isinstance(body, dict) else {}, f"HTTP {e.code} {msg}".strip())
        except (OSError, ValueError) as e:  # URLError, timeouts, refused, bad URL
            return LinkResult(False, False, {}, str(e))
        try:
            body = json.loads(raw)
        except ValueError:
            return LinkResult(False, True, {}, "reply was not JSON")
        if not isinstance(body, dict):
            return LinkResult(False, True, {}, "reply was not a JSON object")
        return LinkResult(body.get("ok") is True, True, body, "" if body.get("ok") is True else str(body.get("error", "")))

    def announce(self, model: dict) -> LinkResult:
        return self._call("POST", "/api/model", model)

    def send_records(self, records: list[dict]) -> LinkResult:
        payload = records[0] if len(records) == 1 else records
        return self._call("POST", "/api/records", payload)

    def check_in(self, body: dict) -> LinkResult:
        return self._call("POST", f"/api/devices/{urllib.parse.quote(self.device_id, safe='')}", body)
