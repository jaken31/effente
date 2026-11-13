"""logic.py against the cases of platform/web/tests/dashboard_logic.test.js.

Each JS test has a Python test here with the same inputs and answers, named
with @js_case("<the JS test's name>"). test_every_js_case_is_ported fails when
the JS file gains a test this file does not have.
"""

import base64
import math
import re

import pytest
from app.data import logic as L
from app.data.paths import WEB_DIR

T0 = 1_800_000_000_000

PORTED = {}
NOT_PORTED = {
    # The JS page's DOM safety; the desktop app has no innerHTML. Not logic.
    "page never puts API text into innerHTML",
}


def js_case(name):
    def mark(fn):
        PORTED[name] = fn.__name__
        return fn

    return mark


def rec(**over):
    r = {
        "device_id": "effente-01",
        "machine_id": "conveyor-07",
        "model_version": "conveyor-07-20261114",
        "at": "2026-11-14T16:02:11Z",
        "diagnosis": "normal",
        "confidence": 0.9,
        "unknown_score": 0.4,
        "reason": "",
        "features": {"vib_1x": 0.8, "snd_level_db": -0.6, "th_max_c": 0.5},
        "abnormal_minutes": 0,
        "out_of_range": 0,
        "note": "",
        "problems": [],
        "wrong": False,
        "spectrum": [-5] * 20,
        "simulated": False,
    }
    r.update(over)
    return r


MODEL = {
    "device_id": "effente-01",
    "machine_id": "conveyor-07",
    "model_version": "conveyor-07-20261114",
    "fault_types": [
        {"name": "unbalance_like", "text": "once-per-turn vibration grown", "group": "vibration", "validated": False},
        {"name": "vibration_too_high", "text": "overall vibration well above normal", "group": "vibration",
         "validated": True},
    ],
    "features": [
        {"name": "vib_1x", "text": "vibration once per turn", "unit": "log"},
        {"name": "snd_level_db", "text": "sound level", "unit": "db"},
    ],
    "spectrum_edges_hz": [5 * 100 ** (i / 20) for i in range(21)],
    "simulated": False,
}

TOO_HOT = {"name": "too_hot", "kind": "limit", "text": "over 100 C", "value": 104.5, "since": "2026-11-14T16:01:50Z"}
ABOVE = {"name": "vibration_above_normal", "kind": "outside_normal", "text": "vibrating well beyond its normal",
         "value": 2.3, "since": "2026-11-14T16:00:47Z"}


def live(s):
    s["platform_up"] = True
    return s


def dev_event(connected, mode):
    return {"device_id": "effente-01", "connected": connected, "last_seen": "x", "mode": mode}


def prob(problem):
    return {"device_id": "effente-01", "machine_id": "conveyor-07", "at": "x", "problem": problem}


def cleared(name, **extra):
    return {"device_id": "effente-01", "machine_id": "conveyor-07", "at": "x", "name": name, **extra}


def test_every_js_case_is_ported():
    src = (WEB_DIR / "tests" / "dashboard_logic.test.js").read_text()
    js = set(re.findall(r'^test\("((?:[^"\\]|\\.)*)"', src, re.MULTILINE))
    assert js, "no tests found in the JS file"
    assert js - NOT_PORTED - set(PORTED) == set(), "JS cases with no Python port"
    assert set(PORTED) - js == set(), "Python ports of JS cases that no longer exist"


@js_case("display names: runtime names read as words, unknown names as sent")
def test_display_names():
    assert L.display_name("normal") == "Normal"
    assert L.display_name("warming_up") == "Warming up"
    assert L.display_name("stopped") == "Stopped"
    assert L.display_name("unrecognised_pattern") == "Unrecognised pattern"
    assert L.display_name("unbalance_like") == "Unbalance-like"
    assert L.display_name("looseness_or_rattle") == "Looseness or rattle"
    assert L.display_name("bearing") == "Bearing"
    assert L.diagnosis_kind("unbalance_like") == "fault"
    assert L.diagnosis_kind("unrecognised_pattern") == "unrecognised"
    assert L.diagnosis_kind("stopped") == "stopped"
    assert L.diagnosis_kind(None) == "none"


