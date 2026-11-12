import json
import os
import random
from datetime import datetime, timedelta, timezone

import pytest

import sim_device as sim_main
import sim_model as m

SAMPLE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sample_replay.jsonl")


def parse(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def test_sample_file_covers_its_scenarios():
    records = sim_main.load_replay(SAMPLE)
    seen = [(r["diagnosis"], [p["name"] for p in r["problems"]]) for r in records]
    assert ("normal", []) in seen
    assert ("normal", ["too_hot"]) in seen  # a problem while the model says normal
    assert ("unbalance_like", ["too_hot"]) in seen
    assert ("stopped", ["too_hot"]) in seen  # a problem next to stopped
    assert ("unrecognised_pattern", []) in seen
    assert all(r["simulated"] is True for r in records)


def test_sample_file_is_what_the_generator_makes():
    got = [json.loads(line) for line in open(SAMPLE)]
    assert got == m.sample_records()


def test_replay_moves_every_time_by_the_same_amount():
    src = m.sample_records()
    rp = m.Replayer("dev", "mc")
    before = datetime.now(timezone.utc)
    out = [rp.record(r) for r in src]
    assert before - timedelta(seconds=1) <= parse(out[0]["at"]) <= datetime.now(timezone.utc)
    for a, b in zip(src, out):
        shift = parse(b["at"]) - parse(a["at"])
        assert abs((shift - rp.shift).total_seconds()) < 0.002
        for pa, pb in zip(a["problems"], b["problems"]):
            assert abs(((parse(pb["since"]) - parse(pa["since"])) - rp.shift).total_seconds()) < 0.002
        assert b["diagnosis"] == a["diagnosis"] and b["wrong"] == a["wrong"]


def test_replay_keeps_machine_and_uses_own_device_and_model():
    src = dict(m.make_record("normal", random.Random(1)), device_id="other", model_version="x-9", machine_id="fan-2")
    out = m.Replayer("dev", "mc").record(src)
    assert (out["device_id"], out["model_version"], out["machine_id"]) == ("dev", "sim-3", "fan-2")


@pytest.mark.parametrize("flag, expected", [(None, True), (True, True), (False, False)])
def test_replay_simulated_flag(flag, expected):
    src = m.sample_records()[0]
    src.pop("simulated")
    if flag is not None:
        src["simulated"] = flag
    assert m.Replayer("d", "mc").record(src)["simulated"] is expected


def test_replay_fills_in_problems_and_wrong_for_older_records():
    src = {"machine_id": "fan-3", "at": "2020-01-01T00:00:00Z", "diagnosis": "unbalance_like"}
    out = m.Replayer("dev", "mc").record(src)
    assert out["problems"] == [] and out["wrong"] is True
    assert out["simulated"] is True and out["at"] != src["at"]


def test_load_replay_rejects_bad_lines(tmp_path):
    p = tmp_path / "bad.jsonl"
    p.write_text('{"a": 1}\nnot json\n')
    with pytest.raises(SystemExit):
        sim_main.load_replay(str(p))
    p.write_text("\n\n")
    with pytest.raises(SystemExit):
        sim_main.load_replay(str(p))
