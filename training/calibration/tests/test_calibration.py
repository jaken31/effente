"""Tests for calibration on top of a library entry (effente.calibration). NumPy only.

Run with:  python3 -m training.calibration.tests.test_calibration   (or pytest on this file)

The "library entry" here is a fixture built in this file: a model trained the normal way on a
synthetic machine, and a manifest in the shape training/library/entries/<entry_id>/manifest.json
is agreed to have. It is not the real library. Every machine in these tests is synthetic.
"""

import copy
import json
import math
import sys
import tempfile
from pathlib import Path

import numpy as np

from effente import calibration as C
from effente import features as F
from effente import model_runtime as RT
from effente.trainer import summarise, train_model

NAMES = list(F.FEATURE_NAMES)
_CACHE = {}


def seconds(n, seed, start=0.0, vib=0.0, heat=0.0, kind="conveyor"):
    """One-a-second rows of a synthetic machine whose load swings every two minutes.

    kind "conveyor": vibration, sound and heat all rise with the load (as the entry's machine does).
    kind "other": a different machine type: vibration falls as the load rises, sound follows its own
    faster cycle, heat follows a third. vib and heat move this machine's levels (another machine of
    the type, or another mount)."""
    rng = np.random.default_rng(seed)
    t = start + np.arange(n, dtype=float)
    load = 0.5 + 0.3 * np.sin(2 * np.pi * t / 120)
    if kind == "conveyor":
        lv, ls, lh = load, load, load
    else:
        lv = 1.0 - load
        ls = 0.5 + 0.3 * np.sin(2 * np.pi * t / 45 + 1.0)
        lh = 0.5 + 0.3 * np.sin(2 * np.pi * t / 75 + 2.0)
    X = np.full((n, F.N_FEATURES), np.nan)

    def put(name, v):
        X[:, F.INDEX[name]] = v

    for a in "xyz":
        put(f"vib_rms_{a}", -1.6 + vib + 0.3 * lv + rng.normal(0, 0.02, n))
    put("vib_1x", -2.0 + vib + 0.4 * lv + rng.normal(0, 0.03, n))
    put("vib_2x", -2.5 + vib + 0.2 * lv + rng.normal(0, 0.03, n))
    put("vib_velocity_mm_s", (1.0 + 1.0 * lv) * 10 ** vib + rng.normal(0, 0.05, n))
    put("snd_level_db", 70 + 6 * ls + rng.normal(0, 0.3, n))
    put("snd_tone_mod_db", 3 + rng.normal(0, 0.5, n))
    put("snd_kurt_hf", 0.2 + rng.normal(0, 0.05, n))
    put("env_room_c", 22 + rng.normal(0, 0.2, n))
    put("env_hot_above_room_c", 15 + heat + 10 * lh + rng.normal(0, 0.3, n))
    put("th_max_c", X[:, F.INDEX["env_room_c"]] + X[:, F.INDEX["env_hot_above_room_c"]])
    put("trend_heat_swing_10m", 0.5 + rng.normal(0, 0.05, n))
    put("trend_heat_swing_1h", 1.0 + rng.normal(0, 0.08, n))
    put("trend_running", 1.0)
    return t, X


def entry():
    """The fixture entry: (model dict, manifest dict)."""
    if not _CACHE:
        t, X = seconds(2400, seed=1)
        d, _ = train_model(t, X, NAMES, source="synthetic test machine (fixture entry)")
        assert d["period_s"] == 1
        held = t >= t[0] + (t[-1] - t[0]) * 0.85  # the trainer's held-out part
        s = RT.Model(d).score(X[held])[0]
        manifest = {"entry_id": "fixture_conveyor", "version": "0", "normal_model": d["normal_model"],
                    "features_version": d["features_version"], "sources": ["synthetic"],
                    "held_out_score_quantiles": {"p50": float(np.percentile(s, 50)), "p90": float(np.percentile(s, 90)),
                                                 "p99": float(np.percentile(s, 99))},
                    "scorecard": {}, "status": "unverified"}
        _CACHE.update(d=d, manifest=manifest)
    return _CACHE["d"], _CACHE["manifest"]


def calibrated(key="shifted", **kw):
    """Calibrations used by several tests, done once. "shifted": another machine of the entry's type
    with higher vibration and heat."""
    if key not in _CACHE:
        d, _ = entry()
        t, X = seconds(300, seed=2, start=50_000, **(kw or dict(vib=0.5, heat=8.0)))
        _CACHE[key] = C.calibrate(d, t, X, NAMES)
    return _CACHE[key]


def watch(d, t, X):
    w = RT.Watcher(RT.Model(d))
    return [w.predict_at(float(ti), x) for ti, x in zip(t, X)]


def raises(fn, text):
    try:
        fn()
    except ValueError as e:
        assert text in str(e), str(e)
        return
    raise AssertionError("no ValueError")


