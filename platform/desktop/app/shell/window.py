"""The main window: sidebar, top bar and the page stack, bound to DashboardState."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import ClassVar

from PySide6.QtCore import QPointF, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QPainter, QRadialGradient, QTransform
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QScrollArea,
    QScrollBar,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import zoom
from ..settings import Choices
from ..theme import schemes
from ..theme import tokens as t
from . import format as fmt
from . import shortcuts
from .notices import Notices
from .pages import CHILDREN, KEYS, make_page
from .search import Entry, Search
from .sidebar import Sidebar
from .topbar import TopBar

log = logging.getLogger("effente.desktop.shell")

# The smallest size the sidebar and top bar still lay out cleanly: half of a 1920px
# screen, so the window can sit beside a terminal. Pages narrower than their content
# scroll sideways.
MIN_W, MIN_H = 960, 660
DEFAULT_W, DEFAULT_H = 1440, 900  # the mockup's size

# What the window remembers between runs: keys in the settings store.
GEOMETRY, PAGE, MACHINE = "window/geometry", "window/page", "window/machine"
# The window's place in screen pixels, kept across the restart that changes the app's
# size, so the window stays where it was and only what is in it grows or shrinks.
ZOOM_RECT = "window/zoom_rect"
ZOOM_WAIT_MS = 600  # after the last size key, so several presses restart the app once


class _Backdrop(QWidget):
    """The page background: the scheme's background with the mockup's two glows."""

    # (centre x, centre y as a fraction of the window, radii px, colour token, stop)
    GLOWS: ClassVar[list] = [
        (0.30, -0.15, 1100, 620, "GLOW_TOP", 0.62),
        (1.00, 1.10, 900, 500, "GLOW_BOTTOM", 0.60),
    ]

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), t.qcolor(t.BG))
        w, h = self.width(), self.height()
        for fx, fy, rx, ry, token, stop in self.GLOWS:
            r, g, b, a = getattr(t, token)
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


class _PageHolder(QWidget):
    """Holds a page inside its scroll area at no less than the page's natural
    height. A resizable scroll area otherwise squeezes the page down to its
    minimum, which overlaps text and shrinks panel headers below their controls."""

    def minimumSizeHint(self) -> QSize:
        hint = super().minimumSizeHint()
        lay = self.layout()
        natural = lay.sizeHint().height() if lay is not None else hint.height()
        return QSize(hint.width(), max(hint.height(), natural))


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
    `settings` is a QSettings the window keeps its size, page and machine in, so the
    next run opens where this one closed; without it nothing is remembered.
    A new size (app/zoom.py) closes the window with `restart_wanted` set: the
    program that made the window starts the app again, which is when Qt reads it.
    """

    def __init__(self, state=None, *, pages: Mapping[str, QWidget] | None = None,
                 placeholders: bool = False, receiver_address: str | None = None, settings=None):
        super().__init__()
        self.state = state
        self.receiver_address = receiver_address
        self.settings = settings
        self.restart_wanted = False  # set by a new size, read after the window closes
        self._zoom = zoom.current()  # the size asked for; differs from the one drawn until the restart
        self._zoom_notice = None
        self._zoom_timer = QTimer(self)
        self._zoom_timer.setSingleShot(True)
        self._zoom_timer.setInterval(ZOOM_WAIT_MS)
        self._zoom_timer.timeout.connect(self._restart_for_zoom)
        self.setWindowTitle("effente")
        self.setMinimumSize(MIN_W, MIN_H)
        self.resize(DEFAULT_W, DEFAULT_H)

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
            if hasattr(page, "remember"):  # the page's own kept choices: its range, filter, view
                page.remember(Choices(settings, key))
            if hasattr(page, "simulated_changed"):
                page.simulated_changed.connect(self.refresh_sim_chip)

        self.sidebar.navigate.connect(self.navigate)
        self.topbar.navigate.connect(self.navigate)
        self.sidebar.machine_changed.connect(self._machine_picked)
        self.sidebar.scheme_changed.connect(schemes.save)
        self.shortcuts = shortcuts.install(self)

        self.notices = Notices(root)  # last child of root, so it paints above the pages
        self.shortcut_help = shortcuts.ShortcutHelp(root)
        self.search = Search(root)

        self.current = ""
        self._closing_after_agent = False
        self._setup_offered: set[str] = set()  # machines the Setup page was opened for, or not needed for
        geometry = self._remembered(GEOMETRY)
        if geometry:
            self.restoreGeometry(geometry)
        self._restore_zoom_rect()
        # Picked again once the machine list is known: see refresh().
        self._wanted_machine = self._remembered(MACHINE)
        page = self._remembered(PAGE)
        self.navigate(page if page in self.pages else "live")

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
        holder = _PageHolder()
        lay = QVBoxLayout(holder)
        lay.setContentsMargins(t.CONTENT_PAD, t.CONTENT_PAD, t.CONTENT_PAD, t.CONTENT_PAD)
        lay.addWidget(page)
        area.setWidget(holder)
        holder.setAutoFillBackground(False)
        return area

    # remembering
    def _remembered(self, key: str):
        return self.settings.value(key) if self.settings is not None else None

    def _remember(self, key: str, value) -> None:
        if self.settings is not None:
            self.settings.setValue(key, value)

    # closing
    STOP, WAIT, CANCEL = "stop", "wait", "cancel"

    def closeEvent(self, event) -> None:
        """Closing during a report run this app started asks first: the receiver the
        run reads and writes to lives in this app, so quitting would cut it off."""
        s = self.state
        self._remember(GEOMETRY, self.saveGeometry())
        if self._closing_after_agent or s is None or not getattr(s, "own_agent_running", lambda: False)():
            event.accept()
            return
        choice = self.ask_about_agent()
        if choice == self.STOP:
            s.stop_agent()
            event.accept()
            return
        event.ignore()
        if choice == self.WAIT:
            self._closing_after_agent = True
            log.info("window hidden; quitting when the report finishes")
            self.hide()
            s.runs_changed.connect(self._quit_if_agent_done)
            self._quit_if_agent_done()

    def ask_about_agent(self) -> str:
        """STOP, WAIT or CANCEL, from a dialog. Tests replace this."""
        machine = self.state.current_machine() if self.state is not None else None
        box = QMessageBox(self)
        box.setContentsMargins(14, 12, 14, 10)  # the style gives a message box almost no edge
        box.setWindowTitle("Report still running")
        box.setText(f"A report{f' for {machine}' if machine else ''} is still running.")
        box.setInformativeText("The report needs this app open to finish: the receiver it reads "
                               "and writes to runs inside it. Stopping now loses the report.")
        stop = box.addButton("Stop report and close", QMessageBox.ButtonRole.DestructiveRole)
        wait = box.addButton("Close when it finishes", QMessageBox.ButtonRole.AcceptRole)
        cancel = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel)
        box.setEscapeButton(cancel)
        box.exec()
        return {stop: self.STOP, wait: self.WAIT}.get(box.clickedButton(), self.CANCEL)

    def _quit_if_agent_done(self) -> None:
        if self._closing_after_agent and not self.state.own_agent_running():
            QApplication.quit()

    # routing
    def navigate(self, key: str) -> None:
        if key not in self.pages:
            log.warning("navigate: no page %r", key)
            return
        page = self.pages[key]
        self.current = key
        self._remember(PAGE, key)
        self.stack.setCurrentIndex(KEYS.index(key))
        self.sidebar.set_active(key)
        self.topbar.set_page(getattr(page, "title", key), getattr(page, "subtitle", ""), child=key in CHILDREN)
        self.refresh_sim_chip()

    def current_page(self) -> QWidget:
        return self.pages[self.current]

    # shortcut actions (shortcuts.ACTIONS)
    def go_back(self) -> None:
        """Esc: close the search or the shortcut list if one is up, else leave
        Spectrum or Thermal for Live."""
        if self.search.isVisible():
            self.search.close_search()
        elif self.shortcut_help.isVisible():
            self.shortcut_help.hide()
        elif self.current in CHILDREN:
            self.navigate("live")

    def previous_machine(self) -> None:
        self._step_machine(-1)

    def next_machine(self) -> None:
        self._step_machine(1)

    def _step_machine(self, step: int) -> None:
        if self.state is None:
            return
        machines = list(self.state.machines())
        current = self.state.current_machine()
        if len(machines) > 1 and current in machines:
            self._machine_picked(machines[(machines.index(current) + step) % len(machines)])

    def run_report(self) -> None:
        """As the Agents page's Run Report Agent button, in the mode picked there."""
        run = getattr(self.pages["agent"], "run_agent", None)
        if callable(run):
            run()

    def export_page(self) -> None:
        export = getattr(self.current_page(), "export_dialog", None)
        if callable(export):
            export()

    def toggle_full_screen(self) -> None:
        self.setWindowState(self.windowState() ^ Qt.WindowState.WindowFullScreen)

    def zoom_in(self) -> None:
        self._set_zoom(zoom.stepped(self._zoom, 1, self._zoom_limit()), "This screen has no room for a larger size")

    def zoom_out(self) -> None:
        self._set_zoom(zoom.stepped(self._zoom, -1), "This is the smallest size")

    def zoom_reset(self) -> None:
        self._set_zoom(zoom.DEFAULT, "This is the normal size")

    def _zoom_limit(self) -> float:
        """The largest size at which the smallest window still fits this screen."""
        screen = self.screen()
        if screen is None:
            return zoom.STEPS[-1]
        room, now = screen.availableGeometry(), zoom.current()
        return min(room.width() * now / MIN_W, room.height() * now / MIN_H)

    def _set_zoom(self, value: float, at_the_end: str) -> None:
        """Keep a new size and, a moment after the last key, start again to show it."""
        if value == self._zoom:
            self._zoom_note(f"Size {zoom.percent(value)}: {at_the_end}")
            return
        self._zoom = value
        self._remember(zoom.KEY, value)
        if value == zoom.current():  # back where this run already is
            self._zoom_timer.stop()
            self._zoom_note(f"Size {zoom.percent(value)}")
            return
        self._zoom_note(f"Size {zoom.percent(value)}: Restarting to show it")
        self._zoom_timer.start()

    def _zoom_note(self, text: str) -> None:
        if self._zoom_notice is not None:
            self._zoom_notice.closed.emit()  # one card for the size, not one a key press
        self._zoom_notice = self.notices.show_failure(text)

    def _restart_for_zoom(self) -> None:
        if self._zoom == zoom.current():
            return
        s = self.state
        if s is not None and getattr(s, "own_agent_running", lambda: False)():
            # Starting again would cut the report off (see closeEvent).
            self._zoom_note(f"Size {zoom.percent(self._zoom)}: A report is running, "
                            "so it shows the next time the app starts")
            return
        if not (self.isMaximized() or self.isFullScreen()):
            g, now = self.geometry(), zoom.current()
            self._remember(ZOOM_RECT, ",".join(str(round(v * now)) for v in (g.x(), g.y(), g.width(), g.height())))
        self.restart_wanted = True
        self.close()
        QApplication.quit()

    def _restore_zoom_rect(self) -> None:
        """After a restart for a new size: the window where it was, in screen pixels."""
        kept = self._remembered(ZOOM_RECT)
        if not kept:
            return
        self.settings.remove(ZOOM_RECT)
        try:
            x, y, w, h = (round(float(v) / zoom.current()) for v in str(kept).split(","))
        except ValueError:
            return
        if any(screen.availableGeometry().intersects(QRect(x, y, w, h)) for screen in QApplication.screens()):
            self.setGeometry(x, y, w, h)
        else:
            self.resize(w, h)

    def toggle_shortcut_help(self) -> None:
        self.shortcut_help.toggle()

    def toggle_search(self) -> None:
        if self.search.isVisible():
            self.search.close_search()
        else:
            self.shortcut_help.hide()
            self.search.open(self.search_entries())

    # Actions the search leaves out: it is one of them, and the others only make
    # sense as keys.
    NOT_SEARCHED = ("toggle_search", "go_back", "previous_machine", "next_machine")

    def search_entries(self) -> list[Entry]:
        """What Ctrl+K can find: every page, every other machine, and the actions."""
        entries = [Entry(getattr(self.pages[key], "title", key),
                         f"Page \u00b7 {shortcuts.PAGE_KEY[key]}" if key in shortcuts.PAGE_KEY else "Page",
                         lambda k=key: self.navigate(k))
                   for key in KEYS]
        if self.state is not None:
            current = self.state.current_machine()
            entries += [Entry(m, "Machine", lambda m=m: self._machine_picked(m))
                        for m in self.state.machines() if m != current]
        entries += [Entry(what, shown, getattr(self, method))
                    for _, shown, what, method in shortcuts.ACTIONS if method not in self.NOT_SEARCHED]
        return entries

    # state
    def bind(self, state) -> None:
        self.state = state
        state.snapshot_changed.connect(self.refresh)
        state.record_added.connect(self._record_added)
        state.alarms_changed.connect(self.refresh_badges)
        state.runs_changed.connect(self.refresh_badges)
        state.connection_changed.connect(lambda _up: self.refresh_chrome())
        # The full data interface has these two; the shell's own test stub does not.
        if hasattr(state, "action_failed"):
            state.action_failed.connect(self.notices.show_failure)
        if hasattr(state, "alarm_opened"):
            state.alarm_opened.connect(self._alarm_opened)
        for page in self.pages.values():
            page.bind(state)
        setup = getattr(state, "setup", None)
        if setup is not None and hasattr(setup, "profile_known"):
            setup.profile_changed.connect(self.offer_setup)
        self.refresh()
        self._tick.start()

    def _machine_picked(self, machine: str) -> None:
        if self.state is not None and machine and machine != self.state.current_machine():
            self._wanted_machine = None
            self._remember(MACHINE, machine)
            self.state.set_machine(machine)
            self.refresh()

    def _alarm_opened(self, alarm: dict) -> None:
        from app.data import logic
        from app.pages.live.common import clock

        machine = alarm.get("machine_id")
        what = logic.diagnosis_text(self.state.model(), alarm.get("diagnosis"), alarm.get("model_version"))
        self.notices.show_alarm(
            f"New alarm on {machine}" if machine else "New alarm",
            f"{what} · since {clock(alarm.get('start'))}. Real problem or false alarm?",
            "Answer in History",
            lambda: self._open_alarm(machine),
        )

    def _open_alarm(self, machine) -> None:
        self._machine_picked(machine)
        self.navigate("history")

    def _record_added(self, _record: dict) -> None:
        self.refresh_chrome()
        if not self.topbar.stale:
            self.topbar.fresh_dot.pulse()  # one beat for each record that arrives

    def refresh(self) -> None:
        if self.state is None:
            return
        machines = list(self.state.machines())
        if self._wanted_machine and machines:
            # The machine picked last run, if it is still there the first time the
            # list is known. If it is gone it is dropped, not waited for.
            wanted, self._wanted_machine = self._wanted_machine, None
            if wanted in machines and wanted != self.state.current_machine():
                self.state.set_machine(wanted)
        self.sidebar.set_machines(machines, self.state.current_machine())
        self.refresh_chrome()
        self.refresh_badges()
        self.offer_setup()

    def offer_setup(self) -> None:
        """Open the Setup page by itself, once a run, for a machine the platform says
        has no machine profile (contract section 10.1). Not before it has said."""
        setup = getattr(self.state, "setup", None)
        machine = self.state.current_machine() if self.state is not None else None
        if not machine or machine in self._setup_offered or setup is None:
            return
        if not getattr(setup, "profile_known", lambda: False)():
            return
        self._setup_offered.add(machine)
        if setup.profile() is None:
            self.navigate("setup")

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
        self.refresh_sim_chip()
        self._refresh_device_card(latest, live)

    def refresh_sim_chip(self) -> None:
        """The Simulated data chip is on when anything on the screen is simulated (contract 9.6): the
        latest record, or anything the current page draws. A page says so with an optional
        `shows_simulated()` and `simulated_changed` signal; a page without them goes by the record."""
        s = self.state
        if s is None:
            return
        latest = s.latest()
        shows = getattr(self.pages.get(self.current), "shows_simulated", None)
        self.topbar.set_simulated(bool(latest and latest.get("simulated")) or bool(callable(shows) and shows()))

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
    """Records stored for the machine, or a dash when the receiver is not in this
    process. records() is capped at the history length, so it would understate it."""
    count = getattr(state, "stored_count", None)
    value = count() if callable(count) else None
    return f"{int(value):,}" if value is not None else "\u2013"
