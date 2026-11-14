"""Run every fixed history through the part review with the stand-in and check the known answer."""

import json

import pytest
from part_review_cases import CASES, HISTORIES, MACHINE, history_of, load_case

from device.agent.standin import MODEL_NAME
from partlog.review import history as histories
from partlog.review.agent import run_review
from partlog.review.review import REVIEW_KEYS, check_review
from partlog.review.standin import LABEL, stand_in_call


def review_case(name, tmp_path, call=stand_in_call):
    data, expected = load_case(name)
    result = run_review(
        MACHINE, expected["part_id"],
        load=lambda: histories.part_history(data, expected["part_id"]),
        call=call, agent_log_path=tmp_path / "part-review.jsonl",
    )
    return result, expected


def test_the_five_histories_of_the_plan_are_there():
    assert CASES == ["confirmed_waiting", "false_alarm", "never_checked", "possible_only", "repaired"]
    for name in CASES:
        data, expected = load_case(name)
        part = next(p for p in data["parts"] if p["part_id"] == expected["part_id"])
        assert part["state"]["name"] == expected["state"], name


@pytest.mark.parametrize("name", CASES)
def test_stand_in_gives_the_known_right_answer(name, tmp_path):
    result, expected = review_case(name, tmp_path)

    assert result.ok is True, result
    assert (result.reason, result.problems) == ("", [])
    review = result.review
    assert tuple(review) == REVIEW_KEYS
    assert review["needs_repair"] == expected["needs_repair"]
    assert review["cited_alarms"] == expected["cited_alarms"]
    assert review["cited_entries"] == expected["cited_entries"]
    assert review["what"].startswith(LABEL)
    assert result.simulated is True
    assert result.run_id.startswith("review-") and result.run_id.endswith(expected["part_id"])
    assert result.part_id == expected["part_id"]
    assert result.entry_id == ""  # no platform: no log entry

    logged = json.loads((tmp_path / "part-review.jsonl").read_text())
    assert logged["model"] == MODEL_NAME
    assert logged["review"] == review and logged["machine_id"] == MACHINE


@pytest.mark.parametrize("name", CASES)
def test_how_and_since_are_copied_by_the_code(name, tmp_path):
    result, expected = review_case(name, tmp_path)
    data, _ = load_case(name)
    part = next(p for p in data["parts"] if p["part_id"] == expected["part_id"])

    assert result.review["how"] == expected["how"]
    assert result.review["since"] == expected["since"] == part["state"]["since"]
    if expected["how"]:
        assert expected["how"] in [f["text"] for f in data["fault_types"]]


def test_how_and_since_do_not_come_from_the_writer(tmp_path):
    """A writer that returns a good draft gets `how` and `since` added; it never sees or sets them."""
    seen = {}

    def writer(sheet, schema, check):
        seen["schema"] = schema
        return stand_in_call(sheet, schema, check)

    result, expected = review_case("confirmed_waiting", tmp_path, call=writer)

    assert result.ok and result.review["how"] == expected["how"] and result.review["since"] == expected["since"]
    assert "how" not in seen["schema"]["properties"] and "since" not in seen["schema"]["properties"]


@pytest.mark.parametrize("name", CASES)
def test_a_run_reads_only_its_own_parts_history(name, tmp_path):
    result, expected = review_case(name, tmp_path)
    data, _ = load_case(name)

    (call,) = result.tool_calls
    assert call["name"] == "get_part_history" and call["input"] == {"part_id": expected["part_id"]}
    sheet = histories.fact_sheet(history_of(name))
    assert len(sheet["entries"]) == sheet["counts"]["entries"] == expected["sheet_entries"]
    assert len(sheet["alarms"]) == sheet["counts"]["alarms"] == expected["sheet_alarms"]
    # Every fixed history holds another part with an entry of its own. None of it is on the sheet.
    others = [p for p in data["parts"] if p["part_id"] != expected["part_id"]]
    theirs = [e for e in data["entries"] if e["part_id"] != expected["part_id"]]
    assert others and theirs
    text = json.dumps(sheet)
    for other in others:
        assert other["part_id"] not in text and other["name"] not in text
    for entry in theirs:
        assert entry["entry_id"] not in text and entry["text"] not in text


def test_an_alarm_is_linked_by_an_entry_by_the_state_or_by_waiting_on_the_part():
    data, _ = load_case("confirmed_waiting")

    def alarms_of(part_id):
        return [a["alarm_id"] for a in histories.part_history(data, part_id)["alarms"]]

    assert alarms_of("part-0007") == ["alarm-1234"]  # its finding names it; the open looseness alarm is another part's
    assert alarms_of("part-0002") == ["alarm-2301"]  # open, unanswered, and its diagnosis is in the part's fault_types
    assert alarms_of("part-0003") == []


def test_an_alarm_from_before_the_newest_repair_no_longer_waits_on_the_part():
    data, _ = load_case("possible_only")
    data["entries"].append({"entry_id": "entry-000200", "part_id": "part-0002", "at": "2026-11-14T19:50:00Z",
                            "kind": "repair", "text": "bolts tightened", "by": "Sam", "needs_repair": None,
                            "alarm_id": None, "diagnosis": None})
    data["parts"][1]["state"] = {"name": "not_checked", "since": None, "reason": "", "alarm_id": None, "entry_id": None}

    assert histories.part_history(data, "part-0002")["alarms"] == []


def test_a_fault_type_the_announcement_does_not_list_has_no_text(confirmed):
    confirmed["fault_types"] = [f for f in confirmed["fault_types"] if f["name"] != "unbalance_like"]

    sheet = histories.fact_sheet(confirmed)

    assert sheet["fault_type"] == {"name": "unbalance_like", "text": "", "validated": False}
    draft, _ = stand_in_call(sheet, {})
    assert check_review({**draft, "how": "", "since": sheet["state"]["since"]}, confirmed) == []


def test_no_history_gives_no_review_and_a_plain_reason(tmp_path):
    data, _ = load_case("repaired")

    result = run_review(MACHINE, "part-9999", load=lambda: histories.part_history(data, "part-9999"),
                        agent_log_path=tmp_path / "log.jsonl")

    assert (result.ok, result.review, result.tool_calls) == (False, None, [])
    assert result.reason == "no history: conveyor-07 has no part part-9999"


def test_a_history_file_for_another_machine_is_refused():
    with pytest.raises(histories.HistoryUnavailable, match="holds no history for fan-1"):
        histories.read_file(HISTORIES / "repaired.json", "fan-1")


def test_a_stand_in_review_that_breaks_a_check_is_discarded(tmp_path):
    """The stand-in goes through the same checks as a model."""
    def careless(sheet, schema, check):
        draft, usage = stand_in_call(sheet, schema, check)
        return {**draft, "needs_repair": "yes"}, usage

    result, _ = review_case("never_checked", tmp_path, call=careless)

    assert (result.ok, result.review, result.reason) == (False, None, "review failed checks")
    assert result.problems == ["needs_repair is yes with no alarm and no entry that says the part needs repair"]


def test_a_bug_in_the_writer_closes_the_run_and_surfaces(tmp_path):
    def broken(sheet, schema, check):
        raise KeyError("boom")

    with pytest.raises(KeyError):
        review_case("repaired", tmp_path, call=broken)

    logged = json.loads((tmp_path / "part-review.jsonl").read_text())
    assert logged["ok"] is False and logged["reason"] == "agent error: KeyError: 'boom'"
