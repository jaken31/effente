"""`state.log` (INTERFACES.md addition 19) on a DashboardState against a real Receiver."""

import csv

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from fake_partlog import CSV_COLUMNS
from receiver_testkit import MODEL, Client, make_record, make_run

MACHINE = MODEL["machine_id"]
BEFORE_THE_PART = "2020-01-01T00:00:00Z"


def later(n):
    return f"2099-01-01T00:00:{n:02d}Z"


def normal(**over):
    return make_record(**{"diagnosis": "normal", "wrong": False, "abnormal_minutes": 0, **over})


@pytest.fixture
def host(make_host):
    return make_host()


@pytest.fixture
def client(host):
    c = Client(host.port)
    assert c.json("POST", "/api/model", MODEL)[0] == 200
    assert c.json("POST", "/api/records", normal(at=BEFORE_THE_PART))[0] == 200  # so the machine is known
    return c


@pytest.fixture
def state(client, host, make_state):
    return make_state(host)


def made(qtbot, log, name="Drive roller", kind="roller", position=None):
    """Make a part through state.log and return it once it is there."""
    with qtbot.waitSignal(log.parts_changed, timeout=5000):
        log.create_part(name, kind, position)
    return next(p for p in log.parts() if p["name"] == name)


# ---- parts and the selection: what the Machine page waits on -----------------------


def test_a_part_is_made_changed_and_removed(qtbot, state):
    log = state.log
    assert log.parts() == [] and log.part("part-0001") is None
    part = made(qtbot, log, position={"scan_id": "scan-1", "at": [0.18, 0.06, 0.0]})
    assert (part["name"], part["kind"], part["created"]["by"]) == ("Drive roller", "roller", state.answerer())
    assert part["position"] == {"scan_id": "scan-1", "at": [0.18, 0.06, 0.0]} and part["state"]["name"] == "not_checked"
    assert "unbalance_like" in part["fault_types"] and log.part(part["part_id"]) == part

    with qtbot.waitSignal(log.parts_changed, timeout=5000):
        log.update_part(part["part_id"], position={"scan_id": "scan-1", "at": [0.2, 0.0, 0.1]}, name="Drive roller A")
    moved = log.part(part["part_id"])
    assert (moved["name"], moved["position"]["at"]) == ("Drive roller A", [0.2, 0.0, 0.1])
    with qtbot.waitSignal(log.parts_changed, timeout=5000):
        log.update_part(part["part_id"], removed=True)
    assert log.parts() == [] and log.part(part["part_id"])["removed"] is True


def test_parts_follow_the_stream_the_snapshot_and_the_machine(qtbot, state, client, host, make_state):
    log = state.log
    with qtbot.waitSignal(log.parts_changed, timeout=5000):  # made by someone else
        client.json("POST", f"/api/machines/{MACHINE}/parts", {"name": "Belt", "kind": "belt", "by": "Ana"})
    assert [p["name"] for p in log.parts()] == ["Belt"]
    made(qtbot, log)
    assert [p["name"] for p in log.parts()] == ["Belt", "Drive roller"]  # in the order they were made

    with qtbot.waitSignal(log.parts_changed, timeout=5000):  # a state moves: the probe reports
        client.json("POST", "/api/records", normal(at=later(1)))
    qtbot.waitUntil(lambda: [p["state"]["name"] for p in log.parts()] == ["nothing_found"] * 2, timeout=5000)

    client.json("POST", "/api/machines/press-01/parts", {"name": "Ram", "kind": "other", "by": "Ana"})
    second = make_state(host)  # an app started later reads them from the snapshot
    assert [p["name"] for p in second.log.parts()] == ["Belt", "Drive roller"]
    second.set_machine("press-01")
    assert [p["name"] for p in second.log.parts()] == ["Ram"]


