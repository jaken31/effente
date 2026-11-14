"""Parts, log entries and part states (platform/CONTRACT.md sections 8.5 to 8.7), over HTTP.

Times: a part is made at the receiver's own clock, so records "newer than the
part" carry a time in 2099 (`later(n)`, n seconds in) and records older than it
a time in 2020.
"""

import csv
import io
import json

import part_kinds
import pytest
from receiver_testkit import make_record

M = "conveyor-07"
BEFORE_THE_PART = "2020-01-01T00:00:00Z"
NOTHING_FOUND_REASON = "the probe has been on the machine and nothing points at this part"


def later(n):
    return f"2099-01-01T00:{n // 60:02d}:{n % 60:02d}Z"


def ok(client, method, path, body=None):
    status, reply = client.json(method, path, body)
    assert status == 200, reply
    return reply


def new_part(client, machine=M, **over):
    body = {"name": "Drive roller", "kind": "roller", "by": "Sam"}
    body.update(over)
    return ok(client, "POST", f"/api/machines/{machine}/parts", body)["part"]


def state_of(client, part, machine=M):
    parts = ok(client, "GET", f"/api/machines/{machine}/parts?removed=1")["parts"]
    return next(p for p in parts if p["part_id"] == part["part_id"])["state"]


def name_of(client, part):
    return state_of(client, part)["name"]


def add(client, kind, part=None, machine=M, **over):
    body = {"kind": kind, "by": "Sam", "text": f"a {kind}", "part_id": part["part_id"] if part else None}
    body.update(over)
    return ok(client, "POST", f"/api/machines/{machine}/log", body)


def normal(client, n, **over):
    return ok(client, "POST", "/api/records", make_record(at=later(n), diagnosis="normal", abnormal_minutes=0, **over))


def abnormal(client, n, minutes=1, **over):
    return ok(client, "POST", "/api/records", make_record(at=later(n), abnormal_minutes=minutes, **over))


def newest_alarm(client):
    return ok(client, "GET", f"/api/machines/{M}/alarms")["alarms"][0]


def answer(client, alarm_id, **body):
    return client.json("POST", f"/api/alarms/{alarm_id}/answers", {"verdict": "real", "by": "Sam", **body})


# ---- the kind table ------------------------------------------------------------


def test_the_kind_table_holds_only_names_from_the_fault_types_module():
    from effente.fault_types import NAMES

    assert part_kinds.PROBLEM == ""  # every starting value is a name the module has
    assert part_kinds.KINDS == ("motor", "bearing", "roller", "belt", "gearbox", "coupling", "fan", "pump", "mount",
                                "frame", "probe_point", "other")
    assert set(part_kinds.FAULT_TYPES_BY_KIND) == set(part_kinds.KINDS)
    for names in part_kinds.FAULT_TYPES_BY_KIND.values():
        assert set(names) <= set(NAMES) and len(set(names)) == len(names)
    assert part_kinds.fault_types_for("other") == [] and "unbalance_like" in part_kinds.fault_types_for("roller")


# ---- parts (8.5) ---------------------------------------------------------------


def test_a_part_is_made_with_the_contracts_fields(client):
    part = new_part(client, position={"scan_id": "scan-1", "at": [0.18, 0.06, 0]}, note="left side", colour="grey", state={"name": "needs_repair"})
    assert part["part_id"] == "part-0001" and part["machine_id"] == M
    assert (part["name"], part["kind"], part["note"], part["removed"]) == ("Drive roller", "roller", "left side", False)
    assert part["fault_types"] == part_kinds.fault_types_for("roller")  # left out: the kind's starting values
    assert part["position"] == {"scan_id": "scan-1", "at": [0.18, 0.06, 0]}
    assert part["created"]["by"] == "Sam" and part["created"]["at"].endswith("Z")
    assert part["colour"] == "grey"  # an unknown field is stored and passed on
    assert part["state"] == {"name": "not_checked", "since": None, "alarm_id": None, "entry_id": None,
                             "reason": "the probe has not been on the machine since the part was made"}
    listed = new_part(client, name="Belt", kind="belt", fault_types=["speed_change", "speed_change", "unrecognised_pattern"])
    assert listed["part_id"] == "part-0002" and listed["fault_types"] == ["speed_change", "unrecognised_pattern"]
    assert listed["position"] is None and listed["note"] == ""
    assert new_part(client, name="Guard", kind="other", fault_types=[])["fault_types"] == []


