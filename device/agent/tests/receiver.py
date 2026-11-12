"""A tiny standard library HTTP server that stands in for the platform in tests."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Receiver:
    """Accepts PUT /api/agent/runs/<run_id> and keeps every body it got, in order."""

    def __init__(self, delay: float = 0.0, status: int = 200):
        self.delay = delay
        self.status = status
        self.puts: list[tuple[str, dict]] = []
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            def do_PUT(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                receiver.puts.append((self.path, body))
                if receiver.delay:
                    time.sleep(receiver.delay)
                reply = json.dumps({"ok": receiver.status == 200}).encode()
                self.send_response(receiver.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(reply)))
                self.end_headers()
                self.wfile.write(reply)

            def log_message(self, *_):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.block_on_close = False  # do not wait for a deliberately slow reply
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )

    def __enter__(self) -> "Receiver":
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()

    @property
    def bodies(self) -> list[dict]:
        return [body for _, body in self.puts]
