"""The simulated device in the app's process, and agent runs through platform/report.py."""

import json
import os
import shutil

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from app.data import paths
from app.data.state import NO_KEY_MESSAGE
from fakes import sample_records
from receiver_testkit import Client

SIM_MACHINE = "demo-fan-1"


def test_sim_runs_in_process_and_takes_keys(qtbot, tmp_path, make_host, make_state):
    host = make_host(with_sim=True, sim_args=["--seed", "1", "--hold", "0"])
    state = make_state(host)
    sim = state.sim()
    assert sim is not None
    qtbot.waitUntil(lambda: state.current_machine() == SIM_MACHINE and state.latest() is not None, timeout=5000)
    assert state.freshness()["status"] == "live"
    assert state.latest()["simulated"] is True
    assert "heat" in state.latest() and state.latest()["heat"]["source"] == "camera"
    assert "case normal" in sim.status()

    sim.key("2")  # unbalance
    qtbot.waitUntil(lambda: state.unanswered_alarms() != [], timeout=5000)
    assert state.unanswered_alarms()[0]["diagnosis"] == "unbalance_like"
    sim.key("h")  # too_hot, a limit, shown at once with --hold 0
    qtbot.waitUntil(lambda: [p["name"] for p in state.problems()] == ["too_hot"], timeout=5000)

    # The files report.py reads, as platform/run.py has the sim write them.
    assert (tmp_path / "data" / "device.jsonl").exists()
    assert json.loads((tmp_path / "data" / "model.json").read_text())["model_version"] == "sim-3"

    host.stop()
    assert state.sim() is None
    qtbot.waitUntil(lambda: state.freshness()["platform_up"] is False, timeout=5000)


def test_sim_takes_the_sensitivity_end_to_end(qtbot, make_host, make_state):
    host = make_host(with_sim=True, sim_args=["--seed", "2", "--case", "unbalance"])
    state = make_state(host)
    qtbot.waitUntil(lambda: state.sensitivity()["reported"] == 0.5, timeout=5000)
    qtbot.waitUntil(lambda: state.model_line() and state.model_line()["kind"] == "fault", timeout=5000)
    state.set_sensitivity(0.0)  # alarm line 4: the unbalance case (score about 2.4) falls under it
    qtbot.waitUntil(lambda: state.sensitivity() == {"reported": 0.0, "wanted": 0.0}, timeout=5000)
    qtbot.waitUntil(lambda: state.latest()["sensitivity"] == 0.0, timeout=5000)
    assert state.latest()["alarm_line"] == 4.0
    qtbot.waitUntil(lambda: state.model_line()["kind"] == "normal", timeout=5000)
    assert "sensitivity set to 0" in host.sim.last_message


@pytest.fixture
def report_tree(tmp_path):
    """A copy of platform/report.py whose data folder is a temp one, with the real device/agent beside it."""
    root = tmp_path / "repo"
    (root / "platform" / "server" / "data").mkdir(parents=True)
    shutil.copy(paths.REPORT_SCRIPT, root / "platform" / "report.py")
    os.symlink(paths.REPO / "device", root / "device")
    return root


def write_session(root, records):
    _, sim_model, _ = paths.sim_modules()
    data = root / "platform" / "server" / "data"
    (data / "device.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))
    (data / "model.json").write_text(json.dumps(sim_model.model_file()))


def test_stand_in_agent_streams_its_tool_calls(qtbot, make_host, make_state, report_tree):
    records = sample_records()
    write_session(report_tree, records)
    host = make_host()
    client = Client(host.port)
    for r in records:
        client.json("POST", "/api/records", r)
    state = make_state(host, report_script=report_tree / "platform" / "report.py")
    qtbot.waitUntil(lambda: state.current_machine() == SIM_MACHINE, timeout=5000)
    seen = []
    state.runs_changed.connect(lambda: seen.append(
        (state.runs()[0]["status"], len(state.runs()[0]["tool_calls"])) if state.runs() else None))

    assert state.start_agent("standin") is True
    assert state.agent_running() is True
    assert state.start_agent("standin") is False  # one run per machine
    assert "already running" in state.last_agent_message()
    qtbot.waitUntil(lambda: bool(state.runs()) and state.runs()[0]["status"] == "done" and not state.agent_running(),
                    timeout=30000)
    run = state.runs()[0]
    assert run["ok"] is True and run["simulated"] is True
    assert run["report"]["summary"].startswith("Stand-in report:")
    assert run["machine_id"] == SIM_MACHINE
    # runs_changed fired while it ran, once per tool call as each arrived.
    progress = [s for s in seen if s and s[0] == "running"]
    assert [n for _, n in progress] == sorted({n for _, n in progress})
    assert len(progress) >= len(run["tool_calls"]) and len(run["tool_calls"]) >= 1
    assert state.last_agent_message() == ""


def test_no_device_log_says_report_pys_own_message(qtbot, make_host, make_state, report_tree):
    host = make_host()
    Client(host.port).json("POST", "/api/records", sample_records(heat=False)[0])
    state = make_state(host, report_script=report_tree / "platform" / "report.py")
    qtbot.waitUntil(lambda: state.current_machine() == SIM_MACHINE, timeout=5000)
    with qtbot.waitSignal(state.agent_message, timeout=20000, check_params_cb=bool) as sig:
        assert state.start_agent() is True
    assert sig.args[0] == "No device log yet. Start python3 platform/run.py first."
    qtbot.waitUntil(lambda: not state.agent_running(), timeout=5000)


def test_live_without_a_key_is_refused(qtbot, make_host, make_state, report_tree, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    host = make_host()
    Client(host.port).json("POST", "/api/records", sample_records(heat=False)[0])
    state = make_state(host, report_script=report_tree / "platform" / "report.py")
    qtbot.waitUntil(lambda: state.current_machine() == SIM_MACHINE, timeout=5000)
    with qtbot.waitSignal(state.agent_message, timeout=1000) as sig:
        assert state.start_agent("live") is False
    assert sig.args[0] == NO_KEY_MESSAGE
    assert state.agent_running() is False
    with pytest.raises(ValueError):
        state.start_agent("claude")


def test_fake_state_reads(qtbot):
    from fakes import ANSWERED_ALARM, OPEN_ALARM, FakeState

    state = FakeState(with_sim=True)
    assert state.headline()["state"] == "wrong"
    assert state.freshness()["status"] == "live"
    assert [a["alarm_id"] for a in state.alarms()] == [OPEN_ALARM, ANSWERED_ALARM]
    assert [a["alarm_id"] for a in state.unanswered_alarms()] == [OPEN_ALARM]
    assert state.runs()[0]["report"]["summary"].startswith("Stand-in report:")
    assert state.sensitivity() == {"reported": 0.5, "wanted": None}
    assert "frames" in state.latest()["heat"] and "frames" not in state.records()[-1]["heat"]
    with qtbot.waitSignal(state.alarms_changed):
        state.answer_alarm(OPEN_ALARM, "false")
    assert state.unanswered_alarms() == []
    with qtbot.waitSignal(state.settings_changed):
        state.set_sensitivity(0.8)
    assert state.sensitivity()["wanted"] == 0.8
    state.sim().key("2")
    assert state.sim().keys == ["2"]
    state.advance(10)
    assert state.freshness()["status"] == "no_readings"
