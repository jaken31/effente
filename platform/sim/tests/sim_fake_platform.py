"""Tiny stand-in for the platform's receiver, for the simulator's tests only.

Implements just the device side of CONTRACT.md: POST /api/model,
POST /api/records, GET /api/devices/<id>/mask. Not the real platform.
"""

import base64
import binascii
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REQUIRED = ("device_id", "machine_id", "model_version", "at", "diagnosis")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakePlatform:
    def __init__(self, port: int = 0, host: str = "127.0.0.1"):
        self.lock = threading.Lock()
        self.models = []  # message 1 bodies, in order
        self.records = []  # message 2 records, in order
        self.mask_gets = []  # device ids
        self.log = []  # ("model" | "records" | "mask", ...) in arrival order
        self.mask = []
        self.mask_version = 0
        self.known_models = set()
        self.force_need_model_once = False
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _reply(self, code, body):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                try:
                    body = json.loads(self.rfile.read(n))
                except ValueError:
                    return self._reply(400, {"ok": False, "error": "not JSON"})
                with owner.lock:
                    if self.path == "/api/model":
                        owner.models.append(body)
                        owner.log.append(("model",))
                        owner.known_models.add(body.get("model_version"))
                        return self._reply(200, {"ok": True})
                    if self.path == "/api/records":
                        recs = body if isinstance(body, list) else [body]
                        for r in recs:
                            missing = [k for k in REQUIRED if k not in r]
                            if missing:
                                return self._reply(400, {"ok": False, "error": f"missing {missing}"})
                        owner.records.extend(recs)
                        owner.log.append(("records", len(recs)))
                        need = any(r["model_version"] not in owner.known_models for r in recs)
                        if owner.force_need_model_once:
                            owner.force_need_model_once = False
                            owner.known_models.clear()
                            need = True
                        return self._reply(
                            200,
                            {
                                "ok": True,
                                "stored": len(recs),
                                "mask": list(owner.mask),
                                "mask_version": owner.mask_version,
                                "need_model": need,
                            },
                        )
                return self._reply(404, {"ok": False, "error": "no such path"})

            def do_GET(self):
                parts = self.path.split("/")
                if len(parts) == 5 and parts[1:3] == ["api", "devices"] and parts[4] == "mask":
                    with owner.lock:
                        owner.mask_gets.append(parts[3])
                        owner.log.append(("mask",))
                        return self._reply(
                            200,
                            {"ok": True, "device_id": parts[3], "mask": list(owner.mask), "mask_version": owner.mask_version},
                        )
                return self._reply(404, {"ok": False, "error": "no such path"})

        ThreadingHTTPServer.allow_reuse_address = True
        self.server = ThreadingHTTPServer((host, port), Handler)
        self.port = self.server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def set_mask(self, mask):
        with self.lock:
            self.mask = list(mask)
            self.mask_version += 1

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


def record_problems(r: dict) -> list[str]:
    """Contract checks on one message 2 record. Empty list means it looks right."""
    p = [f"missing {k}" for k in REQUIRED if not r.get(k)]
    if not 0 <= r.get("confidence", 0) <= 1:
        p.append("confidence outside 0..1")
    if r.get("unknown_score", 0) < 0:
        p.append("unknown_score below 0")
    s = r.get("spectrum")
    if not isinstance(s, list) or len(s) != 64 or not all(0 <= x <= 1 for x in s):
        p.append("spectrum is not 64 values in 0..1")
    feats = r.get("features", {})
    if any(not 0 <= v <= 1 for v in feats.values()):
        p.append("feature activity outside 0..1")
    if any(feats.get(name, 0) != 0 for name in r.get("mask", [])):
        p.append("a masked feature is not 0")
    if r.get("diagnosis") in ("normal", "unknown") and r.get("reason"):
        p.append("reason must be empty for normal and unknown")
    if r.get("heat") is not None:
        p += heat_problems(r["heat"])
    return p


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def heat_problems(h) -> list[str]:
    """Contract 3.3 checks on a record's `heat` field."""
    if not isinstance(h, dict):
        return ["heat is not an object"]
    p = []
    if h.get("source") not in ("camera", "imu"):
        p.append("heat.source is not camera or imu")
    if "imu_temp_c" not in h or not (h["imu_temp_c"] is None or _num(h["imu_temp_c"])):
        p.append("heat.imu_temp_c missing or not a number")
    if h.get("source") == "imu":
        extra = set(h) - {"source", "imu_temp_c"}
        if extra:
            p.append(f"heat with source imu carries {sorted(extra)}")
        return p
    for k in ("max_c", "background_c"):
        if not _num(h.get(k)):
            p.append(f"heat.{k} is not a number")
    if not (isinstance(h.get("hot_row"), int) and 0 <= h["hot_row"] < 24):
        p.append("heat.hot_row not 0..23")
    if not (isinstance(h.get("hot_col"), int) and 0 <= h["hot_col"] < 32):
        p.append("heat.hot_col not 0..31")
    for k in ("cells", "spots"):
        v = h.get(k)
        if not (isinstance(v, list) and len(v) == 48 and all(_num(x) for x in v)):
            p.append(f"heat.{k} is not 48 numbers")
    if not isinstance(h.get("frozen"), bool):
        p.append("heat.frozen is not true or false")
    sm = h.get("scene_match")
    if not (sm is None or (_num(sm) and -1 <= sm <= 1)):
        p.append("heat.scene_match not null or -1..1")
    frames = h.get("frames", [])
    if not isinstance(frames, list) or len(frames) > 2:
        p.append("heat.frames is not a list of at most 2")
        return p
    for i, f in enumerate(frames):
        try:
            ok = (isinstance(f, dict) and isinstance(f.get("at"), str) and _num(f.get("min_c"))
                  and _num(f.get("step_c")) and f["step_c"] > 0
                  and len(base64.b64decode(f.get("data", ""), validate=True)) == 768)
        except (binascii.Error, TypeError, ValueError):
            ok = False
        if not ok:
            p.append(f"heat.frames[{i}] is not at, min_c, step_c and 768 bytes of base64")
    return p


if __name__ == "__main__":
    # Manual use: a receiver to point a device at. Prints what arrives.
    #   python3 platform/sim/tests/sim_fake_platform.py [port] [feature,feature]
    import sys
    import time

    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    fp = FakePlatform(port, host="0.0.0.0")  # reachable from a device on the hotspot
    if len(sys.argv) > 2:
        fp.set_mask([m for m in sys.argv[2].split(",") if m])
    with fp:
        print(f"fake receiver on {fp.url}, mask {fp.mask}. Ctrl-C to stop.")
        seen_m = seen_r = 0
        try:
            while True:
                time.sleep(0.2)
                with fp.lock:
                    models, records = fp.models[seen_m:], fp.records[seen_r:]
                    seen_m, seen_r = len(fp.models), len(fp.records)
                for m in models:
                    print("model", m.get("device_id"), m.get("model_version"), [f.get("name") for f in m.get("features", [])])
                for r in records:
                    probs = record_problems(r)
                    print("record", r.get("diagnosis"), "mask", r.get("mask"), "PROBLEMS: " + "; ".join(probs) if probs else "ok")
        except KeyboardInterrupt:
            pass
