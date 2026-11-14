"""The Log page on FakeState: every panel, every action and every state it has to draw."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from app.pages.log import LogPage, view
from app.pages.log.table import PART
from app.theme import tokens as t
from fake_partlog import (
    ANSWERED_ALARM,
    BELT,
    DRIVE_ROLLER,
    IDLE_ROLLER,
    MOTOR_MOUNT,
    OPEN_ALARM,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QFileDialog, QLabel

PAGE_SIZE = (1320, 896)  # the page area inside a 1600x1000 window


@pytest.fixture
def state():
    from fakes import FakeState

    return FakeState()


@pytest.fixture
def page(qtbot, state):
    p = LogPage()
    qtbot.addWidget(p)
    p.resize(*PAGE_SIZE)
    p.bind(state)
    p.show()
    return p


@pytest.fixture
def failures(state):
    seen = []
    state.action_failed.connect(seen.append)
    return seen


def click(qtbot, widget):
    qtbot.mouseClick(widget, Qt.MouseButton.LeftButton)


def added(state) -> dict:
    """The entry the fake stored last. Its frozen clock is before the sample's entries,
    so a new entry is not the first of the newest-first list."""
    return state.log._entries[-1]


def table_texts(page, col_field):
    from app.pages.log.table import FIELDS

    col = FIELDS.index(col_field)
    return [page.table.entries.text(r, col) for r in range(page.table.entries.rowCount())]


# ---- the shell finds it -------------------------------------------------------


def test_the_shell_builds_this_page_for_the_log_key():
    from app.shell.pages import find_page_class

    assert find_page_class("log") is LogPage
    assert (LogPage.key, LogPage.title) == ("log", "Log")


def test_the_page_never_imports_the_machine_page():
    folder = Path(view.__file__).parent
    for source in folder.glob("*.py"):
        assert "pages.machine" not in source.read_text(), source.name
        assert "from ..machine" not in source.read_text(), source.name


# ---- parts ------------------------------------------------------------------


def test_parts_list_shows_each_part_with_its_state(page):
    rows = page.parts_panel.rows
    assert [r.name.text() for r in rows] == ["Drive roller", "Motor mount", "Belt", "Idle roller", "Whole machine"]
    assert [r.mark.state() for r in rows] == ["needs_repair", "possible", "nothing_found", "not_checked", ""]
    assert rows[0].line.text() == f"Needs repair since {view.when('2026-11-14T16:05:00Z')}"
    assert rows[1].line.text().startswith("Possible since ")
    assert rows[2].line.text().startswith("Nothing found since ")
    assert rows[3].line.text() == "Not checked, no pin"
    assert rows[4].line.text() == "6 entries"
    assert rows[4].row.selected  # nothing selected yet: the whole machine


def test_nothing_found_is_never_drawn_as_healthy(page, state):
    state.log.select_part(BELT)
    assert page.state_line.text().startswith("Nothing found since ")
    assert "does not mean the part is healthy" in page.state_note.text()
    shown = " ".join(w.text().lower() for w in page.findChildren(QLabel) if w.isVisible())
    assert "nothing found" in shown
    for word in ("healthy", "no fault", "all good", "ok "):
        assert word not in shown.replace("does not mean the part is healthy", "")


def test_state_marks_differ_and_only_the_urgent_ones_are_red():
    from app.pages.log.marks import MARKS

    assert len(set(MARKS.values())) == len(MARKS) == 4
    assert MARKS["nothing_found"] != MARKS["not_checked"]
    red = {name for name, (color, _shape) in MARKS.items() if t.is_red(getattr(t, color))}
    assert red == {"needs_repair", "possible"}


def test_clicking_a_part_selects_it_through_the_log(qtbot, page, state):
    click(qtbot, page.parts_panel.rows[1])
    assert state.log.selected_part() == MOTOR_MOUNT
    assert page.log_panel.title() == "Motor mount"
    assert page.log_panel.hint() == "mount"
    assert [r.row.selected for r in page.parts_panel.rows] == [False, True, False, False, False]
    click(qtbot, page.parts_panel.rows[4])
    assert state.log.selected_part() is None
    assert page.log_panel.title() == "Whole machine"


def test_the_page_follows_a_selection_made_elsewhere(page, state):
    state.log.select_part(DRIVE_ROLLER)  # as the Machine page's "Open log" does
    assert page.log_panel.title() == "Drive roller"
    assert page.state_mark.state() == "needs_repair"
    assert table_texts(page, "kind") == ["Review", "Finding"]


def test_show_on_machine_selects_and_asks_for_the_machine_page(qtbot, page, state):
    assert not page.machine_button.isVisible()  # the whole machine has no pin to show
    state.log.select_part(BELT)
    with qtbot.waitSignal(page.navigate) as asked:
        click(qtbot, page.machine_button)
    assert asked.args == ["machine"]
    assert state.log.selected_part() == BELT


def test_a_part_state_change_redraws(page, state):
    state.log.select_part(BELT)
    state.log.set_part_state(BELT, "possible", since="2026-11-14T16:00:08Z", reason="alarm open")
    assert page.state_line.text().startswith("Possible since ")
    assert page.parts_panel.rows[2].mark.state() == "possible"


def test_another_machine_has_its_own_empty_log(page, state):
    state.log.select_part(DRIVE_ROLLER)
    state.set_machine("some-other-machine")
    assert page.parts_panel.rows == []
    assert page.log_panel.title() == "Whole machine"
    assert not page.table.isVisible()
    assert page.empty.isVisible()


# ---- the table ----------------------------------------------------------------


def test_whole_machine_lists_every_entry_newest_first(page):
    assert table_texts(page, "kind") == ["Note", "Review", "Finding", "Inspection", "Repair", "Note"]
    assert table_texts(page, "part") == ["Motor mount", "Drive roller", "Drive roller", "Belt", "Belt",
                                         "Whole machine"]
    assert not page.table.isColumnHidden(PART)
    assert page.count.text() == "6 entries"


def test_a_part_lists_only_its_entries(page, state):
    state.log.select_part(BELT)
    assert table_texts(page, "text") == ["tension checked after the new belt", "belt replaced"]
    assert table_texts(page, "needs_repair") == ["no", view.DASH]
    assert page.table.isColumnHidden(PART)
    assert page.count.text() == "2 entries"


def test_the_finding_from_an_alarm_answer_shows_its_alarm(page, state):
    state.log.select_part(DRIVE_ROLLER)
    assert table_texts(page, "alarm") == [view.DASH, ANSWERED_ALARM]
    assert table_texts(page, "needs_repair") == [view.DASH, "yes"]
    assert table_texts(page, "tag") == ["simulated", "simulated"]
    assert table_texts(page, "by")[0] == "part review agent"


def test_filter_by_kind(qtbot, page):
    assert page.kind_filter.keys() == ["all", "finding", "inspection", "repair", "note", "review"]
    click(qtbot, page.kind_filter.button("note"))
    assert table_texts(page, "kind") == ["Note", "Note"]
    assert page.count.text() == "2 note entries"
    click(qtbot, page.kind_filter.button("all"))
    assert len(table_texts(page, "kind")) == 6


def test_a_filter_with_no_match_says_so(qtbot, page, state):
    state.log.select_part(BELT)
    click(qtbot, page.kind_filter.button("review"))
    assert not page.table.isVisible()
    assert page.empty.text() == "No review entries for Belt."


def test_no_entries_yet(page, state):
    state.log.select_part(IDLE_ROLLER)
    assert not page.table.isVisible()
    assert page.empty.isVisible()
    assert page.empty.text().startswith("No entries yet for Idle roller.")
    assert page.count.text() == "0 entries"


def test_simulated_chip_follows_the_entries_shown(page, state):
    assert page.sim_chip.isVisible()
    state.log.select_part(BELT)  # typed by a person, about no alarm
    assert not page.sim_chip.isVisible()
    state.log.select_part(DRIVE_ROLLER)
    assert page.sim_chip.isVisible()


# ---- add entry ----------------------------------------------------------------


def test_a_note_is_typing_and_enter(qtbot, page, state):
    state.log.select_part(BELT)
    assert page.compose.kind.current() == "note"
    assert not page.compose.add_button.isEnabled()
    assert not page.compose.needs.isVisible()
    qtbot.keyClicks(page.compose.text, "belt runs true")
    qtbot.keyClick(page.compose.text, Qt.Key.Key_Return)
    newest = added(state)
    assert (newest["kind"], newest["text"], newest["part_id"], newest["needs_repair"], newest["source"]) == (
        "note", "belt runs true", BELT, None, "person")
    assert newest["by"] == state.answerer()
    assert page.compose.text.text() == ""
    assert "belt runs true" in table_texts(page, "text")


def test_a_finding_is_two_clicks_and_must_say_if_it_needs_repair(qtbot, page, state):
    state.log.select_part(MOTOR_MOUNT)
    click(qtbot, page.compose.kind.button("finding"))  # click one
    assert page.compose.needs.isVisible()
    assert page.compose.needs.current() is None  # nothing chosen for the person
    qtbot.keyClicks(page.compose.text, "bolt loose")
    assert not page.compose.add_button.isEnabled()
    qtbot.keyClick(page.compose.text, Qt.Key.Key_Return)
    assert len(state.log.entries(MOTOR_MOUNT)) == 1  # not added yet
    click(qtbot, page.compose.needs.button("yes"))  # click two
    assert page.compose.add_button.isEnabled()
    qtbot.keyClick(page.compose.text, Qt.Key.Key_Return)
    newest = added(state)
    assert (newest["kind"], newest["text"], newest["needs_repair"]) == ("finding", "bolt loose", True)
    assert page.compose.kind.current() == "note"  # back to the quickest kind
    assert page.compose.needs.current() is None


def test_an_inspection_may_have_no_text(qtbot, page, state):
    state.log.select_part(BELT)
    click(qtbot, page.compose.kind.button("inspection"))
    click(qtbot, page.compose.needs.button("no"))
    click(qtbot, page.compose.add_button)
    newest = added(state)
    assert (newest["kind"], newest["text"], newest["needs_repair"]) == ("inspection", "", False)


def test_a_repair_needs_text_and_no_answer(qtbot, page, state):
    state.log.select_part(DRIVE_ROLLER)
    click(qtbot, page.compose.kind.button("repair"))
    assert not page.compose.needs.isVisible()
    assert not page.compose.add_button.isEnabled()
    qtbot.keyClicks(page.compose.text, "weight removed")
    click(qtbot, page.compose.add_button)
    newest = added(state)
    assert (newest["kind"], newest["needs_repair"]) == ("repair", None)


def test_an_entry_about_the_whole_machine_has_no_part(qtbot, page, state):
    qtbot.keyClicks(page.compose.text, "moved to line 2")
    qtbot.keyClick(page.compose.text, Qt.Key.Key_Return)
    assert added(state)["part_id"] is None
    assert page.compose.text.placeholderText() == "Note about the whole machine"


def test_a_failed_add_keeps_what_was_typed(qtbot, page, state, monkeypatch):
    monkeypatch.setattr(state.log, "add_entry",
                        lambda *a, **k: state.action_failed.emit("Entry not saved: the receiver did not answer"))
    qtbot.keyClicks(page.compose.text, "do not lose me")
    qtbot.keyClick(page.compose.text, Qt.Key.Key_Return)
    assert page.compose.text.text() == "do not lose me"
    state.log.entries_changed.emit()  # someone else's entry arrives: still not ours to clear
    assert page.compose.text.text() == "do not lose me"


# ---- alarms waiting -------------------------------------------------------------


def test_waiting_alarms_show_their_suspects_as_possible(page):
    rows = page.alarms_panel.rows
    assert [r.row.alarm_id for r in rows] == [OPEN_ALARM]  # alarm-5 already names a part
    assert rows[0].title.text() == "Unrecognised pattern"
    assert rows[0].suspects.text() == "possible: Motor mount"
    assert rows[0].when.text().endswith("still going · simulated")
    assert not page.alarms_panel.form.isVisible()


def test_answering_asks_for_verdict_part_and_finding(qtbot, page, state):
    panel = page.alarms_panel
    click(qtbot, panel.rows[0].button)
    assert panel.form.isVisible()
    assert [panel.part.itemText(i) for i in range(panel.part.count())] == [
        "Motor mount (possible)", "Drive roller", "Belt", "Idle roller"]
    assert panel.part.currentIndex() == -1  # the lone suspect is not picked for the person
    assert panel.verdict.current() is None
    assert not panel.save.isEnabled()
    assert panel.problem.text() == "Say whether it was a real problem."
    click(qtbot, panel.verdict.button("real"))
    assert not panel.save.isEnabled()
    assert panel.problem.text() == "Pick the part."
    panel.part.setCurrentIndex(0)
    assert panel.save.isEnabled()
    qtbot.keyClicks(panel.finding, "mount bolt loose")
    click(qtbot, panel.save)

    newest = added(state)
    assert (newest["kind"], newest["text"], newest["needs_repair"], newest["alarm_id"], newest["source"]) == (
        "finding", "mount bolt loose", True, OPEN_ALARM, "alarm_answer")
    answered = next(a for a in state.alarms() if a["alarm_id"] == OPEN_ALARM)
    assert answered["answer"]["verdict"] == "real"
    assert panel.rows == []  # no longer waiting
    assert not panel.form.isVisible()
    assert panel.finding.text() == ""


def test_a_false_alarm_never_needs_repair(qtbot, page, state):
    panel = page.alarms_panel
    click(qtbot, panel.rows[0].button)
    click(qtbot, panel.verdict.button("false"))
    panel.part.setCurrentIndex(panel.part.findData(BELT))
    click(qtbot, panel.save)
    newest = added(state)
    assert (newest["source"], newest["needs_repair"], newest["text"]) == ("alarm_answer", False, "")


def test_a_half_typed_answer_survives_new_records(qtbot, page, state):
    panel = page.alarms_panel
    click(qtbot, panel.rows[0].button)
    click(qtbot, panel.verdict.button("real"))
    panel.part.setCurrentIndex(1)
    qtbot.keyClicks(panel.finding, "half typed")
    for signal in (state.alarms_changed, state.snapshot_changed, state.log.parts_changed, state.log.entries_changed):
        signal.emit()
    assert panel.form.isVisible()
    assert (panel.verdict.current(), panel.part.currentData(), panel.finding.text()) == (
        "real", DRIVE_ROLLER, "half typed")


def test_cancel_puts_the_answer_form_away(qtbot, page):
    panel = page.alarms_panel
    click(qtbot, panel.rows[0].button)
    qtbot.keyClicks(panel.finding, "never mind")
    click(qtbot, panel.cancel)
    assert not panel.form.isVisible()
    click(qtbot, panel.rows[0].button)
    assert panel.finding.text() == ""


def test_no_alarm_waiting(page, state):
    state.log.answer_alarm_for_part(OPEN_ALARM, "real", MOTOR_MOUNT)
    labels = [w.text() for w in page.alarms_panel.area.widget().findChildren(QLabel)]
    assert labels == [view.NO_ALARMS]


def test_the_live_pages_answer_is_left_alone(page, state):
    """Answering here goes through state.log only; the state's own answer_alarm keeps its meaning."""
    before = len(state.log.entries())
    state.answer_alarm(OPEN_ALARM, "real")  # as the Live page does: no part named
    assert len(state.log.entries()) == before
    assert [r.row.alarm_id for r in page.alarms_panel.rows] == [OPEN_ALARM]  # still waiting for a part


