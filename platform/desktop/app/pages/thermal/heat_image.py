"""The thermal image and its colour bar (the mockup's paintThermal and th-scale).

Colours are the mockup's thermal ramp (gray to white to red), looked up once into a 256-entry table. Each
new frame builds one small QImage and one scaled pixmap; paintEvent only blits
it, so playing frames costs about a millisecond (see the timing test).
"""

from __future__ import annotations

from app.pages.thermal import stats
from app.theme import tokens as T
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QSizePolicy, QWidget

_LUT: list[tuple[int, int, int]] | None = None
_LUT_BYTES: list[bytes] | None = None


# The mockup's thermal stops, coldest first: gray to white, then red for the hottest.
# Fixed colours, not tokens: hot stays red whatever the colour scheme.
STOPS = (
    "#0b0b0b", "#1f1f1f", "#383838", "#565656", "#7a7a7a",
    "#a3a3a3", "#cfcfcf", "#f2f2f2", "#ff8a6a", "#ff4d2e",
)


def _ramp(i: int) -> tuple[int, int, int]:
    """The mockup's LUT: stop colours blended linearly, i in 0..255."""
    stops = [tuple(int(h[k : k + 2], 16) for k in (1, 3, 5)) for h in STOPS]
    t = i / 255 * (len(stops) - 1)
    k = min(len(stops) - 2, int(t))
    f = t - k
    return tuple(round(a + (b - a) * f) for a, b in zip(stops[k], stops[k + 1], strict=True))


def lut() -> list[tuple[int, int, int]]:
    """The thermal ramp sampled at 256 steps."""
    global _LUT, _LUT_BYTES
    if _LUT is None:
        _LUT = [_ramp(i) for i in range(256)]
        # Format_RGB32 is 0xffRRGGBB, stored little endian: B, G, R, 0xff.
        _LUT_BYTES = [bytes((b, g, r, 255)) for r, g, b in _LUT]
    return _LUT


def color_for(v: float, lo: float, hi: float) -> tuple[int, int, int]:
    return lut()[stats.color_index(v, lo, hi)]


def heat_qimage(values, w: int, h: int, lo: float, hi: float) -> QImage:
    """A w x h image of degC values (row 0 first) in the thermal colours."""
    lut()
    span = hi - lo
    table = _LUT_BYTES
    data = b"".join(table[max(0, min(255, round((v - lo) / span * 255)))] for v in values)
    return QImage(data, w, h, w * 4, QImage.Format.Format_RGB32).copy()


