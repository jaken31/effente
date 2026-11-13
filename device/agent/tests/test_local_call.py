"""The local model call against a fake model server in this process. No real model, no network."""

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from device.agent import __main__ as cli
from device.agent.harness import SEARCH_DISABLED, LocalCall, run_agent, search_disabled

from .receiver import Receiver

TOOL_CALLS = ["get_readings", "get_feature_table"]


class FakeModelServer:
    """Speaks the OpenAI-style routes the agent uses and replays canned chat answers.

    Each item in `answers` is the text of one chat answer, in order, or an
    HTTP status to answer with an error instead. With
    `forces_schema` false it rejects a request that asks for a JSON schema, as
    a server too old to force one does.
    """

    def __init__(self, answers, forces_schema=True, models=("llama-test.gguf",), delay=0.0):
        self.answers = list(answers)
        self.chats: list[dict] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def reply(self, body, status=200):
                raw = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                self.reply({"object": "list", "data": [{"id": name} for name in models]})

            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                # Keep a copy as sent: the caller reuses the message list for the retry.
                fake.chats.append(request)
                if not forces_schema and request["response_format"]["type"] == "json_schema":
                    self.reply({"error": {"message": "response_format json_schema is not supported"}}, 400)
                    return
                if delay:
                    time.sleep(delay)
                answer = fake.answers.pop(0)
                if isinstance(answer, int):
                    self.reply({"error": {"message": "Loading model"}}, answer)
                    return
                self.reply({
                    "model": request["model"],
                    "choices": [{"message": {"role": "assistant", "content": answer},
                                 "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 20},
                })

            def log_message(self, *_):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.block_on_close = False  # do not wait for a deliberately slow answer
        self.server.handle_error = lambda *_: None  # the caller hanging up on a slow answer is expected
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )

    def __enter__(self) -> "FakeModelServer":
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()


def record(at, diagnosis="unbalance_like", **features):
    return {"device_id": "effente-01", "machine_id": "fan-1", "at": at, "diagnosis": diagnosis,
            "unknown_score": 0.1, "features": features}


def report(cited=("vib_1x",)):
    return json.dumps({
        "summary": "Imbalance on the fan.",
        "priority": "medium",
        "cited_features": list(cited),
        "recommended_action": "Clean and rebalance the blades.",
        "part_reference": "",
    })


GOOD = report()
FAILS_CHECKS = report(["vib_harm_3_10"])  # in the table, but it did not move
PROBLEM = "cites feature that did not move in the readings: vib_harm_3_10"


@pytest.fixture
def paths(tmp_path, monkeypatch):
    monkeypatch.delenv("EFFENTE_PLATFORM_URL", raising=False)
    monkeypatch.delenv("EFFENTE_LLM_URL", raising=False)
    log = tmp_path / "device.jsonl"
    records = [
        record("2026-11-14T16:00:00Z", vib_1x=8.4, vib_harm_3_10=0.24),
        record("2026-11-14T16:00:01Z", vib_1x=9.6, vib_harm_3_10=0.6),
    ]
    log.write_text("".join(json.dumps(r) + "\n" for r in records))
    model = tmp_path / "model.json"
    model.write_text(json.dumps({"features": [
        {"name": "vib_1x", "sentence": "strong vibration once per turn"},
        {"name": "vib_harm_3_10", "sentence": "rattling pattern"},
    ]}))
    return log, model, tmp_path / "agent.jsonl"


def run(paths, url, timeout=5.0, **kwargs):
    log, model, agent_log = paths
    return run_agent("fan-1", log, model, agent_log, call=LocalCall(url, timeout=timeout),
                     search=search_disabled, **kwargs)


def tool_names(result):
    return [call["name"] for call in result.tool_calls]


def closed_port_url():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return f"http://127.0.0.1:{probe.getsockname()[1]}"


def test_a_good_answer_becomes_a_checked_report(paths):
    with FakeModelServer([GOOD]) as server, Receiver() as platform:
        result = run(paths, server.url, platform_url=platform.url)
    assert result.ok and result.report["cited_features"] == ["vib_1x"]
    assert result.simulated is False  # a real model call on readings not marked simulated
    assert tool_names(result) == TOOL_CALLS
    assert [call["name"] for call in platform.bodies[-1]["tool_calls"]] == TOOL_CALLS
    assert {body["model"] for body in platform.bodies} == {"local: llama-test.gguf"}
    assert platform.bodies[-1]["usage"] == {"input_tokens": 100, "output_tokens": 20}
    entry = json.loads(paths[2].read_text())
    assert entry["model"] == "local: llama-test.gguf" and entry["ok"] is True


def test_the_request_carries_the_instructions_the_tool_output_and_the_schema(paths):
    with FakeModelServer([GOOD]) as server:
        result = run(paths, server.url)
    (chat,) = server.chats
    system, user = chat["messages"]
    assert chat["model"] == "llama-test.gguf" and chat["stream"] is False
    assert system["role"] == "system" and "search_parts" not in system["content"]
    for call in result.tool_calls:
        assert call["output"] in user["content"]
    # The server is asked to force the schema, and the model is shown it too.
    forced = chat["response_format"]
    assert forced["type"] == "json_schema"
    assert forced["json_schema"]["schema"]["properties"]["cited_features"]["items"]["enum"] == ["vib_1x", "vib_harm_3_10"]
    assert json.dumps(forced["json_schema"]["schema"]) in user["content"]


def test_a_server_that_cannot_force_the_schema_gets_plain_json_mode(paths):
    with FakeModelServer([FAILS_CHECKS, GOOD], forces_schema=False) as server:
        result = run(paths, server.url)
    assert result.ok
    # Refused once with the schema, then plain JSON mode for the answer and for the retry.
    assert [chat["response_format"]["type"] for chat in server.chats] == ["json_schema", "json_object", "json_object"]


def test_a_server_error_means_agent_unavailable_and_passes_on_what_it_said(paths):
    with FakeModelServer([503, GOOD]) as server:
        result = run(paths, server.url)
    assert not result.ok
    assert result.reason == "agent unavailable: the model server answered HTTP 503: Loading model"
    assert len(server.chats) == 1 and tool_names(result) == TOOL_CALLS


def test_a_report_that_fails_checks_is_retried_once_with_the_problems(paths):
    with FakeModelServer([FAILS_CHECKS, GOOD]) as server:
        result = run(paths, server.url)
    assert result.ok and result.report["cited_features"] == ["vib_1x"]
    assert len(server.chats) == 2
    *_, earlier_answer, retry = server.chats[1]["messages"]
    assert earlier_answer == {"role": "assistant", "content": FAILS_CHECKS}
    assert retry["role"] == "user" and PROBLEM in retry["content"]
    assert json.loads(paths[2].read_text())["usage"] == {"input_tokens": 200, "output_tokens": 40}
    assert tool_names(result) == TOOL_CALLS  # the retry does not repeat the tool calls


def test_a_report_that_fails_checks_twice_is_discarded(paths):
    with FakeModelServer([FAILS_CHECKS, FAILS_CHECKS, GOOD]) as server:
        result = run(paths, server.url)
    assert not result.ok and result.report is None
    assert result.reason == "report failed checks" and result.problems == [PROBLEM]
    assert len(server.chats) == 2  # one retry, no more
    assert tool_names(result) == TOOL_CALLS


@pytest.mark.parametrize("answer, reason", [
    ("Sure! Here is the report:", "the model's answer was not valid JSON"),
    ('["vib_1x"]', "the model's answer was not a report: not a JSON object"),
    ('{"summary": "Imbalance."}', "the model's answer was not a report: priority is missing or the wrong type"),
])
def test_an_answer_that_is_not_a_report_means_agent_unavailable(paths, answer, reason):
    with FakeModelServer([answer, GOOD]) as server:
        result = run(paths, server.url)
    assert not result.ok and result.report is None
    assert result.reason == f"agent unavailable: {reason}"
    assert len(server.chats) == 1  # not retried
    assert tool_names(result) == TOOL_CALLS


def test_a_server_that_is_too_slow_means_agent_unavailable(paths):
    with FakeModelServer([GOOD], delay=1.5) as server:
        started = time.monotonic()
        result = run(paths, server.url, timeout=0.3)
        waited = time.monotonic() - started
    assert not result.ok
    assert result.reason == f"agent unavailable: the model server at {server.url} did not answer within 0.3 seconds"
    assert waited < 1.2
    assert tool_names(result) == TOOL_CALLS


def test_no_server_means_agent_unavailable(paths):
    url = closed_port_url()
    result = run(paths, url)
    assert not result.ok and result.report is None
    assert result.reason == f"agent unavailable: no model server is running at {url}"
    assert tool_names(result) == TOOL_CALLS
    assert json.loads(paths[2].read_text())["model"] == "local: (not called)"


def test_a_server_with_no_model_means_agent_unavailable(paths):
    with FakeModelServer([GOOD], models=()) as server:
        result = run(paths, server.url)
    assert result.reason == f"agent unavailable: the model server at {server.url} has no model"
    assert server.chats == []


def test_the_url_comes_from_the_environment_when_none_is_given(monkeypatch):
    monkeypatch.setenv("EFFENTE_LLM_URL", "http://llm.test:1234/")
    assert LocalCall().url == "http://llm.test:1234"
    assert LocalCall("http://other.test:1/").url == "http://other.test:1"


def test_parts_lookup_is_disabled_in_local_mode():
    assert search_disabled("fan blade") == SEARCH_DISABLED == "unavailable: disabled at this site"


def cli_args(paths, *extra):
    log, model, agent_log = paths
    return ["--machine", "fan-1", "--log", str(log), "--model-file", str(model),
            "--agent-log", str(agent_log), *extra]


def test_cli_local_is_the_default_backend_and_passes_the_disabled_search(paths, capsys, monkeypatch):
    seen = {}
    real_run_agent = cli.run_agent

    def spy(*args, **kwargs):
        seen.update(kwargs)
        return real_run_agent(*args, **kwargs)

    monkeypatch.setattr(cli, "run_agent", spy)
    with FakeModelServer([GOOD]) as server:
        code = cli.main(cli_args(paths, "--llm-url", server.url))
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out["ok"] and out["report"]["cited_features"] == ["vib_1x"]
    assert seen["search"] is search_disabled and isinstance(seen["call"], LocalCall)


def test_cli_local_without_a_server_exits_1_and_says_why(paths, capsys):
    url = closed_port_url()
    with Receiver() as platform:
        code = cli.main(cli_args(paths, "--backend", "local", "--llm-url", url, "--platform", platform.url))
    captured = capsys.readouterr()
    assert code == 1
    assert f"no model server is running at {url}" in captured.err and "--backend standin" in captured.err
    final = platform.bodies[-1]
    assert final["ok"] is False and final["report"] is None
    assert final["reason"] == f"agent unavailable: no model server is running at {url}"


def test_cli_stand_in_backend_needs_no_server(paths, capsys):
    code = cli.main(cli_args(paths, "--backend", "standin"))
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out["report"]["summary"].startswith("Stand-in report:")
