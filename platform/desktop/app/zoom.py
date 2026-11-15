"""The size everything in the app is drawn at: 1.0 is the mockup's size.

Qt reads its scale factor once, when the QApplication is made, and every size in
the app is in pixels. So a new size shows by starting the app again: the window
saves the size and closes with `restart_wanted` set, and main.py starts again
(see `MainWindow.zoom_in`). The choice is kept with QSettings, like the scheme.
"""

from __future__ import annotations

import os

from . import settings

KEY = "zoom"
STEPS = (0.75, 0.8, 0.9, 1.0, 1.1, 1.25, 1.5, 1.75, 2.0)
DEFAULT = 1.0
# The scale factor the app was started with from outside, kept across restarts so
# the app's own size multiplies it instead of replacing it.
BASE_ENV = "EFFENTE_BASE_SCALE"

_current = DEFAULT


def current() -> float:
    """The size this run is drawn at."""
    return _current


def nearest(value) -> float:
    """The step closest to a stored value; anything unreadable gives the default."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return DEFAULT
    return min(STEPS, key=lambda s: abs(s - value))


def stepped(value: float, step: int, limit: float = STEPS[-1]) -> float:
    """The size `step` steps up or down from `value`, kept inside STEPS and no
    larger than `limit` (but never below the default because of it)."""
    i = STEPS.index(nearest(value)) + step
    new = STEPS[max(0, min(len(STEPS) - 1, i))]
    while new > max(limit, DEFAULT) and new > STEPS[0]:
        new = STEPS[STEPS.index(new) - 1]
    return new


def percent(value: float) -> str:
    return f"{round(value * 100)}%"


def saved() -> float:
    return nearest(settings.store().value(KEY, DEFAULT))


def save(value: float) -> None:
    settings.store().setValue(KEY, nearest(value))


def apply(value: float) -> float:
    """Tell Qt the size, through its environment variable. Call before the
    QApplication is made. Returns the size used."""
    global _current
    _current = nearest(value)
    base = os.environ.setdefault(BASE_ENV, os.environ.get("QT_SCALE_FACTOR") or "1")
    try:
        base_factor = float(base)
    except ValueError:
        base_factor = 1.0
    os.environ["QT_SCALE_FACTOR"] = f"{base_factor * _current:g}"
    return _current
