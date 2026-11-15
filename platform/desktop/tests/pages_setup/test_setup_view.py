"""The Setup page's rules, without a window."""

from __future__ import annotations

import pytest
from app.data.setup import FIELDS
from app.pages.setup import view

pytest.importorskip("PySide6")  # app.data.setup imports Qt

from fake_setup import sample_choice, sample_profile, sample_run

EMPTY = dict.fromkeys(FIELDS)


def test_no_question_asks_for_a_library_entry():
    words = " ".join(q.text + " " + q.example for q in view.QUESTIONS).lower()
    for banned in ("library", "entry", "model", "conveyor (demo)", "generic_rotating"):
        assert banned not in words


def test_dont_know_and_nothing_typed_are_none():
    typed = {"machine_type": "  a belt   conveyor ", "drive": "belt", "speed_rpm": ""}
    items = view.answers(typed, {"drive"})
    by_id = {a["about"]: a["answer"] for a in items}
    assert by_id == {"machine_type": "a belt conveyor", "drive": None, "speed_rpm": None, "mounting": None,
                     "sensor_location": None}
    assert tuple(by_id) == FIELDS  # each question asks about one field, in the profile's order
    assert [a["question"] for a in items] == [q.text for q in view.QUESTIONS]
    assert view.answered_text(items) == "1 answered, 4 not known"
    assert view.answered_text(view.answers({}, set())) == "Nothing answered yet"


@pytest.mark.parametrize("text, value", [
    ("", None), ("1450", 1450), ("1450.5", 1450.5), ("1400 to 1500", [1400, 1500]), ("1400-1500", [1400, 1500]),
])
def test_speed_parses(text, value):
    assert view.parse_speed(text) == (value, "")


@pytest.mark.parametrize("text", ["0", "fast", "1500 to 1400", "-3", "1400 to 1400"])
def test_a_bad_speed_says_why(text):
    assert view.parse_speed(text) == (None, view.SPEED_HINT)


def test_speed_text_round_trips():
    for v in (1450, [1400, 1500], 1450.5):
        assert view.parse_speed(view.speed_text(v))[0] == v
    assert view.speed_text(None) == ""


def test_choice_rows_keep_trained_and_checked_apart():
    rows = dict(view.choice_rows(sample_choice()))
    assert rows["Why"] == "machine type is conveyor"
    assert rows["Trained on"] == "sim conveyor (simulated)"
    assert rows["Checked on"] == "Nothing yet"
    assert rows["Not checked on"] == "sim server rack (simulated): wrong machine: a server rack, for the fit check"
    assert rows["Fault types validated"] == "0 of 21"
    assert view.choice_title(sample_choice()) == "conveyor (demo)"
    assert view.choice_warning(sample_choice())
    assert view.choice_rows(None) == []


def test_a_validated_fault_type_is_named():
    choice = dict(sample_choice(), fault_types_validated=["imbalance"], fault_types_not_validated=20,
                  checked_on=[{"name": "line 3", "kind": "real"}])
    rows = dict(view.choice_rows(choice))
    assert rows["Fault types validated"] == "1 of 21: imbalance"
    assert rows["Checked on"] == "line 3 (real)"
    assert view.choice_warning(choice) == ""


def test_run_states():
    assert view.run(None, False).status == view.NOT_RUN
    assert view.run(None, True).status == view.RUNNING
    done = view.run(sample_run("m"), False)
    assert (done.status, done.standin) == (view.DONE, True)
    assert done.title == "What the stand-in understood" and done.text.startswith("Stand-in:")
    failed = view.run(sample_run("m", ok=False, reason="no shape."), False)
    assert failed.status == view.FAILED and "Why: no shape." in failed.text and "by hand" in failed.text
    assert "gave no reason" in view.run(sample_run("m", ok=False), False).text
    real = view.run(dict(sample_run("m"), simulated=False), False)
    assert real.title == "What the agent understood" and not real.standin


def test_confirmed_changed_and_not_confirmed():
    profile = sample_profile("m")
    draft = {k: profile[k] for k in FIELDS}
    assert view.profile_hint(None, draft) == view.NOT_CONFIRMED
    assert view.profile_hint(profile, draft) == view.CONFIRMED
    assert view.profile_hint(profile, dict(draft, drive="gearbox")) == view.CHANGED
    assert "Nothing has been sent to the device" in view.confirm_line(None, draft)
    assert view.confirm_line(profile, draft).startswith("Confirmed by Sam on 14 Nov")


def test_use_problem():
    kw = {"machine": "m", "choice": sample_choice(), "running": False, "speed_problem": "", "profile": None,
          "draft": EMPTY}
    assert view.use_problem(**kw) == ""
    assert view.use_problem(**dict(kw, machine=None)) == view.NO_MACHINE
    assert view.use_problem(**dict(kw, choice=None)) == view.LOOKING_UP
    assert view.use_problem(**dict(kw, choice=None, lookup_problem="no entry")) == "no entry"
    assert view.use_problem(**dict(kw, running=True))
    assert view.use_problem(**dict(kw, speed_problem=view.SPEED_HINT)).startswith("Speed:")


def test_every_field_empty_is_allowed_and_said():
    assert "allowed" in view.profile_note(EMPTY)
    assert view.profile_note(dict(EMPTY, machine_type="fan", drive="belt", mounting="rigid")).startswith("2 left")
    assert view.profile_note({k: "x" for k in FIELDS}) == ""


def test_the_wanted_entry_is_never_shown_as_in_use():
    assert view.device(None, "generic_rotating", True).status == view.NOTHING_SENT
    no_device = view.device("conveyor", None, False)
    waiting = view.device("conveyor", None, True)
    differs = view.device("conveyor", "generic_rotating", True)
    taken = view.device("conveyor", "conveyor", True)
    assert [d.status for d in (no_device, waiting, differs, taken)] == [
        view.NO_DEVICE, view.WAITING, view.DIFFERS, view.TAKEN]
    for d in (no_device, waiting):
        assert d.line == "Chosen: conveyor, not yet loaded on the device."
        assert view.NOT_ACTED in d.note
    assert differs.line == "Chosen: conveyor. Device is using: generic rotating."
    assert taken.line == "Device is using: conveyor, the chosen one."


def test_local_profile_words():
    assert view.local_text({"name": "Sam", "role": "technician"}) == "Sam, technician"
    assert view.local_text({"name": "Sam", "role": ""}) == "Sam"
    assert view.local_hint(None) == "kept on this laptop only"
    assert view.fallback_note("jake") == "Until one is saved, your name is sent as “jake”."