# ---- the review -----------------------------------------------------------------


def test_review_is_shown_as_advice_with_what_it_cited(page, state):
    state.log.select_part(DRIVE_ROLLER)
    panel = page.review_panel
    assert panel.message.text() == view.ADVICE_NOTE
    assert panel.rows.text("Needs repair") == "Yes (advice)"
    assert panel.rows.text("Cited") == f"{ANSWERED_ALARM}, entry-000004"
    assert panel.rows.text("What").startswith("Stand-in review:")
    assert panel.rows.text("Written").endswith("by stand-in (no model call)")
    assert panel.sim_chip.isVisible()
    assert panel.agree_button.isVisible()
    assert panel.run_button.text() == view.RUN_REVIEW


def test_agree_fills_the_form_and_adds_nothing(qtbot, page, state):
    state.log.select_part(DRIVE_ROLLER)
    before = len(state.log.entries())
    click(qtbot, page.review_panel.agree_button)
    assert len(state.log.entries()) == before  # nothing without the person's own click
    assert page.compose.kind.current() == "finding"
    assert page.compose.text.text().startswith("Stand-in review: possible unbalance")
    assert page.compose.needs.current() == "yes"
    page.compose.text.setText("unbalance confirmed by eye")  # the person edits it
    click(qtbot, page.compose.add_button)
    newest = added(state)
    assert (newest["kind"], newest["text"], newest["needs_repair"], newest["source"], newest["by"]) == (
        "finding", "unbalance confirmed by eye", True, "person", state.answerer())
    assert len(state.log.entries()) == before + 1


