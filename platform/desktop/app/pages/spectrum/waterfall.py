"""Waterfall: the last 120 periods' band levels, newest on top (the mockup's s-wf)."""

from __future__ import annotations

from app.pages.spectrum import maths
from app.theme import tokens as T
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath
from PySide6.QtWidgets import QSizePolicy, QWidget


def waterfall_image(records: list[dict], periods: int = maths.WATERFALL_PERIODS) -> QImage:
    """20 x `periods` image, newest record in row 0. A record without a
    spectrum leaves its row empty."""
    img = QImage(maths.N_BANDS, periods, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    dot = T.qcolor(T.DOT)
    for row, rec in enumerate(reversed(records[-periods:])):
        s = maths.spectrum_of(rec)
        if s is None:
            continue
        for col, v in enumerate(s):
            c = QColor(dot)
            c.setAlphaF(maths.waterfall_alpha(v))
            img.setPixelColor(col, row, c)
    return img


class Waterfall(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._img: QImage | None = None
        self._count = 0
        self.setMinimumSize(200, 120)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_records(self, records: list[dict]) -> None:
        self._count = sum(1 for r in records[-maths.WATERFALL_PERIODS :] if maths.spectrum_of(r))
        self._img = waterfall_image(records) if self._count else None
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        clip = QPainterPath()
        clip.addRoundedRect(r, 6, 6)
        p.setClipPath(clip)
        bg = T.qcolor(T.BG)
        p.fillRect(r, bg)
        if self._img is None:
            p.setPen(T.qcolor(T.MUTED))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, "No spectra yet")
            return
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        p.drawImage(r, self._img)
        # 1 px gaps between the band columns, as the mockup draws them.
        bw = r.width() / maths.N_BANDS
        for k in range(1, maths.N_BANDS + 1):
            p.fillRect(QRectF(round(k * bw) - 1, 0, 1, r.height()), bg)
