"""Agents: the agent fleet on this laptop, its runs, tool calls and reports.

Run Report Agent calls `state.start_agent(mode)`, which runs platform/report.py
as a local process (CONTRACT.md "Not in version 3.3"). The key is never asked
for here: live mode without one shows the state's own refusal message.
"""

from __future__ import annotations

from html import escape

from app.pages.evidence.table_model import Column, RowTable
from app.theme import fonts
from app.theme import tokens as t
from app.theme.qss import css
from app.widgets import Button, Panel, Segmented, SimChip
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QLabel,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import view
from .kv import KeyValues, code_html, pin_title_left
from .levels import Levels

IO_MAX_H = 180


class _RunRow(QFrame):
    clicked = Signal(str)

    def __init__(self, row: view.RunRow, selected: bool):
        super().__init__()
        self.run_id = row.run_id
        self.setObjectName("runRow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        bg = css(t.ACCENT_BG) if selected else "transparent"
        self.setStyleSheet(f"#runRow {{ background: {bg}; border: 0; border-bottom: 1px solid {css(t.GRID)}; }}")
        g = QGridLayout(self)
        g.setContentsMargins(t.CELL_PAD_H, t.CELL_PAD_V, t.CELL_PAD_H, t.CELL_PAD_V)
        g.setHorizontalSpacing(12)
        g.setVerticalSpacing(0)
        time = QLabel(row.time)
        time.setProperty("role", "strong")
        time.setFont(fonts.font(t.FONT_TABLE))
        who = QLabel(row.who)
        who.setProperty("role", "muted")
        who.setFont(fonts.small())
        who.setWordWrap(True)
        status = QLabel(row.status)
        status.setFont(fonts.font(t.FONT_TABLE, 500 if row.status != "report ready" else 400))
        status.setProperty("role", "soft" if row.status == "report ready" else "strong")
        status.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        g.addWidget(time, 0, 0)
        g.addWidget(who, 1, 0)
        g.addWidget(status, 0, 1, 2, 1)
        g.setColumnStretch(0, 1)
        self.status_label = status

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.run_id)
        super().mouseReleaseEvent(e)


