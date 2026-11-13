"""The run reaches a platform in the shape of platform/CONTRACT.md message 4."""

import json
import re
import socket
from pathlib import Path

import pytest

from device.agent import run as run_module
from device.agent.harness import AgentUnavailable, run_agent
from device.agent.run import make_run_id
from device.agent.standin import MODEL_NAME, stand_in_call

from .receiver import Receiver

LOGS = Path(__file__).parent / "logs"
CONTRACT_FIELDS = {
    "run_id", "device_id", "machine_id", "at", "status", "model", "usage", "ok",
    "reason", "problems", "tool_calls", "report", "simulated",
}
REPORT_FIELDS = {"summary", "priority", "cited_features", "recommended_action", "part_reference"}
TIME = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?Z$")
RUN_ID = re.compile(r"^[A-Za-z0-9_.-]+$")


@pytest.fixture(autouse=True)
def no_env_platform(monkeypatch):
    monkeypatch.delenv("EFFENTE_PLATFORM_URL", raising=False)


def stand_in(machine="conveyor-07", log="imbalance", tmp_path=None, **kwargs):
    return run_agent(machine, LOGS / f"{log}.jsonl", LOGS / "model.json", tmp_path / "agent.jsonl",
                     call=kwargs.pop("call", stand_in_call), **kwargs)


def check_contract_shape(path, body):
    assert CONTRACT_FIELDS <= set(body)
    assert path == f"/api/agent/runs/{body['run_id']}"
    assert RUN_ID.match(body["run_id"])
    assert isinstance(body["machine_id"], str) and body["machine_id"]
    assert isinstance(body["device_id"], str)
    assert TIME.match(body["at"])
    assert body["status"] in ("running", "done")
    assert isinstance(body["model"], str)
    assert isinstance(body["usage"], dict)
    assert isinstance(body["ok"], bool)
    assert isinstance(body["reason"], str)
    assert isinstance(body["problems"], list) and all(isinstance(p, str) for p in body["problems"])
    assert isinstance(body["simulated"], bool)
    for call in body["tool_calls"]:
        assert set(call) == {"at", "name", "input", "output"}
        assert TIME.match(call["at"])
        assert isinstance(call["input"], dict)
        assert isinstance(call["output"], str) and len(call["output"]) <= 2000
    if body["ok"]:
        assert set(body["report"]) == REPORT_FIELDS
    else:
        assert body["report"] is None


def test_runs_are_posted_running_then_each_tool_call_then_done(tmp_path):
    with Receiver() as platform:
        result = stand_in(tmp_path=tmp_path, platform_url=platform.url)

    assert result.ok
    for path, body in platform.puts:
        check_contract_shape(path, body)
    bodies = platform.bodies
    assert [b["status"] for b in bodies] == ["running", "running", "running", "done"]
    assert [len(b["tool_calls"]) for b in bodies] == [0, 1, 2, 2]
    assert {b["run_id"] for b in bodies} == {result.run_id}
    assert {b["at"] for b in bodies} == {bodies[0]["at"]}

    first, final = bodies[0], bodies[-1]
    assert first["ok"] is False and first["report"] is None
    assert final["ok"] is True and final["report"] == result.report
    assert final["device_id"] == "effente-01" and final["machine_id"] == "conveyor-07"
    assert final["model"] == MODEL_NAME and final["simulated"] is True
    assert final["model_version"] == "fake-0.1"
    assert [c["name"] for c in final["tool_calls"]] == ["get_feature_table", "get_readings"]
    assert final["tool_calls"][1]["input"] == {"since": ""}


def test_a_failed_report_is_posted_with_problems_and_no_report(tmp_path):
    def cites_rattle(machine_id, records, table, schema, tools):
        report, usage = stand_in_call(machine_id, records, table, schema, tools)
        return {**report, "cited_features": ["vib_harm_3_10"]}, usage

    with Receiver() as platform:
        stand_in(tmp_path=tmp_path, platform_url=platform.url, call=cites_rattle)
    final = platform.bodies[-1]
    check_contract_shape(*platform.puts[-1])
    assert final["status"] == "done" and final["ok"] is False and final["report"] is None
    assert final["reason"] == "report failed checks"
    assert final["problems"] == ["cites feature that did not move in the readings: vib_harm_3_10"]


def test_agent_unavailable_is_posted_as_done_with_the_reason(tmp_path):
    def offline(*_):
        raise AgentUnavailable("no connection")

    with Receiver() as platform:
        stand_in(tmp_path=tmp_path, platform_url=platform.url, call=offline)
    final = platform.bodies[-1]
    assert final["status"] == "done" and final["ok"] is False
    assert final["reason"] == "agent unavailable: no connection"


def test_no_readings_is_still_posted_and_closed(tmp_path):
    with Receiver() as platform:
        result = stand_in(log="wrong_machine", tmp_path=tmp_path, platform_url=platform.url)
    assert result.reason == "no readings for this machine"
    assert [b["status"] for b in platform.bodies] == ["running", "done"]
    check_contract_shape(*platform.puts[-1])


def test_tool_output_is_cut_to_2000_characters(tmp_path):
    def asks_for_parts(machine_id, records, table, schema, tools):
        tools.search_parts("fan blade 120 mm")
        return stand_in_call(machine_id, records, table, schema, tools)

    with Receiver() as platform:
        result = stand_in(tmp_path=tmp_path, platform_url=platform.url,
                          call=asks_for_parts, search=lambda q: "x" * 5000)
    parts = platform.bodies[-1]["tool_calls"][0]
    assert parts["name"] == "search_parts" and parts["input"] == {"query": "fan blade 120 mm"}
    assert len(parts["output"]) == 2000
    assert result.tool_calls[0] == parts


def test_platform_url_falls_back_to_the_environment(tmp_path, monkeypatch):
    with Receiver() as platform:
        monkeypatch.setenv("EFFENTE_PLATFORM_URL", platform.url + "/")
        stand_in(tmp_path=tmp_path)
    assert len(platform.puts) == 4


def test_nothing_is_posted_without_a_platform_url(tmp_path):
    with Receiver() as platform:
        result = stand_in(tmp_path=tmp_path)
    assert platform.puts == [] and result.ok and result.notes == []


def closed_port_url():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    return f"http://127.0.0.1:{port}"


def test_an_unreachable_platform_does_not_fail_the_run(tmp_path):
    result = stand_in(tmp_path=tmp_path, platform_url=closed_port_url())
    assert result.ok and result.report is not None
    assert any(note.startswith("could not post to platform") for note in result.notes)
    entry = json.loads((tmp_path / "agent.jsonl").read_text())
    assert entry["ok"] is True and entry["run_id"] == result.run_id


def test_a_slow_platform_does_not_fail_the_run(tmp_path, monkeypatch):
    monkeypatch.setattr(run_module, "POST_TIMEOUT_SECONDS", 0.2)
    with Receiver(delay=1.0) as platform:
        result = stand_in(tmp_path=tmp_path, platform_url=platform.url)
    assert result.ok
    assert any("could not post to platform" in note for note in result.notes)


def test_a_platform_that_rejects_the_run_does_not_fail_it(tmp_path):
    with Receiver(status=400) as platform:
        result = stand_in(tmp_path=tmp_path, platform_url=platform.url)
    assert result.ok and len(platform.puts) == 4


def test_run_id_keeps_only_allowed_characters():
    run_id = make_run_id("line 3/fan#2")
    assert RUN_ID.match(run_id) and run_id.endswith("-line-3-fan-2")
