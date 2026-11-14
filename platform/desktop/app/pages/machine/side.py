"""The Machine page's smaller panels: the parts list, the New scan card that sits over the model, the
part review, and the part's log.

The panels only show what the page hands them and report clicks and typing. The page turns those
into `state.log` and `state.scans` calls. A list is rebuilt only when its rows differ, so a record a
second does not make it flicker.
"""

from __future__ import annotations

from app.theme import fonts
from app.theme import tokens as t
from app.theme.qss import css
from app.widgets import Button, Panel, Segmented, SimChip, motion
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QGuiApplication, QTextOption
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import logic, qr
from . import readings as rd
from .marks import PinMark, Strip, keep_place, label

PAD_V, PAD_H = t.PANEL_BODY_PAD

ADD_PART = "Add part"
PLACE = "Place"
OPEN_LOG = "Open log"
IMPORT = "Import file"
IMPORT_NOTE = "A USDZ or GLB file on this laptop, for when the phone cannot send its scan."
COPY = "Copy line"
COPIED = "Copied"
CLOSE = "Close"
SCAN_HOW = "Scan this code with the effente phone app, or paste the line under it into the app."
ADD_ENTRY = "Add entry"
CANCEL = "Cancel"
NO_ENTRIES = "No entries for this part yet."
NEEDS = [("yes", "Needs repair"), ("no", "Does not")]


def _scroll(cls=QScrollArea) -> QScrollArea:
    area = cls()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    return area


def _column(parent: QWidget, spacing: int = 0, margins=(0, 0, 0, 0)) -> QVBoxLayout:
    lay = QVBoxLayout(parent)
    lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    return lay


def _swap(area: QScrollArea, inner: QWidget) -> None:
    old = area.takeWidget()
    area.setWidget(inner)
    if old is not None:  # deleted later: a click on one of its rows may be what asked for this
        old.hide()
        old.deleteLater()


