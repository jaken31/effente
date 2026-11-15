"""The Find boards window: what a search turned up, and Connect for the one picked."""

from __future__ import annotations

from app.pages.evidence.table_model import Column, RowTable
from app.theme import fonts
from app.widgets import Button
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QVBoxLayout

from . import view


class FindBoards(QDialog):
    """Starts a search when shown. `picked(address)` fires on Connect, and the window closes."""

    picked = Signal(str)

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.selected = ""  # the address of the row picked
        self._rows: list[tuple[str, bool]] = []
        self.setWindowTitle(view.FIND_TITLE)
        self.setModal(True)
        self.resize(820, 420)
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 18, 20, 18)
        v.setSpacing(12)
        self.status = QLabel()
        self.status.setProperty("role", "strong")
        self.status.setFont(fonts.body())
        self.status.setWordWrap(True)
        self.table = RowTable([
            Column("Device", strong=True),
            Column("Machine"),
            Column("Mode", width=110),
            Column("Address", mono=True, width=150),
            Column("Found by"),
        ], clickable=True)
        self.table.row_clicked.connect(self._pick)
        self.table.doubleClicked.connect(lambda _index: self.connect_picked())
        self.why = QLabel(view.FIND_WHY)
        self.why.setProperty("role", "muted")
        self.why.setFont(fonts.body())
        self.why.setWordWrap(True)
        self.again = Button(view.SEARCH_AGAIN, "ghost")
        self.again.clicked.connect(self.state.find_boards)
        self.close_button = Button("Close", "ghost")
        self.close_button.clicked.connect(self.reject)
        self.connect_button = Button(view.CONNECT, "primary")
        self.connect_button.clicked.connect(self.connect_picked)
        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(10)
        buttons.addWidget(self.again)
        buttons.addStretch(1)
        buttons.addWidget(self.close_button)
        buttons.addWidget(self.connect_button)
        v.addWidget(self.status)
        v.addWidget(self.table, 1)
        v.addWidget(self.why)
        v.addLayout(buttons)
        state.boards_changed.connect(self.refresh)
        self.finished.connect(lambda _code: self.state.stop_finding())
        state.find_boards()
        self.refresh()

    def refresh(self) -> None:
        found = self.state.found_boards()
        self._rows = view.board_addresses(found)
        self.table.set_rows(view.board_rows(found), keys=[a for a, _ in self._rows])
        self.status.setText(view.find_status(found))
        self.again.setEnabled(not found.get("searching"))
        answering = [a for a, ok in self._rows if ok]
        if self.selected not in answering:
            self.selected = answering[0] if len(answering) == 1 else ""  # the only one is picked already
        if self.selected:
            self.table.selectRow([a for a, _ in self._rows].index(self.selected))
        else:
            self.table.clearSelection()
        self.connect_button.setEnabled(bool(self.selected))

    def _pick(self, row: int) -> None:
        if 0 <= row < len(self._rows):
            address, answers = self._rows[row]
            self.selected = address if answers else ""  # a board that does not answer cannot be read
            self.refresh()

    def connect_picked(self) -> None:
        if self.selected:
            address = self.selected
            self.accept()
            self.picked.emit(address)
