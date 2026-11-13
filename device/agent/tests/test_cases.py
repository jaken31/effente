"""Run every synthetic test log through the agent with the stand-in and check the known answer."""

import json
from pathlib import Path

import pytest

from device.agent import harness
from device.agent.harness import run_agent
from device.agent.standin import MODEL_NAME, stand_in_call

LOGS = Path(__file__).parent / "logs"
FEATURES = ["vib_1x", "vib_harm_3_10"]


def cases():
    for expected_file in sorted(LOGS.glob("*.expected.json")):
        name = expected_file.name.removesuffix(".expected.json")
        expected = json.loads(expected_file.read_text())
        for run in expected["runs"]:
            yield pytest.param(name, expected["model_file"], run, id=f"{name}-{run['machine_id']}")


def no_search(query):
    raise AssertionError("the stand-in must not call search_parts")


@pytest.fixture(autouse=True)
def no_platform(monkeypatch):
    monkeypatch.delenv("EFFENTE_PLATFORM_URL", raising=False)


def run_case(name, model_file, machine_id, tmp_path, call=stand_in_call):
    return run_agent(
        machine_id,
        LOGS / f"{name}.jsonl",
        LOGS / model_file,
        tmp_path / "agent.jsonl",
        call=call,
        search=no_search,
    )


def test_there_are_about_ten_logs_and_every_record_is_marked_simulated():
    logs = sorted(LOGS.glob("*.jsonl"))
    assert len(logs) == 12
    for log in logs:
        for line in log.read_text().splitlines():
            record = json.loads(line)
            assert record["simulated"] is True
            for field in ("device_id", "machine_id", "model_version", "at", "diagnosis"):
                assert field in record, (log.name, field)


@pytest.mark.parametrize("name, model_file, expected", list(cases()))
def test_stand_in_gives_the_known_right_answer(name, model_file, expected, tmp_path):
    result = run_case(name, model_file, expected["machine_id"], tmp_path)

    assert result.ok is expected["ok"], result
    assert result.reason == expected["reason"]
    assert result.model_version == expected["model_version"]
    assert result.simulated is True
    if expected["note_contains"]:
        assert any(expected["note_contains"] in note for note in result.notes), result.notes

    if not expected["ok"]:
        assert result.report is None and result.tool_calls == []
        return

    readings = next(c for c in result.tool_calls if c["name"] == "get_readings")
    assert json.loads(readings["output"])["diagnoses"] == expected["diagnoses"]

    report = result.report
    cited = set(report["cited_features"])
    assert cited <= set(expected["may_cite"])
    assert set(expected["must_cite"]) <= cited
    assert report["priority"] in expected["priority"]
    assert report["summary"].startswith("Stand-in report:")
    assert report["part_reference"] == ""

    entry = json.loads((tmp_path / "agent.jsonl").read_text())
    assert entry["model"] == MODEL_NAME
    assert entry["model_version"] == expected["model_version"]
    assert entry["simulated"] is True


def citing(features):
    """A stand-in that writes the usual report but cites `features` instead."""

    def call(machine_id, records, table, schema, tools):
        report, usage = stand_in_call(machine_id, records, table, schema, tools)
        return {**report, "cited_features": list(features)}, usage

    return call


def wrong_citations():
    for param in cases():
        name, model_file, expected = param.values
        if not expected["ok"]:
            continue
        for feature in FEATURES + ["bearing_wear"]:
            if feature not in expected["may_cite"]:
                yield pytest.param(name, model_file, expected, feature, id=f"{name}-{expected['machine_id']}-{feature}")


@pytest.mark.parametrize("name, model_file, expected, feature", list(wrong_citations()))
def test_a_report_citing_a_feature_it_may_not_cite_is_rejected(name, model_file, expected, feature, tmp_path):
    result = run_case(name, model_file, expected["machine_id"], tmp_path, call=citing([feature]))
    assert not result.ok and result.report is None
    assert result.reason == "report failed checks"
    assert len(result.problems) == 1 and result.problems[0].endswith(f": {feature}")


