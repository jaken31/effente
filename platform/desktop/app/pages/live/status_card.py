"""The Live page's status card: headline, sub line, meta tiles, and the alarm question.

The headline is logic.headline (contract section 5: `wrong` decides it). The
model's line and the problems come after it, fault type first, never merged.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ...data import logic
from ...theme import fonts
from ...theme import tokens as t
from ...widgets import Button, Pill, StatusDot
from .common import clock, label, periods

HEADLINE_DOT = {"wrong": t.WARN, "ok": t.NORMAL}


class LinkLabel(QLabel):
    """Soft text with a faint underline that acts as a link."""

    clicked = Signal()

    def __init__(self, text: str = "", parent: QWidget | None = None):
        super().__init__(text, parent)
        self.setProperty("role", "soft")
        self.setFont(fonts.body())
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        p = QPainter(self)
        fm = self.fontMetrics()
        w = min(self.width(), fm.horizontalAdvance(self.text()))
        y = (self.height() + fm.height()) / 2 - fm.descent() + 3
        p.setPen(QPen(t.qcolor(t.TEXT if self.underMouse() else t.BORDER_STRONG), 1))
        p.drawLine(0, int(y), int(w), int(y))

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(e)


class MetaTile(QWidget):
    """One of the status card's meta tiles: a small label over a value. A link."""

    clicked = Signal()

    def __init__(self, caption: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self.caption = label(caption, "muted", self, fonts.font(t.FONT_SMALL))
        self.value = label("–", "strong", self, fonts.big_value(19))
        lay.addWidget(self.caption)
        lay.addWidget(self.value)

    def set(self, caption: str, value: str) -> None:
        self.caption.setText(caption)
        self.value.setText(value)

    def event(self, e):
        if e.type() in (e.Type.HoverEnter, e.Type.HoverLeave):
            f = self.value.font()
            f.setUnderline(e.type() == e.Type.HoverEnter)
            self.value.setFont(f)
        return super().event(e)

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(e)


class _AskStrip(QFrame):
    """The strip under the status: "Was this a real problem?"."""

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        r = QRectF(self.rect())
        p.fillRect(r.adjusted(0, 0, 0, 0), t.qcolor(t.ASK_BG))
        p.setPen(QPen(t.qcolor(t.BORDER), 1))
        p.drawLine(r.topLeft(), r.topRight())


class StatusCard(QFrame):
    """navigate(key) asks for another page; focus_controls() asks for the controls panel.
    answer(alarm_id, verdict) answers; later(alarm_id) snoozes on this laptop only."""

    navigate = Signal(str)
    focus_controls = Signal()
    answer = Signal(str, str)
    later = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setProperty("cls", "panel")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(1, 1, 1, 1)
        outer.setSpacing(0)

        main = QWidget(self)
        ml = QHBoxLayout(main)
        ml.setContentsMargins(19, 17, 19, 17)
        ml.setSpacing(16)
        self.dot = StatusDot(t.MUTED, 10, main)
        dot_box = QVBoxLayout()
        dot_box.setContentsMargins(0, 14, 0, 0)
        dot_box.addWidget(self.dot)
        dot_box.addStretch(1)
        ml.addLayout(dot_box)

        text = QVBoxLayout()
        text.setSpacing(4)
        self.headline = label("", "strong", main, fonts.font(t.FONT_STATUS, 700, display=True, tracking=0.004))
        sub_row = QHBoxLayout()
        sub_row.setSpacing(8)
        self.sub = label("", "soft", main, fonts.body())
        self.possible = Pill("possible", t.WARN, main)
        self.possible.hide()
        sub_row.addWidget(self.sub)
        sub_row.addWidget(self.possible)
        sub_row.addStretch(1)
        self.problems = label("", "soft", main, fonts.body())
        self.problems.setTextFormat(Qt.TextFormat.RichText)
        self.problems.hide()
        self.note = label("", "muted", main, fonts.body())
        self.note.hide()
        text.addWidget(self.headline)
        text.addLayout(sub_row)
        text.addWidget(self.problems)
        text.addWidget(self.note)
        ml.addLayout(text, 1)

        meta = QHBoxLayout()
        meta.setSpacing(28)
        self.tile_state = MetaTile("Abnormal for", main)
        self.tile_since = MetaTile("Since", main)
        self.tile_protection = MetaTile("Protection", main)
        self.tile_sensitivity = MetaTile("Sensitivity", main)
        self.tile_state.clicked.connect(lambda: self.navigate.emit("history"))
        self.tile_since.clicked.connect(lambda: self.navigate.emit("history"))
        self.tile_protection.clicked.connect(lambda: self.navigate.emit("device"))
        self.tile_sensitivity.clicked.connect(self.focus_controls.emit)
        for tile in self.tiles():
            meta.addWidget(tile)
        ml.addLayout(meta)
        ml.setAlignment(meta, Qt.AlignmentFlag.AlignVCenter)
        outer.addWidget(main)

        self.ask = _AskStrip(self)
        al = QHBoxLayout(self.ask)
        al.setContentsMargins(19, 10, 19, 10)
        al.setSpacing(10)
        self.ask_q = label("Was this a real problem?", "strong", self.ask, fonts.body())
        self.ask_d = label("", "soft", self.ask, fonts.body())
        self.more = LinkLabel("", self.ask)
        self.more.clicked.connect(lambda: self.navigate.emit("history"))
        self.btn_real = Button("Real Problem", "secondary", self.ask, small=True)
        self.btn_false = Button("False Alarm", "secondary", self.ask, small=True)
        self.btn_later = Button("Later", "ghost", self.ask, small=True)
        for w in (self.ask_q, self.ask_d):
            al.addWidget(w)
        al.addStretch(1)
        for w in (self.more, self.btn_real, self.btn_false, self.btn_later):
            al.addWidget(w)
        al.insertSpacing(al.indexOf(self.btn_real), 4)
        self.btn_real.clicked.connect(lambda: self._answer("real"))
        self.btn_false.clicked.connect(lambda: self._answer("false"))
        self.btn_later.clicked.connect(lambda: self._alarm_id and self.later.emit(self._alarm_id))
        outer.addWidget(self.ask)
        self.ask.hide()
        self._alarm_id: str | None = None
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def tiles(self) -> list[MetaTile]:
        return [self.tile_state, self.tile_since, self.tile_protection, self.tile_sensitivity]

    def asked_alarm(self) -> str | None:
        return self._alarm_id if self.ask.isVisible() or not self.ask.isHidden() else None

    def _answer(self, verdict: str) -> None:
        if self._alarm_id:
            self.answer.emit(self._alarm_id, verdict)

    # ---- drawing ------------------------------------------------------------

    def show_state(self, state, records: list, snoozed: set) -> None:
        head = state.headline()
        self.headline.setText(head["text"])
        self.dot.set_color(HEADLINE_DOT.get(head["state"], t.MUTED))

        ml = state.model_line()
        if ml is None:
            self.sub.setText("")
            self.possible.hide()
            self.note.hide()
        else:
            detail = logic.capitalise(ml["detail"]) if ml["detail"] else ""
            self.sub.setText(" · ".join(x for x in (ml["label"], detail) if x))
            self.possible.setVisible(bool(ml["possible"]))
            self.note.setText(ml["note"])
            self.note.setVisible(bool(ml["note"]))
        self._show_problems(state, head["state"])
        self._show_meta(state, records)
        self._show_ask(state, snoozed)

    def _show_problems(self, state, head_state: str) -> None:
        probs = state.problems() if head_state != "not_live" else []
        if not probs:
            self.problems.hide()
            return
        bits = []
        for p in probs:
            text = logic.capitalise(p.get("text") or p["name"])
            value = logic.problem_value_text(p)
            kind = logic.kind_label(p.get("kind"))
            bits.append(" · ".join(x for x in (f'<span style="color:{t.TEXT}">{text}</span>', value, kind) if x))
        dot = f'<span style="color:{t.MUTED}">Problems</span>&nbsp;&nbsp;'
        self.problems.setText(dot + "&nbsp;&nbsp;|&nbsp;&nbsp;".join(bits))
        self.problems.show()

    def _show_meta(self, state, records: list) -> None:
        rec = state.latest()
        diag = rec.get("diagnosis") if rec else None
        if rec and logic.is_abnormal(diag):
            self.tile_state.set("Abnormal for", periods(int(rec.get("abnormal_minutes") or 0)))
        else:
            self.tile_state.set("Diagnosis", logic.display_name(diag) if diag else "–")
        since = None
        if records:
            i = len(records) - 1
            while i > 0 and records[i - 1].get("diagnosis") == records[-1].get("diagnosis"):
                i -= 1
            since = records[i].get("at")
        self.tile_since.set("Since", clock(since) if since else "–")
        dev = state.devices().get(state.device_id()) if state.device_id() else None
        mode = dev.get("mode") if dev else None
        model = state.model()
        if mode and mode != logic.WATCHING:
            protection = logic.mode_name(mode)
        elif model and model.get("source"):
            protection = logic.capitalise(str(model["source"]))
        else:
            protection = "–"
        self.tile_protection.set("Protection", protection)
        sens = state.sensitivity()
        self.tile_sensitivity.set("Sensitivity", logic.sensitivity_name(sens["reported"]))

    def _show_ask(self, state, snoozed: set) -> None:
        open_alarms = [a for a in state.unanswered_alarms() if a["alarm_id"] not in snoozed]
        if not open_alarms:
            self._alarm_id = None
            self.ask.hide()
            return
        a = open_alarms[0]
        self._alarm_id = a["alarm_id"]
        if a.get("open"):
            self.ask_d.setText("The alarm above, still going")
        else:
            self.ask_d.setText(f"{logic.display_name(a.get('diagnosis'))} at {clock(a.get('start'))} · "
                               f"{periods(int(a.get('periods') or 0))}")
        more = len(open_alarms) - 1
        self.more.setText(f"{more} more to answer")
        self.more.setVisible(more > 0)
        self.ask.show()
