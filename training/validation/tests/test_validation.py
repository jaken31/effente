"""Tests for the validation harness itself: that each check measures what it says it measures.

Run with:  python3 -m pytest training/validation

They use a small synthetic machine (training/model/tests/test_model.py: one row a minute) and
made-up judgements, not the simulators, so they take seconds. One test runs 20 simulated seconds.
"""

import json

import numpy as np
import pytest

from effente import calibration as CAL
from effente import fault_types as FT
from effente import features as F
from effente import model_runtime as RT
from effente import trainer as TR
from training.model.tests.test_model import minutes
from training.validation import (check_calibration_length, check_faults, check_fit, check_heldout_normal, check_ims,
                                 check_int8, check_unknown_fault, report, run_all, track3)
from training.validation import common as C

NAMES = list(F.FEATURE_NAMES)
DAY = 86400


@pytest.fixture(scope="module")
def model():
    """A model of the synthetic machine, one period a minute. It also serves as a library entry:
    track 3's calibration takes any effente-model-1 dict."""
    t, X = minutes(3, seed=1)
    d, _ = TR.train_model(t, X, NAMES, period=60)
    return d


def judged(alarm, t0=0.0, step=1.0, name="unbalance_like", symptom="unusual_vibration"):
    """A made-up common.watch() result: one judged period per flag."""
    alarm = np.asarray(alarm, bool)
    return {"t": t0 + step * np.arange(len(alarm)), "alarm": alarm,
            "diagnosis": [name if a else "normal" for a in alarm],
            "diagnosis_this_period": [name if a else None for a in alarm],
            "symptom": [symptom if a else None for a in alarm]}


# ----------------------------------------------------------------- shared pieces

def test_bench_helpers_count_alarms_and_time_a_fault():
    got = C.alarm_share([0, 1, 1, 0, 1, 0], judged=[1, 1, 1, 1, 0, 1])
    assert got == {"rows": 5, "alarm_rows": 2, "share": 0.4, "episodes": 1}
    assert C.alarm_share([], None)["share"] is None
    t = np.arange(10) * 60.0
    alarm = np.array([1, 0, 0, 0, 0, 0, 1, 1, 0, 1], bool)
    assert C.fault_outcome(t, alarm, 240, 480) == {"caught": True, "after_s": 120.0}  # the alarm at t=0 is before it
    assert C.fault_outcome(t, alarm, 60, 300) == {"caught": False, "after_s": None}
    assert C.fault_outcome(t, alarm, 480, 540)["caught"] is False  # the end is exclusive


def test_watch_gives_one_item_per_judged_period_and_flags_the_abnormal_ones(model):
    t, X = minutes(0.2, seed=3, start=3 * DAY)
    X[120:, F.INDEX["vib_1x"]] += 0.6
    X[120:, F.INDEX["vib_velocity_mm_s"]] += 1.0
    w = C.watch(model, t, X)
    assert len(w["t"]) == len(t) - 1 and list(w["t"]) == list(t[:-1])  # the last period is never closed
    assert len(w["alarm"]) == len(w["diagnosis"]) == len(w["symptom"]) == len(w["moved_most"]) == len(w["t"])
    assert w["alarm"][:118].mean() < 0.05 and w["alarm"][130:].all()
    assert set(w["diagnosis"][140:]) == {"unbalance_like"}
    assert all(d not in C.NOT_ALARM for d, a in zip(w["diagnosis"], w["alarm"]) if a)


def test_calibrate_keeps_the_last_seconds_before_the_watch_and_reports_a_refusal(monkeypatch):
    seen = {}

    def fake(entry, t, X, names, seed=0):
        seen.update(t=t, seed=seed)
        if len(t) < 100:
            raise ValueError("too little recorded")
        return {"ok": True}, {}

    monkeypatch.setattr(C.track3, "calibrate", fake)
    t = 5000.0 + np.arange(900)
    X = np.zeros((900, 3))
    assert C.calibrate(None, t, X, seed=4, length_s=120)[0] == {"ok": True}
    assert seen["t"][0] == 5000 + 180 and seen["t"][-1] == 5000 + 299 and seen["seed"] == 4
    assert C.calibrate(None, t, X, length_s=30) == (None, "too little recorded")


