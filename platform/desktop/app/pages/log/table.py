"""The entry table: the app's DataTable over the rows of view.entry_rows.

Cells are painted by DataTable's own delegate through its roles. An entry that says
"needs repair: yes" gets the red dot before the word, never red text.
"""

from __future__ import annotations

from app.widgets import DataTable
from PySide6.QtCore import QAbstractTableModel, Qt
from PySide6.QtWidgets import QHeaderView

from . import view

# (header, EntryRow field, fixed width in px or None to take the rest)
COLUMNS = (
    ("When", "when", 116),
    ("Part", "part", 132),
    ("Kind", "kind", 98),
    ("What", "text", None),
    ("Needs repair", "needs_repair", 118),
    ("By", "by", 142),
    ("Alarm", "alarm", 78),
    ("", "tag", 108),
)
FIELDS = [c[1] for c in COLUMNS]
PART, WHAT, NEEDS, ALARM, TAG = (FIELDS.index(f) for f in ("part", "text", "needs_repair", "alarm", "tag"))


class EntryModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: list[view.EntryRow] = []

    def set_rows(self, rows: list[view.EntryRow]) -> None:
        self.beginResetModel()
        self.rows = list(rows)
        self.endResetModel()

    def rowCount(self, parent=None) -> int:
        return 0 if parent is not None and parent.isValid() else len(self.rows)

    def columnCount(self, parent=None) -> int:
        return 0 if parent is not None and parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section][0]
        return None

    def text(self, row: int, col: int) -> str:
        return getattr(self.rows[row], FIELDS[col])

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        r, c = index.row(), index.column()
        text = self.text(r, c)
        if role == Qt.ItemDataRole.DisplayRole:
            return text
        if role == DataTable.StrongRole:
            return c == WHAT
        if role == DataTable.WrapRole:
            return c == WHAT
        if role == DataTable.FlagRole:
            return c == NEEDS and self.rows[r].flag
        if role == DataTable.MonoRole:
            return c == ALARM and text != view.DASH
        if role == DataTable.TagRole:
            return c == TAG and bool(text)
        if role == DataTable.DimRole:
            return text == view.DASH
        return None


class EntryTable(DataTable):
    """Entries, newest first. The Part column shows only when the whole machine is listed."""

    def __init__(self, parent=None):
        super().__init__(parent, wrap=True)
        self.entries = EntryModel(self)
        self.setModel(self.entries)
        self.setSelectionMode(DataTable.SelectionMode.NoSelection)
        hh = self.horizontalHeader()
        hh.setStretchLastSection(False)
        for i, (_header, _field, width) in enumerate(COLUMNS):
            if width:
                hh.setSectionResizeMode(i, QHeaderView.ResizeMode.Fixed)
                self.setColumnWidth(i, width)
            else:
                hh.setSectionResizeMode(i, QHeaderView.ResizeMode.Stretch)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def set_rows(self, rows: list[view.EntryRow], *, with_part: bool) -> None:
        self.entries.set_rows(rows)
        self.setColumnHidden(PART, not with_part)
        self.resizeRowsToContents()
