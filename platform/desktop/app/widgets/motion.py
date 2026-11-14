"""Motion: the durations, the off switch and the few moves every part of the app shares.

Only the app's own chrome moves: hover, selection, a card coming or
going. A reading is never eased from one value to the next, because the steps in
between would be numbers the device did not measure.

Motion is off when EFFENTE_REDUCE_MOTION is set (main.py's --no-motion sets it) and
on the offscreen platform, so tests and screenshots see the finished state at once.
With it off every helper here lands on its end value straight away.

The moves: Glide (a number easing), fade_in and fade_out (a card coming or going),
lift_in, cascade and swap (cards settling into place, words changing in place),
set_shown (a card that comes and goes without moving its neighbours), Reveal (part of
a card opening or closing) and Sweep (a busy line).
"""

from __future__ import annotations

import os
from collections.abc import Callable

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QEvent,
    QObject,
    QPointF,
    QRectF,
    QSequentialAnimationGroup,
    Qt,
    QVariantAnimation,
)
from PySide6.QtGui import QColor, QLinearGradient, QPainter
from PySide6.QtWidgets import (
    QGraphicsEffect,
    QGraphicsOpacityEffect,
    QLayout,
    QVBoxLayout,
    QWidget,
)

# Milliseconds. HOVER and TOGGLE are the mockup's .12s and .15s transitions.
HOVER = 120
PRESS = 80  # a button going down under the pointer
RELEASE = 220  # and coming back up
TOGGLE = 150
SLIDE = 180  # a selection moving to another option
ENTER = 180  # a card appearing
EXIT = 140  # a card leaving
PULSE = 420  # the freshness dot's beat for a record
RISE = 340  # a page's cards settling in when it opens
STAGGER = 45  # between one card and the next as they settle
REVEAL = 240  # a part of a card opening or closing
SWAP = 220  # words changing in place
SWEEP = 1300  # one pass of the busy line

EASE = QEasingCurve.Type.OutCubic
SETTLE = QEasingCurve.Type.OutQuint  # longer moves: quick start, long soft landing
_MAX = 16777215  # QWIDGETSIZE_MAX

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


# ---- lift: fade and settle -------------------------------------------------------


class Lift(QGraphicsEffect):
    """Paints its widget faded and lowered: `opacity` 0 to 1, `drop` in pixels.

    Paint only: the layout never sees the drop, so nothing around the widget moves.
    """

    def __init__(self, parent: QObject | None = None, opacity: float = 1.0, drop: float = 0.0):
        super().__init__(parent)
        self.opacity = opacity
        self.drop = drop
        self._room = max(0.0, drop)

    def set(self, opacity: float, drop: float) -> None:
        self.opacity, self.drop = opacity, drop
        self.update()

    def boundingRectFor(self, rect: QRectF) -> QRectF:
        return rect.adjusted(0, 0, 0, self._room + 1)  # room for the drop, so its trail repaints

    def draw(self, painter: QPainter) -> None:
        if self.opacity >= 1.0 and not self.drop:
            self.drawSource(painter)
            return
        if self.opacity <= 0.0:
            return
        # PySide6 returns only the pixmap, not its offset. Unpadded, the pixmap covers the
        # source's own rect exactly, so that rect's corner is the offset.
        logical = Qt.CoordinateSystem.LogicalCoordinates
        pixmap = self.sourcePixmap(logical, None, QGraphicsEffect.PixmapPadMode.NoPad)
        painter.save()
        painter.setOpacity(self.opacity)
        painter.drawPixmap(self.sourceBoundingRect(logical).topLeft() + QPointF(0, self.drop), pixmap)
        painter.restore()


def lift_in(widget: QWidget, delay: int = 0, ms: int = RISE, drop: float = 10.0) -> None:
    """Fade `widget` in while it settles `drop` pixels up into place, after `delay` ms.
    It is clear from the moment this is called, so a delayed card never flashes first."""
    if not enabled():
        return
    old = getattr(widget, "_motion_fade", None)
    if old is not None:
        old.stop()
    effect = Lift(widget, 0.0, drop)
    widget.setGraphicsEffect(effect)
    anim = QVariantAnimation()
    anim.setDuration(ms)
    anim.setEasingCurve(SETTLE)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    # Opacity arrives a little ahead of the position: the card is solid before it lands.
    anim.valueChanged.connect(lambda f: effect.set(min(1.0, float(f) * 1.35), drop * (1.0 - float(f))))
    group = QSequentialAnimationGroup(widget)
    if delay:
        group.addPause(delay)
    group.addAnimation(anim)

    def finished() -> None:
        widget._motion_fade = None
        widget.setGraphicsEffect(None)

    group.finished.connect(finished)
    widget._motion_fade = group
    group.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)


def cascade(widgets, step: int = STAGGER, drop: float = 10.0) -> None:
    """Settle shown widgets in one after another, top to bottom."""
    shown = [w for w in widgets if not w.isHidden()]
    for i, w in enumerate(shown):
        lift_in(w, delay=i * step, drop=drop)


def swap(widget: QWidget) -> None:
    """Call right after a visible widget's words change: the new words fade up into place."""
    if widget.isVisible():
        lift_in(widget, ms=SWAP, drop=3.0)


