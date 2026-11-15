"""The Machine page on FakeState: every panel, every action and every state it has to draw.

In offscreen mode the real 3D view draws nothing and no click hits, so these tests give the page a
stand-in view with the same signals and calls. What the real view draws is checked by hand on a real
screen (see shoot.py and the PR).
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from app.pages.machine import MachinePage, logic, side, stage
from app.pages.machine.marks import PinMark, Steps
from app.theme import tokens as t
from fake_partlog import BELT, DRIVE_ROLLER, IDLE_ROLLER, MOTOR_MOUNT
from fake_scans import SCAN_ID
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QFileDialog, QLabel, QWidget

PAGE_SIZE = (1320, 896)  # the page area inside a 1600x1000 window
NOW = datetime(2026, 11, 14, 18, 7, tzinfo=UTC)
POINT = (0.05, 0.11, 0.09)


class FakeView(QWidget):
    """view3d.ModelView's interface, with nothing drawn."""

    picked = Signal(float, float, float)
    missed = Signal()
    pin_clicked = Signal(str)
    status_changed = Signal(str)

    def __init__(self, problem: str = ""):
        super().__init__()
        self._problem = problem
        self._status = "empty"
        self.loaded: list = []
        self.pins: list = []
        self.colours = None
        self.resets = 0

    def load(self, path, box=None) -> None:
        self.loaded.append((str(path), box))
        self._status = "ready"

    def set_pins(self, pins) -> None:
        self.pins = [dict(p) for p in pins]

    def set_colours(self, *colours) -> None:
        self.colours = colours

    def reset_camera(self) -> None:
        self.resets += 1

    def status(self) -> str:
        return self._status

    def problem(self) -> str:
        return self._problem

    def fail(self, problem: str) -> None:
        self._problem, self._status = problem, "error"
        self.status_changed.emit("error")


@pytest.fixture
def state():
    from fakes import FakeState

    s = FakeState()
    s._now = NOW.timestamp() * 1000.0  # the fake's clock is the day the tests run: pin it
    return s


def make_page(qtbot, state, *, can_draw=True, make_view=FakeView):
    p = MachinePage(make_view=make_view, can_draw=lambda: can_draw)
    qtbot.addWidget(p)
    p.resize(*PAGE_SIZE)
    p.bind(state)
    p.show()
    return p


@pytest.fixture
def page(qtbot, state):
    return make_page(qtbot, state)


@pytest.fixture
def failures(state):
    seen = []
    state.action_failed.connect(seen.append)
    return seen


def click(qtbot, widget):
    qtbot.mouseClick(widget, Qt.MouseButton.LeftButton)


def row(page, part_id):
    return next(r for r in page.parts_panel.rows if r.row["part_id"] == part_id)


def pin(page, part_id):
    return next(p for p in page.view.pins if p["id"] == part_id)


def with_convert_again(state) -> list:
    """`convert_again` is not in FakeScans yet (INTERFACES.md addition 18 has no such action)."""
    asked = []
    state.scans.convert_again = asked.append
    return asked


# ---- the shell finds it, and the two pages stay apart ----------------------------------------------------


def test_the_shell_builds_this_page_for_the_machine_key():
    from app.shell.pages import find_page_class

    assert find_page_class("machine") is MachinePage
    assert (MachinePage.key, MachinePage.title) == ("machine", "Machine")


def test_the_page_never_imports_the_log_page():
    import subprocess

    code = ("import sys; sys.path[:0] = %r; import app.pages.machine; "
            "sys.exit(any(m.startswith('app.pages.log') for m in sys.modules))" % [p for p in sys.path if p])
    assert subprocess.run([sys.executable, "-c", code], check=False).returncode == 0


def test_the_page_opens_with_no_state_and_with_the_stub_stores(qtbot):
    """The real `state.scans` is a stub until built: no scan, and no pairing line."""
    from app.data.partlog import PartLog
    from app.data.scans import Scans
    from fakes import FakeState

    state = FakeState()
    state.scans, state.log = Scans(state), PartLog(state)
    p = make_page(qtbot, state)
    assert p.view_state["name"] == "no_scan" and p.view is None
    assert p.scan_panel.isVisible() and not p.scan_panel.code.isVisible()
    assert p.scan_panel.note.text() == logic.NO_QR_TEXT
    assert p.scan_panel.import_button.isVisible()


# ---- ready: the newest scan, with a pin on each part ------------------------------------------------------