class _Fit(QScrollArea):
    """A scroll area that asks for the height of what it holds, so a short list takes no more room than
    it needs. It scrolls only when the window leaves less room than that.

    Call `fit()` after changing what it holds: the height it asks for eases to the new one, so the
    panels under it glide to their places."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._height = motion.Glide(self, motion.REVEAL, on_change=self.updateGeometry)
        self._height.finished.connect(lambda: self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded))

    def _natural(self) -> int:
        inner = self.widget()
        h = 0
        if inner is not None:
            w = self.viewport().width()
            h = inner.heightForWidth(w) if inner.hasHeightForWidth() and w > 0 else -1
            h = h if h > 0 else inner.sizeHint().height()
        return h + 2

    def fit(self) -> None:
        if not self._height.moving():
            self._height.at = float(self.height())  # from the height it has, whatever set it
        self._height.to(self._natural())  # jumps while hidden or with motion off
        if self._height.moving():  # no scroll bar flickering by while it grows into its room
            self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def sizeHint(self) -> QSize:
        h = round(self._height.at) if self._height.moving() else self._natural()
        return QSize(super().sizeHint().width(), h)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        if e.size().width() != e.oldSize().width():
            self.updateGeometry()  # wrapped text is taller in a narrower panel

    def minimumSizeHint(self) -> QSize:
        return QSize(0, 0)


class PartRow(QFrame):
    clicked = Signal(str)  # the part's id
    place = Signal(str)

    def __init__(self, row: dict, can_place: bool):
        super().__init__()
        self.row = row
        self.setObjectName("machinePartRow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(f"#machinePartRow {{ background: transparent; border: 0; "
                           f"border-top: 1px solid {css(t.GRID)}; }}")
        self.tint = motion.RowTint(self, row["selected"], lambda: t.qcolor(t.HOVER), lambda: t.qcolor(t.ACCENT_BG))
        lay = QHBoxLayout(self)
        lay.setContentsMargins(PAD_H, 6, PAD_H - 4, 6)
        lay.setSpacing(10)
        self.mark = PinMark(row["state"])
        lay.addWidget(self.mark, 0, Qt.AlignmentFlag.AlignTop)
        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(1)
        self.name = label(row["name"], "strong", fonts.font(t.FONT_TABLE, 500))
        line = logic.capitalise(row["line"]) + (f", {row['note']}" if row["note"] else "")
        self.line = label(line, "muted", fonts.small(), wrap=True)
        text.addWidget(self.name)
        text.addWidget(self.line)
        lay.addLayout(text, 1)
        self.place_button = None
        if can_place and row["action"] == "place":
            self.place_button = Button(PLACE, small=True)
            self.place_button.setToolTip("Then click the model where this part is")
            self.place_button.clicked.connect(lambda: self.place.emit(row["part_id"]))
            lay.addWidget(self.place_button, 0, Qt.AlignmentFlag.AlignVCenter)
        # the newest reading in mm/s, with "simulated" after it when it is (contract 9.6)
        self.number = label(row.get("number", rd.DASH), "soft")
        self.number.setToolTip("The newest reading, in mm/s")
        lay.addWidget(self.number, 0, Qt.AlignmentFlag.AlignVCenter)

    def paintEvent(self, e) -> None:
        self.tint.paint()
        super().paintEvent(e)  # the hairline goes over

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit(self.row["part_id"])
        super().mouseReleaseEvent(e)


class PartsPanel(Panel):
    """The current machine's parts, each with the mark its pin has on the model and its newest reading."""

    selected = Signal(str)
    place = Signal(str)
    add = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__("Parts", parent=parent)
        self._shown = None  # (rows, can_place) of the list as built
        self.rows: list[PartRow] = []
        self.add_button = Button(ADD_PART, small=True)
        self.add_button.clicked.connect(self.add)
        self.header_end.addWidget(self.add_button)
        self.note = label("", "empty", wrap=True)
        self.note.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.note_reveal = self.add(motion.Reveal(self.note))
        self.area = _scroll(_Fit)
        self.add(self.area, 1)
        self.set_rows([], False, "")

    def set_rows(self, rows: list[dict], can_place: bool, note: str) -> None:
        """`can_place`: a model is on screen, so a part with no pin there gets its "Place" button."""
        if (rows, can_place) != self._shown:
            was = {r["part_id"]: r["selected"] for r in self._shown[0]} if self._shown else {}
            self._shown = ([dict(r) for r in rows], can_place)
            inner = QWidget()
            col = _column(inner)
            self.rows = []
            for row in rows:
                w = PartRow(row, can_place)
                w.clicked.connect(self.selected)
                w.place.connect(self.place)
                col.addWidget(w)
                self.rows.append(w)
                if row["selected"] and was.get(row["part_id"]) is False and self.isVisible():
                    w.tint.pick_in()
                elif was and row["part_id"] not in was and self.isVisible():
                    motion.lift_in(w, ms=motion.REVEAL, drop=6.0)  # a part added just now
            col.addStretch(1)
            _swap(self.area, inner)
            self.area.fit()
        if note:  # the old words stay while it closes
            self.note.setText(note)
        self.note_reveal.set_open(bool(note))
        self.set_hint("newest reading, mm/s" if rows else "")