def test_the_selection(qtbot, state):
    log = state.log
    part = made(qtbot, log)
    assert log.selected_part() is None
    with qtbot.waitSignal(log.selection_changed, timeout=1000):
        log.select_part(part["part_id"])
    assert log.selected_part() == part["part_id"]
    with qtbot.assertNotEmitted(log.selection_changed):
        log.select_part(part["part_id"])
    state.set_machine("press-01")
    assert log.selected_part() is None  # a part of another machine is not selected
    state.set_machine(MACHINE)
    assert log.selected_part() == part["part_id"]
    with qtbot.waitSignal(log.selection_changed, timeout=1000):
        log.select_part(None)
    assert log.selected_part() is None


def test_a_refused_action_says_why(qtbot, state, host, make_host, make_state):
    with qtbot.waitSignal(state.action_failed, timeout=5000) as said:
        state.log.create_part("Belt", "strap")
    assert said.args[0].startswith("Part not added: part 'kind' must be one of motor, bearing")
    with qtbot.waitSignal(state.action_failed, timeout=5000) as said:
        state.log.update_part("part-9999", note="x")
    assert said.args == ["Part not changed: no such part: part-9999"]
    with qtbot.waitSignal(state.action_failed, timeout=5000) as said:
        state.log.add_entry("repair", "")
    assert said.args[0].startswith("Entry not added: entry is missing required field 'text'")
    with qtbot.waitSignal(state.action_failed, timeout=5000) as said:
        state.log.answer_alarm_for_part("alarm-9999", "real", "part-0001")
    assert said.args == ["Answer not saved: no such alarm: alarm-9999"]
    with qtbot.waitSignal(state.action_failed, timeout=5000) as said:
        state.log.start_review("part-0001")
    assert said.args == ["The part review is not built yet."]

    empty = make_state(make_host(port=0, data_dir=host.data_dir.parent / "empty"))  # no machine yet
    for action in (lambda: empty.log.create_part("Belt", "belt"), lambda: empty.log.add_entry("note", "x"),
                   lambda: empty.log.export_csv("nowhere.csv")):
        with qtbot.waitSignal(empty.action_failed, timeout=1000) as said:
            action()
        assert said.args[0].endswith("no machine is shown.")


# ---- entries, alarms and the export ---------------------------------------------


def test_entries_are_added_fetched_and_filtered(qtbot, state, client, host, make_state):
    log = state.log
    part = made(qtbot, log)
    client.json("POST", f"/api/machines/{MACHINE}/log", {"kind": "note", "by": "Ana", "text": "machine arrived", "at": later(1)})
    qtbot.waitUntil(lambda: len(log.entries()) == 1, timeout=5000)

    with qtbot.waitSignal(log.entries_changed, timeout=5000):
        log.add_entry("finding", "crack in the shell", part["part_id"], needs_repair=True)
    qtbot.waitUntil(lambda: log.part(part["part_id"])["state"]["name"] == "needs_repair", timeout=5000)
    with qtbot.waitSignal(log.entries_changed, timeout=5000):
        log.add_entry("repair", "shell welded", part["part_id"])
    qtbot.waitUntil(lambda: log.part(part["part_id"])["state"]["name"] != "needs_repair", timeout=5000)

    assert [e["kind"] for e in log.entries()] == ["note", "repair", "finding"]  # newest `at` first: the note is from 2099
    assert [e["kind"] for e in log.entries(part_id=part["part_id"])] == ["repair", "finding"]
    assert [e["text"] for e in log.entries(kind="finding")] == ["crack in the shell"]
    assert log.entries(part_id=part["part_id"], kind="note") == []
    assert {e["by"] for e in log.entries(part_id=part["part_id"])} == {state.answerer()}

    second = make_state(host)  # an app started later: the first read fetches the log
    assert second.log.entries() == []
    qtbot.waitUntil(lambda: len(second.log.entries()) == 3, timeout=5000)
    assert second.log.entries() == log.entries()
    second.set_machine("press-01")
    assert second.log.entries() == []


