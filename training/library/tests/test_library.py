"""Tests for the model library: the build, its sources, and the entries committed in entries/.

Run with:  python3 -m training.library.tests.test_library      (minutes: it simulates machines)
"""

import hashlib
import json
import sys
import tempfile
from pathlib import Path

import numpy as np

from training.library import build as B  # first: it sets one maths thread before NumPy loads
from effente import fault_types as FT
from effente import features as F
from effente import model_runtime as RT
from effente import trainer as TR
from effente.model_input import Prep, _transform_raw
from training.library.catalog import ENTRIES, Entry, FaultCheck
from training.library import load as L
from training.library.sources import (DEVICE_SENSORS, MPU6050, MPU6500, PublicSet, Recording, SimConveyor, SimPump,
                                      SimServerRack)

MANIFEST_FIELDS = ["entry_id", "version", "normal_model", "features_version", "sources", "heldout_score_quantiles",
                   "scorecard", "status", "built", "model_sha1", "calibration"]
VERDICTS = ("not validated", "simulated only")  # and "validated on <data>"
_CACHE = {}

# A small entry: two short simulated machines that differ in speed, load and mount, one fault check.
MACHINES = (SimConveyor("sim_a", seed=1, hours=11 / 60),
            SimConveyor("sim_b", seed=2, hours=11 / 60, speed=0.7, load=1.2, turn_deg=(90, 0, 0), gain=0.5))
SMALL = Entry("small", "0.0.1", "unverified", sources=MACHINES + (PublicSet("some_public_set", "a script"),),
              fault_checks=(FaultCheck("unbalance", "unbalance_like"),))


def small(key="first", seed=0):
    """SMALL built into its own folder (kept for the whole run): the build's result."""
    if key not in _CACHE:
        tmp = tempfile.TemporaryDirectory()
        _CACHE[key] = (tmp, B.build(SMALL, seed, tmp.name))
    return _CACHE[key][1]


def machine_rows():
    """The two machines' prepared rows, as the build makes them: [(t, X)]."""
    if "rows" not in _CACHE:
        _CACHE["rows"] = [B.prepare(s.load_rows(0)) for s in MACHINES]
    return _CACHE["rows"]


def committed():
    """(entry, model path, manifest) for every entry in the catalog."""
    out = []
    for entry in ENTRIES.values():
        folder = B.ENTRIES_DIR / entry.entry_id
        out.append((entry, folder / "model.json", json.loads((folder / "manifest.json").read_text())))
    return out


def check_manifest(m: dict, model: dict):
    assert list(m) == MANIFEST_FIELDS, list(m)
    assert all(isinstance(m[k], str) and m[k] for k in ("entry_id", "version", "features_version"))
    assert m["normal_model"] == "pca" == model["normal_model"]
    assert m["features_version"] == model["features_version"] == RT._version()
    for s in m["sources"]:  # an evaluation source says whether it was run; the build runs none
        extra = ["evaluated"] if s["used_for"] == "evaluation" else []
        assert list(s) in (["name", "kind", "used_for"] + extra, ["name", "kind", "used_for"] + extra + ["note"]), s
        assert s.get("evaluated", False) is False
    assert all(s["kind"] in ("simulated", "public", "device") and s["used_for"] in ("training", "evaluation")
               for s in m["sources"])
    assert any(s["used_for"] == "training" for s in m["sources"])
    assert not any(s["kind"] == "public" and s["used_for"] == "training" for s in m["sources"])
    q = m["heldout_score_quantiles"]
    assert list(q) == ["p50", "p90", "p99", "max"] and all(isinstance(v, float) for v in q.values())
    assert 0 < q["p50"] <= q["p90"] <= q["p99"] <= q["max"]
    assert list(m["scorecard"]) == list(FT.NAMES)  # every fault type, none invented
    assert all(v in VERDICTS or v.startswith("validated on ") for v in m["scorecard"].values())
    if not any(s["kind"] == "device" for s in m["sources"]):  # no device recordings: nothing is "validated on"
        assert all(v in VERDICTS for v in m["scorecard"].values())
    assert m["status"] in ("demo", "unverified", "verified")
    assert list(m["built"]) == ["command", "seed", "git_commit"]
    assert m["built"]["command"] == f"python3 -m training.library.build {m['entry_id']} --seed {m['built']['seed']}"
    assert isinstance(m["built"]["seed"], int) and m["built"]["git_commit"]
    assert m["model_sha1"] == hashlib.sha1(json.dumps(model, separators=(",", ":")).encode()).hexdigest()
    cal = m["calibration"]
    assert list(cal) == ["min_s", "hold", "widen", "prep", "threshold"]
    assert cal["min_s"] == 300 and cal["hold"] == list(B.HOLD)
    first = next(s["name"] for s in m["sources"] if s["used_for"] == "training")
    assert first in cal["prep"] and "held-out" in cal["threshold"]  # what the file's own numbers stand for
    assert list(cal["widen"]) == model["prep"]["columns"]  # one factor per reading the entry uses
    assert all(B.WIDEN_RANGE[0] <= v <= B.WIDEN_RANGE[1] for v in cal["widen"].values())


