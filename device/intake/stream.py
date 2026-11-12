#!/usr/bin/env python3
"""Live stream: watch mode's data, served over HTTP to anyone who connects.

Watch mode (live.py --serve PORT) publishes here; a dashboard, the laptop
platform or curl connects. No UI here, only the data.

  GET /stream     Server-Sent Events (browser EventSource, curl -N).
                  ?topics=results,features,events,status   (default: all four)
                    results   every window, 4 a second: the results-log record
                    features  a full feature row, at most one per second of data
                    events    as they happen: jolts, bangs, beeping, sensor lost, gaps, cleanup ...
                    status    a heartbeat every 5 s
                  ?features_every=S   fewer feature rows for this client (S seconds of data apart)
                  ?replay=S           replay only the last S seconds on connect (0 = none)
                  On connect a client first gets the last 30 s of its topics, then live
                  data. Reconnecting with Last-Event-ID (EventSource does this by
                  itself) resumes after the last item it got, without repeats.
  GET /latest     the last item of each topic, as one JSON object
  GET /features   name, text, unit and sensor of every feature, in row order
  GET /health     the server is up; how old the last result is

Publishing never waits for a client: each client has its own small queue that
drops its oldest items when full, and a client that stops reading is
disconnected after a few seconds. NaN never appears in the JSON (it is null).
CORS is open (Access-Control-Allow-Origin: *). There is no authentication:
use --serve-host 127.0.0.1 to keep the stream on the device itself.

Standard library only (plus effente.features for the feature table).
"""

from __future__ import annotations

import json
import math
import socket
import threading
import time
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from effente import features as F
from device.intake.extract import features_version

TOPICS = ("results", "features", "events", "status")
_ALIASES = {"result": "results", "feature": "features", "event": "events"}
WALL_CLOCK_AFTER_S = 1e9  # same rule as live.log_record: above this, t is Unix time

UNITS = {
    "log": "log10 of the quantity", "hz": "hertz", "ratio": "plain number, no unit", "g": "g (1 = gravity)",
    "db": "decibels", "c": "degrees C", "pct": "percent", "index": "which item (a label, not an amount)",
    "count": "count", "s": "seconds", "min": "minutes", "mm_s": "mm/s", "c_per_min": "degrees C per minute",
    "c_per_h": "degrees C per hour", "c_per_day": "degrees C per day", "c_per_week": "degrees C per week",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def at_of(t) -> str:
    """UTC time of a device timestamp, or now when the timestamp is not Unix time (a replay)."""
    if t is not None and t > WALL_CLOCK_AFTER_S:
        return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="seconds")
    return now_iso()