def test_agreeing_with_not_sure_leaves_the_choice_open(qtbot, page, state):
    state.log.select_part(DRIVE_ROLLER)
    state.log._reviews[-1]["review"]["needs_repair"] = "not_sure"
    state.log.reviews_changed.emit()
    assert page.review_panel.rows.text("Needs repair") == "Not sure (advice)"
    click(qtbot, page.review_panel.agree_button)
    assert page.compose.needs.current() is None
    assert not page.compose.add_button.isEnabled()


def test_no_review_yet(page, state):
    state.log.select_part(BELT)
    panel = page.review_panel
    assert panel.message.text().startswith("No review yet for Belt.")
    assert not panel.rows.isVisible()
    assert not panel.agree_button.isVisible()
    assert not panel.sim_chip.isVisible()
    assert panel.run_button.isEnabled()


def test_run_part_review_in_the_chosen_mode(qtbot, page, state):
    state.log.select_part(BELT)
    panel = page.review_panel
    assert panel.mode.keys() == ["standin", "local"]
    click(qtbot, panel.run_button)
    assert state.log.started_reviews == [(BELT, "standin")]
    assert not panel.run_button.isEnabled()  # one run per part at a time
    assert panel.run_button.text() == view.REVIEWING
    assert panel.message.text() == "Reviewing Belt…"
    state.log.select_part(MOTOR_MOUNT)  # another part is free to be reviewed
    assert panel.run_button.isEnabled()
    click(qtbot, panel.mode.button("local"))
    click(qtbot, panel.run_button)
    assert state.log.started_reviews[-1] == (MOTOR_MOUNT, "local")
    state.log.finish_review()
    assert panel.run_button.isEnabled()
    assert panel.run_button.text() == view.RUN_REVIEW


