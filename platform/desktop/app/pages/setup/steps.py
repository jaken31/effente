"""The Setup page's pieces: the local profile strip and the four numbered steps.

Each piece only draws what the page gives it and says what the person did through
a signal. The page alone talks to `state.setup`.
"""

from __future__ import annotations

from typing import ClassVar

from app.pages.log.marks import Strip, label
from app.theme import fonts
from app.theme import tokens as t
from app.theme.qss import css
from app.widgets import Button, Panel, SimChip
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from . import view

PAD_V, PAD_H = t.PANEL_BODY_PAD
FIELD_W = 300  # the five fields' inputs
QUESTION_W = 300  # a question's words, beside its answer
MARK = 10
# "I don't know" while picked: the selected-row tint, as for a picked option. Never red text.
DONT_KNOW_ON = (f"QPushButton {{ background: {css(t.ACCENT_BG)}; border-color: {css(t.LINK_HOVER_BORDER)}; "
                f"color: {t.TEXT}; }}")
EMPTY_COMBO = f"QComboBox {{ color: {t.MUTED}; }}"


def _restyle(w: QWidget, role: str) -> None:
    if w.property("role") != role:
        w.setProperty("role", role)
        w.style().unpolish(w)
        w.style().polish(w)


def _column(parent: QWidget, spacing: int = 12) -> QVBoxLayout:
    col = QVBoxLayout(parent)
    col.setContentsMargins(PAD_H, PAD_V, PAD_H, PAD_V + 2)
    col.setSpacing(spacing)
    return col


class DeviceMark(QWidget):
    """The device step's mark, always beside its words. A red ring when the device
    is using another entry than the wanted one; never a tick or a "good" colour."""

    LOOK: ClassVar[dict[str, tuple[str, str]]] = {
        view.TAKEN: ("DOT", "dot"),
        view.DIFFERS: ("RED", "ring"),
        view.WAITING: ("MUTED", "dashed"),
        view.NO_DEVICE: ("MUTED", "dashed"),
        view.NOTHING_SENT: ("MUTED", "dashed"),
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.status = ""
        self.setFixedSize(MARK + 2, MARK + 2)

    def set_status(self, status: str) -> None:
        if status != self.status:
            self.status = status
            self.update()

    def paintEvent(self, _event) -> None:
        look = self.LOOK.get(self.status)
        if look is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = t.qcolor(getattr(t, look[0]))
        c = QPointF(QRectF(self.rect()).center())
        if look[1] == "dot":
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color)
            p.drawEllipse(c, MARK / 2, MARK / 2)
            return
        pen = QPen(color, 1.6)
        if look[1] == "dashed":
            pen.setStyle(Qt.PenStyle.CustomDashLine)
            pen.setDashPattern([1.6, 1.6])
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(c, MARK / 2 - 0.8, MARK / 2 - 0.8)


# ---- the local profile ----------------------------------------------------------