# ----------------------------------------------------------------- the model that comes out

def test_the_result_is_a_model_file_the_runtime_loads_unchanged():
    d, _ = entry()
    cal, report = calibrated()
    again = json.loads(json.dumps(cal))  # as it would be written to and read from model.json
    m = RT.Model(again)
    assert again["format"] == RT.FORMAT and again["features_version"] == d["features_version"]
    assert set(cal) == set(d), set(cal) ^ set(d)  # no keys added to or dropped from the model file
    assert m.threshold == cal["threshold"] > 0 and m.period == 1
    assert report["calibrated"] is True and cal["training_report"]["calibrated"] is True
    assert cal["training_report"]["entry_training_report"] == d["training_report"]
    json.dumps(report)  # the report is plain JSON too


def test_the_network_is_kept_and_the_entry_is_not_touched():
    d, _ = entry()
    before = copy.deepcopy(d)
    t, X = seconds(300, seed=3, start=70_000, vib=0.3)
    cal, _ = C.calibrate(d, t, X, NAMES)
    assert d == before
    for key in ("autoencoder", "pca_score", "normal_model", "smooth_minutes", "feature_table", "trained_on"):
        assert cal[key] == d[key], key
    for key in ("columns", "names_in", "axis_col", "missing_cols", "names_out", "features_version", "clip"):
        assert cal["prep"][key] == d["prep"][key], key
    assert cal["prep"]["centre"] != d["prep"]["centre"]
    assert cal["fault_classifier"]["inputs"] == d["fault_classifier"]["inputs"]
    assert cal["fault_classifier"]["layers"] != d["fault_classifier"]["layers"]  # retrained on this machine


def test_levels_come_from_the_first_part_and_the_threshold_from_the_held_out_part():
    d, _ = entry()
    t, X = seconds(300, seed=4, start=90_000, vib=0.4)
    held = t >= t[0] + (t[-1] - t[0]) * 0.85
    X[held, F.INDEX["snd_level_db"]] += 3.0  # the last 15 % is louder: it must not move the levels
    cal, report = C.calibrate(d, t, X, NAMES)
    assert report["rows_fit"] == int((~held).sum()) and report["rows_held_out"] == int(held.sum()) == 45
    j = cal["prep"]["columns"].index("snd_level_db")
    assert math.isclose(cal["prep"]["centre"][j], float(np.median(X[~held, F.INDEX["snd_level_db"]])), rel_tol=1e-9)
    j = cal["prep"]["columns"].index("vib_1x")
    assert abs(cal["prep"]["centre"][j] - (d["prep"]["centre"][d["prep"]["columns"].index("vib_1x")] + 0.4)) < 0.05
    m = RT.Model(cal)
    s = m.score(X[held])[0]
    smoothed = [float(np.median(s[max(0, i - m.smooth + 1):i + 1])) for i in range(len(s))]
    # the threshold: the multiple times the median of the held-out scores, smoothed as the Watcher smooths
    assert math.isclose(cal["threshold"], C.THRESHOLD_MEDIAN_MULTIPLE * float(np.median(smoothed)), rel_tol=1e-9)
    assert math.isclose(report["held_out_smoothed_median"], float(np.median(smoothed)), abs_tol=1e-6)
    assert math.isclose(report["held_out_smoothed_max"], max(smoothed), abs_tol=1e-6)
    assert report["threshold_below_highest_held_out"] == (cal["threshold"] < max(smoothed))
    assert report["score_quantiles_held_out"]["p50"] > report["score_quantiles_fit"]["p50"]  # the louder part shows


def test_a_stop_in_the_middle_does_not_shrink_the_rows_fitted_on():
    d, _ = entry()
    t, X = seconds(600, seed=30, start=10_000)
    _, whole = C.calibrate(d, t, X, NAMES)
    assert (whole["rows_fit"], whole["rows_held_out"]) == (510, 90)  # no gap: the split by time, as before
    for ran_first in (20, 1):  # the machine ran this long, stood still for two hours, then ran the rest
        gap = t.copy()
        gap[ran_first:] += 7200
        cal, report = C.calibrate(d, gap, X, NAMES)
        assert (report["rows_fit"], report["rows_held_out"]) == (510, 90)
        j = cal["prep"]["columns"].index("vib_1x")  # the levels come from the first 510 running rows
        assert math.isclose(cal["prep"]["centre"][j], float(np.median(X[:510, F.INDEX["vib_1x"]])), rel_tol=1e-9)
    X[20:300, F.INDEX["trend_running"]] = 0.0  # the same with the stop recorded as rows: 320 s of running
    _, report = C.calibrate(d, t, X, NAMES)
    assert report["running_s"] == 320 and (report["rows_fit"], report["rows_held_out"]) == (272, 48)