@js_case("problem kinds and values read as the contract says")
def test_problem_kinds_and_values():
    assert L.kind_label("limit") == "limit for any machine"
    assert L.kind_label("outside_normal") == "beyond this machine's normal"
    assert L.kind_label("new_kind") == "new kind"
    assert L.problem_value_text(TOO_HOT) == "104.5 °C"
    assert L.problem_value_text(ABOVE) == "2.3× its normal"
    assert L.problem_value_text({"name": "hotter_than_normal", "value": 18.2}) == "+18 °C over its normal"
    assert L.problem_value_text({"name": "smells_of_smoke", "value": 3.14159}) == "3.14"
    assert L.problem_value_text({"name": "too_hot", "value": None}) == ""


@js_case("headline follows wrong, in every mode")
def test_headline_follows_wrong():
    s = live(L.create_state())
    assert L.headline(s, None, T0) == {"state": "none", "text": "Waiting for a device"}
    L.apply_event(s, "record", rec(), T0)
    assert L.headline(s, "effente-01", T0)["state"] == "ok"
    L.apply_event(s, "record", rec(problems=[TOO_HOT], wrong=True), T0 + 1000)
    assert L.headline(s, "effente-01", T0 + 1000) == {"state": "wrong", "text": "Something is wrong"}
    assert L.model_line(s, "effente-01")["label"] == "Model: nothing unusual yet"
    L.apply_event(s, "device", dev_event(True, "recording"), T0 + 2000)
    assert L.device_status(s, "effente-01", T0 + 60000) == "checking_in"
    assert L.headline(s, "effente-01", T0 + 60000)["state"] == "wrong"
    assert L.model_line(s, "effente-01")["label"] == "Model: not judging while recording"
    L.apply_event(s, "problem_cleared", cleared("too_hot"), T0 + 3000)
    assert L.headline(s, "effente-01", T0 + 3000)["state"] == "ok"


@js_case("not live never says nothing is wrong")
def test_not_live():
    s = live(L.create_state())
    L.apply_event(s, "record", rec(), T0)
    assert L.headline(s, "effente-01", T0 + 6000)["state"] == "not_live"
    L.apply_event(s, "device", dev_event(False, "watching"), T0 + 6000)
    assert L.headline(s, "effente-01", T0 + 6000)["state"] == "not_live"
    s2 = L.create_state()
    L.apply_event(s2, "record", rec(), T0)
    assert L.headline(s2, "effente-01", T0)["state"] == "not_live"


@js_case("model line: next to problems, never hidden; unvalidated fault types are possible")
def test_model_line():
    s = live(L.create_state())
    L.apply_event(s, "model", MODEL, T0)
    L.apply_event(s, "record", rec(diagnosis="stopped", unknown_score=None, note="the machine is not running",
                                   problems=[TOO_HOT], wrong=True), T0)
    assert L.model_line(s, "effente-01") == {"kind": "stopped", "label": "Model: machine stopped",
                                             "detail": "the machine is not running", "note": "", "possible": False}
    assert L.headline(s, "effente-01", T0)["state"] == "wrong"
    L.apply_event(s, "record", rec(diagnosis="warming_up", unknown_score=None, problems=[TOO_HOT], wrong=True), T0)
    assert L.model_line(s, "effente-01")["label"] == "Model: warming up"
    L.apply_event(s, "record", rec(diagnosis="unbalance_like", reason="once-per-turn vibration grown", wrong=True), T0)
    assert L.model_line(s, "effente-01") == {"kind": "fault", "label": "Model: Unbalance-like",
                                             "detail": "once-per-turn vibration grown", "note": "", "possible": True}
    L.apply_event(s, "record", rec(diagnosis="vibration_too_high", wrong=True), T0)
    assert L.model_line(s, "effente-01")["possible"] is False
    L.apply_event(s, "record", rec(diagnosis="unrecognised_pattern", reason="abnormal, no known fault type fits",
                                   wrong=True, note="1 reading far out"), T0)
    ml = L.model_line(s, "effente-01")
    assert ml["kind"] == "unrecognised"
    assert ml["note"] == "1 reading far out"


