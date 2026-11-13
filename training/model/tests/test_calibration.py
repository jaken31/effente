"""Tests for calibration on top of a library entry (effente.calibration).

Run with:  python3 -m training.model.tests.test_calibration   (needs SciPy, for the simulator)

Everything here is simulated (effente.simulate_conveyor). The "library entry" is a model trained the
normal way on 20 minutes of simulated conveyor A; machine B is the same simulator with another seed.
Nothing here says how calibration does on real machines.
"""

import copy
import json
import sys

import numpy as np

from device.intake import contract as C
from device.intake.extract import feature_stream
from effente import features as F
from effente import model_runtime as RT
from effente.calibration import OFF_SCALE, calibrate
from effente.simulate_conveyor import ConveyorSim
from effente.trainer import train_model

NAMES = list(F.FEATURE_NAMES)
START = 10 * 3600  # Monday 10:00, mid-shift
WARM, CALIB = 300, 330  # B's first 5 minutes fill the history features; the next 330 s calibrate
_CACHE = {}


def rows(seed, seconds):
    sim = ConveyorSim(days=1, seed=seed, start_s=START, end_s=START + seconds, faults=[])
    out = list(feature_stream(sim, hop_s=1.0))
    return np.array([t for t, _ in out]), np.array([x for _, x in out])


def data():
    """The entry (trained on A), B's calibration rows and B's later normal rows, made once."""
    if not _CACHE:
        tA, XA = rows(1, 1200)
        tB, XB = rows(2, 1200)
        entry, _ = train_model(tA, XA, NAMES, source="simulated conveyor A")
        _CACHE.update(A=(tA, XA), entry=entry, entry_json=json.dumps(entry, sort_keys=True),
                      calib=(tB[WARM:WARM + CALIB], XB[WARM:WARM + CALIB]),
                      later=(tB[WARM + CALIB:], XB[WARM + CALIB:]))
        _CACHE["model"], _CACHE["report"] = calibrate(entry, *_CACHE["calib"], NAMES, source="simulated conveyor B")
    return _CACHE


def test_the_result_keeps_the_entrys_network_and_alarm_line_and_learns_new_levels():
    c = data()
    entry, d = c["entry"], json.loads(json.dumps(c["model"]))  # as it comes back from a model file
    m = RT.Model(d)
    assert m.prep.n_out == RT.Model(entry).prep.n_out == entry["autoencoder"][0]["shape"][0]
    for k in ("autoencoder", "threshold", "pca_score", "residual_scale", "period_s", "smooth_minutes",
              "normal_model", "fault_classifier", "fault_types", "feature_table"):
        assert d[k] == entry[k], k
    for k in ("columns", "names_in", "axis_col", "missing_cols", "names_out", "features_version"):
        assert d["prep"][k] == entry["prep"][k], k
    assert d["prep"]["centre"] != entry["prep"]["centre"] and d["prep"]["scale"] != entry["prep"]["scale"]
    assert d["source"] == "calibrated" and d["trained_on"] == "simulated conveyor B" and d["created"]
    assert d["library_entry"] is None and d["entry_version"] is None  # a plain train_model entry has no name
    assert d["baseline"]["levels"] and d["calibration_report"] == c["report"]
    r = c["report"]
    assert r["rows_used"] == CALIB and r["period_s"] == entry["period_s"]
    assert r["median_score_over_threshold"] > 0 and 0 <= r["share_rows_over_threshold"] <= 1
    assert r["warnings"] == d["baseline"]["warnings"]


def test_the_entrys_name_version_and_pipeline_are_carried_over():
    c = data()
    entry = dict(c["entry"], library_entry="conveyor", entry_version="0.1", pipeline={"window_s": 1.0})
    d, _ = calibrate(entry, *c["calib"], NAMES)
    assert (d["library_entry"], d["entry_version"], d["pipeline"]) == ("conveyor", "0.1", {"window_s": 1.0})