def test_a_part_is_changed_removed_and_listed(client):
    roller, belt = new_part(client), new_part(client, name="Belt", kind="belt")
    put = ok(client, "PUT", f"/api/parts/{roller['part_id']}", {"by": "Ana", "name": "Drive roller A", "kind": "bearing",
             "position": {"scan_id": "scan-2", "at": [1, 2, 3]}, "fault_types": ["impacts"], "note": "moved"})["part"]
    assert (put["name"], put["kind"], put["fault_types"], put["note"]) == ("Drive roller A", "bearing", ["impacts"], "moved")
    assert put["position"]["scan_id"] == "scan-2" and put["created"] == roller["created"] and put["changed"]["by"] == "Ana"
    assert ok(client, "PUT", f"/api/parts/{roller['part_id']}", {"by": "Ana", "position": None})["part"]["position"] is None

    listed = ok(client, "GET", f"/api/machines/{M}/parts")["parts"]
    assert [p["name"] for p in listed] == ["Drive roller A", "Belt"]  # in the order they were made
    ok(client, "PUT", f"/api/parts/{roller['part_id']}", {"by": "Ana", "removed": True})
    assert [p["name"] for p in ok(client, "GET", f"/api/machines/{M}/parts")["parts"]] == ["Belt"]
    assert [p["removed"] for p in ok(client, "GET", f"/api/machines/{M}/parts?removed=1")["parts"]] == [True, False]
    assert ok(client, "GET", "/api/machines/press-01/parts")["parts"] == []

    state = ok(client, "GET", "/api/state")
    assert [p["part_id"] for p in state["parts"][M]] == [belt["part_id"]] and state["parts"][M][0]["state"]["name"] == "not_checked"
    assert state["machines"] == [M]  # a machine with a part, though no probe has reported on it
    ok(client, "PUT", f"/api/parts/{belt['part_id']}", {"by": "Ana", "removed": True})
    state = ok(client, "GET", "/api/state")
    assert state["parts"] == {} and state["machines"] == []


def test_part_events_and_no_event_for_a_change_that_changes_nothing(make_receiver, open_stream):
    receiver, client = make_receiver()
    stream = open_stream(receiver.port)
    part = new_part(client)
    assert stream.next("part") == part
    same = ok(client, "PUT", f"/api/parts/{part['part_id']}", {"by": "Ana", "name": "Drive roller"})["part"]
    assert same == part  # nothing differs: nothing stored, nothing sent
    moved = ok(client, "PUT", f"/api/parts/{part['part_id']}", {"by": "Ana", "note": "x"})["part"]
    assert stream.next("part") == moved


# ---- the state table (8.7), every row in both directions -------------------------


def test_not_checked_and_nothing_found_both_ways(client):
    part = new_part(client)
    assert state_of(client, part)["name"] == "not_checked" and state_of(client, part)["since"] is None
    ok(client, "POST", "/api/records", make_record(at=BEFORE_THE_PART, diagnosis="normal"))
    assert name_of(client, part) == "not_checked"  # the probe was there before the part was made: does not count

    normal(client, 1)
    assert state_of(client, part) == {"name": "nothing_found", "since": later(1), "reason": NOTHING_FOUND_REASON,
                                      "alarm_id": None, "entry_id": None}
    normal(client, 2)
    assert state_of(client, part)["since"] == later(1)  # the oldest such record

    add(client, "repair", part, at=later(5))
    assert state_of(client, part) == {"name": "not_checked", "since": None, "alarm_id": None, "entry_id": None,
                                      "reason": "the probe has not been on the machine since the repair"}
    normal(client, 5)
    assert name_of(client, part) == "not_checked"  # at the repair's own time is not newer than it
    normal(client, 8)
    assert state_of(client, part)["since"] == later(8)
    normal(client, 7)  # a record that arrives late
    assert state_of(client, part)["since"] == later(7)


