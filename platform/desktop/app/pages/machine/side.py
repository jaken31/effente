"""The Machine page's right column: the parts list, the selected part, and the New scan panel.

The panels only show what the page hands them and report clicks and typing. The page turns those
into `state.log` and `state.scans` calls. The list is rebuilt only when its rows differ, so a record
a second does not make it flicker.
"""

from __future__ import annotations

from app.theme import fonts
from app.theme import tokens as t
from app.theme.qss import css
from app.widgets import Button, Panel, SimChip
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication, QTextOption
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from . import logic, qr
from .marks import PinMark, keep_place, label

PAD_V, PAD_H = t.PANEL_BODY_PAD

ADD_PART = "Add part"
PLACE = "Place"
OPEN_LOG = "Open log"
NO_SELECTION = "Select a part in the list or on the model to see its state and its last entries."
IMPORT = "Import file"
IMPORT_NOTE = "A USDZ or GLB file on this laptop, for when the phone cannot send its scan."
COPY = "Copy line"
COPIED = "Copied"
CLOSE = "Close"
SCAN_HOW = "Scan this code with the effente phone app, or paste the line under it into the app."


def _scroll() -> QScrollArea:
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    return area


def _column(parent: QWidget, spacing: int = 0, margins=(0, 0, 0, 0)) -> QVBoxLayout:
    lay = QVBoxLayout(parent)
    lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    return lay


