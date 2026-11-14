"""The Machine page's three charts, drawn with QPainter: a part's velocity over time, what moved in
the newest visit, and that visit's spectrum against the normal.

Each only draws what the page hands it, already worked out in readings.py. Red is for shapes only: an
abnormal reading's dot and the limit line. Every label is drawn in the theme's text colours.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.theme import fonts
from app.theme import tokens as t
from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from . import readings as rd

LEFT, RIGHT, TOP, BOTTOM = 34, 10, 12, 22  # room for the axis labels


def _text(p: QPainter, rect: QRectF, flags, text: str, colour=t.MUTED) -> None:
    p.setPen(t.qcolor(colour))
    p.drawText(rect, int(flags), text)


class _Chart(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFont(fonts.small())
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._empty = ""

    def minimumSizeHint(self) -> QSize:
        return QSize(200, 96)

    def sizeHint(self) -> QSize:
        return QSize(400, 160)

    def empty_text(self) -> str:
        return self._empty

    def _draw_empty(self, p: QPainter) -> bool:
        if self._empty:
            _text(p, QRectF(self.rect()).adjusted(12, 0, -12, 0),
                  Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self._empty)
        return bool(self._empty)


class VelocityChart(_Chart):
    """A reading's velocity in mm/s over time. A simulated point is hollow and a real one filled, so a
    chart with both can be read (contract 9.6). The 11 mm/s limit line is always drawn and labelled.
    Nothing from the projection is drawn here: it is a score with no unit (contract 9.5)."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.points: list[rd.Point] = []
        self.marks: list[rd.Mark] = []
        self.span = (0.0, 1.0)
        self.top = rd.LIMIT_MM_S
        self.tz = None

    def set_data(self, points: list[rd.Point], marks: list[rd.Mark], span: tuple[float, float], empty: str = "",
                 tz=None) -> None:
        self.points, self.span, self._empty, self.tz = list(points), span, empty, tz
        self.marks = [m for m in marks if span[0] <= m.at <= span[1]]
        self.top = rd.y_top(self.points)
        self.update()

    def plot(self) -> QRectF:
        return QRectF(LEFT, TOP, max(1, self.width() - LEFT - RIGHT), max(1, self.height() - TOP - BOTTOM))

    def x(self, at: float) -> float:
        r, (lo, hi) = self.plot(), self.span
        return r.left() + (at - lo) / max(1.0, hi - lo) * r.width()

    def y(self, v: float) -> float:
        r = self.plot()
        return r.bottom() - v / max(1e-9, self.top) * r.height()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.plot()
        fm = self.fontMetrics()
        step = 2 if self.top <= 14 else 5
        v = 0
        while v <= self.top:  # the mm/s grid
            p.setPen(QPen(t.qcolor(t.GRID), 1))
            p.drawLine(QPointF(r.left(), self.y(v)), QPointF(r.right(), self.y(v)))
            _text(p, QRectF(0, self.y(v) - 8, LEFT - 8, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                  str(v))
            v += step
        lo, hi = self.span
        for i in range(5):  # dates along the bottom
            at = lo + (hi - lo) * i / 4
            label = rd.day(datetime.fromtimestamp(at, UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), self.tz)
            w = fm.horizontalAdvance(label)
            x = min(max(self.x(at) - w / 2, r.left()), r.right() - w)
            _text(p, QRectF(x, r.bottom() + 4, w + 2, BOTTOM - 4), Qt.AlignmentFlag.AlignLeft, label)

        for m in self.marks:  # repairs
            x = self.x(m.at)
            p.setPen(QPen(t.qcolor(t.MUTED), 1, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(x, r.top() + 18), QPointF(x, r.bottom()))
            _text(p, QRectF(x + 4, r.bottom() - 18, fm.horizontalAdvance(m.label) + 4, 16),
                  Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, m.label)

        ly = self.y(rd.LIMIT_MM_S)  # the limit line: a shape, so red; its label is not
        p.setPen(QPen(t.qcolor(t.RED), 1.4, Qt.PenStyle.DashLine))
        p.drawLine(QPointF(r.left(), ly), QPointF(r.right(), ly))
        _text(p, QRectF(r.left() + 6, ly - 18, r.width(), 16),
              Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, rd.LIMIT_LABEL)

        if self._draw_empty(p) or not self.points:
            return
        path = QPainterPath(QPointF(self.x(self.points[0].at), self.y(self.points[0].velocity)))
        for pt in self.points[1:]:
            path.lineTo(self.x(pt.at), self.y(pt.velocity))
        p.setPen(QPen(t.qcolor(t.SOFT), 1.3))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        for i, pt in enumerate(self.points):
            newest = i == len(self.points) - 1
            radius = 4.2 if newest else 3.0
            colour = t.RED if pt.abnormal else t.TEXT
            p.setPen(QPen(t.qcolor(colour), 1.4))
            p.setBrush(t.qcolor(t.PANEL) if pt.simulated else t.qcolor(colour))
            p.drawEllipse(QPointF(self.x(pt.at), self.y(pt.velocity)), radius, radius)
        last = self.points[-1]
        label = rd.number(last.velocity)
        w = fm.horizontalAdvance(label) + 4
        lx = min(max(self.x(last.at) - w / 2, r.left()), r.right() - w)  # above the newest point
        ly = max(r.top(), min(self.y(last.velocity), *(self.y(q.velocity) for q in self.points[-4:])) - 24)
        _text(p, QRectF(lx, ly, w, 16), Qt.AlignmentFlag.AlignHCenter, label, t.TEXT)


class MovedChart(_Chart):
    """The newest visit's record: the readings that moved most, in spreads from normal, either way.
    No "counts as evidence" line: no such threshold is in the contract (plan 15.1)."""

    ROW = 22

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.rows: list[rd.Moved] = []

    def set_rows(self, rows: list[rd.Moved], empty: str = "") -> None:
        self.rows, self._empty = list(rows), empty
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._draw_empty(p) or not self.rows:
            return
        fm = self.fontMetrics()
        names_w = min(max(fm.horizontalAdvance(m.text) for m in self.rows) + 12, self.width() * 0.45)
        r = QRectF(12 + names_w, 4, max(1, self.width() - names_w - 60), self.height() - 24)
        top = max(abs(m.spreads) for m in self.rows) or 1.0
        neg = any(m.spreads < 0 for m in self.rows)
        zero = r.left() + (r.width() / 2 if neg else 0)
        scale = (r.width() / 2 if neg else r.width()) / top
        step = min(self.ROW, r.height() / len(self.rows))
        p.setPen(QPen(t.qcolor(t.GRID), 1))
        p.drawLine(QPointF(zero, r.top()), QPointF(zero, r.top() + step * len(self.rows)))
        for i, m in enumerate(self.rows):
            y = r.top() + i * step
            h = max(4.0, min(12.0, step - 8))
            name = fm.elidedText(m.text, Qt.TextElideMode.ElideRight, int(names_w - 12))
            _text(p, QRectF(4, y, names_w - 8, step), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                  name, t.SOFT)
            w = m.spreads * scale
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(t.qcolor(t.DOT))
            p.drawRoundedRect(QRectF(min(zero, zero + w), y + (step - h) / 2, abs(w), h), 2, 2)
            value = f"{'+' if m.spreads >= 0 else ''}{rd.number(m.spreads)}"
            vx = zero + w + 6 if m.spreads >= 0 else zero + w - 6 - fm.horizontalAdvance(value)
            _text(p, QRectF(vx, y, fm.horizontalAdvance(value) + 2, step), Qt.AlignmentFlag.AlignVCenter, value,
                  t.TEXT)
        _text(p, QRectF(4, self.height() - 18, self.width() - 8, 16), Qt.AlignmentFlag.AlignLeft,
              "spreads from normal, in the newest visit's last record")


class SpectrumChart(_Chart):
    """The newest visit's record as 20 band levels, log10 as the record holds them (contract 9.4), with
    the normal as a short line over each band when it is known. With none, no line is drawn."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.view: rd.Spectrum | None = None

    def set_view(self, view: rd.Spectrum | None, empty: str = "") -> None:
        self.view, self._empty = view, empty
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._draw_empty(p) or self.view is None:
            return
        v = self.view
        values = list(v.bars) + list(v.normal)
        lo, hi = min(values), max(values)
        lo, hi = int(lo) - (1 if lo < int(lo) else 0), int(hi) + 1  # whole decades
        r = QRectF(LEFT, TOP + 10, max(1, self.width() - LEFT - RIGHT), max(1, self.height() - TOP - 10 - BOTTOM))

        def y(level: float) -> float:
            return r.bottom() - (level - lo) / max(1e-9, hi - lo) * r.height()

        for level in range(lo, hi + 1):
            p.setPen(QPen(t.qcolor(t.GRID), 1))
            p.drawLine(QPointF(r.left(), y(level)), QPointF(r.right(), y(level)))
            _text(p, QRectF(0, y(level) - 8, LEFT - 8, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                  str(level))
        n = len(v.bars)
        w = r.width() / n
        for i, level in enumerate(v.bars):
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(t.qcolor(t.SOFT))
            p.drawRect(QRectF(r.left() + i * w + 2, y(level), w - 4, r.bottom() - y(level)))
            if v.normal:
                p.setPen(QPen(t.qcolor(t.TEXT), 2))
                p.drawLine(QPointF(r.left() + i * w, y(v.normal[i])), QPointF(r.left() + (i + 1) * w, y(v.normal[i])))
        if v.edges:  # a few band edges in Hz along the bottom
            fm = self.fontMetrics()
            for i in (0, n // 4, n // 2, 3 * n // 4, n):
                label = f"{v.edges[i]:.3g}" + (" Hz" if i == n else "")
                lw = fm.horizontalAdvance(label)
                x = min(max(r.left() + i * w - lw / 2, r.left()), r.right() - lw)
                _text(p, QRectF(x, r.bottom() + 4, lw + 2, BOTTOM - 4), Qt.AlignmentFlag.AlignLeft, label)
        _text(p, QRectF(r.left(), 0, r.width(), TOP + 8), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
              ("line: " + v.note if v.normal else v.note) + "  ·  bars: newest record, log10 band level")
