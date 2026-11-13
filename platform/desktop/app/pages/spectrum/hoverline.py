"""The strip under a chart that reads out what the mouse is over (the mockup's
.hoverline and .th-foot): a hairline on top, soft 13px text, room for controls."""

from __future__ import annotations

from app.theme import fonts
from app.theme import tokens as T
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget


class HoverLine(QFrame):
    def __init__(self, idle: str, parent: QWidget | None = None, min_height: int = 34):
        super().__init__(parent)
        self.idle = idle
        self.setMinimumHeight(min_height)
        self.row = QHBoxLayout(self)
        self.row.setContentsMargins(16, 8, 16, 8)
        self.row.setSpacing(12)
        self.label = QLabel(idle, self)
        self.label.setProperty("role", "soft")
        self.label.setFont(fonts.body())
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        self.row.addWidget(self.label, 1)

    def set_text(self, text: str) -> None:
        self.label.setText(text or self.idle)

    def text(self) -> str:
        return self.label.text()

    def paintEvent(self, e) -> None:
        super().paintEvent(e)
        p = QPainter(self)
        p.setPen(QPen(T.qcolor(T.BORDER), 1))
        p.drawLine(QPointF(0, 0.5), QPointF(self.width(), 0.5))