def test_the_newest_scan_is_loaded_with_its_bounds(page, state):
    scan = state.scans.current()
    assert page.view_state["name"] == "ready" and page.stage.model_shown()
    assert page.view.loaded == [(state.scans.model_path(SCAN_ID), scan["model"]["bounds"])]
    assert page.stage.hint() == logic.scan_when(scan)
    assert "box.glb" in page.stage.hint_label.toolTip() and "scanned" in page.stage.hint_label.toolTip()
    assert page.stage.reset_button.isVisible()


def test_each_part_with_a_position_has_a_pin_coloured_by_the_logic(page):
    assert {p["id"] for p in page.view.pins} == {DRIVE_ROLLER, MOTOR_MOUNT, BELT}  # the idle roller has none
    palette = {"red": t.RED, "text": t.TEXT, "muted": t.MUTED, "bg": t.PANEL}
    for part_id, name in ((DRIVE_ROLLER, "needs_repair"), (MOTOR_MOUNT, "possible"), (BELT, "nothing_found")):
        look = logic.pin_look(name, palette)
        assert (pin(page, part_id)["colour"], pin(page, part_id)["ring"]) == (look["colour"], look["ring"])
    assert (pin(page, DRIVE_ROLLER)["x"], pin(page, DRIVE_ROLLER)["y"], pin(page, DRIVE_ROLLER)["z"]) == (0.18, 0.06, 0.0)


def test_the_list_marks_are_the_pins_looks(page):
    for part_id in (DRIVE_ROLLER, MOTOR_MOUNT, BELT):
        look = row(page, part_id).mark.look()
        assert (look["colour"], look["ring"]) == (pin(page, part_id)["colour"], pin(page, part_id)["ring"])


def test_nothing_found_reads_as_nothing_found_never_as_healthy(page, state):
    assert row(page, BELT).line.text() == "Nothing found"
    state.log.select_part(BELT)
    assert page.part_column.head.headline.text() == "Nothing found"
    assert page.part_column.head.headline.toolTip().startswith("Nothing found since ")
    assert "not a check of the part itself" in page.part_column.head.means.text()
    words = " ".join(w.text().lower() for w in page.findChildren(QLabel))
    for wrong in ("healthy", "good", "fine", " ok", "no problem"):
        assert wrong not in words
    for part_id in (BELT, IDLE_ROLLER):  # and neither is drawn in red or any other colour of its own
        look = row(page, part_id).mark.look()
        assert not t.is_red(look["colour"]) and not t.is_red(look["ring"])


def test_the_model_is_loaded_once_however_often_the_page_redraws(page, state):
    for _ in range(3):
        state.snapshot_changed.emit()
        state.log.parts_changed.emit()
    assert len(page.view.loaded) == 1


def test_reset_view_puts_the_camera_back(qtbot, page):
    click(qtbot, page.stage.reset_button)
    assert page.view.resets == 1


# ---- selecting ---------------------------------------------------------------------------------------------


def test_a_click_on_a_pin_selects_that_part_through_the_log(page, state):
    page.view.pin_clicked.emit(MOTOR_MOUNT)
    assert state.log.selected_part() == MOTOR_MOUNT
    assert pin(page, MOTOR_MOUNT)["selected"] and page.view.pins[-1]["id"] == MOTOR_MOUNT  # drawn on top
    assert row(page, MOTOR_MOUNT).row["selected"]
    assert page.part_column.head.name.text() == "Motor mount"


def test_a_click_on_a_row_selects_that_part(qtbot, page, state):
    click(qtbot, row(page, BELT))
    assert state.log.selected_part() == BELT


def test_the_page_follows_a_selection_made_elsewhere(page, state):
    state.log.select_part(DRIVE_ROLLER)  # as the Log page's "Show on machine" does
    assert pin(page, DRIVE_ROLLER)["selected"]
    assert page.part_column.head.name.text() == "Drive roller"
    assert "Needs repair since" in page.part_column.head.headline.toolTip()


def test_a_click_beside_the_model_clears_the_selection(page, state):
    state.log.select_part(DRIVE_ROLLER)
    page.view.missed.emit()
    assert state.log.selected_part() is None
    assert page.part_column.blank.isVisible() and page.part_column.blank.title.text() == "Pick a part"


def test_a_click_on_the_model_with_no_mode_changes_nothing(page, state):
    before = len(state.log.parts())
    page.view.picked.emit(*POINT)
    assert len(state.log.parts()) == before and not page.stage.form.isVisible()


