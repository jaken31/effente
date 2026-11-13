"""The simulated device in the app's process, and agent runs through platform/report.py."""

import json
import os
import shutil

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from app.data import model_server, paths
from app.data.state import NO_SERVER_HINT
from fake_llama import answering, free_port, write_fake
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


def machine_state(qtbot, make_host, make_state, **kw):
    """A state with one machine to report on."""
    host = make_host()
    Client(host.port).json("POST", "/api/records", sample_records(heat=False)[0])
    state = make_state(host, **kw)
    qtbot.waitUntil(lambda: state.current_machine() == SIM_MACHINE, timeout=5000)
    return state


@pytest.fixture
def recording_report(tmp_path):
    """A report.py that writes the arguments it was given and stops."""
    script, args = tmp_path / "report.py", tmp_path / "report-args.json"
    script.write_text(f"import json, sys\njson.dump(sys.argv[1:], open({str(args)!r}, 'w'))\n")
    return script, args


def test_local_run_is_refused_when_no_model_server_answers(qtbot, make_host, make_state, recording_report):
    script, args = recording_report
    models = model_server.ModelServer(port=free_port())
    state = machine_state(qtbot, make_host, make_state, report_script=script, models=models)
    with qtbot.waitSignal(state.agent_message, timeout=1000) as sig:
        assert state.start_agent("local") is False
    assert sig.args[0] == f"No model server is running at {models.url}. {NO_SERVER_HINT}"
    assert sig.args[0] == state.last_agent_message()
    assert state.agent_running() is False
    assert not args.exists()  # report.py was never run
    for old in ("live", "claude"):
        with pytest.raises(ValueError):
            state.start_agent(old)


def test_local_run_is_refused_while_the_model_is_loading(qtbot, make_host, make_state, recording_report):
    with answering(status=503, body=b'{"error": {"message": "Loading model"}}') as port:
        models = model_server.ModelServer(port=port)
        state = machine_state(qtbot, make_host, make_state, report_script=recording_report[0], models=models)
        assert state.start_agent("local") is False
        assert state.last_agent_message() == (
            f"The model server at {models.url} is still loading its model. Try again in a moment.")


def test_local_run_names_the_backend_and_this_laptops_server(qtbot, make_host, make_state, recording_report,
                                                              monkeypatch):
    script, args = recording_report
    monkeypatch.setenv("EFFENTE_LLM_URL", "http://elsewhere.test:9")  # must not win
    with answering() as port:
        models = model_server.ModelServer(port=port)
        state = machine_state(qtbot, make_host, make_state, report_script=script, models=models)
        assert state.start_agent("local") is True
        qtbot.waitUntil(args.exists, timeout=10000)
        qtbot.waitUntil(lambda: not state.own_agent_running(), timeout=10000)
    given = json.loads(args.read_text())
    assert given[-4:] == ["--backend", "local", "--llm-url", f"http://127.0.0.1:{port}"]
    assert state.last_agent_message() == ""


def test_stand_in_run_needs_no_model_server(qtbot, make_host, make_state, recording_report):
    script, args = recording_report
    models = model_server.ModelServer(port=free_port())
    state = machine_state(qtbot, make_host, make_state, report_script=script, models=models)
    assert state.start_agent("standin") is True
    qtbot.waitUntil(args.exists, timeout=10000)
    qtbot.waitUntil(lambda: not state.own_agent_running(), timeout=10000)
    assert json.loads(args.read_text())[-2:] == ["--backend", "standin"]


def test_state_starts_the_model_server_and_stops_it_on_close(qtbot, make_host, make_state, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings

    binary, args = write_fake(tmp_path, monkeypatch)
    folder = tmp_path / "models"
    folder.mkdir()
    (folder / "large.gguf").write_bytes(b"not a real model")
    store = QSettings(str(tmp_path / "effente.ini"), QSettings.Format.IniFormat)
    models = model_server.ModelServer(binary=str(binary), port=free_port())
    state = machine_state(qtbot, make_host, make_state, settings=store, models=models)
    qtbot.waitUntil(lambda: state.model_status().state == model_server.STOPPED, timeout=5000)

    state.set_model_folder(folder)
    state.start_model_server()  # small.gguf is not there
    assert state.model_status() == model_server.Status(
        model_server.MODEL_MISSING, f"The model file {folder / 'small.gguf'} is missing.")
    with qtbot.waitSignal(state.model_changed, timeout=1000):
        state.set_model_size("large")
    assert (store.value("model_folder"), store.value("model_size")) == (str(folder), "large")

    state.start_model_server()
    assert state.model_status().state == model_server.LOADING
    qtbot.waitUntil(lambda: state.model_status().state == model_server.READY, timeout=10000)
    assert state.model_status() == model_server.Status(model_server.READY, "Running with large.gguf.", True, "large.gguf")
    assert json.loads(args.read_text())[:2] == ["-m", str(folder / "large.gguf")]

    child = models._child
    state.stop()  # what closing the app does
    assert child.poll() is not None
    assert model_server.probe(models.url)[0] == model_server.STOPPED


def test_stopping_the_model_server_shows_it_stopped(qtbot, make_host, make_state, tmp_path, monkeypatch):
    binary, _ = write_fake(tmp_path, monkeypatch)
    (tmp_path / "small.gguf").write_bytes(b"not a real model")
    models = model_server.ModelServer(binary=str(binary), port=free_port())
    state = machine_state(qtbot, make_host, make_state, models=models)
    state.set_model_folder(tmp_path)
    state.start_model_server()
    qtbot.waitUntil(lambda: state.model_status().state == model_server.READY, timeout=10000)
    state.stop_model_server()
    qtbot.waitUntil(lambda: state.model_status() == model_server.Status(model_server.STOPPED, "Not running."),
                    timeout=5000)


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


@pytest.fixture
def lingering_report(tmp_path):
    """A report.py that starts a child, as the real one starts device.agent, and
    waits for it. The child writes its pid and sleeps."""
    pid_file = tmp_path / "child.pid"
    script = tmp_path / "report.py"
    script.write_text(
        "import subprocess, sys\n"
        "child = (\"import os, pathlib, time; \"\n"
        f"         \"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid())); time.sleep(60)\")\n"
        "sys.exit(subprocess.call([sys.executable, '-c', child]))\n")
    return script, pid_file


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:  # a zombie is dead for this purpose
        with open(f"/proc/{pid}/stat") as f:
            return f.read().split(") ", 1)[1][0] != "Z"
    except OSError:
        return True


@pytest.mark.skipif(not hasattr(os, "killpg"), reason="process groups are Unix only")
@pytest.mark.parametrize("how", ["stop_agent", "stop"])
def test_stopping_ends_the_agent_report_py_started(qtbot, make_host, make_state, lingering_report, how):
    script, pid_file = lingering_report
    host = make_host()
    Client(host.port).json("POST", "/api/records", sample_records()[0])
    state = make_state(host, report_script=script)
    qtbot.waitUntil(lambda: state.current_machine() is not None, timeout=5000)
    assert state.start_agent("standin") is True
    qtbot.waitUntil(pid_file.exists, timeout=10000)
    qtbot.waitUntil(lambda: pid_file.read_text().strip() != "", timeout=5000)
    child = int(pid_file.read_text())
    assert state.own_agent_running() is True
    getattr(state, how)()
    qtbot.waitUntil(lambda: not _alive(child), timeout=5000)
    assert state.own_agent_running() is False
