"""The local model call against a fake model server in this process. No real model, no network."""

import importlib.util
import json
import socket

import pytest
from part_review_cases import HISTORIES, MACHINE, PLATFORM, load_case
from fake_part_receiver import FakeReceiver

from device.agent.tests.test_local_call import FakeModelServer
from partlog.review import history as histories
from partlog.review.agent import run_review
from partlog.review.local import LocalReviewCall

PART = "part-0007"
HOW = "once-per-turn vibration grown (unbalance, a bent shaft, a lost blade)"


def draft(**changes) -> str:
    return json.dumps({
        "what": "Possible unbalance-like on the Drive roller, answered as a real problem by Sam.",
        "why": "1 alarm, a finding that says it needs repair, and 0 repairs since.",
        "needs_repair": "yes",
        "cited_alarms": ["alarm-1234"],
        "cited_entries": ["entry-000123"],
        **changes,
    })


GOOD = draft()
WRONG_PART = draft(why="The weight may have come from the Motor mount.")
PROBLEM = "why names a part that is not in the history: Motor mount"


@pytest.fixture(autouse=True)
def no_env(monkeypatch):
    monkeypatch.delenv("EFFENTE_LLM_URL", raising=False)


def review(server_url, tmp_path, data=None, **kw):
    data = data or load_case("confirmed_waiting")[0]
    return run_review(MACHINE, PART, load=lambda: histories.part_history(data, PART),
                      call=LocalReviewCall(server_url, **kw), agent_log_path=tmp_path / "log.jsonl")


def closed_port_url() -> str:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return f"http://127.0.0.1:{sock.getsockname()[1]}"


def test_a_good_answer_becomes_a_checked_review(tmp_path):
    with FakeModelServer([GOOD]) as server:
        result = review(server.url, tmp_path)

    assert result.ok, result
    assert result.review == {**json.loads(GOOD), "how": HOW, "since": "2026-11-14T19:12:03Z"}
    assert list(result.review) == ["what", "how", "since", "needs_repair", "why", "cited_alarms", "cited_entries"]
    logged = json.loads((tmp_path / "log.jsonl").read_text())
    assert logged["model"] == "local: llama-test.gguf"
    assert logged["usage"] == {"input_tokens": 100, "output_tokens": 20}
    assert [call["name"] for call in result.tool_calls] == ["get_part_history"]


def test_the_model_is_given_the_fact_sheet_and_nothing_of_another_part(tmp_path):
    data, _ = load_case("confirmed_waiting")
    with FakeModelServer([GOOD]) as server:
        review(server.url, tmp_path, data)

    (chat,) = server.chats
    assert "tools" not in chat and chat["temperature"] == 0
    system, user = chat["messages"]
    sheet = histories.fact_sheet(histories.part_history(data, PART))
    assert json.dumps(sheet) in user["content"]
    prompt = system["content"] + user["content"]
    for other in ("Motor mount", "part-0002", "alarm-2301", "Belt", "part-0003", "entry-000090", "looseness"):
        assert other not in prompt, other


def test_the_model_is_asked_for_the_draft_only_never_how_or_since(tmp_path):
    with FakeModelServer([GOOD]) as server:
        review(server.url, tmp_path)

    forced = server.chats[0]["response_format"]
    assert forced["type"] == "json_schema" and forced["json_schema"]["name"] == "part_review"
    schema = forced["json_schema"]["schema"]
    assert sorted(schema["properties"]) == ["cited_alarms", "cited_entries", "needs_repair", "what", "why"]
    assert schema["additionalProperties"] is False


def test_a_local_review_of_real_history_is_not_marked_simulated(tmp_path):
    data, _ = load_case("confirmed_waiting")
    for row in data["alarms"] + data["entries"]:
        row["simulated"] = False

    with FakeModelServer([GOOD]) as server:
        result = review(server.url, tmp_path, data)

    assert result.ok and result.simulated is False


def test_a_local_review_of_simulated_history_is_marked_simulated(tmp_path):
    with FakeModelServer([GOOD]) as server:
        result = review(server.url, tmp_path)

    assert result.ok and result.simulated is True


def test_an_earlier_stand_in_review_does_not_make_a_local_review_simulated(tmp_path):
    data, _ = load_case("confirmed_waiting")
    for row in data["alarms"] + data["entries"]:
        row["simulated"] = False
    data["entries"].append({"entry_id": "entry-000400", "part_id": PART, "at": "2026-11-14T19:30:00Z",
                            "kind": "review", "text": "Stand-in review: Drive roller needs repair.",
                            "by": "part review agent", "needs_repair": None, "simulated": True})

    with FakeModelServer([GOOD]) as server:
        result = review(server.url, tmp_path, data)

    assert result.ok and result.simulated is False