def test_nothing_found_and_possible_both_ways(client):
    roller = new_part(client)  # a roller's starting values hold unbalance_like
    belt = new_part(client, name="Belt", kind="belt")  # a belt's do not
    normal(client, 1)
    abnormal(client, 2)
    alarm_id = newest_alarm(client)["alarm_id"]
    assert state_of(client, roller) == {"name": "possible", "since": later(2), "reason": "alarm open: unbalance_like",
                                        "alarm_id": alarm_id, "entry_id": None}
    assert name_of(client, belt) == "nothing_found"

    normal(client, 3)  # closed, and nobody has answered
    assert state_of(client, roller)["name"] == "possible"
    assert state_of(client, roller)["reason"] == "alarm not answered: unbalance_like"
    assert answer(client, alarm_id)[0] == 200  # closed and answered, naming no part
    assert state_of(client, roller) == {"name": "nothing_found", "since": later(1), "reason": NOTHING_FOUND_REASON,
                                        "alarm_id": None, "entry_id": None}

    abnormal(client, 4)
    second = newest_alarm(client)["alarm_id"]
    assert answer(client, second, verdict="false")[0] == 200
    assert state_of(client, roller)["name"] == "possible" and state_of(client, roller)["alarm_id"] == second  # still open
    normal(client, 5)
    assert name_of(client, roller) == "nothing_found"

    abnormal(client, 6)
    normal(client, 7)
    abnormal(client, 8)
    assert state_of(client, roller)["since"] == later(6)  # the start of the oldest such alarm
    ok(client, "PUT", f"/api/parts/{roller['part_id']}", {"by": "Sam", "fault_types": ["impacts"]})
    assert name_of(client, roller) == "nothing_found"  # the list no longer holds the alarm's diagnosis
    ok(client, "PUT", f"/api/parts/{belt['part_id']}", {"by": "Sam", "fault_types": ["unbalance_like"]})
    assert name_of(client, belt) == "possible"


def test_possible_and_needs_repair_both_ways(client):
    part = new_part(client)
    abnormal(client, 2)
    assert name_of(client, part) == "possible"
    found = add(client, "finding", part, needs_repair=True, at=later(3))
    assert found["part"]["state"] == {"name": "needs_repair", "since": later(3), "reason": "finding: needs repair",
                                      "alarm_id": None, "entry_id": found["entry"]["entry_id"]}
    assert state_of(client, part) == found["part"]["state"]
    cleared = add(client, "inspection", part, needs_repair=False, at=later(4))
    assert cleared["part"]["state"]["name"] == "possible"  # the alarm is still open


def test_needs_repair_follows_the_newest_finding_or_inspection(client):
    part = new_part(client)
    looked = add(client, "inspection", part, needs_repair=True, at=later(10), text="")
    assert state_of(client, part)["reason"] == "inspection: needs repair" and state_of(client, part)["since"] == later(10)
    add(client, "finding", part, needs_repair=False, at=later(9))  # added later, but older
    assert state_of(client, part)["entry_id"] == looked["entry"]["entry_id"]
    add(client, "finding", part, needs_repair=False, at=later(10))  # same time: the one added later is newer
    assert name_of(client, part) == "not_checked"
    add(client, "finding", part, needs_repair=True, at=later(11))
    assert name_of(client, part) == "needs_repair"
    other = new_part(client, name="Idle roller")
    assert name_of(client, other) == "not_checked"  # another part's entries do not count


def test_a_repair_clears_needs_repair_and_possible(client):
    part = new_part(client)
    abnormal(client, 2)
    add(client, "finding", part, needs_repair=True, at=later(3))
    add(client, "repair", part, at=later(1))  # older than the finding: clears nothing
    assert name_of(client, part) == "needs_repair"
    add(client, "repair", part, at=later(4))
    assert name_of(client, part) == "not_checked"  # the alarm started before the repair, and no record since
    abnormal(client, 5, minutes=2)  # the same alarm goes on
    assert state_of(client, part)["name"] == "nothing_found" and state_of(client, part)["since"] == later(5)
    normal(client, 6)
    abnormal(client, 7)  # a new alarm, started after the repair
    assert state_of(client, part)["name"] == "possible" and state_of(client, part)["since"] == later(7)
    add(client, "repair", part, at=later(8))
    assert name_of(client, part) == "not_checked"
    add(client, "finding", part, needs_repair=True, at=later(9))  # and back
    assert name_of(client, part) == "needs_repair"


