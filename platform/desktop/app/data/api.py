"""ApiClient: the receiver's HTTP routes the dashboard uses (platform/CONTRACT.md
sections 5 to 7), and StreamReader: /api/stream on a background thread.

The client is plain blocking urllib with short timeouts; DashboardState calls
it off the Qt thread. StreamReader hands events over through Qt signals, which
Qt queues onto the receiving object's thread.
"""

from __future__ import annotations

import http.client
import json
import os
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request

from PySide6.QtCore import QObject, Signal

TIMEOUT_S = 5.0
STREAM_READ_TIMEOUT_S = 15.0  # the receiver sends a comment every 5 s; three missed means it is gone
RECONNECT_S = (0.5, 1.0, 2.0)  # waits between attempts, then 2 s for ever
FILE_CHUNK_BYTES = 64 * 1024  # how much of a file upload or download is in memory at once
RANGE_PAGE = 2000  # records per request when reading a range: the receiver's most


class ApiError(Exception):
    """The receiver refused a request (its `error` text), or could not be reached."""


def _q(part: str) -> str:
    return urllib.parse.quote(part, safe="")


quote = _q  # for callers of ApiClient.get, post, put and get_bytes


def _query(query: dict) -> str:
    """`?a=1&b=2` from the values that are not None, or nothing."""
    pairs = [(k, "1" if v is True else "0" if v is False else str(v)) for k, v in query.items() if v is not None]
    return "?" + urllib.parse.urlencode(pairs, quote_via=urllib.parse.quote) if pairs else ""


def _error_text(e: urllib.error.HTTPError) -> str:
    """The receiver's `error` sentence from a refused request, else the status."""
    with e:
        try:
            reply = json.loads(e.read() or b"{}")
        except ValueError:
            reply = {}
    message = reply.get("error") if isinstance(reply, dict) else None
    return message or f"HTTP {e.code}"


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
        return self._reply(req)

    def _reply(self, req) -> dict:
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            raise ApiError(_error_text(e)) from None
        except (OSError, ValueError) as e:
            raise ApiError(f"receiver not reachable: {e}") from None
        try:
            reply = json.loads(raw)
        except ValueError:
            raise ApiError("reply was not JSON") from None
        if not isinstance(reply, dict) or reply.get("ok") is not True:
            raise ApiError(str(reply.get("error", "reply was not ok")) if isinstance(reply, dict) else "bad reply")
        return reply

    # ---- general calls, for app/data/scans.py and app/data/partlog.py -----
    #
    # `path` starts at /api and is already quoted: use `quote()` on each id put
    # into it. `query` values that are None are left out. All four raise ApiError.

    def get(self, path: str, **query) -> dict:
        """GET a JSON route. Returns the whole reply."""
        return self._call("GET", path + _query(query))

    def post(self, path: str, body=None, *, file=None, content_type: str | None = None, **query) -> dict:
        """POST a JSON `body`, or with `file` (a path) that file's raw bytes, sent
        a piece at a time with `content_type`. Returns the whole reply."""
        if file is None:
            return self._call("POST", path + _query(query), {} if body is None else body)
        with open(file, "rb") as f:
            headers = {"Content-Type": content_type or "application/octet-stream",
                       "Content-Length": str(os.fstat(f.fileno()).st_size)}
            req = urllib.request.Request(self.base + path + _query(query), data=f, method="POST", headers=headers)
            return self._reply(req)

    def put(self, path: str, body: dict, **query) -> dict:
        """PUT a JSON body. Returns the whole reply."""
        return self._call("PUT", path + _query(query), body)

    def get_bytes(self, path: str, to=None, **query):
        """GET a route that answers with a file. Returns its bytes, or with `to` (a path)
        writes them there a piece at a time and returns how many there were."""
        req = urllib.request.Request(self.base + path + _query(query), method="GET")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                if to is None:
                    return resp.read()
                size = 0
                with open(to, "wb") as f:
                    while chunk := resp.read(FILE_CHUNK_BYTES):
                        f.write(chunk)
                        size += len(chunk)
                return size
        except urllib.error.HTTPError as e:
            raise ApiError(_error_text(e)) from None
        except (OSError, ValueError) as e:
            raise ApiError(f"receiver not reachable: {e}") from None

    # ---- section 5: reads -------------------------------------------------

    def state(self) -> dict:
        """GET /api/state."""
        return self._call("GET", "/api/state")

    def machine_records(self, machine_id: str, limit: int = 300, spectrum: bool = False, frames: bool = False,
                        since: str | None = None, after_seq: int | None = None) -> list:
        """GET /api/machines/<id>/records: oldest first. The newest `limit`, or with
        `since` (ISO time) or `after_seq` the oldest `limit` from there."""
        query = f"?limit={int(limit)}" + ("&spectrum=1" if spectrum else "") + ("&frames=1" if frames else "")
        if since is not None:
            query += f"&since={_q(since)}"
        if after_seq is not None:
            query += f"&after_seq={int(after_seq)}"
        return self._call("GET", f"/api/machines/{_q(machine_id)}/records{query}")["records"]

    def machine_range(self, machine_id: str, since: str | None = None) -> list:
        """Every record from `since` (ISO time) on, or from the first, oldest first,
        without spectrum and frames: as many pages as it takes."""
        out: list = []
        after = None if since is not None else 0
        while True:
            page = self.machine_records(machine_id, RANGE_PAGE, since=since, after_seq=after)
            if after is not None and page and not (isinstance(page[0].get("seq"), int) and page[0]["seq"] > after):
                return out  # a receiver that ignores after_seq: stop rather than read the same page again
            out.extend(page)
            if len(page) < RANGE_PAGE or not isinstance(page[-1].get("seq"), int):
                return out
            after = page[-1]["seq"]

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
