"""A row model for DataTable, and three cell kinds DataTable does not paint yet.

Used by the Evidence, Agents and Device & model pages. The extra cell kinds:

- a strong name followed by a muted code ("Unbalance-like  unbalance_like"),
- wrapped text, for long descriptions,
- a tag: muted text in a dashed ring ("possible · not validated"),
- a code in TEXT rather than MUTED (the agent's tool names),

plus a tinted row that is not the table's selection (rows with evidence now).
Everything else is drawn by DataTable's own delegate.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.theme import fonts
from app.theme import tokens as t
from app.widgets import DataTable
from app.widgets.table import _Delegate
from PySide6.QtCore import QAbstractTableModel, QRectF, QSize, Qt
from PySide6.QtGui import QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QHeaderView, QStyle

_USER = int(Qt.ItemDataRole.UserRole)
CodeRole = _USER + 21  # str: muted mono code painted after the text
WrapRole = _USER + 22  # bool: wrap the text over lines
TagRole = _USER + 23  # bool: paint the text as a dashed tag
TintRole = _USER + 24  # bool: tint the row as the mockup's `tr.sel`

LINE_H = 19
CODE_GAP = 6


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
    """Rows of cells. A cell is a str, or (text, code) for a strong name with its code."""

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
        return cell[0] if isinstance(cell, tuple) else str(cell)

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
        if col.pill:
            if role == DataTable.PillRole:
                return cell if isinstance(cell, tuple) else None
            if role == CodeRole:
                return None
        if role == DataTable.StrongRole:
            return col.strong
        if role == DataTable.MonoRole:
            return col.mono
        if role == CodeRole:
            return cell[1] if isinstance(cell, tuple) and len(cell) > 1 else None
        if role == WrapRole:
            return col.wrap
        if role == TagRole:
            return col.tag and bool(self.text(r, c))
        if role == TintRole:
            return self._tint[r]
        return None


class ReviewDelegate(_Delegate):
    """DataTable's delegate, plus the code, wrap, tag and tint cells."""

    def __init__(self, table: DataTable):
        super().__init__(table)
        self._text_font = fonts.font(t.FONT_TABLE)
        self._mono_font = fonts.mono()
        self._tag_font = fonts.chip()

    def paint(self, p: QPainter, option, index) -> None:
        if index.data(TintRole) and not option.state & QStyle.StateFlag.State_Selected:
            p.fillRect(option.rect, t.qcolor(t.ACCENT_BG))
        code = index.data(CodeRole)
        wrap = index.data(WrapRole)
        tag = index.data(TagRole)
        mono_strong = bool(index.data(DataTable.MonoRole) and index.data(DataTable.StrongRole))
        if not (code or wrap or tag or mono_strong):
            super().paint(p, option, index)
            return
        self._paint_frame(p, option, index)
        inner = QRectF(option.rect).adjusted(t.CELL_PAD_H, 0, -t.CELL_PAD_H, -1)
        text = index.data(Qt.ItemDataRole.DisplayRole) or ""
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if tag:
            self._paint_tag(p, inner, text)
        elif mono_strong:
            p.setFont(self._mono_font)
            p.setPen(t.qcolor(t.TEXT))
            p.drawText(inner, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                       p.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, int(inner.width())))
        elif code:
            p.setFont(self._text_font)
            p.setPen(t.qcolor(t.TEXT if index.data(DataTable.StrongRole) else t.SOFT))
            flags = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            p.drawText(inner, flags, text)
            x = p.fontMetrics().horizontalAdvance(text) + CODE_GAP
            p.setFont(self._mono_font)
            p.setPen(t.qcolor(t.MUTED))
            rest = inner.adjusted(x, 1, 0, 1)
            p.drawText(rest, flags, p.fontMetrics().elidedText(code, Qt.TextElideMode.ElideRight, int(rest.width())))
        else:
            p.setFont(self._text_font)
            p.setPen(t.qcolor(t.SOFT))
            p.drawText(inner, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextWordWrap), text)
        p.restore()

    def _paint_frame(self, p: QPainter, option, index) -> None:
        tbl = self.parent()
        r = QRectF(option.rect)
        if option.state & QStyle.StateFlag.State_Selected:
            p.fillRect(r, t.qcolor(t.ACCENT_BG))
        elif index.row() == tbl.hover_row():
            p.fillRect(r, t.qcolor(t.HOVER))
        if index.row() < index.model().rowCount() - 1:
            p.setPen(QPen(t.qcolor(t.GRID), 1))
            y = r.bottom() - 0.5
            p.drawLine(r.left(), y, r.right(), y)

    def _paint_tag(self, p: QPainter, inner: QRectF, text: str) -> None:
        p.setFont(self._tag_font)
        fm = p.fontMetrics()
        w = fm.horizontalAdvance(text) + 16
        r = QRectF(inner.left(), inner.center().y() - 9, min(w, inner.width()), 18).adjusted(0.5, 0.5, -0.5, -0.5)
        pen = QPen(t.qcolor(t.BORDER_STRONG), 1)
        pen.setStyle(Qt.PenStyle.CustomDashLine)
        pen.setDashPattern([3, 3])
        p.setPen(pen)
        path = QPainterPath()
        path.addRoundedRect(r, r.height() / 2, r.height() / 2)
        p.drawPath(path)
        p.setPen(t.qcolor(t.MUTED))
        p.drawText(r, int(Qt.AlignmentFlag.AlignCenter), fm.elidedText(text, Qt.TextElideMode.ElideRight, int(r.width()) - 8))

    def sizeHint(self, option, index) -> QSize:
        s = super().sizeHint(option, index)
        if index.data(WrapRole):
            tbl = self.parent()
            width = tbl.columnWidth(index.column()) - 2 * t.CELL_PAD_H
            text = index.data(Qt.ItemDataRole.DisplayRole) or ""
            fm = tbl.fontMetrics()
            br = fm.boundingRect(0, 0, max(40, width), 10_000, int(Qt.TextFlag.TextWordWrap), text)
            s.setHeight(max(t.ROW_H, br.height() + 2 * t.CELL_PAD_V + 1))
        return s


class RowTable(DataTable):
    """A DataTable over a RowModel, with fixed or shared column widths."""

    def __init__(self, columns: list[Column], parent=None, *, clickable: bool = False):
        super().__init__(parent, clickable=clickable)
        self.row_model = RowModel(columns, self)
        self.setModel(self.row_model)
        self.setItemDelegate(ReviewDelegate(self))
        if not clickable:
            self.setSelectionMode(DataTable.SelectionMode.NoSelection)
        self._columns = columns
        hh = self.horizontalHeader()
        hh.setStretchLastSection(False)
        for i, c in enumerate(columns):
            if c.width:
                hh.setSectionResizeMode(i, QHeaderView.ResizeMode.Fixed)
                self.setColumnWidth(i, c.width)
            else:
                hh.setSectionResizeMode(i, QHeaderView.ResizeMode.Stretch)
        if any(c.wrap for c in columns):
            self.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
            hh.sectionResized.connect(lambda *_: self.resizeRowsToContents())
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def set_rows(self, rows: list[list], keys: list | None = None, tint: list[bool] | None = None) -> None:
        self.row_model.set_rows(rows, keys, tint)