def test_a_note_and_a_review_never_change_the_state(client):
    part = new_part(client)
    add(client, "finding", part, needs_repair=True, at=later(1))
    before = state_of(client, part)
    add(client, "note", part, at=later(2))
    review = add(client, "review", part, at=later(3), by="part review agent", run_id="review-1", simulated=True)
    assert state_of(client, part) == before and review["part"]["state"] == before
    assert (review["entry"]["source"], review["entry"]["run_id"], review["entry"]["simulated"]) == ("agent", "review-1", True)


def test_a_part_event_only_when_a_state_changes(make_receiver, open_stream):
    receiver, client = make_receiver()
    part = new_part(client)
    stream = open_stream(receiver.port)
    for n in range(1, 6):
        normal(client, n)  # five records, one change: not_checked to nothing_found
    abnormal(client, 6)
    abnormal(client, 7, minutes=2)  # the alarm changes, the part's state does not
    add(client, "note", part)
    seen = []
    while not seen or seen[-1][0] != "log_entry":
        seen.append(stream.next_of(("part", "log_entry")))
    assert [(name, data.get("state", {}).get("name")) for name, data in seen] == [
        ("part", "nothing_found"), ("part", "possible"), ("log_entry", None)]
    assert seen[0][1]["part_id"] == part["part_id"] and seen[2][1]["kind"] == "note"


# ---- entries (8.6) -------------------------------------------------------------


def test_an_entry_has_the_contracts_fields(client):
    part = new_part(client)
    abnormal(client, 2, simulated=True)
    alarm_id = newest_alarm(client)["alarm_id"]
    reply = add(client, "finding", part, text="added weight", needs_repair=True, at=later(3), alarm_id=alarm_id, shift="night")
    entry = reply["entry"]
    assert entry.pop("received_at").endswith("Z")
    assert entry == {"entry_id": "entry-000001", "machine_id": M, "part_id": part["part_id"], "at": later(3),
                     "kind": "finding", "text": "added weight", "by": "Sam", "needs_repair": True, "alarm_id": alarm_id,
                     "diagnosis": "unbalance_like", "source": "person", "run_id": None, "simulated": True, "shift": "night"}
    whole = add(client, "note", text="machine arrived")  # no part, no time, no alarm
    assert whole["part"] is None and whole["entry"]["part_id"] is None and whole["entry"]["needs_repair"] is None
    assert whole["entry"]["at"] == whole["entry"]["received_at"] and whole["entry"]["simulated"] is False
    assert add(client, "repair", source="import", text="from the old sheet")["entry"]["source"] == "import"
    assert add(client, "repair", source="alarm_answer", text="x")["entry"]["source"] == "person"  # the platform's to set
    assert add(client, "note", machine="press-01", text="no parts here yet")["entry"]["machine_id"] == "press-01"


def test_the_log_is_read_newest_first_with_filters(client):
    roller, belt = new_part(client), new_part(client, name="Belt", kind="belt")
    add(client, "note", at=later(1), text="one")
    add(client, "repair", belt, at=later(3), text="three")
    add(client, "finding", roller, at=later(2), text="two", needs_repair=False)
    add(client, "repair", roller, at=later(4), text="four")
    add(client, "note", machine="press-01", text="elsewhere")

    def texts(query=""):
        return [e["text"] for e in ok(client, "GET", f"/api/machines/{M}/log{query}")["entries"]]

    assert texts() == ["four", "three", "two", "one"]
    assert texts(f"?part={roller['part_id']}") == ["four", "two"]
    assert texts("?kind=repair") == ["four", "three"]
    assert texts(f"?since={later(3)}") == ["four", "three"]
    assert texts(f"?part={roller['part_id']}&kind=repair&since={later(1)}&limit=5") == ["four"]
    assert texts("?limit=2") == ["four", "three"] and texts("?limit=0") == []
    assert texts("?part=part-9999") == []
    assert ok(client, "GET", "/api/machines/nowhere/log")["entries"] == []


