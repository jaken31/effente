"""Unit tests of effente/band_model.py (NumPy only).

    python -m pytest training/record/sscc_eval/tests -q        (from the repository root)
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from effente import band_model as BM  # noqa: E402

SAMPLE = Path(__file__).resolve().parent / "data" / "sscc_light_vel100_clean_sample.npz"


def _signals(rng, seconds, tone_hz=None, tone_amp=0.0, vib_amp=0.0005):
    t1 = np.arange(1000) / 1000.0
    t16 = np.arange(16000) / 16000.0
    for _ in range(seconds):
        acc = rng.normal(0, 0.0044, (1000, 3)) + vib_amp * np.sin(2 * np.pi * 31 * t1)[:, None]
        acc[:, 2] += 1.0
        snd = rng.normal(0, 200, 16000) + 400 * np.sin(2 * np.pi * 700 * t16) + 150 * np.sin(2 * np.pi * 2300 * t16)
        if tone_hz:
            snd = snd + tone_amp * np.sin(2 * np.pi * tone_hz * t16)
        yield acc, snd


def _rows(rng, seconds, **kw):
    return np.array([BM.window_row(a, s) for a, s in _signals(rng, seconds, **kw)])


def test_window_row_layout_and_band_of_a_tone():
    rng = np.random.default_rng(1)
    t16 = np.arange(16000) / 16000.0
    snd = 1000 * np.sin(2 * np.pi * 1234 * t16) + rng.normal(0, 1, 16000)
    acc = rng.normal(0, 0.001, (1000, 3))
    r = BM.window_row(acc, snd)
    assert r.shape == (BM.N_VIB + BM.N_SND,) == (231,)
    bands = r[BM.N_VIB:BM.N_VIB + len(BM.SND_EDGES) - 1]
    lo = BM.SND_EDGES[np.argmax(bands)]
    assert lo <= 1234 < lo + 50
    r64 = BM.window_row(acc, snd, snd_edges=BM.SND_EDGES_LOG64)
    assert r64.shape == (BM.N_VIB + 67,)


def test_learn_then_normal_is_quiet_and_a_new_tone_alarms():
    rng = np.random.default_rng(2)
    m = BM.BandModel().learn(_rows(rng, 400))
    normal = _rows(rng, 60)
    alarms = [m.judge(r)["alarm"] for r in normal]
    assert sum(alarms) <= 2
    m.reset()
    for r in _rows(rng, 3):
        m.judge(r)
    fault = _rows(rng, 4, tone_hz=4321, tone_amp=60)
    out = [m.judge(r) for r in fault]
    assert out[-1]["alarm"] and out[-1]["level"] > 1


def test_vibration_group_dropped_at_the_sensor_floor_and_kept_above_it():
    rng = np.random.default_rng(3)
    quiet = BM.BandModel().learn(_rows(rng, 200, vib_amp=0.0))
    assert not quiet.active[:BM.N_VIB].any() and quiet.active[BM.N_VIB:].all()
    assert len(quiet.cores[0]["groups"]) == 1
    loud_rows = _rows(rng, 200)
    loud_rows[:, :BM.N_VIB] += np.tile(np.r_[np.full(20, 2.0), 1.0, 0.0, 0.0], 3)  # 20 dB over the floor everywhere
    m = BM.BandModel().learn(loud_rows)
    assert m.active[:BM.N_VIB].all() and len(m.cores[0]["groups"]) == 2


def test_save_load_round_trip(tmp_path):
    rng = np.random.default_rng(4)
    R = _rows(rng, 250)
    for preset in ("lin50", "log64"):
        rows = R if preset == "lin50" else np.array(
            [BM.window_row(a, s, snd_edges=BM.SND_EDGES_LOG64) for a, s in _signals(rng, 250)])
        m = BM.make_model(preset).learn(rows)
        p = tmp_path / f"{preset}.json"
        m.save(p)
        m2 = BM.BandModel.load(p)
        assert m2.line == m.line
        np.testing.assert_allclose(m2.scores(rows[:20]), m.scores(rows[:20]), rtol=1e-12)
        assert [m2.judge(r)["alarm"] for r in rows[:10]] == [m.judge(r)["alarm"] for r in rows[:10]]


def test_modes_take_the_closest_operating_point():
    rng = np.random.default_rng(5)
    a = _rows(rng, 200)
    b = _rows(rng, 200, tone_hz=3000, tone_amp=300)
    rec = np.r_[np.zeros(len(a)), np.ones(len(b))]
    modes = BM.BandModes().learn(np.vstack([a, b]), rec)
    single_a = BM.BandModel().learn(a)
    test_b = _rows(rng, 20, tone_hz=3000, tone_amp=300)
    assert np.median(modes.scores(test_b)) < 1.0 < np.median(single_a.scores(test_b) / single_a.line)


@pytest.mark.skipif(not SAMPLE.exists(), reason="SSCC sample not present")
def test_reproduces_the_benchmark_alarms_on_a_saved_sscc_sample():
    """light_vel100_clean (selection set): the shipped code must give the benchmark's line, scores and alarms."""
    d = np.load(SAMPLE)
    m = BM.BandModel().learn(d["learn"].astype(float))
    assert m.line == pytest.approx(float(d["line"]), rel=1e-9)
    hold = d["hold"].astype(float)
    np.testing.assert_allclose(m.scores(hold), d["hold_score"], rtol=1e-9)
    pre = float(np.median(d["hold_score"]))
    m.reset()
    m.recent = [pre, pre]
    per_s = [m.judge(r)["alarm"] for r in hold]
    assert list(np.array(per_s).reshape(-1, 5).any(1)) == list(d["hold_alarm"])
    got = []
    for clip, ref in zip(d["fault"].astype(float), d["fault_score"]):
        np.testing.assert_allclose(m.scores(clip), ref, rtol=1e-9)
        m.reset()
        m.recent = [pre, pre]
        got.append(any(m.judge(r)["alarm"] for r in clip))
    assert got == list(d["fault_alarm"])
    assert 0 < sum(got) < len(got)  # the sample holds caught and missed fault clips