@js_case("problem events: added and removed by name; quiet clears everything")
def test_problem_events():
    s = live(L.create_state())
    L.apply_event(s, "record", rec(diagnosis="unbalance_like", wrong=True), T0)
    d = s["devices"]["effente-01"]
    L.apply_event(s, "problem", prob(TOO_HOT), T0)
    L.apply_event(s, "problem", prob(ABOVE), T0)
    L.apply_event(s, "problem", prob(dict(TOO_HOT, value=110)), T0)
    assert [p["name"] for p in d["problems"]] == ["vibration_above_normal", "too_hot"]
    L.apply_event(s, "problem_cleared", cleared("vibration_above_normal"), T0)
    L.apply_event(s, "problem_cleared", cleared("too_hot"), T0)
    assert d["problems"] == []
    assert d["wrong"] is True
    L.apply_event(s, "problem", prob(TOO_HOT), T0)
    L.apply_event(s, "device", dev_event(False, "watching"), T0)
    L.apply_event(s, "problem_cleared", cleared("too_hot", reason="device quiet"), T0)
    assert d["problems"] == []
    assert d["wrong"] is False


@js_case("a record sets mode, wrong and problems")
def test_record_sets_mode():
    s = live(L.create_state())
    L.apply_event(s, "device", dev_event(True, "calibrating"), T0)
    L.apply_event(s, "record", rec(problems=[ABOVE], wrong=True), T0)
    d = s["devices"]["effente-01"]
    assert d["mode"] == "watching"
    assert d["machine_id"] == "conveyor-07"
    assert d["problems"] == [ABOVE]
    assert d["wrong"] is True


@js_case("moved readings use the announced text, in record order")
def test_moved_readings():
    s = live(L.create_state())
    L.apply_event(s, "model", MODEL, T0)
    L.apply_event(s, "record", rec(features={"snd_level_db": -0.6, "vib_1x": 7.2, "not_announced": 1.5, "bad": "x"}),
                  T0)
    assert L.moved_readings(s, "effente-01") == [
        {"name": "snd_level_db", "text": "sound level", "value": -0.6},
        {"name": "vib_1x", "text": "vibration once per turn", "value": 7.2},
        {"name": "not_announced", "text": "not_announced", "value": 1.5},
    ]
    assert L.feature_texts(s, "effente-01") == {"vib_1x": "vibration once per turn", "snd_level_db": "sound level"}


@js_case("device status: live, quiet after 5 s, disconnected, none")
def test_device_status():
    s = L.create_state()
    assert L.device_status(s, None, T0) == "none"
    L.apply_event(s, "record", rec(), T0)
    assert L.device_status(s, "effente-01", T0 + 4000) == "live"
    assert L.device_status(s, "effente-01", T0 + 5001) == "no_readings"
    L.apply_event(s, "device", dev_event(False, "watching"), T0 + 6000)
    assert L.device_status(s, "effente-01", T0 + 6000) == "disconnected"


@js_case("snapshot: uses the platform's now for ages, drops vanished devices")
def test_snapshot():
    s = L.create_state()
    L.apply_event(s, "record", rec(device_id="gone"), T0)
    L.apply_snapshot(s, {
        "ok": True,
        "now": "2026-11-14T16:02:13Z",
        "devices": {
            "effente-01": {
                "connected": True, "last_seen": "2026-11-14T16:02:11Z", "mode": "watching",
                "machine_id": "conveyor-07", "model_version": "conveyor-07-20261114", "wrong": True,
                "problems": [TOO_HOT],
                "latest": rec(received_at="2026-11-14T16:02:11Z", problems=[TOO_HOT], wrong=True),
            },
            "quiet": {
                "connected": False, "last_seen": "2026-11-14T16:00:00Z", "mode": "recording",
                "machine_id": "fan-02", "model_version": None, "wrong": True, "problems": [TOO_HOT], "latest": None,
            },
        },
        "models": {"conveyor-07-20261114": MODEL},
        "machines": ["conveyor-07"],
    }, T0)
    assert sorted(s["devices"]) == ["effente-01", "quiet"]
    d = s["devices"]["effente-01"]
    assert d["last_record_local_ms"] == T0 - 2000
    assert L.device_status(s, "effente-01", T0) == "live"
    assert L.device_status(s, "effente-01", T0 + 3500) == "no_readings"
    assert d["mode"] == "watching"
    assert d["wrong"] is True
    assert d["problems"] == [TOO_HOT]
    q = s["devices"]["quiet"]
    assert q["wrong"] is False
    assert q["problems"] == []
    assert q["mode"] == "recording"
    assert q["machine_id"] == "fan-02"
    assert L.model_for(s, "effente-01") is MODEL
    assert s["snapshot_loaded"] is True


