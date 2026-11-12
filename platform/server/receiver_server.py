"""HTTP side of the effente receiver: API routes, the event stream and the
dashboard's static files. Routes follow platform/CONTRACT.md sections 1 to 5.
"""

import http.server
import json
import mimetypes
import os
import queue
import socket
import sys
import threading
import traceback
import urllib.parse

from receiver_store import DEFAULT_HISTORY_LIMIT, MAX_HISTORY_LIMIT, QUIET_AFTER_S, BadRequest, Store

KEEPALIVE_S = 5.0
MAX_BODY_BYTES = 32 * 1024 * 1024
STREAM_QUEUE_SIZE = 2000  # events a slow stream client may fall behind before it is dropped

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".json": "application/json",
    ".map": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".ico": "image/x-icon",
    ".txt": "text/plain; charset=utf-8",
}

MISSING_DASHBOARD_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>effente receiver</title></head>
<body style="font-family: sans-serif; max-width: 40em; margin: 3em auto; line-height: 1.5">
<h1>Dashboard files are not there</h1>
<p>The receiver is running and the API works, but there is no <code>index.html</code> in
the web dir:</p>
<pre>{web_dir}</pre>
<p>Start the receiver with <code>--web-dir</code> pointing at the dashboard folder,
or check <a href="/api/state">/api/state</a> to see the raw data.</p>
</body></html>
"""


class Hub:
    """Fans events out to every connected /api/stream client.

    `publish` never blocks: each client has its own bounded queue, and a client
    that falls too far behind is dropped rather than slowing record uploads.
    """

    CLOSE = object()

    def __init__(self):
        self.lock = threading.Lock()
        self.clients = set()
        self.closed = False

    def subscribe(self):
        q = queue.Queue(maxsize=STREAM_QUEUE_SIZE)
        with self.lock:
            if self.closed:
                q.put_nowait(self.CLOSE)
            else:
                self.clients.add(q)
        return q

    def unsubscribe(self, q):
        with self.lock:
            self.clients.discard(q)

    def publish(self, name, data):
        message = f"event: {name}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n".encode()
        with self.lock:
            for q in list(self.clients):
                try:
                    q.put_nowait(message)
                except queue.Full:
                    self.clients.discard(q)
                    _drain_and_close(q)

    def close(self):
        with self.lock:
            self.closed = True
            clients, self.clients = self.clients, set()
        for q in clients:
            _drain_and_close(q)


def _drain_and_close(q):
    try:
        while True:
            q.get_nowait()
    except queue.Empty:
        pass
    q.put_nowait(Hub.CLOSE)


class ReceiverHTTPServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, store, hub, web_dir, keepalive=KEEPALIVE_S):
        self.store = store
        self.hub = hub
        self.web_dir = os.path.realpath(web_dir)
        self.keepalive = keepalive
        super().__init__(address, Handler)

    def handle_error(self, request, client_address):
        # A client vanishing mid-request is normal; anything else gets a traceback.
        if isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
            return
        super().handle_error(request, client_address)


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "effente-receiver/1"
    timeout = 60  # idle keep-alive connections are closed after this

    def log_message(self, format, *args):
        pass  # do not log every request

    # ---- plumbing ---------------------------------------------------------

    def _send(self, status, body, content_type, head_only=False):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def _json(self, status, obj):
        self._send(status, json.dumps(obj, separators=(",", ":")).encode(), "application/json")

    def _error(self, status, message):
        self._json(status, {"ok": False, "error": message})

    def _body(self):
        if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
            self.close_connection = True
            raise BadRequest("chunked uploads are not supported; send Content-Length")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.close_connection = True
            raise BadRequest("bad Content-Length")
        if length < 0 or length > MAX_BODY_BYTES:
            self.close_connection = True
            raise BadRequest(f"body must be between 0 and {MAX_BODY_BYTES} bytes")
        raw = self.rfile.read(length) if length else b""
        if not raw.strip():
            raise BadRequest("body is empty; expected JSON")
        try:
            return json.loads(raw)
        except ValueError as e:
            raise BadRequest(f"body is not valid JSON: {e}")

    def _discard_body(self):
        """Read and drop a body no route will read. On a keep-alive connection,
        unread bytes would be taken as the start of the next request. A body
        that cannot be read safely closes the connection instead."""
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if "chunked" in self.headers.get("Transfer-Encoding", "").lower() or not 0 <= length <= MAX_BODY_BYTES:
            self.close_connection = True
        elif length:
            self.rfile.read(length)

    def _route(self, method):
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        if not path.startswith("/api/") and path != "/api":
            if method in ("GET", "HEAD"):
                return self._static(path, head_only=method == "HEAD")
            self._discard_body()
            return self._error(405, f"{method} is not allowed on dashboard files")
        query = urllib.parse.parse_qs(parsed.query)
        parts = [urllib.parse.unquote(p) for p in path.split("/")[2:]]
        try:
            handler, args = self._match(method, parts)
            if handler is None:
                self._discard_body()
                return self._error(args, "method not allowed" if args == 405 else f"no such API route: {path}")
            handler(query, *args)
        except BadRequest as e:
            self._error(400, str(e))
        except (ConnectionError, TimeoutError):
            raise
        except Exception:
            traceback.print_exc()
            self._error(500, "internal error in receiver")

    def _match(self, method, parts):
        routes = [
            (("model",), {"POST": self.post_model}),
            (("records",), {"POST": self.post_records}),
            (("devices", None, "mask"), {"GET": self.get_mask, "PUT": self.put_mask}),
            (("agent", "runs", None), {"PUT": self.put_run}),
            (("state",), {"GET": self.get_state}),
            (("stream",), {"GET": self.get_stream}),
            (("machines", None, "records"), {"GET": self.get_machine_records}),
            (("machines", None, "agent-runs"), {"GET": self.get_machine_runs}),
        ]
        for pattern, methods in routes:
            if len(pattern) != len(parts):
                continue
            args = []
            for want, got in zip(pattern, parts):
                if want is None:
                    if got == "":
                        break
                    args.append(got)
                elif want != got:
                    break
            else:
                handler = methods.get(method)
                return (handler, args) if handler else (None, 405)
        return None, 404

    def do_GET(self):
        self._route("GET")

    def do_HEAD(self):
        self._route("HEAD")

    def do_POST(self):
        self._route("POST")

    def do_PUT(self):
        self._route("PUT")

    def do_DELETE(self):
        self._route("DELETE")

    # ---- API routes -------------------------------------------------------

    def post_model(self, query):
        self.server.store.announce_model(self._body())
        self._json(200, {"ok": True})

    def post_records(self, query):
        self._json(200, self.server.store.add_records(self._body()))

    def put_mask(self, query, device_id):
        self._json(200, self.server.store.set_mask(device_id, self._body()))

    def get_mask(self, query, device_id):
        self._json(200, self.server.store.get_mask(device_id))

    def put_run(self, query, run_id):
        self.server.store.put_run(run_id, self._body())
        self._json(200, {"ok": True})

    def get_state(self, query):
        self._json(200, self.server.store.state())

    def get_machine_records(self, query, machine_id):
        limit = _limit(query, DEFAULT_HISTORY_LIMIT)
        spectrum = _flag(query, "spectrum")
        frames = _flag(query, "frames")
        records = self.server.store.machine_records(machine_id, limit, spectrum, frames)
        self._json(200, {"ok": True, "records": records})

    def get_machine_runs(self, query, machine_id):
        limit = _limit(query, 20)
        self._json(200, {"ok": True, "runs": self.server.store.machine_runs(machine_id, limit)})

    def get_stream(self, query):
        hub = self.server.hub
        q = hub.subscribe()
        self.close_connection = True
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            while True:
                try:
                    message = q.get(timeout=self.server.keepalive)
                except queue.Empty:
                    message = b": keep-alive\n\n"
                if message is Hub.CLOSE:
                    return
                self.wfile.write(message)
                self.wfile.flush()
        except (ConnectionError, TimeoutError, OSError):
            return  # tab closed mid-stream: just stop
        finally:
            hub.unsubscribe(q)

    # ---- static files -----------------------------------------------------

    def _static(self, url_path, head_only):
        web_dir = self.server.web_dir
        rel = urllib.parse.unquote(url_path)
        if "\x00" in rel or "\\" in rel:
            return self._error(404, "not found")
        rel = rel.lstrip("/")
        if rel == "" or rel.endswith("/"):
            rel += "index.html"
        full = os.path.realpath(os.path.join(web_dir, rel))
        if full != web_dir and not full.startswith(web_dir + os.sep):
            return self._error(404, "not found")
        if os.path.isdir(full):
            full = os.path.join(full, "index.html")
        if not os.path.isfile(full):
            if rel == "index.html":
                page = MISSING_DASHBOARD_PAGE.replace("{web_dir}", _html_escape(web_dir)).encode()
                return self._send(200, page, "text/html; charset=utf-8", head_only)
            return self._error(404, "not found")
        try:
            with open(full, "rb") as f:
                body = f.read()
        except OSError:
            return self._error(404, "not found")
        ext = os.path.splitext(full)[1].lower()
        content_type = CONTENT_TYPES.get(ext) or mimetypes.guess_type(full)[0] or "application/octet-stream"
        self._send(200, body, content_type, head_only)


def _flag(query, name):
    return query.get(name, ["0"])[-1].lower() in ("1", "true", "yes")


def _limit(query, default):
    raw = query.get("limit", [str(default)])[-1]
    try:
        limit = int(raw)
    except ValueError:
        raise BadRequest("limit must be a whole number")
    if limit < 0:
        raise BadRequest("limit must be 0 or more")
    return min(limit, MAX_HISTORY_LIMIT)


def _html_escape(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class Receiver:
    """The whole running receiver: store, event hub, HTTP server and the
    watcher that marks quiet devices. Used by main.py and by the tests."""

    def __init__(self, host, port, data_dir, web_dir, quiet_after=QUIET_AFTER_S, keepalive=KEEPALIVE_S, log=None):
        log = log or (lambda message: print(message, flush=True))
        self.hub = Hub()
        self.log = log
        self.store = Store(data_dir, emit=self.hub.publish, quiet_after=quiet_after, log=log)
        self.httpd = ReceiverHTTPServer((host, port), self.store, self.hub, web_dir, keepalive=keepalive)
        self.port = self.httpd.server_address[1]
        self._stop = threading.Event()
        self._watcher = threading.Thread(target=self._watch, name="quiet-watcher", daemon=True)
        self._serving = None

    def _watch(self):
        while not self._stop.wait(0.25):
            self.store.check_quiet()

    def start(self):
        """Serve in a background thread."""
        self._watcher.start()
        self._serving = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True)
        self._serving.start()

    def serve_forever(self):
        """Serve in this thread until stop() or Ctrl-C."""
        self._watcher.start()
        self.httpd.serve_forever(poll_interval=0.2)

    def stop(self):
        self._stop.set()
        self.hub.close()
        if self._serving is not None:
            self.httpd.shutdown()
            self._serving.join(timeout=5)
        self.httpd.server_close()


def lan_address():
    """Best guess at this laptop's address on the hotspot. Sends nothing."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return None