def set_shown(widget: QWidget, on: bool, drop: float = 8.0) -> None:
    """In place of `setVisible`, for a card that lies over others or keeps its own room.

    Shown, it fades in while it settles `drop` pixels up into place; hidden, it fades out
    first. Asking for what it already is, or is on its way to, does nothing, so a page
    that redraws every second can call it each time. Use Reveal where the neighbours
    should make room instead.
    """
    on = bool(on)
    was = getattr(widget, "_motion_shown", None)
    if was is None:
        was = not widget.isHidden()
    widget._motion_shown = on
    parent = widget.parentWidget()
    if not enabled() or parent is None or not parent.isVisible():
        old = getattr(widget, "_motion_fade", None)
        if old is not None:
            old.stop()
            widget._motion_fade = None
        if widget.graphicsEffect() is not None:
            widget.setGraphicsEffect(None)
        widget.setVisible(on)
        return
    if on == was:
        return
    if on:
        effect = widget.graphicsEffect()
        if widget.isVisible() and isinstance(effect, QGraphicsOpacityEffect):
            _fade(widget, effect.opacity(), 1.0, ENTER, None)  # turned back halfway: up from where it is
        else:
            widget.show()
            lift_in(widget, ms=ENTER + 60, drop=drop)
        return

    def gone() -> None:
        if not widget._motion_shown:  # not asked back in the meantime
            widget.hide()
            widget.setGraphicsEffect(None)

    fade_out(widget, gone)


# ---- reveal: a part that opens and closes ------------------------------------------


class Reveal(QWidget):
    """Holds one widget and opens or closes it: the height eases while the content fades.

    Use `set_open` in place of the child's `setVisible`. Closed, the Reveal is hidden,
    so the child reads as not visible. The child keeps its own size while the Reveal
    clips it, so its words never reflow on the way.
    """

    def __init__(self, child: QWidget, parent: QWidget | None = None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        lay.addWidget(child)
        self.child = child
        self._open = True
        self._from = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(REVEAL)
        self._anim.setEasingCurve(EASE)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.valueChanged.connect(self._step)
        self._anim.finished.connect(self._landed)

    def is_open(self) -> bool:
        return self._open

    def moving(self) -> bool:
        return self._anim.state() == QAbstractAnimation.State.Running

    def _natural(self) -> float:
        lay = self.layout()
        if lay.hasHeightForWidth() and self.width() > 0:
            return float(lay.totalHeightForWidth(self.width()))
        return float(lay.totalSizeHint().height())

    def set_open(self, on: bool) -> None:
        if on == self._open:
            return
        self._open = on
        parent = self.parentWidget()
        if not enabled() or parent is None or not parent.isVisible():
            self._anim.stop()
            self._landed()
            return
        self._from = float(self.height()) if (self.isVisible() and (self.moving() or not on)) else 0.0
        self._anim.stop()
        if on:
            self.setMaximumHeight(round(self._from))
            self.show()
            if parent.layout() is not None:
                parent.layout().activate()  # give it its width now, so its natural height is right
        old = getattr(self.child, "_motion_fade", None)
        if old is not None:
            old.stop()
            self.child._motion_fade = None
        self.child.setGraphicsEffect(Lift(self.child, 0.0 if on else 1.0))
        self._anim.start()

    def _step(self, f) -> None:
        f = float(f)
        to = self._natural() if self._open else 0.0
        self.setMaximumHeight(max(0, round(self._from + (to - self._from) * f)))
        effect = self.child.graphicsEffect()
        if isinstance(effect, Lift):
            # In: the words wait for the room to open. Out: they go first.
            effect.set(min(1.0, max(0.0, f * 1.6 - 0.4)) if self._open else max(0.0, 1.0 - f * 1.8), 0.0)

    def _landed(self) -> None:
        if isinstance(self.child.graphicsEffect(), Lift):
            self.child.setGraphicsEffect(None)
        self.setMaximumHeight(_MAX)
        self.setVisible(self._open)


# ---- sweep: something is being worked on --------------------------------------------


class Sweep(QWidget):
    """A short bright line running along the top edge of its parent while busy.

    It says only "working": it never stands for an amount done. With motion off it is
    not drawn at all, since a still line would read as a progress bar.
    """

    H = 2

    def __init__(self, parent: QWidget, color: QColor):
        super().__init__(parent)
        self._color = QColor(color)
        self._at = 0.0
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(SWEEP)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setLoopCount(-1)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._anim.valueChanged.connect(self._moved)
        parent.installEventFilter(self)
        self._fit()
        self.hide()

    def _fit(self) -> None:
        self.setGeometry(0, 0, self.parentWidget().width(), self.H)

    def eventFilter(self, obj, event) -> bool:
        if obj is self.parentWidget() and event.type() == QEvent.Type.Resize:
            self._fit()
        return False

    def _moved(self, v) -> None:
        self._at = float(v)
        self.update()

    def running(self) -> bool:
        return self._anim.state() == QAbstractAnimation.State.Running

    def set_running(self, on: bool) -> None:
        on = on and enabled()
        if on == self.running():
            return
        if on:
            self._fit()
            self.raise_()
            self.show()
            self._anim.start()
        else:
            self._anim.stop()
            self.hide()

    def paintEvent(self, _event) -> None:
        w = float(self.width())
        if w <= 0:
            return
        seg = w * 0.32
        x = -seg + (w + seg) * self._at
        grad = QLinearGradient(x, 0, x + seg, 0)
        clear = QColor(self._color)
        clear.setAlphaF(0.0)
        grad.setColorAt(0.0, clear)
        grad.setColorAt(0.5, self._color)
        grad.setColorAt(1.0, clear)
        p = QPainter(self)
        p.fillRect(QRectF(x, 0, seg, self.H), grad)