@js_case("snapshot with a device but no record")
def test_snapshot_no_record():
    s = L.create_state()
    L.apply_snapshot(s, {"ok": True, "now": "2026-11-14T16:02:13Z", "devices": {"a": {
        "connected": True, "last_seen": "2026-11-14T16:02:13Z", "mode": "watching", "machine_id": "m",
        "model_version": None, "wrong": False, "problems": [], "latest": None}}, "models": {}, "machines": []}, T0)
    assert s["devices"]["a"]["latest"] is None
    assert L.device_status(s, "a", T0) == "no_readings"


@js_case("shown device: most recently heard, unless one is picked")
def test_shown_device():
    s = L.create_state()
    L.apply_event(s, "record", rec(device_id="a"), T0)
    L.apply_event(s, "record", rec(device_id="b"), T0 + 10)
    assert L.shown_device_id(s) == "b"
    L.apply_event(s, "record", rec(device_id="a"), T0 + 20)
    assert L.shown_device_id(s) == "a"
    s["selected_device_id"] = "b"
    assert L.shown_device_id(s) == "b"
    s["selected_device_id"] = "missing"
    assert L.shown_device_id(s) == "a"


@js_case("history: loaded, extended from the stream, deduplicated, capped, no spectrum")
def test_history():
    s = L.create_state()
    L.apply_event(s, "record", rec(seq=1), T0)
    assert "conveyor-07" not in s["history"]
    L.set_history(s, "conveyor-07", [rec(seq=1), rec(seq=2)])
    L.apply_event(s, "record", rec(seq=3), T0)
    L.apply_event(s, "record", rec(seq=3), T0)
    L.apply_event(s, "record", rec(seq=4, machine_id="other"), T0)
    h = s["history"]["conveyor-07"]
    assert [r["seq"] for r in h] == [1, 2, 3]
    assert all("spectrum" not in r for r in h)
    L.set_history(s, "conveyor-07", [rec(seq=2)])
    assert [r["seq"] for r in s["history"]["conveyor-07"]] == [1, 2, 3]
    many = [rec(seq=100 + i, machine_id="x") for i in range(1, L.HISTORY_CAP + 51)]
    L.set_history(s, "x", many)
    assert len(s["history"]["x"]) == L.HISTORY_CAP
    assert s["history"]["x"][0]["seq"] == 151


@js_case("agent runs: latest per machine; stream updates replace by run_id")
def test_agent_runs():
    s = L.create_state()

    def run(run_id, at, **over):
        return {"run_id": run_id, "machine_id": "conveyor-07", "at": at, "status": "running", "tool_calls": [],
                **over}

    L.add_runs(s, [run("r2", "2026-11-14T16:05:00Z", status="done"), run("r1", "2026-11-14T16:03:00Z", status="done")])
    assert L.latest_run_for_machine(s, "conveyor-07")["run_id"] == "r2"
    L.apply_event(s, "agent_run", run("r3", "2026-11-14T16:06:00Z"), T0)
    assert L.latest_run_for_machine(s, "conveyor-07")["run_id"] == "r3"
    L.apply_event(s, "agent_run", run("r3", "2026-11-14T16:06:00Z", tool_calls=[{"name": "get_readings"}]), T0)
    assert len(L.latest_run_for_machine(s, "conveyor-07")["tool_calls"]) == 1
    L.add_runs(s, [run("r3", "2026-11-14T16:06:00Z")])
    assert len(L.latest_run_for_machine(s, "conveyor-07")["tool_calls"]) == 1
    L.add_runs(s, [run("r3", "2026-11-14T16:06:00Z", status="done", tool_calls=[{}, {}])])
    assert L.latest_run_for_machine(s, "conveyor-07")["status"] == "done"
    assert L.latest_run_for_machine(s, "other") is None
    assert L.latest_run_for_machine(s, None) is None


