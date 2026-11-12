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
        return self.next_of((kind,), timeout)[1]

    def next_of(self, kinds, timeout=5):
        """Next event named one of `kinds`, skipping others. Returns (name, data)."""
        end = time.monotonic() + timeout
        while True:
            left = end - time.monotonic()
            if left <= 0:
                raise AssertionError(f"no {kinds} event within {timeout}s")
            try:
                name, data = self.events.get(timeout=left)
            except queue.Empty:
                continue
            if name in kinds:
                return name, data

    def close(self):
        try:
            self.sock.shutdown(socket.SHUT_RDWR)  # unblocks the reader thread at once
        except OSError:
            pass
        self.sock.close()
        self.thread.join(timeout=5)


MODEL = {
    "device_id": "effente-01",
    "machine_id": "conveyor-07",
    "model_version": "conveyor-07-20261114",
    "features_version": "c985a0908a96",
    "library_entry": "conveyor",
    "source": "calibrated",
    "threshold": 1.0,
    "fault_types": [
        {"name": "unbalance_like", "text": "once-per-turn vibration grown", "group": "vibration", "validated": False},
    ],
    "features": [
        {"name": "vib_1x", "text": "vibration once per turn", "unit": "log"},
        {"name": "vib_velocity_mm_s", "text": "vibration velocity, 10-250 Hz rms", "unit": "mm_s"},
    ],
    "spectrum_edges_hz": [5.0 * 1.3**i for i in range(21)],
    "simulated": True,
}

TOO_HOT = {"name": "too_hot", "kind": "limit", "text": "over 100 C", "value": 104.5, "since": "2026-11-14T16:01:50Z"}
ABOVE_NORMAL = {
    "name": "vibration_above_normal",
    "kind": "outside_normal",
    "text": "vibrating well beyond its normal",
    "value": 2.3,
    "since": "2026-11-14T16:00:47Z",
}


def make_record(**over):
    record = {
        "device_id": "effente-01",
        "machine_id": "conveyor-07",
        "model_version": "conveyor-07-20261114",
        "at": "2026-11-14T16:02:11Z",
        "diagnosis": "unbalance_like",
        "diagnosis_this_minute": "unbalance_like",
        "confidence": 0.71,
        "unknown_score": 2.4,
        "reason": "once-per-turn vibration grown",
        "features": {"vib_1x": 7.2, "vib_velocity_mm_s": 3.1},
        "fault_types": {"unbalance_like": 9.1},
        "abnormal_minutes": 3,
        "out_of_range": 0,
        "note": "",
        "problems": [],
        "wrong": True,
        "spectrum": [-4.7 + i / 20 for i in range(20)],
        "simulated": True,
    }
    record.update(over)
    return record


def make_check_in(**over):
    body = {
        "machine_id": "conveyor-07",
        "mode": "recording",
        "at": "2026-11-14T15:40:02Z",
        "wrong": False,
        "problems": [],
    }
    body.update(over)
    return body


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
