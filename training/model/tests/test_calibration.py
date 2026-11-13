"""Tests for calibrating a library entry to a machine, and for the fit check.

Run with:  python3 -m training.model.tests.test_calibration

Until the library's entries are merged, the entry here is a stand-in: train_model on the conveyor
simulator (machine A), with its manifest written by hand in the library's layout. Machine B is another simulated conveyor
(another seed, a heavier load, a warmer room, another time of day). The machine of another type is
the simulated server rack. Everything is seeded, so the tests give the same numbers every run.
"""

import json
import sys
import tempfile
from pathlib import Path

import numpy as np

from device.intake import extract as E
from effente import calibration as C
from effente import features as F
from effente import model_runtime as RT
from effente import trainer as TR
from effente.simulate_conveyor import ConveyorSim, Fault
from effente.simulate_server import ServerRackSim
from training.model import calibrate as CLI

NAMES = list(F.FEATURE_NAMES)
HOUR = 3600
A_SECONDS = 1200  # the stand-in entry's recording
CALIBRATION_S = 600  # machine B's calibration: 10 minutes of it running
WATCH_S = 240  # the minutes straight after it
FAULT_AFTER_S = 60  # the fault starts this long into the watch
B = dict(days=1, seed=5, realistic=True, load_scale=1.15, ambient_offset_c=5.0, start_s=14 * HOUR,
         end_s=14 * HOUR + CALIBRATION_S + WATCH_S)
_CACHE = {}


def rows(sim):
    """One feature row a second from a simulated machine: (t, X)."""
    out = list(E.feature_stream(sim, hop_s=1.0))
    return np.array([t for t, _ in out]), np.array([x for _, x in out])


def cached(key, make):
    if key not in _CACHE:
        _CACHE[key] = make()
    return _CACHE[key]


def machine_a():
    return cached("a", lambda: rows(ConveyorSim(days=1, seed=1, realistic=True, faults=[], start_s=9 * HOUR,
                                                end_s=9 * HOUR + A_SECONDS)))


def machine_b():
    """Machine B with no fault: calibration, then the minutes after it."""
    return cached("b", lambda: rows(ConveyorSim(faults=[], **B)))


def machine_b_loose():
    """The same seconds of machine B, with looseness starting a minute after calibration."""
    at = B["start_s"] + CALIBRATION_S + FAULT_AFTER_S
    return cached("b_loose", lambda: rows(ConveyorSim(faults=[Fault("looseness", at, B["end_s"], "test")], **B)))


def server_rack():
    return cached("server", lambda: rows(ServerRackSim(days=1, seed=2, faults=[], start_s=10 * HOUR,
                                                       end_s=10 * HOUR + 300)))


def entry():
    """The stand-in library entry: a model trained on machine A, and its manifest written by hand."""
    def make():
        t, X = machine_a()
        d, _ = TR.train_model(t, X, NAMES, source="simulated conveyor A")
        model = RT.Model(d)
        ts, Xs, _, _ = TR.running_summaries(t, X, NAMES, model.period)
        _, held_out = TR.time_split(ts)  # the rows that set the entry's threshold
        manifest = {"entry_id": "conveyor", "version": "0.1.0",
                    "heldout_score_quantiles": C.score_quantiles(model.score(Xs[held_out])[0])}
        return C.Entry(d, manifest)
    return cached("entry", make)


def calibrated():
    def make():
        t, X = machine_b()
        return C.calibrate(entry(), t[:CALIBRATION_S], X[:CALIBRATION_S], NAMES, source="simulated conveyor B")
    return cached("calibrated", make)


def alarms(d, t, X):
    """Through the device's Watcher, as the device does: which rows it called abnormal."""
    w = RT.Watcher(RT.Model(d))
    return np.array([w.predict_at(float(ti), x)["diagnosis"] not in ("normal", "warming_up", "stopped")
                     for ti, x in zip(t, X)])


