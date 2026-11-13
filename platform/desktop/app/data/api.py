"""ApiClient: the receiver's HTTP routes the dashboard uses (platform/CONTRACT.md
sections 5 to 7), and StreamReader: /api/stream on a background thread.

The client is plain blocking urllib with short timeouts; DashboardState calls
it off the Qt thread. StreamReader hands events over through Qt signals, which
Qt queues onto the receiving object's thread.
"""

from __future__ import annotations

import http.client
import json
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request

from PySide6.QtCore import QObject, Signal

TIMEOUT_S = 5.0
STREAM_READ_TIMEOUT_S = 15.0  # the receiver sends a comment every 5 s; three missed means it is gone
RECONNECT_S = (0.5, 1.0, 2.0)  # waits between attempts, then 2 s for ever


class ApiError(Exception):
    """The receiver refused a request (its `error` text), or could not be reached."""


def _q(part: str) -> str:
    return urllib.parse.quote(part, safe="")


class ApiClient:
    def __init__(self, base_url: str, timeout: float = TIMEOUT_S):
        self.base = base_url.rstrip("/")
        parts = urllib.parse.urlsplit(self.base)
        self.host = parts.hostname or "127.0.0.1"
        self.port = parts.port or 80
        self.timeout = timeout

    def _call(self, method: str, path: str, body=None) -> dict:
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            with e:
                try:
                    reply = json.loads(e.read() or b"{}")
                except ValueError:
                    reply = {}
            message = reply.get("error") if isinstance(reply, dict) else None
            raise ApiError(message or f"HTTP {e.code}") from None
        except (OSError, ValueError) as e:
            raise ApiError(f"receiver not reachable: {e}") from None
        try:
            reply = json.loads(raw)
        except ValueError:
            raise ApiError("reply was not JSON") from None
        if not isinstance(reply, dict) or reply.get("ok") is not True:
            raise ApiError(str(reply.get("error", "reply was not ok")) if isinstance(reply, dict) else "bad reply")
        return reply

    # ---- section 5: reads -------------------------------------------------

    def state(self) -> dict:
        """GET /api/state."""
        return self._call("GET", "/api/state")

    def machine_records(self, machine_id: str, limit: int = 300, spectrum: bool = False, frames: bool = False) -> list:
        """GET /api/machines/<id>/records: oldest first."""
        query = f"?limit={int(limit)}" + ("&spectrum=1" if spectrum else "") + ("&frames=1" if frames else "")
        return self._call("GET", f"/api/machines/{_q(machine_id)}/records{query}")["records"]

    def machine_runs(self, machine_id: str, limit: int = 20) -> list:
        """GET /api/machines/<id>/agent-runs: newest first."""
        return self._call("GET", f"/api/machines/{_q(machine_id)}/agent-runs?limit={int(limit)}")["runs"]

    def machine_alarms(self, machine_id: str, limit: int = 20) -> list:
        """GET /api/machines/<id>/alarms: newest start first."""
        return self._call("GET", f"/api/machines/{_q(machine_id)}/alarms?limit={int(limit)}")["alarms"]

    # ---- section 6: answers -----------------------------------------------

    def answer_alarm(self, alarm_id: str, verdict: str, by: str, at: str | None = None,
                     finding: str | None = None) -> dict:
        """POST /api/alarms/<id>/answers. Returns the alarm with the answer."""
        body = {"verdict": verdict, "by": by}
        if at:
            body["at"] = at
        if finding:
            body["finding"] = finding
        return self._call("POST", f"/api/alarms/{_q(alarm_id)}/answers", body)["alarm"]

    # ---- section 7: settings ----------------------------------------------

    def put_settings(self, device_id: str, sensitivity: float | None):
        """PUT /api/devices/<id>/settings. None clears. Returns the settings, or None once cleared."""
        return self._call("PUT", f"/api/devices/{_q(device_id)}/settings", {"sensitivity": sensitivity})["settings"]


class StreamReader(QObject):
    """Reads /api/stream on its own thread, reconnecting until stopped.

    After every (re)connect it calls GET /api/state, as the contract says, and
    emits `snapshot` before any event read on that connection. Signals cross
    to the Qt thread queued, in order.
    """

    connected = Signal(bool)
    snapshot = Signal(object)
    event = Signal(str, object)

    def __init__(self, client: ApiClient, parent=None):
        super().__init__(parent)
        self.client = client
        self._stop = threading.Event()
        self._thread = None
        self._sock = None
        self._sock_lock = threading.Lock()
        self.connects = 0  # how many times a stream opened, for tests and status
        self._up = None  # what `connected` last said; None before the first attempt

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._up = None
        self._thread = threading.Thread(target=self._run, name="effente-stream", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._drop()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _drop(self) -> None:
        """Close the open stream at once; the reader thread then reconnects (or stops)."""
        with self._sock_lock:
            sock = self._sock
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def _say(self, up: bool) -> None:
        """Emit `connected` only when it changes: once per outage, once per recovery."""
        if up != self._up:
            self._up = up
            self.connected.emit(up)

    def _run(self) -> None:
        failures = 0
        while not self._stop.is_set():
            before = self.connects
            try:
                self._once()
            except (OSError, http.client.HTTPException, ApiError, ValueError):
                pass  # an open stream can end this way too, on a read timeout
            if self._stop.is_set():
                break
            self._say(False)  # however the attempt ended, the stream is closed now
            failures = 0 if self.connects > before else failures + 1
            self._stop.wait(RECONNECT_S[min(failures, len(RECONNECT_S) - 1)])

    def _once(self) -> None:
        """One connection, read until it ends. `connects` counts it if it opened."""
        conn = http.client.HTTPConnection(self.client.host, self.client.port, timeout=TIMEOUT_S)
        try:
            conn.request("GET", "/api/stream", headers={"Accept": "text/event-stream"})
            sock = conn.sock
            resp = conn.getresponse()
            if resp.status != 200:
                return
            sock.settimeout(STREAM_READ_TIMEOUT_S)
            with self._sock_lock:
                self._sock = sock
            if self._stop.is_set():
                return
            snap = self.client.state()
            self.connects += 1
            self._say(True)
            self.snapshot.emit(snap)
            self._read(resp)
        finally:
            with self._sock_lock:
                self._sock = None
            conn.close()

    def _read(self, resp) -> None:
        name, data = None, []
        while not self._stop.is_set():
            line = resp.readline()
            if not line:
                return  # the receiver closed the stream
            text = line.decode("utf-8", errors="replace").rstrip("\r\n")
            if text.startswith(":"):
                continue  # comment: keep-alive
            if text == "":
                if name is not None and data:
                    try:
                        payload = json.loads("\n".join(data))
                    except ValueError:
                        payload = None
                    if isinstance(payload, dict):
                        self.event.emit(name, payload)
                name, data = None, []
                continue
            field, _, value = text.partition(":")
            value = value.removeprefix(" ")
            if field == "event":
                name = value
            elif field == "data":
                data.append(value)