# ----------------------------------------------------------------- the build

def test_the_build_is_reproducible():
    a, b = small("first"), small("again")
    assert a["model_path"] != b["model_path"]
    assert a["model_path"].read_bytes() == b["model_path"].read_bytes()
    assert a["manifest_path"].read_bytes() == b["manifest_path"].read_bytes()  # the same commit, so all of it
    other = small("seed 1", seed=1)  # the seed reaches the simulated machines and the trainer
    assert other["model_path"].read_bytes() != a["model_path"].read_bytes()
    assert other["manifest"]["heldout_score_quantiles"] != a["manifest"]["heldout_score_quantiles"]


def test_no_clock_reading_is_left_in_the_model_file():
    built = small()
    d = built["model"]
    assert d["training_report"]["fit_seconds"] is None
    if built["manifest"]["built"]["git_commit"] != "unknown":
        assert d["created"] == B._git()[1]  # the commit's date, not the time of the build


def test_each_model_loads_in_the_runtime():
    paths = [small()["model_path"]] + [p for _, p, _ in committed()]
    for path in paths:
        model = RT.Model.load(path)  # refuses another format or feature list
        d = json.loads(path.read_text())
        assert d["format"] == "effente-model-1" and d["normal_model"] == "pca" and model.period == B.PERIOD_S
        s, r, Z = model.score(np.full((1, F.N_FEATURES), np.nan))  # a row with every sensor missing still scores
        assert np.isfinite(s).all() and Z.shape[1] == model.prep.n_out
        assert RT.Watcher(model).predict_at(0.0, np.full(F.N_FEATURES, np.nan))["diagnosis"] == "warming_up"


def test_the_model_file_has_no_new_fields():
    t, X, _, _ = B.pool(machine_rows())
    plain, _ = TR.train_model(t, X, list(F.FEATURE_NAMES), period=B.PERIOD_S)  # the trainer, nothing else
    for d in [small()["model"]] + [json.loads(p.read_text()) for _, p, _ in committed()]:
        assert set(d) == set(plain), set(d) ^ set(plain)
        assert set(d["training_report"]) == set(plain["training_report"])


def test_the_manifest_has_every_required_field():
    built = small()
    check_manifest(json.loads(built["manifest_path"].read_text()), built["model"])
    assert built["manifest"]["sources"] == [{"name": "sim_a", "kind": "simulated", "used_for": "training"},
                                            {"name": "sim_b", "kind": "simulated", "used_for": "training"},
                                            {"name": "some_public_set", "kind": "public", "used_for": "evaluation",
                                             "evaluated": False}]
    for _, path, manifest in committed():
        check_manifest(manifest, json.loads(path.read_text()))


def test_the_file_size_is_reported():
    built = small()
    size = built["model_path"].stat().st_size
    assert built["model_bytes"] == size > 0
    text = B.describe(built)
    assert f"model.json: {size} bytes" in text and "target about 50 kB" in text
    for entry, path, _ in committed():
        print(f"      {entry.entry_id}: model.json {path.stat().st_size} bytes "
              f"(target about {B.TARGET_BYTES // 1000} kB)")
        assert path.stat().st_size < 2_000_000  # "a few MB" is the most the repo takes


def test_the_scorecard_follows_the_fault_checks():
    built = small()
    check = built["fault_checks"]["unbalance"]
    assert check["expects"] == "unbalance_like" and isinstance(check["passed"], bool)
    card = built["manifest"]["scorecard"]
    assert card["unbalance_like"] == ("simulated only" if check["passed"] else "not validated")
    assert all(v == "not validated" for k, v in card.items() if k != "unbalance_like")  # nothing else was checked
    assert check["passed"] == (check["quiet_before"] and check["said_at_end"] == "unbalance_like")
    assert check["highest_level"] > 1 or check["said_at_end"] == "normal"  # no alarm unless the score passed the line


