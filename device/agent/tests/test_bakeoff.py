"""The bake-off against a fake model server in this process. No real model, no network."""

import json

import pytest

from device.agent import bakeoff
from device.agent.bakeoff import AFTER_RETRY, FAILED, FIRST_TRY, NOT_ASKED

from .test_local_call import FAILS_CHECKS, GOOD, PROBLEM, FakeModelServer, closed_port_url, record

# Passes the checks on any log: cites nothing, names no reading, has no number.
CITES_NOTHING = json.dumps({
    "summary": "Nothing to report.",
    "priority": "none",
    "cited_features": [],
    "recommended_action": "No action.",
    "part_reference": "",
})


@pytest.fixture(autouse=True)
def no_environment(monkeypatch):
    monkeypatch.delenv("EFFENTE_PLATFORM_URL", raising=False)
    monkeypatch.delenv("EFFENTE_LLM_URL", raising=False)


@pytest.fixture
def logs(tmp_path):
    """One log for fan-1 that should cite vib_1x, and one expected run for a machine with no readings."""
    folder = tmp_path / "logs"
    folder.mkdir()
    records = [
        record("2026-11-14T16:00:00Z", vib_1x=8.4, vib_harm_3_10=0.24),
        record("2026-11-14T16:00:01Z", vib_1x=9.6, vib_harm_3_10=0.6),
    ]
    (folder / "fan.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))
    (folder / "model.json").write_text(json.dumps({"features": [
        {"name": "vib_1x", "sentence": "strong vibration once per turn"},
        {"name": "vib_harm_3_10", "sentence": "rattling pattern"},
    ]}))
    (folder / "fan.expected.json").write_text(json.dumps({"model_file": "model.json", "runs": [
        {"machine_id": "fan-1", "may_cite": ["vib_1x"], "must_cite": ["vib_1x"],
         "priority": ["medium", "high"], "ok": True, "reason": ""},
        {"machine_id": "fan-9", "may_cite": [], "must_cite": [], "priority": [],
         "ok": False, "reason": "no readings for this machine"},
    ]}))
    (folder / "no_expected_result.jsonl").write_text(json.dumps(records[0]) + "\n")
    return folder


def fan_runs(results):
    (entry,) = [e for e in results["logs"] if e["machine_id"] == "fan-1"]
    return entry["runs"]


def test_first_try_after_retry_and_failed_are_told_apart_with_reasons_and_every_report(logs):
    answers = [GOOD, FAILS_CHECKS, GOOD, FAILS_CHECKS, FAILS_CHECKS]
    with FakeModelServer(answers) as server:
        results = bakeoff.run_bakeoff(server.url, repeat=3, logs=logs, timeout=5.0)
    first, retried, failed = fan_runs(results)

    assert (first["outcome"], first["reasons"]) == (FIRST_TRY, [])
    assert (retried["outcome"], retried["reasons"]) == (AFTER_RETRY, [PROBLEM])
    assert (failed["outcome"], failed["reasons"]) == (FAILED, [PROBLEM])

    # Every report the model wrote is kept whole, failed ones too.
    assert [a["report"] for a in first["reports"]] == [json.loads(GOOD)]
    assert [a["report"] for a in retried["reports"]] == [json.loads(FAILS_CHECKS), json.loads(GOOD)]
    assert [a["problems"] for a in retried["reports"]] == [[PROBLEM], []]
    assert [a["report"] for a in failed["reports"]] == [json.loads(FAILS_CHECKS)] * 2
    assert first["reports"][0]["report"]["summary"] == "Imbalance on the fan."
    assert first["reports"][0]["report"]["recommended_action"] == "Clean and rebalance the blades."
    assert len(server.chats) == 5 and server.answers == []


def test_only_logs_with_an_expected_result_run_and_a_machine_with_no_readings_is_not_asked(logs):
    with FakeModelServer([GOOD]) as server:
        results = bakeoff.run_bakeoff(server.url, repeat=1, logs=logs, timeout=5.0)
    assert [(e["log"], e["machine_id"]) for e in results["logs"]] == [("fan", "fan-1"), ("fan", "fan-9")]
    (not_asked,) = results["logs"][1]["runs"]
    assert not_asked["outcome"] == NOT_ASKED and not_asked["reports"] == []
    assert not_asked["reasons"] == ["no readings for this machine"]
    assert len(server.chats) == 1


def test_an_answer_that_is_not_a_report_fails_that_run_and_the_next_run_still_happens(logs):
    with FakeModelServer(["Sure! Here is the report:", GOOD]) as server:
        results = bakeoff.run_bakeoff(server.url, repeat=2, logs=logs, timeout=5.0)
    broken, fine = fan_runs(results)
    assert broken["outcome"] == FAILED and broken["reports"] == []
    assert broken["reasons"] == ["agent unavailable: the model's answer was not valid JSON"]
    assert fine["outcome"] == FIRST_TRY


def test_a_passing_report_that_differs_from_the_expected_answer_is_pointed_out(logs):
    with FakeModelServer([CITES_NOTHING]) as server:
        results = bakeoff.run_bakeoff(server.url, repeat=1, logs=logs, timeout=5.0)
    (run,) = fan_runs(results)
    assert run["outcome"] == FIRST_TRY
    assert run["differs_from_expected"] == ["does not cite vib_1x", "priority is none, expected medium or high"]
    assert "1 of 1 differ from the expected answer" in bakeoff.table(results)


def test_seconds_are_timed_not_made_up(logs):
    with FakeModelServer([FAILS_CHECKS, GOOD], delay=0.2) as server:
        results = bakeoff.run_bakeoff(server.url, repeat=1, logs=logs, timeout=5.0)
    (run,) = fan_runs(results)
    assert all(0.2 <= attempt["seconds"] < 2 for attempt in run["reports"])
    assert sum(attempt["seconds"] for attempt in run["reports"]) <= run["seconds"] < 4


def test_the_model_name_is_the_one_the_server_reports(logs):
    with FakeModelServer([GOOD], models=("Some-Model-Q4_K_M.gguf", "another")) as server:
        results = bakeoff.run_bakeoff(server.url, repeat=1, logs=logs, timeout=5.0)
    assert results["model"] == "Some-Model-Q4_K_M.gguf" and results["llm_url"] == server.url
    assert "model: Some-Model-Q4_K_M.gguf" in bakeoff.table(results)


def test_the_table_counts_each_outcome_per_log_and_in_total(logs):
    with FakeModelServer([GOOD, FAILS_CHECKS, GOOD, FAILS_CHECKS, FAILS_CHECKS]) as server:
        results = bakeoff.run_bakeoff(server.url, repeat=3, logs=logs, timeout=5.0)
    rows = {line.split()[0]: line.split() for line in bakeoff.table(results).splitlines() if line.strip()}
    assert rows["log"][:2] == ["log", "machine"]
    fan, no_readings = [line.split() for line in bakeoff.table(results).splitlines() if line.startswith("fan ")]
    assert fan[:5] == ["fan", "fan-1", "1", "1", "1"]
    assert no_readings[:7] == ["fan", "fan-9", "0", "0", "0", "-", "-"]
    assert "model not asked: no readings for this machine" in " ".join(no_readings)
    assert rows["total"][:4] == ["total", "1", "1", "1"]


def test_cli_runs_every_real_log_writes_one_json_file_and_prints_one_table(tmp_path, capsys):
    out = tmp_path / "bakeoff.json"
    with FakeModelServer([CITES_NOTHING] * 100) as server:
        code = bakeoff.main(["--llm-url", server.url, "--repeat", "2", "--out", str(out)])
    printed = capsys.readouterr().out
    results = json.loads(out.read_text())

    assert code == 0 and list(tmp_path.iterdir()) == [out]
    assert results["model"] == "llama-test.gguf" and results["repeat"] == 2
    expected_logs = sorted(p.name.removesuffix(".expected.json") for p in bakeoff.LOGS.glob("*.expected.json"))
    assert len(expected_logs) == 12
    assert sorted({entry["log"] for entry in results["logs"]}) == expected_logs
    assert all(len(entry["runs"]) == 2 for entry in results["logs"])
    outcomes = {entry["log"]: {run["outcome"] for run in entry["runs"]} for entry in results["logs"]}
    assert outcomes["wrong_machine"] == {NOT_ASKED}
    assert all(seen == {FIRST_TRY} for log, seen in outcomes.items() if log != "wrong_machine")
    asked = sum(len(run["reports"]) for entry in results["logs"] for run in entry["runs"])
    assert asked == len(server.chats)
    for name in expected_logs:
        assert f"\n{name} " in printed
    assert "model: llama-test.gguf" in printed and str(out) in printed


def test_cli_without_a_server_exits_1_says_why_and_writes_nothing(tmp_path, capsys):
    url = closed_port_url()
    out = tmp_path / "bakeoff.json"
    code = bakeoff.main(["--llm-url", url, "--out", str(out)])
    captured = capsys.readouterr()
    assert code == 1 and not out.exists() and captured.out == ""
    assert f"no model server is running at {url}" in captured.err
