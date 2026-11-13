"""The 8 x 6 grid: each block's average (top) and hottest point (bottom), in degC
above room, straight from the record's `cells` and `spots`."""

from __future__ import annotations

from app.pages.thermal import heat_image, stats
from app.theme import tokens as T
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QSizePolicy, QWidget

GAP = 3
DARK_TEXT = "#14161a"


class CellGrid(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._cells: list[float] | None = None
        self._spots: list[float] | None = None
        self._bg: float | None = None
        self._range: tuple[float, float] | None = None
        self.setMinimumSize(stats.COLS * 40, stats.ROWS * 34)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_cells(self, cells, spots, background_c, lo_hi) -> None:
        self._cells, self._spots, self._bg, self._range = cells, spots, background_c, lo_hi
        self.update()

    def cell_texts(self, i: int) -> tuple[str, str]:
        return f"{self._cells[i]:.1f}", f"{(self._spots or self._cells)[i]:.1f}"

    def cell_fill(self, i: int) -> QColor:
        lo, hi = self._range
        hot = (self._spots or self._cells)[i]
        return QColor(*heat_image.color_for(self._bg + hot, lo, hi))

    def text_color(self, i: int) -> QColor:
        c = self.cell_fill(i)
        return QColor(DARK_TEXT) if stats.luminance((c.red(), c.green(), c.blue())) > 150 else QColor(T.WHITE)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self._cells or self._bg is None or self._range is None:
            p.setPen(T.qcolor(T.MUTED))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No grid in this reading")
            return
        cw = (self.width() - GAP * (stats.COLS - 1)) / stats.COLS
        ch = (self.height() - GAP * (stats.ROWS - 1)) / stats.ROWS
        small, bold = QFont(self.font()), QFont(self.font())
        small.setPixelSize(12)
        bold.setPixelSize(13)
        bold.setWeight(QFont.Weight.DemiBold)
        for i in range(stats.ROWS * stats.COLS):
            r, c = divmod(i, stats.COLS)
            box = QRectF(c * (cw + GAP), r * (ch + GAP), cw, ch)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(self.cell_fill(i))
            p.drawRoundedRect(box, 4, 4)
            avg, spot = self.cell_texts(i)
            ink = self.text_color(i)
            shadow = ink != QColor(DARK_TEXT)
            for font, text, y in ((small, avg, box.top() + 3 + 12), (bold, spot, box.bottom() - 5)):
                p.setFont(font)
                pt = QPointF(box.left() + 6, y)
                if shadow:
                    p.setPen(QColor(0, 0, 0, 150))
                    p.drawText(pt + QPointF(0, 1), text)
                col = QColor(ink)
                if font is small:
                    col.setAlphaF(0.85)
                p.setPen(col)
                p.drawText(pt, text)