# ----------------------------------------------------------------- held-out scores and pooling

def test_the_held_out_scores_are_the_trainers_own_split():
    """The trainer does not hand back its held-out rows. Its threshold is a function of every held-out
    score (their mean, spread and highest), so getting the same threshold from our rows proves they are
    the same rows with the same scores."""
    t, X, _, _ = B.pool(machine_rows())
    built = small()
    d, report = built["model"], built["report"]
    held = B.held_out(t)
    assert int(held.sum()) == report["rows_calibration"] and int((~held).sum()) == report["rows_learned"]
    model = RT.Model(d)
    s = model.score(X)[0][held]
    assert TR.gamma_threshold(TR._causal_median(s, model.smooth), 7 * 86400 / B.PERIOD_S) == d["threshold"]
    # and the manifest's numbers are those scores' quantiles
    assert built["manifest"]["heldout_score_quantiles"] == {
        "p50": float(np.percentile(s, 50)), "p90": float(np.percentile(s, 90)),
        "p99": float(np.percentile(s, 99)), "max": float(s.max())}
    assert built["held_out_rows"] == len(s)


def test_the_split_holds_out_the_end_of_every_machine():
    rows = machine_rows()
    longer = [rows[0], (rows[1][0][:150], rows[1][1][:150])]  # machines need not be the same length
    for machines in (rows, longer):
        t, X, who, _ = B.pool(machines)
        assert len(t) == sum(len(r[0]) for r in machines) and (np.diff(t) == B.PERIOD_S).all()
        held = B.held_out(t)
        for m, (tm, _) in enumerate(machines):
            mine = held[who == m]  # this machine's rows, in its own time order
            assert len(mine) == len(tm) and (np.diff(mine.astype(int)) >= 0).all()  # learned first, then held out
            assert abs(mine.mean() - B.CALIB_SHARE) < 0.02, mine.mean()


def test_pooled_rows_keep_each_machines_own_normal():
    """What the build promises: after pooling, the trainer's own Prep sees each row as far from normal
    as that row's machine's own Prep does. So the network learns what calibration will feed it."""
    rows = machine_rows()
    t, X, _, express = B.pool(rows)
    names = list(F.FEATURE_NAMES)
    fit = ~B.held_out(t)
    trainer_prep = Prep.fit(X[fit], names, t[fit], running_only=False)  # as train_model fits it
    for m, (tm, Xm) in enumerate(rows):
        through = (tm - tm[0]) / (tm[-1] - tm[0])
        own = Prep.fit(Xm[through < 1 - B.CALIB_SHARE], names, running_only=False)
        shared = [c for c in own.columns if c in trainer_prep.columns]
        assert len(shared) > 40, len(shared)
        z_own = own.transform(Xm)[:, [own.columns.index(c) for c in shared]]
        z_pooled = trainer_prep.transform(express[m](Xm))[:, [trainer_prep.columns.index(c) for c in shared]]
        off = np.abs(z_own - z_pooled)
        # Not exact: the two fits split a few rows differently at the cut, and a pooled median sits between two rows.
        assert np.median(off) < 0.02 and np.percentile(off, 99) < 0.5, (np.median(off), np.percentile(off, 99))
    # Raw pooling would not: the two machines sit at different levels, far apart in the pooled spreads.
    j = F.INDEX["vib_speed_hz"]
    gap_raw = abs(np.nanmedian(rows[0][1][:, j]) - np.nanmedian(rows[1][1][:, j]))
    gap_pooled = abs(np.nanmedian(express[0](rows[0][1])[:, j]) - np.nanmedian(express[1](rows[1][1])[:, j]))
    assert gap_raw > 5 and gap_pooled < 0.1 * gap_raw, (gap_raw, gap_pooled)


def test_one_source_is_trained_as_recorded():
    t, X = machine_rows()[0]
    tp, Xp, _, express = B.pool([(t, X)])
    assert tp is t and Xp is X and np.array_equal(express[0](X), X, equal_nan=True)


def test_the_transform_inverse_round_trips():
    """build._raw_from_transformed must undo Prep's own transform for every feature (it fails here if
    Prep gains a transform the build does not know)."""
    rng = np.random.default_rng(0)
    names = list(F.FEATURE_NAMES)
    X = rng.uniform(0.5, 50.0, (40, len(names)))
    T = _transform_raw(X, names)
    assert np.allclose(B._raw_from_transformed(T, names), X, rtol=1e-9)
    assert np.allclose(_transform_raw(B._raw_from_transformed(T, names), names), T, rtol=1e-9)


