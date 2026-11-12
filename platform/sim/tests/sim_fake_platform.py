"""Tiny stand-in for the platform's receiver, for the simulator's tests only.

Implements just the device side of CONTRACT.md: POST /api/model,
POST /api/records and POST /api/devices/<id> (the check-in). Not the real
platform: it checks shapes, keeps what arrived, and replies as the contract
says.
"""

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REQUIRED = ("device_id", "machine_id", "model_version", "at", "diagnosis", "problems", "wrong")
CHECK_IN_REQUIRED = ("machine_id", "mode", "at", "wrong", "problems")
CHECK_IN_MODES = ("display", "calibrating", "recording", "training")
NOT_ABNORMAL = ("normal", "stopped", "warming_up")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakePlatform:
    def __init__(self, port: int = 0, host: str = "127.0.0.1"):
        self.lock = threading.Lock()
        self.models = []  # message 1 bodies, in order
        self.records = []  # message 2 records, in order
        self.check_ins = []  # (device_id, message 3 body), in order
        self.log = []  # ("model",) | ("records", n) | ("check_in", mode), in arrival order
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
                parts = self.path.split("/")
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
                        return self._reply(200, {"ok": True, "stored": len(recs), "need_model": need})
                    if len(parts) == 4 and parts[1:3] == ["api", "devices"] and parts[3]:
                        problems = check_in_problems(body)
                        if problems:
                            return self._reply(400, {"ok": False, "error": "; ".join(problems)})
                        owner.check_ins.append((parts[3], body))
                        owner.log.append(("check_in", body["mode"]))
                        return self._reply(200, {"ok": True})
                return self._reply(404, {"ok": False, "error": "no such path"})

        ThreadingHTTPServer.allow_reuse_address = True
        self.server = ThreadingHTTPServer((host, port), Handler)
        self.port = self.server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


def _problem_list_problems(problems) -> list[str]:
    if not isinstance(problems, list):
        return ["problems is not a list"]
    out = []
    names = [p.get("name") if isinstance(p, dict) else None for p in problems]
    if not all(isinstance(n, str) and n for n in names):
        out.append("a problem has no name")
    elif len(set(names)) != len(names):
        out.append("a problem name repeats")
    for p in problems:
        if isinstance(p, dict) and not {"name", "kind", "text", "value", "since"} <= set(p):
            out.append(f"problem {p.get('name')} lacks some of name, kind, text, value, since")
    return out


def record_problems(r: dict) -> list[str]:
    """Contract checks on one message 2 record. Empty list means it looks right."""
    p = [f"missing {k}" for k in REQUIRED if k not in r or r[k] in ("", None)]
    if not 0 <= r.get("confidence", 0) <= 1:
        p.append("confidence outside 0..1")
    score = r.get("unknown_score")
    if r.get("diagnosis") in ("warming_up", "stopped"):
        if score is not None:
            p.append("unknown_score must be null while warming_up or stopped")
    elif not isinstance(score, (int, float)) or score < 0:
        p.append("unknown_score is not a number 0 or more")
    s = r.get("spectrum")
    if not isinstance(s, list) or len(s) != 20 or not all(isinstance(x, (int, float)) for x in s):
        p.append("spectrum is not 20 numbers")
    feats = r.get("features", {})
    if not isinstance(feats, dict) or not all(isinstance(v, (int, float)) for v in feats.values()):
        p.append("features is not {name: spreads}")
    abnormal = r.get("diagnosis") not in NOT_ABNORMAL
    if abnormal != ("fault_types" in r) or abnormal != ("diagnosis_this_minute" in r):
        p.append("fault_types and diagnosis_this_minute must be there exactly while abnormal")
    if not abnormal and r.get("reason"):
        p.append("reason must be empty for normal, stopped and warming_up")
    problems = r.get("problems")
    p += _problem_list_problems(problems)
    if isinstance(problems, list) and r.get("wrong") is not (abnormal or bool(problems)):
        p.append("wrong does not match the diagnosis and problems")
    return p


def check_in_problems(body) -> list[str]:
    """Contract checks on one message 3 check-in. Empty list means it looks right."""
    if not isinstance(body, dict):
        return ["check-in is not an object"]
    p = [f"missing {k}" for k in CHECK_IN_REQUIRED if k not in body]
    if p:
        return p
    if body["mode"] not in CHECK_IN_MODES:
        p.append(f"mode {body['mode']!r} is not a check-in mode")
    p += _problem_list_problems(body["problems"])
    if isinstance(body["problems"], list):
        if any(isinstance(x, dict) and x.get("kind") != "limit" for x in body["problems"]):
            p.append("only limits can be on outside watching")
        if body["wrong"] is not bool(body["problems"]):
            p.append("wrong must be true exactly when problems is not empty")
    return p


if __name__ == "__main__":
    # Manual use: a receiver to point a device at. Prints what arrives.
    #   python3 platform/sim/tests/sim_fake_platform.py [port]
    import sys
    import time

    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    fp = FakePlatform(port, host="0.0.0.0")  # reachable from a device on the hotspot
    with fp:
        print(f"fake receiver on {fp.url}. Ctrl-C to stop.")
        seen_m = seen_r = seen_c = 0
        try:
            while True:
                time.sleep(0.2)
                with fp.lock:
                    models, records, checks = fp.models[seen_m:], fp.records[seen_r:], fp.check_ins[seen_c:]
                    seen_m, seen_r, seen_c = len(fp.models), len(fp.records), len(fp.check_ins)
                for m in models:
                    print("model", m.get("device_id"), m.get("model_version"), [f.get("name") for f in m.get("features", [])])
                for r in records:
                    bad = record_problems(r)
                    names = [x.get("name") for x in r.get("problems") or [] if isinstance(x, dict)]
                    print("record", r.get("diagnosis"), "problems", names, "CONTRACT: " + "; ".join(bad) if bad else "ok")
                for device_id, c in checks:
                    print("check-in", device_id, c.get("mode"), "problems", [x.get("name") for x in c["problems"]], "ok")
        except KeyboardInterrupt:
            pass