def test_unrecognised_case_cites_nothing(tmp_path):
    # vib_harm_3_10 does move past 6 spreads here, but only in unrecognised_pattern windows.
    result = run_case("unknown", "model.json", "conveyor-07", tmp_path, call=citing(["vib_harm_3_10"]))
    assert result.problems == ["cites feature that moved only in windows with no fault type: vib_harm_3_10"]


def test_masked_feature_is_mentioned_but_not_cited(tmp_path):
    result = run_case("masked", "model.json", "conveyor-07", tmp_path)
    assert result.report["cited_features"] == []
    assert "with vib_1x switched off in some windows" in result.report["summary"]


def test_mixed_session_reports_every_finding_in_three_sentences(tmp_path):
    result = run_case("mixed_session", "model.json", "demo-fan-1", tmp_path)
    report = result.report
    summary = report["summary"]
    assert summary == (
        "Stand-in report: demo-fan-1, 45 windows: 25 looseness or rattle, 6 unbalance-like, "
        "5 unrecognised pattern, 9 normal. "
        "Fix looseness or rattle first, then unbalance-like. "
        "The cause of the 5 unrecognised pattern windows is not known: inspect the machine."
    )
    assert report["cited_features"] == ["vib_harm_3_10", "vib_1x"]
    assert report["priority"] == "high"
    action = report["recommended_action"]
    assert action.index("tighten the bolts") < action.index("rebalance") < action.index("Inspect the machine")


def test_two_versions_uses_only_the_newest_and_notes_the_model_file(tmp_path):
    result = run_case("two_versions", "model.json", "conveyor-07", tmp_path)
    assert result.model_version == "fake-0.2"
    assert any("model file is version fake-0.1" in note for note in result.notes)


def test_stand_in_records_its_tool_calls_in_order(tmp_path):
    result = run_case("imbalance", "model.json", "conveyor-07", tmp_path)
    assert [c["name"] for c in result.tool_calls] == ["get_feature_table", "get_readings"]


def test_old_four_argument_call_still_works_and_is_marked_simulated(tmp_path):
    report = {
        "summary": "Imbalance.",
        "priority": "medium",
        "cited_features": ["vib_1x"],
        "recommended_action": "Rebalance.",
        "part_reference": "",
    }
    result = run_case("imbalance", "model.json", "conveyor-07", tmp_path, call=lambda *_: (report, {}))
    assert result.ok and result.tool_calls == []
    assert result.simulated is True
    entry = json.loads((tmp_path / "agent.jsonl").read_text())
    assert entry["model"] == harness.INJECTED_MODEL


def test_simulated_is_false_only_for_the_real_call_on_real_readings(tmp_path, monkeypatch):
    log = tmp_path / "real.jsonl"
    rows = [json.loads(line) for line in (LOGS / "imbalance.jsonl").read_text().splitlines()]
    for row in rows:
        row.pop("simulated")  # missing means false
    log.write_text("".join(json.dumps(r) + "\n" for r in rows))

    def fake_claude(machine_id, records, features, schema, tools=None):
        return citing(["vib_1x"])(machine_id, records, features, schema, tools)

    # Stands in for the real function so run_agent treats it as the live call. No API is used.
    monkeypatch.setattr(harness, "call_claude", fake_claude)
    result = run_agent("conveyor-07", log, LOGS / "model.json", tmp_path / "a.jsonl", search=no_search)
    assert result.ok and result.simulated is False

    rows[0]["simulated"] = True
    log.write_text("".join(json.dumps(r) + "\n" for r in rows))
    result = run_agent("conveyor-07", log, LOGS / "model.json", tmp_path / "a.jsonl", search=no_search)
    assert result.simulated is True


def test_a_crashing_call_closes_the_run_and_still_raises(tmp_path):
    def broken(*_):
        raise KeyError("bug")

    with pytest.raises(KeyError):
        run_case("imbalance", "model.json", "conveyor-07", tmp_path, call=broken)
    entry = json.loads((tmp_path / "agent.jsonl").read_text())
    assert entry["ok"] is False and entry["reason"].startswith("agent error: KeyError")
