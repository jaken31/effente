"""Controls: Button, Segmented, Toggle."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPointF,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QHBoxLayout,
    QPushButton,
    QSizePolicy,
    QWidget,
)

from ..theme import fonts
from ..theme import tokens as t

KINDS = ("primary", "secondary", "ghost")


class Button(QPushButton):
    """A rounded button. kind is "primary" (red fill, dark text), "secondary" or "ghost"."""

    def __init__(self, text: str = "", kind: str = "secondary", parent: QWidget | None = None, *, small: bool = False):
        super().__init__(text, parent)
        if kind not in KINDS:
            raise ValueError(f"Button kind must be one of {KINDS}, not {kind!r}")
        self.setProperty("kind", kind)
        self.setProperty("small", "true" if small else "false")
        self.setFont(fonts.button())
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    @property
    def kind(self) -> str:
        return self.property("kind")

    def set_kind(self, kind: str) -> None:
        if kind not in KINDS:
            raise ValueError(f"Button kind must be one of {KINDS}, not {kind!r}")
        self.setProperty("kind", kind)
        self.style().unpolish(self)
        self.style().polish(self)


class _SegButton(QAbstractButton):
    def __init__(self, label: str, small: bool, parent: QWidget):
        super().__init__(parent)
        self.setText(label)
        self.setCheckable(True)
        self.setFont(fonts.button())
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pad = (2, 10) if small else (4, 12)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)

    def sizeHint(self) -> QSize:
        fm = self.fontMetrics()
        return QSize(fm.horizontalAdvance(self.text()) + 2 * self._pad[1], fm.height() + 2 * self._pad[0])

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        if self.isChecked():
            p.setPen(QPen(t.qcolor(t.ACCENT), 1))
            rr = r.adjusted(0.5, 0.5, -0.5, -0.5)
            p.drawRoundedRect(rr, rr.height() / 2, rr.height() / 2)
        on = self.isChecked() or self.underMouse()
        p.setPen(t.qcolor(t.TEXT if on else t.SOFT))
        if not self.isEnabled():
            p.setOpacity(0.4)
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, self.text())


class Segmented(QWidget):
    """A rounded group of exclusive options. options: labels, or (key, label) pairs.

    Emits changed(key) when the user picks one. set_current(key) does not emit.
    """

    changed = Signal(str)

    def __init__(self, options: Sequence[str | tuple[str, str]], parent: QWidget | None = None, *,
                 current: str | None = None, small: bool = False):
        super().__init__(parent)
        self._keys: list[str] = []
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)  # 1px border + 2px padding
        lay.setSpacing(2)
        for opt in options:
            key, label = (opt, opt) if isinstance(opt, str) else opt
            b = _SegButton(label, small, self)
            self._group.addButton(b, len(self._keys))
            self._keys.append(key)
            lay.addWidget(b)
        self._group.idClicked.connect(self._clicked)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.set_current(current if current is not None else (self._keys[0] if self._keys else None))

    def keys(self) -> list[str]:
        return list(self._keys)

    def current(self) -> str | None:
        i = self._group.checkedId()
        return self._keys[i] if i >= 0 else None

    def set_current(self, key: str | None) -> None:
        if key is None:
            b = self._group.checkedButton()
            if b:
                self._group.setExclusive(False)
                b.setChecked(False)
                self._group.setExclusive(True)
            return
        self._group.button(self._keys.index(key)).setChecked(True)

    def button(self, key: str) -> QAbstractButton:
        return self._group.button(self._keys.index(key))

    def _clicked(self, i: int) -> None:
        self.changed.emit(self._keys[i])

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(t.qcolor(t.BORDER), 1))
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)


class Toggle(QAbstractButton):
    """A 34x20 switch. Off: raised track with a hairline and a gray knob. On: red track, white knob."""

    W, H, KNOB, INSET = 34, 20, 14, 3

    def __init__(self, checked: bool = False, parent: QWidget | None = None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setFixedSize(self.W, self.H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pos = 1.0 if checked else 0.0
        self._anim = QPropertyAnimation(self, b"knob", self)
        self._anim.setDuration(150)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._animate)

    def _get_knob(self) -> float:
        return self._pos

    def _set_knob(self, v: float) -> None:
        self._pos = v
        self.update()

    knob = Property(float, _get_knob, _set_knob)

    def _animate(self, on: bool) -> None:
        self._anim.stop()
        if not self.isVisible():
            self._set_knob(1.0 if on else 0.0)
            return
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def sizeHint(self) -> QSize:
        return QSize(self.W, self.H)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.isEnabled():
            p.setOpacity(0.4)
        r = QRectF(0, 0, self.W, self.H)
        on = self.isChecked()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(t.qcolor(t.PRIMARY if on else t.RAISED))
        p.drawRoundedRect(r, self.H / 2, self.H / 2)
        if not on:
            p.setPen(QPen(t.qcolor(t.BORDER), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            rr = r.adjusted(0.5, 0.5, -0.5, -0.5)
            p.drawRoundedRect(rr, rr.height() / 2, rr.height() / 2)
        x = self.INSET + self._pos * (self.W - 2 * self.INSET - self.KNOB)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(t.qcolor(t.WHITE if on else t.MUTED))
        p.drawEllipse(QPointF(x + self.KNOB / 2, self.H / 2), self.KNOB / 2, self.KNOB / 2)
