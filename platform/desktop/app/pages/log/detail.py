"""The Log page's right column pieces: the add-entry row and the review panel."""

from __future__ import annotations

from app.pages.agents.kv import KeyValues
from app.theme import fonts
from app.theme import tokens as t
from app.widgets import Button, Panel, Segmented, SimChip
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLineEdit, QScrollArea, QVBoxLayout, QWidget

from . import view
from .marks import Strip, label

PAD_V, PAD_H = t.PANEL_BODY_PAD
NEEDS = [("yes", "Needs repair"), ("no", "No repair needed")]


class Compose(Strip):
    """Add an entry without leaving the table: pick a kind, type, press Enter.

    A note or a repair is typing and Enter. A finding or an inspection is two clicks
    more, its kind and whether it needs repair, which the contract makes the person
    say: there is no default for it.
    """

    submitted = Signal(str, str, object)  # kind, text, needs_repair (True, False or None)

    def __init__(self, parent=None):
        super().__init__("compose", parent, tinted=True)
        self._part = None
        lay = QHBoxLayout(self)
        lay.setContentsMargins(PAD_H, 10, PAD_H, 10)
        lay.setSpacing(10)
        self.kind = Segmented([(k, view.kind_label(k)) for k in view.PERSON_KINDS], current=view.DEFAULT_KIND,
                              small=True)
        self.kind.changed.connect(self._draw)
        self.text = QLineEdit()
        self.text.setFont(fonts.body())
        self.text.textChanged.connect(self._draw)
        self.text.returnPressed.connect(self.submit)
        self.needs = Segmented(NEEDS, small=True)
        self.needs.set_current(None)
        self.needs.changed.connect(self._draw)
        self.add_button = Button("Add entry", "primary", small=True)
        self.add_button.clicked.connect(self.submit)
        lay.addWidget(self.kind)
        lay.addWidget(self.text, 1)
        lay.addWidget(self.needs)
        lay.addWidget(self.add_button)
        self._draw()

    def needs_repair(self):
        """True, False, or None when not chosen or not asked for this kind."""
        if self.kind.current() not in view.NEEDS_ANSWER:
            return None
        return {"yes": True, "no": False}.get(self.needs.current())

    def problem(self) -> str:
        return view.entry_problem(self.kind.current(), self.text.text(), self.needs_repair())

    def set_part(self, part) -> None:
        self._part = part
        self._draw()

    def _draw(self, *_args) -> None:
        kind = self.kind.current()
        self.needs.setVisible(kind in view.NEEDS_ANSWER)
        self.text.setPlaceholderText(view.entry_placeholder(kind, self._part))
        problem = self.problem()
        self.add_button.setEnabled(self.isEnabled() and not problem)
        self.add_button.setToolTip(problem)

    def submit(self) -> None:
        if self.isEnabled() and not self.problem():
            self.submitted.emit(self.kind.current(), self.text.text().strip(), self.needs_repair())

    def fill(self, kind: str, text: str, needs_repair) -> None:
        """Put a draft in the form for the person to edit. Adds nothing."""
        self.kind.set_current(kind)
        self.needs.set_current({True: "yes", False: "no"}.get(needs_repair))
        self.text.setText(text)
        self._draw()
        self.text.setFocus()
        self.text.setCursorPosition(0)  # a long draft shows from its start

    def clear(self) -> None:
        """After an entry was added: empty, and back to a note."""
        self.text.clear()
        self.needs.set_current(None)
        self.kind.set_current(view.DEFAULT_KIND)
        self._draw()

    def changeEvent(self, e) -> None:
        super().changeEvent(e)
        if e.type() == e.Type.EnabledChange:
            self._draw()


class ReviewPanel(Panel):
    """The newest part review, as advice. Agreeing only asks the page to fill the
    add-entry row; the person still has to add it."""

    run = Signal(str)  # the mode: "standin" or "local"
    agree = Signal()

    def __init__(self, parent=None):
        super().__init__("Review", "advice from the part review agent", parent=parent)
        self.shown: view.Review | None = None
        self.sim_chip = SimChip("Simulated")
        self.sim_chip.setToolTip("This review was not written by a real model call.")
        self.sim_chip.hide()
        self.mode = Segmented(view.MODES, small=True)
        self.run_button = Button(view.RUN_REVIEW)
        self.run_button.clicked.connect(lambda: self.run.emit(self.mode.current() or "standin"))
        for w in (self.sim_chip, self.mode, self.run_button):
            self.header_end.addWidget(w)

        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        col = QVBoxLayout(inner)
        col.setContentsMargins(PAD_H, PAD_V, PAD_H, PAD_V)
        col.setSpacing(12)
        self.message = label("", "soft", wrap=True)
        self.rows = KeyValues(label_width=140)
        self.agree_button = Button(view.AGREE)
        self.agree_button.clicked.connect(self.agree)
        col.addWidget(self.message)
        col.addWidget(self.rows)
        col.addWidget(self.agree_button)
        col.addStretch(1)
        area.setWidget(inner)
        self.add(area, 1)

    def show_review(self, review: view.Review, *, run_text: str, can_run: bool) -> None:
        self.run_button.setText(run_text)
        self.run_button.setEnabled(can_run)
        self.mode.setEnabled(can_run)
        if review == self.shown:
            return
        self.shown = review
        self.message.setText(review.message)
        self.message.setProperty("role", "strong" if review.status in (view.FAILED, view.RUNNING) else "soft")
        self.message.style().unpolish(self.message)
        self.message.style().polish(self.message)
        self.rows.set_rows(list(review.rows), big=("Needs repair",))
        self.rows.setVisible(bool(review.rows))
        self.sim_chip.setVisible(review.simulated)
        self.agree_button.setVisible(review.can_agree)