class LocalProfile(Panel):
    """A name and a role kept on this laptop. Asked once: with none set the form is
    open; after that one line and "Change"."""

    saved = Signal(str, str)  # name, role

    def __init__(self, parent=None):
        super().__init__(view.LOCAL, "", parent=parent, number=False)
        self._local = None
        self.change_button = Button(view.CHANGE, small=True)
        self.change_button.clicked.connect(lambda: self.open_form(True))
        self.header_end.addWidget(self.change_button)

        inner = QWidget()
        col = _column(inner, 10)
        self.ask = label(view.LOCAL_ASK, "strong", fonts.font(t.FONT_BASE, 600))
        self.why = label(view.LOCAL_WHY, "muted", wrap=True)
        self.form = QWidget()
        fl = QHBoxLayout(self.form)
        fl.setContentsMargins(0, 0, 0, 0)
        fl.setSpacing(10)
        self.name = QLineEdit()
        self.name.setPlaceholderText(view.NAME_HINT)
        self.name.setMaxLength(60)
        self.role = QLineEdit()
        self.role.setPlaceholderText(view.ROLE_HINT)
        self.role.setMaxLength(40)
        for w in (self.name, self.role):
            w.setFont(fonts.body())
            w.textChanged.connect(self._draw_buttons)
            w.returnPressed.connect(self.save)
        self.save_button = Button(view.SAVE, "primary", small=True)
        self.save_button.clicked.connect(self.save)
        self.cancel_button = Button(view.CANCEL, "ghost", small=True)
        self.cancel_button.clicked.connect(lambda: self.open_form(False))
        fl.addWidget(self.name, 2)
        fl.addWidget(self.role, 2)
        fl.addWidget(self.save_button)
        fl.addWidget(self.cancel_button)
        self.fallback = label("", "muted", fonts.small(), wrap=True)
        for w in (self.ask, self.why, self.form, self.fallback):
            col.addWidget(w)
        self.add(inner)
        self.inner = inner
        self._editing = False
        self._asked = False  # the form is open because no local profile is set, not because "Change" was pressed
        self._answerer = None

    def set_local(self, local, answerer: str = "") -> None:
        if local == self._local and answerer == self._answerer:
            return
        self._local, self._answerer = local, answerer
        self.fallback.setText(view.fallback_note(answerer) if not local else "")
        if not local:
            self._editing, self._asked = True, True  # asked once: open until one is saved
        elif self._asked:
            self._editing, self._asked = False, False
        self._draw()

    def open_form(self, on: bool) -> None:
        self._editing = on or not self._local
        self._asked = False
        if self._editing:
            self.name.setText((self._local or {}).get("name", ""))
            self.role.setText((self._local or {}).get("role", ""))
            self.name.setFocus()
        self._draw()

    def save(self) -> None:
        if self.name.text().strip():
            self.saved.emit(self.name.text(), self.role.text())

    def saved_ok(self) -> None:
        self._editing = False
        self._draw()

    def _draw_buttons(self, *_args) -> None:
        self.save_button.setEnabled(bool(self.name.text().strip()))

    def _draw(self) -> None:
        editing = self._editing
        self.set_hint(view.local_hint(self._local))
        self.inner.setVisible(editing)
        self.ask.setVisible(editing)
        self.why.setVisible(editing)
        self.form.setVisible(editing)
        self.fallback.setVisible(editing and not self._local and bool(self.fallback.text()))
        self.change_button.setVisible(not editing and bool(self._local))
        self.cancel_button.setVisible(editing and bool(self._local))
        self._draw_buttons()


# ---- step 1: questions -----------------------------------------------------------


class _Answer(QWidget):
    """One question: its words, a line to type in and "I don't know"."""

    changed = Signal()

    def __init__(self, q: view.Question, parent=None):
        super().__init__(parent)
        self.q = q
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.question = label(q.text, "strong")
        self.question.setFixedWidth(QUESTION_W)
        row.addWidget(self.question)
        self.text = QLineEdit()
        self.text.setFont(fonts.body())
        self.text.setPlaceholderText(q.example)
        self.text.textChanged.connect(self.changed)
        self.dont_know = Button(view.DONT_KNOW, small=True)
        self.dont_know.setCheckable(True)
        self.dont_know.toggled.connect(self._toggled)
        row.addWidget(self.text, 1)
        row.addWidget(self.dont_know)

    def _toggled(self, on: bool) -> None:
        self.text.setEnabled(not on)
        self.text.setPlaceholderText(view.DONT_KNOW if on else self.q.example)
        if on:
            self.text.clear()
        self.dont_know.setStyleSheet(DONT_KNOW_ON if on else "")
        self.changed.emit()


