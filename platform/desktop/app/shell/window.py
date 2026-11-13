"""The main window: sidebar, top bar and the page stack, bound to DashboardState."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import ClassVar

from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtGui import QKeySequence, QPainter, QRadialGradient, QShortcut, QTransform
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QMainWindow,
    QScrollArea,
    QScrollBar,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..theme import tokens as t
from . import format as fmt
from .pages import CHILDREN, KEYS, make_page
from .sidebar import Sidebar
from .topbar import TopBar

log = logging.getLogger("effente.desktop.shell")

MIN_W, MIN_H = 1440, 900


class _Backdrop(QWidget):
    """The page background: near-black with the mockup's two warm glows."""

    # (centre x, centre y as a fraction of the window, radii px, rgba, stop)
    GLOWS: ClassVar[list] = [
        (0.30, -0.15, 1100, 620, (255, 120, 60, 0.15), 0.62),
        (1.00, 1.10, 900, 500, (255, 90, 40, 0.05), 0.60),
    ]

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), t.qcolor(t.BG))
        w, h = self.width(), self.height()
        for fx, fy, rx, ry, (r, g, b, a), stop in self.GLOWS:
            g_ = QRadialGradient(QPointF(0, 0), rx)
            c = t.qcolor(f"rgba({r}, {g}, {b}, {a})")
            clear = t.qcolor(f"rgba({r}, {g}, {b}, 0)")
            g_.setColorAt(0, c)
            g_.setColorAt(stop, clear)
            g_.setColorAt(1, clear)
            p.save()
            p.setTransform(QTransform().translate(fx * w, fy * h).scale(1, ry / rx))
            p.fillRect(-rx, -rx, 2 * rx, 2 * rx, g_)
            p.restore()


