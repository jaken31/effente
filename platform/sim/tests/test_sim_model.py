import itertools
import random
from datetime import datetime

import pytest

import sim_model as m

EXPECTED = {
    "normal": ("normal", ""),
    "imbalance": ("imbalance", "once-per-turn vibration"),
    "loose_mount": ("loose_mount", "rattling pattern"),
    "unknown": ("unknown", ""),
}
ALL_MASKS = [list(c) for n in range(3) for c in itertools.combinations(m.FEATURE_NAMES, n)]
WINDOWS = 200  # windows checked per case


def assert_contract_record(r):
    for k in ("device_id", "machine_id", "model_version", "at", "diagnosis"):
        assert isinstance(r[k], str) and r[k]
    assert r["diagnosis"] in ("normal", "imbalance", "loose_mount", "unknown")
    assert 0 <= r["confidence"] <= 1
    assert r["unknown_score"] >= 0
    assert (r["diagnosis"] == "unknown") == (r["unknown_score"] > m.UNKNOWN_THRESHOLD)
    assert isinstance(r["reason"], str)
    if r["diagnosis"] in ("normal", "unknown"):
        assert r["reason"] == ""
    assert set(r["features"]) == set(m.FEATURE_NAMES)
    assert all(0 <= v <= 1 for v in r["features"].values())
    assert isinstance(r["mask"], list)
    for name in r["mask"]:
        if name in r["features"]:
            assert r["features"][name] == 0
    assert len(r["spectrum"]) == 64
    assert all(isinstance(x, float) and 0 <= x <= 1 for x in r["spectrum"])
    assert r["simulated"] is True
    datetime.fromisoformat(r["at"].replace("Z", "+00:00"))
    assert r["at"].endswith("Z")


@pytest.mark.parametrize("case", m.CASES)
def test_case_gives_expected_diagnosis_and_valid_record(case):
    rng = random.Random(1)
    for _ in range(WINDOWS):
        r = m.make_record(case, rng, [])
        assert (r["diagnosis"], r["reason"]) == EXPECTED[case]
        assert r["mask"] == []
        assert r["model_version"] == "sim-1"
        assert r["device_id"] == "effente-sim-01" and r["machine_id"] == "demo-fan-1"
        assert_contract_record(r)


def test_spectrum_peaks_where_described():
    rng = random.Random(2)
    turn_bin = round(m.TURN_HZ / m.BIN_HZ)
    imb = m.make_spectrum("imbalance", rng)
    assert imb.index(max(imb)) == turn_bin
    unk = m.make_spectrum("unknown", rng)
    assert abs(m.bin_hz(unk.index(max(unk))) - m.UNKNOWN_HZ) <= m.BIN_HZ
    normal = m.make_spectrum("normal", rng)
    assert max(normal) < 0.2
    loose = m.make_spectrum("loose_mount", rng)
    assert sum(loose) / 64 > 2 * sum(normal) / 64  # raised broadband


def test_loose_mount_harmonics_vary_between_windows():
    rng = random.Random(3)
    a, b = m.make_spectrum("loose_mount", rng), m.make_spectrum("loose_mount", rng)
    diff = max(abs(x - y) for x, y in zip(a, b))
    assert diff > 0.1


def _expected_under_mask(case, mask):
    if case == "imbalance" and "once_per_turn" in mask:
        return "normal", ""
    if case == "loose_mount" and "rattle" in mask:
        return "normal", ""
    return EXPECTED[case]


@pytest.mark.parametrize("case", m.CASES)
@pytest.mark.parametrize("mask", ALL_MASKS, ids=lambda x: "+".join(x) or "none")
def test_every_mask_combination(case, mask):
    rng = random.Random(4)
    for _ in range(WINDOWS):
        spectrum = m.make_spectrum(case, rng)
        raw = m.feature_activity(spectrum)
        u = m.unknown_score(spectrum)
        base = m.diagnose_window(spectrum, raw, u, [], "d", "mc")
        r = m.diagnose_window(spectrum, raw, u, mask, "d", "mc")
        assert_contract_record(r)
        assert r["mask"] == mask
        assert (r["diagnosis"], r["reason"]) == _expected_under_mask(case, mask)
        # The spectrum does not change with the mask; only the model's use of it.
        assert r["spectrum"] == base["spectrum"]
        assert r["unknown_score"] == base["unknown_score"]
        for name in m.FEATURE_NAMES:
            assert r["features"][name] == (0 if name in mask else base["features"][name])
        # Switching off a feature the diagnosis does not use changes nothing.
        if case in ("imbalance", "loose_mount") and _expected_under_mask(case, mask) == EXPECTED[case]:
            assert {**r, "mask": [], "at": ""} == {**base, "at": ""}


def test_imbalance_with_once_per_turn_off_reads_normal():
    r = m.make_record("imbalance", random.Random(5), ["once_per_turn"])
    assert r["diagnosis"] == "normal"
    assert r["reason"] == ""
    assert r["features"]["once_per_turn"] == 0
    assert r["mask"] == ["once_per_turn"]


def test_mask_names_not_in_table_are_echoed_but_ignored():
    rng_a, rng_b = random.Random(6), random.Random(6)
    a = m.make_record("imbalance", rng_a, [])
    b = m.make_record("imbalance", rng_b, ["no_such_feature"])
    assert b["mask"] == ["no_such_feature"]
    assert (b["diagnosis"], b["features"]) == (a["diagnosis"], a["features"])


def test_seed_is_repeatable_and_seeds_differ():
    def run(seed):
        rng = random.Random(seed)
        return [m.make_record(c, rng, [], at="t") for c in m.CASES for _ in range(5)]

    assert run(42) == run(42)
    assert run(42) != run(43)


def test_announcement_and_model_file():
    a = m.model_announcement("dev-x")
    assert a == {
        "device_id": "dev-x",
        "model_version": "sim-1",
        "class_names": ["normal", "imbalance", "loose_mount"],
        "unknown_threshold": 0.5,
        "spectrum_hz_max": 500.0,
        "features": [
            {"name": "once_per_turn", "sentence": "once-per-turn vibration"},
            {"name": "rattle", "sentence": "rattling pattern"},
        ],
        "simulated": True,
    }
    f = m.model_file()
    assert f["simulated"] is True and f["model_version"] == "sim-1"
    assert f["features"][0] == {"name": "once_per_turn", "unit_indices": [], "sentence": "once-per-turn vibration"}