def json_safe(o):
    """`o` with NaN and infinity as None, NumPy values as plain Python, anything odd as text."""
    if isinstance(o, float):  # also NumPy float64
        return o if math.isfinite(o) else None
    if o is None or isinstance(o, (str, bool, int)):
        return o
    if isinstance(o, dict):
        return {str(k): json_safe(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [json_safe(v) for v in o]
    if hasattr(o, "tolist"):  # NumPy arrays and scalars
        return json_safe(o.tolist())
    return str(o)


def dumps(o) -> str:
    return json.dumps(json_safe(o), allow_nan=False, separators=(",", ":"))


def _num(v):
    v = float(v)
    return float(f"{v:.6g}") if math.isfinite(v) else None


class _Item:
    __slots__ = ("id", "seq", "topic", "mono", "t", "payload", "_data")

    def __init__(self, id_, seq, topic, mono, t, payload, data):
        self.id, self.seq, self.topic, self.mono, self.t = id_, seq, topic, mono, t
        self.payload, self._data = payload, data

    def data(self) -> str:
        """The JSON text, made on first use by a reader, never by the publisher."""
        if self._data is None:
            p = self.payload
            self._data = dumps(p.json() if isinstance(p, _FeatureRow) else p)
        return self._data


class _Client:
    def __init__(self, topics, queue_len, features_every_s, addr):
        self.topics = topics
        self.q: deque = deque(maxlen=queue_len)
        self.wake = threading.Event()
        self.idle = False
        self.dropped = 0
        self.sent = 0
        self.features_every_s = features_every_s
        self.last_features_t = None
        self.addr = addr

    def offer(self, item) -> None:
        if item.topic not in self.topics:
            return
        if len(self.q) == self.q.maxlen:
            self.dropped += 1  # the deque drops its oldest item
        self.q.append(item)
        self.wake.set()

    def wants(self, item) -> bool:
        """This client's own, slower feature rate (if it asked for one)."""
        if item.topic != "features" or not self.features_every_s or item.t is None:
            return True
        last = self.last_features_t
        if last is not None and 0 <= item.t - last < self.features_every_s - 1e-6:
            return False
        self.last_features_t = item.t
        return True


class Stream:
    """Serves watch mode's results, feature rows, events and status. Call `start()`, then publish."""

    def __init__(self, host: str = "0.0.0.0", port: int = 8765, *, device_id: str = "effente-01",
                 machine_id: str = "machine-1", features_every_s: float = 1.0, status_every_s: float = 5.0,
                 replay_s: float = 30.0, client_queue: int = 256, stall_s: float = 10.0,
                 keepalive_s: float = 15.0, max_clients: int = 16):
        self.host, self.port = host, port
        self.device_id, self.machine_id = device_id, machine_id
        self.features_every_s, self.status_every_s = features_every_s, status_every_s
        self.replay_s, self.client_queue, self.stall_s = replay_s, client_queue, stall_s
        self.keepalive_s, self.max_clients = keepalive_s, max_clients
        self.status_provider = None  # () -> dict, set by watch mode
        self.settings_handler = None  # (changes dict or None) -> settings dict, set by the device app
        self.counts = {t: 0 for t in TOPICS}
        self.stalled = 0  # clients disconnected for not reading
        self.client_drops = 0  # items dropped for clients that have since gone
        self._boot = format(int(time.time()), "x")  # ids from an earlier run are not resumed
        self._seq = 0
        self._lock = threading.Lock()
        self._clients: set = set()
        n_results = int(replay_s * 8) + 8  # twice the window rate, for faster-than-real-time replays
        n_features = int(replay_s / max(features_every_s, 0.25)) + 4
        self._rings = {"results": deque(maxlen=n_results), "features": deque(maxlen=n_features),
                       "events": deque(maxlen=200), "status": deque(maxlen=1)}
        self._last_features_t = None
        self._last_result_mono = None
        self._started = time.monotonic()
        self._stop = threading.Event()
        self._server = None
        self.features_version = features_version()
        self.features_json = dumps(feature_table())

    # ------------------------------------------------------------ lifecycle
    def start(self) -> "Stream":
        handler = type("Handler", (_Handler,), {"stream": self, "timeout": self.stall_s})
        self._server = ThreadingHTTPServer((self.host, self.port), handler)
        self.port = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.2},
                         name="stream-http", daemon=True).start()
        threading.Thread(target=self._heartbeat, name="stream-status", daemon=True).start()
        return self

    def stop(self, drain_s: float = 0.0) -> None:
        """Stop serving; first give connected clients up to `drain_s` to receive what is queued."""
        deadline = time.monotonic() + drain_s
        while time.monotonic() < deadline:
            with self._lock:
                busy = [c for c in self._clients if c.q or not c.idle]
            if not busy:
                break
            time.sleep(0.02)
        self._stop.set()
        with self._lock:
            clients = list(self._clients)
        for c in clients:
            c.wake.set()
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()

    def _heartbeat(self) -> None:
        while not self._stop.wait(self.status_every_s):
            self.publish_status()

    # ------------------------------------------------------------ publishing (never blocks)
    def _publish(self, topic, t, payload=None, data=None) -> _Item:
        now = time.monotonic()
        with self._lock:
            self._seq += 1
            item = _Item(f"{self._boot}-{self._seq}", self._seq, topic, now, t, payload, data)
            self._rings[topic].append(item)
            self.counts[topic] += 1
            for c in self._clients:
                c.offer(item)
        return item

    def publish_result(self, line: str, t: float) -> None:
        """One results-log line (already JSON, as live.log_record makes it)."""
        self._publish("results", t, data=line)
        self._last_result_mono = time.monotonic()

    def publish_features(self, t: float, x, missing=None) -> bool:
        """A full feature row, at most one per `features_every_s` seconds of data. True if published."""
        if self.features_every_s is None or self.features_every_s < 0:
            return False
        last = self._last_features_t
        if last is not None and 0 <= t - last < self.features_every_s - 1e-6:
            return False
        self._last_features_t = t
        values = x.tolist() if hasattr(x, "tolist") else list(x)  # a copy: the model may change x later
        self._publish("features", t, _FeatureRow(self, t, values, missing))
        return True

    def publish_event(self, t, name: str, details: dict | None = None) -> None:
        self._publish("events", t, {"device_id": self.device_id, "machine_id": self.machine_id, "at": at_of(t),
                                    "t": None if t is None else round(float(t), 3), "event": name,
                                    "details": dict(details or {})})

    def publish_status(self) -> None:
        self._publish("status", None, data=dumps(self.status()))

    # ------------------------------------------------------------ snapshots
    def status(self) -> dict:
        s = {"device_id": self.device_id, "machine_id": self.machine_id, "at": now_iso(),
             "uptime_s": round(time.monotonic() - self._started, 1)}
        if self.status_provider is not None:
            try:
                s.update(self.status_provider())
            except Exception as e:  # noqa: BLE001  a status bug must not stop the stream
                s["status_error"] = f"{type(e).__name__}: {e}"
        s["stream"] = self.stream_stats()
        return s

    def stream_stats(self) -> dict:
        with self._lock:
            clients = list(self._clients)
        return {"clients": len(clients), "client_drops": self.client_drops + sum(c.dropped for c in clients),
                "stalled_disconnects": self.stalled, "published": dict(self.counts)}

    def health(self) -> dict:
        age = None if self._last_result_mono is None else round(time.monotonic() - self._last_result_mono, 1)
        return {"ok": True, "device_id": self.device_id, "machine_id": self.machine_id, "at": now_iso(),
                "uptime_s": round(time.monotonic() - self._started, 1), "last_result_age_s": age,
                "clients": len(self._clients), "published": dict(self.counts)}

    def latest_json(self) -> str:
        with self._lock:
            last = {t: (self._rings[t][-1] if self._rings[t] else None) for t in ("results", "features", "events")}
        parts = [f'"{t}":' + (it.data() if it else "null") for t, it in last.items()]
        parts.append('"status":' + dumps(self.status()))
        return "{" + ",".join(parts) + "}"

    # ------------------------------------------------------------ clients
    def _register(self, topics, features_every_s, addr, replay_s, after):
        with self._lock:
            if len(self._clients) >= self.max_clients:
                return None, []
            c = _Client(topics, self.client_queue, features_every_s, addr)
            cutoff = time.monotonic() - replay_s
            items = [it for t in topics if t != "status" for it in self._rings[t]
                     if (it.seq > after if after is not None else it.mono >= cutoff)]
            self._clients.add(c)  # in the same lock as the replay: nothing missed, nothing twice
        items.sort(key=lambda it: it.seq)
        return c, items

    def _unregister(self, c, stalled: bool) -> None:
        with self._lock:
            self._clients.discard(c)
            self.client_drops += c.dropped
            if stalled:
                self.stalled += 1

    def _resume_after(self, last_id):
        """The sequence number in a Last-Event-ID from this run, or None."""
        if not last_id or "-" not in last_id:
            return None
        boot, _, seq = last_id.strip().rpartition("-")
        return int(seq) if boot == self._boot and seq.isdigit() else None


