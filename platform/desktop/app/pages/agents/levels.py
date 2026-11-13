"""A list of steps, one marked on with a red dot: the agent fleet, the protection levels."""

from __future__ import annotations

from app.theme import fonts
from app.theme import tokens as t
from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

DOT = 7
GAP = 8
ROW_GAP = 6
LINE_H = 19  # 13px text at the mockup's 1.45 line height


class Levels(QWidget):
    """Steps as (text, on, planned). On: red dot, text in TEXT. Off: a hollow ring in
    MUTED. Planned: the same at 60% opacity."""

    def __init__(self, steps: list[tuple[str, bool, bool]] = (), parent=None):
        super().__init__(parent)
        self.setFont(fonts.body())
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.steps: list[tuple[str, bool, bool]] = []
        self.set_steps(steps)

    def set_steps(self, steps) -> None:
        self.steps = [(str(a), bool(b), bool(c)) for a, b, c in steps]
        self.setFixedHeight(self._row_h() * len(self.steps) + ROW_GAP * max(0, len(self.steps) - 1))
        self.update()

    def _row_h(self) -> int:
        return max(LINE_H, self.fontMetrics().height() + 2)

    def sizeHint(self) -> QSize:
        w = max((self.fontMetrics().horizontalAdvance(s) for s, _, _ in self.steps), default=0)
        return QSize(DOT + GAP + w, self.height())

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rh = self._row_h()
        for i, (text, on, planned) in enumerate(self.steps):
            y = i * (rh + ROW_GAP)
            p.setOpacity(0.6 if planned and not on else 1.0)
            c = QPointF(DOT / 2 + 0.75, y + rh / 2)
            if on:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(t.qcolor(t.RED))
                p.drawEllipse(c, DOT / 2, DOT / 2)
            else:
                p.setPen(QPen(t.qcolor(t.MUTED), 1.5))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(c, DOT / 2 - 0.75, DOT / 2 - 0.75)
            p.setPen(t.qcolor(t.TEXT if on else t.MUTED))
            p.drawText(QRectF(DOT + GAP, y, self.width() - DOT - GAP, rh),
                       int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), text)