def test_the_log_column_lists_the_parts_entries_and_marks_simulated_ones(page, state):
    state.log.select_part(DRIVE_ROLLER)
    texts = [w.text() for w in page.log_panel.findChildren(QLabel) if w.isVisibleTo(page.log_panel)]
    assert "added weight found on the drive roller" in texts
    finding = next(r for r in page.log_panel.rows if "Finding" in r.head.text())
    assert finding.head.text().endswith("· simulated")  # that finding is about a simulated alarm
    state.log.select_part(BELT)
    assert not any("simulated" in r.head.text() for r in page.log_panel.rows)
    assert [r.head.text().split(" · ")[1] for r in page.log_panel.rows] == ["Inspection", "Repair"]


def test_open_log_selects_the_part_and_asks_for_the_log_page(qtbot, page, state):
    state.log.select_part(MOTOR_MOUNT)
    with qtbot.waitSignal(page.navigate) as asked:
        click(qtbot, page.log_panel.log_button)
    assert asked.args == ["log"] and state.log.selected_part() == MOTOR_MOUNT


# ---- adding a part -----------------------------------------------------------------------------------------


def test_add_part_then_a_click_on_the_model_makes_a_part_at_that_point(qtbot, page, state):
    click(qtbot, page.parts_panel.add_button)
    assert page.mode == logic.adding()
    assert page.stage.hint_text.text() == "Click the model where the new part is. Esc cancels."
    page.view.picked.emit(*POINT)
    assert page.stage.form.isVisible() and page.stage.form.note.text() == stage.PINNED
    assert page.view.pins[-1]["label"] == "New part" and page.view.pins[-1]["selected"]
    assert not page.stage.form.add_button.isEnabled()  # no name yet

    qtbot.keyClicks(page.stage.form.name, "Guard plate")
    page.stage.form.kind.setCurrentIndex(page.stage.form.kind.findData("frame"))
    click(qtbot, page.stage.form.add_button)
    new = state.log.parts()[-1]
    assert (new["name"], new["kind"]) == ("Guard plate", "frame")
    assert new["position"] == {"scan_id": SCAN_ID, "at": list(POINT)}
    assert state.log.selected_part() == new["part_id"]  # the new part is shown
    assert not page.stage.form.isVisible() and page.stage.form.name.text() == ""
    assert pin(page, new["part_id"])["selected"]
    assert all(p["id"] for p in page.view.pins)  # the nameless pin is gone


def test_enter_in_the_name_adds_the_part(qtbot, page, state):
    page.add_part()
    page.view.picked.emit(*POINT)
    qtbot.keyClicks(page.stage.form.name, "Guard plate")
    qtbot.keyClick(page.stage.form.name, Qt.Key.Key_Return)
    assert state.log.parts()[-1]["name"] == "Guard plate"


def test_a_click_that_misses_while_adding_says_so_and_keeps_waiting(page, state):
    page.add_part()
    page.view.missed.emit()
    assert page.mode == logic.adding()
    assert page.stage.hint_text.text() == "That click missed the model. Click on the model itself."
    page.view.picked.emit(*POINT)
    assert page.stage.form.isVisible()


def test_cancel_and_esc_leave_adding(qtbot, page, state):
    before = len(state.log.parts())
    page.add_part()
    click(qtbot, page.stage.hint_cancel)
    assert page.mode == logic.IDLE and not page.stage.hint_strip.isVisible()

    page.add_part()
    page.view.picked.emit(*POINT)
    qtbot.keyClicks(page.stage.form.name, "Half typed")
    qtbot.keyClick(page.stage.form.name, Qt.Key.Key_Escape)
    assert not page.stage.form.isVisible() and page.stage.form.name.text() == ""
    assert len(state.log.parts()) == before
    assert all(p["id"] for p in page.view.pins)


def test_esc_cancels_inside_the_window_whose_own_esc_shortcut_would_take_the_key(qtbot, state):
    from app.shell import MainWindow

    w = MainWindow(state, receiver_address="0.0.0.0:8000")
    qtbot.addWidget(w)
    w.show()
    w.navigate("machine")
    p = w.pages["machine"]
    p._can_draw, p._make_view = (lambda: True), FakeView
    p.refresh()
    qtbot.waitExposed(w)
    w.activateWindow()
    click(qtbot, p.parts_panel.add_button)
    assert p.mode == logic.adding()
    qtbot.keyClick(w.focusWidget() or p, Qt.Key.Key_Escape)
    assert p.mode == logic.IDLE and w.current == "machine"