def test_the_network_is_kept_and_the_machine_parts_are_refitted():
    before = json.dumps(entry().model, sort_keys=True)
    d, report = calibrated()
    e = entry().model
    assert json.dumps(e, sort_keys=True) == before  # the entry itself is not touched
    assert d["autoencoder"] == e["autoencoder"] and d["pca_score"] == e["pca_score"]  # int8 weights, bit for bit
    for key in ("format", "features_version", "period_s", "smooth_minutes", "normal_model"):
        assert d[key] == e[key], key
    assert d["prep"]["columns"] == e["prep"]["columns"]  # the network's inputs stay the same readings
    assert d["prep"]["centre"] != e["prep"]["centre"] and d["prep"]["scale"] != e["prep"]["scale"]
    for key in ("threshold", "residual_scale", "baseline"):
        assert d[key] != e[key], key
    assert d["fault_classifier"]["layers"] != e["fault_classifier"]["layers"]
    assert d["fault_classifier"]["classes"] == e["fault_classifier"]["classes"]
    assert set(d) == set(e)  # no new keys: the model file format is unchanged
    assert report["calibrated"] and report["entry"] == "conveyor" and report["entry_version"] == "0.1.0"
    assert report["rows_learned"] + report["rows_calibration"] == CALIBRATION_S and report["threshold"] > 0


def test_the_calibrated_file_loads_in_the_runtime_and_scores():
    d, _ = calibrated()
    t, X = machine_b()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "model.json"
        path.write_text(json.dumps(d))
        model = RT.Model.load(path)
    s, r, Z = model.score(X[:CALIBRATION_S])
    assert s.shape == (CALIBRATION_S,) and np.isfinite(s).all() and (s > 0).all()
    assert np.isfinite(r).all() and np.abs(Z).max() <= 8
    # in its own spreads, this machine's readings sit around 0: the median is this machine's
    assert abs(float(np.median(Z[:, :len(model.prep.columns)]))) < 0.1
    w = RT.Watcher(model)
    results = [w.predict_at(float(ti), x) for ti, x in zip(t[:60], X[:60])]
    assert results[0]["diagnosis"] == "warming_up" and results[-1]["diagnosis"] == "normal"
    assert results[-1]["unknown_score"] < 1


def test_whole_number_weights_give_the_same_scores_as_the_float_network():
    d, _ = calibrated()
    e = entry().model
    # The entry's float network, fitted again the way train_model fitted it (the file holds int8 only).
    t, X = machine_a()
    ts, Xs, _, _ = TR.running_summaries(t, X, NAMES, e["period_s"])
    fit, _ = TR.time_split(ts)
    layers, _ = TR.fit_pca(np.nan_to_num(RT.Model(e).prep.transform(Xs[fit])))
    assert [RT.quantize(W, b, "linear") for W, b in layers] == e["autoencoder"]  # it is the entry's network
    # Machine B through the calibrated file's int8 runtime, and through the float network.
    tb, Xb = machine_b()
    _, r, Z = RT.Model(d).score(Xb)
    h = Z.astype(np.float64)
    for W, b in layers:
        h = h @ W.astype(np.float64) + b
    s_float, s_int8 = ((Z - h) ** 2).mean(1), (r ** 2).mean(1)
    assert np.corrcoef(s_float, s_int8)[0, 1] > 0.99
    assert np.abs(s_int8 / s_float - 1).max() < 0.05


def test_calibrated_on_machine_b_is_quiet_on_its_normal_and_alarms_on_its_fault():
    d, _ = calibrated()
    t, X = machine_b()
    tf, Xf = machine_b_loose()
    onset = CALIBRATION_S + FAULT_AFTER_S
    assert np.array_equal(X[:onset], Xf[:onset], equal_nan=True)  # the same machine until the fault starts
    after = slice(CALIBRATION_S, CALIBRATION_S + WATCH_S)  # rows calibration never saw
    quiet = alarms(d, t[after], X[after])
    assert quiet.sum() <= 0.02 * WATCH_S, quiet.sum()
    faulty = alarms(d, tf[after], Xf[after])
    assert faulty[:FAULT_AFTER_S].sum() <= 0.02 * WATCH_S  # still quiet before it starts
    grown = faulty[FAULT_AFTER_S + 60:]  # the simulated fault takes a minute to switch on
    assert grown.mean() > 0.95, grown.mean()
    # What calibration is for: the entry as shipped, on the same normal rows, alarms nearly all the time.
    assert alarms(entry().model, t[after], X[after]).mean() > 0.9