def test_a_session_is_the_same_on_a_rerun_and_comes_from_the_cache(tmp_path, monkeypatch):
    t, X = C.session("server", 1, 36000, 20, cache=tmp_path)
    assert len(t) == 20 and X.shape == (20, F.N_FEATURES) and t[0] == 36000 and np.all(np.diff(t) == 1)
    monkeypatch.setattr(C, "make_source", lambda *a, **k: pytest.fail("simulated again instead of reading the cache"))
    t2, X2 = C.session("server", 1, 36000, 20, cache=tmp_path)
    assert np.array_equal(t, t2) and np.array_equal(X, X2, equal_nan=True)
    with pytest.raises(pytest.fail.Exception):  # another fault list is another session
        C.session("server", 1, 36000, 20, faults=(("fan5_failure", 36010, 99999, 1.0),), cache=tmp_path)


def test_session_starts_are_in_the_running_shift_and_fixed_by_the_seed():
    for machine in C.SITE:
        starts = [C.start_of(machine, s) for s in range(1, 30)]
        assert all(7 * 3600 <= s < 20 * 3600 for s in starts) and len(set(starts)) > 20
        assert starts[0] == C.start_of(machine, 1) != C.start_of(machine, 1, later=True)


def test_entry_machines_are_never_test_machines():
    # the library's catalog: `conveyor` is built on seed 21, generic_rotating on 101 to 106; the
    # server rack is an evaluation source only, so no server seed is a training machine
    assert track3.TRAINING_SEEDS == {"conveyor": [21, 101, 102, 103, 104, 105, 106], "server": []}
    for seed in (21, 101):
        with pytest.raises(SystemExit):
            C.args_for("", ["--seeds", f"1,{seed}"])
    assert C.args_for("", ["--seeds", "3,4"]).seeds == [3, 4]


def test_a_result_file_carries_what_the_report_needs(tmp_path, capsys):
    row = {"entry": "conveyor", "metric": "m", "value": np.float64(0.25), "n": "4 periods"}
    r = C.write_result("x", tmp_path / "x.json", [row], command="cmd", seeds=[1, 2], data_source="sim",
                       label="simulated", uses_library=True, notes=["one thing not measured"])
    on_disk = json.loads((tmp_path / "x.json").read_text())
    assert on_disk["rows"][0]["value"] == 0.25 and on_disk["command"] == "cmd" and on_disk["seeds"] == [1, 2]
    assert on_disk["library_code"] == track3.NAME and on_disk["standin"] is None  # track 3's code is not a stand-in
    assert on_disk["label"] == "simulated" and on_disk["notes"] == ["one thing not measured"] and r["status"] == "ok"
    assert capsys.readouterr().out.count("\n") == 1  # one summary line
    per = C.write_result("z", tmp_path / "z.json", [dict(row), dict(row, entry="generic_rotating")], command="c",
                         seeds=[1], data_source=C.sources(" + more"), label="simulated", uses_library=True)
    assert [r["data_source"] for r in per["rows"]] == [C.SOURCE["conveyor"] + " + more", C.SOURCE["server"] + " + more"]
    capsys.readouterr()
    other = C.write_result("y", tmp_path / "y.json", [], command="c", seeds=[0], data_source="d", label="public",
                           uses_library=False, status="not run", reason="no data")
    assert other["library_code"] is None and "not run: no data" in capsys.readouterr().out


# ----------------------------------------------------------------- track 3's code, as the checks call it

def test_the_library_is_track_3s_committed_entries():
    lib = track3.library()
    assert tuple(lib) == track3.ENTRIES == ("conveyor", "generic_rotating") and not track3.IS_STANDIN
    for name, entry in lib.items():
        assert track3.manifest(name)["entry_id"] == name and entry["period_s"] == 1 and RT.Model(entry).threshold > 0


def test_calibration_keeps_the_entrys_network_and_runs_under_five_minutes(model):
    t, X = minutes(0.05, seed=2)  # 72 periods: under the 300 s that calibration asks for on its own
    d, report = track3.calibrate(model, t, X, NAMES)
    assert d["autoencoder"] == model["autoencoder"] and d["prep"]["centre"] != model["prep"]["centre"]
    assert report["rows_fit"] + report["rows_held_out"] == 72
    # the threshold is this machine's own, not the entry's
    assert d["threshold"] == pytest.approx(report["threshold_median_multiple"] * report["held_out_smoothed_median"], rel=1e-3)
    with pytest.raises(ValueError):  # calibration's own hard floor still refuses
        track3.calibrate(model, t[:CAL.MIN_ROWS - 1], X[:CAL.MIN_ROWS - 1], NAMES)


