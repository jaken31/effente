"""Where the app keeps what it remembers between runs: the colour scheme, the
window's size, the page and the machine. One QSettings store for all of it."""

from __future__ import annotations


def store():
    from PySide6.QtCore import QSettings

    return QSettings("effente", "desktop")
