"""Small bars drawn with QPainter for the Evidence page."""

from __future__ import annotations

from app.theme import tokens as T
from app.theme.tokens import qcolor
from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QSizePolicy, QWidget


class _Bar(QWidget):
    HEIGHT = 6

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(self.HEIGHT + 6)

    def sizeHint(self) -> QSize:
        return QSize(200, self.HEIGHT + 6)

    def _track(self, p: QPainter) -> QRectF:
        r = QRectF(0, 3, self.width(), self.HEIGHT)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(qcolor(T.RAISED))
        p.drawRoundedRect(r, 3, 3)
        return r

    def _fill(self, p: QPainter, r: QRectF, red: bool) -> None:
        if r.width() <= 0:
            return
        p.setBrush(qcolor(T.RED if red else T.SOFT))
        p.drawRoundedRect(r, 3, 3)


class CentredBar(_Bar):
    """A signed value on a bar centred at 0, with ticks at the evidence threshold."""

    def __init__(self, fraction: float, threshold_fraction: float, red: bool, parent=None):
        super().__init__(parent)
        self.fraction = fraction
        self.threshold = threshold_fraction
        self.red = red

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = self._track(p)
        half = track.width() / 2
        mid = track.left() + half
        p.fillRect(QRectF(mid, 0, 1, self.height()), qcolor(T.BORDER_STRONG))
        for side in (-1, 1):
            p.fillRect(QRectF(mid + side * self.threshold * half, 0, 1, self.height()), qcolor(T.MUTED))
        w = abs(self.fraction) * half
        left = mid if self.fraction >= 0 else mid - w
        self._fill(p, QRectF(left, track.top(), w, track.height()), self.red)
        p.end()


class FillBar(_Bar):
    """A value from the left edge, as a share of the bar."""

    def __init__(self, fraction: float, red: bool, parent=None):
        super().__init__(parent)
        self.fraction = fraction
        self.red = red

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = self._track(p)
        self._fill(p, QRectF(track.left(), track.top(), track.width() * self.fraction, track.height()), self.red)
        p.end()