def test_a_failed_create_keeps_what_was_typed(qtbot, page, state, failures):
    def refuse(name, kind, position=None):
        state.action_failed.emit("Part not added: the receiver did not answer")

    state.log.create_part = refuse
    page.add_part()
    page.view.picked.emit(*POINT)
    qtbot.keyClicks(page.stage.form.name, "Guard plate")
    click(qtbot, page.stage.form.add_button)
    assert failures and page.stage.form.isVisible() and page.stage.form.name.text() == "Guard plate"
    state.log.parts_changed.emit()  # some other part moved: the form is still not put away
    assert page.stage.form.isVisible()


def test_a_click_on_a_pin_ends_adding(page, state):
    page.add_part()
    page.view.pin_clicked.emit(BELT)
    assert page.mode == logic.IDLE and state.log.selected_part() == BELT


# ---- moving and placing a pin -----------------------------------------------------------------------------


def test_a_part_with_no_pin_can_be_placed_from_its_row(qtbot, page, state):
    assert row(page, IDLE_ROLLER).line.text() == "Not checked, no pin"
    assert row(page, DRIVE_ROLLER).place_button is None
    click(qtbot, row(page, IDLE_ROLLER).place_button)
    assert page.mode == logic.placing(IDLE_ROLLER)
    assert page.stage.hint_text.text() == "Click the model where Idle roller is. Esc cancels."
    page.view.picked.emit(*POINT)
    assert state.log.part(IDLE_ROLLER)["position"] == {"scan_id": SCAN_ID, "at": list(POINT)}
    assert page.mode == logic.IDLE and pin(page, IDLE_ROLLER)["selected"]
    assert row(page, IDLE_ROLLER).place_button is None


def test_move_pin_moves_the_selected_parts_pin(qtbot, page, state):
    state.log.select_part(DRIVE_ROLLER)
    assert page.part_column.head.pin_button.text() == "Move pin"
    click(qtbot, page.part_column.head.pin_button)
    page.view.picked.emit(*POINT)
    assert state.log.part(DRIVE_ROLLER)["position"]["at"] == list(POINT)


def test_a_part_pinned_on_an_older_scan_says_so_and_can_be_placed(page, state):
    state.log.update_part(BELT, position={"scan_id": "scan-20261101090000-aaaa", "at": [0.0, 0.1, 0.0]})
    assert row(page, BELT).line.text() == "Nothing found, no pin on this scan"
    assert row(page, BELT).place_button is not None
    assert BELT not in {p["id"] for p in page.view.pins}
    state.log.select_part(BELT)
    assert "older scan" in page.part_column.head.means.text()
    assert page.part_column.head.pin_button.text() == "Place pin"


# ---- the states with no model to show ----------------------------------------------------------------------


def test_no_scan_yet(qtbot, page, state, monkeypatch):
    state.scans.clear()
    assert page.view_state["name"] == "no_scan" and not page.stage.model_shown()
    assert page.stage.card.title.text() == "No scan yet"
    assert page.stage.card.button.text() == stage.IMPORT and not page.stage.card.steps.isVisible()
    assert page.scan_panel.isVisible()  # the way to get one is open, over the card
    assert not page.scan_panel.close_button.isVisible()
    assert len(page.parts_panel.rows) == 4  # the list works with no scan
    assert row(page, IDLE_ROLLER).line.text() == "Not checked" and row(page, IDLE_ROLLER).place_button is None
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: ("/tmp/scan.usdz", ""))
    click(qtbot, page.stage.card.button)
    assert state.scans.imported == ["/tmp/scan.usdz"]
    assert page.view_state["name"] == "ready"


def test_a_scan_waiting_to_be_converted(page, state):
    state.scans.set_status("waiting")
    assert page.view_state["name"] == "arriving"
    assert page.stage.card.title.text() == "Scan received"
    assert [s["mark"] for s in page.stage.card.steps.steps()] == ["done", "todo", "todo"]
    assert not page.stage.card.button.isVisible() and not page.stage.reset_button.isVisible()


def test_a_scan_being_converted(page, state):
    state.scans.set_status("converting")
    assert page.stage.card.title.text() == "Converting the scan"
    assert [s["mark"] for s in page.stage.card.steps.steps()] == ["done", "now", "todo"]
    assert not page.stage.card.button.isVisible()


