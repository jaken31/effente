import json

import pytest

from device.agent.data import load_feature_table, load_log, summarize_readings
from device.agent.harness import AgentUnavailable, run_agent
from device.agent.report import build_report_schema, check_report

FEATURES = ["vib_1x", "vib_harm_3_10"]


def record(machine_id="fan-1", at="2026-11-14T16:00:00Z", diagnosis="unbalance_like", **features):
    return {
        "device_id": "effente-01",
        "machine_id": machine_id,
        "at": at,
        "diagnosis": diagnosis,
        "unknown_score": 0.1,
        "features": features,
    }


def good_report(cited=("vib_1x",)):
    return {
        "summary": "Imbalance on the fan.",
        "priority": "medium",
        "cited_features": list(cited),
        "recommended_action": "Clean and rebalance the blades.",
        "part_reference": "",
    }


@pytest.fixture
def paths(tmp_path):
    log = tmp_path / "device.jsonl"
    records = [
        record(at="2026-11-14T16:00:01Z", vib_1x=9.6, vib_harm_3_10=0.6),
        record(at="2026-11-14T16:00:00Z", vib_1x=8.4, vib_harm_3_10=0.24),
        record(machine_id="fan-2", diagnosis="normal", vib_1x=0.0, vib_harm_3_10=0.0),
    ]
    log.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    model = tmp_path / "model.json"
    model.write_text(
        json.dumps(
            {
                "features": [
                    {"name": "vib_1x", "sentence": "strong vibration once per turn"},
                    {"name": "vib_harm_3_10", "sentence": "rattling pattern"},
                ]
            }
        )
    )
    return log, model, tmp_path / "agent.jsonl"


def test_schema_limits_cited_features_to_the_table():
    schema = build_report_schema(["vib_harm_3_10", "vib_1x"])
    assert schema["properties"]["cited_features"]["items"]["enum"] == ["vib_1x", "vib_harm_3_10"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])


def test_schema_needs_at_least_one_verified_feature():
    with pytest.raises(ValueError):
        build_report_schema([])


def test_check_passes_a_report_whose_feature_moved_either_way():
    assert check_report(good_report(), [record(vib_1x=9.6)], FEATURES) == []
    assert check_report(good_report(), [record(vib_1x=-6.0)], FEATURES) == []


def test_check_rejects_a_feature_that_did_not_move():
    problems = check_report(good_report(["vib_harm_3_10"]), [record(vib_1x=9.6, vib_harm_3_10=5.9)], FEATURES)
    assert problems == ["cites feature that did not move in the readings: vib_harm_3_10"]


@pytest.mark.parametrize("diagnosis", ["normal", "stopped", "warming_up", "unrecognised_pattern"])
def test_check_rejects_a_feature_that_moved_only_without_a_fault_type(diagnosis):
    problems = check_report(good_report(), [record(diagnosis=diagnosis, vib_1x=9.6)], FEATURES)
    assert problems == ["cites feature that moved only in windows with no fault type: vib_1x"]


def test_check_rejects_an_unverified_feature():
    problems = check_report(good_report(["bearing_wear"]), [record(bearing_wear=0.9)], FEATURES)
    assert problems == ["cites unverified feature: bearing_wear"]


def test_load_log_keeps_one_machine_in_time_order(paths):
    log, _, _ = paths
    records = load_log(log, "fan-1")
    assert [r["at"] for r in records] == ["2026-11-14T16:00:00Z", "2026-11-14T16:00:01Z"]


def test_load_feature_table(paths):
    _, model, _ = paths
    assert load_feature_table(model)["vib_harm_3_10"] == "rattling pattern"


def test_summary_counts_diagnoses_and_feature_activity():
    summary = summarize_readings([record(vib_1x=7.2), record(vib_1x=-9.6)])
    assert summary["windows"] == 2
    assert summary["diagnoses"] == {"unbalance_like": 2}
    assert summary["feature_activity"]["vib_1x"] == {"mean": -1.2, "peak": 9.6}
    assert summary["peak_by_diagnosis"] == {"unbalance_like": {"vib_1x": 9.6}}


def test_summary_takes_stopped_and_warming_up_records():
    # unknown_score is null while stopped or warming up (contract message 2).
    stopped = dict(record(diagnosis="stopped"), unknown_score=None, features={})
    warming = dict(record(diagnosis="warming_up"), unknown_score=None, features={})
    summary = summarize_readings([record(vib_1x=7.2), stopped, warming])
    assert summary["diagnoses"] == {"unbalance_like": 1, "stopped": 1, "warming_up": 1}
    assert summary["max_unknown_score"] == 0.1
    assert summarize_readings([stopped])["max_unknown_score"] is None


def test_run_agent_returns_a_checked_report_and_logs_it(paths):
    log, model, agent_log = paths
    result = run_agent("fan-1", log, model, agent_log, call=lambda *_: (good_report(), {"input_tokens": 1}))
    assert result.ok and result.report["priority"] == "medium"
    entry = json.loads(agent_log.read_text())
    assert entry["machine_id"] == "fan-1" and entry["ok"] is True


def test_run_agent_discards_a_report_that_fails_checks(paths):
    log, model, agent_log = paths
    result = run_agent("fan-1", log, model, agent_log, call=lambda *_: (good_report(["vib_harm_3_10"]), {}))
    assert not result.ok and result.report is None
    assert result.reason == "report failed checks"


def test_run_agent_reports_when_the_agent_is_unavailable(paths):
    log, model, agent_log = paths

    def offline(*_):
        raise AgentUnavailable("no connection")

    result = run_agent("fan-1", log, model, agent_log, call=offline)
    assert not result.ok and result.reason == "agent unavailable: no connection"


def test_run_agent_without_readings_does_not_call_the_agent(paths):
    log, model, agent_log = paths

    def must_not_run(*_):
        raise AssertionError("agent was called")

    result = run_agent("fan-9", log, model, agent_log, call=must_not_run)
    assert not result.ok and result.reason == "no readings for this machine"
