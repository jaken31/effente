"""logic.py and platform/web/dashboard_logic.js give the same answers.

The same cases run through both: a few hand-picked ones for the edges
(rounding, odd names, bad input) and a few hundred random event sequences.
logic_parity.js runs the JS side. Skipped when node is not installed, as the
web logic tests are.
"""

import base64
import json
import math
import random
import re
import shutil
import subprocess
from array import array
from pathlib import Path

import pytest
from app.data import logic as L
from app.data.paths import WEB_DIR

RUNNER = Path(__file__).with_name("logic_parity.js")
T0 = 1_800_000_000_000

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def snake(name: str) -> str:
    return re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", name).lower()


def plain_py(v):
    if isinstance(v, array):
        return [plain_py(x) for x in v]
    if isinstance(v, (list, tuple)):
        return [plain_py(x) for x in v]
    if isinstance(v, dict):
        return {k: plain_py(x) for k, x in v.items()}
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return v


def snake_keys(v):
    if isinstance(v, list):
        return [snake_keys(x) for x in v]
    if isinstance(v, dict):
        return {snake(k): snake_keys(x) for k, x in v.items()}
    return v


def run_py(steps):
    state = None
    raws, outs = [], []

    def arg(a):
        if a == "$state":
            return state
        if isinstance(a, dict) and "$ref" in a:
            raw = raws[a["$ref"]]
            return raw[snake(a["key"])] if "key" in a else raw
        return a

    for op, *rest in steps:
        if op == "new":
            state = L.create_state()
        elif op == "set":
            state[snake(rest[0])] = rest[1]
        elif op == "dump":
            raws.append(state)
            outs.append(plain_py(json.loads(json.dumps(plain_py(state)))))
        elif op == "call":
            fn = getattr(L, snake(rest[0]))
            raw = fn(*[arg(a) for a in rest[1:]])
            raws.append(raw)
            outs.append(json.loads(json.dumps(plain_py(raw))))
    return outs


def run_js(cases):
    res = subprocess.run(["node", str(RUNNER), str(WEB_DIR / "dashboard_logic.js")], input=json.dumps(cases),
                         capture_output=True, text=True, timeout=60, check=False)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def same(a, b):
    """Equal, with numbers compared as numbers (JS has no int/float split)."""
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b or math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-9)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
    return a == b


# ---------------- cases ----------------

PROBLEM_NAMES = ["vibration_over_limit", "knocking", "too_hot", "heating_fast", "repeated_jolts",
                 "vibration_above_normal", "hotter_than_normal", "louder_than_normal", "smells_of_smoke"]
DIAGNOSES = ["normal", "stopped", "warming_up", "unbalance_like", "vibration_too_high", "looseness_or_rattle",
             "unrecognised_pattern", "", None, "bearing"]
MODES = ["watching", "display", "calibrating", "recording", "training", "new_mode", None]

MODEL = {
    "device_id": "a", "model_version": "v1",
    "fault_types": [{"name": "unbalance_like", "validated": False}, {"name": "vibration_too_high", "validated": True}],
    "features": [{"name": "vib_1x", "text": "vibration once per turn"}, {"name": "snd_level_db", "text": "sound level"},
                 {"name": "no_text"}],
    "simulated": True,
}


