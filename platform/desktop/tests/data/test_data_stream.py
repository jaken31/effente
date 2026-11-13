"""StreamReader against a receiver that opens the stream and then goes silent."""

import http.server
import itertools
import json
import threading
import time

import pytest

pytest.importorskip("PySide6")

from app.data import api
from app.data.api import ApiClient, StreamReader
from PySide6.QtCore import Qt


class _Silent(http.server.BaseHTTPRequestHandler):
    """Answers /api/state, opens /api/stream, then sends nothing, so every read times out."""

    def do_GET(self):
        if self.path == "/api/state":
            body = json.dumps({"ok": True, "devices": {}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.flush()
        self.server.gone.wait(5)

    def log_message(self, *args):
        pass


@pytest.fixture
def silent_receiver():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Silent)
    server.daemon_threads = True
    server.gone = threading.Event()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.gone.set()
    server.shutdown()
    server.server_close()


def test_every_outage_says_down_even_after_a_reconnect(monkeypatch, silent_receiver):
    """Each open stream times out. Every time it opened it must later say "down",
    not only the first time, or the app shows "live" while the receiver is gone."""
    monkeypatch.setattr(api, "STREAM_READ_TIMEOUT_S", 0.2)
    monkeypatch.setattr(api, "RECONNECT_S", (0.05,))
    reader = StreamReader(ApiClient(f"http://127.0.0.1:{silent_receiver.server_address[1]}"))
    seen, lock = [], threading.Lock()

    def note(up):
        with lock:
            seen.append(up)

    reader.connected.connect(note, Qt.DirectConnection)  # no event loop here
    reader.start()
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and reader.connects < 3:
            time.sleep(0.02)
        time.sleep(0.4)  # the third stream times out too
    finally:
        reader.stop()
    with lock:
        events = list(seen)
    assert reader.connects >= 3
    assert events[:6] == [True, False] * 3
    assert all(a != b for a, b in itertools.pairwise(events))  # each change said once