class ScanPanel(QFrame):
    """How a new scan gets here, shown over the model: the pairing code for the phone app, the same line as
    text, and a file from this laptop's disk."""

    changed = Signal()  # the machine id or the name was typed
    import_file = Signal()
    closed = Signal()

    QR_SIDE = 150

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("machineScanCard")
        self.setStyleSheet(f"#machineScanCard {{ background: {t.BG}; border: 1px solid {css(t.BORDER)}; "
                           f"border-radius: 8px; }}")
        inner = QWidget()
        inner.setObjectName("machineScanInner")
        inner.setStyleSheet("#machineScanInner { background: transparent; }")
        col = _column(inner, 10, (PAD_H, PAD_V, PAD_H, PAD_V))
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        self.heading = label("New scan from the phone", "strong", fonts.font(t.FONT_PANEL_TITLE, 600))
        self.close_button = Button(CLOSE, "ghost", small=True)
        self.close_button.clicked.connect(self.closed)
        head.addWidget(self.heading, 1)
        head.addWidget(keep_place(self.close_button))
        col.addLayout(head)

        fields = QHBoxLayout()
        fields.setContentsMargins(0, 0, 0, 0)
        fields.setSpacing(8)
        self.machine = QLineEdit()
        self.machine.setPlaceholderText("Machine id")
        self.machine.setToolTip("The machine the scan is for. Type a new id to start a new machine.")
        self.name = QLineEdit()
        self.name.setPlaceholderText("Name on the phone")
        self.name.setToolTip("Optional. What the phone app shows the person scanning.")
        for field in (self.machine, self.name):
            field.setFont(fonts.body())
            field.textEdited.connect(self.changed)
            fields.addWidget(field, 1)
        col.addLayout(fields)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(14)
        self.code = qr.QrCode(self.QR_SIDE)
        body.addWidget(self.code, 0, Qt.AlignmentFlag.AlignTop)
        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(8)
        self.note = label("", "muted", fonts.small(), wrap=True)
        text.addWidget(self.note)
        self.line = QPlainTextEdit()
        self.line.setReadOnly(True)
        self.line.setFont(fonts.mono())
        self.line.setFrameShape(QFrame.Shape.NoFrame)
        self.line.setWordWrapMode(QTextOption.WrapMode.WrapAnywhere)
        self.line.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.line.setFixedHeight(self.line.fontMetrics().lineSpacing() * 4 + 14)
        self.line.setAccessibleName("Pairing line")
        self.line.setObjectName("machinePairingLine")
        self.line.setStyleSheet(f"#machinePairingLine {{ background: {t.PANEL}; color: {t.SOFT}; "
                                f"border: 1px solid {css(t.BORDER)}; border-radius: {t.R_SM}px; padding: 4px 6px; }}")
        text.addWidget(self.line)
        text.addStretch(1)
        body.addLayout(text, 1)
        col.addLayout(body)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(8)
        self.copy_button = Button(COPY, "ghost", small=True)
        self.copy_button.clicked.connect(self.copy)
        self.import_button = Button(IMPORT, small=True)
        self.import_button.setToolTip(IMPORT_NOTE)
        self.import_button.clicked.connect(self.import_file)
        buttons.addWidget(self.import_button)
        buttons.addStretch(1)
        buttons.addWidget(keep_place(self.copy_button))
        col.addLayout(buttons)
        col.addStretch(1)
        area = _scroll()  # in a short window the card is shorter than its content
        area.viewport().setAutoFillBackground(False)
        area.setWidget(inner)
        outer = _column(self, 0, (1, 1, 1, 1))
        outer.addWidget(area)

    def machine_id(self) -> str:
        return self.machine.text().strip()

    def machine_name(self) -> str:
        return self.name.text().strip()

    def set_machine_id(self, machine_id: str) -> None:
        if machine_id != self.machine.text():
            self.machine.setText(machine_id)

    def show_code(self, text: str, note: str, can_close: bool) -> None:
        """`text` is the pairing line, or "" when there is none to show. `note` says why, or how to use it."""
        self.code.set_text(text)
        self.code.setVisible(bool(self.code.squares()))
        self.note.setText(note)
        self.note.setVisible(bool(note))
        if text != self.line.toPlainText():
            self.line.setPlainText(text)
            self.copy_button.setText(COPY)
        self.line.setVisible(bool(text))
        self.copy_button.setVisible(bool(text))
        self.close_button.setVisible(can_close)

    def copy(self) -> None:
        text = self.line.toPlainText()
        if text:
            QGuiApplication.clipboard().setText(text)
            self.copy_button.setText(COPIED)


