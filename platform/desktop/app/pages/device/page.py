"""Device & model: devices, the model the device announced, and an event log."""

from __future__ import annotations

from app.pages.agents.kv import KeyValues, pin_title_left
from app.pages.agents.levels import Levels
from app.pages.evidence.table_model import Column, RowTable
from app.theme import fonts
from app.theme import tokens as t
from app.widgets import Panel, Toggle
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QGridLayout, QLabel, QVBoxLayout, QWidget

from . import view

PROT_KEYS = ("Fit check", "Calibrated")


class DevicePage(QWidget):
    key = "device"
    title = "Device & model"
    subtitle = "mode, library model, calibration and events"
    navigate = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.state = None
        self.seen: list[view.Event] = []  # mode changes and model announcements seen live
        self._modes: dict[str, str | None] = {}
        self._versions: dict[str, str | None] = {}
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(16)

        self.devices_panel = Panel("Devices")
        pin_title_left(self.devices_panel)
        self.devices = RowTable([
            Column("Device", strong=True),
            Column("Status", pill=True),
            Column("Mode"),
            Column("Last seen"),
            Column("Machine"),
            Column("Model version", mono=True),
        ])
        self.devices_panel.add(self.devices, 1)

        self.model_panel = Panel("Model", padded=True)
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)
        self.prot_head = QLabel()
        self.prot_head.setProperty("role", "strong")
        self.prot_head.setFont(fonts.font(t.FONT_BASE, 600))
        self.prot_head.setWordWrap(True)
        self.levels = Levels()
        self.prot_kv = KeyValues()
        self.model_kv = KeyValues()
        v.addWidget(self.prot_head)
        v.addWidget(self.levels)
        v.addWidget(self.prot_kv)
        v.addSpacing(4)
        v.addWidget(self.model_kv)
        v.addStretch(1)
        self.model_panel.add(box, 1)

        self.log_panel = Panel("Event log")
        pin_title_left(self.log_panel)
        pause = QLabel("Pause")
        pause.setProperty("role", "muted")
        pause.setFont(fonts.body())
        self.pause = Toggle()
        self.pause.toggled.connect(self._paused)
        self.log_panel.header_end.addWidget(pause)
        self.log_panel.header_end.addWidget(self.pause)
        self.log = RowTable([
            Column("Time", width=84),
            Column("Event", strong=True, width=132),
            Column("Detail"),
        ])
        self.log_panel.add(self.log, 1)

        grid.addWidget(self.devices_panel, 0, 0, 1, 2)
        grid.addWidget(self.model_panel, 1, 0)
        grid.addWidget(self.log_panel, 1, 1)
        grid.setRowStretch(1, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

    def bind(self, state) -> None:
        self.state = state
        self._watch_devices(initial=True)
        state.snapshot_changed.connect(self._snapshot)
        state.record_added.connect(self.refresh)
        state.alarms_changed.connect(self.refresh_log)
        state.runs_changed.connect(self.refresh_log)
        self.refresh()

    def _snapshot(self) -> None:
        self._watch_devices()
        self.refresh()

    def _watch_devices(self, initial: bool = False) -> None:
        """Note mode changes and model announcements as they arrive. Never made up:
        only a change seen between two snapshots becomes an event."""
        models = self.state.view().get("models", {}) if hasattr(self.state, "view") else {}
        for did, d in (self.state.devices() or {}).items():
            mode, version = d.get("mode"), d.get("model_version")
            at = d.get("last_seen") or ""
            if not initial and did in self._modes and mode != self._modes[did] and at:
                self.seen.append(view.mode_event(at, self._modes[did], mode))
            if not initial and did in self._versions and version and version != self._versions[did] and at:
                self.seen.append(view.model_event(at, models.get(version) or {"model_version": version}))
            self._modes[did], self._versions[did] = mode, version

    def _paused(self, on: bool) -> None:
        if not on:
            self.refresh_log()

    def refresh(self, *_args) -> None:
        if self.state is None:
            return
        rows = view.device_rows(self.state.devices())
        self.devices.set_rows([
            [r.device_id, (r.status, t.DOT) if r.connected else r.status, r.mode, r.last_seen, r.machine, r.model_version]
            for r in rows
        ], keys=[r.device_id for r in rows])
        h = self.devices.horizontalHeader().height() + sum(self.devices.rowHeight(i) for i in range(len(rows)))
        self.devices.setFixedHeight(max(h, t.ROW_H) + 2)

        model = self.state.model()
        d = (self.state.devices() or {}).get(self.state.device_id()) or {}
        source = (model or {}).get("source")
        self.model_panel.set_hint({"calibrated": "from the library, calibrated to this machine",
                                   "trained": "trained on this machine"}.get(source, ""))
        head, levels = view.protection(d.get("mode"), model)
        self.prot_head.setText(head)
        self.levels.set_steps([(lv.text, lv.on, lv.planned) for lv in levels])
        fields = view.model_fields(model, self.state.latest())
        self.prot_kv.set_rows([f for f in fields if f[0] in PROT_KEYS])
        self.model_kv.set_rows([f for f in fields if f[0] not in PROT_KEYS])
        self.refresh_log()

    def refresh_log(self, *_args) -> None:
        if self.state is None or self.pause.isChecked():
            return
        evs = view.events(self.state.alarms(), self.state.runs(), self.seen)[:200]
        self.log.set_rows([[e.time, e.kind, e.detail] for e in evs])

    def field(self, key: str) -> str:
        for kv in (self.prot_kv, self.model_kv):
            if key in kv.values:
                return kv.text(key)
        return ""
