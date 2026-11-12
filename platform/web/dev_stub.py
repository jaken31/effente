#!/usr/bin/env python3
"""DEVELOPMENT AID ONLY. NOT THE PRODUCT.

A stand-in for the real platform receiver so the dashboard can be built and
demoed alone. It serves platform/web/ at / and a subset of the API in
platform/CONTRACT.md under /api/. Every record, model and agent run it makes
is fake and carries "simulated": true. Its feature names, sentences and
spectra are made up for layout work; they are not measurements and not the
real model's feature table.

Run:  python3 platform/web/dev_stub.py [--port 8000]

Implements: GET /api/state, GET /api/stream (SSE), GET and PUT
/api/devices/<id>/mask, GET /api/machines/<id>/records, GET
/api/machines/<id>/agent-runs. Not implemented: POST /api/model,
POST /api/records, PUT /api/agent/runs/<id> (the stub is its own device and
agent).

Stub-only controls, outside the contract, for trying the page's states:
  POST /dev/pause      stop sending records (device goes quiet, then disconnected)
  POST /dev/resume     start again
  POST /dev/agent-run  start a sample agent run now
  POST /dev/heat?source=camera|imu|none
                       what the records' heat field carries (contract 3.3;
                       made up by platform/sim/sim_heat.py)
"""

import argparse
import json
import os
import queue
import random
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

WEB_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(WEB_ROOT), "sim"))
import sim_heat  # noqa: E402  (the simulator's made-up heat readings)

DEVICE_ID = "effente-sim-01"
MACHINE_ID = "sim-fan-01"
MODEL_VERSION = "stub-0.1"
SPECTRUM_BINS = 64
SPECTRUM_HZ_MAX = 500
CONNECTED_WINDOW_S = 5.0
HISTORY_MAX = 2000

# Made-up feature table for the stub. Sentences reuse the contract's examples.
FEATURES = [
    {"name": "once_per_turn", "sentence": "once-per-turn vibration"},
    {"name": "rattle", "sentence": "rattling pattern"},
]
FEATURE_NAMES = [f["name"] for f in FEATURES]
SENTENCE = {f["name"]: f["sentence"] for f in FEATURES}
# Which fake fault each fake feature drives. Stub behaviour only.
FAULT_FEATURE = {"imbalance": "once_per_turn", "loose_mount": "rattle"}
CYCLE = ["normal", "imbalance", "loose_mount", "unknown"]

MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".md": "text/plain; charset=utf-8",
}