class HeatImage(QWidget):
    """Draws one 32 x 24 frame (or the 8 x 6 grid when no frame came), the
    hottest-point ring and label, the 4 x 4 block grid and the hovered pixel."""

    hovered = Signal(int, int)  # column, row in camera pixels; -1, -1 when none

    def __init__(self, parent: QWidget | None = None, full: bool = True):
        super().__init__(parent)
        self._full = full
        self._img: QImage | None = None
        self._pix: QPixmap | None = None
        self._pix_key = None
        self._hot: tuple[int, int, float] | None = None  # row, col, degC
        self._smooth = True
        self._grid = True
        self._hover: tuple[int, int] | None = None
        self.setMouseTracking(full)
        self.setMinimumSize(stats.W * 3, stats.H * 3)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        if full:
            self.setCursor(Qt.CursorShape.CrossCursor)

    # ---- data -------------------------------------------------------------

    def set_image(self, values, w: int, h: int, lo: float, hi: float, hot: tuple[int, int, float] | None) -> None:
        """values: w*h degC, row 0 first. hot: (row, col, degC) in 32 x 24 pixels."""
        self._img = heat_qimage(values, w, h, lo, hi) if values else None
        self._hot = hot
        self._pix = None
        self.update()

    def clear(self) -> None:
        self._img, self._hot, self._pix = None, None, None
        self.update()

    def set_smooth(self, on: bool) -> None:
        self._smooth = on
        self._pix = None
        self.update()

    def set_grid(self, on: bool) -> None:
        self._grid = on
        self.update()

    def smooth(self) -> bool:
        return self._smooth

    def grid(self) -> bool:
        return self._grid

    # ---- geometry ---------------------------------------------------------

    def scale(self) -> int:
        """Whole screen pixels per camera pixel, as large as fits, at least 3."""
        return max(3, min(self.width() // stats.W, self.height() // stats.H))

    def image_rect(self) -> QRectF:
        s = self.scale()
        w, h = stats.W * s, stats.H * s
        return QRectF((self.width() - w) // 2, (self.height() - h) // 2, w, h)

    def pixel_at(self, x: float, y: float) -> tuple[int, int] | None:
        r = self.image_rect()
        if not r.contains(QPointF(x, y)):
            return None
        s = self.scale()
        return min(stats.W - 1, int((x - r.left()) // s)), min(stats.H - 1, int((y - r.top()) // s))

    def pixel_center(self, col: int, row: int) -> QPointF:
        r, s = self.image_rect(), self.scale()
        return QPointF(r.left() + (col + 0.5) * s, r.top() + (row + 0.5) * s)

    def sizeHint(self) -> QSize:
        return QSize(stats.W * 18, stats.H * 18)

    # ---- events -----------------------------------------------------------

    def mouseMoveEvent(self, e) -> None:
        px = self.pixel_at(e.position().x(), e.position().y())
        if px != self._hover:
            self._hover = px
            self.update()
            self.hovered.emit(*(px or (-1, -1)))

    def leaveEvent(self, e) -> None:
        if self._hover is not None:
            self._hover = None
            self.update()
            self.hovered.emit(-1, -1)

    # ---- painting ---------------------------------------------------------

    def _pixmap(self, r: QRectF) -> QPixmap:
        key = (int(r.width()), int(r.height()), self._smooth)
        if self._pix is None or self._pix_key != key:
            # A grid-only image (8 x 6) always shows as blocks.
            smooth = self._smooth and self._img.width() == stats.W
            mode = Qt.TransformationMode.SmoothTransformation if smooth else Qt.TransformationMode.FastTransformation
            self._pix = QPixmap.fromImage(self._img.scaled(key[0], key[1], Qt.AspectRatioMode.IgnoreAspectRatio, mode))
            self._pix_key = key
        return self._pix

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.image_rect()
        if self._img is None:
            return
        clip = QPainterPath()
        clip.addRoundedRect(r, 6, 6)
        p.save()
        p.setClipPath(clip)
        p.drawPixmap(r.topLeft(), self._pixmap(r))
        s = self.scale()
        if self._full and self._grid:
            p.setPen(QPen(QColor(255, 255, 255, 46), 1))
            for k in range(1, stats.COLS):
                x = r.left() + k * 4 * s + 0.5
                p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
            for k in range(1, stats.ROWS):
                y = r.top() + k * 4 * s + 0.5
                p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
        p.restore()

        if self._hot is not None:
            row, col, val = self._hot
            c = self.pixel_center(col, row)
            rad = max(6.0, s * 0.9)
            p.setPen(QPen(QColor(T.WHITE), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(c, rad, rad)
            f = QFont(self.font())
            f.setPixelSize(13 if self._full else 12)
            f.setWeight(QFont.Weight.DemiBold)
            p.setFont(f)
            label = f"{val:.1f} °C"
            lw = p.fontMetrics().horizontalAdvance(label)
            off = max(9.0, s * 1.3)
            x = c.x() - off - lw if c.x() + off + lw > r.right() else c.x() + off
            y = c.y() + 4
            p.setPen(QColor(0, 0, 0, 170))  # shadow, so the label reads on white
            for dx, dy in ((1, 1), (0, 1), (1, 0)):
                p.drawText(QPointF(x + dx, y + dy), label)
            p.setPen(QColor(T.WHITE))
            p.drawText(QPointF(x, y), label)

        if self._full and self._hover is not None:
            col, row = self._hover
            pen = QPen(T.qcolor(T.RED), 1.5)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(QRectF(r.left() + col * s + 0.5, r.top() + row * s + 0.5, s - 1, s - 1))


class ColourBar(QWidget):
    """Vertical ramp with the range's top, middle and bottom."""

    def __init__(self, parent: QWidget | None = None, labels: bool = True):
        super().__init__(parent)
        self._lo: float | None = None
        self._hi: float | None = None
        self._labels = labels
        self.setFixedWidth(56 if labels else 10)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

    def set_range(self, lo: float | None, hi: float | None) -> None:
        self._lo, self._hi = lo, hi
        self.update()

    def label_texts(self) -> tuple[str, str, str] | None:
        if self._lo is None:
            return None
        return f"{self._hi:g} °C", f"{(self._lo + self._hi) / 2:.0f}", f"{self._lo:g} °C"

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bar = QRectF(0, 4, 10, self.height() - 8)
        g = QLinearGradient(bar.bottomLeft(), bar.topLeft())
        table = lut()
        for k in range(0, 256, 15):
            g.setColorAt(k / 255, QColor(*table[k]))
        g.setColorAt(1.0, QColor(*table[255]))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g)
        p.drawRoundedRect(bar, 5, 5)
        texts = self.label_texts()
        if not self._labels or texts is None:
            return
        f = QFont(self.font())
        f.setPixelSize(12)
        p.setFont(f)
        p.setPen(T.qcolor(T.SOFT))
        x, w = 18, self.width() - 18
        top = Qt.AlignmentFlag.AlignLeft
        p.drawText(QRectF(x, 0, w, 16), top | Qt.AlignmentFlag.AlignTop, texts[0])
        p.drawText(QRectF(x, self.height() / 2 - 8, w, 16), top | Qt.AlignmentFlag.AlignVCenter, texts[1])
        p.drawText(QRectF(x, self.height() - 16, w, 16), top | Qt.AlignmentFlag.AlignBottom, texts[2])
