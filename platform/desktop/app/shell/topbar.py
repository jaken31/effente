"""The top bar: "machine / Title" (with a Live crumb for Spectrum and Thermal), the
subtitle, the Simulated data chip and freshness."""

from __future__ import annotations

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from ..theme import fonts
from ..theme import tokens as t
from ..theme.qss import css
from ..widgets import SimChip, StatusDot


class _Crumb(QLabel):
    clicked = Signal()

    def __init__(self, text: str, parent: QWidget | None = None):
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setProperty("role", "soft")
        self.setToolTip("Back to Live")

    def enterEvent(self, e) -> None:
        self.setStyleSheet(f"color: {t.TEXT};")
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:
        self.setStyleSheet("")
        super().leaveEvent(e)

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(e)


def _slash(parent: QWidget) -> QLabel:
    s = QLabel("/", parent)
    s.setFont(fonts.font(18, 300))
    s.setStyleSheet(f"color: {css(t.BORDER_STRONG)};")
    return s


class TopBar(QWidget):
    """Emits navigate("live") from the crumb."""

    navigate = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedHeight(t.TOPBAR_H)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(t.TOPBAR_PAD_H, 0, t.TOPBAR_PAD_H, 1)
        lay.setSpacing(t.TOPBAR_GAP)

        h1 = QHBoxLayout()
        h1.setSpacing(8)
        self.machine = QLabel("", self)
        self.machine.setProperty("role", "soft")
        self.machine.setFont(fonts.font(t.FONT_TITLE, 400))
        self.crumb = _Crumb("Live", self)
        self.crumb.setFont(fonts.page_title())
        self.crumb.clicked.connect(lambda: self.navigate.emit("live"))
        self.title = QLabel("", self)
        self.title.setFont(fonts.page_title())
        self.title.setProperty("role", "strong")
        self._slash1 = _slash(self)
        self._slash2 = _slash(self)
        for w in (self.machine, self._slash1, self.crumb, self._slash2, self.title):
            h1.addWidget(w, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addLayout(h1)

        self.subtitle = QLabel("", self)
        self.subtitle.setProperty("role", "muted")
        self.subtitle.setFont(fonts.body())
        lay.addWidget(self.subtitle, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addStretch(1)

        self.sim_chip = SimChip(parent=self)
        self.sim_chip.setVisible(False)
        lay.addWidget(self.sim_chip, 0, Qt.AlignmentFlag.AlignVCenter)

        fresh = QHBoxLayout()
        fresh.setSpacing(8)
        self.fresh_dot = StatusDot(t.MUTED)
        self.fresh = QLabel("No reading yet", self)
        self.fresh.setFont(fonts.body())
        fresh.addWidget(self.fresh_dot, 0, Qt.AlignmentFlag.AlignVCenter)
        fresh.addWidget(self.fresh, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addLayout(fresh)
        self.set_fresh("No reading yet", "stale")
        self.set_page("", "", child=False)

    def set_machine(self, name: str | None) -> None:
        self.machine.setText(name or "No machine yet")

    def set_page(self, title: str, subtitle: str, child: bool) -> None:
        self.title.setText(title)
        self.subtitle.setText(subtitle)
        self.subtitle.setVisible(bool(subtitle))
        self.crumb.setVisible(child)
        self._slash2.setVisible(child)

    def set_simulated(self, simulated: bool) -> None:
        self.sim_chip.setVisible(bool(simulated))

    def set_fresh(self, text: str, look: str) -> None:
        """look is format.LIVE, STALE or IDLE. Stale reads in white, medium weight,
        with a gray dot; never in red."""
        stale = look == "stale"
        self.fresh.setText(text)
        self.fresh.setFont(fonts.font(t.FONT_BASE, 500 if stale else 400, tracking=t.TRACK_BASE))
        self.fresh.setStyleSheet(f"color: {t.TEXT if stale else t.SOFT};")
        self.fresh_dot.set_color(t.DOT if look == "live" else t.MUTED)
        self.look = look

    @property
    def stale(self) -> bool:
        return self.look == "stale"

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setPen(QPen(t.qcolor(t.BORDER_TOPBAR), 1))
        y = self.height() - 0.5
        p.drawLine(QPointF(0, y), QPointF(self.width(), y))