class _FeatureRow:
    """A feature row waiting to be written as JSON (by a reader thread, not the publisher)."""
    __slots__ = ("s", "t", "at", "values", "missing")

    def __init__(self, s, t, values, missing):
        self.s, self.t, self.at, self.values = s, t, at_of(t), values
        self.missing = None if missing is None else list(missing)

    def json(self):
        return {"device_id": self.s.device_id, "machine_id": self.s.machine_id, "at": self.at,
                "t": round(float(self.t), 3), "features_version": self.s.features_version, "missing": self.missing,
                "values": [_num(v) for v in self.values]}


def feature_table() -> dict:
    names = F.FEATURE_NAMES
    bands = [f"vib_band_{i:02d}" for i in range(len(F.VIB_EDGES_HZ) - 1)]
    return {
        "features_version": features_version(), "count": len(names),
        "features": [{"index": i, "name": n, "text": F.FEATURE_TEXT[n], "unit": F.FEATURE_KIND[n],
                      "sensor": F.FEATURE_SENSOR[n], "from_history": i >= F.N_BASE} for i, n in enumerate(names)],
        "units": UNITS,
        "spectrum": {"text": "vibration level per band, log10 band power, 5-500 Hz (the results' spectrum field)",
                     "features": bands, "edges_hz": [round(float(e), 2) for e in F.VIB_EDGES_HZ]},
    }


_INDEX = {"endpoints": {
    "/stream": "Server-Sent Events; ?topics=results,features,events,status (default all), "
               "?features_every=S, ?replay=S",
    "/latest": "last item of each topic", "/features": "the feature table", "/health": "server up",
    "/settings": "GET the device's settings; POST JSON to change them, e.g. {\"sensitivity\": 0.7}"}}


