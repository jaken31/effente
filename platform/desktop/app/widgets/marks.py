"""Small painted marks: StatusDot, Pill, FlagValue, SimChip.

Text is always painted in a text colour (TEXT, SOFT or MUTED). Colour that carries
meaning goes on the dot, never on the text (the RED rule).
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..theme import fonts
from ..theme import tokens as t


def _color(value) -> QColor:
    return t.qcolor(value) if isinstance(value, str) else QColor(value)


class StatusDot(QWidget):
    """A filled circle, 8px by default."""

    def __init__(self, color=t.DOT, size: int = 8, parent: QWidget | None = None):
        super().__init__(parent)
        self._color = _color(color)
        self._size = size
        self.setFixedSize(size, size)

    def color(self) -> QColor:
        return QColor(self._color)

    def set_color(self, color) -> None:
        self._color = _color(color)
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._color)
        p.drawEllipse(QRectF(0, 0, self._size, self._size))


class Pill(QWidget):
    """White text after a coloured dot, in a ring of the same colour at 45%."""

    PAD = 8
    GAP = 6
    DOT = 6
    H = 20

    def __init__(self, text: str = "", color=t.DOT, parent: QWidget | None = None):
        super().__init__(parent)
        self._text = text
        self._color = _color(color)
        self.setFont(fonts.chip())
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def text(self) -> str:
        return self._text

    def color(self) -> QColor:
        return QColor(self._color)

    def set_text(self, text: str) -> None:
        self._text = text
        self.updateGeometry()
        self.update()

    def set_color(self, color) -> None:
        self._color = _color(color)
        self.update()

    def sizeHint(self) -> QSize:
        w = self.fontMetrics().horizontalAdvance(self._text)
        return QSize(self.PAD * 2 + self.DOT + self.GAP + w, self.H)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def paintEvent(self, _event) -> None:
        paint_pill(QPainter(self), QRectF(0, (self.height() - self.H) / 2, self.width(), self.H), self._text, self._color, self.font())


def paint_pill(p: QPainter, rect: QRectF, text: str, color: QColor, font: QFont) -> None:
    """Draw a pill into rect (left aligned, natural width). Shared with DataTable."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setFont(font)
    w = Pill.PAD * 2 + Pill.DOT + Pill.GAP + p.fontMetrics().horizontalAdvance(text)
    r = QRectF(rect.left(), rect.center().y() - Pill.H / 2, min(w, rect.width()), Pill.H)
    ring = QColor(color)
    ring.setAlphaF(color.alphaF() * 0.45)
    p.setPen(QPen(ring, 1))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), Pill.H / 2, Pill.H / 2)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color)
    p.drawEllipse(QPointF(r.left() + Pill.PAD + Pill.DOT / 2, r.center().y()), Pill.DOT / 2, Pill.DOT / 2)
    p.setPen(t.qcolor(t.TEXT))
    tx = r.adjusted(Pill.PAD + Pill.DOT + Pill.GAP, 0, -Pill.PAD + 1, 0)
    p.drawText(tx, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
               p.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, int(tx.width()) + 1))
    p.restore()


class FlagValue(QWidget):
    """A value in white, with a small red dot before it when abnormal.

    `font` defaults to the body font; pass fonts.big_value() for the signals table.
    `alignment` places the dot and text together (left or right).
    """

    DOT = 7
    GAP = 8

    def __init__(self, text: str = "", abnormal: bool = False, parent: QWidget | None = None, *,
                 font: QFont | None = None, alignment=Qt.AlignmentFlag.AlignLeft, color=t.TEXT):
        super().__init__(parent)
        self._text = text
        self._abnormal = abnormal
        self._align = alignment
        self._color = t.text_color(color)
        self.setFont(font or fonts.body())
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    def text(self) -> str:
        return self._text

    def abnormal(self) -> bool:
        return self._abnormal

    def set_text(self, text: str) -> None:
        self._text = text
        self.updateGeometry()
        self.update()

    def set_abnormal(self, abnormal: bool) -> None:
        self._abnormal = bool(abnormal)
        self.updateGeometry()
        self.update()

    def set_value(self, text: str, abnormal: bool = False) -> None:
        self._text = text
        self.set_abnormal(abnormal)

    def sizeHint(self) -> QSize:
        fm = self.fontMetrics()
        w = fm.horizontalAdvance(self._text) + (self.DOT + self.GAP if self._abnormal else 0)
        return QSize(w + 1, fm.height())

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        paint_flag_value(p, QRectF(self.rect()), self._text, self._abnormal, self.font(), self._color, self._align)


def paint_flag_value(p: QPainter, rect: QRectF, text: str, abnormal: bool, font: QFont,
                     color: QColor, align=Qt.AlignmentFlag.AlignLeft) -> None:
    """Draw text with the abnormal dot before it. Shared with DataTable and KpiStrip."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setFont(font)
    fm = p.fontMetrics()
    tw = fm.horizontalAdvance(text)
    extra = FlagValue.DOT + FlagValue.GAP if abnormal else 0
    if align & Qt.AlignmentFlag.AlignRight:
        x = rect.right() - tw - extra
    elif align & Qt.AlignmentFlag.AlignHCenter:
        x = rect.center().x() - (tw + extra) / 2
    else:
        x = rect.left()
    if abnormal:
        # The mockup lifts the dot 2px above the text's middle.
        cy = rect.center().y() - 1
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(t.qcolor(t.RED))
        p.drawEllipse(QPointF(x + FlagValue.DOT / 2, cy), FlagValue.DOT / 2, FlagValue.DOT / 2)
    p.setPen(t.text_color(color))
    tx = QRectF(x + extra, rect.top(), max(0.0, rect.right() - x - extra) + 1, rect.height())
    p.drawText(tx, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
               fm.elidedText(text, Qt.TextElideMode.ElideRight, int(tx.width())))
    p.restore()


class SimChip(QWidget):
    """The "Simulated data" chip: dashed hairline, rounded ends, soft text."""

    PAD_H = 10
    PAD_V = 2

    def __init__(self, text: str = "Simulated data", parent: QWidget | None = None):
        super().__init__(parent)
        self._text = text
        self.setFont(fonts.chip())
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setToolTip("These readings did not come from a real sensor.")

    def text(self) -> str:
        return self._text

    def set_text(self, text: str) -> None:
        self._text = text
        self.updateGeometry()
        self.update()

    def sizeHint(self) -> QSize:
        fm = self.fontMetrics()
        return QSize(fm.horizontalAdvance(self._text) + 2 * self.PAD_H + 2, fm.height() + 2 * self.PAD_V + 2)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        pen = QPen(t.qcolor(t.BORDER), 1)
        pen.setStyle(Qt.PenStyle.CustomDashLine)
        pen.setDashPattern([3, 3])
        p.setPen(pen)
        path = QPainterPath()
        path.addRoundedRect(r, r.height() / 2, r.height() / 2)
        p.drawPath(path)
        p.setPen(t.qcolor(t.SOFT))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._text)