def test_the_same_rows_and_seed_give_the_same_model():
    d, _ = entry()
    t, X = seconds(300, seed=5, start=20_000)
    a, ra = C.calibrate(d, t, X, NAMES, seed=7)
    order = np.random.default_rng(0).permutation(len(t))  # rows out of time order are put in order
    b, rb = C.calibrate(d, t[order], X[order], NAMES, seed=7)
    a.pop("created"), b.pop("created")
    assert a == b and ra == rb


# ----------------------------------------------------------------- what it refuses

def test_it_refuses_too_little_running():
    d, _ = entry()
    t, X = seconds(299, seed=6)
    raises(lambda: C.calibrate(d, t, X, NAMES), "too little recorded")
    t, X = seconds(600, seed=6)
    X[250:, F.INDEX["trend_running"]] = 0.0  # ten minutes recorded, but stopped after 250 s
    raises(lambda: C.calibrate(d, t, X, NAMES), "too little recorded: 250 s")
    X[:, F.INDEX["trend_running"]] = 0.0
    raises(lambda: C.calibrate(d, t, X, NAMES), "no running data")
    raises(lambda: C.calibrate(d, t[:10], X, NAMES), "10 times for 600 rows")


def test_it_refuses_a_sensor_that_gave_no_reading():
    d, _ = entry()
    t, X = seconds(600, seed=31)
    dead = np.full_like(X, np.nan)  # every reading missing: a model from this could never alarm
    raises(lambda: C.calibrate(d, t, dead, NAMES), "no reading the entry uses was measured")
    one = dead.copy()
    one[:, F.INDEX["vib_1x"]] = X[:, F.INDEX["vib_1x"]]  # one reading is enough to go on (MIN_READINGS)
    _, report = C.calibrate(d, t, one, NAMES)
    assert C.MIN_READINGS == 1 and len(report["readings_missing"]) == report["readings"] - 1


def test_it_refuses_times_and_settings_that_are_not_numbers():
    d, _ = entry()
    t, X = seconds(600, seed=33)
    for bad in (float("nan"), float("inf")):
        broken = t.copy()
        broken[5] = bad
        raises(lambda: C.calibrate(d, broken, X, NAMES), "1 of the 600 times are not finite")
        raises(lambda: C.calibrate(dict(d, period_s=10), broken, X, NAMES), "1 of the 600 times are not finite")
    for bad in (float("nan"), float("inf"), -1, "300", True):  # a NaN used to switch the length check off
        raises(lambda: C.calibrate(d, t[:100], X[:100], NAMES, min_running_s=bad), "min_running_s")
    a = C.calibrate(d, t, X, NAMES, threshold_median_multiple=np.float32(4), min_running_s=np.int64(300))
    b = C.calibrate(d, t, X, NAMES, threshold_median_multiple=4.0)
    assert a[0]["threshold"] == b[0]["threshold"] and a[1]["threshold_median_multiple"] == 4.0
    raises(lambda: C.calibrate(d, t, X, NAMES, threshold_median_multiple=np.bool_(True)), "threshold_median_multiple")


def test_min_running_s_can_be_lowered_but_not_below_the_floor():
    d, _ = entry()
    t, X = seconds(120, seed=7)
    cal, report = C.calibrate(d, t, X, NAMES, min_running_s=120)
    assert report["running_s"] == 120 and RT.Model(cal).threshold > 0
    t, X = seconds(C.MIN_ROWS - 1, seed=7)
    raises(lambda: C.calibrate(d, t, X, NAMES, min_running_s=10), f"at least {C.MIN_ROWS} s")


def test_it_refuses_an_entry_this_device_cannot_run():
    d, _ = entry()
    t, X = seconds(300, seed=8)
    raises(lambda: C.calibrate(dict(d, features_version="0123456789ab"), t, X, NAMES), "0123456789ab")
    raises(lambda: C.calibrate(dict(d, format="effente-model-0"), t, X, NAMES), "not an effente-model-1")


def test_it_refuses_a_model_that_is_already_calibrated():
    cal, _ = calibrated()
    t, X = seconds(300, seed=32, start=50_300, vib=0.5, heat=8.0)
    raises(lambda: C.calibrate(cal, t, X, NAMES), "already calibrated")


# ----------------------------------------------------------------- what calibration is for

def test_another_machine_of_the_type_alarms_on_the_entry_and_not_once_calibrated():
    d, _ = entry()
    cal, report = calibrated()
    t, X = seconds(600, seed=9, start=50_300, vib=0.5, heat=8.0)  # the same machine, carrying on, all normal
    shipped = [r["diagnosis"] for r in watch(d, t, X)[10:]]
    assert sum(x not in ("normal", "warming_up") for x in shipped) > 0.9 * len(shipped)  # the entry as shipped
    mine = [r["diagnosis"] for r in watch(cal, t, X)[10:]]
    assert sum(x not in ("normal", "warming_up") for x in mine) <= 0.01 * len(mine), set(mine)
    before = report["score_quantiles_before_calibration"]["p50"]
    assert before > 3 * report["score_quantiles_held_out"]["p50"]


