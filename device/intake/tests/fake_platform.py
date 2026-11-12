"""A stand-in for the laptop platform's receiver, for the uploader's tests only.

Implements the device side of platform/CONTRACT.md 3.1: POST /api/model, POST /api/records and
POST /api/devices/<id>, with the contract's required fields. Not the real platform. The same
pattern as platform/sim/tests/sim_fake_platform.py.

  python3 -m device.intake.tests.fake_platform [port]     prints what arrives, with contract errors
"""

from __future__ import annotations

import json
import re
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from effente import fault_types as FT
from effente import safety as SF

RECORD_REQUIRED = ("device_id", "machine_id", "model_version", "at", "diagnosis", "problems", "wrong")
CHECKIN_REQUIRED = ("machine_id", "mode", "at", "wrong", "problems")
MODES = ("display", "calibrating", "recording", "training")
GROUPS = ("vibration", "sound", "heat", "room", "sensing")
DIAGNOSES = ("warming_up", "stopped", "normal", "unrecognised_pattern") + FT.NAMES
ISO = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?Z$")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def problem_errors(problems) -> list[str]:
    if not isinstance(problems, list):
        return ["problems is not a list"]
    out, kinds = [], []
    for p in problems:
        name = p.get("name")
        if name not in SF.CHECKS:
            out.append(f"unknown problem {name}")
        if name in FT.NAMES:
            out.append(f"problem {name} has a fault type's name")
        want = "limit" if name in SF.UNIVERSAL else "outside_normal"
        if p.get("kind") != want:
            out.append(f"{name}: kind {p.get('kind')}, not {want}")
        if not isinstance(p.get("text"), str) or not p["text"]:
            out.append(f"{name}: no text")
        if p.get("value") is not None and not isinstance(p["value"], (int, float)):
            out.append(f"{name}: value is not a number or null")
        if not isinstance(p.get("since"), str) or not ISO.match(p["since"]):
            out.append(f"{name}: since {p.get('since')!r} is not ISO UTC")
        kinds.append(p.get("kind"))
    if kinds != sorted(kinds, key=lambda k: k != "limit"):
        out.append("limits are not first")
    return out


def record_errors(r: dict) -> list[str]:
    """Contract 3.1 checks on one message 2 record. Empty means it looks right."""
    p = [f"missing {k}" for k in RECORD_REQUIRED if k not in r]
    p += [f"{k} is empty" for k in RECORD_REQUIRED[:5] if k in r and not (isinstance(r[k], str) and r[k])]
    if p:
        return p
    if not ISO.match(r["at"]):
        p.append(f"at {r['at']!r} is not ISO UTC")
    if r["diagnosis"] not in DIAGNOSES:
        p.append(f"unknown diagnosis {r['diagnosis']}")
    if not 0 <= r.get("confidence", 0) <= 1:
        p.append("confidence outside 0..1")
    if r["diagnosis"] in ("warming_up", "stopped") and r.get("unknown_score") is not None:
        p.append("unknown_score must be null while warming_up or stopped")
    p += problem_errors(r["problems"])
    wrong = r["diagnosis"] not in ("normal", "warming_up", "stopped") or bool(r["problems"])
    if r["wrong"] is not wrong:
        p.append(f"wrong is {r['wrong']}, should be {wrong}")
    if "sensitivity" in r:
        s = r["sensitivity"]
        if not 0 <= s <= 1:
            p.append("sensitivity outside 0..1")
        if abs(r.get("alarm_line", -1) - 4 ** (1 - 2 * s)) > 1e-3:
            p.append(f"alarm_line {r.get('alarm_line')} is not 4 ** (1 - 2 * {s})")
    reason = r.get("reason")
    if r["diagnosis"] == "unrecognised_pattern" and reason != "abnormal, no known fault type fits":
        p.append(f"reason {reason!r} for unrecognised_pattern")
    elif r["diagnosis"] in FT.NAMES and reason != FT.TYPES[FT.NAMES.index(r["diagnosis"])].text:
        p.append(f"reason {reason!r} is not the fault type's text")
    elif r["diagnosis"] in ("normal", "warming_up", "stopped") and reason:
        p.append("reason must be empty for normal, warming_up and stopped")
    s = r.get("spectrum")
    if s is not None and (not isinstance(s, list) or len(s) != 20):
        p.append("spectrum is not 20 values")
    return p


