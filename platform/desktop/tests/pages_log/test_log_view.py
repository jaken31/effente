"""The Log page's rules, with no window: what each state reads as, what a row shows,
when a form may be sent, and what a review may claim."""

from __future__ import annotations

from datetime import UTC

import pytest

pytest.importorskip("PySide6")

from app.pages.log import view

UNHEALTHY_WORDS = ("healthy", "ok", "fine", "good", "normal", "no fault", "no problem")


def part(part_id="part-0001", name="Drive roller", kind="roller", state="needs_repair", since=None, pin=True, **more):
    return {"part_id": part_id, "name": name, "kind": kind, "fault_types": ["unbalance_like"],
            "position": {"scan_id": "scan-1", "at": [0, 0, 0]} if pin else None,
            "state": {"name": state, "since": since, "reason": "", "alarm_id": None, "entry_id": None}, **more}


def run(**changes):
    made = {
        "run_id": "review-1", "agent": "part_review", "part_id": "part-0001", "at": "2026-11-14T16:06:00Z",
        "status": "done", "model": "stand-in (no model call)", "ok": True, "reason": "", "problems": [],
        "review": {"what": "Possible unbalance on the drive roller.", "how": "once-per-turn vibration grown",
                   "since": "2026-11-14T16:05:00Z", "needs_repair": "yes", "why": "One alarm, answered as real.",
                   "cited_alarms": ["alarm-5"], "cited_entries": ["entry-000004"]},
        "simulated": True,
    }
    made.update(changes)
    return made


# ---- time and names ---------------------------------------------------------


def test_when_is_day_month_and_minutes():
    assert view.when("2026-11-04T09:05:59Z", UTC) == "4 Nov 09:05"
    assert view.when("2026-11-14T16:00:08.000Z", UTC) == "14 Nov 16:00"
    for bad in (None, "", "soon", 5):
        assert view.when(bad, UTC) == view.DASH


def test_kind_names_read_as_words():
    assert view.kind_label("probe_point") == "Probe point"
    assert [view.kind_label(k) for k in view.PERSON_KINDS] == ["Finding", "Inspection", "Repair", "Note"]
    assert "review" not in view.PERSON_KINDS  # a person does not write a review
    assert len(view.PART_KINDS) == 12


# ---- part states ------------------------------------------------------------


def test_every_state_has_its_words():
    assert view.state_text("needs_repair") == "Needs repair"
    assert view.state_text("possible") == "Possible"
    assert view.state_text("nothing_found") == "Nothing found"
    assert view.state_text("not_checked") == "Not checked"
    assert view.state_text("") == view.NO_STATE
    assert view.state_text("being_rebuilt") == "Being rebuilt"  # a state from a newer platform, shown as sent


def test_nothing_found_never_reads_as_healthy():
    p = part(state="nothing_found", since="2026-11-14T16:00:00Z")
    line = view.state_line(p, UTC)
    assert line == "Nothing found since 14 Nov 16:00"
    assert not any(w in line.lower() for w in UNHEALTHY_WORDS)
    note = view.state_note(p)
    assert "does not mean the part is healthy" in note
    row = view.part_rows([p], None, 0, UTC)[0]
    assert not any(w in row.line.lower() for w in UNHEALTHY_WORDS)


def test_not_checked_and_nothing_found_differ():
    assert view.state_text("not_checked") != view.state_text("nothing_found")
    assert view.state_note(part(state="not_checked")) != view.state_note(part(state="nothing_found"))
    assert view.state_line(part(state="not_checked"), UTC) == "Not checked"  # no `since`


def test_possible_says_it_is_not_confirmed():
    note = view.state_note(part(state="possible"))
    assert "Not confirmed" in note and "does not locate a fault" in note


# ---- the parts list ---------------------------------------------------------


def test_part_rows_end_with_the_whole_machine():
    parts = [part(since="2026-11-14T16:05:00Z"), part("part-0004", "Idle roller", state="not_checked", pin=False)]
    rows = view.part_rows(parts, "part-0004", 6, UTC)
    assert [(r.part_id, r.name, r.state, r.line, r.selected) for r in rows] == [
        ("part-0001", "Drive roller", "needs_repair", "Needs repair since 14 Nov 16:05", False),
        ("part-0004", "Idle roller", "not_checked", "Not checked, no pin", True),
        (None, "Whole machine", "", "6 entries", False),
    ]
    assert view.part_rows(parts, None, 1, UTC)[-1].selected
    assert view.part_rows(parts, None, 1, UTC)[-1].line == "1 entry"
    assert view.part_rows(parts, None, 0, UTC)[-1].line == "no entries yet"
    assert view.part_rows([], None, 3, UTC) == []  # no parts: the page shows its empty state instead


