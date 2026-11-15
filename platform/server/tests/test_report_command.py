"""The command platform/report.py: the report agent runs on what the receiver has stored.

Each test runs the real command against a real receiver, with the stand-in writing the
report (no model, no network). The records are the simulator's sample, re-labelled as a
board's where a test says so; none came from a board.
"""

import importlib.util
import json
import os

import pytest

PLATFORM = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SIM_DIR = os.path.join(PLATFORM, "sim")

BOARD, MACHINE = "effente-01", "conveyor-07"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sim_model = load("report_test_sim_model", os.path.join(SIM_DIR, "sim_model.py"))


def sample(machine_id=None, device_id=None):
    """The simulator's sample session; with a machine, as if a board on that machine had sent it."""
    with open(os.path.join(SIM_DIR, "sample_replay.jsonl"), encoding="utf-8") as lines:
        records = [json.loads(line) for line in lines if line.strip()]
    for record in records:
        if machine_id:
            record.update(machine_id=machine_id, device_id=device_id, simulated=False)
    return records


def announcement(machine_id=MACHINE, device_id=BOARD):
    return dict(sim_model.model_announcement(device_id, machine_id), simulated=False)


@pytest.fixture
def report(tmp_path, monkeypatch):
    """platform/report.py with its data folder in a temp place, empty: no simulator session files."""
    command = load("report_command", os.path.join(PLATFORM, "report.py"))
    monkeypatch.setattr(command, "DATA", tmp_path / "report-data")
    return command


def run(report, capfd, port, *args):
    code = report.main(["--port", str(port), "--backend", "standin", *args])
    out, err = capfd.readouterr()
    return code, (json.loads(out) if out.strip() else None), err


def test_a_boards_stored_records_are_reported_on_with_no_session_files(make_receiver, report, capfd):
    receiver, client = make_receiver()
    client.json("POST", "/api/model", announcement())
    client.json("POST", "/api/records", sample(MACHINE, BOARD))

    code, result, err = run(report, capfd, receiver.port)  # no --machine: the only one stored

    assert code == 0, err
    assert result["ok"] is True and result["report"]["summary"].startswith("Stand-in report:")
    _, runs = client.json("GET", f"/api/machines/{MACHINE}/agent-runs")
    stored = runs["runs"][0]
    assert stored["machine_id"] == MACHINE and stored["device_id"] == BOARD
    assert stored["ok"] is True and stored["model_version"] == sim_model.MODEL_VERSION
    readings = json.loads(next(c["output"] for c in stored["tool_calls"] if c["name"] == "get_readings"))
    assert readings["windows"] == len(sample())
    # Every reading it cites moved in the stored records.
    moved = {name for r in sample() for name, v in (r.get("features") or {}).items() if abs(v) >= 6}
    assert set(result["report"]["cited_features"]) <= moved
    assert (report.DATA / "agent.jsonl").exists()


def test_the_device_links_announcement_with_no_fault_types_is_enough(make_receiver, report, capfd):
    """platform/link announces the feature table but no fault types (the device's port has none)."""
    receiver, client = make_receiver()
    said = announcement()
    client.json("POST", "/api/model", {**{k: said[k] for k in ("device_id", "machine_id", "model_version",
                                                                "features_version", "features", "simulated")},
                                        "fault_types": []})
    client.json("POST", "/api/records", sample(MACHINE, BOARD))

    code, result, err = run(report, capfd, receiver.port, "--machine", MACHINE)

    assert code == 0, err
    assert result["ok"] is True and result["simulated"] is True  # the stand-in wrote it, not a model


def test_records_without_an_announced_model_give_no_report_and_say_why(make_receiver, report, capfd):
    receiver, client = make_receiver()
    client.json("POST", "/api/records", sample(MACHINE, BOARD))

    code, result, err = run(report, capfd, receiver.port, "--machine", MACHINE)

    assert code == 1 and result is None
    assert err.strip() == (f"The receiver has {len(sample())} records for {MACHINE} but no feature table for model "
                           "sim-3: the device has not announced that model. No report was written.")
    assert client.json("GET", f"/api/machines/{MACHINE}/agent-runs")[1]["runs"] == []


def test_several_machines_and_none_named_is_refused_with_their_names(make_receiver, report, capfd):
    receiver, client = make_receiver()
    for machine, device in ((MACHINE, BOARD), ("press-02", "effente-02")):
        client.json("POST", "/api/model", announcement(machine, device))
        client.json("POST", "/api/records", sample(machine, device)[0])

    code, _, err = run(report, capfd, receiver.port)

    assert code == 1
    assert err.strip() == ("No report was written: the receiver knows several machines; "
                           f"name one with --machine: {MACHINE}, press-02.")


def test_the_machine_a_connected_device_is_on_is_picked_among_several(make_receiver, report):
    state = {"machines": ["a", "b"], "devices": {"d1": {"connected": True, "machine_id": "b"},
                                                 "d2": {"connected": False, "machine_id": "a"}}}
    assert report.pick_machine(state) == "b"
    assert report.pick_machine({"machines": ["only"], "devices": {}}) == "only"
    with pytest.raises(report.Unavailable, match="knows no machine yet"):
        report.pick_machine({"machines": [], "devices": {}})


def test_nothing_stored_and_no_session_files_says_there_is_no_log(make_receiver, report, capfd):
    receiver, _ = make_receiver()
    code, _, err = run(report, capfd, receiver.port, "--machine", MACHINE)
    assert code == 1 and err.strip() == report.NO_READINGS


def test_the_simulators_session_files_are_still_read_when_the_receiver_has_no_model(make_receiver, report, capfd):
    receiver, client = make_receiver()
    client.json("POST", "/api/records", sample())  # records but no announcement, as before this change
    report.DATA.mkdir()
    (report.DATA / "device.jsonl").write_text("".join(json.dumps(r) + "\n" for r in sample()))
    (report.DATA / "model.json").write_text(json.dumps(sim_model.model_file()))

    code, result, err = run(report, capfd, receiver.port, "--machine", "demo-fan-1")

    assert code == 0, err
    assert result["ok"] is True and result["simulated"] is True


def test_a_real_machine_is_never_reported_on_from_the_simulators_session_files(make_receiver, report, capfd):
    """Left over from an earlier simulated run: they hold demo-fan-1's readings, not this machine's."""
    receiver, client = make_receiver()
    client.json("POST", "/api/records", sample(MACHINE, BOARD))  # no announcement
    report.DATA.mkdir()
    (report.DATA / "device.jsonl").write_text("".join(json.dumps(r) + "\n" for r in sample()))
    (report.DATA / "model.json").write_text(json.dumps(sim_model.model_file()))

    code, result, err = run(report, capfd, receiver.port, "--machine", MACHINE)

    assert code == 1 and result is None and "no feature table" in err
    assert client.json("GET", f"/api/machines/{MACHINE}/agent-runs")[1]["runs"] == []


def test_a_long_history_is_read_page_by_page(make_receiver, report, monkeypatch):
    receiver, client = make_receiver()
    records = sample(MACHINE, BOARD)
    client.json("POST", "/api/records", records)
    monkeypatch.setattr(report, "PAGE", 3)

    got = report.stored_records(f"http://localhost:{receiver.port}", MACHINE)

    assert [r["at"] for r in got] == [r["at"] for r in records]
    assert "spectrum" not in got[0]


def test_no_receiver_and_no_session_files_says_there_is_no_log(report, capfd):
    code, _, err = run(report, capfd, 1)  # nothing listens on port 1
    assert code == 1 and err.strip() == report.NO_READINGS