def checkin_errors(c: dict) -> list[str]:
    p = [f"missing {k}" for k in CHECKIN_REQUIRED if k not in c]
    if p:
        return p
    if c["mode"] not in MODES:
        p.append(f"unknown mode {c['mode']}")
    if not ISO.match(c["at"]):
        p.append(f"at {c['at']!r} is not ISO UTC")
    p += problem_errors(c["problems"])
    if c["wrong"] is not bool(c["problems"]):
        p.append("wrong does not match problems")
    if any(q.get("kind") != "limit" for q in c["problems"]):
        p.append("an outside_normal problem outside watching")
    return p


def model_errors(m: dict) -> list[str]:
    p = [f"missing {k}" for k in ("device_id", "machine_id", "model_version", "fault_types", "features",
                                  "spectrum_edges_hz") if k not in m]
    if p:
        return p
    for t in m["fault_types"]:
        if t.get("group") not in GROUPS or not t.get("text") or not isinstance(t.get("validated"), bool):
            p.append(f"fault type {t.get('name')} is not {{name, text, group, validated}}")
    if len(m["spectrum_edges_hz"]) != 21:
        p.append("spectrum_edges_hz is not 21 edges")
    if not m["features"] or any(not f.get("name") for f in m["features"]):
        p.append("features table empty or unnamed")
    return p


class FakePlatform:
    def __init__(self, port: int = 0, host: str = "127.0.0.1"):
        self.lock = threading.Lock()
        self.models: list[dict] = []
        self.records: list[dict] = []
        self.checkins: list[tuple[str, dict]] = []  # (device id, body)
        self.batches: list[int] = []  # records per POST /api/records, in order
        self.errors: list[str] = []  # contract errors in what arrived
        self.known = set()
        self.need_model_once = False
        self.settings: dict | None = None  # put in every reply to message 2 and 3 when set
        self.delay_s = 0.0  # wait this long before answering, like a slow platform
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

            def _ok(self, **more):
                if owner.settings is not None:
                    more["settings"] = dict(owner.settings)
                return self._reply(200, {"ok": True, **more})

            def do_POST(self):
                if owner.delay_s:
                    time.sleep(owner.delay_s)
                n = int(self.headers.get("Content-Length", 0))
                try:
                    body = json.loads(self.rfile.read(n))
                except ValueError:
                    return self._reply(400, {"ok": False, "error": "not JSON"})
                parts = self.path.split("/")
                with owner.lock:
                    if self.path == "/api/model":
                        owner.errors += [f"model: {e}" for e in model_errors(body)]
                        owner.models.append(body)
                        owner.known.add(body.get("model_version"))
                        return self._reply(200, {"ok": True})
                    if self.path == "/api/records":
                        recs = body if isinstance(body, list) else [body]
                        for r in recs:
                            missing = [k for k in RECORD_REQUIRED if k not in r]
                            if missing:
                                return self._reply(400, {"ok": False, "error": f"missing {missing}"})
                        for r in recs:
                            owner.errors += [f"record: {e}" for e in record_errors(r)]
                        owner.records.extend(recs)
                        owner.batches.append(len(recs) if isinstance(body, list) else 0)  # 0: a single object
                        need = any(r["model_version"] not in owner.known for r in recs)
                        if owner.need_model_once:
                            owner.need_model_once, need = False, True
                            owner.known.clear()
                        return self._ok(stored=len(recs), need_model=need)
                    if len(parts) == 4 and parts[1:3] == ["api", "devices"]:
                        errs = checkin_errors(body)
                        if errs:
                            return self._reply(400, {"ok": False, "error": "; ".join(errs)})
                        owner.checkins.append((parts[3], body))
                        return self._ok()
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


if __name__ == "__main__":
    import sys
    import time

    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    with FakePlatform(port, host="0.0.0.0") as fp:
        print(f"fake platform on port {fp.port}. Ctrl-C to stop.")
        seen = [0, 0, 0, 0]
        try:
            while True:
                time.sleep(0.5)
                with fp.lock:
                    lists = (fp.models, fp.records, fp.checkins, fp.errors)
                    new = [lst[n:] for lst, n in zip(lists, seen)]
                    seen = [len(lst) for lst in lists]
                for m in new[0]:
                    print("model", m.get("model_version"), len(m.get("fault_types", [])), "fault types")
                for r in new[1]:
                    print("record", r.get("diagnosis"), "wrong", r.get("wrong"), [q["name"] for q in r.get("problems", [])])
                for dev, c in new[2]:
                    print("check-in", dev, c.get("mode"), [q["name"] for q in c.get("problems", [])])
                for e in new[3]:
                    print("CONTRACT:", e)
        except KeyboardInterrupt:
            pass
