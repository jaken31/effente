import json

import pytest

from device.agent.data import load_feature_table, load_log, summarize_readings
from device.agent.harness import AgentUnavailable, run_agent
from device.agent.report import build_report_schema, check_report

FEATURES = ["once_per_turn", "rattle"]


def record(machine_id="fan-1", at="2026-11-14T16:00:00Z", diagnosis="imbalance", **features):
    return {
        "device_id": "effente-01",
        "machine_id": machine_id,
        "at": at,
        "diagnosis": diagnosis,
        "unknown_score": 0.1,
        "features": features,
    }


def good_report(cited=("once_per_turn",)):
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
        record(at="2026-11-14T16:00:01Z", once_per_turn=0.8, rattle=0.05),
        record(at="2026-11-14T16:00:00Z", once_per_turn=0.7, rattle=0.02),
        record(machine_id="fan-2", diagnosis="normal", once_per_turn=0.0, rattle=0.0),
    ]
    log.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    model = tmp_path / "model.json"
    model.write_text(
        json.dumps(
            {
                "features": [
                    {"name": "once_per_turn", "sentence": "strong vibration once per turn"},
                    {"name": "rattle", "sentence": "rattling pattern"},
                ]
            }
        )
    )
    return log, model, tmp_path / "agent.jsonl"


def test_schema_limits_cited_features_to_the_table():
    schema = build_report_schema(["rattle", "once_per_turn"])
    assert schema["properties"]["cited_features"]["items"]["enum"] == ["once_per_turn", "rattle"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])


def test_schema_needs_at_least_one_verified_feature():
    with pytest.raises(ValueError):
        build_report_schema([])


def test_check_passes_a_report_whose_feature_was_active():
    assert check_report(good_report(), [record(once_per_turn=0.8)], FEATURES) == []


def test_check_rejects_a_feature_that_was_not_active():
    problems = check_report(good_report(["rattle"]), [record(once_per_turn=0.8, rattle=0.05)], FEATURES)
    assert problems == ["cites feature that was not active in the readings: rattle"]


def test_check_rejects_an_unverified_feature():
    problems = check_report(good_report(["bearing_wear"]), [record(bearing_wear=0.9)], FEATURES)
    assert problems == ["cites unverified feature: bearing_wear"]


def test_load_log_keeps_one_machine_in_time_order(paths):
    log, _, _ = paths
    records = load_log(log, "fan-1")
    assert [r["at"] for r in records] == ["2026-11-14T16:00:00Z", "2026-11-14T16:00:01Z"]


def test_load_feature_table(paths):
    _, model, _ = paths
    assert load_feature_table(model)["rattle"] == "rattling pattern"


def test_summary_counts_diagnoses_and_feature_activity():
    summary = summarize_readings([record(once_per_turn=0.6), record(once_per_turn=0.8)])
    assert summary["windows"] == 2
    assert summary["diagnoses"] == {"imbalance": 2}
    assert summary["feature_activity"]["once_per_turn"] == {"mean": 0.7, "peak": 0.8}


def test_run_agent_returns_a_checked_report_and_logs_it(paths):
    log, model, agent_log = paths
    result = run_agent("fan-1", log, model, agent_log, call=lambda *_: (good_report(), {"input_tokens": 1}))
    assert result.ok and result.report["priority"] == "medium"
    entry = json.loads(agent_log.read_text())
    assert entry["machine_id"] == "fan-1" and entry["ok"] is True


def test_run_agent_discards_a_report_that_fails_checks(paths):
    log, model, agent_log = paths
    result = run_agent("fan-1", log, model, agent_log, call=lambda *_: (good_report(["rattle"]), {}))
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
