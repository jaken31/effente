"""Simulated heat field (contract 3.3): shape, image encoding, and agreement
with the th_* definitions in effente/features.py."""

import base64
import os
import random
import sys

import pytest

import sim_heat
import sim_model as model

AT = "2026-11-14T16:02:11.000Z"


def test_camera_heat_has_every_contract_field():
    h = sim_heat.HeatSim("camera", seed=1).heat(AT)
    assert h["source"] == "camera"
    assert set(h) == {"source", "imu_temp_c", "max_c", "hot_row", "hot_col", "background_c",
                      "cells", "spots", "frozen", "scene_match", "frames"}
    assert len(h["cells"]) == 48 and len(h["spots"]) == 48
    assert 0 <= h["hot_row"] < 24 and 0 <= h["hot_col"] < 32
    assert h["frozen"] is False
    assert len(h["frames"]) == 2
    assert [f["at"] for f in h["frames"]] == ["2026-11-14T16:02:10.500Z", AT]
    for f in h["frames"]:
        assert len(base64.b64decode(f["data"])) == 768


def test_imu_and_none_modes():
    assert set(sim_heat.HeatSim("imu", seed=1).heat(AT)) == {"source", "imu_temp_c"}
    assert sim_heat.HeatSim("none", seed=1).heat(AT) is None
    with pytest.raises(ValueError):
        sim_heat.HeatSim("laser")


def test_encoding_round_trip_within_half_a_step():
    rng = random.Random(3)
    img = sim_heat.scene(10.0, rng)
    f = sim_heat.encode_frame(img, AT)
    back = sim_heat.decode_frame(f)
    worst = max(abs(a - b) for ra, rb in zip(img, back) for a, b in zip(ra, rb))
    assert worst <= f["step_c"] / 2 + 1e-9
    raw = base64.b64decode(f["data"])
    assert min(raw) == 0 and max(raw) <= 255


def test_encoding_flat_image_and_wide_range():
    flat = [[30.0] * 32 for _ in range(24)]
    f = sim_heat.encode_frame(flat, AT)
    assert f["step_c"] == sim_heat.MIN_STEP_C
    assert sim_heat.decode_frame(f)[5][5] == pytest.approx(30.0)

    wide = [[20.0 + (r * 32 + c) * 0.1 for c in range(32)] for r in range(24)]  # 20 to 96.7 degC
    f = sim_heat.encode_frame(wide, AT)
    back = sim_heat.decode_frame(f)
    assert back[23][31] == pytest.approx(wide[23][31], abs=f["step_c"] / 2 + 1e-9)


def test_decode_rejects_wrong_size():
    with pytest.raises(ValueError):
        sim_heat.decode_frame({"min_c": 0, "step_c": 1, "data": base64.b64encode(b"x" * 10).decode()})


def test_frozen_when_frames_identical():
    img = sim_heat.scene(0.0, random.Random(1))
    assert sim_heat.summarise([img, img])["frozen"] is True
    assert sim_heat.summarise([img])["frozen"] is False


def test_heat_does_not_change_vibration_records_for_a_seed():
    # The replay sample is generated from random.Random(7); heat has its own generator.
    a = model.make_record("unbalance", random.Random(7), [])
    sim_heat.HeatSim("camera", seed=7).heat(AT)
    b = model.make_record("unbalance", random.Random(7), [])
    assert a["spectrum"] == b["spectrum"]


def test_summary_matches_effente_features():
    np = pytest.importorskip("numpy")
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    if root not in sys.path:
        sys.path.insert(0, root)
    features = pytest.importorskip("effente.features")

    rng = random.Random(5)
    imgs = [sim_heat.scene(t, rng) for t in (0.0, 0.5)]
    ours = sim_heat.summarise(imgs)
    vals = features._heat(np.asarray(imgs, float))
    names = features.FEATURE_NAMES[features.SLICES["heat"]]
    assert len(names) == len(vals) == 107
    th = dict(zip(names, vals))
    assert ours["max_c"] == pytest.approx(th["th_max_c"], abs=0.01)
    assert ours["background_c"] == pytest.approx(th["th_background_c"], abs=0.01)
    assert (ours["hot_row"], ours["hot_col"]) == (int(th["th_hot_row"]), int(th["th_hot_col"]))
    assert ours["frozen"] == bool(th["th_frozen"])
    cells = [th[f"th_cell_{r}{c}"] for r in range(6) for c in range(8)]
    spots = [th[f"th_spot_{r}{c}"] for r in range(6) for c in range(8)]
    assert ours["cells"] == pytest.approx(cells, abs=0.01)
    assert ours["spots"] == pytest.approx(spots, abs=0.01)