def test_a_fault_after_calibration_alarms_and_is_named():
    cal, _ = calibrated()
    t, X = seconds(400, seed=10, start=50_300, vib=0.5, heat=8.0)
    X[120:, F.INDEX["vib_1x"]] += 0.6  # four times the once-per-turn vibration
    X[120:, F.INDEX["vib_velocity_mm_s"]] *= 2.0
    res = watch(cal, t, X)
    assert all(r["diagnosis"] in ("normal", "warming_up") for r in res[:120])
    late = [r["diagnosis"] for r in res[140:]]
    assert late.count("unbalance_like") > 0.8 * len(late), set(late)
    assert res[-1]["unknown_score"] > 1 and "vib_1x" in res[-1]["features"]


def test_a_reading_this_machine_lacks_is_not_judged_and_one_that_never_changes_keeps_the_entrys_spread():
    d, _ = entry()
    t, X = seconds(300, seed=11, start=30_000, vib=0.2)
    for n in ("snd_level_db", "snd_tone_mod_db", "snd_kurt_hf"):
        X[:, F.INDEX[n]] = np.nan  # no microphone on this device
    X[:, F.INDEX["trend_heat_swing_1h"]] = 1.25  # never changes here
    cal, report = C.calibrate(d, t, X, NAMES)
    cols = d["prep"]["columns"]
    assert sorted(report["readings_missing"]) == ["snd_kurt_hf", "snd_level_db", "snd_tone_mod_db"]
    assert report["readings_constant"] == ["trend_heat_swing_1h"]
    for n in report["readings_missing"]:
        j = cols.index(n)
        assert cal["prep"]["centre"][j] == d["prep"]["centre"][j] and cal["prep"]["scale"][j] == C.NOT_JUDGED
    j = cols.index("trend_heat_swing_1h")
    assert cal["prep"]["centre"][j] == 1.25 and cal["prep"]["scale"][j] == d["prep"]["scale"][j]
    assert any("not judged" in w for w in report["warnings"])
    assert all(np.isfinite(cal["prep"]["centre"])) and all(v > 0 for v in cal["prep"]["scale"])
    res = watch(cal, *seconds(200, seed=12, start=30_300, vib=0.2))  # the microphone is there later: still loads and judges
    assert res[-1]["diagnosis"] != "warming_up"


def test_rows_recorded_with_an_older_feature_list_are_mapped_by_name():
    d, _ = entry()
    t, X = seconds(300, seed=13, start=40_000, vib=0.1)
    keep = [i for i, n in enumerate(NAMES) if not n.startswith("th_cell_")]
    a, ra = C.calibrate(d, t, X, NAMES, seed=1)
    b, rb = C.calibrate(d, t, X[:, keep], [NAMES[i] for i in keep], seed=1)
    assert a["prep"] == b["prep"] and a["threshold"] == b["threshold"]


# ----------------------------------------------------------------- the entry as guard

def test_spreads_never_go_below_the_entrys():
    d, _ = entry()
    t, X = seconds(300, seed=17, start=45_000, vib=0.3)
    j = d["prep"]["columns"].index("env_room_c")
    X[:, F.INDEX["env_room_c"]] = 22 + np.random.default_rng(0).normal(0, 0.01, len(t))  # a very steady room, for 5 minutes
    cal, report = C.calibrate(d, t, X, NAMES)
    assert all(a >= b for a, b in zip(cal["prep"]["scale"], d["prep"]["scale"]))  # no spread below the entry's
    assert cal["prep"]["scale"][j] == d["prep"]["scale"][j]
    assert report["entry_threshold"] == round(d["threshold"], 4) and report["spreads"] == "never below the entry's"


