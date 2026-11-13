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
    assert cal["threshold"] >= max(smoothed) - 1e-9  # never below the highest held-out (smoothed) score
    assert report["score_quantiles_held_out"]["p50"] > report["score_quantiles_fit"]["p50"]  # the louder part shows


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


def test_a_reading_this_machine_lacks_or_never_changes_keeps_the_entrys_spread():
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

def test_a_short_calibration_is_never_stricter_than_the_entry():
    d, _ = entry()
    t, X = seconds(300, seed=17, start=45_000, vib=0.3)
    j = d["prep"]["columns"].index("env_room_c")
    X[:, F.INDEX["env_room_c"]] = 22 + np.random.default_rng(0).normal(0, 0.01, len(t))  # a very steady room, for 5 minutes
    cal, report = C.calibrate(d, t, X, NAMES)
    assert all(a >= b for a, b in zip(cal["prep"]["scale"], d["prep"]["scale"]))  # no spread below the entry's
    assert cal["prep"]["scale"][j] == d["prep"]["scale"][j]
    assert cal["threshold"] == max(report["threshold_fitted_here"], d["threshold"]) >= d["threshold"]
    assert report["entry_threshold"] == round(d["threshold"], 4) and report["spreads"] == "never below the entry's"


def test_the_threshold_floor_share_sets_how_much_of_the_entrys_threshold_is_kept():
    d, _ = entry()
    high = dict(d, threshold=10 * d["threshold"])  # an entry whose own threshold is far above this machine's
    t, X = seconds(300, seed=20, start=75_000, vib=0.2)
    out = {share: C.calibrate(high, t, X, NAMES, threshold_floor_share=share) for share in (0.0, 0.5, 1.0)}
    fitted = out[0.0][1]["threshold_fitted_here"]
    assert fitted < 0.5 * high["threshold"]
    assert math.isclose(out[0.0][0]["threshold"], fitted, abs_tol=1e-4)  # 0: the threshold fitted here alone
    assert out[1.0][0]["threshold"] == high["threshold"]  # 1: never below the entry's
    assert out[0.5][0]["threshold"] == 0.5 * high["threshold"]  # 0.5: between the two
    assert out[0.0][0]["threshold"] < out[0.5][0]["threshold"] < out[1.0][0]["threshold"]
    assert [out[s][1]["threshold_floor_share"] for s in (0.0, 0.5, 1.0)] == [0.0, 0.5, 1.0]
    default = C.calibrate(high, t, X, NAMES)  # the default is the module constant, 1.0
    assert C.THRESHOLD_FLOOR_SHARE == 1.0 and default[0]["threshold"] == out[1.0][0]["threshold"]
    assert default[1]["threshold_floor_share"] == 1.0
    low = dict(d, threshold=1e-3)  # a fitted threshold above the floor is used whatever the share
    assert C.calibrate(low, t, X, NAMES, threshold_floor_share=1.0)[0]["threshold"] == out[0.0][0]["threshold"]
    raises(lambda: C.calibrate(d, t, X, NAMES, threshold_floor_share=-0.1), "threshold_floor_share")
    raises(lambda: C.calibrate(d, t, X, NAMES, threshold_floor_share=float("nan")), "threshold_floor_share")


def library_style_entry():
    """The fixture entry in the shape training/library builds: neutral preparation, widen factors and
    held readings under "calibration", no fault classifier and no baseline, and its own extra keys."""
    d, manifest = entry()
    lib = copy.deepcopy(d)
    n = len(lib["prep"]["columns"])
    lib["prep"]["centre"], lib["prep"]["scale"] = [0.0] * n, [1.0] * n
    lib.pop("fault_classifier"), lib.pop("baseline")
    lib.update(library_entry="fixture_conveyor", entry_version="0.1.0", source="entry",
               calibration={"widen": [2.0] * n, "hold": ["trend_", "env_room_c"], "min_s": 300})
    man = {"manifest": "effente-entry-1", "entry": "fixture_conveyor", "version": "0.1.0", "status": "unverified",
           "features_version": d["features_version"],
           "fit_check": {"entry_heldout_score_median": manifest["held_out_score_quantiles"]["p50"], "rule": "open"}}
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
    assert cal["threshold"] >= lib["threshold"]
    v = C.fit_check(report, man)  # the library's manifest spelling of the held-out median
    assert v["ok"] is True and v["ratio"] > 0
    held = np.array(X[:200])  # a held reading far from anything seen does not alarm: it is not judged
    held[:, F.INDEX["env_room_c"]] += 30.0
    res = watch(cal, t[:200] + 300, held)
    assert all(r["diagnosis"] in ("normal", "warming_up") for r in res), {r["diagnosis"] for r in res}
    bad = copy.deepcopy(lib)
    bad["calibration"]["widen"] = bad["calibration"]["widen"][:-1]
    raises(lambda: C.calibrate(bad, t, X, NAMES), "widen factors")
    bad["calibration"]["widen"] = [0.0] * len(cols)
    raises(lambda: C.calibrate(bad, t, X, NAMES), "widen factors")


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
