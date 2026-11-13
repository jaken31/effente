"""A minimal DashboardState for the shell's own tests: the interface's signals and
reads, with fields the tests set directly. Pages use agent 2's tests/fakes.py."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QObject, Signal

SAMPLE = Path(__file__).resolve().parents[3] / "sim" / "sample_replay.jsonl"


def sample_records() -> list[dict]:
    return [json.loads(line) for line in SAMPLE.read_text(encoding="utf-8").splitlines() if line.strip()]


class StubState(QObject):
    snapshot_changed = Signal()
    record_added = Signal(dict)
    alarms_changed = Signal()
    runs_changed = Signal()
    problems_changed = Signal()
    settings_changed = Signal()
    connection_changed = Signal(bool)

    def __init__(self):
        super().__init__()
        self._records = sample_records()
        self.machine = self._records[-1]["machine_id"]
        self.unanswered: list[dict] = []
        self.running = False
        self.fresh: dict | None = {"stale": False, "age_s": 0.4, "last_at": self._records[-1]["at"]}
        self.machine_calls: list[str] = []

    # reads
    def machines(self):
        return [self.machine, "conveyor-08"] if self.machine != "conveyor-08" else ["demo-fan-1", "conveyor-08"]

    def current_machine(self):
        return self.machine

    def set_machine(self, machine_id):
        self.machine_calls.append(machine_id)
        self.machine = machine_id
        self.snapshot_changed.emit()

    def latest(self):
        return self._records[-1]

    def records(self, limit=300, spectrum=False, frames=False):
        return self._records[-limit:]

    def alarms(self):
        return list(self.unanswered)

    def unanswered_alarms(self):
        return list(self.unanswered)

    def runs(self):
        return []

    def agent_running(self):
        return self.running

    def problems(self):
        return []

    def devices(self):
        r = self._records[-1]
        return {r["device_id"]: {"connected": True, "last_seen": r["at"], "mode": "watching",
                                 "machine_id": r["machine_id"], "model_version": r["model_version"]}}

    def model(self):
        return {"model_version": "sim-3", "library_entry": "conveyor", "source": "calibrated", "simulated": True}

    def sensitivity(self):
        return {"reported": 0.5, "wanted": None}

    def freshness(self):
        return self.fresh
