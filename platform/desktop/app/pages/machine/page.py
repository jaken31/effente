"""Machine: the newest scan of the current machine as a 3D model, with a pin on each part
(docs/scan-and-log-plan.md, item C5).

Scans come from `state.scans` and parts from `state.log` (INTERFACES.md additions 18 and 19). This
page and the Log page meet only through `state.log`'s selection: "Open log" selects the part and
asks the shell for the `log` page. Neither page imports the other.

What to show and what a click means is decided in logic.py, in plain Python. This file wires the
panels to the state. The 3D view is made only when a model is about to be shown, so a laptop whose
graphics cannot draw 3D, and the offscreen tests, still get the parts list and every other state.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.theme import tokens as t
from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QVBoxLayout, QWidget

from . import logic, qr
from .marks import palette
from .side import SCAN_HOW, DetailPanel, PartsPanel, ScanPanel
from .stage import CONVERT_AGAIN, IMPORT, Stage

SIDE_W = 320  # with the stage at its narrowest, the page still fits the smallest window (960x660)
IMPORT_TITLE = "Import scan file"
IMPORT_FILTER = "Scan files (*.usdz *.glb)"
WORKS_WITHOUT = "The parts list and the log work without it."
NEW_PART = "New part"


def _can_draw() -> bool:
    try:
        from .view3d import can_draw
    except ImportError:  # a PySide6 without Qt Quick: the page says so when it has a model to show
        return True
    return can_draw()


def _make_view() -> QWidget:
    from .view3d import ModelView

    return ModelView()


class MachinePage(QWidget):
    key = "machine"
    title = "Machine"
    subtitle = "3D scan of the machine, with a pin on each part"
    navigate = Signal(str)

    def __init__(self, parent=None, *, make_view=None, can_draw=None):
        """`make_view` builds the 3D view (view3d.ModelView's interface) and `can_draw` says whether
        this laptop can draw one at all. Tests pass their own."""
        super().__init__(parent)
        self.state = None
        self._make_view = make_view or _make_view
        self._can_draw = can_draw or _can_draw
        self.view = None  # the 3D view, once a model had to be shown
        self._start_problem = ""  # the view could not start: holds for good
        self._load_problem = ""  # this scan's file could not be shown
        self._loaded = None  # (scan_id, path) the view was last given
        self._pins = None  # the pins the view was last given

        self.mode = logic.IDLE  # what the next click on the model means
        self._said = ""  # what the last click did, when it did nothing
        self._form_open = False
        self._form_position = None  # where the part in the form goes, or None for no pin
        self._awaiting = None  # the part ids before create_part, until the new part shows
        self._scan_id = None
        self._machine = None
        self._qr_checked = ("", "")  # the pairing line last checked, and why it cannot be drawn
        self._scan_wanted = None  # the New scan panel: True or False once the person chose, else by itself
        self.view_state = logic.view_state(None, False)

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(t.PAGE_GAP)

        self.stage = Stage()
        self.stage.new_scan.connect(self.toggle_scan_panel)
        self.stage.reset_view.connect(self.reset_view)
        self.stage.card_pressed.connect(self.card_pressed)
        self.stage.cancelled.connect(self.cancel)
        self.stage.part_submitted.connect(self.submit_part)
        row.addWidget(self.stage, 1)

        self.parts_panel = PartsPanel()
        self.parts_panel.selected.connect(self.select_part)
        self.parts_panel.place.connect(self.place)
        self.parts_panel.add.connect(self.add_part)
        self.detail_panel = DetailPanel()
        self.detail_panel.open_log.connect(self.open_log)
        self.detail_panel.move_pin.connect(self.move_pin)
        self.scan_panel = ScanPanel()
        self.scan_panel.changed.connect(self._draw_scan_panel)
        self.scan_panel.import_file.connect(self.import_dialog)
        self.scan_panel.closed.connect(self.toggle_scan_panel)
        side = QVBoxLayout()
        side.setContentsMargins(0, 0, 0, 0)
        side.setSpacing(t.PAGE_GAP)
        side.addWidget(self.parts_panel, 2)
        side.addWidget(self.detail_panel, 2)
        side.addWidget(self.scan_panel, 3)
        for panel in (self.parts_panel, self.detail_panel, self.scan_panel):
            panel.setFixedWidth(SIDE_W)
        row.addLayout(side)

    def bind(self, state) -> None:
        self.state = state
        state.scans.scans_changed.connect(self.refresh)
        state.scans.model_ready.connect(self.refresh)
        state.log.parts_changed.connect(self._parts_changed)
        state.log.entries_changed.connect(self.refresh)
        state.log.selection_changed.connect(self._selection_changed)
        state.snapshot_changed.connect(self.refresh)
        state.action_failed.connect(self._action_failed)
        self.refresh()

    # ---- reads ------------------------------------------------------------

    def scan(self):
        return self.state.scans.current() if self.state is not None else None

    def part(self):
        """The selected part. A selection that is not one of this machine's parts counts as none."""
        if self.state is None:
            return None
        part_id = self.state.log.selected_part()
        return next((p for p in self.state.log.parts() if p["part_id"] == part_id), None)

    def model_shown(self) -> bool:
        return self.view_state["show_model"]

    def busy(self) -> bool:
        """Adding or placing, or the new part's form is up: Esc and Cancel end it."""
        return self.mode != logic.IDLE or self._form_open

    def scan_panel_open(self) -> bool:
        if self._scan_wanted is not None:
            return self._scan_wanted
        return self.scan() is None and self.part() is None

    def can_convert_again(self) -> bool:
        """`convert_again(scan_id)` is asked of `state.scans` only where it has one: it is not yet in
        INTERFACES.md addition 18."""
        return self.state is not None and callable(getattr(self.state.scans, "convert_again", None))

    def _now(self) -> datetime:
        return datetime.fromtimestamp(self.state.now_ms() / 1000.0, UTC)

    # ---- actions ----------------------------------------------------------

    def select_part(self, part_id) -> None:
        if self.state is not None:
            self.state.log.select_part(part_id or None)

    def add_part(self) -> None:
        """With a model on screen the next click on it is where the part goes. With none, the part
        is listed without a pin: the log works with no scan."""
        if self.state is None or self.state.current_machine() is None:
            return
        self._said = ""
        if self.model_shown():
            self.mode, self._form_open, self._form_position = logic.adding(), False, None
            self.setFocus()
        else:
            self.mode, self._form_open, self._form_position = logic.IDLE, True, None
        self.refresh()

    def place(self, part_id: str) -> None:
        """The next click on the model is where this part's pin goes."""
        if self.state is None or not self.model_shown():
            return
        self.state.log.select_part(part_id)
        self.mode, self._form_open, self._form_position, self._said = logic.placing(part_id), False, None, ""
        self.setFocus()
        self.refresh()

    def move_pin(self) -> None:
        part = self.part()
        if part is not None:
            self.place(part["part_id"])

    def cancel(self) -> None:
        self.mode, self._form_open, self._form_position, self._said = logic.IDLE, False, None, ""
        self._awaiting = None
        self.stage.form.clear()
        self.refresh()

    def submit_part(self, name: str, kind: str) -> None:
        if self.state is None or not self._form_open:
            return
        self._awaiting = {p["part_id"] for p in self.state.log.parts()}
        self.state.log.create_part(name, kind, self._form_position)

    def open_log(self) -> None:
        part = self.part()
        if part is not None:
            self.state.log.select_part(part["part_id"])
            self.navigate.emit("log")

    def toggle_scan_panel(self) -> None:
        self._scan_wanted = not self.scan_panel_open()
        self.refresh()

    def import_dialog(self) -> None:
        if self.state is None:
            return
        path, _ = QFileDialog.getOpenFileName(self, IMPORT_TITLE, "", IMPORT_FILTER)
        if path:
            self.state.scans.import_file(path)

    def convert_again(self) -> None:
        scan = self.scan()
        if scan is not None and self.can_convert_again():
            self.state.scans.convert_again(scan["scan_id"])

    def card_pressed(self) -> None:
        if self.view_state["name"] == "no_scan":
            self.import_dialog()
        elif self.view_state["retry"]:
            self.convert_again()

    def reset_view(self) -> None:
        if self.view is not None:
            self.view.reset_camera()

    # ---- clicks on the model ----------------------------------------------

    def _picked(self, x: float, y: float, z: float) -> None:
        action, self.mode = logic.on_model_click(self.mode, self.scan(), (x, y, z))
        self._said = ""
        if action[0] == "new_part":
            self._form_open, self._form_position = True, action[1]
        elif action[0] == "move_pin":
            self.state.log.update_part(action[1], position=action[2])
        self.refresh()

    def _missed(self) -> None:
        action, self.mode = logic.on_miss(self.mode)
        if action[0] == "say":
            self._said = action[1]
            self.refresh()
        else:
            self.select_part(action[1])

    def _pin_clicked(self, part_id: str) -> None:
        if not part_id:  # the new part's own pin, before it has a name
            return
        action, self.mode = logic.on_pin_click(self.mode, part_id)
        self._said = ""
        self.select_part(action[1])
        self.refresh()

    # ---- what the state says ----------------------------------------------

    def _parts_changed(self) -> None:
        if self._awaiting is not None:
            new = logic.new_part_id(self._awaiting, self.state.log.parts())
            if new is not None:  # the part asked for is there: put the form away and show it
                self._awaiting, self._form_open, self._form_position = None, False, None
                self.stage.form.clear()
                self.state.log.select_part(new)
        self.refresh()

    def _selection_changed(self) -> None:
        if self._scan_wanted and self.part() is not None:
            self._scan_wanted = None  # a part was picked: show it, not the pairing code
        self.refresh()

    def _action_failed(self, _message: str) -> None:
        """The window shows the reason. What was typed stays, to be sent again."""
        self._awaiting = None

    def _view_status(self, status: str) -> None:
        if status == "error" and self.view is not None:
            self._load_problem = self.view.problem()
            self.refresh()

    # ---- the 3D view ------------------------------------------------------

    def _ensure_view(self) -> bool:
        if self.view is None and not self._start_problem:
            try:
                view = self._make_view()
            except Exception as e:  # noqa: BLE001  whatever stops it, the page must still open
                self._start_problem = f"the 3D view could not start on this laptop ({e})"
                return False
            problem = view.problem()
            if problem:
                self._start_problem = problem
                view.deleteLater()
                return False
            self.view = view
            view.picked.connect(self._picked)
            view.missed.connect(self._missed)
            view.pin_clicked.connect(self._pin_clicked)
            view.status_changed.connect(self._view_status)
            self._colour_view()
            self.stage.set_view(view)
        return self.view is not None

    def _colour_view(self) -> None:
        back = QColor(t.qcolor(t.PANEL))
        back.setAlphaF(0.9)
        self.view.set_colours(t.qcolor(t.PANEL).name(), t.qcolor(t.TEXT).name(),
                              back.name(QColor.NameFormat.HexArgb))

    def _load(self, scan: dict, path: str) -> bool:
        """Show this scan's model. False if the view cannot: `_start_problem` or `_load_problem` says why."""
        if not self._ensure_view():
            return False
        if self._loaded != (scan["scan_id"], path):
            self._loaded = (scan["scan_id"], path)
            self._pins = None
            self.view.load(path, (scan.get("model") or {}).get("bounds"))
            if self.view.status() == "error":
                self._load_problem = self.view.problem()
        return not self._load_problem

    def _view_pins(self, parts: list, scan: dict, selected) -> list:
        pins = logic.pins(parts, scan, selected, palette())
        if self._form_open and self._form_position is not None:  # where the part in the form will go
            x, y, z = self._form_position["at"]
            for pin in pins:
                pin["selected"] = False
            pins.append({"id": "", "x": x, "y": y, "z": z, "colour": t.PANEL, "ring": t.TEXT, "selected": True,
                         "label": NEW_PART})
        return pins

    # ---- drawing ----------------------------------------------------------

    def refresh(self, *_args) -> None:
        if self.state is None:
            return
        scans, log = self.state.scans, self.state.log
        machine = self.state.current_machine()
        scan = scans.current()
        scan_id = scan["scan_id"] if scan else None
        if (machine, scan_id) != (self._machine, self._scan_id):
            # another machine or another scan: a half-made click means nothing there
            if machine != self._machine:
                self.scan_panel.set_machine_id(machine or "")
                self.scan_panel.name.clear()
            self._machine, self._scan_id = machine, scan_id
            self._scan_wanted, self._load_problem = None, ""
            self.mode, self._form_open, self._form_position, self._said = logic.IDLE, False, None, ""
            self.stage.form.clear()
        parts = log.parts()
        part = self.part()
        selected = part["part_id"] if part else None

        can = self._can_draw()
        problem = self._start_problem or self._load_problem
        ready = scan is not None and (scan.get("model") or {}).get("status") == "ready"
        path = scans.model_path(scan_id) if ready and can and not problem else None  # asking starts the fetch
        state = logic.view_state(scan, can, problem, path is not None)
        if state["show_model"] and not self._load(scan, path):
            problem = self._start_problem or self._load_problem
            state = logic.view_state(scan, can, problem, True)
        self.view_state = state
        shown = state["show_model"]
        if not shown:
            self.mode, self._said = logic.IDLE, ""

        self.stage.set_hint(logic.scan_line(scan))
        if shown:
            self.stage.show_model()
            pins = self._view_pins(parts, scan, selected)
            if pins != self._pins:
                self._pins = pins
                self.view.set_pins(pins)
        else:
            text = state["text"]
            if state["name"] == "cannot_draw" and problem:
                text = f"{logic.capitalise(problem)}. {WORKS_WITHOUT}"
            button = IMPORT if state["name"] == "no_scan" and machine else ""
            if state["retry"] and self.can_convert_again():
                button = CONVERT_AGAIN
            self.stage.show_card(state["title"], text, logic.steps(state["name"]), button)
        placing = part["name"] if part and self.mode[0] == "placing" else ""
        self.stage.show_hint(self._said or logic.hint(self.mode, placing), self.mode != logic.IDLE)
        self.stage.show_form(self._form_open, pinned=self._form_position is not None)

        rows = logic.rows(parts, scan, selected, palette(), self._now())
        note = "" if self._form_open or not machine else logic.parts_note(parts, scan)
        self.parts_panel.set_rows(rows, shown, note)
        self.parts_panel.add_button.setEnabled(bool(machine))

        entries = log.entries(selected) if selected else []
        self.detail_panel.set_detail(logic.detail(part, entries, scan, shown, self._now()))
        scan_open = self.scan_panel_open()
        self.detail_panel.setVisible(not scan_open)
        self.scan_panel.setVisible(scan_open)
        self._draw_scan_panel()

    def _draw_scan_panel(self, *_args) -> None:
        if self.state is None:
            return
        machine_id = self.scan_panel.machine_id()
        note = logic.machine_id_problem(machine_id)
        text = "" if note else self.state.scans.qr_text(machine_id, self.scan_panel.machine_name())
        if not note:
            if text != self._qr_checked[0]:  # worked out once for each line, not on every redraw
                self._qr_checked = (text, qr.problem(text))
            note = (logic.NO_QR_TEXT if not text else
                    self._qr_checked[1] or logic.other_machine_note(machine_id, self.state.current_machine())
                    or SCAN_HOW)
        self.scan_panel.show_code(text, note, can_close=self.scan() is not None or self.part() is not None)

    # ---- keys -------------------------------------------------------------

    def event(self, e) -> bool:
        # The window's own Esc shortcut would take the key first: claim it while there is something to cancel.
        if e.type() == QEvent.Type.ShortcutOverride and e.key() == Qt.Key.Key_Escape and self.busy():
            e.accept()
            return True
        return super().event(e)

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key.Key_Escape and self.busy():
            self.cancel()
            return
        super().keyPressEvent(e)