class PartRow(QFrame):
    clicked = Signal(str)  # the part's id
    place = Signal(str)

    def __init__(self, row: dict, can_place: bool):
        super().__init__()
        self.row = row
        self.setObjectName("machinePartRow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        bg = css(t.ACCENT_BG) if row["selected"] else "transparent"
        self.setStyleSheet(f"#machinePartRow {{ background: {bg}; border: 0; "
                           f"border-bottom: 1px solid {css(t.GRID)}; }}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(PAD_H, t.CELL_PAD_V, PAD_H - 4, t.CELL_PAD_V)
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

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit(self.row["part_id"])
        super().mouseReleaseEvent(e)


class PartsPanel(Panel):
    """The current machine's parts, each with the mark its pin has on the model."""

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
        self.add(self.note)
        self.area = _scroll()
        self.add(self.area, 1)
        self.set_rows([], False, "")

    def set_rows(self, rows: list[dict], can_place: bool, note: str) -> None:
        """`can_place`: a model is on screen, so a part with no pin there gets its "Place" button."""
        if (rows, can_place) != self._shown:
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
            col.addStretch(1)
            old = self.area.takeWidget()
            self.area.setWidget(inner)
            if old is not None:  # deleted later: a click on one of its rows may be what asked for this
                old.hide()
                old.deleteLater()
        self.note.setText(note)
        self.note.setVisible(bool(note))
        self.set_hint(f"{len(rows)}" if rows else "")


class DetailPanel(Panel):
    """The selected part: its state in the contract's words, its last entries, and the way to its log."""

    open_log = Signal()
    move_pin = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__("Part", parent=parent)
        self.sim_chip = SimChip()
        self.sim_chip.setToolTip("Some of these entries are about simulated alarms, or were written with no "
                                 "real model call.")
        self.header_end.addWidget(self.sim_chip)

        self.empty = label(NO_SELECTION, "empty", wrap=True)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.add(self.empty, 1)

        self.content = QWidget()
        col = _column(self.content, 0, (PAD_H, PAD_V, PAD_H, PAD_V))
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(10)
        self.mark = PinMark()
        self.headline = label("", "strong", fonts.font(t.FONT_BASE, 600), wrap=True)
        head.addWidget(self.mark, 0, Qt.AlignmentFlag.AlignTop)
        head.addWidget(self.headline, 1)
        self.means = label("", "muted", fonts.small(), wrap=True)
        self.pin_note = label("", "muted", fonts.small(), wrap=True)
        self.entries = QWidget()
        self.entries_col = _column(self.entries, 6, (0, 0, 0, 0))
        self.more = label("", "muted", fonts.small())
        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(8)
        self.log_button = Button(OPEN_LOG, small=True)
        self.log_button.setToolTip("This part's whole history, on the Log page")
        self.log_button.clicked.connect(self.open_log)
        self.pin_button = Button("", "ghost", small=True)
        self.pin_button.setToolTip("Then click the model where this part is")
        self.pin_button.clicked.connect(self.move_pin)
        buttons.addWidget(self.log_button)
        buttons.addWidget(self.pin_button)
        buttons.addStretch(1)
        col.addLayout(head)
        col.addSpacing(4)
        col.addWidget(self.means)
        col.addWidget(self.pin_note)
        col.addSpacing(12)
        col.addWidget(self.entries)
        col.addWidget(self.more)
        col.addStretch(1)
        col.addSpacing(12)
        col.addLayout(buttons)
        self.add(self.content, 1)
        self._detail = "unset"
        self.set_detail(None)

    def detail(self):
        return self._detail

    def set_detail(self, detail: dict | None) -> None:
        if detail == self._detail:
            return
        self._detail = detail
        self.empty.setVisible(detail is None)
        self.content.setVisible(detail is not None)
        self.sim_chip.setVisible(bool(detail and detail["simulated"]))
        if detail is None:
            self.set_title("Part")
            self.set_hint("")
            return
        self.set_title(detail["name"])
        self.set_hint(detail["kind"].lower())
        self.mark.set_state(detail["state"])
        self.headline.setText(detail["headline"])
        self.means.setText(detail["means"])
        self.pin_note.setText(detail["pin_note"])
        self.pin_note.setVisible(bool(detail["pin_note"]))
        while self.entries_col.count():
            item = self.entries_col.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        for entry in detail["entries"]:
            self.entries_col.addWidget(self._entry(entry))
        if detail["no_entries"]:
            self.entries_col.addWidget(label(detail["no_entries"], "muted", fonts.small(), wrap=True))
        self.more.setText(detail["more"])
        self.more.setVisible(bool(detail["more"]))
        self.pin_button.setText(detail["pin_action"])
        self.pin_button.setVisible(bool(detail["pin_action"]))

    @staticmethod
    def _entry(entry: dict) -> QWidget:
        w = QWidget()
        col = _column(w, 1)
        head = " · ".join(x for x in (entry["when"], entry["kind"]) if x)
        col.addWidget(label(head, "muted", fonts.small()))
        if entry["text"]:
            col.addWidget(label(entry["text"], "soft", wrap=True))
        return w


class ScanPanel(Panel):
    """How a new scan gets here: the pairing code for the phone app, the same line as text, and a
    file from this laptop's disk."""

    changed = Signal()  # the machine id or the name was typed
    import_file = Signal()
    closed = Signal()

    QR_SIDE = 172

    def __init__(self, parent: QWidget | None = None):
        super().__init__("New scan", "pair the phone app", parent=parent)
        self.close_button = Button(CLOSE, "ghost", small=True)
        self.close_button.clicked.connect(self.closed)
        self.header_end.addWidget(self.close_button)

        inner = QWidget()
        col = _column(inner, 10, (PAD_H, PAD_V, PAD_H, PAD_V))
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

        self.code = qr.QrCode(self.QR_SIDE)
        col.addWidget(self.code, 0, Qt.AlignmentFlag.AlignHCenter)
        self.note = label("", "muted", fonts.small(), wrap=True)
        col.addWidget(self.note)

        self.line = QPlainTextEdit()
        self.line.setReadOnly(True)
        self.line.setFont(fonts.mono())
        self.line.setFrameShape(QFrame.Shape.NoFrame)
        self.line.setWordWrapMode(QTextOption.WrapMode.WrapAnywhere)
        self.line.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.line.setFixedHeight(self.line.fontMetrics().lineSpacing() * 4 + 14)
        self.line.setAccessibleName("Pairing line")
        self.line.setObjectName("machinePairingLine")
        self.line.setStyleSheet(f"#machinePairingLine {{ background: {t.BG}; color: {t.SOFT}; "
                                f"border: 1px solid {css(t.BORDER)}; border-radius: {t.R_SM}px; padding: 4px 6px; }}")
        col.addWidget(self.line)

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
        area = _scroll()  # in the smallest window the panel is shorter than its content
        area.setWidget(inner)
        self.add(area, 1)

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