def test_a_failed_conversion_shows_its_reason_and_convert_again(qtbot, page, state):
    asked = with_convert_again(state)
    state.scans.set_status("failed", "Blender is not installed on this laptop")
    assert page.stage.card.title.text() == "The scan could not be converted"
    assert page.stage.card.text.text() == "Blender is not installed on this laptop"
    assert [s["mark"] for s in page.stage.card.steps.steps()] == ["done", "failed", "todo"]
    assert page.stage.card.button.text() == stage.CONVERT_AGAIN
    click(qtbot, page.stage.card.button)
    assert asked == [SCAN_ID]


def test_convert_again_is_not_offered_where_the_store_cannot_do_it(page, state):
    state.scans.set_status("failed", "Blender is not installed on this laptop")
    assert not hasattr(state.scans, "convert_again")
    assert page.stage.card.text.text() == "Blender is not installed on this laptop"
    assert not page.stage.card.button.isVisible()


def test_the_model_shows_when_the_conversion_ends(page, state):
    state.scans.set_status("converting")
    state.scans.set_status("ready")
    assert page.view_state["name"] == "ready" and page.stage.model_shown()
    assert len(page.view.pins) == 3


def test_a_model_still_being_fetched(qtbot, state):
    state.scans.model_path = lambda scan_id: None
    p = make_page(qtbot, state)
    assert p.view_state["name"] == "fetching" and p.view is None
    assert [s["mark"] for s in p.stage.card.steps.steps()] == ["done", "done", "now"]
    del state.scans.model_path
    state.scans.model_ready.emit(SCAN_ID)
    assert p.view_state["name"] == "ready" and len(p.view.loaded) == 1


def test_a_laptop_whose_graphics_cannot_draw_3d_gets_the_parts_list(qtbot, state):
    made = []
    p = make_page(qtbot, state, can_draw=False, make_view=lambda: made.append(1))
    assert p.view_state["name"] == "cannot_draw" and not made  # no view is even tried
    assert p.stage.card.title.text() == "The 3D view cannot draw on this laptop"
    assert "parts list and the log work without it" in p.stage.card.text.text()
    assert len(p.parts_panel.rows) == 4 and row(p, IDLE_ROLLER).place_button is None
    state.log.select_part(DRIVE_ROLLER)
    assert p.part_column.head.name.text() == "Drive roller" and not p.part_column.head.pin_button.isVisible()


def test_a_view_that_cannot_start_says_why(qtbot, state):
    p = make_page(qtbot, state, make_view=lambda: FakeView("the 3D view could not start on this laptop (no driver)"))
    assert p.view_state["name"] == "cannot_draw" and p.view is None
    assert p.stage.card.text.text() == ("The 3D view could not start on this laptop (no driver). "
                                        "The parts list and the log work without it.")

    def broken():
        raise ImportError("No module named 'PySide6.QtQuickWidgets'")

    p = make_page(qtbot, state, make_view=broken)
    assert p.view_state["name"] == "cannot_draw" and "QtQuickWidgets" in p.stage.card.text.text()


def test_a_model_file_the_view_cannot_show_says_why_and_a_new_scan_is_tried(page, state):
    page.view.fail("the model could not be shown: not a glTF file")
    assert page.view_state["name"] == "cannot_draw"
    assert "not a glTF file" in page.stage.card.text.text()
    page.view._problem = ""
    scan = dict(state.scans.current(), scan_id="scan-20261114170000-next")
    state.scans._scans = [scan]
    state.scans.model_path = lambda scan_id: "/tmp/next.glb"
    state.scans.scans_changed.emit()
    assert page.view_state["name"] == "ready" and page.view.loaded[-1][0] == "/tmp/next.glb"


def test_add_part_with_no_model_lists_the_part_without_a_pin(qtbot, page, state):
    state.scans.set_status("failed", "Blender is not installed on this laptop")
    click(qtbot, page.parts_panel.add_button)
    assert page.mode == logic.IDLE and page.stage.form.isVisible()
    assert page.stage.form.note.text() == stage.NO_PIN
    qtbot.keyClicks(page.stage.form.name, "Gearbox")
    click(qtbot, page.stage.form.add_button)
    new = state.log.parts()[-1]
    assert (new["name"], new["position"]) == ("Gearbox", None)
    assert state.log.selected_part() == new["part_id"]