def iso(t=None):
    t = time.time() if t is None else t
    return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class StubPlatform:
    """All stub state. Thread-safe. tick() makes one fake record."""

    def __init__(self, records_per_phase=8, mask_delay_s=1.0, seed=7, heat="camera", interval_s=1.0):
        self.lock = threading.RLock()
        self.heat = sim_heat.HeatSim(heat, seed)
        self.interval_s = interval_s
        self.rng = random.Random(seed)
        self.records_per_phase = records_per_phase
        self.mask_delay_s = mask_delay_s
        self.seq = 0
        self.ticks = 0
        self.mask = []
        self.mask_version = 0
        self.mask_changed_at = 0.0
        self.applied_mask = []  # what the fake device has picked up so far
        self.records = []  # oldest first
        self.runs = {}  # run_id -> run
        self.last_seen = None
        self.last_seen_t = None
        self.connected = False
        self.paused = False
        self.subscribers = []
        self.model = {
            "device_id": DEVICE_ID,
            "model_version": MODEL_VERSION,
            "class_names": ["normal", "imbalance", "loose_mount"],
            "unknown_threshold": 0.5,
            "spectrum_hz_max": SPECTRUM_HZ_MAX,
            "features": FEATURES,
            "simulated": True,
        }
        self.model_announced = False

    # ---- events -------------------------------------------------------
    def subscribe(self):
        q = queue.Queue()
        with self.lock:
            self.subscribers.append(q)
        return q

    def unsubscribe(self, q):
        with self.lock:
            if q in self.subscribers:
                self.subscribers.remove(q)

    def publish(self, name, data):
        with self.lock:
            subs = list(self.subscribers)
        for q in subs:
            q.put((name, data))

    # ---- fake device ----------------------------------------------------
    def _spectrum(self, fault):
        r = self.rng
        s = [0.02 + 0.03 * r.random() for _ in range(SPECTRUM_BINS)]
        if fault == "imbalance":
            # one strong peak at the fake rotation frequency and a small harmonic
            s[4] = 0.75 + 0.1 * r.random()
            s[3] += 0.15
            s[5] += 0.2
            s[8] += 0.2
        elif fault == "loose_mount":
            for i in range(SPECTRUM_BINS):
                s[i] += 0.25 * r.random() + (0.2 if i % 4 == 0 else 0.0)
            s[4] += 0.2
        elif fault == "unknown":
            s[37] = 0.85 + 0.1 * r.random()
            s[38] += 0.3
            s[36] += 0.25
        return [round(min(1.0, max(0.0, v)), 3) for v in s]

    def tick(self, now=None):
        """Make, store and publish one simulated record. Returns it."""
        now = time.time() if now is None else now
        with self.lock:
            if not self.model_announced:
                self.model_announced = True
                self.publish("model", self.model)
            if self.mask != self.applied_mask and now - self.mask_changed_at >= self.mask_delay_s:
                self.applied_mask = list(self.mask)
            mask = list(self.applied_mask)
            fault = CYCLE[(self.ticks // self.records_per_phase) % len(CYCLE)]
            self.ticks += 1
            r = self.rng
            features = {n: round(0.02 + 0.05 * r.random(), 3) for n in FEATURE_NAMES}
            if fault in FAULT_FEATURE:
                features[FAULT_FEATURE[fault]] = round(0.75 + 0.2 * r.random(), 3)
            for n in mask:
                features[n] = 0.0
            unknown_score = round(0.9 + 0.3 * r.random(), 3) if fault == "unknown" else round(0.05 + 0.15 * r.random(), 3)
            if unknown_score > self.model["unknown_threshold"]:
                diagnosis, reason, conf = "unknown", "", round(0.4 + 0.2 * r.random(), 3)
            elif fault in FAULT_FEATURE and FAULT_FEATURE[fault] not in mask:
                diagnosis, reason = fault, SENTENCE[FAULT_FEATURE[fault]]
                conf = round(0.85 + 0.12 * r.random(), 3)
            else:
                diagnosis, reason, conf = "normal", "", round(0.8 + 0.15 * r.random(), 3)
            self.seq += 1
            rec = {
                "device_id": DEVICE_ID,
                "machine_id": MACHINE_ID,
                "model_version": MODEL_VERSION,
                "at": iso(now),
                "diagnosis": diagnosis,
                "confidence": conf,
                "unknown_score": unknown_score,
                "reason": reason,
                "features": features,
                "mask": mask,
                "spectrum": self._spectrum(fault),
                "simulated": True,
                "seq": self.seq,
                "received_at": iso(now),
            }
            heat = self.heat.heat(rec["at"], self.interval_s)
            if heat is not None:
                rec["heat"] = heat
            self.records.append(rec)
            if len(self.records) > HISTORY_MAX:
                del self.records[: len(self.records) - HISTORY_MAX]
            was_connected = self.connected
            self.last_seen, self.last_seen_t, self.connected = rec["received_at"], now, True
        if not was_connected:
            self.publish("device", {"device_id": DEVICE_ID, "connected": True, "last_seen": rec["received_at"]})
        self.publish("record", rec)
        return rec

    def check_connected(self, now=None):
        now = time.time() if now is None else now
        with self.lock:
            if not self.connected or self.last_seen_t is None:
                return
            if now - self.last_seen_t <= CONNECTED_WINDOW_S:
                return
            self.connected = False
            data = {"device_id": DEVICE_ID, "connected": False, "last_seen": self.last_seen}
        self.publish("device", data)

    # ---- mask -----------------------------------------------------------
    def mask_reply(self):
        return {"ok": True, "device_id": DEVICE_ID, "mask": list(self.mask), "mask_version": self.mask_version}

    def set_mask(self, names, now=None):
        """Returns (status, body)."""
        if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
            return 400, {"ok": False, "error": "mask must be a list of feature names"}
        bad = [n for n in names if n not in FEATURE_NAMES]
        if bad:
            return 400, {"ok": False, "error": "not in the announced feature table: " + ", ".join(bad)}
        clean = list(dict.fromkeys(names))
        with self.lock:
            changed = clean != self.mask
            if changed:
                self.mask = clean
                self.mask_version += 1
                self.mask_changed_at = time.time() if now is None else now
            reply = self.mask_reply()
        if changed:
            self.publish("mask", reply)
        return 200, reply

    # ---- agent ----------------------------------------------------------
    def agent_run_steps(self):
        """Yield successive copies of one sample run, as an agent would PUT them."""
        with self.lock:
            n = len(self.runs)
            recent = [r for r in self.records if r["machine_id"] == MACHINE_ID][-42:]
        start = time.time()
        run_id = "run-" + datetime.fromtimestamp(start, timezone.utc).strftime("%Y%m%d-%H%M%S") + f"-{n}-" + MACHINE_ID
        run = {
            "run_id": run_id,
            "device_id": DEVICE_ID,
            "machine_id": MACHINE_ID,
            "at": iso(start),
            "status": "running",
            "model": "stub (no model called)",
            "usage": {"input_tokens": 0, "output_tokens": 0},
            "ok": False,
            "reason": "",
            "problems": [],
            "tool_calls": [],
            "report": None,
            "simulated": True,
        }
        yield json.loads(json.dumps(run))
        counts = {}
        for r in recent:
            counts[r["diagnosis"]] = counts.get(r["diagnosis"], 0) + 1
        calls = [
            ("get_readings", {"machine_id": MACHINE_ID, "since": ""}, json.dumps({"windows": len(recent), "diagnoses": counts})),
            ("get_feature_table", {}, json.dumps(SENTENCE)),
            ("search_parts", {"query": "fan blade balancing kit"}, json.dumps({"results": [], "note": "stub: no search made"})),
        ]
        for name, inp, out in calls:
            run["tool_calls"].append({"at": iso(), "name": name, "input": inp, "output": out[:2000]})
            yield json.loads(json.dumps(run))
        run["status"] = "done"
        if n % 3 == 2:
            run["ok"] = False
            run["reason"] = "agent unavailable: no connection (stub)"
            run["problems"] = ["stub: simulated failure to show the no-report state"]
        else:
            top = max(counts, key=counts.get) if counts else "normal"
            cited = [FAULT_FEATURE[top]] if top in FAULT_FEATURE else []
            run["ok"] = True
            run["report"] = {
                "summary": f"Stub report: {top} in {counts.get(top, 0)} of {len(recent)} recent simulated windows on {MACHINE_ID}.",
                "priority": {"normal": "none", "imbalance": "medium", "loose_mount": "high", "unknown": "medium"}[top],
                "cited_features": cited,
                "recommended_action": {
                    "normal": "No action. Probe again at the next round.",
                    "imbalance": "Stub text: check the blades for added weight and rebalance.",
                    "loose_mount": "Stub text: check and tighten the mounting screws.",
                    "unknown": "Stub text: inspect by hand; the pattern was not seen in training.",
                }[top],
                "part_reference": "",
            }
        yield json.loads(json.dumps(run))

    def store_run(self, run):
        with self.lock:
            self.runs[run["run_id"]] = run
        self.publish("agent_run", run)

    def run_agent(self, step_delay_s=0.0):
        last = None
        for i, run in enumerate(self.agent_run_steps()):
            if i and step_delay_s:
                time.sleep(step_delay_s)
            self.store_run(run)
            last = run
        return last

    # ---- reads ----------------------------------------------------------
    def state(self):
        with self.lock:
            latest = self.records[-1] if self.records else None
            return {
                "ok": True,
                "now": iso(),
                "devices": {
                    DEVICE_ID: {
                        "connected": self.connected,
                        "last_seen": self.last_seen,
                        "model_version": MODEL_VERSION,
                        "mask": list(self.mask),
                        "mask_version": self.mask_version,
                        "latest": latest,
                    }
                }
                if self.records or self.model_announced
                else {},
                "models": {MODEL_VERSION: self.model} if self.model_announced else {},
                "machines": [MACHINE_ID] if self.records else [],
            }

    def machine_records(self, machine_id, limit=300, spectrum=False, frames=False):
        limit = max(1, min(2000, limit))
        with self.lock:
            recs = [r for r in self.records if r["machine_id"] == machine_id][-limit:]
        if not spectrum:
            recs = [{k: v for k, v in r.items() if k != "spectrum"} for r in recs]
        if not frames:
            recs = [dict(r, heat={k: v for k, v in r["heat"].items() if k != "frames"}) if "heat" in r else r
                    for r in recs]
        return recs

    def machine_runs(self, machine_id, limit=20):
        with self.lock:
            runs = [r for r in self.runs.values() if r["machine_id"] == machine_id]
        runs.sort(key=lambda r: r["at"], reverse=True)
        return runs[: max(1, limit)]


def make_handler(platform):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "effente-dev-stub"

        def log_message(self, fmt, *args):  # quiet
            pass

        def send_json(self, status, body):
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def read_json(self):
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            return json.loads(raw.decode() or "null")

        def parts(self):
            u = urlparse(self.path)
            return u, [unquote(p) for p in u.path.split("/") if p], parse_qs(u.query)

        def do_GET(self):
            u, p, q = self.parts()
            if p[:1] != ["api"]:
                return self.static(u.path)
            if p == ["api", "state"]:
                return self.send_json(200, platform.state())
            if p == ["api", "stream"]:
                return self.stream()
            if len(p) == 4 and p[1] == "devices" and p[3] == "mask":
                if p[2] != DEVICE_ID:
                    return self.send_json(400, {"ok": False, "error": "unknown device"})
                with platform.lock:
                    return self.send_json(200, platform.mask_reply())
            if len(p) == 4 and p[1] == "machines" and p[3] in ("records", "agent-runs"):
                try:
                    limit = int(q.get("limit", ["300" if p[3] == "records" else "20"])[0])
                except ValueError:
                    return self.send_json(400, {"ok": False, "error": "limit must be a whole number"})
                if p[3] == "records":
                    spectrum = q.get("spectrum", ["0"])[0] == "1"
                    frames = q.get("frames", ["0"])[0] == "1"
                    return self.send_json(200, {"ok": True, "records": platform.machine_records(p[2], limit, spectrum, frames)})
                return self.send_json(200, {"ok": True, "runs": platform.machine_runs(p[2], limit)})
            return self.send_json(404, {"ok": False, "error": "not found"})

        def do_PUT(self):
            _, p, _ = self.parts()
            if len(p) == 4 and p[:2] == ["api", "devices"] and p[3] == "mask":
                if p[2] != DEVICE_ID:
                    return self.send_json(400, {"ok": False, "error": "unknown device"})
                try:
                    body = self.read_json()
                except (ValueError, UnicodeDecodeError):
                    return self.send_json(400, {"ok": False, "error": "body is not JSON"})
                if not isinstance(body, dict) or "mask" not in body:
                    return self.send_json(400, {"ok": False, "error": "body must be {\"mask\": [...]}"})
                return self.send_json(*platform.set_mask(body["mask"]))
            return self.send_json(404, {"ok": False, "error": "not found"})

        def do_POST(self):
            _, p, q = self.parts()
            if p == ["dev", "pause"]:
                platform.paused = True
            elif p == ["dev", "resume"]:
                platform.paused = False
            elif p == ["dev", "agent-run"]:
                threading.Thread(target=platform.run_agent, args=(1.0,), daemon=True).start()
            elif p == ["dev", "heat"]:
                source = q.get("source", [""])[0]
                if source not in sim_heat.MODES:
                    return self.send_json(400, {"ok": False, "error": "source must be camera, imu or none"})
                platform.heat.mode = source
            else:
                return self.send_json(404, {"ok": False, "error": "not found"})
            return self.send_json(200, {"ok": True})

        def stream(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            q = platform.subscribe()
            try:
                self.wfile.write(b": connected\n\n")
                self.wfile.flush()
                while not getattr(self.server, "stopping", False):
                    try:
                        name, data = q.get(timeout=5.0)
                        msg = f"event: {name}\ndata: {json.dumps(data)}\n\n"
                    except queue.Empty:
                        msg = ": keep-alive\n\n"
                    self.wfile.write(msg.encode())
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                platform.unsubscribe(q)

        def static(self, path):
            rel = unquote(path).lstrip("/") or "index.html"
            full = os.path.realpath(os.path.join(WEB_ROOT, rel))
            if os.path.isdir(full):
                full = os.path.join(full, "index.html")
            if not (full == WEB_ROOT or full.startswith(WEB_ROOT + os.sep)) or not os.path.isfile(full):
                return self.send_json(404, {"ok": False, "error": "not found"})
            with open(full, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", MIME.get(os.path.splitext(full)[1], "application/octet-stream"))
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

    return Handler


def start_background(platform, server, interval_s=1.0, agent_every_s=60.0, first_agent_after_s=6.0):
    """Emit a record every interval and a sample agent run now and then."""

    def emitter():
        while not server.stopping:
            if not platform.paused:
                platform.tick()
            platform.check_connected()
            time.sleep(interval_s)

    def agent():
        time.sleep(first_agent_after_s)
        while not server.stopping:
            platform.run_agent(step_delay_s=1.2)
            time.sleep(agent_every_s)

    for fn in (emitter, agent):
        threading.Thread(target=fn, daemon=True).start()


def make_server(platform, host="0.0.0.0", port=8000):
    server = ThreadingHTTPServer((host, port), make_handler(platform))
    server.daemon_threads = True
    server.stopping = False
    return server


def main():
    ap = argparse.ArgumentParser(description="Dashboard dev stub (simulated data only).")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--heat", default="camera", choices=sim_heat.MODES, help="what the records' heat field carries")
    ap.add_argument("--mask-delay", type=float, default=1.0, help="seconds before the fake device applies a new mask")
    args = ap.parse_args()
    platform = StubPlatform(mask_delay_s=args.mask_delay, heat=args.heat)
    server = make_server(platform, args.host, args.port)
    start_background(platform, server)
    print(f"effente dev stub (SIMULATED DATA) on http://localhost:{args.port}/  - Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.stopping = True
        server.server_close()


if __name__ == "__main__":
    main()