def test_the_threshold_is_a_multiple_of_this_machines_held_out_median():
    d, _ = entry()
    t, X = seconds(300, seed=20, start=75_000, vib=0.2)
    out = {m: C.calibrate(d, t, X, NAMES, threshold_median_multiple=m) for m in (1.0, 5.0, 7.5)}
    median = out[1.0][1]["held_out_smoothed_median"]
    assert median > 0 and len({out[m][1]["held_out_smoothed_median"] for m in out}) == 1  # the same median each time
    for m in out:  # the threshold scales with the multiple and nothing else
        assert math.isclose(out[m][0]["threshold"], m * out[1.0][0]["threshold"], rel_tol=1e-9)
        assert math.isclose(out[m][0]["threshold"], m * median, rel_tol=1e-5)
        assert out[m][1]["threshold_median_multiple"] == m and out[m][1]["threshold"] == round(out[m][0]["threshold"], 4)
    default = C.calibrate(d, t, X, NAMES)  # the agreed signature, and the default (a starting value, simulated only)
    assert C.THRESHOLD_MEDIAN_MULTIPLE == 5.0 and default[0]["threshold"] == out[5.0][0]["threshold"]
    assert default[1]["threshold_median_multiple"] == 5.0
    for entry_threshold in (1e-3, 1e3):  # the entry's own threshold plays no part: no floor, no ceiling
        other = C.calibrate(dict(d, threshold=entry_threshold), t, X, NAMES)
        assert other[0]["threshold"] == default[0]["threshold"]
    assert not hasattr(C, "THRESHOLD_FLOOR_SHARE")
    for bad in (0, -1.0, float("nan"), float("inf"), True, "5"):
        raises(lambda: C.calibrate(d, t, X, NAMES, threshold_median_multiple=bad), "threshold_median_multiple")
    try:
        C.calibrate(d, t, X, NAMES, threshold_floor_share=0.25)
    except TypeError:
        pass
    else:
        raise AssertionError("the old keyword is still accepted")


def test_no_threshold_is_set_from_too_few_or_meaningless_held_out_scores():
    d, _ = entry()
    t, X = seconds(300, seed=24, start=77_000, vib=0.2)
    wide = dict(d, smooth_minutes=60)  # an entry that smooths over 60 rows: 300 s hold out only 45
    raises(lambda: C.calibrate(wide, t, X, NAMES), "45 held-out rows, fewer than the entry's smoothing window of 60")
    cal, _ = C.calibrate(dict(d, smooth_minutes=45), t, X, NAMES)  # exactly enough is accepted
    assert cal["threshold"] > 0

    class Scores:  # a model whose scores are all zero, then all NaN: no median to multiply
        def __init__(self, value):
            self.value = value

        def __call__(self, model, X):
            Z = model.prep.transform(X)
            return np.full(len(Z), self.value), np.zeros_like(Z), Z

    real = RT.Model.score
    for value, text in ((0.0, "median held-out score is 0.0"), (float("nan"), "median held-out score is nan")):
        RT.Model.score = lambda self, X, v=value: Scores(v)(self, X)
        try:
            raises(lambda: C.calibrate(d, t, X, NAMES), text)
        finally:
            RT.Model.score = real


def library_style_entry():
    """The fixture entry in the shape training/library builds: neutral preparation, widen factors and
    held readings under "calibration", no fault classifier and no baseline, and its own extra keys."""
    d, manifest = entry()
    lib = copy.deepcopy(d)
    cols = lib["prep"]["columns"]
    n = len(cols)
    lib["prep"]["centre"], lib["prep"]["scale"] = [0.0] * n, [1.0] * n
    lib.pop("fault_classifier"), lib.pop("baseline")
    lib.update(library_entry="fixture_conveyor", entry_version="0.1.0", source="entry",
               calibration={"widen": dict({c: 2.0 for c in cols}, a_reading_the_entry_does_not_use=3.0),
                            "hold": ["trend_", "env_room_c"], "min_s": 300})  # by name, as the manifests have it
    man = {"manifest": "effente-entry-1", "entry": "fixture_conveyor", "version": "0.1.0", "status": "unverified",
           "features_version": d["features_version"],
           "heldout_score_quantiles": {"p50": manifest["held_out_score_quantiles"]["p50"]}}
    return lib, man


def test_a_library_entrys_widen_factors_and_held_readings_are_used():
    lib, man = library_style_entry()
    t, X = seconds(300, seed=18, start=55_000, vib=0.4, heat=5.0)
    cal, report = C.calibrate(lib, t, X, NAMES)
    m = RT.Model(json.loads(json.dumps(cal)))
    cols = lib["prep"]["columns"]
    assert report["readings_held"] == ["env_room_c", "trend_heat_swing_10m", "trend_heat_swing_1h"]
    for name in report["readings_held"]:
        assert cal["prep"]["scale"][cols.index(name)] == C.NOT_JUDGED
    fit = t < t[0] + (t[-1] - t[0]) * 0.85
    v = X[fit, F.INDEX["vib_1x"]]
    q1, q3 = np.percentile(v, [25, 75])
    assert math.isclose(cal["prep"]["scale"][cols.index("vib_1x")], 2.0 * (q3 - q1) / 1.349, rel_tol=1e-9)
    assert report["spreads"] == "widened by the entry's factors"
    assert set(cal) == set(lib) | {"baseline"}  # the entry's own keys kept; no classifier where the entry has none
    assert report["fault_classes"] == 0 and report["classifier_exact_match"] is None
    assert cal["source"] == "calibrated" and cal["library_entry"] == "fixture_conveyor" and cal["entry_version"] == "0.1.0"
    assert cal["calibration"] == lib["calibration"] and m.classifier is None
    assert math.isclose(cal["threshold"], 5.0 * report["held_out_smoothed_median"], rel_tol=1e-5)
    v = C.fit_check(report, man)  # the library's manifest spelling of the held-out median
    assert v["ok"] is True and v["ratio"] > 0
    held = np.array(X[:200])  # a held reading far from anything seen does not alarm: it is not judged
    held[:, F.INDEX["env_room_c"]] += 30.0
    res = watch(cal, t[:200] + 300, held)
    assert all(r["diagnosis"] in ("normal", "warming_up") for r in res), {r["diagnosis"] for r in res}
    good = lib["calibration"]["widen"]
    for widen in ({c: f for c, f in good.items() if c != "vib_1x"},  # a reading the entry uses is not named
                  dict(good, vib_1x=0.0), dict(good, vib_1x=float("nan")), dict(good, vib_1x="2"),
                  [2.0] * len(cols), 2.0):  # one factor per column in a list is not the library's shape
        bad = dict(lib, calibration=dict(lib["calibration"], widen=widen))
        raises(lambda: C.calibrate(bad, t, X, NAMES), "widen factors")
    for hold in ("trend_", ["trend_", 3], {"trend_": True}):  # a bare string used to hold nothing, silently
        bad = dict(lib, calibration=dict(lib["calibration"], hold=hold))
        raises(lambda: C.calibrate(bad, t, X, NAMES), "hold must be a list")


