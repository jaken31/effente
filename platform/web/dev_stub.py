#!/usr/bin/env python3
"""DEVELOPMENT AID ONLY. NOT THE PRODUCT.

Runs the dashboard alone, with no device and no agent: the real receiver
(platform/server) on a throwaway data folder, fed in-process by a scripted
simulated device (platform/sim/sim_model.py) and a sample agent run. So the
page sees exactly the API in platform/CONTRACT.md, and the stub cannot drift
from the receiver. Everything it makes is fake and carries "simulated": true.

Run:  python3 platform/web/dev_stub.py [--port 8000]

The script loops through every state the page must show, a few seconds each
(SCRIPT below): normal, a problem while the model says normal, a fault type
with the problem still on, a fault type alone, stopped with a problem,
recording (check-ins) with a problem, warming up, and an unrecognised pattern
with an outside-normal problem.

Stub-only controls, outside the contract:
  POST /dev/pause      stop sending (device goes quiet, then disconnected)
  POST /dev/resume     start again
  POST /dev/agent-run  start a sample agent run now
  POST /dev/step?n=N   jump to step N of the script (0 is the first)
  POST /dev/heat?source=camera|imu|none
                       what the records' heat field carries (contract 3.3;
                       made up by platform/sim/sim_heat.py)
"""

import argparse
import json
import os
import random
import shutil
import sys
import tempfile
import threading
import time
import urllib.parse
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
PLATFORM = os.path.dirname(HERE)
for sub in ("server", "sim"):
    path = os.path.join(PLATFORM, sub)
    if path not in sys.path:
        sys.path.insert(0, path)

import sim_heat  # noqa: E402  (the simulator's made-up heat readings)
import sim_model  # noqa: E402
from receiver_server import Handler, Receiver  # noqa: E402

DEVICE_ID = sim_model.DEVICE_ID
MACHINE_ID = "sim-fan-01"
STEP_S = 8  # records per step, one a second

# (case, mode, problems on). Problems show at once here: the stub is for the
# page, not for the device's timing.
SCRIPT = [
    ("normal", "watching", []),
    ("normal", "watching", ["too_hot"]),
    ("unbalance", "watching", ["too_hot"]),
    ("unbalance", "watching", []),
    ("stopped", "watching", ["too_hot"]),
    ("normal", "recording", ["too_hot"]),
    ("warming_up", "watching", []),
    ("unrecognised", "watching", ["vibration_above_normal"]),
]


