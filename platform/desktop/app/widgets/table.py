"""DataTable: a QTableView with the mockup's table styling and a sticky header.

Cells are painted by CellDelegate so every row gets the mockup's look: 12px side
padding, soft text, a hairline under each row but the last, a row-wide hover and a
tinted selected row. Models may return these extra roles:

    DataTable.PillRole    (text, colour)  draw a Pill instead of text
    DataTable.FlagRole    bool            red dot before the text (abnormal)
    DataTable.StrongRole  bool            text in TEXT rather than SOFT (td.strong)
    DataTable.MonoRole    bool            mono, small, muted (.code); with StrongRole, in TEXT
    DataTable.CodeRole    str             a muted mono code after the text: "Unbalance unbalance_like"
    DataTable.WrapRole    bool            wrap the text over lines (needs DataTable(wrap=True))
    DataTable.TagRole     bool            the text as a dashed outline tag (.vtag)
    DataTable.TintRole    bool            on any cell: tint the row like tr.sel, without selecting it
    DataTable.DimRole     bool            text in MUTED (.muted), for dashes and "none"

Qt.ForegroundRole is honoured, except that any red is painted as TEXT (the RED rule).
Qt.TextAlignmentRole right-aligns a column, header included.
"""

from __future__ import annotations

from PySide6.QtCore import QModelIndex, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPainterPath, QPen
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
_LEFT = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
CODE_GAP = 5  # a space at 13.5px, as in "Name <span class=code>"
TAG_H = 18


