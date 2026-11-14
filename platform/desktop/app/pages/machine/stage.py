"""The Machine page's large panel: the scan as a 3D model, or, when there is none to show, a card
that says which state holds and what to do about it.

It only shows what the page hands it and reports clicks. The 3D view itself is made by the page and
put in with `set_view`, so this file runs in the offscreen tests with no graphics card.
"""

from __future__ import annotations

from app.theme import fonts
from app.theme import tokens as t
from app.widgets import Button, Panel, motion
from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLineEdit,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import logic
from .marks import Steps, Strip, keep_place, label

PAD_V, PAD_H = t.PANEL_BODY_PAD
CARD_W = 440

TITLE = "Scan"
NEW_SCAN = "New scan"
RESET_VIEW = "Reset"
IMPORT = "Import file"
CONVERT_AGAIN = "Convert again"
ADD_PART = "Add part"
CANCEL = "Cancel"
PINNED = "Its pin goes where you clicked."
NO_PIN = "It will be listed without a pin."


class PartForm(Strip):
    """Name and kind for a new part. The place was chosen by the click before it, or there is none."""

    submitted = Signal(str, str)  # name, kind
    cancelled = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__("machinePartForm", parent, tinted=True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(PAD_H, 10, PAD_H, 10)
        lay.setSpacing(8)
        self.name = QLineEdit()
        self.name.setPlaceholderText("Part name")
        self.name.setFont(fonts.body())
        self.name.setMinimumWidth(90)
        self.name.setMaximumWidth(260)
        self.name.textChanged.connect(self._draw)
        self.name.returnPressed.connect(self.submit)
        self.kind = QComboBox()
        self.kind.setFont(fonts.body())
        for kind in logic.PART_KINDS:
            self.kind.addItem(logic.kind_label(kind), kind)
        self.add_button = Button(ADD_PART, "primary", small=True)
        self.add_button.clicked.connect(self.submit)
        self.cancel_button = Button(CANCEL, "ghost", small=True)
        self.cancel_button.clicked.connect(self.cancelled)
        self.note = label("", "muted", fonts.small())
        # the note takes the room that is left and is cut short in a narrow window, so it never widens the page
        self.note.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.note.setMinimumWidth(0)
        lay.addWidget(self.name, 2)
        lay.addWidget(self.kind)
        lay.addWidget(self.add_button)
        lay.addWidget(self.cancel_button)
        lay.addWidget(self.note, 3)
        self.clear()

    def clear(self) -> None:
        self.name.clear()
        self.kind.setCurrentIndex(logic.PART_KINDS.index(logic.DEFAULT_PART_KIND))
        self._draw()

    def set_pinned(self, pinned: bool) -> None:
        self.note.setText(PINNED if pinned else NO_PIN)

    def _draw(self, *_args) -> None:
        self.add_button.setEnabled(bool(self.name.text().strip()))

    def submit(self) -> None:
        name = self.name.text().strip()
        if name:
            self.submitted.emit(name, self.kind.currentData())


class Card(QWidget):
    """What is shown in place of the model: a title, a sentence or two, the steps of a scan on its
    way, and at most one button."""

    pressed = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(PAD_H, PAD_V, PAD_H, PAD_V)
        inner = QWidget()
        inner.setMaximumWidth(CARD_W)
        col = QVBoxLayout(inner)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        self.title = label("", "strong", fonts.font(t.FONT_PAGE_TITLE, 600, tracking=t.TRACK_TITLE), wrap=True)
        self.text = label("", "muted", wrap=True)
        self.steps = Steps()
        self.button = Button("", "secondary")
        self.button.clicked.connect(self.pressed)
        for w in (self.title, self.text):
            w.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        col.addStretch(1)
        col.addWidget(self.title)
        col.addSpacing(8)
        col.addWidget(self.text)
        col.addSpacing(18)
        col.addWidget(self.steps, 0, Qt.AlignmentFlag.AlignHCenter)
        col.addSpacing(18)
        col.addWidget(self.button, 0, Qt.AlignmentFlag.AlignHCenter)
        col.addStretch(1)
        outer.addStretch(1)
        outer.addWidget(inner, 10)
        outer.addStretch(1)

    def show_state(self, title: str, text: str, steps: list[dict], button: str) -> None:
        self.title.setText(title)
        self.text.setText(text)
        self.steps.set_steps(steps)
        self.steps.setVisible(bool(steps))
        self.button.setText(button)
        self.button.setVisible(bool(button))


class Stage(Panel):
    new_scan = Signal()
    reset_view = Signal()
    card_pressed = Signal()  # the card's one button: import a file, or convert again
    cancelled = Signal()  # leave adding or placing, or put the new part's form away
    part_submitted = Signal(str, str)  # name, kind

    def __init__(self, parent: QWidget | None = None):
        super().__init__(TITLE, parent=parent)
        self.reset_button = Button(RESET_VIEW, "ghost", small=True)
        self.reset_button.setToolTip("Reset view: turn the model back to where it started")
        self.reset_button.clicked.connect(self.reset_view)
        self.scan_button = Button(NEW_SCAN, small=True)
        self.scan_button.setToolTip("Pair the phone app with this laptop, or import a scan file")
        self.scan_button.clicked.connect(self.new_scan)
        self.header_end.addWidget(keep_place(self.reset_button))
        self.header_end.addWidget(self.scan_button)

        # what the next click on the model means, or that the last one missed
        self.hint_strip = Strip("machineHint", tinted=True)
        hl = QHBoxLayout(self.hint_strip)
        hl.setContentsMargins(PAD_H, 8, PAD_H - 4, 8)
        hl.setSpacing(10)
        self.hint_text = label("", "strong")
        self.hint_cancel = Button(CANCEL, "ghost", small=True)
        self.hint_cancel.clicked.connect(self.cancelled)
        hl.addWidget(self.hint_text, 1)
        hl.addWidget(self.hint_cancel)
        self.hint_reveal = self.add(motion.Reveal(self.hint_strip))

        self.form = PartForm()
        self.form.submitted.connect(self.part_submitted)
        self.form.cancelled.connect(self.cancelled)
        self.form_reveal = self.add(motion.Reveal(self.form))

        self.stack = QStackedWidget()
        self.card = Card()
        self.card.pressed.connect(self.card_pressed)
        self.stack.addWidget(self.card)
        self.view: QWidget | None = None
        self._holder: QWidget | None = None
        self.overlay: QWidget | None = None
        self.add(self.stack, 1)
        self.show_hint("", False)
        self.show_form(False)

    def set_view(self, view: QWidget) -> None:
        """Put the 3D view in. It is shown only when `show_model` says so."""
        self.view = view
        holder = QWidget()
        lay = QVBoxLayout(holder)
        # The panel's corners are round and the view's are not: keep it clear of the two bottom ones.
        lay.setContentsMargins(0, 0, 0, t.R - 4)
        lay.addWidget(view)
        self.stack.addWidget(holder)
        self._holder = holder
        self._place()

    def set_overlay(self, overlay: QWidget) -> None:
        """A card shown over the model or the state card, such as the New scan panel."""
        self.overlay = overlay
        overlay.setParent(self.stack)
        overlay.hide()
        self.stack.installEventFilter(self)
        self._place()

    def show_overlay(self, shown: bool) -> None:
        if self.overlay is not None:
            if shown:
                self.overlay.raise_()
                self._place()
            motion.set_shown(self.overlay, shown)  # settles in over the model, fades out

    def _place(self) -> None:
        if self.overlay is not None:
            self.overlay.setGeometry(self.stack.rect().adjusted(10, 2, -10, -10))

    def eventFilter(self, obj, e) -> bool:
        if obj is self.stack and e.type() == QEvent.Type.Resize:
            self._place()
        return super().eventFilter(obj, e)

    def show_model(self) -> None:
        if self.view is not None:
            self.stack.setCurrentWidget(self._holder)
        self.reset_button.setVisible(self.view is not None)

    def show_card(self, title: str, text: str, steps: list[dict], button: str) -> None:
        changed = title != self.card.title.text() or self.stack.currentWidget() is not self.card
        self.card.show_state(title, text, steps, button)
        self.stack.setCurrentWidget(self.card)
        if changed:
            motion.swap(self.card)  # one state of a scan gives way to the next
        self.reset_button.setVisible(False)

    def model_shown(self) -> bool:
        return self.view is not None and self.stack.currentWidget() is self._holder

    def show_hint(self, text: str, can_cancel: bool) -> None:
        if text:  # the old words stay while it closes
            self.hint_text.setText(text)
            self.hint_cancel.setVisible(can_cancel)
        self.hint_reveal.set_open(bool(text))

    def show_form(self, shown: bool, *, pinned: bool = False) -> None:
        was = self.form_reveal.is_open()
        if shown:  # the note stays as it was while the form closes
            self.form.set_pinned(pinned)
        self.form_reveal.set_open(shown)
        if shown and not was:
            self.form.name.setFocus()