@js_case("simulated sources")
def test_simulated_sources():
    s = L.create_state()
    L.apply_event(s, "model", dict(MODEL, simulated=True), T0)
    L.apply_event(s, "record", rec(simulated=True), T0)
    assert L.simulated_sources(s, "effente-01", {"simulated": True}) == ["readings", "model", "agent report"]
    s2 = L.create_state()
    L.apply_event(s2, "record", rec(), T0)
    assert L.simulated_sources(s2, "effente-01", None) == []


@js_case("bad events are ignored")
def test_bad_events():
    s = L.create_state()
    L.apply_event(s, "record", None, T0)
    L.apply_event(s, "record", {"diagnosis": "normal"}, T0)
    L.apply_event(s, "problem", {"device_id": "x"}, T0)
    L.apply_event(s, "problem_cleared", {"name": "too_hot"}, T0)
    L.apply_event(s, "mask", {"device_id": "x", "mask": []}, T0)
    L.apply_event(s, "nonsense", {"device_id": "x"}, T0)
    assert s["devices"] == {}


# ---------------- heat (contract 3.3) ----------------


def frame(at, fill=None, min_c=20, step_c=0.25):
    data = bytes(fill(i) for i in range(768)) if fill else bytes(768)
    return {"at": at, "min_c": min_c, "step_c": step_c, "data": base64.b64encode(data).decode()}


def cam_heat(**over):
    h = {
        "source": "camera", "imu_temp_c": 42.6, "max_c": 61.2, "hot_row": 9, "hot_col": 21, "background_c": 25,
        "cells": [0.5] * 48, "spots": [1.5] * 48, "frozen": False, "scene_match": 0.93,
        "frames": [frame("2026-11-14T16:02:10.5Z", lambda i: i % 256), frame("2026-11-14T16:02:11Z", lambda i: 8)],
    }
    h.update(over)
    return {k: v for k, v in h.items() if v is not ...}


@js_case("decodeFrame turns bytes into degC and rejects bad images")
def test_decode_frame():
    f = L.decode_frame(frame("2026-11-14T16:02:11Z", lambda i: 255 if i == 5 else 4, 20, 0.25))
    assert len(f["pixels"]) == 768
    assert f["pixels"][0] == 21
    assert f["pixels"][5] == 20 + 255 * 0.25
    assert f["at_ms"] == L.parse_time("2026-11-14T16:02:11Z")
    assert L.decode_frame({"at": "x", "min_c": 20, "step_c": 0.25, "data": base64.b64encode(bytes(10)).decode()}) is None
    assert L.decode_frame({"at": "x", "min_c": 20, "step_c": 0, "data": base64.b64encode(bytes(768)).decode()}) is None
    assert L.decode_frame({"at": "x", "min_c": 20, "step_c": 0.1, "data": "%%%not base64"}) is None
    assert L.decode_frame(None) is None


@js_case("heatView reads each heat source")
def test_heat_view():
    assert L.heat_view(None)["kind"] == "none"
    assert L.heat_view(rec())["kind"] == "none"
    assert L.heat_view(rec(heat=None))["kind"] == "none"
    assert L.heat_view(rec(heat={"source": "imu", "imu_temp_c": 42.6})) == {"kind": "imu", "imu_temp_c": 42.6}
    assert L.heat_view(rec(heat={"source": "imu", "imu_temp_c": None})) == {"kind": "imu", "imu_temp_c": None}
    assert L.heat_view(rec(heat={"source": "laser"}))["kind"] == "bad"
    assert L.heat_view(rec(heat="hot"))["kind"] == "bad"
    v = L.heat_view(rec(heat=cam_heat()))
    assert v["kind"] == "camera"
    assert len(v["frames"]) == 2
    assert [v["max_c"], v["hot_row"], v["hot_col"], v["background_c"], v["scene_match"], v["frozen"]] == [
        61.2, 9, 21, 25, 0.93, False]
    g = L.heat_view(rec(heat=cam_heat(frames=...)))
    assert g["kind"] == "camera"
    assert len(g["frames"]) == 0
    assert L.heat_view(rec(heat=cam_heat(frames=[], cells=[1, 2])))["kind"] == "bad"
    assert L.heat_view(rec(heat=cam_heat(hot_row=24)))["hot_row"] is None


