"""platform/setup_run.py, the command a person or the desktop app runs (plan row U4)."""

import json
import socket
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from setup_cases import CASES, MACHINE

COMMAND = Path(__file__).resolve().parents[2] / "setup_run.py"


def command(*args, stdin=None, cwd=None):
    return subprocess.run([sys.executable, str(COMMAND), *args], input=stdin, capture_output=True, text=True,
                          timeout=60, cwd=cwd)


def answers_file(tmp_path, case="belt_conveyor"):
    path = tmp_path / "answers.json"
    path.write_text(json.dumps(CASES[case][0]))
    return str(path)


def test_the_stand_in_fills_the_profile_and_posts_nothing_with_no_post(tmp_path):
    done = command("--machine", MACHINE, "--answers", answers_file(tmp_path), "--no-post",
                   "--agent-log", str(tmp_path / "log.jsonl"), cwd=tmp_path)
    assert done.returncode == 0, done.stderr
    shown = json.loads(done.stdout)
    assert shown["ok"] and shown["setup"]["profile"] == CASES["belt_conveyor"][1]
    assert shown["setup"]["choice"]["library_entry"] == "conveyor" and shown["simulated"] is True
    assert "tool_calls" not in shown


def test_answers_on_stdin(tmp_path):
    done = command("--machine", MACHINE, "--answers", "-", "--no-post", "--agent-log", str(tmp_path / "log.jsonl"),
                   stdin=json.dumps(CASES["pump_direct"][0]))
    assert done.returncode == 0 and json.loads(done.stdout)["setup"]["profile"] == CASES["pump_direct"][1]


def test_unreadable_answers_exit_1_with_the_reason(tmp_path):
    bad = tmp_path / "answers.json"
    bad.write_text("{not json")
    done = command("--machine", MACHINE, "--answers", str(bad), "--no-post", "--agent-log", str(tmp_path / "l.jsonl"))
    assert done.returncode == 1
    assert "No profile was filled: no answers: cannot read" in done.stderr and "by hand" in done.stderr


def test_a_running_setup_run_for_the_machine_stops_a_second_one(receiver, tmp_path):
    port = receiver.rsplit(":", 1)[1]
    running = {"run_id": "setup-20990101-000000-conveyor-07", "agent": "setup", "machine_id": MACHINE,
               "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "status": "running"}
    request = urllib.request.Request(f"{receiver}/api/agent/runs/{running['run_id']}", method="PUT",
                                     data=json.dumps(running).encode(), headers={"Content-Type": "application/json"})
    urllib.request.urlopen(request).read()

    done = command("--machine", MACHINE, "--answers", answers_file(tmp_path), "--port", port,
                   "--agent-log", str(tmp_path / "log.jsonl"))
    assert done.returncode == 1 and "already running" in done.stderr and running["run_id"] in done.stderr
    with urllib.request.urlopen(f"{receiver}/api/machines/{MACHINE}/agent-runs") as response:
        assert len(json.loads(response.read())["runs"]) == 1  # no second run was posted


def test_a_run_posts_to_the_receiver(receiver, tmp_path):
    port = receiver.rsplit(":", 1)[1]
    done = command("--machine", MACHINE, "--answers", answers_file(tmp_path), "--port", port,
                   "--agent-log", str(tmp_path / "log.jsonl"))
    assert done.returncode == 0, done.stderr
    with urllib.request.urlopen(f"{receiver}/api/machines/{MACHINE}/agent-runs") as response:
        (posted,) = json.loads(response.read())["runs"]
    assert posted["agent"] == "setup" and posted["status"] == "done" and posted["ok"] is True


def test_with_no_model_server_the_local_backend_exits_1_and_points_at_the_stand_in(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        url = f"http://127.0.0.1:{sock.getsockname()[1]}"
    done = command("--machine", MACHINE, "--answers", answers_file(tmp_path), "--backend", "local", "--llm-url", url,
                   "--no-post", "--agent-log", str(tmp_path / "log.jsonl"))
    assert done.returncode == 1
    assert "No profile was filled: agent unavailable:" in done.stderr and "--backend standin" in done.stderr
