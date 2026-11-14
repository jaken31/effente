"""The page registry: the nine page keys in shortcut order, their packages, and a
placeholder for any page not built yet."""

from __future__ import annotations

import importlib
import inspect
import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ..theme import fonts
from ..theme import tokens as t

log = logging.getLogger("effente.desktop.shell")

# key -> (package under app.pages, title, subtitle). Order is the shortcut order 1 to 9.
# Titles and subtitles here are only for placeholders; a real page brings its own.
PAGES: dict[str, tuple[str, str, str]] = {
    "live": ("live", "Live", "current reading"),
    "spectrum": ("spectrum", "Spectrum", "what the sensor hears"),
    "thermal": ("thermal", "Thermal", "live heat image, optional sensor"),
    "history": ("history", "History", "every period and alarm for this machine"),
    "evidence": ("evidence", "Evidence", "why the device thinks what it thinks"),
    "agent": ("agents", "Agents", "the agent fleet on this laptop"),
    "device": ("device", "Device & model", "mode, library model, calibration and events"),
    # Added after the first seven, so shortcuts 1 to 7 stay as they were (docs/scan-and-log-plan.md).
    "machine": ("machine", "Machine", "3D scan of the machine, with a pin on each part"),
    "log": ("log", "Log", "every part's history, and what needs repair"),
}
KEYS = list(PAGES)
CHILDREN = ("spectrum", "thermal")  # shown under Live, with a Live crumb


class PlaceholderPage(QWidget):
    """Stands in for a page that is not built yet: shows its title."""

    navigate = Signal(str)

    def __init__(self, key: str, parent: QWidget | None = None):
        super().__init__(parent)
        _, self.title, self.subtitle = PAGES[key]
        self.key = key
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addStretch(1)
        head = QLabel(self.title, self)
        head.setFont(fonts.font(t.FONT_PAGE_TITLE, 600, tracking=t.TRACK_TITLE))
        head.setAlignment(Qt.AlignmentFlag.AlignCenter)
        note = QLabel("This page is not built yet.", self)
        note.setProperty("role", "muted")
        note.setFont(fonts.body())
        note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(head)
        lay.addSpacing(6)
        lay.addWidget(note)
        lay.addStretch(1)

    def bind(self, state) -> None:
        return None


def find_page_class(key: str) -> type[QWidget] | None:
    """The page class in app.pages.<package> whose `key` matches, or None if that
    package does not exist yet. Any other import error is a bug and is raised."""
    package = f"app.pages.{PAGES[key][0]}"
    try:
        module = importlib.import_module(package)
    except ModuleNotFoundError as e:
        if e.name and (package == e.name or package.startswith(e.name + ".")):
            return None
        raise
    for _, obj in inspect.getmembers(module, inspect.isclass):
        if issubclass(obj, QWidget) and getattr(obj, "key", None) == key:
            return obj
    log.warning("%s has no QWidget class with key %r; using a placeholder", package, key)
    return None


def make_page(key: str, *, placeholders: bool = False) -> QWidget:
    cls = None if placeholders else find_page_class(key)
    if cls is None:
        return PlaceholderPage(key)
    return cls()
