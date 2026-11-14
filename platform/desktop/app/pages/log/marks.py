"""Small pieces the Log page's panels share: the part state mark, a text label and a
strip with a hairline under it.

The mark is the one place a part's state has a colour, and it always sits beside
the state's words. Red is for the two states that ask for attention. `nothing_found`
and `not_checked` are both gray and differ in shape, and neither is drawn as a tick
or in a "good" colour: nothing here says a part is healthy.
"""

from __future__ import annotations

from app.theme import fonts
from app.theme import tokens as t
from app.theme.qss import css
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import QFrame, QLabel, QWidget

MARK = 10  # px across

# state name -> (colour token name, shape). Read the token when painting: a colour scheme may replace it.
MARKS = {
    "needs_repair": ("RED", "dot"),
    "possible": ("RED", "ring"),
    "nothing_found": ("MUTED", "dot"),
    "not_checked": ("MUTED", "dashed"),
}


def paint_state_mark(p: QPainter, rect: QRectF, state: str) -> None:
    """Draw the mark for a part state in the middle of `rect`. An unknown state draws nothing."""
    look = MARKS.get(state)
    if look is None:
        return
    color = t.qcolor(getattr(t, look[0]))
    shape = look[1]
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    c = QPointF(rect.center())
    if shape == "dot":
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawEllipse(c, MARK / 2, MARK / 2)
    else:
        pen = QPen(color, 1.6)
        if shape == "dashed":
            pen.setStyle(Qt.PenStyle.CustomDashLine)
            pen.setDashPattern([1.6, 1.6])
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(c, MARK / 2 - 0.8, MARK / 2 - 0.8)
    p.restore()


class StateMark(QWidget):
    def __init__(self, state: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self._state = state
        self.setFixedSize(MARK + 2, MARK + 2)

    def state(self) -> str:
        return self._state

    def set_state(self, state: str) -> None:
        if state != self._state:
            self._state = state
            self.update()

    def paintEvent(self, _event) -> None:
        paint_state_mark(QPainter(self), QRectF(self.rect()), self._state)


def label(text: str = "", role: str = "soft", font=None, *, wrap: bool = False) -> QLabel:
    """A plain-text label in one of the theme's text roles. Never rich text: names and
    entries are typed by people."""
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