class CellDelegate(QStyledItemDelegate):
    """Paints DataTable cells. Subclass and call paint_frame()/paint_* to add a cell kind."""

    def __init__(self, table: DataTable):
        super().__init__(table)
        self._table = table
        self.text_font = fonts.font(t.FONT_TABLE)
        self.mono_font = fonts.mono()
        self.pill_font = fonts.chip()
        self.tag_font = fonts.chip()

    # pieces
    def paint_frame(self, p: QPainter, option, index: QModelIndex) -> None:
        """Row background (selected, tinted or hovered) and the hairline under it."""
        r = QRectF(option.rect)
        row = index.row()
        if option.state & QStyle.StateFlag.State_Selected or self.row_tinted(index):
            p.fillRect(r, t.qcolor(t.ACCENT_BG))
        elif row == self._table.hover_row():
            p.fillRect(r, t.qcolor(t.HOVER))
        if row < index.model().rowCount(index.parent()) - 1:
            p.setPen(QPen(t.qcolor(t.GRID), 1))
            y = r.bottom() - 0.5
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))

    @staticmethod
    def row_tinted(index: QModelIndex) -> bool:
        """TintRole on any cell tints the whole row."""
        cols = index.model().columnCount(index.parent())
        return any(index.siblingAtColumn(c).data(DataTable.TintRole) for c in range(cols))

    @staticmethod
    def inner(option) -> QRectF:
        return QRectF(option.rect).adjusted(t.CELL_PAD_H, 0, -t.CELL_PAD_H, -1)

    def text_color(self, index: QModelIndex) -> QColor:
        fg = index.data(Qt.ItemDataRole.ForegroundRole)
        if fg is not None:
            return t.text_color(fg.color() if hasattr(fg, "color") else fg)
        strong = bool(index.data(DataTable.StrongRole))
        if index.data(DataTable.DimRole):
            return t.qcolor(t.MUTED)
        if index.data(DataTable.MonoRole):
            return t.qcolor(t.TEXT if strong else t.MUTED)
        return t.qcolor(t.TEXT if strong else t.SOFT)

    def paint_tag(self, p: QPainter, rect: QRectF, text: str) -> None:
        p.setFont(self.tag_font)
        fm = p.fontMetrics()
        w = fm.horizontalAdvance(text) + 16 + 2
        r = QRectF(rect.left(), rect.center().y() - TAG_H / 2, min(w, rect.width()), TAG_H).adjusted(0.5, 0.5, -0.5, -0.5)
        pen = QPen(t.qcolor(t.BORDER_STRONG), 1)
        pen.setStyle(Qt.PenStyle.CustomDashLine)
        pen.setDashPattern([3, 3])
        p.setPen(pen)
        path = QPainterPath()
        path.addRoundedRect(r, r.height() / 2, r.height() / 2)
        p.drawPath(path)
        p.setPen(t.qcolor(t.MUTED))
        p.drawText(r, int(Qt.AlignmentFlag.AlignCenter),
                   fm.elidedText(text, Qt.TextElideMode.ElideRight, max(0, int(r.width()) - 16)))

    def paint_code_after(self, p: QPainter, rect: QRectF, text: str, code: str, color: QColor) -> None:
        p.setFont(self.text_font)
        fm = p.fontMetrics()
        p.setPen(color)
        shown = fm.elidedText(text, Qt.TextElideMode.ElideRight, int(rect.width()))
        p.drawText(rect, int(_LEFT), shown)
        x = fm.horizontalAdvance(shown) + CODE_GAP
        if x >= rect.width():
            return
        p.setFont(self.mono_font)
        p.setPen(t.qcolor(t.MUTED))
        rest = rect.adjusted(x, 1, 0, 1)
        p.drawText(rest, int(_LEFT), p.fontMetrics().elidedText(code, Qt.TextElideMode.ElideRight, int(rest.width())))

    # QStyledItemDelegate
    def paint(self, p: QPainter, option, index: QModelIndex) -> None:
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.paint_frame(p, option, index)
        inner = self.inner(option)
        text = index.data(Qt.ItemDataRole.DisplayRole)
        text = "" if text is None else str(text)
        pill = index.data(DataTable.PillRole)
        code = index.data(DataTable.CodeRole)
        if pill:
            label, color = pill
            paint_pill(p, inner, str(label), t.qcolor(color) if isinstance(color, str) else QColor(color), self.pill_font)
        elif index.data(DataTable.TagRole):
            self.paint_tag(p, inner, text)
        elif code:
            self.paint_code_after(p, inner, text, str(code), self.text_color(index))
        elif index.data(DataTable.WrapRole):
            p.setFont(self.text_font)
            p.setPen(self.text_color(index))
            p.drawText(inner.adjusted(0, t.CELL_PAD_V, 0, -t.CELL_PAD_V),
                       int(_LEFT | Qt.TextFlag.TextWordWrap), text)
        else:
            align = index.data(Qt.ItemDataRole.TextAlignmentRole)
            align = Qt.AlignmentFlag(int(align)) if align is not None else Qt.AlignmentFlag.AlignLeft
            mono = bool(index.data(DataTable.MonoRole))
            paint_flag_value(p, inner, text, bool(index.data(DataTable.FlagRole)),
                             self.mono_font if mono else self.text_font, self.text_color(index), align)
        p.restore()

    def sizeHint(self, option, index) -> QSize:
        s = super().sizeHint(option, index)
        s.setWidth(s.width() + 2 * t.CELL_PAD_H)
        s.setHeight(t.ROW_H)
        if index.data(DataTable.WrapRole):
            width = self._table.columnWidth(index.column()) - 2 * t.CELL_PAD_H
            text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
            br = QFontMetrics(self.text_font).boundingRect(0, 0, max(40, width), 100_000,
                                                           int(Qt.TextFlag.TextWordWrap), text)
            s.setHeight(max(t.ROW_H, br.height() + 2 * t.CELL_PAD_V + 1))
        return s


_Delegate = CellDelegate  # old name, kept until the pages move to CellDelegate


class DataTable(QTableView):
    """Styled table view. `row_clicked(row)` fires on a left click when `clickable`.
    `wrap=True` lets rows grow to fit cells with WrapRole."""

    PillRole = _USER + 1
    FlagRole = _USER + 2
    StrongRole = _USER + 3
    MonoRole = _USER + 4
    CodeRole = _USER + 5
    WrapRole = _USER + 6
    TagRole = _USER + 7
    TintRole = _USER + 8
    DimRole = _USER + 9

    row_clicked = Signal(int)

    def __init__(self, parent: QWidget | None = None, *, clickable: bool = False, wrap: bool = False):
        super().__init__(parent)
        self._hover_row = -1
        self._clickable = clickable
        self.setItemDelegate(CellDelegate(self))
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
        if wrap:
            self.set_wrap(True)

    def set_wrap(self, on: bool) -> None:
        """Rows fit their wrapped cells, and refit when a column is resized."""
        vh = self.verticalHeader()
        if on:
            vh.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
            self.horizontalHeader().sectionResized.connect(self._refit)
        else:
            vh.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
            vh.setDefaultSectionSize(t.ROW_H)

    def _refit(self, *_args) -> None:
        self.resizeRowsToContents()

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
