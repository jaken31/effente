"""Keyboard shortcuts, and the card that lists them.

One table drives both, so the list on screen is always the keys that work. Each
action is a MainWindow method. Pages take part by having the method the window
looks for: `export_dialog()` for Ctrl+E, and the Agents page's `run_agent()`.
"""

from __future__ import annotations

import sys

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QFrame, QGridLayout, QLabel, QVBoxLayout, QWidget

from ..theme import fonts
from .pages import KEYS

# Qt reads "Ctrl" in a key sequence as the Command key on macOS, so the list names that key there.
MOD = "Cmd" if sys.platform == "darwin" else "Ctrl"

# (key sequences, keys as shown, what it does, MainWindow method)
ACTIONS: list[tuple[tuple[str, ...], str, str, str]] = [
    (("Ctrl+K",), f"{MOD} K", "Search pages, machines and actions", "toggle_search"),
    (("Esc",), "Esc", "Back to Live from Spectrum or Thermal", "go_back"),
    (("[",), "[", "Previous machine", "previous_machine"),
    (("]",), "]", "Next machine", "next_machine"),
    (("Ctrl+R",), f"{MOD} R", "Run the report agent", "run_report"),
    (("Ctrl+E",), f"{MOD} E", "Export CSV, on Live and History", "export_page"),
    (("F11",), "F11", "Full screen", "toggle_full_screen"),
    (("?", "Shift+?", "F1"), "?", "Show or hide this list", "toggle_shortcut_help"),
    (("Ctrl+Q",), f"{MOD} Q", "Quit", "close"),
]
PAGES_ROW = (f"1 to {len(KEYS)}", "Open a page, in sidebar order")


def install(window) -> list[QShortcut]:
    """Register the page keys and every action on the window."""

    def add(sequence: str, slot) -> QShortcut:
        sc = QShortcut(QKeySequence(sequence), window)
        sc.setContext(Qt.ShortcutContext.WindowShortcut)
        sc.activated.connect(slot)
        return sc

    made = [add(str(i + 1), lambda k=key: window.navigate(k)) for i, key in enumerate(KEYS)]
    for sequences, _shown, _what, method in ACTIONS:
        made += [add(s, getattr(window, method)) for s in sequences]
    return made


class ShortcutHelp(QFrame):
    """The list of shortcuts, on a card in the middle of the window. A click hides it."""

    def __init__(self, host: QWidget):
        super().__init__(host)
        self.setProperty("cls", "notice")
        col = QVBoxLayout(self)
        col.setContentsMargins(22, 18, 22, 20)
        col.setSpacing(14)
        title = QLabel("Keyboard shortcuts", self)
        title.setProperty("role", "strong")
        title.setFont(fonts.panel_title())
        col.addWidget(title)
        grid = QGridLayout()
        grid.setHorizontalSpacing(28)
        grid.setVerticalSpacing(9)
        self.rows: list[tuple[str, str]] = [PAGES_ROW, *((shown, what) for _, shown, what, _ in ACTIONS)]
        for i, (shown, what) in enumerate(self.rows):
            key = QLabel(shown, self)
            key.setProperty("role", "strong")
            key.setFont(fonts.mono())
            text = QLabel(what, self)
            text.setProperty("role", "soft")
            text.setFont(fonts.body())
            grid.addWidget(key, i, 0)
            grid.addWidget(text, i, 1)
        col.addLayout(grid)
        host.installEventFilter(self)
        self.hide()

    def toggle(self) -> None:
        if self.isVisible():
            self.hide()
            return
        self._place()
        self.show()
        self.raise_()

    def _place(self) -> None:
        host = self.parentWidget()
        self.adjustSize()
        self.move((host.width() - self.width()) // 2, (host.height() - self.height()) // 2)

    def mousePressEvent(self, _event) -> None:
        self.hide()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.parentWidget() and event.type() == QEvent.Type.Resize and self.isVisible():
            self._place()
        return False
