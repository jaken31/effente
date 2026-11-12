import json
import os
import random

import pytest

import sim_device as sim_main
import sim_model as m

SAMPLE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sample_replay.jsonl")


def test_sample_file_covers_all_four_cases():
    records = sim_main.load_replay(SAMPLE)
    assert {r["diagnosis"] for r in records} == set(m.CASES)
    assert all(r["simulated"] is True for r in records)


def test_replay_rewrites_time_and_reapplies_mask():
    src = m.make_record("imbalance", random.Random(1), [], at="2020-01-01T00:00:00Z")
    out = m.replay_record(src, [], "dev", "mc")
    assert out["at"] != src["at"] and out["at"].endswith("Z")
    assert out["diagnosis"] == "imbalance" and out["simulated"] is True
    masked = m.replay_record(src, ["once_per_turn"], "dev", "mc")
    assert masked["diagnosis"] == "normal"
    assert masked["mask"] == ["once_per_turn"]
    assert masked["features"]["once_per_turn"] == 0
    assert masked["spectrum"] == src["spectrum"]


def test_replay_keeps_machine_and_uses_own_device_and_model():
    src = dict(m.make_record("normal", random.Random(1), []), device_id="other", model_version="x-9", machine_id="fan-2")
    out = m.replay_record(src, [], "dev", "mc")
    assert (out["device_id"], out["model_version"], out["machine_id"]) == ("dev", "sim-1", "fan-2")


@pytest.mark.parametrize("flag, expected", [(None, True), (True, True), (False, False)])
def test_replay_simulated_flag(flag, expected):
    src = m.make_record("normal", random.Random(1), [])
    src.pop("simulated")
    if flag is not None:
        src["simulated"] = flag
    assert m.replay_record(src, [], "d", "mc")["simulated"] is expected


def test_replay_without_features_is_passed_through_marked_simulated():
    src = {"machine_id": "fan-3", "at": "2020-01-01T00:00:00Z", "diagnosis": "imbalance", "spectrum": [0.1] * 64}
    out = m.replay_record(src, ["once_per_turn"], "dev", "mc")
    assert out["diagnosis"] == "imbalance"  # nothing to re-diagnose from
    assert out["simulated"] is True and out["at"] != src["at"]


def test_load_replay_rejects_bad_lines(tmp_path):
    p = tmp_path / "bad.jsonl"
    p.write_text('{"a": 1}\nnot json\n')
    with pytest.raises(SystemExit):
        sim_main.load_replay(str(p))
    p.write_text("\n\n")
    with pytest.raises(SystemExit):
        sim_main.load_replay(str(p))


def test_sample_file_is_what_the_generator_makes():
    # The sample was made with seed 7, two windows per case, no mask.
    rng = random.Random(7)
    expected = [m.make_record(c, rng, [], at="x") for c in m.CASES for _ in range(2)]
    got = [json.loads(line) for line in open(SAMPLE)]
    for e, g in zip(expected, got):
        assert {**e, "at": ""} == {**g, "at": ""}
