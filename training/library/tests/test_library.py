"""Tests for the model library: the builder, the catalog loader and the committed entries.

Run with:  python3 -m training.library.tests.test_library      (about a minute: it simulates machines)
"""

import json
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np

from effente import features as F
from effente import model_runtime as RT
from effente import trainer as TR
from training.library import build as B
from training.library import simulators as SIM
from training.library.catalog import MANIFEST, load_catalog, load_entry

MANIFEST_KEYS = ["manifest", "entry", "version", "title", "status", "use", "pipeline", "features_version", "sensors",
                 "mount", "profile", "fault_types", "calibration", "fit_check", "trained_on", "scorecard",
                 "model_file", "model_sha1", "notes"]
SHORT_H = 0.12  # 432 simulated seconds: just over the trainer's 300-row minimum
_CACHE = {}


def short_rows(k: int):
    """A few minutes of one simulated machine: (t, X)."""
    if k not in _CACHE:
        machine = (SIM.CONVEYORS[0], SIM.CONVEYORS[1], SIM.SERVER_RACK)[k]
        _CACHE[k] = SIM.simulated_rows(machine, SHORT_H)
    return _CACHE[k]


def pooled():
    if "pooled" not in _CACHE:
        sources = [(f"m{k}", "simulated", *short_rows(k)) for k in range(3)]
        _CACHE["pooled"] = (sources, *B.fit_entry(sources, "small", "0.0.1"))
    return _CACHE["pooled"]


def check_entry_model(d: dict):
    model = RT.Model(d)  # loads as is; refuses another format or feature list
    assert d["format"] == "effente-model-1" and d["normal_model"] == "pca" and model.period == 1
    assert d["pipeline"] == "rotating-v1" and d["source"] == "entry" and d["library_entry"] and d["entry_version"]
    assert "fault_classifier" not in d and model.classifier is None
    cols = d["prep"]["columns"]
    assert d["prep"]["centre"] == [0.0] * len(cols) and d["prep"]["scale"] == [1.0] * len(cols)  # neutral
    cal = d["calibration"]
    assert cal["hold"] == ["time_", "trend_", "imu_temp_c"] and cal["min_s"] == 300
    assert len(cal["widen"]) == len(cols) and all(1.0 <= w <= 5.0 for w in cal["widen"])
    assert [f["name"] for f in d["feature_table"]] == cols
    assert len(d["fault_types"]) == len(RT.FT.TYPES)
    assert not any(c.startswith(("snd_", "th_", "env_")) for c in cols), "sound, heat or room readings in an entry"
    s, _, Z = model.score(np.full((1, F.N_FEATURES), np.nan))
    assert np.isfinite(s).all() and Z.shape[1] == model.prep.n_out
    return model


def test_one_source_is_train_model():
    t, X = short_rows(0)
    names = list(F.FEATURE_NAMES)
    d, _ = TR.train_model(t, X, names, period=1, normal_model="pca")
    entry, info = B.fit_entry([("one", "simulated", t, X)])
    assert entry["threshold"] == d["threshold"], (entry["threshold"], d["threshold"])
    assert entry["prep"]["columns"] == d["prep"]["columns"]
    assert entry["autoencoder"] == d["autoencoder"] and entry["pca_score"] == d["pca_score"]
    assert entry["residual_scale"] == d["residual_scale"]
    assert entry["fault_types"] == d["fault_types"] and entry["feature_table"] == d["feature_table"]
    own = info["sources"][0]["prep"].to_dict()  # the source's own statistics, which the entry does not carry
    assert own == d["prep"]
    s_entry = RT.Model(dict(entry, prep=own)).score(X)[0]
    assert np.array_equal(s_entry, RT.Model(d).score(X)[0])