class Questions(Panel):
    fill = Signal(list)  # the answers

    def __init__(self, parent=None):
        super().__init__("Questions", "plain words, and \"I don't know\" is an answer", parent=parent, number=1)
        inner = QWidget()
        col = _column(inner, 14)
        self.items = [_Answer(q) for q in view.QUESTIONS]
        for a in self.items:
            a.changed.connect(self._draw)
            col.addWidget(a)

        bar = QHBoxLayout()
        bar.setSpacing(12)
        self.fill_button = Button(view.FILL, "primary")
        self.fill_button.clicked.connect(lambda: self.fill.emit(self.answers()))
        self.count = label("", "muted", fonts.small())
        bar.addWidget(self.fill_button)
        bar.addWidget(self.count)
        bar.addStretch(1)
        col.addSpacing(2)
        col.addLayout(bar)
        self.mode = label("", "muted", fonts.small(), wrap=True)
        col.addSpacing(-6)
        col.addWidget(self.mode)

        # the run's result, under a hairline
        self.result = Strip("runResult")
        self.result.setStyleSheet(self.result.styleSheet().replace("border-bottom", "border-top"))
        rl = QVBoxLayout(self.result)
        rl.setContentsMargins(PAD_H, 12, PAD_H, 14)
        rl.setSpacing(6)
        head = QHBoxLayout()
        head.setSpacing(10)
        self.result_title = label("", "strong", fonts.font(t.FONT_BASE, 600))
        self.standin_chip = SimChip("Stand-in, simulated")
        self.standin_chip.setToolTip("No model read these answers: a rule matched words in them.")
        head.addWidget(self.result_title)
        head.addWidget(self.standin_chip)
        head.addStretch(1)
        self.result_text = label("", "soft", wrap=True)
        self.result_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        rl.addLayout(head)
        rl.addWidget(self.result_text)
        self.add(inner)
        self.add(self.result)
        self._can_fill = True
        self._draw()

    def answers(self) -> list[dict]:
        typed = {a.q.id: a.text.text() for a in self.items}
        dont = {a.q.id for a in self.items if a.dont_know.isChecked()}
        return view.answers(typed, dont)

    def set_mode(self, text: str) -> None:
        self.mode.setText(text)

    def set_can_fill(self, on: bool, running: bool) -> None:
        self._can_fill = on
        self.fill_button.setText(view.FILLING if running else view.FILL)
        for a in self.items:
            a.setEnabled(not running)
        self._draw()

    def set_run(self, r: view.Run) -> None:
        self.result.setVisible(r.status != view.NOT_RUN)
        self.result_title.setText(r.title)
        self.result_text.setText(r.text)
        self.standin_chip.setVisible(r.standin and r.status == view.DONE)
        _restyle(self.result_text, "strong" if r.status == view.FAILED else "soft")

    def _draw(self) -> None:
        self.count.setText(view.answered_text(self.answers()))
        self.fill_button.setEnabled(self._can_fill)


# ---- step 2: the machine profile -----------------------------------------------


class ProfileForm(Panel):
    """The five fields. Each is set by hand at any time; empty shows "Empty"."""

    edited = Signal(dict)  # the changed fields, values as the contract has them

    def __init__(self, parent=None):
        super().__init__("Machine profile", view.NOT_CONFIRMED, parent=parent, number=2)
        inner = QWidget()
        col = _column(inner, 12)
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(10)
        self.inputs: dict[str, QWidget] = {}
        for i, field in enumerate(view.LABELS):
            name = label(view.LABELS[field], "muted")
            if field == "speed_rpm":
                w = QLineEdit()
                w.setPlaceholderText(view.EMPTY)
                w.editingFinished.connect(self._speed_done)
                w.textEdited.connect(self._speed_typed)
            else:
                w = QComboBox()
                w.addItem(view.EMPTY, None)
                for value, text in view.CHOICES[field]:
                    w.addItem(text, value)
                w.activated.connect(lambda _i, f=field: self._picked(f))
            w.setFont(fonts.body())
            w.setFixedWidth(FIELD_W)
            self.inputs[field] = w
            grid.addWidget(name, i, 0)
            grid.addWidget(w, i, 1)
        grid.setColumnMinimumWidth(0, 130)
        grid.setColumnStretch(2, 1)
        col.addLayout(grid)
        self.speed_problem = label("", "strong", fonts.small())
        self.note = label("", "muted", wrap=True)
        col.addWidget(self.speed_problem)
        col.addWidget(self.note)
        self.add(inner)
        self.problem = ""

    def set_draft(self, draft: dict, hint: str, enabled: bool) -> None:
        for field, w in self.inputs.items():
            value = draft.get(field)
            w.setEnabled(enabled)
            if isinstance(w, QLineEdit):
                if not w.hasFocus() and not self.problem:
                    w.setText(view.speed_text(value))
            else:
                w.blockSignals(True)
                w.setCurrentIndex(max(0, w.findData(value)) if value is not None else 0)
                w.blockSignals(False)
                w.setStyleSheet(EMPTY_COMBO if w.currentData() is None else "")
        self.set_hint(hint)
        self.note.setText(view.profile_note(draft))
        self.note.setVisible(bool(self.note.text()))
        self._draw_problem()

    def _picked(self, field: str) -> None:
        w = self.inputs[field]
        w.setStyleSheet(EMPTY_COMBO if w.currentData() is None else "")
        self.edited.emit({field: w.currentData()})

    def _speed_typed(self, text: str) -> None:
        _, self.problem = view.parse_speed(text)
        self._draw_problem()

    def _speed_done(self) -> None:
        value, self.problem = view.parse_speed(self.inputs["speed_rpm"].text())
        self._draw_problem()
        if not self.problem:
            self.edited.emit({"speed_rpm": value})

    def _draw_problem(self) -> None:
        self.speed_problem.setText(self.problem)
        self.speed_problem.setVisible(bool(self.problem))