def test_heading_for_a_part_and_for_the_machine():
    head = view.heading(part(since="2026-11-14T16:05:00Z"), "conveyor-07", UTC)
    assert (head.title, head.hint, head.state, head.line) == (
        "Drive roller", "roller", "needs_repair", "Needs repair since 14 Nov 16:05")
    whole = view.heading(None, "conveyor-07", UTC)
    assert (whole.title, whole.hint, whole.state, whole.line, whole.note) == (
        "Whole machine", "conveyor-07", "", "", view.WHOLE_NOTE)
    assert view.heading(None, None, UTC).note == view.NO_MACHINE


# ---- entries ----------------------------------------------------------------


def test_entry_rows():
    entries = [
        {"entry_id": "entry-000004", "part_id": "part-0001", "at": "2026-11-14T16:05:00Z", "kind": "finding",
         "text": "added weight", "by": "Sam", "needs_repair": True, "alarm_id": "alarm-5", "simulated": True},
        {"entry_id": "entry-000003", "part_id": "part-0003", "at": "2026-11-14T15:58:00Z", "kind": "inspection",
         "text": "", "by": "Sam", "needs_repair": False, "alarm_id": None, "simulated": False},
        {"entry_id": "entry-000001", "part_id": None, "at": "2026-11-14T15:45:00Z", "kind": "note",
         "text": "arrived", "by": "Sam", "needs_repair": None, "alarm_id": None},
    ]
    rows = view.entry_rows(entries, {"part-0001": "Drive roller"}, UTC)
    assert [(r.when, r.part, r.kind, r.text, r.needs_repair, r.flag, r.by, r.alarm, r.tag) for r in rows] == [
        ("14 Nov 16:05", "Drive roller", "Finding", "added weight", "yes", True, "Sam", "alarm-5", "simulated"),
        ("14 Nov 15:58", "part-0003", "Inspection", view.DASH, "no", False, "Sam", view.DASH, ""),
        ("14 Nov 15:45", "Whole machine", "Note", "arrived", view.DASH, False, "Sam", view.DASH, ""),
    ]


def test_count_and_empty_texts():
    assert view.count_text(1, view.ALL) == "1 entry"
    assert view.count_text(0, view.ALL) == "0 entries"
    assert view.count_text(2, "repair") == "2 repair entries"
    assert view.count_text(1, "repair") == "1 repair entry"
    p = part()
    assert view.empty_text(p, view.ALL, "m").startswith("No entries yet for Drive roller.")
    assert view.empty_text(None, view.ALL, "m").startswith("No entries yet for this machine.")
    assert view.empty_text(p, "repair", "m") == "No repair entries for Drive roller."
    assert view.empty_text(None, view.ALL, None) == view.NO_MACHINE


@pytest.mark.parametrize(("kind", "text", "needs", "ok"), [
    ("note", "looked at it", None, True),
    ("note", "   ", None, False),
    ("repair", "", None, False),
    ("repair", "new belt", None, True),
    ("finding", "", True, True),  # text may be empty for a finding (contract 8.6)
    ("finding", "worn", None, False),  # but it must say whether it needs repair
    ("inspection", "", False, True),
    ("inspection", "", None, False),
])
def test_when_an_entry_may_be_added(kind, text, needs, ok):
    assert (view.entry_problem(kind, text, needs) == "") is ok


def test_entry_placeholder_names_what_it_is_about():
    assert view.entry_placeholder("note", part()) == "Note about Drive roller"
    assert view.entry_placeholder("finding", None) == "Finding about the whole machine (optional)"


def test_csv_name():
    assert view.csv_name("conveyor-07") == "conveyor-07-log.csv"
    assert view.csv_name(None) == "machine-log.csv"


# ---- alarms waiting ---------------------------------------------------------


def test_suspects_are_always_possible():
    assert view.suspects_text([part(), part("p2", "Idle roller")]) == "possible: Drive roller, Idle roller"
    assert view.suspects_text([]) == view.NO_SUSPECT