@js_case("frameAt plays images at the spacing they were taken")
def test_frame_at():
    fs2 = L.heat_view(rec(heat=cam_heat()))["frames"]
    assert L.frame_at(fs2, 0) == {"index": 0, "next_ms": 500}
    assert L.frame_at(fs2, 499) == {"index": 0, "next_ms": 1}
    assert L.frame_at(fs2, 500) == {"index": 1, "next_ms": None}
    assert L.frame_at([], 0) == {"index": -1, "next_ms": None}
    odd = [{"at_ms": 1000, "pixels": []}, {"at_ms": 0, "pixels": []}]
    assert L.frame_at(odd, 0) == {"index": 1, "next_ms": None}


@js_case("heatRange covers the images in whole degrees, at least 4 wide")
def test_heat_range():
    v = L.heat_view(rec(heat=cam_heat()))
    assert L.heat_range(v) == {"lo": 20, "hi": math.ceil(20 + 255 * 0.25)}
    flat = L.heat_view(rec(heat=cam_heat(frames=[frame("2026-11-14T16:02:11Z", lambda i: 0, 30.3, 0.1)])))
    assert L.heat_range(flat) == {"lo": 30, "hi": 34}
    grid = L.heat_view(rec(heat=cam_heat(frames=...)))
    assert L.heat_range(grid) == {"lo": 25, "hi": 29}


@js_case("heatColor runs dark to pale and clamps")
def test_heat_color():
    assert L.heat_color(0) == [20, 11, 52]
    assert L.heat_color(1) == [252, 255, 164]
    assert L.heat_color(-3) == L.heat_color(0)
    assert L.heat_color(7) == L.heat_color(1)

    def lum(c):
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]

    t = 0.1
    while t <= 1:
        assert lum(L.heat_color(t)) > lum(L.heat_color(t - 0.1)) - 1e-9
        t += 0.1


@js_case("imuTrend is the change over the last 2 minutes")
def test_imu_trend():
    import datetime as dt

    base = dt.datetime(2026, 11, 14, 16, 0, tzinfo=dt.UTC)

    def at(s):
        return (base + dt.timedelta(seconds=s)).isoformat().replace("+00:00", "Z")

    def r(s, v):
        return rec(at=at(s), heat={"source": "imu", "imu_temp_c": v})

    t = L.imu_trend([r(0, 40), r(60, 41), r(130, 42), r(180, 42.5)])
    assert t["span_ms"] == 120000
    assert abs(t["delta_c"] - 1.5) < 1e-9
    assert L.imu_trend([r(0, 40)]) is None
    assert L.imu_trend([r(0, 40), r(5, 41)]) is None
    assert L.imu_trend([rec(), rec()]) is None


@js_case("history keeps heat but drops its images")
def test_history_drops_images():
    s = L.create_state()
    L.set_history(s, "conveyor-07", [])
    L.apply_event(s, "record", rec(seq=1, heat=cam_heat()), T0)
    kept = s["history"]["conveyor-07"][0]
    assert kept["heat"]["max_c"] == 61.2
    assert "frames" not in kept["heat"]
    assert len(s["devices"]["effente-01"]["latest"]["heat"]["frames"]) == 2


# ---------------- laptop only: no JS source ----------------


def test_history_can_keep_bulk_for_the_desktop_pages():
    s = L.create_state()
    L.set_history(s, "conveyor-07", [rec(seq=1, heat=cam_heat())], slim=False)
    L.apply_event(s, "record", rec(seq=2, heat=cam_heat()), T0, slim=False)
    assert all("spectrum" in r and "frames" in r["heat"] for r in s["history"]["conveyor-07"])


def test_fixed_matches_javascript_tofixed():
    # Halves of exactly representable numbers round away from zero, as toFixed does.
    assert L.fixed(2.5, 0) == "3"
    assert L.fixed(0.125, 2) == "0.13"
    assert L.fixed(1.005, 2) == "1.00"  # 1.005 is 1.00499999... in binary
    assert L.fixed(-2.5, 0) == "-3"
    assert L.fixed(-0.0, 1) == "0.0"
    assert L.fixed(-0.01, 1) == "-0.0"


@pytest.mark.parametrize("s, line", [(0, 4.0), (0.5, 1.0), (1, 0.25), (0.75, 0.5), (None, None), (1.2, None)])
def test_alarm_line(s, line):
    assert L.alarm_line(s) == line


