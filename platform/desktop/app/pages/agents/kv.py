"""A label/value grid: the mockup's `dl.kv` (110px muted labels, soft values)."""

from __future__ import annotations

from html import escape

from app.theme import fonts
from app.theme import tokens as t
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QLabel, QWidget

LINE_H = 19  # 13px text at the mockup's 1.45 line height


def code_html(name: str) -> str:
    """A feature name in the mono face, muted, after its text."""
    return f'<span style="font-family: \'{fonts.mono().family()}\'; font-size: {t.FONT_MONO}px; color: {t.MUTED};">{escape(name)}</span>'


class KeyValues(QWidget):
    """Rows of (label, value). A value is plain text, or ("html", text) for rich text
    built only from escaped strings and code_html()."""

    def __init__(self, label_width: int = 110, parent=None):
        super().__init__(parent)
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(12)
        self._grid.setVerticalSpacing(8)
        self._grid.setColumnMinimumWidth(0, label_width)
        self._grid.setColumnStretch(1, 1)
        self._label_width = label_width
        self.values: dict[str, QLabel] = {}

    def set_rows(self, rows: list[tuple[str, object]], big: tuple[str, ...] = ()) -> None:
        while self._grid.count():
            w = self._grid.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.values = {}
        for i, (key, value) in enumerate(rows):
            k = QLabel(key)
            k.setProperty("role", "muted")
            k.setFont(fonts.body())
            k.setFixedWidth(self._label_width)
            k.setMinimumHeight(LINE_H)
            k.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            v = QLabel()
            v.setWordWrap(True)
            v.setMinimumHeight(LINE_H)
            v.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            if isinstance(value, tuple) and value and value[0] == "html":
                v.setTextFormat(Qt.TextFormat.RichText)
                v.setText(value[1])
            else:
                v.setTextFormat(Qt.TextFormat.PlainText)
                v.setText(str(value))
            if key in big:
                v.setProperty("role", "strong")
                v.setFont(fonts.font(t.FONT_BASE, 600, display=True, tracking=t.TRACK_HEADING))
            else:
                v.setProperty("role", "soft")
                v.setFont(fonts.body())
            self._grid.addWidget(k, i, 0)
            self._grid.addWidget(v, i, 1)
            self.values[key] = v
        self._grid.setRowStretch(len(rows), 1)

    def text(self, key: str) -> str:
        w = self.values.get(key)
        return w.text() if w else ""