def test_a_failed_check_gets_one_retry_told_what_was_wrong(tmp_path):
    with FakeModelServer([WRONG_PART, GOOD]) as server:
        result = review(server.url, tmp_path)

    assert result.ok and result.review["why"] == json.loads(GOOD)["why"]
    first, second = server.chats
    assert len(first["messages"]) == 2 and len(second["messages"]) == 4
    assert second["messages"][2] == {"role": "assistant", "content": WRONG_PART}
    assert f"- {PROBLEM}" in second["messages"][3]["content"]
    assert json.loads((tmp_path / "log.jsonl").read_text())["usage"] == {"input_tokens": 200, "output_tokens": 40}


def test_a_second_failure_is_discarded_and_the_run_says_why(tmp_path):
    with FakeModelServer([WRONG_PART, draft(why="It has run 300 hours.")]) as server:
        result = review(server.url, tmp_path)

    assert len(server.chats) == 2  # one retry, no more
    assert (result.ok, result.review, result.reason) == (False, None, "review failed checks")
    assert result.problems == ["why has a number that is not in the history: 300"]


@pytest.mark.parametrize("key, value", [("how", "the roller was dropped"), ("since", "2026-11-01T00:00:00Z")])
def test_a_model_that_writes_how_or_since_itself_is_refused(tmp_path, key, value):
    """A server that cannot force the schema lets the model add keys. The code refuses them."""
    with FakeModelServer([draft(**{key: value})] * 2, forces_schema=False) as server:
        result = review(server.url, tmp_path)

    assert (result.ok, result.review) == (False, None)
    assert result.problems == [f"{key} is filled by the code, not by the writer"]
    assert f"- {key} is filled by the code, not by the writer" in server.chats[-1]["messages"][3]["content"]


def test_a_server_that_cannot_force_the_schema_is_asked_for_plain_json(tmp_path):
    with FakeModelServer([GOOD], forces_schema=False) as server:
        result = review(server.url, tmp_path)

    assert result.ok
    assert [chat["response_format"]["type"] for chat in server.chats] == ["json_schema", "json_object"]


def test_no_model_server_running_gives_no_review_and_a_plain_reason(tmp_path):
    url = closed_port_url()
    data, _ = load_case("confirmed_waiting")
    with FakeReceiver(data) as receiver:
        result = run_review(MACHINE, PART, load=lambda: histories.part_history(data, PART),
                            call=LocalReviewCall(url), platform_url=receiver.url)

    assert (result.ok, result.review) == (False, None)
    assert result.reason == f"agent unavailable: no model server is running at {url}"
    done = receiver.runs[-1]
    assert (done["status"], done["ok"], done["reason"]) == ("done", False, result.reason)
    assert done["model"] == "local: (not called)"
    assert [call["name"] for call in done["tool_calls"]] == ["get_part_history"]
    assert receiver.posted == []


@pytest.mark.parametrize("answer, reason", [
    ("the roller looks fine", "agent unavailable: the model's answer was not valid JSON"),
    (json.dumps({"what": "x"}), "agent unavailable: the model's answer was not a report: why is missing or the wrong type"),
    (503, "agent unavailable: the model server answered HTTP 503: Loading model"),
])
def test_an_answer_that_is_not_a_review_is_reported_separately(tmp_path, answer, reason):
    with FakeModelServer([answer]) as server:
        result = review(server.url, tmp_path)

    assert (result.ok, result.reason) == (False, reason)


def test_a_server_that_does_not_answer_in_time_is_reported(tmp_path):
    with FakeModelServer([GOOD], delay=1.0) as server:
        result = review(server.url, tmp_path, timeout=0.2)

    assert result.reason == f"agent unavailable: the model server at {server.url} did not answer within 0.2 seconds"


def test_the_command_runs_the_local_backend(capsys, tmp_path):
    spec = importlib.util.spec_from_file_location("part_review_command_local", PLATFORM / "part_review.py")
    command = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(command)

    with FakeModelServer([GOOD]) as server:
        code = command.main(["--machine", MACHINE, "--part", PART, "--backend", "local", "--llm-url", server.url,
                             "--history", str(HISTORIES / "confirmed_waiting.json"),
                             "--agent-log", str(tmp_path / "log.jsonl")])
    (shown,) = json.loads(capsys.readouterr().out)

    assert code == 0 and shown["review"]["how"] == HOW
    assert not shown["review"]["what"].startswith("Stand-in review:")


def test_the_command_points_to_the_stand_in_when_no_model_server_runs(capsys, tmp_path):
    spec = importlib.util.spec_from_file_location("part_review_command_local", PLATFORM / "part_review.py")
    command = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(command)

    code = command.main(["--machine", MACHINE, "--part", PART, "--backend", "local", "--llm-url", closed_port_url(),
                         "--history", str(HISTORIES / "confirmed_waiting.json"),
                         "--agent-log", str(tmp_path / "log.jsonl")])
    err = capsys.readouterr().err

    assert code == 1
    assert "part-0007: no review was written: agent unavailable: no model server is running at" in err
    assert "Use --backend standin for a rule-based review with no model call." in err