def test_no_parts_yet(page, state):
    state.log.clear()
    assert page.parts_panel.rows == []
    assert page.parts_panel.note.text() == "No parts yet. Press Add part, then click the model."
    assert page.view.pins == []
    state.scans.clear()
    assert page.parts_panel.note.text() == "No parts yet. Press Add part to list one without a pin."


def test_another_scan_drops_a_half_made_click(page, state):
    page.add_part()
    state.scans.set_status("converting")
    assert page.mode == logic.IDLE and not page.stage.hint_strip.isVisible()


def test_a_machine_with_nothing_shows_no_scan_and_no_parts(page, state):
    state.set_machine("press-02")
    assert page.view_state["name"] == "no_scan" and page.parts_panel.rows == []
    assert page.scan_panel.machine.text() == "press-02"


def test_a_machine_that_shows_up_with_its_first_record_is_followed(page, state, monkeypatch):
    monkeypatch.setattr(state, "current_machine", lambda: "press-02")
    state.record_added.emit({"machine_id": "press-02"})
    assert page.scan_panel.machine.text() == "press-02" and page.parts_panel.rows == []


# ---- the New scan panel ------------------------------------------------------------------------------------


def test_new_scan_shows_the_pairing_code_and_the_same_line_as_text(qtbot, page, state):
    assert not page.scan_panel.isVisible()
    click(qtbot, page.stage.scan_button)
    assert page.scan_panel.isVisible()
    line = state.scans.qr_text(state.current_machine())
    assert line.startswith("effente://scan?v=1&base=")
    assert page.scan_panel.code.text() == line and page.scan_panel.code.isVisible()
    assert page.scan_panel.line.toPlainText() == line
    assert page.scan_panel.line.isReadOnly()
    assert page.scan_panel.line.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse
    assert page.scan_panel.note.text() == side.SCAN_HOW
    click(qtbot, page.scan_panel.close_button)
    assert not page.scan_panel.isVisible()


def test_the_code_follows_the_laptop_onto_another_network(qtbot, page, state, monkeypatch):
    """The laptop changes WiFi while the app runs: the open panel shows the new line, with no click."""
    from app.pages.machine import page as page_module

    real = type(state.scans).qr_text
    monkeypatch.setattr(type(state.scans), "qr_text", lambda self, machine_id, name="": "")
    click(qtbot, page.stage.scan_button)
    assert not page.scan_panel.code.isVisible() and page.scan_panel.note.text() == logic.NO_QR_TEXT
    assert page._network_timer.isActive() and page._network_timer.interval() == page_module.NETWORK_CHECK_MS

    assert page.scan_panel.retry_button.isVisible()
    click(qtbot, page.scan_panel.retry_button)  # still no address: nothing changes
    assert page.scan_panel.note.text() == logic.NO_QR_TEXT and page.scan_panel.retry_button.isVisible()

    monkeypatch.setattr(type(state.scans), "qr_text", real)
    click(qtbot, page.scan_panel.retry_button)  # Try again finds it without waiting for the timer
    assert page.scan_panel.line.toPlainText() == real(state.scans, state.current_machine())
    assert page.scan_panel.retry_button.text() == side.REFRESH  # beside a code the same button refreshes it
    monkeypatch.setattr(type(state.scans), "qr_text", lambda self, machine_id, name="": "")
    page._network_timer.timeout.emit()
    assert page.scan_panel.retry_button.isVisible() and page.scan_panel.retry_button.text() == side.TRY_AGAIN

    monkeypatch.setattr(type(state.scans), "qr_text", real)
    page._network_timer.timeout.emit()
    line = state.scans.qr_text(state.current_machine())
    assert page.scan_panel.line.toPlainText() == line and page.scan_panel.line.isVisible()
    assert page.scan_panel.note.text() == side.SCAN_HOW

    monkeypatch.setattr(type(state.scans), "qr_text", lambda self, machine_id, name="": "")
    page._network_timer.timeout.emit()  # and off it again
    assert not page.scan_panel.line.isVisible() and page.scan_panel.note.text() == logic.NO_QR_TEXT
    page.hide()
    assert not page._network_timer.isActive()