def test_the_csv_opens_in_a_spreadsheet(client):
    part = new_part(client, name='Rodillo "A", lado norte')
    add(client, "finding", part, at=later(2), text='grieta de 3,5 mm, dijo "urgente"\nsegunda línea', by="José Núñez", needs_repair=True)
    add(client, "note", at=later(1), text="=1+1")
    add(client, "repair", part, at=later(3), text="soldado")
    status, headers, raw = client.request("GET", f"/api/machines/{M}/log.csv")
    assert status == 200 and headers["Content-Type"] == "text/csv; charset=utf-8"
    assert raw.startswith(b"\xef\xbb\xbf") and b"\r\n" in raw  # the mark a spreadsheet needs to read the accents
    rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"), newline="")))
    assert rows[0] == ["at", "part", "kind", "text", "by", "needs_repair", "alarm_id", "diagnosis", "source", "entry_id"]
    assert rows[1] == [later(3), 'Rodillo "A", lado norte', "repair", "soldado", "Sam", "", "", "", "person", "entry-000003"]
    assert rows[2] == [later(2), 'Rodillo "A", lado norte', "finding", 'grieta de 3,5 mm, dijo "urgente"\nsegunda línea',
                       "José Núñez", "yes", "", "", "person", "entry-000001"]
    assert rows[3][1:4] == ["", "note", "=1+1"] and len(rows) == 4
    _, _, raw = client.request("GET", f"/api/machines/{M}/log.csv?kind=repair&limit=1")
    assert [r[2] for r in csv.reader(io.StringIO(raw.decode("utf-8-sig"), newline=""))] == ["kind", "repair"]
    assert client.json("GET", f"/api/machines/{M}/log.csv?kind=nonsense")[0] == 400


def test_log_entry_events(make_receiver, open_stream):
    receiver, client = make_receiver()
    part = new_part(client)
    stream = open_stream(receiver.port)
    reply = add(client, "finding", part, needs_repair=True)
    assert stream.next_of(("log_entry", "part")) == ("log_entry", reply["entry"])
    assert stream.next("part") == reply["part"]


# ---- an alarm answer that names a part -------------------------------------------


def test_an_answer_with_a_part_makes_one_entry_and_a_repeat_makes_another(client):
    part = new_part(client)
    abnormal(client, 2, simulated=True)
    alarm_id = newest_alarm(client)["alarm_id"]
    status, reply = answer(client, alarm_id, part_id=part["part_id"], finding="added weight found", at=later(3))
    assert status == 200 and reply["alarm"]["answer"]["part_id"] == part["part_id"]  # the answer is unchanged
    entries = ok(client, "GET", f"/api/machines/{M}/log")["entries"]
    assert len(entries) == 1
    entry = dict(entries[0])
    entry.pop("received_at")
    assert entry == {"entry_id": "entry-000001", "machine_id": M, "part_id": part["part_id"], "at": later(3),
                     "kind": "finding", "text": "added weight found", "by": "Sam", "needs_repair": True,
                     "alarm_id": alarm_id, "diagnosis": "unbalance_like", "source": "alarm_answer", "run_id": None,
                     "simulated": True}
    assert state_of(client, part) == {"name": "needs_repair", "since": later(3), "reason": "finding: needs repair",
                                      "alarm_id": alarm_id, "entry_id": "entry-000001"}

    assert answer(client, alarm_id, part_id=part["part_id"], needs_repair=False, at=later(4))[0] == 200
    entries = ok(client, "GET", f"/api/machines/{M}/log")["entries"]
    assert [(e["entry_id"], e["needs_repair"], e["text"]) for e in entries] == [("entry-000002", False, ""), ("entry-000001", True, "added weight found")]
    assert name_of(client, part) == "possible"  # the alarm is still open
    assert answer(client, alarm_id, finding="no part named")[0] == 200
    assert len(ok(client, "GET", f"/api/machines/{M}/log")["entries"]) == 2
    assert len(newest_alarm(client)["answers"]) == 3