class PageScroll(QScrollArea):
    """A page's scroll area whose vertical scrollbar floats over the right padding
    instead of taking width from the page, so panels end 24px from the window edge
    as in the mockup."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setWidgetResizable(True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.viewport().setAutoFillBackground(False)
        inner = self.verticalScrollBar()  # hidden, but still the one the wheel moves
        self.overlay = QScrollBar(Qt.Orientation.Vertical, self)
        self.overlay.setRange(inner.minimum(), inner.maximum())
        self.overlay.hide()
        inner.rangeChanged.connect(self._range)
        inner.valueChanged.connect(self.overlay.setValue)
        self.overlay.valueChanged.connect(inner.setValue)

    def _range(self, lo: int, hi: int) -> None:
        self.overlay.setRange(lo, hi)
        self.overlay.setPageStep(self.verticalScrollBar().pageStep())
        self.overlay.setSingleStep(self.verticalScrollBar().singleStep())
        self.overlay.setVisible(hi > lo)
        self._place()

    def _place(self) -> None:
        bottom = self.horizontalScrollBar().height() if self.horizontalScrollBar().isVisible() else 0
        self.overlay.setGeometry(self.width() - t.SCROLLBAR, 0, t.SCROLLBAR, self.height() - bottom)
        self.overlay.raise_()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._place()


class MainWindow(QMainWindow):
    """`state` follows the DashboardState interface (FakeState works too).

    `pages` maps a key to a ready page widget, for tests; missing keys are built from
    app.pages or shown as placeholders. `placeholders=True` uses placeholders only.
    `receiver_address` is shown on the device card, for example "0.0.0.0:8000".
    """

    def __init__(self, state=None, *, pages: Mapping[str, QWidget] | None = None,
                 placeholders: bool = False, receiver_address: str | None = None):
        super().__init__()
        self.state = state
        self.receiver_address = receiver_address
        self.setWindowTitle("effente")
        self.setMinimumSize(MIN_W, MIN_H)

        root = _Backdrop(self)
        self.setCentralWidget(root)
        row = QHBoxLayout(root)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        self.sidebar = Sidebar(root)
        row.addWidget(self.sidebar)
        main = QWidget(root)
        col = QVBoxLayout(main)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        self.topbar = TopBar(main)
        col.addWidget(self.topbar)
        self.stack = QStackedWidget(main)
        col.addWidget(self.stack, 1)
        row.addWidget(main, 1)

        self.pages: dict[str, QWidget] = {}
        for key in KEYS:
            page = (pages or {}).get(key) or make_page(key, placeholders=placeholders)
            self.pages[key] = page
            self.stack.addWidget(self._wrap(page))
            page.navigate.connect(self.navigate)

        self.sidebar.navigate.connect(self.navigate)
        self.topbar.navigate.connect(self.navigate)
        self.sidebar.machine_changed.connect(self._machine_picked)
        self.shortcuts = []
        for i, key in enumerate(KEYS):
            sc = QShortcut(QKeySequence(str(i + 1)), self)
            sc.setContext(Qt.ShortcutContext.WindowShortcut)
            sc.activated.connect(lambda k=key: self.navigate(k))
            self.shortcuts.append(sc)

        self.current = ""
        self.navigate("live")

        # Freshness ages even when nothing arrives.
        self._tick = QTimer(self)
        self._tick.setInterval(1000)
        self._tick.timeout.connect(self.refresh_freshness)
        if state is not None:
            self.bind(state)

    @staticmethod
    def _wrap(page: QWidget) -> PageScroll:
        """Each page scrolls inside the content area's 24px padding."""
        area = PageScroll()
        holder = QWidget()
        lay = QVBoxLayout(holder)
        lay.setContentsMargins(t.CONTENT_PAD, t.CONTENT_PAD, t.CONTENT_PAD, t.CONTENT_PAD)
        lay.addWidget(page)
        area.setWidget(holder)
        holder.setAutoFillBackground(False)
        return area

    # routing
    def navigate(self, key: str) -> None:
        if key not in self.pages:
            log.warning("navigate: no page %r", key)
            return
        page = self.pages[key]
        self.current = key
        self.stack.setCurrentIndex(KEYS.index(key))
        self.sidebar.set_active(key)
        self.topbar.set_page(getattr(page, "title", key), getattr(page, "subtitle", ""), child=key in CHILDREN)

    def current_page(self) -> QWidget:
        return self.pages[self.current]

    # state
    def bind(self, state) -> None:
        self.state = state
        state.snapshot_changed.connect(self.refresh)
        state.record_added.connect(self._record_added)
        state.alarms_changed.connect(self.refresh_badges)
        state.runs_changed.connect(self.refresh_badges)
        state.connection_changed.connect(lambda _up: self.refresh_chrome())
        for page in self.pages.values():
            page.bind(state)
        self.refresh()
        self._tick.start()

    def _machine_picked(self, machine: str) -> None:
        if self.state is not None and machine and machine != self.state.current_machine():
            self.state.set_machine(machine)
            self.refresh()

    def _record_added(self, _record: dict) -> None:
        self.refresh_chrome()

    def refresh(self) -> None:
        if self.state is None:
            return
        self.sidebar.set_machines(list(self.state.machines()), self.state.current_machine())
        self.refresh_chrome()
        self.refresh_badges()

    def refresh_badges(self) -> None:
        s = self.state
        if s is None:
            return
        n = len(s.unanswered_alarms())
        self.sidebar.set_badge("history", str(n) if n else None)
        self.sidebar.set_badge("agent", "1" if s.agent_running() else None)

    def refresh_freshness(self) -> str:
        """Update the freshness line; returns its look (format.LIVE, STALE or IDLE)."""
        s = self.state
        if s is None:
            return fmt.STALE
        _, device = shown_device(s)
        text, look = fmt.freshness_text(s.freshness(), s.latest(), mode_name((device or {}).get("mode")))
        self.topbar.set_fresh(text, look)
        return look

    def refresh_chrome(self) -> None:
        s = self.state
        if s is None:
            return
        machine = s.current_machine()
        self.topbar.set_machine(machine)
        self.setWindowTitle(f"{machine} \u00b7 effente" if machine else "effente")
        live = self.refresh_freshness() == fmt.LIVE
        latest = s.latest()
        self.topbar.set_simulated(bool(latest and latest.get("simulated")))
        self._refresh_device_card(latest, live)

    def _refresh_device_card(self, latest: dict | None, live: bool) -> None:
        s = self.state
        device_id, device = shown_device(s)
        records = list(s.records(limit=11)) if device else []
        connected = bool(device and device.get("connected"))
        mode = (device or {}).get("mode") or "\u2013"
        if device and not connected:
            mode = "not connected"
        values = {
            "mode": mode,
            "model": fmt.model_text(s.model()),
            "receiver": self.receiver_address or "\u2013",
            "records": fmt.record_rate(records, not live) if device else "\u2013",
            "thermal": fmt.thermal_rate(latest, records, not live) if device else "\u2013",
            "stored": stored_count(s),
        }
        self.sidebar.card.set_device(device_id, live, values)


def shown_device(state) -> tuple[str | None, dict | None]:
    """The device the state shows for the current machine (its device_id(), the
    logic port's rule) and its view."""
    device_id = state.device_id()
    return device_id, (state.devices() or {}).get(device_id) if device_id else None


def mode_name(mode) -> str:
    """The logic port's mode name ("Calibrating"), if the data layer is present."""
    try:
        from app.data import logic
    except ImportError:
        return str(mode or "")
    return logic.mode_name(mode)


def stored_count(state) -> str:
    """Records stored for the machine. Needs DashboardState.stored_count(); records()
    is capped at the history length, so it would understate the count."""
    count = getattr(state, "stored_count", None)
    return f"{int(count()):,}" if callable(count) else "\u2013"
