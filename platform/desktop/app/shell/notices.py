"""Notices: short cards stacked in the window's bottom-right corner.

Two kinds. A failure ("Answer not saved: ...") goes away by itself after
FAILURE_MS. An alarm stays until it is closed or its button is clicked,
because each alarm asks a question someone should answer.

The stack is a child of the window's central widget, kept above the pages,
so it never takes keyboard focus from them.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ..theme import fonts
from ..theme import tokens as t
from ..widgets import Button, StatusDot

FAILURE_MS = 8000
MAX_SHOWN = 4  # the oldest goes when a fifth comes
WIDTH = 360
MARGIN = 20  # from the window's right and bottom edges


class Notice(QFrame):
    """One card: a dot, a title, a line of detail, an optional action and a close button."""

    closed = Signal()
    acted = Signal()

    def __init__(self, kind: str, title: str, detail: str = "", action: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.kind = kind
        self.setProperty("cls", "notice")
        self.setFixedWidth(WIDTH)
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 12, 10, 12)
        row.setSpacing(10)
        # A shape carries the colour; text stays neutral (the red rule).
        dot = StatusDot(t.WARN if kind == "alarm" else t.MUTED, 8, self)
        row.addWidget(dot, 0, Qt.AlignmentFlag.AlignTop)
        text = QVBoxLayout()
        text.setSpacing(2)
        self.title_label = QLabel(title, self)
        self.title_label.setProperty("role", "strong")
        self.title_label.setFont(fonts.body())
        self.title_label.setWordWrap(True)
        text.addWidget(self.title_label)
        self.detail_label = QLabel(detail, self)
        self.detail_label.setProperty("role", "muted")
        self.detail_label.setFont(fonts.small())
        self.detail_label.setWordWrap(True)
        self.detail_label.setVisible(bool(detail))
        text.addWidget(self.detail_label)
        if action:
            self.action_button = Button(action, "secondary", self, small=True)
            self.action_button.clicked.connect(self._act)
            text.addSpacing(6)
            text.addWidget(self.action_button, 0, Qt.AlignmentFlag.AlignLeft)
        else:
            self.action_button = None
        row.addLayout(text, 1)
        self.close_button = Button("×", "ghost", self, small=True)
        self.close_button.setToolTip("Dismiss")
        self.close_button.clicked.connect(self.closed.emit)
        row.addWidget(self.close_button, 0, Qt.AlignmentFlag.AlignTop)

    def fit(self) -> None:
        """Fix the height for WIDTH, once styled: wrapped labels only know it for a width."""
        for w in (self, *self.findChildren(QWidget)):
            w.ensurePolished()
        self.setFixedHeight(self.heightForWidth(WIDTH))

    def _act(self) -> None:
        self.acted.emit()
        self.closed.emit()


class Notices(QWidget):
    """The stack. `show_failure(text)` and `show_alarm(...)` add a card on top."""

    def __init__(self, host: QWidget):
        super().__init__(host)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._col = QVBoxLayout(self)
        self._col.setContentsMargins(0, 0, 0, 0)
        self._col.setSpacing(8)
        self._col.addStretch(1)
        host.installEventFilter(self)
        self.hide()

    def notices(self) -> list[Notice]:
        """Shown cards, oldest first."""
        return [self._col.itemAt(i).widget() for i in range(1, self._col.count())]

    def show_failure(self, text: str) -> Notice:
        title, _, detail = text.partition(": ")
        notice = self._add(Notice("failure", title, detail, parent=self))
        QTimer.singleShot(FAILURE_MS, notice, lambda: self._remove(notice))
        return notice

    def show_alarm(self, title: str, detail: str, action: str, on_action) -> Notice:
        notice = self._add(Notice("alarm", title, detail, action, parent=self))
        notice.acted.connect(on_action)
        return notice

    def _add(self, notice: Notice) -> Notice:
        notice.closed.connect(lambda: self._remove(notice))
        self._col.addWidget(notice)
        notice.fit()
        while len(self.notices()) > MAX_SHOWN:
            self._remove(self.notices()[0])
        self._place()
        return notice

    def _remove(self, notice: Notice) -> None:
        if notice.parent() is not self:
            return  # already gone
        self._col.removeWidget(notice)
        notice.setParent(None)
        notice.deleteLater()
        self._place()

    def _place(self) -> None:
        host = self.parentWidget()
        shown = self.notices()
        if not shown or host is None:
            self.hide()
            return
        h = sum(n.height() for n in shown) + self._col.spacing() * (len(shown) - 1)
        self.setGeometry(host.width() - WIDTH - MARGIN, host.height() - h - MARGIN, WIDTH, h)
        self.show()
        self.raise_()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.parentWidget() and event.type() == QEvent.Type.Resize:
            self._place()
        return False