def test_a_pooled_entry_is_a_plain_model_file():
    sources, entry, info = pooled()
    check_entry_model(json.loads(json.dumps(entry)))
    assert len(info["sources"]) == 3 and entry["training_report"]["sources"] == 3
    each = [B.Prep.fit(B.prepare(t, X)[1][B.fit_rows(B.prepare(t, X)[0])], list(F.FEATURE_NAMES),
                       running_only=False).columns for _, _, t, X in sources]
    assert info["columns"] == [c for c in each[0] if all(c in e for e in each)]  # kept by every source
    assert "imu_temp_c" not in info["columns"]  # the server rack has no IMU temperature
    held = np.concatenate([s["heldout_scores"] for s in info["sources"]])
    assert info["heldout_score_median"] == float(np.median(held))
    smoothed = np.concatenate([TR._causal_median(s["heldout_scores"], 5) for s in info["sources"]])
    assert entry["threshold"] == TR.gamma_threshold(smoothed, 7 * 86400) >= smoothed.max()
    for s in info["sources"]:  # the last 15 percent of every source is held out
        assert abs(s["rows_heldout"] / (s["rows_fit"] + s["rows_heldout"]) - 0.15) < 0.01


def test_widen_factors():
    sources, entry, _ = pooled()
    w = B.widen_factors(entry, sources)
    assert w.shape == (len(entry["prep"]["columns"]),) and (w >= 1).all() and (w <= 5).all()
    assert entry["calibration"]["widen"] == [float(v) for v in w]
    # A reading that drifts slowly is far wider over an hour than within 5 minutes; a steady one is not.
    rng = np.random.default_rng(0)
    n = 3600
    X = np.full((n, F.N_FEATURES), np.nan)
    X[:, F.INDEX["vib_rms_x"]] = rng.normal(0, 1, n)
    X[:, F.INDEX["vib_rms_y"]] = rng.normal(0, 1, n) + np.linspace(0, 100, n)
    X[:, F.INDEX["vib_rms_z"]] = rng.normal(0, 1, n) + np.linspace(0, 6, n)
    small = {"prep": {"columns": ["vib_rms_x", "vib_rms_y", "vib_rms_z"]}}
    w = B.widen_factors(small, [("s", "simulated", np.arange(n, dtype=float), X)])
    assert abs(w[0] - 1) < 0.1 and w[1] == 5.0 and 1.3 < w[2] < 4, w
    short = [("s", "simulated", np.arange(200.0), X[:200])]  # shorter than one window: nothing to compare
    assert (B.widen_factors(small, short) == 1).all()


def test_held_readings_stay_in_the_entry():
    t, X = short_rows(0)
    entry, info = B.fit_entry([("one", "simulated", t, X)])
    held = [c for c in info["columns"] if B.is_held(c)]
    assert "imu_temp_c" in held and any(c.startswith("time_") for c in held)
    assert not B.is_held("vib_rms_x")


def test_bad_sources_are_refused():
    t, X = short_rows(0)
    for bad, said in (([("a", "public", t, X)], "kind"), ([("a", "simulated", t[:200], X[:200])], "too little"),
                      ([], "at least one")):
        try:
            B.fit_entry(bad)
        except ValueError as e:
            assert said in str(e), e
        else:
            raise AssertionError(f"accepted: {said}")


def test_the_committed_entries():
    manifests = load_catalog(B.ENTRIES_DIR)
    assert sorted(m["entry"] for m in manifests) == ["conveyor", "generic_rotating"]
    for m in manifests:
        assert list(m) == MANIFEST_KEYS, list(m)
        manifest, d = load_entry(B.ENTRIES_DIR, m["entry"])  # the sha1 and the feature list
        assert manifest == m == load_entry(B.ENTRIES_DIR, m["entry"], m["version"])[0]
        check_entry_model(d)
        assert (d["library_entry"], d["entry_version"]) == (m["entry"], m["version"])
        assert m["manifest"] == MANIFEST and m["use"] == "watch" and m["pipeline"] == d["pipeline"]
        assert m["status"] == "unverified" and m["features_version"] == RT._version()
        assert set(m["sensors"]) == {"required", "optional"} and set(m["profile"]) == {"machine_type", "drive", "speed"}
        assert m["fault_types"] == [ft["name"] for ft in d["fault_types"]]
        assert m["calibration"] == {"unit": "seconds", "min": 300}
        assert m["fit_check"] == {"entry_heldout_score_median": m["fit_check"]["entry_heldout_score_median"],
                                  "entry_threshold": d["threshold"], "rule": "open"}
        assert 0 < m["fit_check"]["entry_heldout_score_median"] < d["threshold"]
        assert m["scorecard"] == {"measured_on_device": False, "false_alarms_per_week": None, "faults": []}
        assert all(s["kind"] == "simulated" and s["description"] and s["running_seconds"] > 3000
                   for s in m["trained_on"])
        assert any("fault_classifier" in n for n in m["notes"])
    by = {m["entry"]: m for m in manifests}
    assert len(by["conveyor"]["trained_on"]) >= 4
    assert len({s["description"] for s in by["conveyor"]["trained_on"]}) == len(by["conveyor"]["trained_on"])
    assert any("server rack" in s["description"] for s in by["generic_rotating"]["trained_on"])
    assert any("caught none of five faults" in n for n in by["generic_rotating"]["notes"])