class Fields(QWidget):
    """Label over value, one pair under another."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.col = _column(self, 6)
        self._rows: tuple = ()

    def set_rows(self, rows: tuple) -> None:
        if rows == self._rows:
            return
        self._rows = rows
        while self.col.count():
            item = self.col.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        for key, value in rows:
            pair = QWidget()
            pc = _column(pair, 0)
            pc.addWidget(label(key, "muted", fonts.small()))
            pc.addWidget(label(value, "strong", fonts.font(t.FONT_BASE, 500), wrap=True))
            self.col.addWidget(pair)

    def rows(self) -> tuple:
        return self._rows


class ReviewPanel(Panel):
    """The part's newest review, as advice. Agreeing only fills the log's add-entry form."""

    run = Signal()
    agree = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__("Review", parent=parent)
        self.sim_chip = SimChip("Simulated")
        self.sim_chip.setToolTip("This review was not written by a real model call, or is about simulated data.")
        self.sim_chip.hide()
        self.run_button = Button(rd.RUN_REVIEW, small=True)
        self.run_button.setToolTip("The stand-in part review. It reads only this part's own history.")
        self.run_button.clicked.connect(self.run)
        self.header_end.addWidget(self.sim_chip)
        self.header_end.addWidget(self.run_button)
        inner = QWidget()
        col = _column(inner, 10, (PAD_H, 2, PAD_H, PAD_V))
        self.message = label("", "muted", fonts.small(), wrap=True)
        self.fields = Fields()
        self.agree_button = Button(rd.AGREE, small=True)
        self.agree_button.clicked.connect(self.agree)
        col.addWidget(self.message)
        col.addWidget(self.fields)
        col.addWidget(self.agree_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.area = _scroll(_Fit)
        self.area.setWidget(inner)
        self.add(self.area, 1)
        self.shown: rd.Review | None = None
        self.show_review(None, False)

    def show_review(self, review: rd.Review | None, running: bool) -> None:
        self.run_button.setText(rd.REVIEWING if running else rd.RUN_REVIEW)
        self.run_button.setEnabled(review is not None and not running)
        if review == self.shown and self.message.text():
            return
        first = not self.message.text()
        self.shown = review
        review = review or rd.Review("Pick a part to read its review.")
        self.message.setText(review.message)
        self.fields.set_rows(review.rows)
        self.fields.setVisible(bool(review.rows))
        self.sim_chip.setVisible(review.simulated)
        self.agree_button.setVisible(review.can_agree)
        self.area.widget().adjustSize()
        self.area.fit()
        if not first and self.isVisible():
            motion.swap(self.area.widget())  # another part's review, or a new one, comes up in place


class Compose(Strip):
    """Add an entry about the part: pick a kind, type, press Enter. A finding or an inspection must say
    whether the part needs repair (contract 8.6): there is no default for it."""

    submitted = Signal(str, str, object)  # kind, text, needs_repair (True, False or None)
    cancelled = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__("machineCompose", parent, tinted=True)
        col = _column(self, 8, (PAD_H, 10, PAD_H, 10))
        self.kind = Segmented([(k, k.capitalize()) for k in rd.PERSON_KINDS], current="note", small=True)
        self.kind.changed.connect(self._draw)
        self.text = QLineEdit()
        self.text.setFont(fonts.body())
        self.text.setPlaceholderText("What happened")
        self.text.textChanged.connect(self._draw)
        self.text.returnPressed.connect(self.submit)
        self.needs = Segmented(NEEDS, small=True)
        self.needs.set_current(None)
        self.needs.changed.connect(self._draw)
        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(8)
        self.add_button = Button(ADD_ENTRY, "primary", small=True)
        self.add_button.clicked.connect(self.submit)
        self.cancel_button = Button(CANCEL, "ghost", small=True)
        self.cancel_button.clicked.connect(self.cancelled)
        buttons.addWidget(self.add_button)
        buttons.addWidget(self.cancel_button)
        buttons.addStretch(1)
        self.needs_reveal = motion.Reveal(self.needs)
        col.addWidget(self.kind)
        col.addWidget(self.text)
        col.addWidget(self.needs_reveal)
        col.addLayout(buttons)
        self._draw()

    def needs_repair(self):
        if self.kind.current() not in rd.NEEDS_ANSWER:
            return None
        return {"yes": True, "no": False}.get(self.needs.current())

    def problem(self) -> str:
        return rd.entry_problem(self.kind.current(), self.text.text(), self.needs_repair())

    def _draw(self, *_args) -> None:
        self.needs_reveal.set_open(self.kind.current() in rd.NEEDS_ANSWER)
        problem = self.problem()
        self.add_button.setEnabled(not problem)
        self.add_button.setToolTip(problem)

    def submit(self) -> None:
        if not self.problem():
            self.submitted.emit(self.kind.current(), self.text.text().strip(), self.needs_repair())

    def fill(self, kind: str, text: str, needs_repair) -> None:
        """Put a draft in the form for the person to edit. Adds nothing."""
        self.kind.set_current(kind)
        self.needs.set_current({True: "yes", False: "no"}.get(needs_repair))
        self.text.setText(text)
        self.text.setCursorPosition(0)
        self._draw()

    def clear(self) -> None:
        self.text.clear()
        self.needs.set_current(None)
        self.kind.set_current("note")
        self._draw()


class EntryRow(QFrame):
    def __init__(self, entry: rd.Entry):
        super().__init__()
        self.entry = entry
        self.setObjectName("machineEntry")
        self.setStyleSheet(f"#machineEntry {{ border: 0; border-top: 1px solid {css(t.GRID)}; }}")
        col = _column(self, 2, (PAD_H, 8, PAD_H, 8))
        self.head = label(entry.head, "muted", fonts.small(), wrap=True)
        col.addWidget(self.head)
        self.text = label(entry.text, "soft", wrap=True)
        self.text.setVisible(bool(entry.text))
        col.addWidget(self.text)


class LogPanel(Panel):
    """This part's entries, newest first. The one list on the page that scrolls."""

    open_log = Signal()
    submitted = Signal(str, str, object)

    def __init__(self, parent: QWidget | None = None):
        super().__init__("Log", parent=parent)
        self.log_button = Button(OPEN_LOG, "ghost", small=True)
        self.log_button.setToolTip("This part's whole history, on the Log page")
        self.log_button.clicked.connect(self.open_log)
        self.add_button = Button(ADD_ENTRY, small=True)
        self.add_button.clicked.connect(self.toggle_form)
        self.header_end.addWidget(self.log_button)
        self.header_end.addWidget(self.add_button)
        self.form = Compose()
        self.form.submitted.connect(self.submitted)
        self.form.cancelled.connect(self.close_form)
        self.form_reveal = self.add(motion.Reveal(self.form))
        self.form_reveal.set_open(False)
        self.empty = label(NO_ENTRIES, "empty", wrap=True)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.add(self.empty)
        self.area = _scroll()
        self.area.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Ignored)
        self.add(self.area, 1)
        self.rows: list[EntryRow] = []
        self._shown = None
        self.set_entries([])

    def form_open(self) -> bool:
        return self.form_reveal.is_open()

    def open_form(self) -> None:
        self.form_reveal.set_open(True)

    def toggle_form(self) -> None:
        if self.form_open():
            self.close_form()
        else:
            self.open_form()
            self.form.text.setFocus()

    def close_form(self) -> None:
        self.form.clear()
        self.form_reveal.set_open(False)

    def set_entries(self, entries: list[rd.Entry]) -> None:
        if entries == self._shown:
            return
        before = self._shown or []
        self._shown = list(entries)
        inner = QWidget()
        col = _column(inner)
        self.rows = [EntryRow(e) for e in entries]
        for row in self.rows:
            col.addWidget(row)
        col.addStretch(1)
        _swap(self.area, inner)
        self.empty.setVisible(not entries)
        self.area.setVisible(bool(entries))
        if not self.isVisible():
            return
        if before and entries[1:] == before:
            motion.lift_in(self.rows[0], ms=motion.REVEAL, drop=6.0)  # the entry added just now
        elif entries:
            motion.lift_in(inner, ms=motion.SWAP, drop=3.0)  # another part's entries
        else:
            motion.swap(self.empty)