def test_the_committed_manifests_widen_and_hold_are_accepted_when_copied_into_the_model():
    """The shape on master (training/library/entries/conveyor/manifest.json), copied into the model
    file: calibrate reads it. As shipped the model file has no such key, so this is not what runs."""
    from training.library.load import load_entry
    manifest, model = load_entry(Path(__file__).resolve().parents[2] / "library" / "entries", "conveyor")
    t, X = seconds(300, seed=21, start=85_000, vib=0.3)
    cal, report = C.calibrate(dict(model, calibration=manifest["calibration"]), t, X, NAMES)
    assert report["spreads"] == "widened by the entry's factors"
    cols = model["prep"]["columns"]
    j = cols.index("vib_1x")
    q1, q3 = np.percentile(X[:255, F.INDEX["vib_1x"]], [25, 75])
    assert math.isclose(cal["prep"]["scale"][j], manifest["calibration"]["widen"]["vib_1x"] * (q3 - q1) / 1.349, rel_tol=1e-9)
    assert report["readings_held"] and all(c.startswith(("time_", "trend_")) or c == "imu_temp_c" for c in report["readings_held"])


def test_the_committed_conveyor_entry_calibrates_with_its_own_spreads_as_the_floor():
    """The real library entry (training/library/entries/conveyor). Its widen factors and held readings
    are in manifest.json only; calibrate takes the model file alone, so it applies neither."""
    from training.library.load import load_entry
    manifest, model = load_entry(Path(__file__).resolve().parents[2] / "library" / "entries", "conveyor")
    assert "calibration" not in model and "widen" in manifest["calibration"] and "hold" in manifest["calibration"]
    before = copy.deepcopy(model)
    t, X = seconds(300, seed=21, start=85_000, vib=0.3)
    cal, report = C.calibrate(model, t, X, NAMES)
    assert model == before and set(cal) == set(model)
    RT.Model(json.loads(json.dumps(cal)))
    assert report["spreads"] == "never below the entry's" and report["readings_held"] == []
    cols = model["prep"]["columns"]
    off = set(report["readings_missing"])
    fitted = [c for c in cols if c not in off]
    assert "vib_1x" in fitted and len(fitted) >= 5
    fit = t < t[0] + (t[-1] - t[0]) * 0.85
    for c in fitted:  # the entry's spread or this machine's, whichever is larger: no widen factor applied
        j = cols.index(c)
        assert cal["prep"]["scale"][j] >= model["prep"]["scale"][j]
    j = cols.index("vib_1x")
    q1, q3 = np.percentile(X[fit, F.INDEX["vib_1x"]], [25, 75])
    here = (q3 - q1) / 1.349
    assert math.isclose(cal["prep"]["scale"][j], max(here, model["prep"]["scale"][j]), rel_tol=1e-9)
    assert manifest["calibration"]["widen"]["vib_1x"] > 1  # the manifest has a factor; it was not used
    assert math.isclose(cal["threshold"], 5.0 * report["held_out_smoothed_median"], rel_tol=1e-5)
    assert cal["threshold"] != model["threshold"]  # the entry's own threshold plays no part
    v = C.fit_check(report, manifest)
    assert set(v) == {"ok", "ratio", "reason"} and math.isfinite(v["ratio"]) and v["ratio"] > 0
    assert "not checked" not in v["reason"]
    assert math.isclose(v["ratio"], report["score_quantiles_held_out"]["p50"] / manifest["heldout_score_quantiles"]["p50"])