def test_the_last_review_stays_while_the_next_runs(qtbot, page, state):
    state.log.select_part(DRIVE_ROLLER)
    click(qtbot, page.review_panel.run_button)
    panel = page.review_panel
    assert panel.message.text() == "Reviewing Drive roller… The review below is the previous one."
    assert panel.rows.isVisible()
    assert not panel.agree_button.isVisible()


def test_a_discarded_review_says_why(page, state):
    state.log.select_part(DRIVE_ROLLER)
    state.log._reviews.append(dict(state.log._reviews[-1], ok=False, review=None,
                                   reason="it cited an alarm it was not given"))
    state.log.reviews_changed.emit()
    panel = page.review_panel
    assert "discarded: it cited an alarm it was not given" in panel.message.text()
    assert not panel.rows.isVisible()
    assert not panel.agree_button.isVisible()
    assert panel.run_button.isEnabled()  # it can be run again


def test_the_whole_machine_reviews_every_part(qtbot, page, state):
    panel = page.review_panel
    assert panel.run_button.text() == view.RUN_ALL
    assert panel.message.text().startswith("Select a part to read its review.")
    click(qtbot, panel.run_button)
    assert state.log.started_reviews == [(None, "standin")]
    assert not panel.run_button.isEnabled()
    assert panel.run_button.text() == view.REVIEWING