def test_refresh_code_looks_again_and_says_when_the_code_is_the_same(qtbot, page, state, monkeypatch):
    """The phone failed to connect with a code that drew fine: the person can ask for the code again."""
    real = type(state.scans).qr_text
    click(qtbot, page.stage.scan_button)
    panel = page.scan_panel
    line = real(state.scans, state.current_machine())
    assert panel.code.isVisible() and panel.line.toPlainText() == line and panel.note.text() == side.SCAN_HOW
    assert panel.retry_button.isVisible() and panel.retry_button.text() == side.REFRESH
    assert panel.retry_button.toolTip() == side.REFRESH_NOTE

    click(qtbot, panel.retry_button)  # the same address: the same code, and the panel says so
    assert panel.line.toPlainText() == line and panel.note.text() == logic.SAME_CODE_TEXT
    page.refresh()  # the note stays while the line does
    assert panel.note.text() == logic.SAME_CODE_TEXT

    other = line.replace("machine=", "machine=x-")  # the laptop's address changed: a new code, the usual note
    monkeypatch.setattr(type(state.scans), "qr_text", lambda self, machine_id, name="", anyway=False: other)
    click(qtbot, panel.retry_button)
    assert panel.line.toPlainText() == other and panel.note.text() == side.SCAN_HOW
    monkeypatch.setattr(type(state.scans), "qr_text", real)
    page._network_timer.timeout.emit()  # back on the first line by itself: not said again
    assert panel.line.toPlainText() == line and panel.note.text() == side.SCAN_HOW

    panel.machine.setText("")  # no machine id: nothing to refresh
    panel.changed.emit()
    assert not panel.retry_button.isVisible()


def test_show_code_anyway_draws_a_code_the_phone_may_not_reach(qtbot, page, state, monkeypatch):
    """No address a phone reaches: the person can still ask for the code, and is told it may not work."""
    real = type(state.scans).qr_text
    monkeypatch.setattr(type(state.scans), "qr_text",
                        lambda self, machine_id, name="", anyway=False: real(self, machine_id, name) if anyway else "")
    click(qtbot, page.stage.scan_button)
    panel = page.scan_panel
    assert not panel.code.isVisible() and panel.note.text() == logic.NO_QR_TEXT
    assert panel.anyway_button.isVisible() and panel.anyway_button.text() == side.SHOW_ANYWAY

    click(qtbot, panel.anyway_button)
    line = real(state.scans, state.current_machine())
    assert panel.code.isVisible() and panel.line.isVisible() and panel.line.toPlainText() == line
    assert panel.note.text() == logic.CODE_ANYWAY_TEXT
    assert not panel.anyway_button.isVisible() and panel.retry_button.isVisible() and panel.copy_button.isVisible()
    page._network_timer.timeout.emit()  # still no address: the code asked for stays
    assert panel.line.toPlainText() == line and panel.note.text() == logic.CODE_ANYWAY_TEXT

    monkeypatch.setattr(type(state.scans), "qr_text", real)  # the laptop joins a network: the real line
    page._network_timer.timeout.emit()
    assert panel.note.text() == side.SCAN_HOW and panel.retry_button.text() == side.REFRESH
    monkeypatch.setattr(type(state.scans), "qr_text", lambda self, machine_id, name="", anyway=False: "")
    page._network_timer.timeout.emit()  # and leaves it: no code until it is asked for again
    assert not panel.code.isVisible() and panel.note.text() == logic.NO_QR_TEXT and panel.anyway_button.isVisible()
    click(qtbot, panel.anyway_button)  # nothing to carry at all: still no code
    assert not panel.code.isVisible() and panel.note.text() == logic.NO_QR_TEXT


def test_the_code_is_drawn_from_segnos_matrix(qtbot, page, state):
    pytest.importorskip("segno")
    from app.pages.machine import qr

    click(qtbot, page.stage.scan_button)
    line = state.scans.qr_text(state.current_machine())
    assert page.scan_panel.code.squares() == qr.matrix(line) != []


def test_copy_line_puts_the_line_on_the_clipboard(qtbot, page, state):
    from PySide6.QtGui import QGuiApplication

    click(qtbot, page.stage.scan_button)
    click(qtbot, page.scan_panel.copy_button)
    assert QGuiApplication.clipboard().text() == state.scans.qr_text(state.current_machine())
    assert page.scan_panel.copy_button.text() == side.COPIED