def test_the_laptop_command_runs_on_the_committed_entry_folder():
    from training.calibration.calibrate import main
    entry_folder = Path(__file__).resolve().parents[2] / "library" / "entries" / "conveyor"
    with tempfile.TemporaryDirectory() as tmp:
        t, X = seconds(320, seed=22, start=95_000, vib=0.2)
        np.savez(Path(tmp) / "rows.npz", t=t, X=X, feature_names=np.array(NAMES))
        out = Path(tmp) / "model.json"
        code = main(["--entry", str(entry_folder), "--rows", str(Path(tmp) / "rows.npz"), "--seconds", "300",
                     "--out", str(out)])
        assert code == 0 and RT.Model.load(out).d["training_report"]["calibrated"] is True  # 0: the fit check passed


def test_the_measurements_hold_variant_copies_the_manifests_list_into_a_copy():
    from training.calibration.measure import with_manifest_hold
    from training.library.load import load_entry
    manifest, model = load_entry(Path(__file__).resolve().parents[2] / "library" / "entries", "conveyor")
    held = with_manifest_hold(model, manifest)
    assert "calibration" not in model and held["calibration"] == {"hold": manifest["calibration"]["hold"]}
    t, X = seconds(300, seed=23, start=8 * 3600.0, vib=0.3)
    X[:, F.INDEX["imu_temp_c"]] = 30 + np.random.default_rng(1).normal(0, 0.05, len(t))
    X[:, F.INDEX["time_hour_sin"]] = np.sin(2 * np.pi * t / 86400)
    _, plain = C.calibrate(model, t, X, NAMES)
    _, with_hold = C.calibrate(held, t, X, NAMES)
    assert plain["readings_held"] == [] and "imu_temp_c" not in plain["readings_missing"]
    assert {"imu_temp_c", "time_hour_sin"} <= set(with_hold["readings_held"])
    assert all(c.startswith(("time_", "trend_")) or c == "imu_temp_c" for c in with_hold["readings_held"])


# ----------------------------------------------------------------- the fit check

def test_fit_check_passes_the_entrys_type_and_fails_another_type():
    d, manifest = entry()
    _, report = calibrated()
    ok = C.fit_check(report, manifest)
    assert ok["ok"] is True and 0 < ok["ratio"] <= C.FIT_MAX_RATIO and "no gross mismatch" in ok["reason"]
    assert "does not show" in ok["reason"]  # a pass is never worded as proof of the machine's type
    t, X = seconds(300, seed=14, start=60_000, kind="other")
    _, other = C.calibrate(d, t, X, NAMES)
    no = C.fit_check(other, manifest)
    assert no["ok"] is False and no["ratio"] > C.FIT_MAX_RATIO > ok["ratio"], (ok, no)
    assert "generic_rotating" in no["reason"] and "long stay" in no["reason"]
    assert set(ok) == set(no) == {"ok", "ratio", "reason"}


def test_fit_check_uses_the_held_out_medians():
    report = {"score_quantiles_held_out": {"p50": 4.0}, "features_version": "abc", "normal_model": "pca"}
    manifest = {"held_out_score_quantiles": {"p50": 2.0}, "features_version": "abc", "normal_model": "pca"}
    assert C.fit_check(report, manifest)["ratio"] == 2.0
    edge = dict(report, score_quantiles_held_out={"p50": 2.0 * C.FIT_MAX_RATIO})
    assert C.fit_check(edge, manifest)["ok"] is True  # at the limit still fits; over it does not
    over = dict(report, score_quantiles_held_out={"p50": 2.0 * C.FIT_MAX_RATIO * 1.001})
    assert C.fit_check(over, manifest)["ok"] is False
    for spelling in ({"q50": 2.0}, {"0.5": 2.0}, {"median": 2.0}):
        assert C.fit_check(report, dict(manifest, held_out_score_quantiles=spelling))["ratio"] == 2.0


def test_fit_check_reads_the_librarys_manifest():
    """The manifest training/library writes (branch feat/model-library): heldout_score_quantiles with
    exactly p50, p90, p99 and max."""
    manifest = {"entry_id": "conveyor", "version": "0.1.0", "normal_model": "pca", "features_version": "89613129193f",
                "sources": [{"name": "sim_conveyor", "kind": "simulated", "used_for": "training"}],
                "heldout_score_quantiles": {"p50": 2.0861323723227825, "p90": 2.5786691741110297,
                                            "p99": 26.307316227339868, "max": 40.00208696907036},
                "scorecard": {}, "status": "demo"}
    report = {"score_quantiles_held_out": {"p50": 4.172264744645565}, "features_version": "89613129193f",
              "normal_model": "pca"}
    v = C.fit_check(report, manifest)
    assert v["ok"] is True and math.isclose(v["ratio"], 2.0) and "not checked" not in v["reason"]
    assert C.fit_check(dict(report, score_quantiles_held_out={"p50": 10.0}), manifest)["ok"] is False
    d, _ = entry()  # and end to end: a real calibration report against a manifest in that shape
    _, rep = calibrated()
    man = dict(manifest, features_version=d["features_version"])
    v = C.fit_check(rep, man)
    assert math.isclose(v["ratio"], rep["score_quantiles_held_out"]["p50"] / 2.0861323723227825)