# ---- no parts yet ---------------------------------------------------------------


def test_no_parts_points_to_the_machine_page_and_offers_a_part_with_no_pin(qtbot, page, state):
    state.log.clear()
    panel = page.parts_panel
    assert panel.rows == []
    assert panel.empty.isVisible()
    assert panel.empty_text.text() == view.NO_PARTS
    assert panel.form.isVisible()  # no extra click to start
    assert not panel.submit_button.isEnabled()
    assert not page.review_panel.run_button.isEnabled()
    assert "No parts yet" in page.review_panel.message.text()
    with qtbot.waitSignal(page.navigate) as asked:
        click(qtbot, panel.machine_button)
    assert asked.args == ["machine"]

    qtbot.keyClicks(panel.name, "Gearbox")
    panel.kind.setCurrentIndex(panel.kind.findData("gearbox"))
    qtbot.keyClick(panel.name, Qt.Key.Key_Return)
    made = state.log.parts()
    assert [(p["name"], p["kind"], p["position"]) for p in made] == [("Gearbox", "gearbox", None)]
    assert [r.name.text() for r in panel.rows] == ["Gearbox", "Whole machine"]
    assert panel.rows[0].line.text() == "Not checked, no pin"
    assert not panel.empty.isVisible()
    assert not panel.form.isVisible()
    assert panel.name.text() == ""