def test_the_catalog_refuses_a_tampered_model():
    with tempfile.TemporaryDirectory() as tmp:
        shutil.copytree(B.ENTRIES_DIR, Path(tmp) / "entries")
        lib = Path(tmp) / "entries"
        m = load_catalog(lib)[0]
        folder = lib / m["entry"] / m["version"]
        good = (folder / "model.json").read_text()
        d = json.loads(good)
        d["threshold"] = d["threshold"] * 2
        (folder / "model.json").write_text(json.dumps(d, separators=(",", ":")))
        for check in (lambda: load_entry(lib, m["entry"]), ):
            try:
                check()
            except ValueError as e:
                assert "sha1" in str(e)
            else:
                raise AssertionError("a changed model file was loaded")
        (folder / "model.json").write_text(good)
        load_entry(lib, m["entry"])
        # built on another feature list: refused even with a matching sha1
        d = json.loads(good)
        d["features_version"] = "0" * 12
        raw = json.dumps(d, separators=(",", ":")).encode()
        (folder / "model.json").write_bytes(raw)
        m2 = dict(m, model_sha1=B.hashlib.sha1(raw).hexdigest())
        (folder / "manifest.json").write_text(json.dumps(m2))
        for manifest in (m2, dict(m2, features_version="0" * 12)):
            (folder / "manifest.json").write_text(json.dumps(manifest))
            try:
                load_entry(lib, m["entry"])
            except ValueError as e:
                assert "features" in str(e)
            else:
                raise AssertionError("a model on another feature list was loaded")
        try:
            load_entry(lib, "no_such_entry")
        except FileNotFoundError:
            pass
        else:
            raise AssertionError("a missing entry was loaded")


def test_a_device_recording_is_a_source():
    """The CLI's --recording, with a learn-mode folder written by the device's own recorder from a
    simulated machine (no real recording exists yet)."""
    from device.intake.extract import feature_stream
    from device.intake.recorder import Recorder
    from effente.history import History
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp) / "session-01"
        src = SIM.packet_source(SIM.ConveyorDesign("stand-in", seed=31), SHORT_H)
        hist = History()
        rec = Recorder(folder, lambda: src.info, src.sensors, label="normal", hop_s=1.0, history=hist)
        for t0, x in feature_stream(src, hop_s=1.0, tap=rec.on_packet, history=hist):
            rec.on_window(t0, x)
        rec.close()
        lib = Path(tmp) / "entries"
        assert B.main(["conveyor", "--hours", str(SHORT_H), "--out", str(lib), "--recording", str(folder),
                       "--status", "demo"]) == 0
        manifest, d = load_entry(lib, "conveyor")
        check_entry_model(d)
        assert manifest["status"] == "demo"
        kinds = [s["kind"] for s in manifest["trained_on"]]
        assert kinds == ["simulated"] * len(SIM.CONVEYORS) + ["device"]
        last = manifest["trained_on"][-1]
        assert last["name"] == "session-01" and last["running_seconds"] >= 300
        try:
            B.build(B.SPECS["conveyor"], lib, SHORT_H, status="verified")
        except ValueError as e:
            assert "scorecard" in str(e)
        else:
            raise AssertionError("an entry was labelled verified with no scorecard")


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