def test_a_new_machine_starts_with_its_id_typed_here(qtbot, page, state):
    click(qtbot, page.stage.scan_button)
    page.scan_panel.machine.clear()
    qtbot.keyClicks(page.scan_panel.machine, "press-02")
    qtbot.keyClicks(page.scan_panel.name, "Press 2")
    line = state.scans.qr_text("press-02", "Press 2")
    assert "machine=press-02" in line and "name=Press%202" in line
    assert page.scan_panel.code.text() == line and page.scan_panel.line.toPlainText() == line
    assert "will arrive under press-02" in page.scan_panel.note.text()


@pytest.mark.parametrize("typed, note", [("", logic.NO_MACHINE_ID), ("Press 2", logic.BAD_MACHINE_ID),
                                         ("x" * 41, logic.BAD_MACHINE_ID)])
def test_an_id_the_code_cannot_carry_shows_no_code_and_says_why(qtbot, page, typed, note):
    click(qtbot, page.stage.scan_button)
    page.scan_panel.machine.selectAll()
    qtbot.keyClick(page.scan_panel.machine, Qt.Key.Key_Backspace)
    qtbot.keyClicks(page.scan_panel.machine, typed)
    assert not page.scan_panel.code.isVisible() and not page.scan_panel.line.isVisible()
    assert not page.scan_panel.copy_button.isVisible()
    assert page.scan_panel.note.text() == note
    assert page.scan_panel.import_button.isVisible()


def test_import_file_asks_for_a_usdz_or_glb_and_sends_it(qtbot, page, state, monkeypatch):
    asked = []

    def pick(parent, title, folder, filter_):
        asked.append(filter_)
        return "/tmp/conveyor.glb", filter_

    monkeypatch.setattr(QFileDialog, "getOpenFileName", pick)
    click(qtbot, page.stage.scan_button)
    assert page.scan_panel.import_button.text() == "Import file"
    click(qtbot, page.scan_panel.import_button)
    assert state.scans.imported == ["/tmp/conveyor.glb"]
    assert "*.usdz" in asked[0] and "*.glb" in asked[0]


def test_a_cancelled_file_picker_sends_nothing(qtbot, page, state, monkeypatch):
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: ("", ""))
    page.import_dialog()
    assert state.scans.imported == []


def test_picking_a_part_puts_the_pairing_code_away(qtbot, page, state):
    click(qtbot, page.stage.scan_button)
    click(qtbot, row(page, BELT))
    assert not page.scan_panel.isVisible() and page.part_column.head.name.text() == "Belt"


# ---- red is for shapes only --------------------------------------------------------------------------------


def text_colours(root) -> set:
    """The colour of every piece of text the stylesheet or a palette gives a widget under `root`."""
    seen = set()
    for w in [root, *root.findChildren(QWidget)]:
        if isinstance(w, (PinMark, Steps)) or not w.isVisibleTo(root):
            continue
        for role in (w.foregroundRole(),):
            seen.add(w.palette().color(role).name())
    return seen


@pytest.mark.parametrize("prepare", [
    lambda s, p: s.log.select_part(DRIVE_ROLLER),
    lambda s, p: s.log.select_part(MOTOR_MOUNT),
    lambda s, p: (with_convert_again(s), s.scans.set_status("failed", "Blender is not installed on this laptop")),
    lambda s, p: s.scans.clear(),
    lambda s, p: p.add_part(),
])
def test_no_text_on_the_page_is_red(page, state, prepare):
    prepare(state, page)
    assert not any(t.is_red(colour) for colour in text_colours(page))


def test_the_failed_step_is_a_red_ring_with_plain_text(qtbot):
    w = Steps()
    qtbot.addWidget(w)
    w.set_steps(logic.steps("failed"))
    w.resize(w.sizeHint())
    image = QImage(w.size(), QImage.Format.Format_ARGB32)
    image.fill(QColor(t.PANEL))
    w.render(image)
    label_from = w.sizeHint().width() - w.fontMetrics().horizontalAdvance("On this laptop") - 1
    reds = [x for x in range(image.width()) for y in range(image.height())
            if abs(image.pixelColor(x, y).red() - 255) < 40 and image.pixelColor(x, y).blue() < 90]
    assert reds, "the failed step has no red mark"
    first_label = Steps.DOT + Steps.GAP
    second_mark = first_label + w.fontMetrics().horizontalAdvance("Received") + Steps.LINE + 2 * Steps.PAD
    assert all(second_mark - 1 <= x <= second_mark + Steps.DOT + 2 for x in reds)  # only the ring, no letters
    assert label_from > second_mark