def test_a_false_verdict_never_leaves_a_part_in_needs_repair(client):
    part = new_part(client)
    abnormal(client, 2)
    alarm_id = newest_alarm(client)["alarm_id"]
    assert answer(client, alarm_id, part_id=part["part_id"], at=later(3))[0] == 200
    assert name_of(client, part) == "needs_repair"
    assert answer(client, alarm_id, verdict="false", part_id=part["part_id"], needs_repair=True, at=later(4))[0] == 200
    newest = ok(client, "GET", f"/api/machines/{M}/log")["entries"][0]
    assert newest["needs_repair"] is False and newest["source"] == "alarm_answer"
    assert name_of(client, part) == "possible"
    normal(client, 5)
    assert name_of(client, part) == "nothing_found"  # closed and answered


def test_an_answer_that_names_a_part_it_cannot_be_about_is_refused(client):
    part = new_part(client)
    elsewhere = new_part(client, machine="press-01")
    abnormal(client, 2)
    alarm_id = newest_alarm(client)["alarm_id"]
    for body, why in (
        ({"part_id": "part-9999"}, f"no such part on {M}: part-9999"),
        ({"part_id": elsewhere["part_id"]}, f"no such part on {M}: {elsewhere['part_id']}"),
        ({"part_id": 7}, "answer 'part_id' must be a part's id"),
        ({"part_id": part["part_id"], "needs_repair": "yes"}, "answer 'needs_repair' must be true or false"),
    ):
        assert answer(client, alarm_id, **body) == (400, {"ok": False, "error": why})
    assert newest_alarm(client)["answers"] == [] and ok(client, "GET", f"/api/machines/{M}/log")["entries"] == []
    assert answer(client, alarm_id, part_id=part["part_id"], at="yesterday")[0] == 200  # as before 3.4: `at` is not checked
    entry = ok(client, "GET", f"/api/machines/{M}/log")["entries"][0]
    assert entry["at"] == entry["received_at"]


# ---- a restart -----------------------------------------------------------------


def test_a_restart_gives_the_same_parts_entries_and_states(make_receiver, tmp_path):
    data = tmp_path / "kept"
    receiver, client = make_receiver(data_dir=data)
    repair = new_part(client)  # needs_repair
    possible = new_part(client, name="Idle roller", position={"scan_id": "scan-1", "at": [0.1, 0.2, 0.3]})
    nothing = new_part(client, name="Belt", kind="belt")
    gone = new_part(client, name="Old guard", kind="other")
    normal(client, 1)
    abnormal(client, 2)
    assert answer(client, newest_alarm(client)["alarm_id"], part_id=repair["part_id"], finding="added weight", at=later(3))[0] == 200
    add(client, "repair", nothing, at=later(1))
    add(client, "note", text="whole machine")
    ok(client, "PUT", f"/api/parts/{possible['part_id']}", {"by": "Ana", "note": "second roller"})
    ok(client, "PUT", f"/api/parts/{gone['part_id']}", {"by": "Ana", "removed": True})
    unchecked = new_part(client, name="Frame", kind="frame")
    add(client, "repair", unchecked, at=later(50))

    parts = ok(client, "GET", f"/api/machines/{M}/parts?removed=1")["parts"]
    entries = ok(client, "GET", f"/api/machines/{M}/log")["entries"]
    assert [p["state"]["name"] for p in parts] == ["needs_repair", "possible", "nothing_found", "nothing_found", "not_checked"]
    state = ok(client, "GET", "/api/state")["parts"]
    receiver.stop()

    receiver, client = make_receiver(data_dir=data)
    assert ok(client, "GET", f"/api/machines/{M}/parts?removed=1")["parts"] == parts
    assert ok(client, "GET", f"/api/machines/{M}/log")["entries"] == entries
    assert ok(client, "GET", "/api/state")["parts"] == state
    assert new_part(client, name="Motor", kind="motor")["part_id"] == "part-0006"  # ids go on, never repeat
    assert add(client, "note")["entry"]["entry_id"] == "entry-000005"
    normal(client, 60)  # and the states still move
    assert name_of(client, unchecked) == "nothing_found"


def test_a_cut_off_last_line_is_skipped_at_start(make_receiver, tmp_path):
    data = tmp_path / "kept"
    receiver, client = make_receiver(data_dir=data)
    part = new_part(client)
    add(client, "note", part)
    receiver.stop()
    for name in ("parts.jsonl", "log_entries.jsonl"):
        with open(data / name, "a", encoding="utf-8") as f:
            f.write('{"part_id": "part-0002", "entry_id": "entry-0000')
    _, client = make_receiver(data_dir=data)
    assert [p["part_id"] for p in ok(client, "GET", f"/api/machines/{M}/parts")["parts"]] == [part["part_id"]]
    assert len(ok(client, "GET", f"/api/machines/{M}/log")["entries"]) == 1


