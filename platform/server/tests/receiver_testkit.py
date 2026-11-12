"""Helpers shared by the receiver tests: an HTTP client, a stream reader and sample messages."""

import http.client
import json
import queue
import socket
import threading
import time
import urllib.error
import urllib.request


class Client:
    """Small HTTP client for one running receiver."""

    def __init__(self, port):
        self.port = port
        self.base = f"http://127.0.0.1:{port}"

    def request(self, method, path, body=None, raw=None, headers=None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers or {})
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as e:
            with e:
                return e.code, dict(e.headers), e.read()

    def json(self, method, path, body=None, **kw):
        status, _, raw = self.request(method, path, body, **kw)
        return status, json.loads(raw)


class Stream:
    """Reads /api/stream in a background thread. Events and comments land in queues."""

    def __init__(self, port):
        self.conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
        self.conn.request("GET", "/api/stream")
        self.sock = self.conn.sock  # http.client drops its own reference once the reply starts
        self.resp = self.conn.getresponse()
        self.events = queue.Queue()
        self.comments = queue.Queue()
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self):
        name, data = None, []
        try:
            while True:
                line = self.resp.readline()
                if not line:
                    return
                line = line.decode().rstrip("\n")
                if line.startswith(":"):
                    self.comments.put(line[1:].strip())
                elif line.startswith("event:"):
                    name = line[6:].strip()
                elif line.startswith("data:"):
                    data.append(line[5:].strip())
                elif line == "" and name is not None:
                    self.events.put((name, json.loads("\n".join(data))))
                    name, data = None, []
        except (OSError, ValueError):
            return

    def next(self, kind, timeout=5):
        """Next event named `kind`, skipping others."""
        end = time.monotonic() + timeout
        while True:
            left = end - time.monotonic()
            if left <= 0:
                raise AssertionError(f"no '{kind}' event within {timeout}s")
            try:
                name, data = self.events.get(timeout=left)
            except queue.Empty:
                continue
            if name == kind:
                return data

    def close(self):
        try:
            self.sock.shutdown(socket.SHUT_RDWR)  # unblocks the reader thread at once
        except OSError:
            pass
        self.sock.close()
        self.thread.join(timeout=5)


MODEL = {
    "device_id": "effente-01",
    "model_version": "fake-0.1",
    "class_names": ["normal", "imbalance", "loose_mount"],
    "unknown_threshold": 0.5,
    "spectrum_hz_max": 500,
    "features": [
        {"name": "once_per_turn", "sentence": "once-per-turn vibration"},
        {"name": "rattle", "sentence": "rattling pattern"},
    ],
    "simulated": True,
}


def make_record(**over):
    record = {
        "device_id": "effente-01",
        "machine_id": "conveyor-07",
        "model_version": "fake-0.1",
        "at": "2026-11-14T16:02:11Z",
        "diagnosis": "imbalance",
        "confidence": 0.94,
        "unknown_score": 0.12,
        "reason": "once-per-turn vibration",
        "features": {"once_per_turn": 0.81, "rattle": 0.03},
        "mask": [],
        "spectrum": [i / 64 for i in range(64)],
        "simulated": True,
    }
    record.update(over)
    return record


def make_run(run_id="run-20261114-160300-conveyor-07", **over):
    run = {
        "run_id": run_id,
        "device_id": "effente-01",
        "machine_id": "conveyor-07",
        "at": "2026-11-14T16:03:00Z",
        "status": "running",
        "model": "test-model",
        "usage": {"input_tokens": 0, "output_tokens": 0},
        "ok": False,
        "reason": "",
        "problems": [],
        "tool_calls": [],
        "report": None,
        "simulated": True,
    }
    run.update(over)
    return run
