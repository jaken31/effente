#!/usr/bin/env python3
"""A stand-in for the laptop's receiver, for testing the phone app alone.

It answers only the two routes the phone uses, as written in
docs/scan-and-log-plan.md: GET /api/hello (5.1) and
POST /api/machines/<machine_id>/scans (5.3). It is not the receiver. Use it
until the real routes exist in platform/server, then use those.

    python3 phone/tools/fake_receiver.py            # port 8000, machine conveyor-07
    python3 phone/tools/fake_receiver.py --port 8001 --machine pump-1 --name "Pump 1"

It prints the pairing line to paste into the app. Files land in a temporary
folder that it names when it starts. Standard library only.
"""

import argparse
import json
import re
import socket
import tempfile
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

MAX_SCAN_BYTES = 268_435_456
SCANS = {}  # client_id -> scan
FOLDER = Path(tempfile.mkdtemp(prefix="effente-fake-scans-"))


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Handler(BaseHTTPRequestHandler):
    def reply(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def refuse(self, error):
        print(f"  refused: {error}", flush=True)
        self.close_connection = True
        self.reply(400, {"ok": False, "error": error})

    def do_GET(self):
        if urlparse(self.path).path == "/api/hello":
            print("hello", flush=True)
            return self.reply(200, {"ok": True, "service": "effente", "contract": "3.4",
                                    "now": now(), "max_scan_bytes": MAX_SCAN_BYTES})
        self.refuse("the fake receiver only knows /api/hello and the scan upload")

    def do_POST(self):
        url = urlparse(self.path)
        query = {key: values[0] for key, values in parse_qs(url.query).items()}
        match = re.fullmatch(r"/api/machines/([a-z0-9-]{1,40})/scans", url.path)
        print(f"upload {url.path} {query}", flush=True)
        if not match:
            return self.refuse("unknown route or bad machine id")
        if self.headers.get("Transfer-Encoding"):
            return self.refuse("chunked uploads are refused")
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            return self.refuse("Content-Length is required")
        if not 1 <= length <= MAX_SCAN_BYTES:
            return self.refuse(f"the body must be 1 to {MAX_SCAN_BYTES} bytes")
        client_id = query.get("client_id", "")
        if not re.fullmatch(r"[A-Za-z0-9-]{8,64}", client_id):
            return self.refuse("client_id must be 8 to 64 letters, digits or -")
        if client_id in SCANS:
            # Read and drop the body so the connection stays usable.
            left = length
            while left > 0:
                left -= len(self.rfile.read(min(left, 1 << 20)))
            print("  duplicate, nothing stored", flush=True)
            return self.reply(200, {"ok": True, "duplicate": True, "scan": SCANS[client_id]})

        scan_id = f"scan-{datetime.now(timezone.utc):%Y%m%d%H%M%S}-fake"
        first = self.rfile.read(min(4, length))
        kind = {b"PK\x03\x04": "usdz", b"glTF": "glb"}.get(first)
        if kind is None:
            return self.refuse("not a USDZ or GLB file")
        path = FOLDER / f"{scan_id}.{kind}"
        with open(path, "wb") as out:
            out.write(first)
            left = length - len(first)
            while left > 0:
                chunk = self.rfile.read(min(left, 1 << 20))
                if not chunk:
                    break
                out.write(chunk)
                left -= len(chunk)
        if left:
            path.unlink()
            print("  the connection dropped before the whole file arrived", flush=True)
            return
        scan = {"scan_id": scan_id, "machine_id": match.group(1), "client_id": client_id,
                "at": query.get("at", now()), "received_at": now(), "format": kind,
                "file": {"name": query.get("name", ""), "bytes": length},
                "app": query.get("app", ""), "model": {"status": "waiting", "reason": ""}}
        SCANS[client_id] = scan
        print(f"  stored {length} bytes at {path}", flush=True)
        self.reply(200, {"ok": True, "duplicate": False, "scan": scan})

    def log_message(self, *args):
        pass


def own_address():
    """This laptop's address on the local network. Sends nothing."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("10.255.255.255", 1))
        return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--machine", default="conveyor-07")
    parser.add_argument("--name", default="Conveyor 7")
    args = parser.parse_args()
    base = f"http://{own_address()}:{args.port}"
    line = f"effente://scan?v=1&base={quote(base, safe='')}&machine={args.machine}"
    if args.name:
        line += f"&name={quote(args.name, safe='')}"
    print(f"Fake receiver at {base}. Files go to {FOLDER}")
    print("Paste this line into the app:")
    print(line, flush=True)
    ThreadingHTTPServer(("0.0.0.0", args.port), Handler).serve_forever()