@pytest.mark.parametrize("s, name", [(0, "Low"), (0.33, "Low"), (0.34, "Balanced"), (0.5, "Balanced"),
                                     (0.66, "Balanced"), (0.67, "High"), (1, "High"), (None, "Not reported")])
def test_sensitivity_name(s, name):
    assert L.sensitivity_name(s) == name


def test_margins_follow_safety_py():
    assert L.margins(0.5) == {"vibration": 2.0, "temperature": 15.0, "sound": 6.0, "model": 1.0}
    assert L.margins(1) == {"vibration": 1.25, "temperature": 3.75, "sound": 1.5, "model": 0.25}
    assert L.margins(0) == {"vibration": 5.0, "temperature": 60.0, "sound": 24.0, "model": 4.0}
    assert L.margins(None) == L.margins(0.5)
    for s in (0, 0.25, 0.5, 0.75, 1):
        assert L.margins(s)["model"] == pytest.approx(L.alarm_line(s))


def test_margins_equal_effente_safety():
    safety = pytest.importorskip("effente.safety", reason="effente.safety needs numpy")
    assert L.OUTSIDE_NORMAL == safety.OUTSIDE_NORMAL
    for s in (0, 0.1, 0.5, 0.9, 1):
        assert L.margins(s) == pytest.approx(safety.margins(s))


def test_unanswered_and_verdicts():
    assert L.is_unanswered({"answer": None}) is True
    assert L.is_unanswered({"answer": {"verdict": "real"}}) is False
    assert L.verdict_text("real") == "Real problem"
    assert L.verdict_text("false") == "False alarm"
    assert L.verdict_text("maybe") == ""


def test_alarms_and_settings_from_snapshot_and_events():
    s = L.create_state()
    a1 = {"alarm_id": "alarm-1", "machine_id": "m", "start": "2026-11-14T16:00:00Z", "answer": None}
    a2 = {"alarm_id": "alarm-7", "machine_id": "m", "start": "2026-11-14T16:05:00Z", "answer": None}
    other = {"alarm_id": "alarm-3", "machine_id": "n", "start": "2026-11-14T16:01:00Z", "answer": None}
    snap = {"now": "2026-11-14T16:06:00Z", "machines": ["m", "n"], "models": {},
            "devices": {"d": {"connected": True, "machine_id": "m", "sensitivity": 0.5,
                              "settings": {"sensitivity": 0.7}, "latest": None}},
            "alarms": {"alarm-1": a1, "alarm-7": a2, "alarm-3": other}}
    L.apply_snapshot(s, snap, T0)
    L.apply_laptop_snapshot(s, snap)
    assert [a["alarm_id"] for a in L.alarms_for_machine(s, "m")] == ["alarm-7", "alarm-1"]
    assert L.sensitivity_of(s, "d") == {"reported": 0.5, "wanted": 0.7}
    assert L.apply_laptop_event(s, "settings", {"device_id": "d", "sensitivity": 0.7, "settings": None})
    assert L.sensitivity_of(s, "d") == {"reported": 0.7, "wanted": None}
    assert L.apply_laptop_event(s, "alarm", dict(a1, answer={"verdict": "false"}))
    assert [a["alarm_id"] for a in L.alarms_for_machine(s, "m") if L.is_unanswered(a)] == ["alarm-7"]
    assert L.apply_laptop_event(s, "record", {"machine_id": "p"})
    assert L.known_machines(s) == ["m", "n", "p"]
    assert L.sensitivity_of(s, "missing") == {"reported": None, "wanted": None}


def test_device_for_machine():
    s = L.create_state()
    L.apply_event(s, "record", rec(device_id="a", machine_id="m"), T0)
    L.apply_event(s, "record", rec(device_id="b", machine_id="m"), T0 + 10)
    L.apply_event(s, "record", rec(device_id="c", machine_id="n"), T0 + 20)
    assert L.device_for_machine(s, "m") == "b"
    s["selected_device_id"] = "a"
    assert L.device_for_machine(s, "m") == "a"
    assert L.device_for_machine(s, "n") == "c"
    assert L.device_for_machine(s, "zzz") is None
