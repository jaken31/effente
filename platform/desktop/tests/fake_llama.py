"""A fake llama-server for tests: a script that takes the real one's flags and
answers GET /v1/models, and a model server already running on a port.

FAKE_LLAMA in the environment says how the script behaves: ready, loading or
broken. It writes its arguments to the file FAKE_LLAMA_ARGS names.
"""

import socket
import stat
import sys
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# The first two lines run the script with this Python through sh, because a "#!"
# line cannot hold a path with a space in it (a pipx venv under
# "~/Library/Application Support" has one). To Python, line 2 is only a string.
FAKE = '''#!/bin/sh
"exec" "{python}" "$0" "$@"
"""Fake llama-server. FAKE_LLAMA says how it behaves: ready, loading or broken."""
import argparse, json, os, sys
from http.server import BaseHTTPRequestHandler, HTTPServer

p = argparse.ArgumentParser()
p.add_argument("-m", required=True)
p.add_argument("--host", required=True)
p.add_argument("--port", type=int, required=True)
p.add_argument("--offline", action="store_true")
a = p.parse_args()
with open(os.environ["FAKE_LLAMA_ARGS"], "w") as f:
    json.dump(sys.argv[1:], f)
mode = os.environ.get("FAKE_LLAMA", "ready")
if mode == "broken":
    print("0.00.100.000 I srv    load_model: loading")
    print("0.00.625.026 E srv  llama_server: exiting due to model loading error", flush=True)
    sys.exit(1)


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        ready = mode == "ready" and self.path == "/v1/models"
        body = json.dumps({{"data": [{{"id": os.path.basename(a.m)}}]}} if ready else {{"error": {{"message": "Loading model"}}}})
        self.send_response(200 if ready else 503)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *args):
        pass


HTTPServer((a.host, a.port), H).serve_forever()
'''


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for(check, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = check()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("timed out")


def write_fake(folder, monkeypatch):
    """Write the fake binary into `folder`. Returns (its path, the file its arguments go to)."""
    binary = folder / "llama-server"
    binary.write_text(FAKE.format(python=sys.executable))
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    args = folder / "args.json"
    monkeypatch.setenv("FAKE_LLAMA_ARGS", str(args))
    monkeypatch.setenv("FAKE_LLAMA", "ready")
    return binary, args


@contextmanager
def answering(status=200, body=b'{"data": [{"id": "other.gguf"}]}'):
    """Something answering every GET on a free port, as if started outside the app. Yields the port."""

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()
