"""The sidebar: brand, machine picker, nav and the device card."""

from __future__ import annotations

from typing import ClassVar

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..theme import fonts
from ..theme import tokens as t
from ..widgets import StatusDot
from . import icons

# key, label, shortcut, child of Live
NAV = [
    ("live", "Live", False),
    ("spectrum", "Spectrum", True),
    ("thermal", "Thermal", True),
    ("history", "History", False),
    ("evidence", "Evidence", False),
    ("agent", "Agents", False),
    ("device", "Device & model", False),
]
KEYS = [k for k, _, _ in NAV]
LINE = 19  # 13px text at the mockup's line-height 1.45


class _Brand(QWidget):
    """"effente" and "console" on one baseline."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._big = fonts.font(20, 700, display=True, tracking=t.TRACK_TITLE)
        self._small = fonts.small()
        self.setFixedHeight(26)  # 2px padding, a 22px line, 2px padding

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        p.setFont(self._big)
        fm = p.fontMetrics()
        base = 19  # the CSS baseline: 2px padding + 17px into a 20px line-height-1 box
        x = 8
        p.setPen(t.qcolor(t.TEXT))
        p.drawText(QPointF(x, base), "effente")
        x += fm.horizontalAdvance("effente") + 8
        p.setFont(self._small)
        p.setPen(t.qcolor(t.MUTED))
        p.drawText(QPointF(x, base), "console")


class NavItem(QAbstractButton):
    """One nav button: icon, label, then a badge or the shortcut key on the right."""

    def __init__(self, key: str, label: str, shortcut: int, child: bool, parent: QWidget | None = None):
        super().__init__(parent)
        self.key = key
        self.setText(label)
        self.shortcut_text = str(shortcut)
        self.child = child
        self.on = False
        self.parent_on = False
        self.show_key = False
        self.badge: str | None = None
        self.setFont(fonts.font(13.5 if child else t.FONT_BASE, tracking=t.TRACK_BASE))
        self._badge_font = fonts.chip()
        self._key_font = fonts.small()
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setFixedHeight(30 if child else 34)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAccessibleName(label)

    def sizeHint(self) -> QSize:
        return QSize(180, self.height())

    def set_badge(self, text: str | None) -> None:
        self.badge = text or None
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        hover = self.underMouse()
        if self.on or hover:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(t.qcolor(t.NAV_ON if self.on else t.HOVER))
            p.drawRoundedRect(r, t.R_NAV, t.R_NAV)
        lit = self.on or self.parent_on or hover
        text_c = t.qcolor(t.TEXT if lit else t.SOFT)
        icon_c = t.qcolor(t.ACCENT) if (self.on or self.parent_on) else text_c
        icons.paint_icon(p, self.key, QRectF(10, (r.height() - 16) / 2, 16, 16), icon_c)
        p.setFont(self.font())
        p.setPen(text_c)
        p.drawText(r.adjusted(36, 0, -10, 0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.text())
        if self.badge:
            p.setFont(self._badge_font)
            fm = p.fontMetrics()
            w = max(18, fm.horizontalAdvance(self.badge) + 10)
            h = 17
            b = QRectF(r.right() - 10 - w, (r.height() - h) / 2, w, h)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(t.qcolor(t.ACCENT))
            p.drawRoundedRect(b, h / 2, h / 2)
            p.setPen(t.qcolor(t.WHITE))
            p.drawText(b, Qt.AlignmentFlag.AlignCenter, self.badge)
        elif self.show_key:
            p.setFont(self._key_font)
            p.setPen(t.qcolor(t.MUTED))
            p.drawText(r.adjusted(0, 0, -10, 0), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       self.shortcut_text)


class _SubGroup(QWidget):
    """Spectrum and Thermal, indented under Live with a hairline on the left."""

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setPen(QPen(t.qcolor(t.BORDER), 1))
        x = 18.5
        p.drawLine(QPointF(x, 0), QPointF(x, self.height() - 4))


class Nav(QWidget):
    navigate = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.items: dict[str, NavItem] = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(1)
        sub = _SubGroup(self)
        sl = QVBoxLayout(sub)
        sl.setContentsMargins(18 + 1 + 8, 0, 0, 4)
        sl.setSpacing(1)
        for i, (key, label, child) in enumerate(NAV):
            item = NavItem(key, label, i + 1, child, self)
            item.clicked.connect(lambda _=False, k=key: self.navigate.emit(k))
            self.items[key] = item
            if child:
                sl.addWidget(item)
            else:
                lay.addWidget(item)
            if key == "live":
                lay.addWidget(sub)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)

    def event(self, e) -> bool:
        if e.type() in (QEvent.Type.HoverEnter, QEvent.Type.HoverLeave):
            show = e.type() == QEvent.Type.HoverEnter
            for item in self.items.values():
                item.show_key = show
                item.update()
        return super().event(e)

    def set_active(self, key: str) -> None:
        for k, item in self.items.items():
            item.on = k == key
            item.parent_on = k == "live" and key in ("spectrum", "thermal")
            item.update()


class _RightElidedLabel(QLabel):
    """Right-aligned and one line: a long value ends in an ellipsis instead of pushing
    the card wider. The full text is the tooltip."""

    def __init__(self, text: str = "", parent: QWidget | None = None):
        super().__init__(text, parent)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setToolTip(text)

    def setText(self, text: str) -> None:
        super().setText(text)
        self.setToolTip(text)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setPen(self.palette().windowText().color())
        shown = self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, self.width())
        p.drawText(self.rect(), int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter), shown)


class DeviceCard(QWidget):
    """Device id with its dot, then label/value rows, under a hairline."""

    ROWS: ClassVar[list[tuple[str, str]]] = [("mode", "Mode"), ("model", "Model"), ("receiver", "Receiver"), ("records", "Records"),
            ("thermal", "Thermal"), ("stored", "Stored")]

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 14, 8, 2)
        lay.setSpacing(6)
        top = QHBoxLayout()
        top.setSpacing(8)
        self.dot = StatusDot(t.MUTED)
        self.name = QLabel("No device yet")
        self.name.setFont(fonts.font(t.FONT_BASE, 500))
        self.name.setFixedHeight(LINE)
        top.addWidget(self.dot, 0, Qt.AlignmentFlag.AlignVCenter)
        top.addWidget(self.name, 1)
        lay.addLayout(top)
        self.values: dict[str, QLabel] = {}
        for key, label in self.ROWS:
            row = QHBoxLayout()
            row.setSpacing(12)
            lab = QLabel(label)
            lab.setProperty("role", "muted")
            lab.setFont(fonts.small())
            val = _RightElidedLabel("\u2013")
            val.setProperty("role", "soft")
            val.setFont(fonts.body())
            val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            lab.setFixedHeight(17)  # 12px at line-height 1.45, top-aligned as in CSS
            val.setFixedHeight(LINE)
            row.addWidget(lab, 0, Qt.AlignmentFlag.AlignTop)
            row.addWidget(val, 1)
            lay.addLayout(row)
            self.values[key] = val

    def set_device(self, name: str | None, live: bool, values: dict[str, str]) -> None:
        self.name.setText(name or "No device yet")
        self.dot.set_color(t.DOT if live else t.MUTED)
        for key, label in self.values.items():
            label.setText(values.get(key, "–"))

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setPen(QPen(t.qcolor(t.BORDER), 1))
        p.drawLine(QPointF(0, 0.5), QPointF(self.width(), 0.5))


class Sidebar(QWidget):
    """Emits navigate(key) from the nav and machine_changed(id) from the picker."""

    navigate = Signal(str)
    machine_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedWidth(t.SIDEBAR_W)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(t.SIDEBAR_PAD_H, t.SIDEBAR_PAD_V, t.SIDEBAR_PAD_H + 1, t.SIDEBAR_PAD_V)
        lay.setSpacing(t.SIDEBAR_GAP)
        lay.addWidget(_Brand(self))

        field = QWidget(self)
        fl = QVBoxLayout(field)
        fl.setContentsMargins(4, 0, 4, 0)
        fl.setSpacing(6)
        label = QLabel("Machine", field)
        label.setProperty("role", "muted")
        label.setFont(fonts.label_caps())
        label.setContentsMargins(4, 0, 0, 0)
        label.setFixedHeight(16)  # 11px at line-height 1.45
        self.machine = QComboBox(field)
        self.machine.setFont(fonts.body())
        self.machine.setMinimumHeight(32)
        self.machine.setCursor(Qt.CursorShape.PointingHandCursor)
        self.machine.setAccessibleName("Machine")
        self.machine.currentIndexChanged.connect(self._machine_picked)
        fl.addWidget(label)
        fl.addWidget(self.machine)
        lay.addWidget(field)

        self.nav = Nav(self)
        self.nav.navigate.connect(self.navigate)
        lay.addWidget(self.nav)
        lay.addStretch(1)
        self.card = DeviceCard(self)
        lay.addWidget(self.card)

    def _machine_picked(self, i: int) -> None:
        if i >= 0 and not self._filling:
            self.machine_changed.emit(self.machine.itemData(i))

    _filling = False

    def set_machines(self, machines: list[str], current: str | None) -> None:
        """Refill the picker without emitting machine_changed."""
        have = [self.machine.itemData(i) for i in range(self.machine.count())]
        self._filling = True
        try:
            if have != list(machines):
                self.machine.clear()
                for m in machines:
                    self.machine.addItem(m, m)
                if not machines:
                    self.machine.addItem("No machine yet", None)
            if current in machines:
                self.machine.setCurrentIndex(machines.index(current))
        finally:
            self._filling = False
        self.machine.setEnabled(bool(machines))

    def set_active(self, key: str) -> None:
        self.nav.set_active(key)

    def set_badge(self, key: str, text: str | None) -> None:
        self.nav.items[key].set_badge(text)

    def badge(self, key: str) -> str | None:
        return self.nav.items[key].badge

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), t.qcolor(t.SIDE))
        p.setPen(QPen(t.qcolor(t.BORDER), 1))
        x = self.width() - 0.5
        p.drawLine(QPointF(x, 0), QPointF(x, self.height()))