def test_an_alarm_is_answered_for_a_part(qtbot, state, client):
    log = state.log
    roller, belt = made(qtbot, log), made(qtbot, log, "Belt", "belt")
    client.json("POST", "/api/records", make_record(at=later(2), abnormal_minutes=1))
    qtbot.waitUntil(lambda: len(log.waiting_alarms()) == 1, timeout=5000)
    alarm = log.waiting_alarms()[0]
    assert [p["name"] for p in log.suspects(alarm)] == ["Drive roller"] and log.suspects(None) == []
    qtbot.waitUntil(lambda: log.part(roller["part_id"])["state"]["name"] == "possible", timeout=5000)
    assert log.part(belt["part_id"])["state"]["name"] != "possible"

    with qtbot.waitSignal(log.entries_changed, timeout=5000):
        log.answer_alarm_for_part(alarm["alarm_id"], "real", roller["part_id"], finding="added weight found")
    qtbot.waitUntil(lambda: log.waiting_alarms() == [], timeout=5000)
    entry = log.entries(part_id=roller["part_id"])[0]
    assert (entry["kind"], entry["text"], entry["source"], entry["needs_repair"], entry["alarm_id"], entry["diagnosis"]) == (
        "finding", "added weight found", "alarm_answer", True, alarm["alarm_id"], "unbalance_like")
    qtbot.waitUntil(lambda: log.part(roller["part_id"])["state"]["name"] == "needs_repair", timeout=5000)
    assert state.alarms()[0]["answer"]["verdict"] == "real"

    with qtbot.waitSignal(log.entries_changed, timeout=5000):
        log.answer_alarm_for_part(alarm["alarm_id"], "false", roller["part_id"], needs_repair=True)
    qtbot.waitUntil(lambda: log.part(roller["part_id"])["state"]["name"] == "possible", timeout=5000)
    assert log.entries(part_id=roller["part_id"])[0]["needs_repair"] is False


def test_the_log_is_saved_as_the_receivers_csv(qtbot, state, tmp_path):
    log = state.log
    part = made(qtbot, log, name='Rodillo "A", norte')
    with qtbot.waitSignal(log.entries_changed, timeout=5000):
        log.add_entry("repair", "soldado, día 3", part["part_id"])
    out = tmp_path / "log.csv"
    log.export_csv(out)
    qtbot.waitUntil(lambda: out.exists() and out.stat().st_size > 0, timeout=5000)
    with out.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    assert tuple(rows[0]) == CSV_COLUMNS and len(rows) == 2
    assert rows[1][1:5] == ['Rodillo "A", norte', "repair", "soldado, día 3", state.answerer()]
    with qtbot.waitSignal(state.action_failed, timeout=5000) as said:
        log.export_csv(tmp_path / "no-such-folder" / "log.csv")
    assert said.args[0].startswith("Log not exported: ")


# ---- part review runs: read only, the agent is plan item J4 ----------------------


def test_part_review_runs_are_read(qtbot, state, client):
    log = state.log
    part = made(qtbot, log)
    assert log.review(part["part_id"]) is None and log.review_running(part["part_id"]) is False
    run = make_run("review-20261114-193000-" + part["part_id"], agent="part_review", part_id=part["part_id"], review=None)
    with qtbot.waitSignal(log.reviews_changed, timeout=5000):
        client.json("PUT", f"/api/agent/runs/{run['run_id']}", run)
    assert log.review(part["part_id"])["run_id"] == run["run_id"] and log.review_running(part["part_id"]) is True
    with qtbot.waitSignal(log.reviews_changed, timeout=5000):
        client.json("PUT", f"/api/agent/runs/{run['run_id']}", dict(run, status="done", ok=True))
    assert log.review_running(part["part_id"]) is False and log.review("part-9999") is None
    with qtbot.assertNotEmitted(log.reviews_changed, wait=300):  # a report run is not a review
        client.json("PUT", "/api/agent/runs/run-1", make_run("run-1", agent="maintenance_report"))