def test_fit_check_gives_track_3s_verdict_and_the_numbers_behind_it(model):
    t, X = minutes(0.05, seed=7, start=5 * DAY)
    _, report = track3.calibrate(model, t, X, NAMES)
    mine = report["score_quantiles_held_out"]["p50"]
    fits = track3.fit_check(model, {"heldout_score_quantiles": {"p50": mine}}, t, X, NAMES)
    assert fits["passes"] is True and fits["score_over_entry_heldout_median"] == 1.0
    assert fits["score_over_entry_line"] == pytest.approx(
        report["score_quantiles_before_calibration"]["p50"] / model["threshold"], rel=1e-3)
    over = mine / (2 * CAL.FIT_MAX_RATIO)  # an entry whose own scores are far lower than this machine's
    far = track3.fit_check(model, {"heldout_score_quantiles": {"p50": over}}, t, X, NAMES)
    assert far["passes"] is False and far["score_over_entry_heldout_median"] == pytest.approx(2 * CAL.FIT_MAX_RATIO)
    unchecked = track3.fit_check(model, {}, t, X, NAMES)  # a check that could not be made does not pass
    assert unchecked["passes"] is False and "not checked" in unchecked["reason"]
    assert "score_over_entry_heldout_median" not in unchecked and "score_over_entry_line" in unchecked
    assert "starting rule" in track3.FIT_RULE and f"{CAL.FIT_MAX_RATIO:g}" in track3.FIT_RULE


# ----------------------------------------------------------------- check 1

def test_heldout_judge_counts_only_rows_after_the_calibration(model, monkeypatch):
    monkeypatch.setattr(C, "CALIB_S", 600)
    first, later = minutes(0.1, seed=2, start=3 * DAY), minutes(0.1, seed=4, start=9 * DAY)
    later[1][:, F.INDEX["vib_1x"]] += 0.6  # an abnormal later session
    later[1][:, F.INDEX["vib_velocity_mm_s"]] += 1.0
    got = check_heldout_normal.judge(model, first, later)
    assert got["same_session"]["rows"] == len(first[0]) - 10 - 1  # 10 calibration rows, and the open last period
    assert got["same_session"]["share"] < 0.05 and got["later_session"]["share"] > 0.9
    assert next(iter(got["moved_most_in_alarm_periods"])) in ("vib_1x", "vib_velocity_mm_s")  # what was moved


def test_heldout_rows_pool_the_periods_of_every_seed():
    part = lambda rows, hit: {"rows": rows, "alarm_rows": hit, "share": hit / rows, "episodes": 1}  # noqa: E731
    detail = [{"entry": "conveyor", "seed": 1, "period_s": 1, "same_session": part(100, 10), "later_session": part(100, 100)},
              {"entry": "conveyor", "seed": 2, "period_s": 1, "same_session": part(300, 10), "later_session": part(100, 0)},
              {"entry": "conveyor", "seed": 3, "refused": "too short"}]
    same, later = check_heldout_normal.rows_of(detail)
    assert same["value"] == 0.05 and same["n"] == "400 periods of 1 s, 2 machines" and same["range"] == "0.03333 to 0.1"
    assert later["value"] == 0.5 and later["range"] == "0 to 1"


# ----------------------------------------------------------------- check 2

def test_fault_cases_are_the_sudden_kinds():
    from effente.simulate_conveyor import FAULT_KINDS, SLOW
    conveyor = check_faults.cases("conveyor")
    assert {k for k, _, _ in conveyor} == set(FAULT_KINDS) - set(SLOW) and len(conveyor) == 2 * (len(FAULT_KINDS) - len(SLOW))
    assert {sev for _, _, sev in conveyor} == {1.0, 0.3}
    assert [k for k, _, _ in check_faults.cases("server")] == ["ups_alarm", "rack_bump", "hdd_clicking", "fan5_failure"]
    s0, onset, end = check_faults.layout("conveyor", 1)
    assert (onset - s0, end - onset) == (C.CALIB_S + check_faults.NORMAL_S, check_faults.FAULT_S)


