"""Tavily parts lookup with a fake fetch, and the command line. No network, no API key."""

import json
import socket
import subprocess
import sys
import urllib.error
from pathlib import Path

import pytest

from device.agent import __main__ as cli
from device.agent.parts import TAVILY_URL, search_parts

from .receiver import Receiver

LOGS = Path(__file__).parent / "logs"
REPO = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("EFFENTE_PLATFORM_URL", "TAVILY_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def test_no_key_means_unavailable_and_no_fetch():
    def must_not_fetch(*_):
        raise AssertionError("fetched without a key")

    text = search_parts("fan blade", fetch=must_not_fetch)
    assert "unavailable" in text and "Leave part_reference empty" in text


def test_results_are_trimmed_and_the_request_is_shaped_as_we_expect(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "fake-test-key")
    seen = {}

    def fake_fetch(url, body, headers, timeout):
        seen.update(url=url, body=json.loads(body), headers=headers, timeout=timeout)
        results = [{"title": f"Part {i}", "url": f"https://example.com/{i}", "content": "y" * 900, "score": 0.5}
                   for i in range(6)]
        return json.dumps({"query": "q", "results": results}).encode()

    out = json.loads(search_parts("120 mm fan blade", fetch=fake_fetch))
    assert seen["url"] == TAVILY_URL
    assert seen["body"]["query"] == "120 mm fan blade" and seen["body"]["max_results"] == 3
    assert seen["headers"]["Authorization"] == "Bearer fake-test-key"
    assert seen["timeout"] <= 10
    assert len(out) == 3
    assert set(out[0]) == {"title", "url", "snippet"} and len(out[0]["snippet"]) == 300


@pytest.mark.parametrize("failure", [
    urllib.error.URLError("no route"),
    socket.timeout("timed out"),
    urllib.error.HTTPError(TAVILY_URL, 401, "Unauthorized", {}, None),
])
def test_network_trouble_becomes_a_sentence_not_an_exception(monkeypatch, failure):
    monkeypatch.setenv("TAVILY_API_KEY", "fake-test-key")

    def failing(*_):
        raise failure

    text = search_parts("fan blade", fetch=failing)
    assert text.startswith("Parts lookup is unavailable") and "Leave part_reference empty" in text


@pytest.mark.parametrize("raw", [b"not json", b"[]", json.dumps({"results": []}).encode()])
def test_odd_replies_do_not_raise(monkeypatch, raw):
    monkeypatch.setenv("TAVILY_API_KEY", "fake-test-key")
    text = search_parts("fan blade", fetch=lambda *_: raw)
    assert "Leave part_reference empty" in text


def args(tmp_path, *extra):
    return ["--machine", "conveyor-07", "--log", str(LOGS / "imbalance.jsonl"),
            "--model-file", str(LOGS / "model.json"), "--agent-log", str(tmp_path / "agent.jsonl"), *extra]


def test_cli_stand_in_writes_a_report_and_posts_it(tmp_path, capsys):
    with Receiver() as platform:
        code = cli.main(args(tmp_path, "--stand-in", "--platform", platform.url))
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out["ok"] and out["simulated"] is True
    assert out["report"]["summary"].startswith("Stand-in report:")
    assert platform.bodies[-1]["status"] == "done" and platform.bodies[-1]["ok"] is True


def test_cli_live_without_the_package_exits_and_posts_agent_unavailable(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(cli.importlib.util, "find_spec", lambda name: None)
    with Receiver() as platform:
        code = cli.main(args(tmp_path, "--platform", platform.url))
    captured = capsys.readouterr()
    assert code == 1
    assert "anthropic package is not installed" in captured.err
    final = platform.bodies[-1]
    assert final["ok"] is False and final["report"] is None
    assert final["reason"] == "agent unavailable: the anthropic package is not installed (pip install anthropic)"
    assert json.loads((tmp_path / "agent.jsonl").read_text())["ok"] is False


def test_cli_live_without_a_key_exits_and_never_calls_claude(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(cli.importlib.util, "find_spec", lambda name: object())

    def must_not_call(*_):
        raise AssertionError("live call attempted")

    monkeypatch.setattr(cli, "call_claude", must_not_call)
    code = cli.main(args(tmp_path))
    out = json.loads(capsys.readouterr().out)
    assert code == 1 and out["reason"] == "agent unavailable: ANTHROPIC_API_KEY is not set"


def test_module_runs_from_the_command_line(tmp_path):
    env = {"PATH": "/usr/bin:/bin"}
    done = subprocess.run([sys.executable, "-m", "device.agent", *args(tmp_path, "--stand-in")],
                          cwd=REPO, env=env, capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["report"]["cited_features"] == ["vib_1x"]