def test_add_part_beside_existing_parts(qtbot, page, state):
    panel = page.parts_panel
    assert not panel.form.isVisible()
    click(qtbot, panel.add_button)
    assert panel.form.isVisible()
    assert [panel.kind.itemData(i) for i in range(panel.kind.count())] == list(view.PART_KINDS)
    qtbot.keyClicks(panel.name, "Tail bearing")
    panel.kind.setCurrentIndex(panel.kind.findData("bearing"))
    click(qtbot, panel.submit_button)
    assert state.log.parts()[-1]["name"] == "Tail bearing"
    assert not panel.form.isVisible()


def test_no_machine_at_all(page, state):
    state.log.clear()
    state.current_machine = lambda: None
    page.refresh()
    assert page.parts_panel.empty_text.text() == view.NO_MACHINE
    assert not page.parts_panel.form.isVisible()
    assert not page.parts_panel.machine_button.isVisible()
    assert not page.compose.isEnabled()
    assert not page.export_button.isEnabled()
    assert page.empty.text() == view.NO_MACHINE


# ---- export -----------------------------------------------------------------------


def test_export_csv_writes_the_log(qtbot, page, state, tmp_path, monkeypatch):
    asked = []
    out = tmp_path / "log.csv"

    def pick(parent, title, name, kinds):
        asked.append((title, name, kinds))
        return str(out), kinds

    monkeypatch.setattr(QFileDialog, "getSaveFileName", pick)
    click(qtbot, page.export_button)
    assert asked == [("Export CSV", f"{state.current_machine()}-log.csv", "CSV (*.csv)")]
    assert state.log.exported == [str(out)]
    with open(out, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    assert rows[0] == ["at", "part", "kind", "text", "by", "needs_repair", "alarm_id", "diagnosis", "source",
                       "entry_id"]
    assert len(rows) == 7


def test_a_cancelled_export_writes_nothing(page, state, monkeypatch):
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: ("", ""))
    page.export_dialog()
    assert state.log.exported == []


def test_ctrl_e_in_the_window_exports_from_the_log_page(qtbot, state, tmp_path, monkeypatch):
    from app.shell import MainWindow

    out = tmp_path / "from-shortcut.csv"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (str(out), ""))
    w = MainWindow(state, receiver_address="0.0.0.0:8000")
    qtbot.addWidget(w)
    w.show()
    w.navigate("log")
    assert isinstance(w.current_page(), LogPage)
    w.export_page()  # what Ctrl+E calls
    assert state.log.exported == [str(out)]
    assert out.exists()


# ---- the RED rule -----------------------------------------------------------------


def test_no_red_text(qtbot, qapp, state, monkeypatch):
    from app.theme import apply_theme

    apply_theme(qapp)
    seen = []
    real = QPainter.drawText

    def spy(self, *args):
        seen.append((self.pen().color(), args[-1] if isinstance(args[-1], str) else args))
        return real(self, *args)

    monkeypatch.setattr(QPainter, "drawText", spy)
    p = LogPage()
    qtbot.addWidget(p)
    p.resize(*PAGE_SIZE)
    p.bind(state)
    p.show()
    qtbot.waitExposed(p)
    for part_id in (None, DRIVE_ROLLER, MOTOR_MOUNT, BELT, IDLE_ROLLER):
        state.log.select_part(part_id)
        p.grab()
    p.alarms_panel.open_form(OPEN_ALARM)
    p.grab()
    assert seen, "no text was painted"
    assert [text for color, text in seen if t.is_red(color)] == []
    for w in p.findChildren(QLabel):
        assert not t.is_red(w.palette().color(w.foregroundRole())), w.text()
