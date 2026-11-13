"""Motion: the durations, the off switch and the few moves every part of the app shares.

Only the app's own chrome moves: hover, selection, a page change, a card coming or
going. A reading is never eased from one value to the next, because the steps in
between would be numbers the device did not measure.

Motion is off when EFFENTE_REDUCE_MOTION is set (main.py's --no-motion sets it) and
on the offscreen platform, so tests and screenshots see the finished state at once.
With it off every helper here lands on its end value straight away.
"""

from __future__ import annotations

import os
from collections.abc import Callable

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QPoint,
    QRect,
    QRectF,
    Qt,
    QVariantAnimation,
)
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QGraphicsOpacityEffect, QWidget

# Milliseconds. HOVER and TOGGLE are the mockup's .12s and .15s transitions.
HOVER = 120
TOGGLE = 150
SLIDE = 180  # a selection moving to another option
PAGE = 320  # one page fading into the next
ENTER = 180  # a card appearing
EXIT = 140  # a card leaving
PULSE = 420  # the freshness dot's beat for a record

EASE = QEasingCurve.Type.OutCubic

_forced: bool | None = None


def enabled() -> bool:
    if _forced is not None:
        return _forced
    if os.environ.get("EFFENTE_REDUCE_MOTION"):
        return False
    return os.environ.get("QT_QPA_PLATFORM", "") != "offscreen"


def set_enabled(on: bool | None) -> None:
    """Force motion on or off; None goes back to the environment's answer."""
    global _forced
    _forced = on


def mix(a: QColor, b: QColor, f: float) -> QColor:
    """The colour a fraction f of the way from a to b, alpha included."""
    f = min(1.0, max(0.0, f))
    return QColor.fromRgbF(*(x + (y - x) * f for x, y in zip(a.getRgbF(), b.getRgbF(), strict=True)))


def lerp_rect(a: QRectF, b: QRectF, f: float) -> QRectF:
    return QRectF(a.x() + (b.x() - a.x()) * f, a.y() + (b.y() - a.y()) * f,
                  a.width() + (b.width() - a.width()) * f, a.height() + (b.height() - a.height()) * f)


class Glide(QVariantAnimation):
    """A number that eases to where it is sent and repaints its widget on the way.

    `at` is the number now. `to(x)` eases there; `jump(x)` sets it. A hidden widget
    jumps, so nothing is still moving when it is first shown.
    """

    def __init__(self, widget: QWidget, ms: int = HOVER, at: float = 0.0,
                 on_change: Callable[[], None] | None = None):
        super().__init__(widget)
        self._widget = widget
        self._on_change = on_change or widget.update
        self.at = float(at)
        self.setDuration(ms)
        self.setEasingCurve(EASE)
        self.valueChanged.connect(self._moved)

    def _moved(self, value) -> None:
        self.at = float(value)
        self._on_change()

    def to(self, target: float) -> None:
        self.stop()
        if not enabled() or not self._widget.isVisible():
            self.jump(target)
        elif target != self.at:
            self.setStartValue(self.at)
            self.setEndValue(float(target))
            self.start()

    def jump(self, target: float) -> None:
        self.stop()
        self.at = float(target)
        self._on_change()

    def moving(self) -> bool:
        return self.state() == QAbstractAnimation.State.Running


def _fade(widget: QWidget, start: float, end: float, ms: int, done: Callable[[], None] | None) -> None:
    old = getattr(widget, "_motion_fade", None)
    if old is not None:
        old.stop()
    effect = QGraphicsOpacityEffect(widget)
    effect.setOpacity(start)
    widget.setGraphicsEffect(effect)  # takes the place of, and deletes, an earlier one
    anim = QVariantAnimation(widget)
    anim.setDuration(ms)
    anim.setEasingCurve(EASE)
    anim.setStartValue(float(start))
    anim.setEndValue(float(end))
    anim.valueChanged.connect(lambda v: effect.setOpacity(float(v)))

    def finished() -> None:
        widget._motion_fade = None
        if end >= 1.0:
            widget.setGraphicsEffect(None)  # back to plain painting
        if done is not None:
            done()

    anim.finished.connect(finished)
    widget._motion_fade = anim
    anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)


def fade_in(widget: QWidget, ms: int = ENTER) -> None:
    """Call right after showing `widget`: it comes up from clear to solid."""
    if enabled():
        _fade(widget, 0.0, 1.0, ms, None)


def fade_out(widget: QWidget, done: Callable[[], None], ms: int = EXIT) -> None:
    """Fade `widget` to clear, then call `done` (which hides or removes it)."""
    if enabled() and widget.isVisible():
        _fade(widget, 1.0, 0.0, ms, done)
    else:
        done()


class _Snapshot(QWidget):
    """A picture of what an area showed, fading out over what it shows now."""

    def __init__(self, area: QWidget):
        super().__init__(area)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._picture = QPixmap()
        self._fade = Glide(self, PAGE, 0.0)
        # Even pace through the middle: an ease-out fade is nearly gone in a third
        # of its time and reads as no fade at all.
        self._fade.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._fade.finished.connect(self.hide)
        self._waiting = False
        self.hide()

    def play(self, picture: QPixmap) -> None:
        self._picture = picture
        self.setGeometry(self.parentWidget().rect())
        self.show()
        self.raise_()
        self._fade.jump(1.0)
        self._waiting = True

    def paintEvent(self, _event) -> None:
        if self._waiting:
            # The clock starts at the first frame, not before it: a page slow to
            # draw would otherwise use up the fade before anything was shown.
            self._waiting = False
            self._fade.to(0.0)
        p = QPainter(self)
        p.setOpacity(self._fade.at)
        p.drawPixmap(0, 0, self._picture)


def crossfade(area: QWidget) -> None:
    """Call just before changing what `area` shows: its old picture stays on top
    and fades out over the new content."""
    if not enabled() or not area.isVisible():
        return
    window = area.window()
    picture = window.grab(QRect(area.mapTo(window, QPoint(0, 0)), area.size()))
    snapshot = getattr(area, "_motion_snapshot", None)
    if snapshot is None:
        snapshot = area._motion_snapshot = _Snapshot(area)
    snapshot.play(picture)
