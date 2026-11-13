"""The Ctrl+K search: type to find a page, a machine or an action, Enter to go.

A card near the top of the window, above the pages. The window supplies the
entries each time it opens (MainWindow.search_entries), so machines are current.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QModelIndex, QObject, QSize, Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from ..theme import fonts
from ..theme import tokens as t

WIDTH = 520
TOP = 96  # from the window's top edge
ROW_H = 36
MAX_ROWS = 8  # more than this scrolls


@dataclass
class Entry:
    label: str  # "History", "conveyor-08", "Run the report agent"
    kind: str  # shown on the right: "Page", "Machine", or the action's keys
    run: Callable[[], None]

    def matches(self, words: list[str]) -> bool:
        text = f"{self.label} {self.kind}".lower()
        return all(w in text for w in words)


class _Row(QStyledItemDelegate):
    """The label on the left, its kind on the right, a tint behind the chosen row."""

    def sizeHint(self, _option, _index: QModelIndex) -> QSize:
        return QSize(0, ROW_H)

    def paint(self, p: QPainter, option, index: QModelIndex) -> None:
        r = option.rect
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        chosen = bool(option.state & QStyle.StateFlag.State_Selected)
        if chosen:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(t.qcolor(t.NAV_ON))
            p.drawRoundedRect(r.adjusted(0, 1, 0, -1), t.R_SM, t.R_SM)
        inner = r.adjusted(12, 0, -12, 0)
        p.setFont(fonts.small())
        p.setPen(t.qcolor(t.MUTED))
        kind = index.data(Qt.ItemDataRole.UserRole) or ""
        p.drawText(inner, int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter), kind)
        room = inner.width() - p.fontMetrics().horizontalAdvance(kind) - 16
        p.setFont(fonts.body())
        p.setPen(t.qcolor(t.TEXT if chosen else t.SOFT))
        label = p.fontMetrics().elidedText(index.data() or "", Qt.TextElideMode.ElideRight, room)
        p.drawText(inner, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), label)
        p.restore()


class Search(QFrame):
    """`open(entries)` shows the card with the field focused; Esc, Enter or a
    click elsewhere closes it."""

    def __init__(self, host: QWidget):
        super().__init__(host)
        self.setProperty("cls", "notice")
        self.setFixedWidth(WIDTH)
        col = QVBoxLayout(self)
        col.setContentsMargins(10, 10, 10, 10)
        col.setSpacing(8)
        self.field = QLineEdit(self)
        self.field.setFont(fonts.body())
        self.field.setPlaceholderText("Search pages, machines and actions")
        self.field.setAccessibleName("Search")
        self.field.setMinimumHeight(34)
        self.field.textChanged.connect(self._filter)
        self.field.returnPressed.connect(self.run_chosen)
        self.field.installEventFilter(self)
        col.addWidget(self.field)
        self.results = QListWidget(self)
        self.results.setFrameShape(QFrame.Shape.NoFrame)
        self.results.setStyleSheet("QListWidget { background: transparent; }")
        self.results.setItemDelegate(_Row(self.results))
        self.results.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # typing stays in the field
        self.results.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.results.setCursor(Qt.CursorShape.PointingHandCursor)
        self.results.itemClicked.connect(lambda _item: self.run_chosen())
        col.addWidget(self.results)
        self.entries: list[Entry] = []
        self.shown: list[Entry] = []
        self._focus_before: QWidget | None = None
        host.installEventFilter(self)
        self.hide()

    def open(self, entries: list[Entry]) -> None:
        self.entries = list(entries)
        self._focus_before = QApplication.focusWidget()
        self.field.clear()
        self._filter("")
        self.show()
        self.raise_()
        self.field.setFocus()

    def close_search(self) -> None:
        if not self.isVisible():
            return
        self.hide()
        if self._focus_before is not None:
            self._focus_before.setFocus()
        self._focus_before = None

    def run_chosen(self) -> None:
        """Run the highlighted entry, if there is one, and close."""
        row = self.results.currentRow()
        entry = self.shown[row] if 0 <= row < len(self.shown) else None
        self.close_search()
        if entry is not None:
            entry.run()

    def _filter(self, text: str) -> None:
        words = text.lower().split()
        self.shown = [e for e in self.entries if e.matches(words)]
        self.results.clear()
        for e in self.shown:
            item = QListWidgetItem(e.label)
            item.setData(Qt.ItemDataRole.UserRole, e.kind)
            self.results.addItem(item)
        if self.shown:
            self.results.setCurrentRow(0)
        self.results.setVisible(bool(self.shown))
        self.results.setFixedHeight(ROW_H * min(len(self.shown), MAX_ROWS))
        self._place()

    def _step(self, by: int) -> None:
        if self.shown:
            self.results.setCurrentRow((self.results.currentRow() + by) % len(self.shown))

    def _place(self) -> None:
        host = self.parentWidget()
        self.adjustSize()
        self.move((host.width() - self.width()) // 2, TOP)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        kind = event.type()
        if watched is self.field:
            if kind == QEvent.Type.KeyPress and event.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                self._step(1 if event.key() == Qt.Key.Key_Down else -1)
                return True
            if kind == QEvent.Type.FocusOut:
                self.close_search()
        elif watched is self.parentWidget() and kind == QEvent.Type.Resize and self.isVisible():
            self._place()
        return False