def test_the_entrys_period_is_used():
    d, _ = entry()
    t, X = seconds(600, seed=19, start=65_000)
    cal, report = C.calibrate(d, t, X, NAMES)
    assert cal["period_s"] == d["period_s"] == report["period_s"] == 1
    slow = dict(d, period_s=10)  # an entry on 10 s summaries: 600 s give 60 rows, 590 s too few
    cal, report = C.calibrate(slow, t, X, NAMES)
    assert cal["period_s"] == 10 and report["rows_fit"] + report["rows_held_out"] == 60
    raises(lambda: C.calibrate(slow, t[:590], X[:590], NAMES), "at least 600 s")


def test_a_fit_that_cannot_be_checked_is_not_a_fit():
    report = {"score_quantiles_held_out": {"p50": 2.0}, "features_version": "abc", "normal_model": "pca"}
    manifest = {"held_out_score_quantiles": {"p50": 2.0}, "features_version": "abc", "normal_model": "pca"}
    assert C.fit_check(report, manifest)["ok"] is True
    cases = [(report, {k: v for k, v in manifest.items() if k != "held_out_score_quantiles"}, "no held-out median"),
             (report, dict(manifest, held_out_score_quantiles={"p50": 0.0}), "no held-out median"),
             (report, dict(manifest, held_out_score_quantiles={"p50": float("nan")}), "no held-out median"),
             (report, dict(manifest, features_version="other"), "features_version"),
             (report, dict(manifest, normal_model="autoencoder"), "normal_model"),
             (dict(report, score_quantiles_held_out={}), manifest, "calibration report"),
             (dict(report, score_quantiles_held_out={"p50": 0.0}), manifest, "calibration report"),
             (dict(report, score_quantiles_held_out={"p50": -1.0}), manifest, "calibration report"),
             (report, {"fit_check": {"entry_heldout_score_median": 2.0}}, "no held-out median"),  # on no manifest
             ({}, manifest, "calibration report")]
    for rep, man, text in cases:
        v = C.fit_check(rep, man)
        assert v["ok"] is False and math.isnan(v["ratio"]) and "not checked" in v["reason"] and text in v["reason"], v


# ----------------------------------------------------------------- on the device, and from a laptop

def test_the_device_model_slot_runs_a_calibrated_model():
    from device.intake.live import ModelSlot
    cal, _ = calibrated()
    loader = Path(__file__).resolve().parents[2] / "model" / "effente_loader.py"
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "model.json"
        path.write_text(json.dumps(cal))
        slot = ModelSlot(loader, path)
        assert slot.status == "ready", slot.error
        t, X = seconds(30, seed=15, start=50_300, vib=0.5, heat=8.0)
        res = [slot.predict(x, ti) for ti, x in zip(t, X)]
        assert res[0]["diagnosis"] == "warming_up" and res[-1]["diagnosis"] == "normal"


def test_the_laptop_command_writes_the_model_and_says_whether_it_fits():
    from training.calibration.calibrate import main
    d, manifest = entry()
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "entry").mkdir()
        (tmp / "entry" / "model.json").write_text(json.dumps(d))
        (tmp / "entry" / "manifest.json").write_text(json.dumps(manifest))
        for name, kind in (("same", "conveyor"), ("other", "other")):
            t, X = seconds(400, seed=16, start=80_000, kind=kind)
            np.savez(tmp / f"{name}.npz", t=t, X=X, feature_names=np.array(NAMES))
        base = ["--entry", str(tmp / "entry"), "--seconds", "300"]
        assert main(base + ["--rows", str(tmp / "same.npz"), "--out", str(tmp / "out" / "same.json")]) == 0
        m = RT.Model.load(tmp / "out" / "same.json")
        assert m.d["training_report"]["running_s"] == 300
        assert main(base + ["--rows", str(tmp / "other.npz"), "--out", str(tmp / "other.json")]) == 2
        assert (tmp / "other.json").exists()  # written all the same: the person decides
        short = ["--entry", str(tmp / "entry"), "--seconds", "100", "--rows", str(tmp / "same.npz")]
        assert main(short + ["--out", str(tmp / "short.json")]) == 1 and not (tmp / "short.json").exists()


if __name__ == "__main__":
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok    {name}")
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(f"FAIL  {name}: {type(e).__name__}: {e}")
    print("all passed" if not failed else f"{failed} failed")
    sys.exit(1 if failed else 0)