# ----------------------------------------------------------------- sources

def test_simulated_machines_differ_in_speed_and_mount():
    (_, a), (_, b) = machine_rows()
    speed = lambda X: float(np.nanmedian(X[:, F.INDEX["vib_speed_hz"]]))  # noqa: E731
    assert 0.65 < speed(b) / speed(a) < 0.75, (speed(a), speed(b))  # sim_b turns 0.7 times as fast
    gravity = lambda X: np.array([np.nanmedian(X[:, F.INDEX[f"vib_gravity_{k}"]]) for k in "xyz"])  # noqa: E731
    ga, gb = gravity(a), gravity(b)
    assert abs(ga[2]) > 0.9 and abs(gb[1]) > 0.9, (ga, gb)  # sim_b's head is turned a quarter about x
    assert abs(np.linalg.norm(ga) - np.linalg.norm(gb)) < 0.02  # turned, not scaled


def test_entries_are_built_on_the_imu_alone():
    src = SimConveyor("x", seed=1, hours=0.01)._source(0, 8 * 3600.0, 8 * 3600.0 + 36, [])
    assert src.sensors == DEVICE_SENSORS
    assert {p.sensor for p in src.packets()} == set(DEVICE_SENSORS)
    d = small()["model"]
    cols = d["prep"]["columns"]
    assert cols and not [c for c in cols if c.startswith(("snd_", "th_", "env_"))], cols
    for _, path, _ in committed():
        cols = json.loads(path.read_text())["prep"]["columns"]
        assert not [c for c in cols if c.startswith(("snd_", "th_", "env_"))]


def test_the_server_rack_gives_rows_and_may_not_train():
    rack = SimServerRack("rack", hours=1 / 60)
    rows = rack.load_rows(0)
    assert rows.X.shape == (60, F.N_FEATURES) and rack.sensor not in (MPU6500, MPU6050) and rack.used_for == "evaluation"
    assert np.isfinite(rows.X[:, F.INDEX["vib_speed_hz"]]).any() and np.isnan(rows.X[:, F.INDEX["snd_level_db"]]).all()


def test_widen_factors_compare_the_whole_source_with_one_window():
    names = list(F.FEATURE_NAMES)
    col = "vib_speed_hz"
    X = np.full((1200, len(names)), np.nan)
    rng = np.random.default_rng(0)
    # four 300-row windows at levels 3 apart, each with spread 1: the whole source is about 3 times as wide
    X[:, F.INDEX[col]] = 20 + np.repeat([0.0, 3.0, 6.0, 9.0], 300) + rng.standard_normal(1200)
    w = B.widen_factors([(np.arange(1200.0), X)], [col])
    assert list(w) == [col] and 2.5 < w[col] < 5.0, w
    steady = X.copy()
    steady[:, F.INDEX[col]] = 20 + rng.standard_normal(1200)
    assert abs(B.widen_factors([(np.arange(1200.0), steady)], [col])[col] - 1.0) < 0.2
    assert B.widen_factors([(np.arange(100.0), X[:100])], [col]) == {col: 1.0}  # shorter than one window


def test_load_entry_checks_the_model_file():
    built = small()
    folder = built["model_path"].parent.parent
    manifest, model = L.load_entry(folder, "small")
    assert manifest == built["manifest"] and model == built["model"]
    assert [m["entry_id"] for m in L.load_catalog(folder)] == ["small"]
    assert [m["entry_id"] for m in L.load_catalog(B.ENTRIES_DIR)] == sorted(ENTRIES)
    for entry in ENTRIES:
        L.load_entry(B.ENTRIES_DIR, entry)
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "small"
        copy.mkdir()
        (copy / "manifest.json").write_text(built["manifest_path"].read_text())
        (copy / "model.json").write_bytes(built["model_path"].read_bytes() + b" ")  # one byte more
        for call, error, says in ((lambda: L.load_entry(tmp, "small"), ValueError, "sha1"),
                                  (lambda: L.load_entry(tmp, "missing"), FileNotFoundError, "no entry"),):
            try:
                call()
            except error as e:
                assert says in str(e), e
            else:
                raise AssertionError(f"expected {error.__name__}")
        (copy / "model.json").write_bytes(built["model_path"].read_bytes())
        (copy / "manifest.json").write_text(json.dumps(dict(built["manifest"], features_version="other")))
        try:
            L.load_entry(tmp, "small")
        except ValueError as e:
            assert "features" in str(e)
        else:
            raise AssertionError("another feature list was accepted")