def iso(ms):
    import datetime as dt

    return dt.datetime.fromtimestamp(ms / 1000, dt.UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def frame_data(rng, n=768):
    return base64.b64encode(bytes(rng.randrange(256) for _ in range(n))).decode()


def edge_cases():
    values = [0, 0.5, 1.5, 2.5, -2.5, 0.25, 0.75, 104.25, 2.75, -0.25, 0.125, 1.005, 2.345, 104.45, 104.55, 18.5, -0.04, 3.14159, 1e-7, 12345.675]
    pure = []
    for name in PROBLEM_NAMES:
        for v in values + [None, "7", float("nan")]:
            pure.append(["call", "problemValueText", {"name": name, "value": v if not (
                isinstance(v, float) and math.isnan(v)) else None}])
    for d in DIAGNOSES + ["a_b_like", "_like", "x_like_y", "UPPER_case", 7]:
        pure.append(["call", "displayName", d])
        pure.append(["call", "diagnosisKind", d])
    for m in MODES + ["", "some_thing"]:
        pure.append(["call", "modeName", m])
    for k in ["limit", "outside_normal", "new_kind", "", None]:
        pure.append(["call", "kindLabel", k])
    for t in [-1, 0, 0.1, 0.2, 0.25, 0.33, 0.5, 0.6, 0.75, 0.9, 1, 2, None, "x"]:
        pure.append(["call", "heatColor", t])
    rng = random.Random(3)
    frames = [
        {"at": "2026-11-14T16:02:10.5Z", "min_c": 24.1, "step_c": 0.15, "data": frame_data(rng)},
        {"at": "2026-11-14T16:02:11Z", "min_c": 23.97, "step_c": 0.13, "data": frame_data(rng)},
        {"at": "x", "min_c": 20, "step_c": 0.1, "data": frame_data(rng)[:-2]},  # padding dropped: atob still reads it
        {"at": "x", "min_c": 20, "step_c": 0.1, "data": " " + frame_data(rng) + "\n"},
        {"at": "x", "min_c": 20, "step_c": 0.1, "data": frame_data(rng, 767)},
        {"at": "x", "min_c": 20, "step_c": -1, "data": frame_data(rng)},
        {"at": "x", "min_c": "20", "step_c": 0.1, "data": frame_data(rng)},
    ]
    for f in frames:
        pure.append(["call", "decodeFrame", f])
    heats = [
        {"source": "camera", "imu_temp_c": 40, "max_c": 61.2, "hot_row": 9.0, "hot_col": 31, "background_c": 25,
         "cells": [rng.uniform(-1, 3) for _ in range(48)], "spots": [rng.uniform(0, 5) for _ in range(48)],
         "frozen": True, "scene_match": None, "frames": frames[:2]},
        {"source": "camera", "background_c": 22.4, "cells": [rng.uniform(-1, 3) for _ in range(48)], "hot_row": -1},
        {"source": "camera", "cells": [1] * 47, "background_c": 22},
        {"source": "camera", "frames": [frames[1], frames[0]]},
        {"source": "imu"},
        {"source": "imu", "imu_temp_c": "hot"},
    ]
    heat_case = []
    for h in heats:
        heat_case.append(["call", "heatView", {"heat": h}])
        heat_case.append(["call", "heatRange", {"$ref": len(heat_case) - 1}])
    view_ref = 0  # heats[0]: two images half a second apart
    for elapsed in (0, 1, 250, 499.5, 500, 600, 2000):
        heat_case.append(["call", "frameAt", {"$ref": view_ref, "key": "frames"}, elapsed])
    trend = []
    t = T0
    recs = []
    for _ in range(40):
        t += rng.choice([1000, 5000, 15000])
        recs.append({"at": iso(t), "heat": {"source": "imu", "imu_temp_c": 40 + rng.uniform(-1, 3)}}
                    if rng.random() > 0.2 else {"at": iso(t)})
        trend.append(["call", "imuTrend", list(recs)])
    return [pure, heat_case, trend]


def random_record(rng, t, devices, machines):
    diagnosis = rng.choice(DIAGNOSES)
    problems = rng.sample([{"name": n, "kind": rng.choice(["limit", "outside_normal"]), "value": rng.uniform(0, 120)}
                           for n in PROBLEM_NAMES], rng.randrange(3))
    rec = {
        "device_id": rng.choice(devices), "machine_id": rng.choice(machines), "model_version": rng.choice(["v1", "v2"]),
        "at": iso(t), "received_at": iso(t + rng.randrange(0, 900)), "diagnosis": diagnosis,
        "reason": rng.choice(["", "some reason"]), "note": rng.choice(["", "a note"]),
        "features": {"vib_1x": rng.uniform(-3, 9), "snd_level_db": rng.choice([1.5, "x", None]), "th_max_c": 0.2},
        "problems": problems, "wrong": rng.random() < 0.5, "spectrum": [-5] * 3, "simulated": rng.random() < 0.5,
    }
    if rng.random() < 0.9:
        rec["seq"] = rng.randrange(1, 60)
    if rng.random() < 0.3:
        rec["heat"] = {"source": "camera", "frames": [], "max_c": 50}
    return rec


def random_case(seed):
    rng = random.Random(seed)
    devices, machines = ["a", "b", "c"], ["m1", "m2"]
    t = T0
    steps = [["new"]]
    if rng.random() < 0.8:
        steps.append(["set", "platformUp", True])
    for _ in range(rng.randrange(5, 40)):
        t += rng.choice([0, 300, 1000, 4000, 6000])
        r = rng.random()
        if r < 0.35:
            steps.append(["call", "applyEvent", "$state", "record", random_record(rng, t, devices, machines), t])
        elif r < 0.45:
            steps.append(["call", "applyEvent", "$state", "device", {
                "device_id": rng.choice(devices), "connected": rng.random() < 0.6, "last_seen": rng.choice([iso(t), ""]),
                "mode": rng.choice(MODES)}, t])
        elif r < 0.55:
            steps.append(["call", "applyEvent", "$state", "problem", {
                "device_id": rng.choice(devices), "machine_id": rng.choice(machines + [""]), "at": iso(t),
                "problem": {"name": rng.choice(PROBLEM_NAMES), "kind": "limit", "value": rng.uniform(0, 9)}}, t])
        elif r < 0.65:
            steps.append(["call", "applyEvent", "$state", "problem_cleared", {
                "device_id": rng.choice(devices), "name": rng.choice(PROBLEM_NAMES)}, t])
        elif r < 0.7:
            steps.append(["call", "applyEvent", "$state", "model", dict(MODEL, device_id=rng.choice(devices),
                                                                         model_version=rng.choice(["v1", "v2"])), t])
        elif r < 0.78:
            steps.append(["call", "applyEvent", "$state", "agent_run", {
                "run_id": rng.choice(["r1", "r2", "r3"]), "machine_id": rng.choice(machines),
                "at": rng.choice([iso(T0), iso(T0 + 1000), "bad"]), "status": rng.choice(["running", "done"]),
                "tool_calls": [{}] * rng.randrange(3), "simulated": rng.random() < 0.5}, t])
        elif r < 0.83:
            steps.append(["call", "addRuns", "$state", [{
                "run_id": rng.choice(["r1", "r2", "r4"]), "machine_id": rng.choice(machines), "at": iso(T0 + 500),
                "status": rng.choice(["running", "done"]), "tool_calls": [{}] * rng.randrange(3)}
                for _ in range(rng.randrange(3))]])
        elif r < 0.88:
            steps.append(["call", "setHistory", "$state", rng.choice(machines),
                          [random_record(rng, t - 9000, devices, machines) for _ in range(rng.randrange(4))]])
        elif r < 0.94:
            snap_devs = {}
            for d in rng.sample(devices, rng.randrange(len(devices) + 1)):
                latest = random_record(rng, t - 2000, [d], machines) if rng.random() < 0.7 else None
                snap_devs[d] = {
                    "connected": rng.random() < 0.7, "last_seen": rng.choice([iso(t - 1000), None, "junk"]),
                    "mode": rng.choice(MODES), "machine_id": rng.choice(machines + [None]),
                    "model_version": rng.choice(["v1", None]), "wrong": rng.random() < 0.5,
                    "problems": [{"name": "too_hot"}, {"bad": 1}, "x"][: rng.randrange(4)], "latest": latest}
            steps.append(["call", "applySnapshot", "$state", {
                "now": rng.choice([iso(t + rng.randrange(-3000, 3000)), "junk"]), "devices": snap_devs,
                "models": {"v1": MODEL} if rng.random() < 0.5 else {}}, t])
        else:
            steps.append(["set", "selectedDeviceId", rng.choice(devices + [None, "missing"])])
        now = t + rng.choice([0, 2000, 5000, 5001, 9000])
        shown = rng.choice(devices + [None])
        steps += [
            ["call", "shownDeviceId", "$state"],
            ["call", "deviceStatus", "$state", shown, now],
            ["call", "headline", "$state", shown, now],
            ["call", "modelLine", "$state", shown],
            ["call", "movedReadings", "$state", shown],
            ["call", "featureTexts", "$state", shown],
            ["call", "modelFor", "$state", shown],
            ["call", "latestRunForMachine", "$state", rng.choice(machines + [None])],
            ["call", "simulatedSources", "$state", shown, rng.choice([None, {"simulated": True}])],
        ]
    steps.append(["dump"])
    return steps


def test_dumps_compare_the_whole_state():
    """The runner's dump reaches every key of the state, so a missed field shows."""
    assert set(run_py([["new"], ["dump"]])[0]) == {snake(k) for k in run_js([[["new"], ["dump"]]])[0][0]}


@pytest.mark.parametrize("batch", range(4))
def test_python_and_javascript_agree(batch):
    cases = edge_cases() if batch == 0 else [random_case(batch * 1000 + i) for i in range(100)]
    js = run_js(cases)
    for case, js_out in zip(cases, js):
        py_out = run_py(case)
        js_out = snake_keys(js_out)
        assert len(py_out) == len(js_out)
        probes = [step for step in case if step[0] in ("call", "dump")]
        for step, p, j in zip(probes, py_out, js_out):
            assert same(p, j), f"{json.dumps(step)[:400]}\npython: {p}\njs:     {j}"