def test_fit_check_passes_on_the_same_machine_type_and_fails_on_another():
    _, report = calibrated()
    same = report["fit_check"]
    assert same["fits"] and 1 < same["median_ratio"] < C.FIT_MAX_MEDIAN_RATIO / 1.5, same
    ts, Xs = server_rack()
    d, report = C.calibrate(entry(), ts, Xs, NAMES)
    other = report["fit_check"]
    assert not other["fits"] and other["median_ratio"] > 1.5 * C.FIT_MAX_MEDIAN_RATIO, other
    assert "does not look like that type" in other["reason"]
    RT.Model(d)  # a failed fit check still returns a model that loads; the caller decides what to say
    # The same rule on plain scores, as the manifest's numbers are made.
    held_out = entry().manifest["heldout_score_quantiles"]
    scores = np.linspace(0.5, 1.5, 101) * held_out["p50"]
    assert C.fit_check(scores, held_out)["median_ratio"] == 1.0
    assert not C.fit_check(scores * (C.FIT_MAX_MEDIAN_RATIO + 1), held_out)["fits"]


def test_too_little_running_data_is_refused():
    t, X = machine_b()

    def refused(t, X, **kw):
        try:
            C.calibrate(entry(), t, X, NAMES, **kw)
        except ValueError as e:
            return str(e)
        return None

    assert "too little recorded: 299 rows of 1 s" in refused(t[:299], X[:299])  # the trainer's own minimum
    assert "record at least 5 minutes" in refused(t[:299], X[:299])
    assert "record at least 15 minutes" in refused(t[:CALIBRATION_S], X[:CALIBRATION_S], min_running_s=900)
    stopped = X[:CALIBRATION_S].copy()
    stopped[150:, F.INDEX["trend_running"]] = 0.0  # it ran for 150 s of the 600: stopped time does not count
    assert "too little recorded: 150 rows" in refused(t[:CALIBRATION_S], stopped)
    stopped[:, F.INDEX["trend_running"]] = 0.0
    assert refused(t[:CALIBRATION_S], stopped) == "no running data recorded"
    try:  # the trainer refuses the same rows the same way
        TR.train_model(t[:299], X[:299], NAMES, period=1)
        assert False, "the trainer took 299 rows"
    except ValueError as e:
        assert str(e) == refused(t[:299], X[:299])


def test_an_unusable_entry_is_refused():
    e = entry()
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)

        def refused(model, manifest):
            (folder / "model.json").write_text(json.dumps(model))
            (folder / "manifest.json").write_text(json.dumps(manifest))
            try:
                C.load_entry(folder)
            except ValueError as err:
                return str(err)
            return None

        assert refused(e.model, e.manifest) is None
        assert "heldout_score_quantiles" in refused(e.model, {"entry_id": "conveyor"})
        assert "heldout_score_quantiles" in refused(e.model, dict(e.manifest, heldout_score_quantiles={"p50": 0}))
        assert "0123456789ab" in refused(dict(e.model, features_version="0123456789ab"), e.manifest)
        assert "not a library entry" in refused(e.model, ["not", "a", "manifest"])
        assert "not a library entry" in str(_raises(C.load_entry, folder / "nowhere"))


def _raises(fn, *args):
    try:
        fn(*args)
    except ValueError as e:
        return e
    raise AssertionError("no ValueError")


def test_the_command_line_calibrates_an_entry_folder():
    e = entry()
    t, X = machine_b()
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        folder = tmp / "library" / "conveyor"
        folder.mkdir(parents=True)
        (folder / "model.json").write_text(json.dumps(e.model))
        (folder / "manifest.json").write_text(json.dumps(e.manifest))
        np.savez(tmp / "b.npz", t=t[:CALIBRATION_S], X=X[:CALIBRATION_S], feature_names=NAMES)
        np.savez(tmp / "short.npz", t=t[:120], X=X[:120], feature_names=NAMES)
        out = tmp / "models" / "b.json"
        assert CLI.main(["--entry", str(folder), "--rows", str(tmp / "b.npz"), "--out", str(out)]) == 0
        d = json.loads(out.read_text())
        assert d["autoencoder"] == e.model["autoencoder"] and d["trained_on"] == str(tmp / "b.npz")
        assert d["training_report"]["fit_check"]["fits"]
        assert d["threshold"] == calibrated()[0]["threshold"]  # the same result as calling calibrate()
        RT.Model.load(out)
        short = tmp / "models" / "short.json"
        assert CLI.main(["--entry", str(folder), "--rows", str(tmp / "short.npz"), "--out", str(short)]) == 1
        assert CLI.main(["--entry", str(tmp), "--rows", str(tmp / "b.npz"), "--out", str(short)]) == 1
        assert not short.exists()  # refused: nothing written


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
