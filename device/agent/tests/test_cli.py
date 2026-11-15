"""The parts lookup that is switched off, and the command line. No network."""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from device.agent import __main__ as cli
from device.agent.tools import SEARCH_DISABLED, Tools, search_disabled

from .receiver import Receiver

LOGS = Path(__file__).parent / "logs"
REPO = Path(__file__).resolve().parents[3]
AGENT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("EFFENTE_PLATFORM_URL", "EFFENTE_LLM_URL"):
        monkeypatch.delenv(name, raising=False)


def test_the_parts_lookup_is_switched_off_unless_a_test_passes_its_own():
    tools = Tools([], {})
    assert tools.search is search_disabled
    assert tools.search_parts("120 mm fan blade") == SEARCH_DISABLED == "unavailable: disabled at this site"


def test_no_address_outside_this_laptop_is_written_in_the_agent():
    """Nothing leaves the site (PRD P9a): the only addresses in the agent's own code are this laptop's."""
    found = {(path.name, url) for path in sorted(AGENT.glob("*.py"))
             for url in re.findall(r"https?://[^\s\"'`<>)]+", path.read_text(encoding="utf-8"))}
    outside = {(name, url) for name, url in found if not re.match(r"https?://(127\.0\.0\.1|localhost)([:/]|$)", url)}
    assert found and outside == set()


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


def test_module_runs_from_the_command_line(tmp_path):
    env = {"PATH": "/usr/bin:/bin"}
    done = subprocess.run([sys.executable, "-m", "device.agent", *args(tmp_path, "--stand-in")],
                          cwd=REPO, env=env, capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["report"]["cited_features"] == ["vib_1x"]
