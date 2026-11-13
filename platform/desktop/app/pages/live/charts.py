"""QPainter charts for the Live and History pages.

Ports of the mockup's diagBand and lineChart. They draw what they are given:
labels and colours come from the page, which takes them from logic.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ...theme import fonts
from ...theme import tokens as t

AXIS_FONT_PX = 12
LABEL_FONT_PX = 11


def _font(px: float) -> QFont:
    return fonts.font(px)


def _label_font() -> QFont:
    # Chart row labels are small uppercase with letter spacing, like table headers.
    return fonts.font(LABEL_FONT_PX, 400, tracking=0.04, caps=True)


def _c(token: str) -> QColor:
    return t.qcolor(token)


@dataclass
class Segment:
    """One run of equal labels in a diagnosis band."""

    count: int
    text: str
    color: str


def segments(items: list[tuple[str, str]]) -> list[Segment]:
    """Group consecutive (text, colour) pairs into runs."""
    out: list[Segment] = []
    for text, color in items:
        if out and out[-1].text == text and out[-1].color == color:
            out[-1].count += 1
        else:
            out.append(Segment(1, text, color))
    return out


class DiagnosisBand(QWidget):
    """A strip of coloured runs, one per stretch of equal diagnosis.

    `label` is the row caption drawn in the left gutter ("" for none);
    `gutter` is that gutter's width, so the band lines up with charts below.
    """

    HEIGHT = 26

    def __init__(self, label: str = "", gutter: int = 124, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._label = label
        self._gutter = gutter
        self._segments: list[Segment] = []
        self._total = 0
        self.setFixedHeight(self.HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_items(self, items: list[tuple[str, str]]) -> None:
        self._segments = segments(items)
        self._total = len(items)
        self.update()

    def segment_texts(self) -> list[str]:
        return [s.text for s in self._segments]

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._label:
            p.setFont(_label_font())
            p.setPen(_c(t.SOFT))
            p.drawText(QRectF(0, 0, self._gutter, self.HEIGHT), Qt.AlignmentFlag.AlignVCenter, self._label)
        left = float(self._gutter)
        pw = max(1.0, self.width() - left - 8)
        band = QRectF(left, 3, pw, 20)
        if not self._total:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(_c(t.RAISED))
            p.drawRoundedRect(band, 6, 6)
            return
        clip = QPainterPath()
        clip.addRoundedRect(band, 6, 6)
        p.setClipPath(clip)
        bw = pw / self._total
        x = left
        p.setFont(_font(LABEL_FONT_PX))
        for s in self._segments:
            w = s.count * bw
            c = _c(s.color)
            c.setAlphaF(0.85)
            p.fillRect(QRectF(x, band.top(), max(0.0, w - 1), band.height()), c)
            if w > len(s.text) * 6.4 + 16:
                p.setPen(_c(t.BG))
                p.drawText(QRectF(x, band.top(), w, band.height()), Qt.AlignmentFlag.AlignCenter, s.text)
            x += w
        p.end()


@dataclass
class Series:
    values: list[float | None]
    color: str
    dashed: bool = False


@dataclass
class RefLine:
    y: float
    color: str
    label: str
    dash: tuple[float, float] = (5, 4)


@dataclass
class LineChartSpec:
    label: str = ""
    ymin: float = 0.0
    ymax: float = 1.0
    ticks: list[float] = field(default_factory=list)
    decimals: int = 1
    lines: list[RefLine] = field(default_factory=list)
    times: list[str] | None = None  # axis labels, one per value; None draws no axis


class LineChart(QWidget):
    """A small multi-series line chart with a left label gutter."""

    def __init__(self, height: int, gutter: int = 124, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._gutter = gutter
        self._spec = LineChartSpec()
        self._series: list[Series] = []
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_data(self, series: list[Series], spec: LineChartSpec) -> None:
        self._series = series
        self._spec = spec
        self.update()

    @property
    def spec(self) -> LineChartSpec:
        return self._spec

    def paintEvent(self, _event) -> None:
        s = self._spec
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        h = self.height()
        left = float(self._gutter if s.label else 44)
        top, right = 8.0, 8.0
        bottom = 22.0 if s.times else 6.0
        pw = max(1.0, self.width() - left - right)
        ph = max(1.0, h - top - bottom)
        lo, hi = s.ymin, s.ymax
        span = (hi - lo) or 1.0

        def yof(v: float) -> float:
            v = min(max(v, lo), hi)
            return top + ph * (1 - (v - lo) / span)

        if s.label:
            p.setFont(_label_font())
            p.setPen(_c(t.SOFT))
            p.drawText(QRectF(0, top, left, ph), Qt.AlignmentFlag.AlignVCenter, s.label)
        p.setFont(_font(AXIS_FONT_PX))
        for v in s.ticks or [lo, hi]:
            y = yof(v)
            p.setPen(QPen(_c(t.GRID), 1))
            p.drawLine(QPointF(left, y), QPointF(left + pw, y))
            p.setPen(_c(t.MUTED))
            p.drawText(QRectF(left - 48, y - 8, 40, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"{v:.{s.decimals}f}")
        for ln in s.lines:
            y = yof(ln.y)
            c = _c(ln.color)
            c.setAlphaF(0.85)
            pen = QPen(c, 1)
            pen.setDashPattern(list(ln.dash))
            p.setPen(pen)
            p.drawLine(QPointF(left, y), QPointF(left + pw, y))
            # Reference line captions are gray text: red stays on shapes only.
            p.setPen(_c(t.SOFT))
            p.drawText(QPointF(left + 6, y - 5), ln.label)
        for ser in self._series:
            n = len(ser.values)
            if n < 2:
                continue
            pen = QPen(_c(ser.color), 1.6)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            if ser.dashed:
                pen.setDashPattern([4 / 1.6, 3 / 1.6])
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            path = QPainterPath()
            pen_down = False
            for i, v in enumerate(ser.values):
                if v is None:
                    pen_down = False
                    continue
                pt = QPointF(left + i / (n - 1) * pw, yof(v))
                if pen_down:
                    path.lineTo(pt)
                else:
                    path.moveTo(pt)
                    pen_down = True
            p.drawPath(path)
        if s.times and len(s.times) > 1:
            n = len(s.times)
            p.setPen(_c(t.MUTED))
            p.setFont(_font(AXIS_FONT_PX))
            for k in range(6):
                i = round(k / 5 * (n - 1))
                x = left + i / (n - 1) * pw
                if k == 0:
                    r, al = QRectF(x, h - 18, 120, 16), Qt.AlignmentFlag.AlignLeft
                elif k == 5:
                    r, al = QRectF(x - 120, h - 18, 120, 16), Qt.AlignmentFlag.AlignRight
                else:
                    r, al = QRectF(x - 60, h - 18, 120, 16), Qt.AlignmentFlag.AlignHCenter
                p.drawText(r, al | Qt.AlignmentFlag.AlignVCenter, s.times[i])
        p.end()
