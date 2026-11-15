"""The Machine page's three charts, drawn with QPainter: a part's velocity over time, what moved in
the newest visit, and that visit's spectrum against the normal.

Each only draws what the page hands it, already worked out in readings.py. Red is for shapes only: an
abnormal reading's dot and the limit line. Every label is drawn in the theme's text colours.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

from app.theme import fonts
from app.theme import tokens as t
from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from . import readings as rd

LEFT, RIGHT, TOP, BOTTOM = 34, 10, 12, 22  # room for the axis labels
UNIT_H = 14


def _text(p: QPainter, rect: QRectF, flags, text: str, colour=t.MUTED) -> None:
    p.setPen(t.qcolor(colour))
    p.drawText(rect, int(flags), text)


def grid_step(top: float) -> int:
    """How far apart the mm/s grid lines are: 2 up to 14 mm/s, 5 above, and wider
    still for a value far off the scale, so there are never more than about a
    dozen lines however wild a reading is."""
    if not math.isfinite(top):  # one line, at 0
        return 1
    step = 2 if top <= 14 else 5
    for wider in (2, 2.5, 2):  # 5, 10, 25, 50, then tens of that
        if top / step <= 12:
            return step
        step = int(step * wider)
    while top / step > 12:
        step *= 10
    return step


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
        top = TOP + UNIT_H  # the axis's unit sits above its numbers
        return QRectF(LEFT, top, max(1, self.width() - LEFT - RIGHT), max(1, self.height() - top - BOTTOM))

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
        step = grid_step(self.top)
        v = 0
        _text(p, QRectF(4, 0, LEFT + 40, UNIT_H), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "mm/s")
        while v <= self.top and math.isfinite(self.top):  # the mm/s grid
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

        ly = self.y(rd.LIMIT_MM_S)  # the limit line: a shape, so red; its label is not
        for m in self.marks:  # repairs: under the limit line, so they never cross its label
            x = self.x(m.at)
            p.setPen(QPen(t.qcolor(t.MUTED), 1, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(x, ly + 4), QPointF(x, r.bottom()))
            w = fm.horizontalAdvance(m.label) + 4
            _text(p, QRectF(min(x + 4, r.right() - w), ly + 4, w, 16),
                  Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, m.label)

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
        _text(p, self.newest_label_rect(), Qt.AlignmentFlag.AlignRight, rd.number(self.points[-1].velocity), t.TEXT)

    def newest_label_rect(self) -> QRectF:
        """Where the newest reading's value goes: just left of its point, on the side away from the line
        coming into it, so it reads as that point's and not a neighbour's. A place that would cover another
        point or leave the plot is passed over: next is the other side, then level with the point."""
        r, last = self.plot(), self.points[-1]
        w = self.fontMetrics().horizontalAdvance(rd.number(last.velocity)) + 4
        x, y = self.x(last.at), self.y(last.velocity)
        left = max(r.left(), x - 8 - w)
        under, over, level = QRectF(left, y + 4, w, 16), QRectF(left, y - 20, w, 16), QRectF(left, y - 8, w, 16)
        rising = len(self.points) < 2 or last.velocity >= self.points[-2].velocity
        others = [QPointF(self.x(p.at), self.y(p.velocity)) for p in self.points[:-1]]

        def clear(box: QRectF) -> bool:
            pad = box.adjusted(-4, -4, 4, 4)
            return r.contains(box) and not any(pad.contains(o) for o in others)

        tries = (over, under, level) if rising else (under, over, level)
        return next((box for box in tries if clear(box)), tries[0] if r.contains(tries[0]) else level)


class MovedChart(_Chart):
    """The newest visit's record: the readings that moved most, in spreads from normal, either way.
    No "counts as evidence" line: no such threshold is in the contract (plan 15.1)."""

    ROW = 22
    ROW_TWO = 34  # when a name takes two lines
    BAR_MIN = 120

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.rows: list[rd.Moved] = []

    def set_rows(self, rows: list[rd.Moved], empty: str = "") -> None:
        self.rows, self._empty = list(rows), empty
        self.update()

    def columns(self) -> tuple[float, float]:
        """The width of the names and of the values. The names take the room they need, leaving the bars
        at least BAR_MIN."""
        fm = self.fontMetrics()
        values_w = max(fm.horizontalAdvance(f"+{rd.number(m.spreads)}") for m in self.rows) + 12
        names_w = min(max(fm.horizontalAdvance(m.text) for m in self.rows) + 16,
                      self.width() - 12 - self.BAR_MIN - values_w)
        return names_w, values_w

    def name_shown(self, row: rd.Moved) -> str:
        """The name as drawn: on one line, on two when it is too long for one, and cut with an ellipsis only
        when two lines are not enough."""
        fm, room = self.fontMetrics(), int(self.columns()[0] - 12)
        if fm.horizontalAdvance(row.text) <= room:
            return row.text
        first = ""
        for word in row.text.split(" "):
            if first and fm.horizontalAdvance(f"{first} {word}") > room:
                break
            first = f"{first} {word}".strip()
        rest = row.text[len(first):].strip()
        if not first or fm.horizontalAdvance(first) > room:  # one long word
            return fm.elidedText(row.text, Qt.TextElideMode.ElideRight, room)
        return f"{first}\n{fm.elidedText(rest, Qt.TextElideMode.ElideRight, room)}"

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._draw_empty(p) or not self.rows:
            return
        fm = self.fontMetrics()
        names_w, values_w = self.columns()
        r = QRectF(12 + names_w, 4, max(1, self.width() - 12 - names_w - values_w), self.height() - 24)
        top = max(abs(m.spreads) for m in self.rows) or 1.0
        neg = any(m.spreads < 0 for m in self.rows)
        zero = r.left() + (r.width() / 2 if neg else 0)
        scale = (r.width() / 2 if neg else r.width()) / top
        names = [self.name_shown(m) for m in self.rows]
        step = min(self.ROW_TWO if any("\n" in n for n in names) else self.ROW, r.height() / len(self.rows))
        p.setPen(QPen(t.qcolor(t.GRID), 1))
        p.drawLine(QPointF(zero, r.top()), QPointF(zero, r.top() + step * len(self.rows)))
        for i, m in enumerate(self.rows):
            y = r.top() + i * step
            h = max(4.0, min(12.0, step - 8))
            _text(p, QRectF(4, y, names_w - 8, step), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                  names[i], t.SOFT)
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