def test_only_mpu6500_sources_may_train():
    assert SimConveyor("x").sensor == MPU6500 and SimPump("x").sensor == MPU6050
    for bad in (SimPump("pump", used_for="training"), PublicSet("ims", "a script", used_for="training"),
                SimServerRack("rack", used_for="training")):
        try:
            B.build(Entry("bad", "0", "unverified", sources=(bad,)), 0, tempfile.gettempdir())
        except ValueError as e:
            assert "evaluation only" in str(e)
        else:
            raise AssertionError(f"{bad.name} was allowed to train an entry")
    for entry in ENTRIES.values():  # the catalog: public sets and the MPU6050 pump are evaluation only
        assert all(s.used_for == "evaluation" for s in entry.sources if s.kind == "public" or s.sensor != MPU6500)
        assert all(s.used_for in ("training", "evaluation") for s in entry.sources)


def test_the_pump_simulator_gives_rows():
    rows = SimPump("pump", minutes=1).load_rows(0)  # minutes: the pump is evaluation only and short
    assert rows.X.shape == (60, F.N_FEATURES) and rows.names == list(F.FEATURE_NAMES)
    assert 25 < np.nanmedian(rows.X[:, F.INDEX["vib_speed_hz"]]) < 31  # a 4-pole motor on 60 Hz mains


def test_a_device_recording_can_replace_the_simulator():
    """The conveyor entry's one-line change: a Recording in place of the SimConveyor."""
    from device.intake.extract import feature_stream
    from device.intake.recorder import Recorder
    from effente.history import History
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp) / "session-01"
        src = SimConveyor("stand-in for the device", seed=3, hours=0.1)._source(0, 8 * 3600.0, 8 * 3600.0 + 360, [])
        hist = History()
        rec = Recorder(folder, lambda: src.info, src.sensors, label="normal", hop_s=1.0, history=hist)
        for t0, x in feature_stream(src, hop_s=1.0, tap=rec.on_packet, history=hist):
            rec.on_window(t0, x)
        rec.close()
        entry = Entry("conveyor", "0.0.1", "demo", sources=(Recording("conveyor_session_01", str(folder)),),
                      fault_checks=ENTRIES["conveyor"].fault_checks)
        built = B.build(entry, 0, Path(tmp) / "entries")
        assert built["manifest"]["sources"] == [{"name": "conveyor_session_01", "kind": "device", "used_for": "training"}]
        assert set(built["manifest"]["scorecard"].values()) == {"not validated"}  # no fault can be switched on
        assert built["rows_per_machine"] == [360]
        RT.Model.load(built["model_path"])


# ----------------------------------------------------------------- the committed entries

def test_the_committed_entries_are_the_catalogs():
    assert set(ENTRIES) == {"generic_rotating", "conveyor"}
    assert sorted(p.name for p in B.ENTRIES_DIR.iterdir()) == sorted(ENTRIES)
    for entry, path, m in committed():
        assert sorted(p.name for p in path.parent.iterdir()) == ["manifest.json", "model.json"]
        assert (m["entry_id"], m["version"], m["status"]) == (entry.entry_id, entry.version, entry.status)
        assert [(s["name"], s["kind"], s["used_for"]) for s in m["sources"]] == [
            (s.name, s.kind, s.used_for) for s in entry.sources]
        ran = [s for s in entry.sources if s.used_for == "training"]
        assert json.loads(path.read_text())["training_report"]["rows_learned"] > 0.8 * 3600 * sum(s.hours for s in ran) * 0.5
        assert json.loads(path.read_text())["trained_on"] == f"library entry {entry.entry_id} {entry.version}"
    assert ENTRIES["conveyor"].status == "demo"
    assert {s.kind for s in ENTRIES["conveyor"].sources} == {"simulated"}  # the simulator alone, for now
    for entry, _, m in committed():  # the server rack: listed as the wrong machine, never trained on, not run
        rack = [s for s in m["sources"] if s["name"] == "sim_server_rack"]
        assert rack == [{"name": "sim_server_rack", "kind": "simulated", "used_for": "evaluation", "evaluated": False,
                         "note": "wrong machine: a server rack, for the fit check"}]
    generic = [s for s in ENTRIES["generic_rotating"].sources if s.used_for == "training"]
    assert len(generic) >= 2 and len({(s.speed, s.load, s.turn_deg, s.gain) for s in generic}) == len(generic)


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
