"""Sparkline: a 30px line of recent values, with an optional dashed threshold."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QPointF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..theme import tokens as t


class Sparkline(QWidget):
    """`values` may hold None for gaps. Values are clamped to [lo, hi].

    The threshold line is a shape, so it is drawn in RED (dashed, 60%), as the mockup does.
    """

    H = 30

    def __init__(self, values: Sequence[float | None] = (), lo: float = 0.0, hi: float = 1.0,
                 threshold: float | None = None, parent: QWidget | None = None, *, color=t.DOT):
        super().__init__(parent)
        self._values = list(values)
        self._lo, self._hi = lo, hi
        self._threshold = threshold
        self._color = t.qcolor(color) if isinstance(color, str) else QColor(color)
        self.setFixedHeight(self.H)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:
        return QSize(240, self.H)

    def values(self) -> list[float | None]:
        return list(self._values)

    def set_values(self, values: Sequence[float | None], lo: float | None = None,
                   hi: float | None = None, threshold: float | None = ...) -> None:
        self._values = list(values)
        if lo is not None:
            self._lo = lo
        if hi is not None:
            self._hi = hi
        if threshold is not ...:
            self._threshold = threshold
        self.update()

    def set_color(self, color) -> None:
        self._color = t.qcolor(color) if isinstance(color, str) else QColor(color)
        self.update()

    def _y(self, v: float) -> float:
        span = (self._hi - self._lo) or 1.0
        v = min(max(v, self._lo), self._hi)
        return self.height() - 3 - (v - self._lo) / span * (self.height() - 6)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        if self._threshold is not None:
            c = t.qcolor(t.RED)
            c.setAlphaF(0.6)
            pen = QPen(c, 1)
            pen.setStyle(Qt.PenStyle.CustomDashLine)
            pen.setDashPattern([3, 3])
            p.setPen(pen)
            y = self._y(self._threshold)
            p.drawLine(QPointF(0, y), QPointF(w, y))
        n = len(self._values)
        if n < 2:
            return
        pen = QPen(self._color, 1.5)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        seg = QPolygonF()
        for k, v in enumerate(self._values):
            if v is None:
                if seg.size() > 1:
                    p.drawPolyline(seg)
                seg = QPolygonF()
                continue
            seg.append(QPointF(k / (n - 1) * w, self._y(v)))
        if seg.size() > 1:
            p.drawPolyline(seg)
