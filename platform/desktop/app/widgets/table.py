"""DataTable: a QTableView with the mockup's table styling and a sticky header.

Cells are painted by a delegate so every row gets the mockup's look: 12px side
padding, soft text, a hairline under each row but the last, a row-wide hover and a
tinted selected row. Models may return these extra roles:

    DataTable.PillRole    (text, colour)  draw a Pill instead of text
    DataTable.FlagRole    bool            red dot before the text (abnormal)
    DataTable.StrongRole  bool            text in TEXT rather than SOFT
    DataTable.MonoRole    bool            mono, small, muted (codes and ids)

Qt.ForegroundRole is honoured, except that any red is painted as TEXT (the RED rule).
Qt.TextAlignmentRole right-aligns a column, header included.
"""

from __future__ import annotations

from PySide6.QtCore import QModelIndex, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QStyle,
    QStyledItemDelegate,
    QTableView,
    QWidget,
)

from ..theme import fonts
from ..theme import tokens as t
from .marks import paint_flag_value, paint_pill

_USER = int(Qt.ItemDataRole.UserRole)


class _Delegate(QStyledItemDelegate):
    def __init__(self, table: DataTable):
        super().__init__(table)
        self._table = table
        self._font = fonts.font(t.FONT_TABLE)
        self._mono = fonts.mono()
        self._pill = fonts.chip()

    def paint(self, p: QPainter, option, index: QModelIndex) -> None:
        tbl = self._table
        r = QRectF(option.rect)
        row = index.row()
        p.save()
        if option.state & QStyle.StateFlag.State_Selected:
            p.fillRect(r, t.qcolor(t.ACCENT_BG))
        elif row == tbl._hover_row:
            p.fillRect(r, t.qcolor(t.HOVER))
        if row < index.model().rowCount(index.parent()) - 1:
            p.setPen(QPen(t.qcolor(t.GRID), 1))
            y = r.bottom() - 0.5
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
        inner = r.adjusted(t.CELL_PAD_H, 0, -t.CELL_PAD_H, -1)

        pill = index.data(DataTable.PillRole)
        if pill:
            text, color = pill
            paint_pill(p, inner, str(text), t.qcolor(color) if isinstance(color, str) else QColor(color), self._pill)
            p.restore()
            return

        text = index.data(Qt.ItemDataRole.DisplayRole)
        text = "" if text is None else str(text)
        align = index.data(Qt.ItemDataRole.TextAlignmentRole)
        align = Qt.AlignmentFlag(int(align)) if align is not None else Qt.AlignmentFlag.AlignLeft
        mono = bool(index.data(DataTable.MonoRole))
        fg = index.data(Qt.ItemDataRole.ForegroundRole)
        if fg is not None:
            color = t.text_color(fg.color() if hasattr(fg, "color") else fg)
        elif mono:
            color = t.qcolor(t.MUTED)
        elif index.data(DataTable.StrongRole):
            color = t.qcolor(t.TEXT)
        else:
            color = t.qcolor(t.SOFT)
        paint_flag_value(p, inner, text, bool(index.data(DataTable.FlagRole)),
                         self._mono if mono else self._font, color, align)
        p.restore()

    def sizeHint(self, option, index):
        s = super().sizeHint(option, index)
        s.setHeight(t.ROW_H)
        s.setWidth(s.width() + 2 * t.CELL_PAD_H)
        return s


class DataTable(QTableView):
    """Styled table view. `row_clicked(row)` fires on a left click when `clickable`."""

    PillRole = _USER + 1
    FlagRole = _USER + 2
    StrongRole = _USER + 3
    MonoRole = _USER + 4

    row_clicked = Signal(int)

    def __init__(self, parent: QWidget | None = None, *, clickable: bool = False):
        super().__init__(parent)
        self._hover_row = -1
        self._clickable = clickable
        self.setItemDelegate(_Delegate(self))
        self.setFont(fonts.font(t.FONT_TABLE))
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.setMouseTracking(True)
        self.setFrameShape(QTableView.Shape.NoFrame)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setCornerButtonEnabled(False)
        vh = self.verticalHeader()
        vh.hide()
        vh.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        vh.setDefaultSectionSize(t.ROW_H)
        hh = self.horizontalHeader()
        hh.setFont(fonts.table_header())
        hh.setHighlightSections(False)
        hh.setSectionsClickable(False)
        hh.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        hh.setStretchLastSection(True)
        hh.setMinimumSectionSize(40)
        hh.setFixedHeight(t.CELL_PAD_V * 2 + 17)
        if clickable:
            self.viewport().setCursor(Qt.CursorShape.PointingHandCursor)

    def hover_row(self) -> int:
        return self._hover_row

    def _set_hover(self, row: int) -> None:
        if row != self._hover_row:
            self._hover_row = row
            self.viewport().update()

    def mouseMoveEvent(self, e) -> None:
        self._set_hover(self.rowAt(int(e.position().y())))
        super().mouseMoveEvent(e)

    def leaveEvent(self, e) -> None:
        self._set_hover(-1)
        super().leaveEvent(e)

    def mouseReleaseEvent(self, e) -> None:
        super().mouseReleaseEvent(e)
        if self._clickable and e.button() == Qt.MouseButton.LeftButton:
            row = self.rowAt(int(e.position().y()))
            if row >= 0:
                self.row_clicked.emit(row)