def iso(t=None):
    t = time.time() if t is None else t
    return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class StubDevice:
    """A scripted simulated device and agent that talk straight to a receiver's store."""

    def __init__(self, store, seed=7, step_s=STEP_S, heat="camera", interval_s=1.0):
        self.store = store
        self.heat = sim_heat.HeatSim(heat, seed)
        self.interval_s = interval_s
        self.rng = random.Random(seed)
        self.step_s = step_s
        self.lock = threading.Lock()
        self.ticks = 0
        self.paused = False
        self.announced = False
        self.since = {}  # problem name -> when it came on
        self.runs = 0

    def step(self):
        return SCRIPT[(self.ticks // self.step_s) % len(SCRIPT)]

    def jump(self, n):
        with self.lock:
            self.ticks = (n % len(SCRIPT)) * self.step_s

    def tick(self, now=None):
        """Send one record or check-in, as the script says. Returns what was sent."""
        now = time.time() if now is None else now
        with self.lock:
            case, mode, names = self.step()
            self.ticks += 1
            if not self.announced:
                self.store.announce_model(sim_model.model_announcement(DEVICE_ID, MACHINE_ID))
                self.announced = True
            for name in list(self.since):
                if name not in names:
                    del self.since[name]
            problems = [sim_model.make_problem(n, self.rng, self.since.setdefault(n, iso(now))) for n in names]
            if mode == "watching":
                rec = sim_model.make_record(case, self.rng, problems, DEVICE_ID, MACHINE_ID, at=iso(now))
                heat = self.heat.heat(rec["at"], self.interval_s)
                if heat is not None:
                    rec["heat"] = heat
                reply = self.store.add_records(rec)
                if reply["need_model"]:
                    self.store.announce_model(sim_model.model_announcement(DEVICE_ID, MACHINE_ID))
                return rec
            body = sim_model.make_check_in(mode, problems, MACHINE_ID, at=iso(now))
            self.store.check_in(DEVICE_ID, body)
            return body

    def agent_run_steps(self):
        """Successive copies of one sample run, as an agent would PUT them."""
        recent = self.store.machine_records(MACHINE_ID, 42, False)
        with self.lock:
            n = self.runs
            self.runs += 1
        start = time.time()
        run = {
            "run_id": "run-" + datetime.fromtimestamp(start, timezone.utc).strftime("%Y%m%d-%H%M%S") + f"-{n}-" + MACHINE_ID,
            "agent": "maintenance_report",
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
        texts = {f["name"]: f["text"] for f in sim_model.FEATURES}
        calls = [
            ("get_readings", {"machine_id": MACHINE_ID, "since": ""}, json.dumps({"records": len(recent), "diagnoses": counts})),
            ("get_fault_types", {}, json.dumps(sim_model.FAULT_TEXT)),
            ("search_parts", {"query": "fan blade balancing kit"}, json.dumps({"results": [], "note": "stub: no search made"})),
        ]
        for name, inp, out in calls:
            run["tool_calls"].append({"at": iso(), "name": name, "input": inp, "output": out[:2000]})
            yield json.loads(json.dumps(run))
        run["status"] = "done"
        if n % 3 == 2:
            run["reason"] = "agent unavailable: no connection (stub)"
            run["problems"] = ["stub: simulated failure to show the no-report state"]
        else:
            faults = {d: c for d, c in counts.items() if d not in ("normal", "stopped", "warming_up")}
            top = max(faults, key=faults.get) if faults else "normal"
            moved = [r["features"] for r in recent if r["diagnosis"] == top and r.get("features")]
            cited = sorted(moved[-1], key=lambda k: -abs(moved[-1][k]))[:2] if moved and top != "normal" else []
            run["ok"] = True
            run["report"] = {
                "summary": f"Stub report: {top} in {counts.get(top, 0)} of {len(recent)} recent simulated records on {MACHINE_ID}"
                           + (f", {texts[cited[0]]} up the most." if cited else "."),
                "priority": "none" if top == "normal" else "medium",
                "cited_features": cited,
                "recommended_action": "No action. Probe again at the next round." if top == "normal"
                else "Stub text: inspect by hand before acting on it.",
                "part_reference": "",
            }
        yield json.loads(json.dumps(run))

    def run_agent(self, step_delay_s=0.0):
        last = None
        for i, run in enumerate(self.agent_run_steps()):
            if i and step_delay_s:
                time.sleep(step_delay_s)
            self.store.put_run(run["run_id"], run)
            last = run
        return last


class StubHandler(Handler):
    """The receiver's own routes, plus the stub's /dev/ controls."""

    server_version = "effente-dev-stub"

    def do_POST(self):
        parsed = urllib.parse.urlsplit(self.path)
        if not parsed.path.startswith("/dev/"):
            return super().do_POST()
        self._discard_body()  # no control takes a body; unread bytes would spoil a keep-alive connection
        device = self.server.stub_device
        what = parsed.path[len("/dev/"):]
        if what == "pause":
            device.paused = True
        elif what == "resume":
            device.paused = False
        elif what == "agent-run":
            threading.Thread(target=device.run_agent, args=(1.0,), daemon=True).start()
        elif what == "step":
            try:
                device.jump(int(urllib.parse.parse_qs(parsed.query).get("n", ["0"])[0]))
            except ValueError:
                return self._error(400, "n must be a whole number")
        elif what == "heat":
            source = urllib.parse.parse_qs(parsed.query).get("source", [""])[0]
            if source not in sim_heat.MODES:
                return self._error(400, "source must be camera, imu or none")
            device.heat.mode = source
        else:
            return self._error(404, "no such stub control")
        return self._json(200, {"ok": True})


class Stub:
    """Receiver on a throwaway data folder, plus the scripted device."""

    def __init__(self, host="0.0.0.0", port=8000, quiet_after=None, seed=7, step_s=STEP_S, heat="camera"):
        self.data_dir = tempfile.mkdtemp(prefix="effente-dev-stub-")
        kw = {} if quiet_after is None else {"quiet_after": quiet_after}
        self.receiver = Receiver(host, port, self.data_dir, HERE, log=lambda m: None, handler=StubHandler, **kw)
        self.device = StubDevice(self.receiver.store, seed=seed, step_s=step_s, heat=heat)
        self.receiver.httpd.stub_device = self.device
        self.port = self.receiver.port
        self.stopping = threading.Event()

    def start_background(self, interval_s=1.0, agent_every_s=60.0, first_agent_after_s=6.0):
        def emitter():
            while not self.stopping.wait(interval_s):
                if not self.device.paused:
                    self.device.tick()

        def agent():
            if self.stopping.wait(first_agent_after_s):
                return
            while not self.stopping.is_set():
                self.device.run_agent(step_delay_s=1.2)
                if self.stopping.wait(agent_every_s):
                    return

        for fn in (emitter, agent):
            threading.Thread(target=fn, daemon=True).start()

    def stop(self):
        self.stopping.set()
        self.receiver.stop()
        shutil.rmtree(self.data_dir, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description="Dashboard dev stub (simulated data only).")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--heat", default="camera", choices=sim_heat.MODES, help="what the records' heat field carries")
    args = ap.parse_args()
    stub = Stub(args.host, args.port, heat=args.heat)
    stub.receiver.start()
    stub.start_background()
    print(f"effente dev stub (SIMULATED DATA) on http://localhost:{stub.port}/  - Ctrl+C to stop")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        stub.stop()


if __name__ == "__main__":
    main()