def test_held_and_absent_readings_are_switched_off_and_score_as_usual():
    c = data()
    t, X = c["calib"]
    cols = c["entry"]["prep"]["columns"]
    held = [n for n in cols if n.startswith(("time_", "trend_"))]
    assert held and "snd_level_db" in cols  # or this test would check nothing
    X = X.copy()
    X[: int(0.6 * len(X)), F.INDEX["snd_level_db"]] = np.nan  # this machine mostly lacks the reading
    d, rep = calibrate(c["entry"], t, X, NAMES)
    assert rep["columns_off"]["held"] == held and "snd_level_db" in rep["columns_off"]["absent"]
    off = held + rep["columns_off"]["absent"]
    for n in off:
        j = cols.index(n)
        assert d["prep"]["centre"][j] == 0.0 and d["prep"]["scale"][j] == OFF_SCALE, n
    assert sum(s == OFF_SCALE for s in d["prep"]["scale"]) == len(off)  # and nothing else is off
    m = RT.Model(d)
    lt, lX = c["later"]
    wild = lX[:50].copy()
    wild[:, [F.INDEX[n] for n in off]] += 1000.0
    assert np.allclose(m.score(wild)[0], m.score(lX[:50])[0], rtol=1e-5, atol=1e-6)
    assert (m.prep.out_of_range(wild) == m.prep.out_of_range(lX[:50])).all()
    # a custom hold list: a whole name, and no prefixes
    entry = dict(c["entry"], calibration={"hold": ["snd_level_db"]})
    assert calibrate(entry, *c["calib"], NAMES)[1]["columns_off"]["held"] == ["snd_level_db"]


def test_widen_factors_multiply_the_refitted_spread():
    c = data()
    cols = c["entry"]["prep"]["columns"]
    widen = [1.0 + (j % 3) for j in range(len(cols))]
    d, _ = calibrate(dict(c["entry"], calibration={"widen": widen}), *c["calib"], NAMES)
    plain, wide = np.array(c["model"]["prep"]["scale"]), np.array(d["prep"]["scale"])
    on = plain != OFF_SCALE
    assert on.sum() > 100 and np.allclose(wide[on], plain[on] * np.array(widen)[on])
    assert (wide[~on] == OFF_SCALE).all() and d["prep"]["centre"] == c["model"]["prep"]["centre"]
    assert d["threshold"] == c["entry"]["threshold"]
    try:
        calibrate(dict(c["entry"], calibration={"widen": [2.0]}), *c["calib"], NAMES)
    except ValueError as e:
        assert "widen" in str(e)
    else:
        raise AssertionError("a widen list of the wrong length was accepted")


def test_too_few_rows_is_refused():
    c = data()
    t, X = c["calib"]
    for n, kw in ((299, {}), (CALIB, {"min_rows": CALIB + 1})):
        try:
            calibrate(c["entry"], t[:n], X[:n], NAMES, **kw)
        except ValueError as e:
            assert "too little recorded" in str(e)
        else:
            raise AssertionError(f"{n} rows were accepted")
    stopped = X.copy()
    stopped[:, F.INDEX["trend_running"]] = 0.0  # stopped rows do not count
    try:
        calibrate(c["entry"], t, stopped, NAMES)
    except ValueError as e:
        assert "too little recorded: 0 rows" in str(e)
    else:
        raise AssertionError("a stopped machine was accepted")


def test_the_watcher_runs_on_the_machines_later_normal_rows():
    c = data()
    t, X = c["later"]
    w = RT.Watcher(RT.Model(json.loads(json.dumps(c["model"]))))
    judged = {}
    for ti, x in zip(t, X):
        r = w.predict_at(float(ti), x)
        rec = {"device_id": "dev-test", "machine_id": "conveyor-b", "model_version": "calibrated-test",
               "at": C.iso_utc(), **C.record_fields(r)}
        assert C.check_record(rec) == [], C.check_record(rec)
        if "minute" in r:
            judged[r["minute"]] = r["diagnosis"]
    alarm = np.mean([v not in ("normal", "stopped", "warming_up") for v in judged.values()])
    # Simulated, one seed, widen factors of 1, the 9 minutes after calibration: not a measure of anything real.
    print(f"      {len(judged)} periods judged after calibration, {100 * alarm:.1f} % in alarm (simulated)")
    assert len(judged) > 500 and alarm < 1.0  # loose on purpose: it is not alarming all the time


def test_train_model_is_unaffected():
    c = data()
    assert json.dumps(c["entry"], sort_keys=True) == c["entry_json"]  # calibrate did not touch the entry
    again, _ = train_model(*c["A"], NAMES, source="simulated conveyor A")

    def fixed(d):  # all but the time it was made and how long the fit took
        d = copy.deepcopy(d)
        d.pop("created")
        d["training_report"].pop("fit_seconds")
        return json.dumps(d, sort_keys=True)

    assert fixed(again) == fixed(c["entry"])
    assert "source" not in again and "calibration_report" not in again


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