# ---- bad requests: a 400 with a useful sentence ------------------------------------

BAD_PARTS = [
    (["not", "an", "object"], "part must be a JSON object"),
    ({"name": "Belt", "kind": "belt"}, "part is missing required field 'by' (the name the person gave)"),
    ({"name": "Belt", "kind": "belt", "by": "  "}, "part is missing required field 'by' (the name the person gave)"),
    ({"kind": "belt", "by": "Sam"}, "part is missing required field 'name'"),
    ({"name": "Belt", "by": "Sam"}, "part is missing required field 'kind'"),
    ({"name": " ", "kind": "belt", "by": "Sam"}, "part 'name' must be text, what people call the part"),
    ({"name": "Belt", "kind": "strap", "by": "Sam"}, "part 'kind' must be one of motor, bearing, roller, belt, gearbox, coupling, fan, pump, mount, frame, probe_point, other"),
    ({"name": "Belt", "kind": "belt", "by": "Sam", "fault_types": "speed_change"}, "part 'fault_types' must be a list of diagnosis names"),
    ({"name": "Belt", "kind": "belt", "by": "Sam", "fault_types": ["speed_change", 3]}, "part 'fault_types' must be a list of diagnosis names"),
    ({"name": "Belt", "kind": "belt", "by": "Sam", "position": [0, 0, 0]}, 'part \'position\' must be null or {"scan_id": "...", "at": [x, y, z]}'),
    ({"name": "Belt", "kind": "belt", "by": "Sam", "position": {"scan_id": "scan-1", "at": [0, 0]}}, 'part \'position\' must be null or {"scan_id": "...", "at": [x, y, z]}'),
    ({"name": "Belt", "kind": "belt", "by": "Sam", "position": {"at": [0, 0, 0]}}, 'part \'position\' must be null or {"scan_id": "...", "at": [x, y, z]}'),
    ({"name": "Belt", "kind": "belt", "by": "Sam", "position": {"scan_id": "scan-1", "at": [0, "0", True]}}, 'part \'position\' must be null or {"scan_id": "...", "at": [x, y, z]}'),
    ({"name": "Belt", "kind": "belt", "by": "Sam", "note": 5}, "part 'note' must be text"),
    ({"name": "Belt", "kind": "belt", "by": "Sam", "removed": "no"}, "part 'removed' must be true or false"),
]


@pytest.mark.parametrize("body,why", BAD_PARTS)
def test_a_bad_part_is_refused(client, body, why):
    assert client.json("POST", f"/api/machines/{M}/parts", body) == (400, {"ok": False, "error": why})
    assert ok(client, "GET", f"/api/machines/{M}/parts")["parts"] == []


def test_a_bad_change_to_a_part_is_refused(client):
    part = new_part(client)
    path = f"/api/parts/{part['part_id']}"
    for body, why in (
        ({"name": "Belt"}, "part is missing required field 'by' (the name the person gave)"),
        ({"by": "Sam"}, "nothing to change: send any of name, kind, position, fault_types, note, removed"),
        ({"by": "Sam", "state": {"name": "needs_repair"}}, "nothing to change: send any of name, kind, position, fault_types, note, removed"),
        ({"by": "Sam", "kind": "strap"}, "part 'kind' must be one of motor, bearing, roller, belt, gearbox, coupling, fan, pump, mount, frame, probe_point, other"),
        ({"by": "Sam", "name": None}, "part 'name' must be text, what people call the part"),
        ({"by": "Sam", "fault_types": None}, "part 'fault_types' must be a list of diagnosis names"),
    ):
        assert client.json("PUT", path, body) == (400, {"ok": False, "error": why})
    assert client.json("PUT", "/api/parts/part-9999", {"by": "Sam", "note": "x"}) == (400, {"ok": False, "error": "no such part: part-9999"})
    assert client.request("PUT", path, raw=b"{not json")[0] == 400
    assert ok(client, "GET", f"/api/machines/{M}/parts")["parts"] == [part]


