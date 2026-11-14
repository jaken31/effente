"""Small pieces the Machine page's panels share: the part's mark, the steps of a scan on its way, a
text label and a strip with a hairline under it.

A part's mark in the list is drawn from the same look as its pin on the model (logic.pin_look), so
the list is the legend for the pins. Red is for these shapes only, never for text.
"""

from __future__ import annotations

from app.theme import fonts
from app.theme import tokens as t
from app.theme.qss import css
from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import QFrame, QLabel, QSizePolicy, QWidget

from . import logic

MARK = 12  # px across, the size of a pin's dot in the list


def palette() -> dict:
    """The theme's colours for logic.pin_look. Read when drawing: a colour scheme may replace them.
    "bg" is the panel's colour, so a hollow mark looks empty on a panel and dark on the model."""
    return {"red": t.RED, "text": t.TEXT, "muted": t.MUTED, "bg": t.PANEL}


class PinMark(QWidget):
    """A part state as its pin: a filled or empty dot with a ring."""

    def __init__(self, state: str = "not_checked", parent: QWidget | None = None):
        super().__init__(parent)
        self._state = state
        self.setFixedSize(MARK + 4, MARK + 4)

    def state(self) -> str:
        return self._state

    def set_state(self, state: str) -> None:
        if state != self._state:
            self._state = state
            self.update()

    def look(self) -> dict:
        return logic.pin_look(self._state, palette())

    def paintEvent(self, _event) -> None:
        look = self.look()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(t.qcolor(look["ring"]), 1.6))
        p.setBrush(t.qcolor(look["colour"]))
        p.drawEllipse(QPointF(self.rect().center()) + QPointF(0.5, 0.5), MARK / 2 - 0.8, MARK / 2 - 0.8)


class Steps(QWidget):
    """The steps of a scan on its way, left to right, each a mark and its name with a line between.

        done    a solid light dot
        now     a light ring: this is what is happening
        failed  a red ring: it stopped here
        todo    a dim dot
    """

    DOT = 9
    GAP = 8  # between a mark and its name
    LINE = 28  # the line between two steps, with its own space each side
    PAD = 10

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._steps: list[dict] = []
        self.setFont(fonts.small())
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def steps(self) -> list[dict]:
        return self._steps

    def set_steps(self, steps: list[dict]) -> None:
        if steps != self._steps:
            self._steps = [dict(s) for s in steps]
            self.updateGeometry()
            self.update()

    def sizeHint(self) -> QSize:
        fm = self.fontMetrics()
        w = sum(self.DOT + self.GAP + fm.horizontalAdvance(s["label"]) for s in self._steps)
        w += max(0, len(self._steps) - 1) * (self.LINE + 2 * self.PAD)
        return QSize(w + 2, max(fm.height(), self.DOT) + 4)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        fm = self.fontMetrics()
        mid = self.height() / 2
        x = 1.0
        for i, step in enumerate(self._steps):
            if i:
                p.setPen(QPen(t.qcolor(t.BORDER_STRONG), 1))
                p.drawLine(QPointF(x + self.PAD, mid), QPointF(x + self.PAD + self.LINE, mid))
                x += self.LINE + 2 * self.PAD
            mark = step["mark"]
            centre = QPointF(x + self.DOT / 2, mid)
            if mark in ("done", "todo"):
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(t.qcolor(t.DOT if mark == "done" else t.DIM))
                p.drawEllipse(centre, self.DOT / 2, self.DOT / 2)
            else:
                p.setPen(QPen(t.qcolor(t.RED if mark == "failed" else t.TEXT), 1.6))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(centre, self.DOT / 2 - 0.8, self.DOT / 2 - 0.8)
            x += self.DOT + self.GAP
            width = fm.horizontalAdvance(step["label"])
            p.setPen(t.qcolor(t.MUTED if mark == "todo" else t.TEXT))  # never red: the ring says it failed
            p.drawText(QRectF(x, 0, width + 1, self.height()),
                       int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), step["label"])
            x += width


def label(text: str = "", role: str = "soft", font=None, *, wrap: bool = False) -> QLabel:
    """A plain-text label in one of the theme's text roles. Never rich text: names and entries are
    typed by people."""
    w = QLabel(text)
    w.setTextFormat(Qt.TextFormat.PlainText)
    w.setProperty("role", role)
    w.setFont(font or fonts.body())
    w.setWordWrap(wrap)
    return w


def keep_place(widget: QWidget) -> QWidget:
    """Hide and show this widget without the layout around it moving."""
    policy = widget.sizePolicy()
    policy.setRetainSizeWhenHidden(True)
    widget.setSizePolicy(policy)
    return widget


class Strip(QFrame):
    """A full-width band inside a panel, with a hairline under it."""

    def __init__(self, name: str, parent: QWidget | None = None, *, tinted: bool = False):
        super().__init__(parent)
        self.setObjectName(name)
        bg = css(t.ASK_BG) if tinted else "transparent"
        self.setStyleSheet(f"#{name} {{ background: {bg}; border: 0; border-bottom: 1px solid {css(t.BORDER)}; }}")
