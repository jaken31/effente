"""The Log page's left column: the parts list with its add-part form, and the alarms
waiting for a part with their answer form.

Both panels only show rows and collect what a person typed. The page turns their
signals into `state.log` calls. The lists are rebuilt only when their rows differ,
and the forms sit outside the lists, so a record a second never wipes a half-typed
answer.
"""

from __future__ import annotations

from app.data import logic
from app.theme import fonts
from app.theme import tokens as t
from app.theme.qss import css
from app.widgets import Button, Panel, Segmented
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import view
from .marks import StateMark, Strip, keep_place, label

PAD_V, PAD_H = t.PANEL_BODY_PAD


def _scroll() -> QScrollArea:
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    return area


def _swap(area: QScrollArea, inner: QWidget) -> None:
    """Show a new list. The old one is deleted later, not now: a click on one of its
    rows is often what asked for the new list, and that row is still handling it."""
    old = area.takeWidget()
    area.setWidget(inner)
    if old is not None:
        old.hide()
        old.deleteLater()


def _column(parent: QWidget, spacing: int = 0, margins=(0, 0, 0, 0)) -> QVBoxLayout:
    lay = QVBoxLayout(parent)
    lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    return lay


def _wide(button: Button) -> Button:
    button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    return button


class _PartRow(QFrame):
    clicked = Signal(object)  # the part's id, or None for the whole machine

    def __init__(self, row: view.PartRow):
        super().__init__()
        self.row = row
        self.setObjectName("partRow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        bg = css(t.ACCENT_BG) if row.selected else "transparent"
        self.setStyleSheet(f"#partRow {{ background: {bg}; border: 0; border-bottom: 1px solid {css(t.GRID)}; }}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(PAD_H, t.CELL_PAD_V, PAD_H, t.CELL_PAD_V)
        lay.setSpacing(10)
        self.mark = StateMark(row.state)
        lay.addWidget(self.mark, 0, Qt.AlignmentFlag.AlignTop)
        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(1)
        self.name = label(row.name, "strong", fonts.font(t.FONT_TABLE, 500))
        self.line = label(row.line, "muted", fonts.small(), wrap=True)
        text.addWidget(self.name)
        text.addWidget(self.line)
        lay.addLayout(text, 1)

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit(self.row.part_id)
        super().mouseReleaseEvent(e)


class PartsPanel(Panel):
    """The parts of the current machine, each with its state, then the whole machine."""

    selected = Signal(object)  # part id, or None for the whole machine
    add_part = Signal(str, str)  # name, kind
    open_machine = Signal()

    def __init__(self, parent=None):
        super().__init__("Parts", parent=parent)
        self._rows: list[view.PartRow] | None = None
        self._machine = True
        self._wanted = False  # the person opened the add-part form
        self.rows: list[_PartRow] = []

        self.add_button = Button(view.ADD_PART, "ghost", small=True)
        self.add_button.clicked.connect(self.toggle_form)
        self.header_end.addWidget(keep_place(self.add_button))

        # no parts yet: where parts come from, and the way to the Machine page
        self.empty = Strip("partsEmpty")
        ev = _column(self.empty, 10, (PAD_H, PAD_V, PAD_H, PAD_V))
        self.empty_text = label(view.NO_PARTS, "muted", wrap=True)
        self.machine_button = Button(view.OPEN_MACHINE, small=True)
        self.machine_button.clicked.connect(self.open_machine)
        ev.addWidget(self.empty_text)
        ev.addWidget(self.machine_button)
        self.add(self.empty)

        # add a part with no pin
        self.form = Strip("partForm", tinted=True)
        fv = _column(self.form, 8, (PAD_H, PAD_V, PAD_H, PAD_V))
        self.name = QLineEdit()
        self.name.setPlaceholderText("Part name")
        self.name.setFont(fonts.body())
        self.name.textChanged.connect(self._draw_form)
        self.name.returnPressed.connect(self.submit)
        self.kind = QComboBox()
        self.kind.setFont(fonts.body())
        for kind in view.PART_KINDS:
            self.kind.addItem(view.kind_label(kind), kind)
        self.kind.setCurrentIndex(view.PART_KINDS.index("other"))
        self.submit_button = _wide(Button(view.ADD_PART, "primary", small=True))
        self.submit_button.clicked.connect(self.submit)
        fv.addWidget(self.name)
        fv.addWidget(self.kind)
        fv.addWidget(self.submit_button)
        fv.addWidget(label("No pin yet. Place it on the scan from the Machine page.", "muted", fonts.small(),
                           wrap=True))
        self.add(self.form)

        self.area = _scroll()
        self.add(self.area, 1)
        self.set_rows([], machine=False)

    def set_rows(self, rows: list[view.PartRow], *, machine: bool) -> None:
        self._machine = machine
        if rows != self._rows:
            self._rows = list(rows)
            inner = QWidget()
            col = _column(inner)
            self.rows = []
            for row in rows:
                w = _PartRow(row)
                w.clicked.connect(self.selected)
                col.addWidget(w)
                self.rows.append(w)
            col.addStretch(1)
            _swap(self.area, inner)
        self.empty_text.setText(view.NO_PARTS if machine else view.NO_MACHINE)
        self.machine_button.setVisible(machine)
        self.empty.setVisible(not rows)
        self.add_button.setVisible(bool(rows))
        self._draw_form()

    def toggle_form(self) -> None:
        self._wanted = not self._wanted
        self._draw_form()
        if self._wanted:
            self.name.setFocus()

    def form_shown(self) -> bool:
        """With no parts yet the form is always there: adding one is the next thing to do."""
        return self._machine and (self._wanted or not self._rows)

    def _draw_form(self, *_args) -> None:
        self.form.setVisible(self.form_shown())
        self.submit_button.setEnabled(bool(self.name.text().strip()))

    def submit(self) -> None:
        name = self.name.text().strip()
        if name and self.form_shown():
            self.add_part.emit(name, self.kind.currentData())

    def part_added(self) -> None:
        """The part asked for is in the list: empty the form and put it away."""
        self.name.clear()
        self._wanted = False
        self._draw_form()


class _AlarmRow(QFrame):
    answer = Signal(str)

    def __init__(self, row: view.AlarmRow, answering: bool):
        super().__init__()
        self.row = row
        self.setObjectName("alarmRow")
        bg = css(t.ACCENT_BG) if answering else "transparent"
        self.setStyleSheet(f"#alarmRow {{ background: {bg}; border: 0; border-bottom: 1px solid {css(t.GRID)}; }}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(PAD_H, t.CELL_PAD_V, PAD_H - 4, t.CELL_PAD_V)
        lay.setSpacing(10)
        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(1)
        self.title = label(row.title, "strong", fonts.font(t.FONT_TABLE, 500))
        self.when = label(row.when + (f" · {view.SIMULATED}" if row.simulated else ""), "muted", fonts.small(),
                          wrap=True)
        self.suspects = label(row.suspects, "soft", fonts.small(), wrap=True)
        for w in (self.title, self.when, self.suspects):
            text.addWidget(w)
        lay.addLayout(text, 1)
        self.button = Button(view.ANSWER, small=True)
        self.button.clicked.connect(lambda: self.answer.emit(row.alarm_id))
        lay.addWidget(self.button, 0, Qt.AlignmentFlag.AlignTop)


class AlarmsPanel(Panel):
    """Alarms with no answer naming a part. Answering here asks for the verdict, the
    part and the finding. The Live page's own question is left as it is."""

    answered = Signal(str, str, str, str)  # alarm id, verdict, part id, finding

    def __init__(self, parent=None):
        super().__init__("Alarms waiting", "no part named yet", parent=parent)
        self._rows: list[view.AlarmRow] | None = None
        self._drawn = None
        self.rows: list[_AlarmRow] = []
        self.alarm_id: str | None = None  # the alarm being answered
        self.choices_of = lambda alarm_id: []  # set by the page: (part id, label) pairs for an alarm

        self.area = _scroll()
        self.add(self.area, 1)

        self.form = QFrame()
        self.form.setObjectName("answerForm")
        self.form.setStyleSheet(
            f"#answerForm {{ background: {css(t.ASK_BG)}; border: 0; border-top: 1px solid {css(t.BORDER)}; }}")
        fv = _column(self.form, 8, (PAD_H, PAD_V, PAD_H, PAD_V))
        self.form_title = label("", "strong", wrap=True)
        self.verdict = Segmented([(k, logic.verdict_text(k)) for k in ("real", "false")], small=True)
        self.verdict.changed.connect(self._draw_form)
        self.part = QComboBox()
        self.part.setFont(fonts.body())
        self.part.setPlaceholderText(view.PICK_PART)
        self.part.currentIndexChanged.connect(self._draw_form)
        self.finding = QLineEdit()
        self.finding.setFont(fonts.body())
        self.finding.setPlaceholderText(view.FINDING_HINT)
        self.finding.returnPressed.connect(self.submit)
        self.problem = label("", "muted", fonts.small(), wrap=True)
        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(8)
        self.save = Button(view.SAVE_ANSWER, "primary", small=True)
        self.save.clicked.connect(self.submit)
        self.cancel = Button("Cancel", "ghost", small=True)
        self.cancel.clicked.connect(self.close_form)
        buttons.addWidget(self.save)
        buttons.addWidget(self.cancel)
        buttons.addStretch(1)
        fv.addWidget(self.form_title)
        fv.addWidget(self.verdict)
        fv.addWidget(self.part)
        fv.addWidget(self.finding)
        fv.addWidget(self.problem)
        fv.addLayout(buttons)
        self.add(self.form)
        self.form.hide()
        self.set_rows([])

    def set_rows(self, rows: list[view.AlarmRow]) -> None:
        """Show the waiting alarms. The form closes when its alarm is no longer waiting,
        which is also how a saved answer puts it away."""
        if self.alarm_id is not None and all(r.alarm_id != self.alarm_id for r in rows):
            self._reset()
        if (rows, self.alarm_id) != self._drawn:
            self._drawn = (list(rows), self.alarm_id)
            inner = QWidget()
            col = _column(inner)
            self.rows = []
            for row in rows:
                w = _AlarmRow(row, row.alarm_id == self.alarm_id)
                w.answer.connect(self.open_form)
                col.addWidget(w)
                self.rows.append(w)
            if not rows:
                empty = label(view.NO_ALARMS, "empty")
                col.addWidget(empty)
            col.addStretch(1)
            _swap(self.area, inner)
        self._rows = list(rows)
        if self.alarm_id is not None:
            self.refresh_choices()

    def _reset(self) -> None:
        self.alarm_id = None
        self.verdict.set_current(None)
        self.finding.clear()
        self.form.hide()

    def open_form(self, alarm_id: str) -> None:
        row = next((r for r in self._rows or [] if r.alarm_id == alarm_id), None)
        if row is None:
            return
        if alarm_id != self.alarm_id:
            self.verdict.set_current(None)
            self.finding.clear()
            self.part.clear()
        self.alarm_id = alarm_id
        self.form_title.setText(f"{row.title}, {row.when}")
        self.form.show()
        self.set_rows(self._rows)  # marks the row and fills the parts
        self.finding.setFocus()

    def close_form(self) -> None:
        self._reset()
        self.set_rows(self._rows or [])

    def refresh_choices(self) -> None:
        """Fill the part picker again, keeping the part picked. A lone suspect is not
        picked for the person: naming the part is their call."""
        choices = self.choices_of(self.alarm_id)
        if choices != [(self.part.itemData(i), self.part.itemText(i)) for i in range(self.part.count())]:
            picked = self.part.currentData()
            self.part.blockSignals(True)
            self.part.clear()
            for part_id, text in choices:
                self.part.addItem(text, part_id)
            self.part.setCurrentIndex(self.part.findData(picked) if picked else -1)
            self.part.blockSignals(False)
        self._draw_form()

    def problem_text(self) -> str:
        return view.answer_problem(self.verdict.current(), self.part.currentData(),
                                   [self.part.itemData(i) for i in range(self.part.count())])

    def _draw_form(self, *_args) -> None:
        problem = self.problem_text()
        self.problem.setText(problem)
        self.problem.setVisible(bool(problem))
        self.save.setEnabled(not problem)

    def submit(self) -> None:
        if self.alarm_id is None or self.problem_text():
            return
        self.answered.emit(self.alarm_id, self.verdict.current(), self.part.currentData(),
                           self.finding.text().strip())