def test_fault_outcome_times_the_alarm_from_the_onset():
    quiet = judged([0] * 100 + [0] * 7 + [1] * 13, name="looseness_or_rattle")
    got = check_faults.outcome(quiet, onset=100, end=120)
    assert got == {"alarmed": True, "seconds_to_alarm": 7.0, "named": "looseness_or_rattle", "symptom": "unusual_vibration"}
    missed = check_faults.outcome(judged([1] * 20 + [0] * 100), onset=100, end=120)  # an alarm before the onset only
    assert missed == {"alarmed": False, "seconds_to_alarm": None, "named": None, "symptom": None}
    mixed = judged([0] * 100 + [1] * 20)
    mixed["diagnosis"][100:104] = ["unrecognised_pattern"] * 4
    assert check_faults.outcome(mixed, 100, 120)["named"] == "unbalance_like"  # the name shown most


def test_injection_moves_a_types_readings_by_its_weights(model):
    prep = RT.Model(model).prep
    X = minutes(0.01, seed=5)[1]
    before = X.copy()
    ft = FT.TYPES[FT.NAMES.index("unbalance_like")]
    assert check_faults.inject_type(X, prep, ft, spreads=8.0) == ["vib_1x", "vib_velocity_mm_s"]
    moved = lambda n: (X - before)[0, F.INDEX[n]] / prep.scale[prep.columns.index(n)]  # noqa: E731
    assert moved("vib_1x") == pytest.approx(8.0) and moved("vib_velocity_mm_s") == pytest.approx(2.0)  # weights 2 and 0.5
    assert np.array_equal(np.delete(X, [F.INDEX["vib_1x"], F.INDEX["vib_velocity_mm_s"]], 1),
                          np.delete(before, [F.INDEX["vib_1x"], F.INDEX["vib_velocity_mm_s"]], 1), equal_nan=True)
    room = FT.TYPES[FT.NAMES.index("device_moved")]  # none of its readings are in this machine's model
    assert check_faults.inject_type(X.copy(), prep, room) == []
    prep.scale[prep.columns.index("vib_1x")] = 1e12  # switched off by a calibration: not a reading to move
    assert check_faults.inject_type(X.copy(), prep, ft) == ["vib_velocity_mm_s"]


def test_injection_moves_a_kurtosis_reading_in_its_prepared_units(model):
    prep = RT.Model(model).prep
    X = minutes(0.01, seed=5)[1]
    z0 = prep.transform(X)[0, prep.names_out.index("snd_kurt_hf")]
    check_faults.shift(X, prep, "snd_kurt_hf", 3.0)
    assert prep.transform(X)[0, prep.names_out.index("snd_kurt_hf")] - z0 == pytest.approx(3.0, abs=1e-3)


def test_an_injected_type_is_alarmed_timed_and_named_end_to_end(model):
    t, X = minutes(0.2, seed=3, start=3 * DAY)
    onset = t[120]
    rows = X[t >= onset]
    check_faults.inject_type(rows, RT.Model(model).prep, FT.TYPES[FT.NAMES.index("unbalance_like")])
    X[t >= onset] = rows
    got = check_faults.outcome(C.watch(model, t, X), onset, t[-1])
    assert got["alarmed"] and got["named"] == "unbalance_like"
    assert 0 <= got["seconds_to_alarm"] <= 5 * 60  # within the 5 periods of persistence


