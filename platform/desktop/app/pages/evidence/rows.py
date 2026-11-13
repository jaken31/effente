"""The stacked rows of the two Evidence lists: readings that moved, fault type evidence."""

from __future__ import annotations

from app.data import logic
from app.theme import fonts
from app.theme import tokens as T
from app.theme.qss import css
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .bars import CentredBar, FillBar
from .view import MOVED_BAR_FULL, FaultEvidence, MovedRow

ROLE = {T.TEXT: "strong", T.SOFT: "soft", T.MUTED: "muted"}


def label(text: str, color: str, *, mono: bool = False, wrap: bool = False) -> QLabel:
    """A plain-text label in one of the three text colours, styled by the theme's roles."""
    w = QLabel(text)
    w.setTextFormat(Qt.TextFormat.PlainText)
    w.setWordWrap(wrap)
    w.setProperty("role", "code" if mono else ROLE[color])
    w.setFont(fonts.mono() if mono else fonts.body())
    return w


class _Row(QFrame):
    def __init__(self, last: bool):
        super().__init__()
        self.setObjectName("evRow")
        line = "none" if last else f"1px solid {css(T.GRID)}"
        self.setStyleSheet(f"#evRow {{ border: none; border-bottom: {line}; background: transparent; }}")
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(16, 10, 16, 10)
        self.box.setSpacing(6)

    def top(self, left: list[QLabel], right: QLabel) -> None:
        h = QHBoxLayout()
        h.setSpacing(6)
        for w in left:
            h.addWidget(w)
        h.addStretch(1)
        h.addWidget(right)
        self.box.addLayout(h)


class RowList(QScrollArea):
    """A scrolling list of rows, or a muted line when there are none."""

    def __init__(self):
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self.empty_text = ""

    def _replace(self, rows: list[QWidget], empty: str) -> None:
        inner = QWidget()
        inner.setStyleSheet("background: transparent;")
        v = QVBoxLayout(inner)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.empty_text = "" if rows else empty
        if not rows:
            e = label(empty, T.MUTED, wrap=True)
            e.setObjectName("empty")
            e.setContentsMargins(16, 18, 16, 18)
            v.addWidget(e)
        for r in rows:
            v.addWidget(r)
        v.addStretch(1)
        self.setWidget(inner)


class MovedList(RowList):
    def set_rows(self, rows: list[MovedRow], empty: str) -> None:
        out = []
        for i, r in enumerate(rows):
            w = _Row(i == len(rows) - 1)
            names = [label(r.text or r.name, T.TEXT)]
            if r.text:
                names.append(label(r.name, T.MUTED, mono=True))
            w.top(names, label(r.value_text, T.SOFT))
            w.box.addWidget(CentredBar(r.fraction, logic.EVIDENCE_SPREADS / MOVED_BAR_FULL, r.evidence))
            w.evidence = r.evidence
            w.feature = r.name
            out.append(w)
        self._replace(out, empty)


class FaultEvidenceList(RowList):
    def set_rows(self, rows: list[FaultEvidence], empty: str) -> None:
        out = []
        for i, r in enumerate(rows):
            w = _Row(i == len(rows) - 1)
            w.top([label(r.label, T.TEXT), label(r.group, T.MUTED), label(r.status, T.MUTED)], label(f"{r.score:.1f}", T.SOFT))
            w.box.addWidget(FillBar(r.fraction, r.named))
            if r.text:
                w.box.addWidget(label(r.text, T.MUTED, wrap=True))
            w.fault_type = r.name
            out.append(w)
        self._replace(out, empty)
