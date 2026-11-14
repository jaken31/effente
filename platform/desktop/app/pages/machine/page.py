"""Machine: one screen that shows where the parts are, how the selected part is running, how it has
run, and where it is heading (docs/scan-and-log-plan.md, item C5 and row M1 of section 15).

Three columns that fill the window, under a strip that says which part the probe is on:

    the scan as a 3D model, and the parts list
    the selected part: its tiles, its velocity over time with the projection's sentence, its evidence
    its part review, and its log: the one list that scrolls

Scans come from `state.scans` and parts from `state.log` (INTERFACES.md additions 18 and 19). This
page and the Log page meet only through `state.log`'s selection: "Open log" selects the part and
asks the shell for the `log` page. Neither page imports the other.

What to show and what a click means is decided in logic.py and readings.py, in plain Python. This file wires the
panels to the state. The 3D view is made only when a model is about to be shown, so a laptop whose
graphics cannot draw 3D, and the offscreen tests, still get the parts list and every other state.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.theme import fonts
from app.theme import tokens as t
from app.theme.qss import css
from app.widgets import Button
from PySide6.QtCore import QEvent, QSize, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QFileDialog, QFrame, QHBoxLayout, QVBoxLayout, QWidget

from . import logic, qr
from . import partview as pv
from . import readings as rd
from .marks import label, palette
from .side import SCAN_HOW, LogPanel, PartsPanel, ReviewPanel, ScanPanel
from .stage import CONVERT_AGAIN, IMPORT, Stage

LEFT_MIN_W = 300
MID_MIN_W = 440
RIGHT_W = 316
# The page's height when the window is 1440x820, less the top bar and the content's padding: the page
# never asks for more, so the window does not scroll it. Only the log list scrolls.
FIT_H = 820 - t.TOPBAR_H - 2 * t.CONTENT_PAD
NO_EVIDENCE = ("The records of this part's newest visit are not on this laptop. They show here while the probe "
               "is on the part.")
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
    subtitle = "pick a part to see how it is running, how it has run, and where it is heading"
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
        self.range = rd.DEFAULT_RANGE  # how far back the chart reaches
        self._awaiting_entry = False

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(t.PAGE_GAP - 4)

        # where the probe is: it moves only with "Probe is here" or "Clear", never with a selection
        self.probe_strip = QFrame()
        self.probe_strip.setObjectName("machineProbe")
        self.probe_strip.setStyleSheet(f"#machineProbe {{ background: {css(t.ASK_BG)}; "
                                       f"border: 1px solid {css(t.BORDER_PANEL)}; border-radius: 10px; }}")
        pl = QHBoxLayout(self.probe_strip)
        pl.setContentsMargins(14, 5, 8, 5)
        pl.setSpacing(10)
        self.probe_text = label("", "strong", fonts.font(t.FONT_BASE, 500))
        self.probe_clear = Button(rd.CLEAR, "ghost", small=True)
        self.probe_clear.setToolTip("The probe is on no part. Records that arrive are kept for the machine only.")
        self.probe_clear.clicked.connect(self.clear_probe)
        pl.addWidget(self.probe_text, 1)
        pl.addWidget(self.probe_clear)
        outer.addWidget(self.probe_strip)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(t.PAGE_GAP)
        outer.addLayout(row, 1)
        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(t.PAGE_GAP)

        self.stage = Stage()
        self.stage.new_scan.connect(self.toggle_scan_panel)
        self.stage.reset_view.connect(self.reset_view)
        self.stage.card_pressed.connect(self.card_pressed)
        self.stage.cancelled.connect(self.cancel)
        self.stage.part_submitted.connect(self.submit_part)
        left.addWidget(self.stage, 1)

        self.parts_panel = PartsPanel()
        self.parts_panel.selected.connect(self.select_part)
        self.parts_panel.place.connect(self.place)
        self.parts_panel.add.connect(self.add_part)
        left.addWidget(self.parts_panel)
        self.scan_panel = ScanPanel()
        self.scan_panel.changed.connect(self._draw_scan_panel)
        self.scan_panel.import_file.connect(self.import_dialog)
        self.scan_panel.closed.connect(self.toggle_scan_panel)
        self.stage.set_overlay(self.scan_panel)
        left_w = QWidget()
        left_w.setLayout(left)
        left_w.setMinimumWidth(LEFT_MIN_W)
        row.addWidget(left_w, 85)

        self.part_column = pv.PartColumn()
        self.part_column.setMinimumWidth(MID_MIN_W)
        self.part_column.head.probe_here.connect(self.probe_here)
        self.part_column.head.move_pin.connect(self.move_pin)
        self.part_column.blank.pressed.connect(self.probe_here)
        self.part_column.chart_panel.range_changed.connect(self.set_range)
        row.addWidget(self.part_column, 120)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(t.PAGE_GAP)
        self.review_panel = ReviewPanel()
        self.review_panel.run.connect(self.run_review)
        self.review_panel.agree.connect(self.agree)
        self.log_panel = LogPanel()
        self.log_panel.open_log.connect(self.open_log)
        self.log_panel.submitted.connect(self.add_entry)
        right.addWidget(self.review_panel)
        right.addWidget(self.log_panel, 1)
        right_w = QWidget()
        right_w.setLayout(right)
        right_w.setFixedWidth(RIGHT_W)
        row.addWidget(right_w)

    def sizeHint(self) -> QSize:
        """No taller than the page area of a 1440x820 window, so the window never scrolls the page."""
        return QSize(super().sizeHint().width(), min(super().sizeHint().height(), FIT_H))

    def hasHeightForWidth(self) -> bool:
        """The columns share the height they are given: wrapped text never makes the page taller."""
        return False

    def bind(self, state) -> None:
        self.state = state
        state.scans.scans_changed.connect(self.refresh)
        state.scans.model_ready.connect(self.refresh)
        state.log.parts_changed.connect(self._parts_changed)
        state.log.entries_changed.connect(self._entries_changed)
        state.log.selection_changed.connect(self._selection_changed)
        state.log.reviews_changed.connect(self.refresh)
        state.log.probe_changed.connect(self.refresh)
        state.log.readings_changed.connect(self._readings_changed)
        state.snapshot_changed.connect(self.refresh)
        state.record_added.connect(self._record_added)
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

    def probe_here(self) -> None:
        """Say the probe sits on the selected part. Selecting a part never does this by itself."""
        part = self.part()
        if part is not None:
            self.state.log.set_probe_part(part["part_id"])

    def clear_probe(self) -> None:
        if self.state is not None:
            self.state.log.set_probe_part(None)

    def set_range(self, key: str) -> None:
        self.range = key
        self.refresh()

    def run_review(self) -> None:
        part = self.part()
        if part is not None:
            self.state.log.start_review(part["part_id"])

    def agree(self) -> None:
        """Put the review's advice in the add-entry form. The person still adds it."""
        part = self.part()
        draft = rd.agree_draft(self.state.log.review(part["part_id"])) if part else None
        if draft is not None:
            self.log_panel.form.setVisible(True)
            self.log_panel.form.fill(*draft)

    def add_entry(self, kind: str, text: str, needs_repair) -> None:
        part = self.part()
        if part is not None:
            self._awaiting_entry = True
            self.state.log.add_entry(kind, text, part["part_id"], needs_repair)

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

    def _record_added(self, record: dict) -> None:
        """A first record can be what makes a machine the one shown, with no snapshot to say so. A record
        of the selected part's visit is its evidence."""
        part = self.part()
        if self.state.current_machine() != self._machine or (part and record.get("part_id") == part["part_id"]):
            self.refresh()

    def _readings_changed(self, part_id: str) -> None:
        part = self.part()
        if part is not None and part["part_id"] == part_id:
            self.refresh()

    def _entries_changed(self) -> None:
        if self._awaiting_entry:  # the entry asked for is in: put the form away
            self._awaiting_entry = False
            self.log_panel.close_form()
        self.refresh()

    def _selection_changed(self) -> None:
        if self._scan_wanted and self.part() is not None:
            self._scan_wanted = None  # a part was picked: show it, not the pairing code
        self.refresh()

    def _action_failed(self, _message: str) -> None:
        """The window shows the reason. What was typed stays, to be sent again."""
        self._awaiting = None
        self._awaiting_entry = False

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

        self.stage.set_hint(logic.scan_when(scan))  # the whole line, with the file's name, on hover
        self.stage.hint_label.setToolTip(logic.scan_line(scan))
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
        by_id = {p["part_id"]: p for p in parts}
        for r in rows:
            r["number"] = rd.part_number(by_id[r["part_id"]])
        note = "" if self._form_open or not machine else logic.parts_note(parts, scan)
        self.parts_panel.set_rows(rows, shown, note)
        self.parts_panel.add_button.setEnabled(bool(machine))

        probe = log.probe_part()
        selection = getattr(log, "probe", None)  # the pick with its `since`, where the store has it
        self.probe_text.setText(rd.probe_line(parts, probe, selection() if callable(selection) else None))
        self.probe_clear.setVisible(bool(probe))

        entries = log.entries(selected) if selected else []
        self.detail = logic.detail(part, entries, scan, shown, self._now())
        self._draw_part(part, parts, probe)
        self.review_panel.show_review(
            rd.review(part, log.review(selected), log.review_running(selected)) if part else None,
            bool(part) and log.review_running(selected))
        self.log_panel.set_entries(rd.entries(entries))
        self.log_panel.add_button.setEnabled(part is not None)
        self.log_panel.log_button.setEnabled(part is not None)
        self.log_panel.empty.setText(rd.NO_ENTRIES_FOR if part else rd.NO_PART_LOG)
        if part is None and self.log_panel.form_open():
            self.log_panel.close_form()
        self.stage.show_overlay(self.scan_panel_open())
        self._draw_scan_panel()

    def _draw_part(self, part, parts: list, probe) -> None:
        """The middle column: the part's head, tiles, chart with its sentence, and evidence."""
        col = self.part_column
        if part is None:
            col.show_blank(pv.PICK_TITLE, pv.PICK_TEXT if parts else pv.NO_PARTS_TEXT, head=False)
            return
        part_id = part["part_id"]
        col.head.set_detail(self.detail, probe == part_id)
        col.head.sim_chip.setVisible(False)
        if rd.never_read(part):
            col.show_blank(pv.NEVER_TITLE, pv.NEVER_TEXT, "" if probe == part_id else rd.PROBE_HERE, head=True)
            return
        now = self._now()
        since = rd.since(self.range, now)
        reply = self.state.log.readings(part_id, since)  # asking starts the fetch
        if reply is None:
            col.show_blank(pv.FETCH_TITLE, pv.FETCH_TEXT, head=True)
            return
        col.show_readings()
        col.tiles.set_tiles(rd.tiles(reply, part))
        col.head.sim_chip.setVisible(col.tiles.simulated())

        panel = col.chart_panel
        panel.range.set_current(self.range)
        points = rd.points(reply.get("readings"))
        marks = rd.repair_marks(self.state.log.entries(part_id, kind="repair"))
        days = next((d for k, _label, d in rd.RANGES if k == self.range), None)
        empty = "" if points else f"No readings in the last {days} days." if days else "No readings."
        panel.chart.set_data(points, marks, rd.x_span(points, marks, since, now), empty)
        projection = rd.projection(reply.get("projection"))
        panel.set_projection(projection)
        panel.sim_chip.setVisible(any(p.simulated for p in points) or bool(projection and projection.simulated))

        ev = col.evidence_panel
        record = rd.visit_record(self.state.records(spectrum=True), part_id, reply)
        texts = self.state.feature_texts() if callable(getattr(self.state, "feature_texts", None)) else {}
        moved = rd.moved(record, texts)
        ev.moved.set_rows(moved, "" if moved else NO_EVIDENCE if record is None
                          else "Nothing moved enough to be sent in this record.")
        own = self.state.log.normal_spectrum(part_id)
        machine = None if own and own.get("ready") else self.state.log.normal_spectrum(None)
        spectrum = rd.spectrum(record, own, machine, (self.state.model() or {}).get("spectrum_edges_hz"))
        ev.spectrum.set_view(spectrum, "" if spectrum else NO_EVIDENCE if record is None
                             else "This record has no spectrum.")
        ev.sim_chip.setVisible(bool(record and record.get("simulated") is True) or bool(spectrum and spectrum.simulated))

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
