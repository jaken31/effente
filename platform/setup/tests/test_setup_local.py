"""The local model call against a fake model server in this process. No real model, no network."""

import json
import socket

from setup_cases import CASES, MACHINE

from device.agent.tests.test_local_call import FakeModelServer
from setup import lookup
from setup.agent.agent import run_setup
from setup.agent.checks import read_answers
from setup.agent.local import LocalSetupCall

GIVEN, RIGHT = CASES["belt_conveyor"]
EXPLANATION = "You said it is a belt conveyor bolted to the floor. You did not know its speed or where the sensor sits."


def answer(explanation=EXPLANATION, **fields) -> str:
    return json.dumps({"profile": {**RIGHT, **fields}, "explanation": explanation})


GOOD = answer()
GUESSED_SPEED = answer(speed_rpm=1450)


def run(url, tmp_path, given=GIVEN):
    return run_setup(MACHINE, load=lambda: read_answers(given), call=LocalSetupCall(url),
                     agent_log_path=tmp_path / "setup.jsonl")


def closed_port_url() -> str:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return f"http://127.0.0.1:{sock.getsockname()[1]}"


def test_a_good_answer_becomes_a_checked_profile_and_the_lookup_picks(tmp_path):
    with FakeModelServer([GOOD]) as server:
        result = run(server.url, tmp_path)

    assert result.ok, result
    assert result.setup == {"profile": RIGHT, "explanation": EXPLANATION, "choice": lookup.choose(RIGHT)}
    assert result.simulated is False  # a real model call on a person's answers
    logged = json.loads((tmp_path / "setup.jsonl").read_text())
    assert logged["model"] == "local: llama-test.gguf" and logged["usage"] == {"input_tokens": 100, "output_tokens": 20}


def test_the_model_is_given_the_answers_and_never_the_library(tmp_path):
    with FakeModelServer([GOOD]) as server:
        run(server.url, tmp_path)

    (chat,) = server.chats
    assert "tools" not in chat and chat["temperature"] == 0
    system, user = chat["messages"]
    for item in read_answers(GIVEN):
        assert json.dumps(item["question"]) in user["content"]
    prompt = system["content"] + user["content"]
    manifest_words = {"generic_rotating", "sim_conveyor", "sim_server_rack", "demo", "unverified", "scorecard",
                      "manifest", "not validated"}
    for word in manifest_words:
        assert word not in prompt, word
    forced = chat["response_format"]
    assert forced["type"] == "json_schema" and forced["json_schema"]["name"] == "machine_profile"
    assert sorted(forced["json_schema"]["schema"]["properties"]) == ["explanation", "profile"]


def test_a_guessed_speed_gets_one_retry_told_what_was_wrong(tmp_path):
    with FakeModelServer([GUESSED_SPEED, GOOD]) as server:
        result = run(server.url, tmp_path)

    assert result.ok and result.setup["profile"]["speed_rpm"] is None
    first, second = server.chats
    assert len(first["messages"]) == 2 and len(second["messages"]) == 4
    assert second["messages"][2] == {"role": "assistant", "content": GUESSED_SPEED}
    assert "- speed_rpm 1450 is not a number the person wrote" in second["messages"][3]["content"]


def test_a_second_failure_is_discarded_and_the_run_says_why(tmp_path):
    named = answer(explanation="A conveyor. Start from generic_rotating.")
    with FakeModelServer([GUESSED_SPEED, named]) as server:
        result = run(server.url, tmp_path)

    assert len(server.chats) == 2  # one retry, no more
    assert not result.ok and result.setup is None and result.reason == "profile failed checks"
    assert result.problems == ["explanation names the library entry generic_rotating: the setup agent never names "
                               "one, the lookup picks it afterwards"]


def test_a_wrong_shape_is_a_failed_check_with_the_retry_not_a_dead_agent(tmp_path):
    extra = json.dumps({"profile": RIGHT, "explanation": EXPLANATION, "library_entry": "conveyor"})
    with FakeModelServer([extra, GOOD]) as server:
        result = run(server.url, tmp_path)

    assert result.ok and len(server.chats) == 2


def test_no_model_server_ends_the_run_as_agent_unavailable(tmp_path):
    result = run(closed_port_url(), tmp_path)
    assert not result.ok and result.reason.startswith("agent unavailable:") and result.setup is None