# ---- step 3: the starting model ---------------------------------------------------


class StartingModel(Panel):
    use = Signal()

    def __init__(self, parent=None):
        super().__init__("Starting model", "picked by code, not by the agent", parent=parent, number=3)
        inner = QWidget()
        col = _column(inner, 12)
        self.entry = label("", "strong", fonts.font(t.FONT_PAGE_TITLE, 600, display=True, tracking=t.TRACK_HEADING))
        # Fixed rows, filled in place: the choice is redrawn often and never changes shape.
        self.rows = QWidget()
        rg = QGridLayout(self.rows)
        rg.setContentsMargins(0, 0, 0, 0)
        rg.setHorizontalSpacing(12)
        rg.setVerticalSpacing(8)
        rg.setColumnMinimumWidth(0, 170)
        rg.setColumnStretch(1, 1)
        self.values: dict[str, QLabel] = {}
        for i, key in enumerate(view.CHOICE_KEYS):
            k = label(key, "muted")
            k.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            v = label("", "soft", wrap=True)
            v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            rg.addWidget(k, i, 0)
            rg.addWidget(v, i, 1)
            self.values[key] = v
        self.warning = label("", "soft", wrap=True)
        self.problem = label("", "strong", wrap=True)
        self.problem.hide()
        self.by_code = label(view.PICKED_BY_CODE, "muted", fonts.small(), wrap=True)
        bar = QHBoxLayout()
        bar.setSpacing(12)
        self.use_button = Button(view.USE, "primary")
        self.use_button.clicked.connect(self.use)
        self.confirm_line = label("", "soft", wrap=True)
        bar.addWidget(self.use_button)
        bar.addWidget(self.confirm_line, 1)
        for w in (self.entry, self.problem, self.rows, self.warning, self.by_code):
            col.addWidget(w)
        col.addSpacing(4)
        col.addLayout(bar)
        self.add(inner)

    def set_choice(self, choice, problem: str = "") -> None:
        self.entry.setText(view.choice_title(choice) if choice else view.NO_CHOICE if problem else view.LOOKING_UP)
        _restyle(self.entry, "strong" if choice else "muted")
        self.problem.setText(problem if not choice else "")
        self.problem.setVisible(bool(self.problem.text()))
        rows = dict(view.choice_rows(choice))
        for key, v in self.values.items():
            v.setText(rows.get(key, ""))
        self.rows.setVisible(bool(rows))
        self.warning.setText(view.choice_warning(choice))
        self.warning.setVisible(bool(self.warning.text()))

    def set_confirm(self, line: str, problem: str) -> None:
        self.confirm_line.setText(line)
        self.use_button.setEnabled(not problem)
        self.use_button.setToolTip(problem)

    def set_confirmed(self, confirmed: bool) -> None:
        """A confirmed profile shows who confirmed it, not a dead button."""
        self.use_button.setVisible(not confirmed)


# ---- step 4: the device ----------------------------------------------------------


class DeviceAnswer(Panel):
    def __init__(self, parent=None):
        super().__init__("The device's answer", "what it reports, not what it was asked", parent=parent, number=4)
        inner = QWidget()
        col = _column(inner, 8)
        row = QHBoxLayout()
        row.setSpacing(10)
        self.mark = DeviceMark()
        self.line = label("", "strong", fonts.font(t.FONT_BASE, 600))
        row.addWidget(self.mark)
        row.addWidget(self.line, 1)
        self.note = label("", "muted", wrap=True)
        col.addLayout(row)
        col.addWidget(self.note)
        self.add(inner)

    def set_device(self, d: view.Device) -> None:
        self.status = d.status
        self.mark.set_status(d.status)
        self.line.setText(d.line)
        self.note.setText(d.note)
        self.note.setVisible(bool(d.note))