BAD_ENTRIES = [
    ("a string", "entry must be a JSON object"),
    ({"by": "Sam", "text": "x"}, "entry 'kind' must be one of finding, inspection, repair, note, review"),
    ({"kind": "fix", "by": "Sam", "text": "x"}, "entry 'kind' must be one of finding, inspection, repair, note, review"),
    ({"kind": "note", "text": "x"}, "entry is missing required field 'by' (the name the person gave)"),
    ({"kind": "note", "by": "Sam"}, "entry is missing required field 'text' (what happened): only a finding or an inspection may leave it empty"),
    ({"kind": "repair", "by": "Sam", "text": "  "}, "entry is missing required field 'text' (what happened): only a finding or an inspection may leave it empty"),
    ({"kind": "finding", "by": "Sam", "text": 5, "needs_repair": True}, "entry is missing required field 'text' (what happened): only a finding or an inspection may leave it empty"),
    ({"kind": "finding", "by": "Sam"}, "entry 'needs_repair' is required for a finding or an inspection: true or false"),
    ({"kind": "inspection", "by": "Sam", "needs_repair": "yes"}, "entry 'needs_repair' is required for a finding or an inspection: true or false"),
    ({"kind": "repair", "by": "Sam", "text": "x", "needs_repair": False}, "'needs_repair' is only for a finding or an inspection, not a repair"),
    ({"kind": "note", "by": "Sam", "text": "x", "at": "last Tuesday"}, "entry 'at' must be an ISO 8601 time"),
    ({"kind": "note", "by": "Sam", "text": "x", "at": 1763136000}, "entry 'at' must be an ISO 8601 time"),
    ({"kind": "note", "by": "Sam", "text": "x", "part_id": 7}, "entry 'part_id' must be an id or null"),
    ({"kind": "note", "by": "Sam", "text": "x", "part_id": "part-9999"}, f"no such part on {M}: part-9999"),
    ({"kind": "note", "by": "Sam", "text": "x", "alarm_id": "alarm-9999"}, f"no such alarm on {M}: alarm-9999"),
    ({"kind": "review", "by": "part review agent", "text": "x", "simulated": "no"}, "entry 'simulated' must be true or false"),
]


@pytest.mark.parametrize("body,why", BAD_ENTRIES)
def test_a_bad_entry_is_refused(client, body, why):
    assert client.json("POST", f"/api/machines/{M}/log", body) == (400, {"ok": False, "error": why})
    assert ok(client, "GET", f"/api/machines/{M}/log")["entries"] == []


def test_an_entry_cannot_name_another_machines_part_or_alarm(client):
    part = new_part(client)
    abnormal(client, 2)
    alarm_id = newest_alarm(client)["alarm_id"]
    for body, why in (
        ({"part_id": part["part_id"]}, f"no such part on press-01: {part['part_id']}"),
        ({"alarm_id": alarm_id}, f"no such alarm on press-01: {alarm_id}"),
    ):
        reply = client.json("POST", "/api/machines/press-01/log", {"kind": "note", "by": "Sam", "text": "x", **body})
        assert reply == (400, {"ok": False, "error": why})


@pytest.mark.parametrize("query,why", [
    ("kind=fix", "kind must be one of finding, inspection, repair, note, review"),
    ("since=yesterday", "since must be an ISO 8601 time"),
    ("limit=many", "limit must be a whole number"),
    ("limit=-1", "limit must be 0 or more"),
])
def test_a_bad_log_read_is_refused(client, query, why):
    assert client.json("GET", f"/api/machines/{M}/log?{query}") == (400, {"ok": False, "error": why})


def test_a_refused_request_leaves_the_connection_usable(client):
    import http.client

    conn = http.client.HTTPConnection("127.0.0.1", client.port, timeout=10)
    try:
        conn.request("POST", f"/api/machines/{M}/parts", body=json.dumps({"name": "Belt"}))
        first = conn.getresponse()
        assert (first.status, json.loads(first.read())["ok"]) == (400, False)
        conn.request("GET", f"/api/machines/{M}/parts")
        second = conn.getresponse()
        assert (second.status, json.loads(second.read())) == (200, {"ok": True, "parts": []})
    finally:
        conn.close()
