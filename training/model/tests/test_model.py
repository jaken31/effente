"""Tests for the trained model, its device runtime and the model-slot loader.

Run with:  python3 -m training.model.tests.test_model
"""

import json
import sys
import tempfile
from pathlib import Path

import numpy as np

from effente import features as F
from effente import model_runtime as RT
from training.model.train import train

LOADER = Path(__file__).resolve().parents[1] / "effente_loader.py"
_CACHE = {}


def minutes(days, seed, start=0.0):
    """Minute rows of a machine whose load follows the day: vibration, sound and heat move with it."""
    rng = np.random.default_rng(seed)
    t = start + np.arange(int(days * 1440)) * 60.0
    n = len(t)
    load = 0.5 + 0.3 * np.sin(2 * np.pi * t / 86400)
    X = np.full((n, F.N_FEATURES), np.nan)

    def put(name, v):
        X[:, F.INDEX[name]] = v

    for a in "xyz":
        put(f"vib_rms_{a}", -1.6 + 0.3 * load + rng.normal(0, 0.02, n))
    put("vib_1x", -2.0 + 0.4 * load + rng.normal(0, 0.03, n))
    put("vib_2x", -2.5 + 0.2 * load + rng.normal(0, 0.03, n))
    put("vib_velocity_mm_s", 1.0 + 1.0 * load + rng.normal(0, 0.05, n))
    put("snd_level_db", 70 + 6 * load + rng.normal(0, 0.3, n))
    put("snd_tone_mod_db", 3 + rng.normal(0, 0.5, n))
    put("snd_kurt_hf", 0.2 + rng.normal(0, 0.05, n))
    put("env_room_c", 22 + rng.normal(0, 0.2, n))
    put("env_hot_above_room_c", 15 + 10 * load + rng.normal(0, 0.3, n))
    put("th_max_c", X[:, F.INDEX["env_room_c"]] + X[:, F.INDEX["env_hot_above_room_c"]])
    put("trend_heat_swing_10m", 0.5 + rng.normal(0, 0.05, n))
    put("trend_heat_swing_1h", 1.0 + rng.normal(0, 0.08, n))
    put("trend_running", 1.0)
    return t, X


def trained():
    if not _CACHE:
        t, X = minutes(3, seed=1)
        d, report = train(t, X, F.FEATURE_NAMES, source="synthetic test machine")
        _CACHE.update(d=d, report=report)
    return _CACHE["d"], _CACHE["report"]


def watch(d, t, X):
    w = RT.Watcher(RT.Model(d))
    out = []
    for ti, x in zip(t, X):
        out.append(w.predict_at(float(ti), x))
    return out


def test_whole_number_weights_give_the_same_scores():
    d, rep = trained()
    assert rep["int8_vs_float_score_correlation"] > 0.99 and rep["threshold"] > 0
    L = d["autoencoder"][0]
    assert all(isinstance(v, int) and -127 <= v <= 127 for row in L["w_q"] for v in row)


def test_a_new_normal_day_gives_almost_no_alarms():
    d, _ = trained()
    t, X = minutes(1, seed=2, start=3 * 86400)
    res = watch(d, t, X)
    abnormal = sum(r["diagnosis"] not in ("normal", "warming_up") for r in res)
    assert abnormal <= 15, abnormal  # at most about 1 % of the day's minutes
    assert res[0]["diagnosis"] == "warming_up" and res[-1]["diagnosis"] == "normal"


def test_once_per_turn_rise_is_named_unbalance():
    d, _ = trained()
    t, X = minutes(0.2, seed=3, start=3 * 86400)
    X[120:, F.INDEX["vib_1x"]] += 0.6  # four times the once-per-turn vibration
    X[120:, F.INDEX["vib_velocity_mm_s"]] += 1.0
    res = watch(d, t, X)
    late = [r["diagnosis"] for r in res[140:]]
    assert late.count("unbalance_like") > 0.8 * len(late), set(late)
    r = res[-1]
    assert r["unknown_score"] > 1 and "vib_1x" in r["features"] and r["confidence"] > 0.3


def test_swinging_temperature_is_named_excess_fluctuation():
    d, _ = trained()
    t, X = minutes(0.2, seed=4, start=3 * 86400)
    X[120:, F.INDEX["trend_heat_swing_10m"]] *= 5
    X[120:, F.INDEX["trend_heat_swing_1h"]] *= 4
    res = watch(d, t, X)
    late = [r["diagnosis"] for r in res[140:]]
    assert late.count("excess_temperature_fluctuation") > 0.8 * len(late), set(late)


def test_a_stopped_machine_is_not_judged():
    d, _ = trained()
    t, X = minutes(0.05, seed=5, start=3 * 86400)
    X[:, F.INDEX["trend_running"]] = 0.0
    X[:, F.INDEX["vib_rms_x"]] -= 2  # a stopped machine barely vibrates: not a fault
    res = watch(d, t, X)
    assert {r["diagnosis"] for r in res[1:]} == {"stopped"}


def test_the_device_model_slot_loads_it_and_refuses_other_features():
    from device.intake.live import ModelSlot
    d, _ = trained()
    with tempfile.TemporaryDirectory() as tmp:
        good = Path(tmp) / "model.json"
        good.write_text(json.dumps(d))
        slot = ModelSlot(LOADER, good)
        assert slot.status == "ready", slot.error
        t, X = minutes(0.01, seed=6, start=3 * 86400)
        r = slot.predict(X[0], t[0])
        assert r["diagnosis"] == "warming_up"
        bad = Path(tmp) / "old.json"
        bad.write_text(json.dumps(dict(d, features_version="0123456789ab")))
        slot = ModelSlot(LOADER, bad)
        assert slot.status == "error" and "0123456789ab" in slot.error


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
