"""Log: every part's history, and what needs repair. The page that replaces the
maintenance spreadsheet (docs/scan-and-log-plan.md, item J3).

Everything comes from and goes to `state.log` (INTERFACES.md addition 19). This page
and the Machine page meet only through its selection: "Show on machine" selects the
part and asks the shell for the `machine` page. Neither page imports the other.

Nothing is added to the log without a person's click. A review is shown as advice,
and "Agree" only fills the add-entry row for the person to edit and add.
"""

from __future__ import annotations

from app.theme import fonts
from app.theme import tokens as t
from app.widgets import Button, Panel, Segmented, SimChip
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFileDialog, QGridLayout, QHBoxLayout, QVBoxLayout, QWidget

from . import view
from .detail import Compose, ReviewPanel
from .marks import StateMark, Strip, keep_place, label
from .side import AlarmsPanel, PartsPanel
from .table import EntryTable

SIDE_W = 320
PAD_V, PAD_H = t.PANEL_BODY_PAD


class LogPage(QWidget):
    key = "log"
    title = "Log"
    subtitle = "every part's history, and what needs repair"
    navigate = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.state = None
        self.kind = view.ALL  # the table's kind filter
        self._adding_entry = False  # an add_entry call is out: its entry empties the form
        self._adding_part = False

        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(t.PAGE_GAP)

        # left: parts, alarms waiting
        self.parts_panel = PartsPanel()
        self.parts_panel.selected.connect(self.select_part)
        self.parts_panel.add_part.connect(self.add_part)
        self.parts_panel.open_machine.connect(lambda: self.navigate.emit("machine"))
        self.alarms_panel = AlarmsPanel()
        self.alarms_panel.choices_of = self._answer_choices
        self.alarms_panel.answered.connect(self.answer_alarm)
        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(t.PAGE_GAP)
        left.addWidget(self.parts_panel, 3)
        left.addWidget(self.alarms_panel, 2)
        for panel in (self.parts_panel, self.alarms_panel):
            panel.setFixedWidth(SIDE_W)

        # right, top: the part's state, add entry, the entries
        self.log_panel = Panel(view.WHOLE_MACHINE)
        self.machine_button = Button(view.SHOW_ON_MACHINE, small=True)
        self.machine_button.clicked.connect(self.show_on_machine)
        self.log_panel.header_end.addWidget(keep_place(self.machine_button))

        self.state_strip = Strip("stateStrip")
        sl = QHBoxLayout(self.state_strip)
        sl.setContentsMargins(PAD_H, 10, PAD_H, 10)
        sl.setSpacing(10)
        self.state_mark = StateMark()
        self.state_line = label("", "strong", fonts.font(t.FONT_BASE, 600))
        self.state_note = label("", "muted", wrap=True)
        sl.addWidget(self.state_mark)
        sl.addWidget(self.state_line)
        sl.addWidget(self.state_note, 1)
        self.log_panel.add(self.state_strip)

        self.compose = Compose()
        self.compose.submitted.connect(self.add_entry)
        self.log_panel.add(self.compose)

        self.filter_strip = Strip("filterStrip")
        fl = QHBoxLayout(self.filter_strip)
        fl.setContentsMargins(PAD_H, 8, PAD_H, 8)
        fl.setSpacing(10)
        self.kind_filter = Segmented([(view.ALL, "All"), *((k, view.kind_label(k)) for k in view.ENTRY_KINDS)],
                                     small=True)
        self.kind_filter.changed.connect(self.set_kind)
        self.count = label("", "muted", fonts.small())
        self.sim_chip = SimChip()
        self.sim_chip.setToolTip("Some of these entries are about simulated alarms, or were written with no real "
                                 "model call.")
        self.export_button = Button(view.EXPORT, small=True)
        self.export_button.setToolTip("The whole machine's log, as a file a spreadsheet opens")
        self.export_button.clicked.connect(self.export_dialog)
        fl.addWidget(self.kind_filter)
        fl.addWidget(self.count)
        fl.addStretch(1)
        fl.addWidget(self.sim_chip)
        fl.addWidget(self.export_button)
        self.log_panel.add(self.filter_strip)

        self.table = EntryTable()
        self.empty = label("", "empty", wrap=True)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.log_panel.add(self.table, 1)
        self.log_panel.add(self.empty, 1)

        # right, bottom: the review
        self.review_panel = ReviewPanel()
        self.review_panel.run.connect(self.run_review)
        self.review_panel.agree.connect(self.agree)

        grid.addLayout(left, 0, 0, 2, 1)
        grid.addWidget(self.log_panel, 0, 1)
        grid.addWidget(self.review_panel, 1, 1)
        grid.setColumnStretch(1, 1)
        grid.setRowStretch(0, 3)
        grid.setRowStretch(1, 2)

    # ---- state ------------------------------------------------------------

    def bind(self, state) -> None:
        self.state = state
        log = state.log
        log.parts_changed.connect(self._parts_changed)
        log.entries_changed.connect(self._entries_changed)
        log.selection_changed.connect(self.refresh)
        log.reviews_changed.connect(self._draw_review)
        state.alarms_changed.connect(self._draw_alarms)
        state.snapshot_changed.connect(self.refresh)
        state.action_failed.connect(self._action_failed)
        self.refresh()

    def part(self):
        """The selected part, or None for the whole machine. A selection that is not
        one of this machine's parts (another machine, a removed part) counts as none."""
        if self.state is None:
            return None
        part_id = self.state.log.selected_part()
        return next((p for p in self.state.log.parts() if p["part_id"] == part_id), None)

    def _parts_changed(self) -> None:
        if self._adding_part:
            self._adding_part = False
            self.parts_panel.part_added()
        self.refresh()

    def _entries_changed(self) -> None:
        if self._adding_entry:
            self._adding_entry = False
            self.compose.clear()
        self.refresh()

    def _action_failed(self, _message: str) -> None:
        """The window shows the reason. What was typed stays, to be sent again."""
        self._adding_entry = self._adding_part = False

    # ---- actions ----------------------------------------------------------

    def select_part(self, part_id) -> None:
        if self.state is not None:
            self.state.log.select_part(part_id)

    def show_on_machine(self) -> None:
        part = self.part()
        if part is not None:
            self.state.log.select_part(part["part_id"])
            self.navigate.emit("machine")

    def add_part(self, name: str, kind: str) -> None:
        if self.state is not None and self.state.current_machine():
            self._adding_part = True
            self.state.log.create_part(name, kind)

    def add_entry(self, kind: str, text: str, needs_repair) -> None:
        if self.state is None or not self.state.current_machine():
            return
        part = self.part()
        self._adding_entry = True
        self.state.log.add_entry(kind, text, part_id=part["part_id"] if part else None, needs_repair=needs_repair)

    def answer_alarm(self, alarm_id: str, verdict: str, part_id: str, finding: str) -> None:
        if self.state is not None:
            self.state.log.answer_alarm_for_part(alarm_id, verdict, part_id, finding=finding or None)

    def run_review(self, mode: str) -> None:
        if self.state is None:
            return
        part = self.part()
        self.state.log.start_review(part["part_id"] if part else None, mode)

    def agree(self) -> None:
        """Agreeing with a review opens a finding with the review's text, to edit. The
        entry is added only when the person then adds it."""
        part = self.part()
        run = self.state.log.review(part["part_id"]) if part else None
        if run and run.get("review"):
            text, needs = view.agree_draft(run)
            self.compose.fill("finding", text, needs)

    def set_kind(self, kind: str) -> None:
        self.kind = kind
        self._draw_entries()

    def export_dialog(self) -> None:
        """Ctrl+E, and the Export CSV button: the whole machine's log."""
        if self.state is None:
            return
        name = view.csv_name(self.state.current_machine())
        path, _ = QFileDialog.getSaveFileName(self, view.EXPORT, name, "CSV (*.csv)")
        if path:
            self.state.log.export_csv(path)

    # ---- drawing ----------------------------------------------------------

    def refresh(self, *_args) -> None:
        if self.state is None:
            return
        self._draw_parts()
        self._draw_heading()
        self._draw_entries()
        self._draw_alarms()
        self._draw_review()

    def _draw_parts(self) -> None:
        log = self.state.log
        part = self.part()
        rows = view.part_rows(log.parts(), part["part_id"] if part else None, len(log.entries()))
        self.parts_panel.set_rows(rows, machine=bool(self.state.current_machine()))

    def _draw_heading(self) -> None:
        part = self.part()
        machine = self.state.current_machine()
        head = view.heading(part, machine)
        self.log_panel.set_title(head.title)
        self.log_panel.set_hint(head.hint)
        self.state_mark.set_state(head.state)
        self.state_mark.setVisible(bool(head.state))
        self.state_line.setText(head.line)
        self.state_line.setVisible(bool(head.line))
        self.state_note.setText(head.note)
        self.machine_button.setVisible(part is not None)
        self.compose.setEnabled(bool(machine))
        self.compose.set_part(part)

    def _draw_entries(self) -> None:
        log = self.state.log
        part = self.part()
        machine = self.state.current_machine()
        kind = None if self.kind == view.ALL else self.kind
        entries = log.entries(part["part_id"], kind) if part else log.entries(kind=kind)
        names = {p["part_id"]: p.get("name") or p["part_id"] for p in log.parts()}
        rows = view.entry_rows(entries, names)
        self.table.set_rows(rows, with_part=part is None)
        self.table.setVisible(bool(rows))
        self.empty.setText(view.empty_text(part, self.kind, machine))
        self.empty.setVisible(not rows)
        self.count.setText(view.count_text(len(rows), self.kind))
        self.sim_chip.setVisible(any(r.tag for r in rows))
        self.export_button.setEnabled(bool(machine))

    def _answer_choices(self, alarm_id) -> list:
        alarm = next((a for a in self.state.log.waiting_alarms() if a["alarm_id"] == alarm_id), None)
        return view.part_choices(self.state.log.parts(), self.state.log.suspects(alarm) if alarm else [])

    def _draw_alarms(self, *_args) -> None:
        if self.state is None:
            return
        log = self.state.log
        self.alarms_panel.set_rows(view.alarm_rows(log.waiting_alarms(), log.suspects))

    def _draw_review(self, *_args) -> None:
        if self.state is None:
            return
        log = self.state.log
        part = self.part()
        parts = log.parts()
        if part is not None:
            running = log.review_running(part["part_id"])
            run = log.review(part["part_id"])
            can_run = not running
        else:
            running = any(log.review_running(p["part_id"]) for p in parts)
            run = None
            can_run = bool(parts) and not running
        shown = view.review(part, run, running if part is not None else False, has_parts=bool(parts))
        text = view.REVIEWING if running else view.RUN_REVIEW if part is not None else view.RUN_ALL
        self.review_panel.show_review(shown, run_text=text, can_run=can_run)