class AgentsPage(QWidget):
    key = "agent"
    title = "Agents"
    subtitle = "the agent fleet on this laptop"
    navigate = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.state = None
        self.selected_run: str | None = None  # None follows the newest run
        self.selected_call: int | None = None
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(16)

        # fleet
        self.fleet_panel = Panel("Agent fleet", "runs on this laptop")
        top = QWidget()
        top.setObjectName("fleetTop")
        top.setStyleSheet(f"#fleetTop {{ border-bottom: 1px solid {css(t.BORDER)}; }}")
        tv = QVBoxLayout(top)
        bv, bh = t.PANEL_BODY_PAD
        tv.setContentsMargins(bh, bv, bh, bv)
        tv.setSpacing(10)
        self.fleet = Levels([(name, active, not active) for name, active in view.FLEET])
        self.mode = Segmented(view.MODES, small=True)
        self.run_button = Button("Run Report Agent", "primary")
        self.run_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.run_button.clicked.connect(self.run_agent)
        self.message = QLabel()
        self.message.setProperty("role", "soft")
        self.message.setFont(fonts.small())
        self.message.setWordWrap(True)
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.message.hide()
        tv.addWidget(self.fleet)
        tv.addWidget(self.mode)
        tv.addWidget(self.run_button)
        tv.addWidget(self.message)
        self.fleet_panel.add(top)
        self.runs_area = QScrollArea()
        self.runs_area.setWidgetResizable(True)
        self.runs_area.setFrameShape(QFrame.Shape.NoFrame)
        self.runs_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.fleet_panel.add(self.runs_area, 1)
        self.run_rows: list[_RunRow] = []

        # tool calls
        self.calls_panel = Panel("Tool calls")
        self.calls = RowTable([
            Column("Time", width=104),
            Column("Tool", strong=True, mono=True, width=160),
            Column("Input"),
            Column("Output"),
        ], clickable=True)
        self.calls.row_clicked.connect(self.select_call)
        self.calls_panel.add(self.calls, 1)
        self.io = QPlainTextEdit()
        self.io.setProperty("role", "io")
        self.io.setReadOnly(True)
        self.io.setFont(fonts.font(12.5, mono=True))
        self.io.setFrameShape(QFrame.Shape.NoFrame)
        self.calls_panel.add(self.io)

        # report
        self.report_panel = Panel("Report", padded=True)
        pin_title_left(self.report_panel)
        self.sim_chip = SimChip("Simulated")
        self.sim_chip.hide()
        self.report_panel.header_end.addWidget(self.sim_chip)
        self.report = KeyValues()
        self.report_panel.add(self.report)
        self.report_panel.body.layout().addStretch(1)

        grid.addWidget(self.fleet_panel, 0, 0)
        grid.addWidget(self.calls_panel, 0, 1)
        grid.addWidget(self.report_panel, 0, 2)
        grid.setColumnMinimumWidth(0, 290)
        grid.setColumnMinimumWidth(2, 360)
        self.fleet_panel.setFixedWidth(290)
        self.report_panel.setFixedWidth(360)
        grid.setColumnStretch(1, 1)

    # ---- state ------------------------------------------------------------

    def bind(self, state) -> None:
        self.state = state
        state.runs_changed.connect(self.refresh)
        state.snapshot_changed.connect(self.refresh)
        state.agent_message.connect(self.show_message)
        self.show_message(state.last_agent_message())
        self.refresh()

    def run_agent(self) -> None:
        if self.state is None or self.state.agent_running():
            return
        self.selected_run = None
        self.selected_call = None
        self.state.start_agent(self.mode.current() or "standin")
        self.refresh()

    def show_message(self, text: str) -> None:
        self.message.setText(text or "")
        self.message.setVisible(bool(text))

    def select_run(self, run_id: str) -> None:
        self.selected_run = run_id
        self.selected_call = None
        self.refresh()

    def select_call(self, row: int) -> None:
        self.selected_call = row
        self._draw_calls(self.current_run())

    def current_run(self):
        runs = self.state.runs() if self.state else []
        if self.selected_run:
            for r in runs:
                if r.get("run_id") == self.selected_run:
                    return r
        return runs[0] if runs else None

    def refresh(self, *_args) -> None:
        if self.state is None:
            return
        runs = self.state.runs()
        run = self.current_run()
        self.run_button.setEnabled(not self.state.agent_running())
        self._draw_runs(runs, run)
        self._draw_calls(run)
        self._draw_report(run)

    # ---- drawing ----------------------------------------------------------

    def _draw_runs(self, runs, run) -> None:
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.run_rows = []
        sel = run.get("run_id") if run else None
        for row in view.run_rows(runs):
            w = _RunRow(row, row.run_id == sel)
            w.clicked.connect(self.select_run)
            v.addWidget(w)
            self.run_rows.append(w)
        if not self.run_rows:
            e = QLabel(view.NO_RUNS)
            e.setProperty("role", "empty")
            e.setFont(fonts.body())
            v.addWidget(e)
        v.addStretch(1)
        self.runs_area.setWidget(inner)

    def _draw_calls(self, run) -> None:
        self.calls_panel.set_hint(view.usage_hint(run))
        rows = view.call_rows(run)
        if rows:
            self.calls.set_rows([[c.time, c.name, c.input, c.output] for c in rows])
        else:
            self.calls.set_rows([["", view.NO_CALLS if run else "", "", ""]] if run else [])
        if self.selected_call is not None and self.selected_call < len(rows):
            self.calls.selectRow(self.selected_call)
        else:
            self.calls.clearSelection()
        self.io.setPlainText(view.call_io(run, self.selected_call))
        lines = self.io.document().lineCount()
        fm = QFontMetrics(self.io.font())
        margin = self.io.document().documentMargin()
        need = int(lines * fm.lineSpacing() + 2 * 12 + 2 * margin + 4)
        self.io.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded if need > IO_MAX_H else Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.io.setFixedHeight(min(IO_MAX_H, need))

    def _draw_report(self, run) -> None:
        self.sim_chip.setVisible(bool(run and run.get("simulated") is True))
        texts = self.state.feature_texts() if self.state else {}
        rows = []
        for key, value in view.report_fields(run, texts):
            if isinstance(value, list):
                value = ("html", ", ".join(
                    (escape(text) + " " if text else "") + code_html(name) for text, name in value))
            rows.append((key, value))
        if not run:
            rows = [("Status", "no run yet")]
        self.report.set_rows(rows, big=("Priority",))