def test_a_machine_is_calibrated_once_on_its_twin_and_every_fault_is_judged_by_that_model(model, monkeypatch):
    """One row a minute stands in for one a second: 2 days of calibration, then 0.1 day normal, 0.1 day fault."""
    monkeypatch.setattr(C, "CALIB_S", 2 * DAY)
    monkeypatch.setattr(check_faults, "NORMAL_S", DAY // 10)
    monkeypatch.setattr(check_faults, "FAULT_S", DAY // 10)
    monkeypatch.setattr(C, "start_of", lambda machine, seed, later=False: 0)
    monkeypatch.setattr(check_faults, "cases", lambda machine: [("unbalance", None, 1.0)])
    calibrations = []
    real = C.track3.calibrate
    monkeypatch.setattr(C.track3, "calibrate", lambda *a, **k: calibrations.append(len(a[1])) or real(*a, **k))

    def session(machine, seed, start_s, length_s, faults=(), week=0, cache=None):
        t, X = minutes(length_s / DAY, seed=seed)
        for _, onset, _, _ in faults:
            X[t >= onset, F.INDEX["vib_1x"]] += 0.6
            X[t >= onset, F.INDEX["vib_velocity_mm_s"]] += 1.0
        return t, X

    monkeypatch.setattr(C, "session", session)
    lib = {"generic_rotating": model}
    twin, fault, *injected = check_faults.machine_one(("generic_rotating", 1), lib, length_s=2 * DAY)
    assert calibrations == [2 * 1440]  # once, on the twin's calibration rows only
    assert twin["how"] == "twin" and twin["alarmed"] is False
    assert fault["how"] == "simulated" and fault["fault"] == "unbalance" and fault["severity"] == 1.0
    assert fault["alarmed"] and fault["twin_alarmed"] is False and fault["named"] == "unbalance_like"
    assert 0 <= fault["seconds_to_alarm"] <= 5 * 60
    by_name = {d["fault"]: d for d in injected}
    assert set(by_name) == set(FT.NAMES) and by_name["unbalance_like"]["named_as_injected"] is True
    assert "skipped" in by_name["device_moved"] and by_name["overheating"]["twin_alarmed"] is False
    short = check_faults.machine_one(("generic_rotating", 1), lib, length_s=DAY // 4, injected=False)
    assert [d["how"] for d in short] == ["twin", "simulated"] and calibrations[-1] == 360
    assert all(d["calibration_s"] == DAY // 4 for d in short)
    monkeypatch.setattr(C.track3, "calibrate", lambda *a, **k: (_ for _ in ()).throw(ValueError("too little recorded")))
    assert check_faults.machine_one(("generic_rotating", 1), lib, length_s=2 * DAY) == [
        {"entry": "generic_rotating", "seed": 1, "calibration_s": 2 * DAY, "how": "twin", "refused": "too little recorded"}]


def test_fault_rows_split_by_severity_and_time_only_faults_whose_twin_is_quiet():
    f = lambda sev, alarmed, s, twin, named="unbalance_like", fits=True: {  # noqa: E731
        "entry": "conveyor", "how": "simulated", "severity": sev, "alarmed": alarmed, "seconds_to_alarm": s,
        "twin_alarmed": twin, "named": named, "symptom": "x", "symptom_expected": fits}
    detail = [f(1.0, True, 10.0, False), f(1.0, True, 0.0, True, named="unrecognised_pattern", fits=False),
              f(1.0, False, None, False), f(0.3, True, 50.0, False),
              {"entry": "conveyor", "how": "twin", "alarmed": True}, {"entry": "conveyor", "how": "twin", "refused": "x"}]
    rows = {r["metric"]: r for r in check_faults.rows_of(detail)}
    pre = "simulated faults, severity 1: "
    assert rows[pre + "share with an alarm after the onset"]["value"] == round(2 / 3, 4)
    assert rows[pre + "share whose twin without the fault alarms in the same window"]["value"] == round(1 / 3, 4)
    assert rows[pre + "share with an alarm after the onset, faults whose twin does not alarm"]["value"] == 0.5
    timing = rows[pre + "median seconds from onset to first alarm, faults whose twin does not alarm"]
    assert timing["value"] == 10.0 and timing["n"] == "1 alarmed of 2"
    assert rows[pre + "share of alarmed faults given a fault type name (not unrecognised_pattern)"]["value"] == 0.5
    assert rows[pre + "share of alarmed faults whose screen symptom is one expected for the fault"]["value"] == 0.5
    assert rows["simulated faults, severity 0.3: share with an alarm after the onset"]["value"] == 1.0
    inj = lambda alarmed, right: {"entry": "conveyor", "how": "injected", "alarmed": alarmed, "seconds_to_alarm": 1.0,  # noqa: E731
                                  "twin_alarmed": False, "named": "x", "symptom": None, "named_as_injected": right}
    rows = {r["metric"]: r for r in check_faults.rows_of([inj(True, True), inj(True, False), inj(False, False)])}
    named = rows["injected fault types, 8 spreads: share of alarmed faults named as the injected type"]
    assert named["value"] == 0.5 and named["n"] == "2 alarmed"  # a fault that never alarmed was never named


# ----------------------------------------------------------------- check 3

def test_uncovered_readings_are_in_no_fault_type(model):
    prep = RT.Model(model).prep
    members = {n for ft in FT.TYPES for m in ft.members for n in m.names}
    assert {"vib_1x", "snd_band_23", "th_max_c"} <= members == set(check_unknown_fault.COVERED)
    prep.columns += ["vib_band_03", "vib_band_04", "vib_band_05", "vib_band_06", "snd_band_23", "snd_band_05"]
    prep.scale = np.r_[prep.scale, np.ones(6)]
    assert check_unknown_fault.uncovered(prep, "vib_band_") == ["vib_band_03", "vib_band_04", "vib_band_05", "vib_band_06"]
    assert check_unknown_fault.uncovered(prep, "snd_band_") == ["snd_band_05"]  # band 23 is grinding_or_hiss's
    picked = check_unknown_fault.pick(check_unknown_fault.uncovered(prep, "vib_band_"), np.random.default_rng(1))
    assert picked in (["vib_band_03", "vib_band_04", "vib_band_05"], ["vib_band_04", "vib_band_05", "vib_band_06"])
    assert check_unknown_fault.pick(["snd_band_05"], np.random.default_rng(1)) == []


def test_unknown_outcome_separates_unrecognised_from_a_wrong_name():
    w = judged([0] * 100 + [1] * 10, name="unrecognised_pattern")
    w["diagnosis_this_period"][100:110] = ["unrecognised_pattern"] * 10
    clean = check_unknown_fault.unknown_outcome(w, 100, 110)
    assert clean["unrecognised_on_screen"] is True and clean["unrecognised_periods"] == clean["alarm_periods"] == 10
    assert clean["names_given"] == {}
    w["diagnosis"][103:110] = ["speed_change"] * 7  # one named period, and the screen keeps that name
    w["diagnosis_this_period"][103] = "speed_change"
    wrong = check_unknown_fault.unknown_outcome(w, 100, 110)
    assert wrong["unrecognised_on_screen"] is False and wrong["unrecognised_periods"] == 9
    assert wrong["names_given"] == {"speed_change": 1}
    none = check_unknown_fault.unknown_outcome(judged([0] * 110), 100, 110)
    assert none["alarmed"] is False and none["unrecognised_on_screen"] is None and none["alarm_periods"] == 0
    rows = {r["metric"].split(": ")[1]: r["value"] for r in check_unknown_fault.rows_of(
        [dict(entry="conveyor", twin_alarmed=False, **clean), dict(entry="conveyor", twin_alarmed=True, **wrong),
         dict(entry="conveyor", twin_alarmed=False, **none)])}
    assert rows["share with an alarm after the onset"] == round(2 / 3, 4)
    assert rows["share whose twin without the pattern alarms in the same window"] == round(1 / 3, 4)
    assert rows["share of alarmed ones shown as unrecognised_pattern on the screen"] == 0.5
    assert rows["share of alarm periods that are unrecognised_pattern"] == 0.95


# ----------------------------------------------------------------- checks 4, 5, 6

def test_int8_rows_report_the_lowest_own_model_and_an_entry_only_if_it_has_the_number(model):
    assert 0.99 < model["training_report"][check_int8.KEY] <= 1.0  # the trainer's number is a correlation
    items, notes = check_int8.entry_items({"with": model, "without": {"training_report": {"inputs": 3}}})
    assert items == [{"entry": "with", "correlation": model["training_report"][check_int8.KEY]}]
    assert len(notes) == 1 and "library entry without: not measured" in notes[0]
    detail = items + [{"entry": "own model, conveyor", "seed": 1, "correlation": 0.99},
                      {"entry": "own model, conveyor", "seed": 2, "correlation": 0.97}]
    entry, own = check_int8.rows_of(detail)
    assert entry["metric"].endswith("the entry's network") and entry["n"] == "1 model"
    assert own["value"] == 0.97 and own["n"] == "2 models" and own["range"] == "0.97 to 0.99"
    assert own["metric"].endswith("lowest over machines")


def test_calibration_length_rows_give_false_alarms_missed_faults_and_refusals_per_length():
    n = lambda length, rows, hit: {"entry": "conveyor", "seed": 1, "what": "normal", "calibration_s": length, "period_s": 1,  # noqa: E731
                                   "rows": rows, "alarm_rows": hit, "share": hit / rows}
    f = lambda length, alarmed: {"entry": "conveyor", "seed": 1, "what": "fault", "calibration_s": length,  # noqa: E731
                                 "alarmed": alarmed}
    twin = lambda length, alarmed: dict(f(length, alarmed), what="twin")  # noqa: E731
    detail = [n(300, 100, 5), n(300, 100, 15), f(300, True), f(300, False), f(300, True), f(300, True),
              twin(300, False), twin(300, True), n(30, 100, 80), f(30, True), twin(30, True),
              {"entry": "conveyor", "seed": 2, "what": "twin", "calibration_s": 30, "refused": "short"}]
    rows = {r["metric"]: r for r in check_calibration_length.rows_of(detail)}
    assert list(rows)[0].startswith("300 s") and list(rows)[-1].startswith("30 s")  # from 5 minutes down
    assert rows["300 s calibration: share of held-out normal periods that alarm"]["value"] == 0.1
    assert rows["300 s calibration: share of simulated faults with no alarm after the onset"]["value"] == 0.25
    assert rows["30 s calibration: share of held-out normal periods that alarm"]["value"] == 0.8
    assert rows["30 s calibration: share of simulated faults with no alarm after the onset"]["value"] == 0.0
    assert rows["300 s calibration: share of the faults' no-fault twins that alarm in the fault window"]["value"] == 0.5
    assert rows["30 s calibration: share of the faults' no-fault twins that alarm in the fault window"]["value"] == 1.0
    assert rows["30 s calibration: calibrations the calibration code refused"]["value"] == 1
    assert "300 s calibration: calibrations the calibration code refused" not in rows
    assert check_calibration_length.LENGTHS_S[0] == C.CALIB_S == max(check_calibration_length.LENGTHS_S)


def test_fit_rows_count_passes_on_the_right_entry_and_fails_on_the_wrong_one():
    d = lambda entry, machine, passes, over: {"entry": entry, "machine": machine, "passes": passes,  # noqa: E731
                                              "right_entry": check_fit.RIGHT[(entry, machine)], "score_over_entry_line": over,
                                              "score_over_entry_heldout_median": 10 * over}
    detail = [d("conveyor", "conveyor", True, 0.2), d("conveyor", "conveyor", False, 1.4),
              d("conveyor", "server", False, 3.0), d("conveyor", "server", True, 0.9), d("conveyor", "server", False, 5.0),
              d("generic_rotating", "server", True, 0.1), d("generic_rotating", "conveyor", True, 0.3)]
    rows = {(r["entry"], r["metric"]): r for r in check_fit.rows_of(detail)}
    right = rows[("conveyor", "fit check, conveyor machines (right entry): share that pass")]
    assert right["value"] == 0.5 and right["standin"] == track3.FIT_RULE  # the rule is a starting value: marked
    assert rows[("conveyor", "fit check, server machines (wrong entry): share that fail")]["value"] == round(2 / 3, 4)
    pre = "fit check, server machines (wrong entry): median "
    line = rows[("conveyor", pre + "calibration score on the entry as shipped over the entry's threshold, median over machines")]
    assert line["value"] == 3.0 and "standin" not in line  # a plain number
    assert rows[("conveyor", pre + "held-out score after calibration over the entry's own held-out median score, "
                 "median over machines")]["value"] == 30.0
    assert rows[("generic_rotating", "fit check, server machines (right entry): share that pass")]["value"] == 1.0
    assert rows[("generic_rotating", "fit check, conveyor machines (neither right nor wrong): share that pass")]["value"] == 1.0


# ----------------------------------------------------------------- check 7

def test_ims_outcome_counts_stretches_before_the_last_as_false_alarms():
    hours = 72 + np.arange(20) / 2.0  # judged every half hour from hour 72 to 81.5; the run ends at hour 82
    quiet = check_ims.run_outcome(hours, np.zeros(20, bool), 82.0)
    assert quiet["alarm_stretches"] == 0 and quiet["false_alarm_stretches"] == 0
    assert quiet["first_alarm_hours_before_failure"] is None
    alarm = np.zeros(20, bool)
    alarm[14:] = True  # from hour 79 to the end
    one = check_ims.run_outcome(hours, alarm, 82.0)
    assert one["false_alarm_stretches"] == 0 and one["first_alarm_hours_before_failure"] == 3.0
    assert one["last_stretch_reaches_the_end"] is True and one["quiet_hours_before_first_alarm"] == 7.0
    alarm[2:4] = True  # an earlier stretch at hours 73-73.5 that cleared
    two = check_ims.run_outcome(hours, alarm, 82.0)
    assert two["alarm_stretches"] == 2 and two["false_alarm_stretches"] == 1
    assert two["first_alarm_hours_before_failure"] == 9.0 and two["last_stretch_hours_before_failure"] == 3.0
    rows = check_ims.rows_of(dict(two, hours_watched=10.0, learn_hours=72.0))
    assert [r["value"] for r in rows] == [1, 9.0, 3.0, 1.0] and all("MPU6050-like conversion" in r["metric"] for r in rows)


def test_ims_without_the_rows_file_writes_not_run_and_no_number(tmp_path):
    assert check_ims.main(["--rows", str(tmp_path / "absent.npz"), "--out", str(tmp_path / "ims.json")]) == 0
    r = json.loads((tmp_path / "ims.json").read_text())
    assert r["status"] == "not run" and r["rows"] == [] and "absent.npz" in r["reason"] and r["standin"] is None
    assert r["label"] == "public, MPU6050-like conversion" and "check_ims" in r["command"]


# ----------------------------------------------------------------- the report and the one command

def fake_results():
    ok = {"status": "ok", "reason": "", "command": "python3 -m training.validation.check_fit --seeds 1,2", "seeds": [1, 2],
          "data_source": "a simulator", "label": "simulated", "standin": None, "library_code": "track 3's code",
          "notes": ["one entry: not measured"],
          "rows": [{"entry": "conveyor", "metric": "share that pass", "value": 0.8125, "n": "16 machines", "range": "0.5 to 1",
                    "standin": "a placeholder rule"},
                   {"entry": "conveyor", "metric": "median seconds", "value": None, "n": "0 alarmed",
                    "data_source": "this row's own simulator"}]}
    results = {name: None for name, _ in report.CHECKS}
    results["fit"] = ok
    results["ims"] = dict(ok, status="not run", reason="the rows file is not there", rows=[], library_code=None,
                          label="public", notes=[])
    return results


def test_the_report_has_every_row_with_its_command_seed_source_and_label():
    text = report.render(fake_results())
    table = [line for line in text.splitlines() if line.startswith("| 6. Fit check")]
    assert len(table) == 2 and all(line.count("|") == len(report.COLUMNS) + 1 for line in table)
    cells = [c.strip() for c in table[0].strip("|").split("|")]
    assert cells == ["6. Fit check", "conveyor", "share that pass", "0.8125", "16 machines", "0.5 to 1",
                     "`python3 -m training.validation.check_fit --seeds 1,2`", "1,2", "a simulator", "simulated",
                     "a placeholder rule"]
    assert "| n/a |" in table[1] and table[1].rstrip(" |").endswith("| no")  # no number invented, no stand-in
    assert "| this row's own simulator |" in table[1] and "a simulator" not in table[1]
    assert "Library and calibration code under test: track 3's code." in text
    assert "- 6. Fit check: one entry: not measured." in text
    stand_in = report.render({**fake_results(), "fit": dict(fake_results()["fit"], standin="a stand-in module")})
    assert stand_in.count("| a stand-in module |") == 1  # a stand-in library marks every row without its own mark
    assert "- 7. NASA IMS run to failure, PCA default: not run. the rows file is not there." in text
    assert "- 1. Held-out normal data after calibration: not run. No result file" in text
    assert chr(0x2014) not in text  # no em dash


def test_the_committed_report_matches_the_committed_results():
    if not (C.HERE / "REPORT.md").exists():
        pytest.skip("no REPORT.md yet")
    assert (C.HERE / "REPORT.md").read_text() == report.render(report.load()), \
        "REPORT.md is out of date: run python3 -m training.validation.report"


def test_run_all_runs_every_check_and_drops_the_result_of_one_that_fails(tmp_path, monkeypatch, capsys):
    assert [f"check_{name}" for name, _ in report.CHECKS] == list(run_all.CHECKS)
    monkeypatch.setattr(C, "RESULTS", tmp_path)
    (tmp_path / "fit.json").write_text("{}")  # a stale result from an earlier run
    called = []
    for name in run_all.CHECKS:
        module = __import__(f"training.validation.{name}", fromlist=["main"])
        monkeypatch.setattr(module, "main", lambda argv, name=name: called.append((name, argv)) or 0)
    monkeypatch.setattr(check_fit, "main", lambda argv: 1 / 0)
    monkeypatch.setattr(report, "main", lambda argv: called.append(("report", argv)) or 0)
    assert run_all.main(["--seeds", "1,2", "--workers", "2"]) == 1
    assert [c[0] for c in called] == [c for c in run_all.CHECKS if c != "check_fit"] + ["report"]
    assert called[0][1] == ["--seeds", "1,2", "--workers", "2"]
    assert not (tmp_path / "fit.json").exists() and "check_fit: FAILED" in capsys.readouterr().out
