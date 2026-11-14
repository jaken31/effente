"""The command, platform/part_review.py."""

import importlib.util
import json
from datetime import datetime, timedelta, timezone

import pytest
from part_review_cases import HISTORIES, MACHINE, PLATFORM, load_case
from fake_part_receiver import FakeReceiver

spec = importlib.util.spec_from_file_location("part_review_command", PLATFORM / "part_review.py")
command = importlib.util.module_from_spec(spec)
spec.loader.exec_module(command)


def run(capsys, *args):
    code = command.main(list(args))
    out, err = capsys.readouterr()
    return code, (json.loads(out) if out.strip() else None), err


def test_one_run_per_part_for_every_part_of_the_machine(capsys, tmp_path):
    code, results, err = run(capsys, "--machine", MACHINE, "--history", str(HISTORIES / "confirmed_waiting.json"),
                             "--agent-log", str(tmp_path / "log.jsonl"))

    assert (code, err) == (0, "")
    assert [(r["part_id"], r["review"]["needs_repair"]) for r in results] == [
        ("part-0007", "yes"), ("part-0003", "no"), ("part-0002", "not_sure")]
    assert len({r["run_id"] for r in results}) == 3
    assert len((tmp_path / "log.jsonl").read_text().splitlines()) == 3


def test_one_part_when_asked(capsys, tmp_path):
    code, results, _ = run(capsys, "--machine", MACHINE, "--part", "part-0002", "--agent-log", str(tmp_path / "l"),
                           "--history", str(HISTORIES / "confirmed_waiting.json"))

    assert code == 0 and [r["part_id"] for r in results] == ["part-0002"]
    assert results[0]["review"]["cited_alarms"] == ["alarm-2301"]


def test_a_removed_part_is_not_reviewed(capsys, tmp_path):
    data = json.loads((HISTORIES / "confirmed_waiting.json").read_text())
    data["parts"][1]["removed"] = True
    history = tmp_path / "history.json"
    history.write_text(json.dumps(data))

    _, results, _ = run(capsys, "--machine", MACHINE, "--history", str(history), "--agent-log", str(tmp_path / "l"))

    assert [r["part_id"] for r in results] == ["part-0007", "part-0002"]


def test_a_part_that_does_not_exist_exits_1_with_the_reason(capsys, tmp_path):
    code, results, err = run(capsys, "--machine", MACHINE, "--part", "part-9999", "--agent-log", str(tmp_path / "l"),
                             "--history", str(HISTORIES / "repaired.json"))

    assert code == 1 and results[0]["ok"] is False
    assert "part-9999: no review was written: no history: conveyor-07 has no part part-9999." in err


def test_no_receiver_exits_1_and_says_so(capsys, tmp_path):
    code, results, err = run(capsys, "--machine", MACHINE, "--port", "9", "--agent-log", str(tmp_path / "l"))

    assert (code, results) == (1, None)
    assert err == "No review was written: no receiver is running at http://localhost:9.\n"


def test_the_stand_in_is_the_default_backend(capsys, tmp_path):
    _, results, _ = run(capsys, "--machine", MACHINE, "--part", "part-0007", "--agent-log", str(tmp_path / "l"),
                        "--history", str(HISTORIES / "repaired.json"))

    assert results[0]["simulated"] is True and results[0]["review"]["what"].startswith("Stand-in review:")


def test_against_a_receiver_every_part_is_posted_and_logged(capsys, tmp_path):
    data, _ = load_case("confirmed_waiting")
    with FakeReceiver(data) as receiver:
        code, results, _ = run(capsys, "--machine", MACHINE, "--port", str(receiver.port),
                               "--agent-log", str(tmp_path / "l"))

    assert code == 0
    assert [entry["part_id"] for entry in receiver.posted] == ["part-0007", "part-0003", "part-0002"]
    done = [r for r in receiver.runs if r["status"] == "done"]
    assert [r["part_id"] for r in done] == ["part-0007", "part-0003", "part-0002"]
    assert [r["entry_id"] for r in results] == [entry["entry_id"] for entry in receiver.posted]


def running(part_id, age=timedelta(seconds=30)):
    at = (datetime.now(timezone.utc) - age).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"run_id": f"review-earlier-{part_id}", "agent": "part_review", "machine_id": MACHINE,
            "part_id": part_id, "at": at, "status": "running"}


def test_a_part_whose_review_is_still_running_gets_no_second_run(capsys, tmp_path):
    data, _ = load_case("confirmed_waiting")
    with FakeReceiver(data) as receiver:
        receiver.runs.append(running("part-0007"))
        code, results, err = run(capsys, "--machine", MACHINE, "--port", str(receiver.port),
                                 "--agent-log", str(tmp_path / "l"))

    assert code == 1
    assert results[0]["reason"] == "a review of this part is already running: review-earlier-part-0007"
    assert [r["ok"] for r in results] == [False, True, True]  # the other parts are still reviewed
    assert "part-0007" not in [r["part_id"] for r in receiver.runs[1:]]


@pytest.mark.parametrize("earlier", [
    running("part-0007", age=timedelta(minutes=11)),  # died: no run lasts that long
    {**running("part-0007"), "status": "done"},
    {**running("part-0007"), "agent": "maintenance_report"},
    running("part-0002"),  # another part's
])
def test_only_a_fresh_running_review_of_the_same_part_blocks(capsys, tmp_path, earlier):
    data, _ = load_case("confirmed_waiting")
    with FakeReceiver(data) as receiver:
        receiver.runs.append(earlier)
        code, results, _ = run(capsys, "--machine", MACHINE, "--part", "part-0007", "--port", str(receiver.port),
                               "--agent-log", str(tmp_path / "l"))

    assert code == 0 and results[0]["ok"] is True