class _Handler(BaseHTTPRequestHandler):
    stream: Stream  # set per server by Stream.start()
    server_version = "effente-stream/1"
    disable_nagle_algorithm = True  # small messages go out at once

    def log_message(self, *args):  # no line per request on the device console
        pass

    def _json(self, code: int, body: str) -> None:
        b = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(b)

    def do_OPTIONS(self):  # CORS preflight, for browser clients that send custom headers
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Last-Event-ID, Cache-Control, Content-Type")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Access-Control-Max-Age", "600")
        self.end_headers()

    def do_GET(self):
        url = urlsplit(self.path)
        path = url.path.rstrip("/") or "/"
        s = self.stream
        try:
            if path == "/stream":
                self._stream(parse_qs(url.query))
            elif path == "/latest":
                self._json(200, s.latest_json())
            elif path == "/features":
                self._json(200, s.features_json)
            elif path == "/health":
                self._json(200, dumps(s.health()))
            elif path == "/settings":
                self._settings(None)
            elif path == "/":
                self._json(200, dumps(_INDEX))
            else:
                self._json(404, dumps({"error": f"no such endpoint: {url.path}", **_INDEX}))
        except OSError:  # the client went away
            pass

    def do_POST(self):
        path = urlsplit(self.path).path.rstrip("/") or "/"
        try:
            if path != "/settings":
                return self._json(404, dumps({"error": f"no such endpoint: {path}", **_INDEX}))
            try:
                n = int(self.headers.get("Content-Length") or 0)
                changes = json.loads(self.rfile.read(min(n, 4096)) or b"{}")
                if not isinstance(changes, dict):
                    raise ValueError("send a JSON object, e.g. {\"sensitivity\": 0.7}")
            except ValueError as e:
                return self._json(400, dumps({"ok": False, "error": str(e)}))
            self._settings(changes)
        except OSError:
            pass

    def _settings(self, changes) -> None:
        handler = self.stream.settings_handler
        if handler is None:
            return self._json(404, dumps({"ok": False, "error": "this device has no settings"}))
        try:
            self._json(200, dumps({"ok": True, **handler(changes)}))
        except ValueError as e:
            self._json(400, dumps({"ok": False, "error": str(e)}))

    def _stream(self, q) -> None:
        s = self.stream
        try:
            asked = [w.strip().lower() for v in q.get("topics", []) for w in v.split(",") if w.strip()]
            topics = {_ALIASES.get(w, w) for w in asked} or set(TOPICS)
            bad = sorted(topics - set(TOPICS))
            every = float(q.get("features_every", ["0"])[0])
            replay = min(max(float(q.get("replay", [s.replay_s])[0]), 0.0), s.replay_s)
        except ValueError as e:
            return self._json(400, dumps({"error": str(e)}))
        if bad:
            return self._json(400, dumps({"error": f"unknown topics {bad}", "topics": list(TOPICS)}))
        after = s._resume_after(self.headers.get("Last-Event-ID") or q.get("last_event_id", [None])[0])
        client, replay_items = s._register(topics, every, self.client_address, replay, after)
        if client is None:
            return self._json(503, dumps({"error": f"too many clients (at most {s.max_clients})"}))
        stalled = False
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(b"retry: 3000\n\n")  # EventSource reconnects after 3 s
            for item in replay_items:
                self._send(client, item)
            if "status" in topics:
                self.wfile.write(f"event: status\ndata: {dumps(s.status())}\n\n".encode())
            last_write = time.monotonic()
            while not s._stop.is_set():
                if not client.q:
                    client.idle = True
                    client.wake.wait(s.keepalive_s)
                    client.wake.clear()
                client.idle = False
                wrote = False
                while client.q and not s._stop.is_set():
                    wrote = self._send(client, client.q.popleft()) or wrote
                now = time.monotonic()
                if wrote:
                    last_write = now
                elif now - last_write >= s.keepalive_s:
                    self.wfile.write(b": keepalive\n\n")
                    last_write = now
        except socket.timeout:  # could not write for stall_s: the client stopped reading
            stalled = True
        except OSError:  # the client went away
            pass
        finally:
            client.idle = True
            s._unregister(client, stalled)
            self.close_connection = True

    def _send(self, client, item) -> bool:
        if not client.wants(item):
            return False
        self.wfile.write(f"id: {item.id}\nevent: {item.topic}\ndata: {item.data()}\n\n".encode())
        client.sent += 1
        return True
