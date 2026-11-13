"""Evidence: why the device thinks what it thinks.

Readings that moved, fault type evidence (only while abnormal), and every fault
type the model can name. Shows only what the record and the model hold.
"""

from __future__ import annotations

from app.widgets import Panel
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QGridLayout, QWidget

from . import view
from .rows import FaultEvidenceList, MovedList
from .table_model import Column, RowTable

MOVED_HINT = "signed spreads from this machine's normal · 6 or more counts as evidence"


class EvidencePage(QWidget):
    key = "evidence"
    title = "Evidence"
    subtitle = "why the device thinks what it thinks"
    navigate = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.state = None
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(16)

        self.moved_panel = Panel("Readings that moved", MOVED_HINT)
        self.moved = self.moved_panel.add(MovedList(), 1)

        self.ft_panel = Panel("Fault type evidence")
        self.ft = self.ft_panel.add(FaultEvidenceList(), 1)

        self.types_panel = Panel("Fault types")
        self.types = self.types_panel.add(RowTable([
            Column("Group", width=94),
            Column("Type", strong=True, width=413),
            Column("What it means", wrap=True),
            Column("Status", tag=True, width=180),
            Column("Evidence now", right=True, width=124),
        ]), 1)

        grid.addWidget(self.moved_panel, 0, 0)
        grid.addWidget(self.ft_panel, 0, 1)
        grid.addWidget(self.types_panel, 1, 0, 1, 2)
        grid.setRowStretch(0, 1)
        grid.setRowStretch(1, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

    def bind(self, state) -> None:
        self.state = state
        state.snapshot_changed.connect(self.refresh)
        state.record_added.connect(self.refresh)
        self.refresh()

    def refresh(self, *_args) -> None:
        if self.state is None:
            return
        rec = self.state.latest()
        model = self.state.model()

        self.moved.set_rows(view.moved_rows(rec, model), "No readings yet." if rec is None else "No readings in this record.")

        self.ft_panel.set_hint(view.fault_evidence_hint(rec))
        self.ft.set_rows(view.fault_evidence(rec, model), view.fault_evidence_empty(rec))

        self.types_panel.set_hint(view.fault_types_hint(model))
        rows = view.fault_type_rows(rec, model)
        self.types.set_rows(
            [[r.group, (r.label, r.name), r.text, r.status, "–" if r.evidence is None else f"{r.evidence:.1f}"] for r in rows],
            keys=[r.name for r in rows],
            tint=[r.evidence is not None for r in rows],
        )
