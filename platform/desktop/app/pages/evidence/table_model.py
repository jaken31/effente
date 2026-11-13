"""A row model for DataTable, and a DataTable with column widths set per column.

Used by the Evidence, Agents and Device & model pages. Cells are painted by
DataTable's own CellDelegate through its roles; this module only says which
role each column uses.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.widgets import DataTable
from PySide6.QtCore import QAbstractTableModel, Qt
from PySide6.QtWidgets import QHeaderView

DIM_TEXTS = ("–", "none")


@dataclass(frozen=True)
class Column:
    header: str
    right: bool = False
    strong: bool = False
    mono: bool = False
    wrap: bool = False
    tag: bool = False
    pill: bool = False  # cells are (text, colour) for a Pill, or plain text
    width: int | None = None  # fixed width in px; None shares the rest


class RowModel(QAbstractTableModel):
    """Rows of cells. A cell is a str, or (text, code) for a name with its code after it."""

    def __init__(self, columns: list[Column], parent=None):
        super().__init__(parent)
        self.columns = columns
        self._rows: list[list] = []
        self._tint: list[bool] = []
        self.keys: list = []

    def set_rows(self, rows: list[list], keys: list | None = None, tint: list[bool] | None = None) -> None:
        self.beginResetModel()
        self._rows = [list(r) for r in rows]
        self.keys = list(keys) if keys is not None else list(range(len(rows)))
        self._tint = list(tint) if tint is not None else [False] * len(rows)
        self.endResetModel()

    def rowCount(self, parent=None) -> int:
        return 0 if parent is not None and parent.isValid() else len(self._rows)

    def columnCount(self, parent=None) -> int:
        return 0 if parent is not None and parent.isValid() else len(self.columns)

    def text(self, row: int, col: int) -> str:
        cell = self._rows[row][col]
        return str(cell[0]) if isinstance(cell, tuple) else str(cell)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation != Qt.Orientation.Horizontal:
            return None
        col = self.columns[section]
        if role == Qt.ItemDataRole.DisplayRole:
            return col.header
        if role == Qt.ItemDataRole.TextAlignmentRole and col.right:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        r, c = index.row(), index.column()
        col = self.columns[c]
        cell = self._rows[r][c]
        if role == Qt.ItemDataRole.DisplayRole:
            return self.text(r, c)
        if role == Qt.ItemDataRole.TextAlignmentRole and col.right:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if role == DataTable.PillRole:
            return cell if col.pill and isinstance(cell, tuple) else None
        if role == DataTable.CodeRole:
            return cell[1] if not col.pill and isinstance(cell, tuple) and len(cell) > 1 else None
        if role == DataTable.StrongRole:
            return col.strong
        if role == DataTable.MonoRole:
            return col.mono
        if role == DataTable.WrapRole:
            return col.wrap
        if role == DataTable.TagRole:
            return col.tag and bool(self.text(r, c))
        if role == DataTable.TintRole:
            return self._tint[r]
        if role == DataTable.DimRole:
            return self.text(r, c) in DIM_TEXTS
        return None


class RowTable(DataTable):
    """A DataTable over a RowModel, with fixed or shared column widths."""

    def __init__(self, columns: list[Column], parent=None, *, clickable: bool = False):
        super().__init__(parent, clickable=clickable, wrap=any(c.wrap for c in columns))
        self.row_model = RowModel(columns, self)
        self.setModel(self.row_model)
        if not clickable:
            self.setSelectionMode(DataTable.SelectionMode.NoSelection)
        hh = self.horizontalHeader()
        hh.setStretchLastSection(False)
        for i, c in enumerate(columns):
            if c.width:
                hh.setSectionResizeMode(i, QHeaderView.ResizeMode.Fixed)
                self.setColumnWidth(i, c.width)
            else:
                hh.setSectionResizeMode(i, QHeaderView.ResizeMode.Stretch)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def set_rows(self, rows: list[list], keys: list | None = None, tint: list[bool] | None = None) -> None:
        self.row_model.set_rows(rows, keys, tint)