def test_alarm_rows():
    alarms = [
        {"alarm_id": "alarm-9", "diagnosis": "unrecognised_pattern", "start": "2026-11-14T16:00:08Z", "open": True,
         "simulated": True},
        {"alarm_id": "alarm-5", "diagnosis": "unbalance_like", "start": "2026-11-14T16:00:04Z", "open": False},
    ]
    rows = view.alarm_rows(alarms, lambda a: [part()] if a["diagnosis"] == "unbalance_like" else [], UTC)
    assert [(r.alarm_id, r.title, r.when, r.suspects, r.simulated) for r in rows] == [
        ("alarm-9", "Unrecognised pattern", "14 Nov 16:00, still going", view.NO_SUSPECT, True),
        ("alarm-5", "Unbalance-like", "14 Nov 16:00", "possible: Drive roller", False),
    ]


def test_part_choices_put_suspects_first_and_mark_them():
    a, b, c = part("a", "Belt"), part("b", "Drive roller"), part("c", "Mount")
    assert view.part_choices([a, b, c], [b]) == [("b", "Drive roller (possible)"), ("a", "Belt"), ("c", "Mount")]
    assert view.part_choices([a], []) == [("a", "Belt")]


def test_an_answer_needs_a_verdict_and_a_part():
    assert view.answer_problem("real", "a", ["a"]) == ""
    assert view.answer_problem("false", "a", ["a"]) == ""
    assert view.answer_problem(None, "a", ["a"]) == "Say whether it was a real problem."
    assert view.answer_problem("real", None, ["a"]) == "Pick the part."
    assert view.answer_problem("real", None, []).startswith("Add a part first")


# ---- the review -------------------------------------------------------------


def test_a_passed_review_is_shown_as_advice():
    shown = view.review(part(), run(), False, UTC)
    assert shown.status == view.DONE
    assert shown.message == view.ADVICE_NOTE
    assert shown.simulated and shown.can_agree
    assert dict(shown.rows) == {
        "Needs repair": "Yes (advice)",
        "What": "Possible unbalance on the drive roller.",
        "How it comes about": "once-per-turn vibration grown",
        "Since": "14 Nov 16:05",
        "Why": "One alarm, answered as real.",
        "Cited": "alarm-5, entry-000004",
        "Written": "14 Nov 16:06 by stand-in (no model call)",
    }


@pytest.mark.parametrize(("advice", "text"), [("no", "No (advice)"), ("not_sure", "Not sure (advice)")])
def test_every_advice_is_labelled_advice(advice, text):
    r = run()
    r["review"]["needs_repair"] = advice
    assert dict(view.review(part(), r, False, UTC).rows)["Needs repair"] == text


def test_a_review_with_nothing_cited_says_so():
    r = run()
    r["review"].update(cited_alarms=[], cited_entries=[], how="")
    rows = dict(view.review(part(), r, False, UTC).rows)
    assert rows["Cited"] == "nothing"
    assert rows["How it comes about"] == view.DASH  # never a cause made up here


def test_review_states():
    p = part()
    none = view.review(p, None, False, UTC)
    assert (none.status, none.rows, none.can_agree) == (view.NO_REVIEW, (), False)
    assert "No review yet for Drive roller" in none.message

    first = view.review(p, None, True, UTC)
    assert (first.status, first.message, first.rows, first.can_agree) == (view.RUNNING, "Reviewing Drive roller…", (), False)

    again = view.review(p, run(), True, UTC)  # the last one stays while the next runs
    assert again.status == view.RUNNING and again.rows and not again.can_agree
    assert again.message.endswith("The review below is the previous one.")

    posted = view.review(p, run(status="running", ok=False, review=None), False, UTC)
    assert posted.status == view.RUNNING and posted.rows == ()

    failed = view.review(p, run(ok=False, review=None, reason="it cited an alarm it was not given"), False, UTC)
    assert (failed.status, failed.rows, failed.can_agree, failed.simulated) == (view.FAILED, (), False, False)
    assert "discarded: it cited an alarm it was not given" in failed.message
    assert "Nothing was added to the log" in failed.message
    assert "no reason was given" in view.review(p, run(ok=False, review=None), False, UTC).message

    assert view.review(None, None, False, UTC).status == view.NO_PART
    assert "No parts yet" in view.review(None, None, False, UTC, has_parts=False).message


def test_agree_draft_is_the_reviews_text_and_advice():
    assert view.agree_draft(run()) == ("Possible unbalance on the drive roller.", True)
    r = run()
    r["review"]["needs_repair"] = "no"
    assert view.agree_draft(r)[1] is False
    r["review"]["needs_repair"] = "not_sure"
    assert view.agree_draft(r)[1] is None  # the person must choose
    assert view.agree_draft(None) == ("", None)
