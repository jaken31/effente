"""FakeLog: the `state.log` interface (INTERFACES.md addition 19) on fixed data, for page tests.

    state = FakeState()
    state.log.parts()            # four parts of the fake state's machine

The sample of INTERFACES.md addition 20, written here by hand, not worked out
by any real code. It sits on the fake state's own machine and alarms (see
fakes.py), so the alarm links hold:

- "Drive roller" (roller): `needs_repair`. The answered alarm-5 was turned into a finding.
- "Motor mount" (mount): `possible`. The open, unanswered alarm-9 points at it.
- "Belt" (belt): `nothing_found`. Replaced and inspected before the probe's records.
- "Idle roller" (roller): `not_checked`, no pin. Made after the newest record.

Six entries cover every kind. One finished part review, for "Drive roller",
labelled as a stand-in and as simulated.

Actions change only this object and emit the same signals as the real one. They
do not work a part's state out again: that is the receiver's job (contract
section 8.7). Use `set_part_state` to show another state.
"""

from __future__ import annotations

import csv
import datetime as dt

from app.data.partlog import PartLog
from fake_scans import SCAN_ID

DRIVE_ROLLER, MOTOR_MOUNT, BELT, IDLE_ROLLER = "part-0001", "part-0002", "part-0003", "part-0004"
ANSWERED_ALARM = "alarm-5"  # as in fakes.py
OPEN_ALARM = "alarm-9"
REVIEW_AGENT = "part review agent"
CSV_COLUMNS = ("at", "part", "kind", "text", "by", "needs_repair", "alarm_id", "diagnosis", "source", "entry_id")
PART_FIELDS = ("name", "kind", "position", "fault_types", "note", "removed")


def _iso(ms: float) -> str:
    return dt.datetime.fromtimestamp(ms / 1000.0, dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def sample_parts(machine_id: str, by: str) -> list:
    def part(part_id, name, kind, fault_types, at, made, state):
        return {
            "part_id": part_id, "machine_id": machine_id, "name": name, "kind": kind, "fault_types": fault_types,
            "position": {"scan_id": SCAN_ID, "at": at} if at else None, "note": "",
            "created": {"at": made, "by": by}, "removed": False, "state": state,
        }

    return [
        part(DRIVE_ROLLER, "Drive roller", "roller", ["unbalance_like"], [0.18, 0.06, 0.0], "2026-11-14T15:50:30Z",
             {"name": "needs_repair", "since": "2026-11-14T16:05:00Z", "reason": "finding: needs repair",
              "alarm_id": ANSWERED_ALARM, "entry_id": "entry-000004"}),
        part(MOTOR_MOUNT, "Motor mount", "mount", ["looseness_or_rattle", "unrecognised_pattern"],
             [-0.15, 0.03, 0.05], "2026-11-14T15:51:00Z",
             {"name": "possible", "since": "2026-11-14T16:00:08.000Z", "reason": "alarm open: unrecognised_pattern",
              "alarm_id": OPEN_ALARM, "entry_id": None}),
        part(BELT, "Belt", "belt", ["unsteady_vibration_or_sound"], [0.0, 0.14, 0.0], "2026-11-14T15:51:30Z",
             {"name": "nothing_found", "since": "2026-11-14T16:00:00.000Z",
              "reason": "the probe has been on the machine and nothing points at this part",
              "alarm_id": None, "entry_id": None}),
        part(IDLE_ROLLER, "Idle roller", "roller", ["unbalance_like"], None, "2026-11-14T16:10:00Z",
             {"name": "not_checked", "since": None, "reason": "the probe has not been on the machine since",
              "alarm_id": None, "entry_id": None}),
    ]


def sample_entries(machine_id: str, by: str) -> list:
    def entry(n, part_id, at, kind, text, who=by, needs_repair=None, alarm_id=None, diagnosis=None,
              source="person", run_id=None, simulated=False):
        return {
            "entry_id": f"entry-{n:06d}", "machine_id": machine_id, "part_id": part_id, "at": at, "received_at": at,
            "kind": kind, "text": text, "by": who, "needs_repair": needs_repair, "alarm_id": alarm_id,
            "diagnosis": diagnosis, "source": source, "run_id": run_id, "simulated": simulated,
        }

    return [
        entry(1, None, "2026-11-14T15:45:00Z", "note", "machine arrived and was scanned"),
        entry(2, BELT, "2026-11-14T15:55:00Z", "repair", "belt replaced"),
        entry(3, BELT, "2026-11-14T15:58:00Z", "inspection", "tension checked after the new belt", needs_repair=False),
        entry(4, DRIVE_ROLLER, "2026-11-14T16:05:00Z", "finding", "added weight found on the drive roller",
              needs_repair=True, alarm_id=ANSWERED_ALARM, diagnosis="unbalance_like", source="alarm_answer",
              simulated=True),
        entry(5, DRIVE_ROLLER, "2026-11-14T16:06:00Z", "review", sample_review_text(by), who=REVIEW_AGENT,
              source="agent", run_id=REVIEW_RUN_ID, simulated=True),
        entry(6, MOTOR_MOUNT, "2026-11-14T16:07:00Z", "note", "to be looked at when the line next stops"),
    ]


REVIEW_RUN_ID = f"review-20261114-160600-{DRIVE_ROLLER}"


def sample_review_text(by: str) -> str:
    return f"Stand-in review: possible unbalance on the drive roller, answered as a real problem by {by}."


def sample_review(machine_id: str, by: str, how: str) -> dict:
    """A finished part review run for "Drive roller" (contract section 8.8). `how` is the
    fault type's own text from the model announcement, copied, never written here."""
    return {
        "run_id": REVIEW_RUN_ID, "agent": "part_review", "machine_id": machine_id, "part_id": DRIVE_ROLLER,
        "at": "2026-11-14T16:06:00Z", "status": "done", "model": "stand-in (no model call)", "ok": True,
        "reason": "", "problems": [],
        "tool_calls": [{"at": "2026-11-14T16:06:00Z", "name": "get_part_history", "input": {"part_id": DRIVE_ROLLER},
                        "output": "{\"entries\": 2, \"alarms\": 1}"}],
        "report": None,
        "review": {
            "what": sample_review_text(by), "how": how, "since": "2026-11-14T16:05:00Z", "needs_repair": "yes",
            "why": "One alarm, answered as a real problem, and no repair entry since.",
            "cited_alarms": [ANSWERED_ALARM], "cited_entries": ["entry-000004"],
        },
        "simulated": True,
    }


class FakeLog(PartLog):
    def __init__(self, state):
        super().__init__(state)
        self.machine = None  # the machine the sample belongs to; set by fill()
        self._parts: list = []  # in the order they were made
        self._entries: list = []  # in the order they were added
        self._reviews: list = []  # part review runs, oldest first
        self._running: set = set()
        self._selected = None
        self.started_reviews: list = []  # (part_id, mode) of each start_review call
        self.exported: list = []  # paths export_csv wrote

    def fill(self) -> None:
        """Load the sample for the fake state's current machine."""
        self.machine = self.state.current_machine()
        by = self.state.answerer()
        types = (self.state.model() or {}).get("fault_types") or []
        how = next((f.get("text", "") for f in types if f.get("name") == "unbalance_like"), "")
        self._parts = sample_parts(self.machine, by)
        self._entries = sample_entries(self.machine, by)
        self._reviews = [sample_review(self.machine, by, how)]

    def _here(self) -> bool:
        return self.state.current_machine() == self.machine

    def _now(self) -> str:
        return _iso(self.state.now_ms())

    def _find(self, part_id):
        return next((p for p in self._parts if p["part_id"] == part_id), None)

    # ---- reads ------------------------------------------------------------

    def parts(self) -> list:
        return [p for p in self._parts if not p["removed"]] if self._here() else []

    def part(self, part_id):
        return self._find(part_id) if self._here() else None

    def entries(self, part_id=None, kind=None) -> list:
        if not self._here():
            return []
        chosen = [e for e in self._entries
                  if (part_id is None or e["part_id"] == part_id) and (kind is None or e["kind"] == kind)]
        return sorted(chosen, key=lambda e: e["at"], reverse=True)

    def suspects(self, alarm) -> list:
        diagnosis = (alarm or {}).get("diagnosis")
        return [p for p in self.parts() if diagnosis in p["fault_types"]]

    def waiting_alarms(self) -> list:
        named = {e["alarm_id"] for e in self._entries if e["source"] == "alarm_answer"}
        return [a for a in self.state.alarms()
                if a["alarm_id"] not in named and not any(x.get("part_id") for x in a.get("answers") or [])]

    def selected_part(self):
        return self._selected

    def review(self, part_id):
        runs = [r for r in self._reviews if r["part_id"] == part_id]
        return runs[-1] if runs and self._here() else None

    def review_running(self, part_id) -> bool:
        return part_id in self._running

    # ---- actions ----------------------------------------------------------

    def select_part(self, part_id) -> None:
        if part_id != self._selected:
            self._selected = part_id
            self.selection_changed.emit()

    def create_part(self, name, kind, position=None) -> None:
        part_id = f"part-{len(self._parts) + 1:04d}"
        self._parts.append({
            "part_id": part_id, "machine_id": self.machine, "name": name, "kind": kind, "fault_types": [],
            "position": position, "note": "", "created": {"at": self._now(), "by": self.state.answerer()},
            "removed": False,
            "state": {"name": "not_checked", "since": None, "reason": "", "alarm_id": None, "entry_id": None},
        })
        self.parts_changed.emit()

    def update_part(self, part_id, **fields) -> None:
        part = self._find(part_id)
        unknown = [k for k in fields if k not in PART_FIELDS]
        if part is None or unknown:
            self.state.action_failed.emit(f"Part not changed: no such part: {part_id}" if part is None
                                          else f"Part not changed: unknown field: {unknown[0]}")
            return
        part.update(fields)
        self.parts_changed.emit()

    def add_entry(self, kind, text, part_id=None, needs_repair=None, alarm_id=None, source="person") -> None:
        alarm = next((a for a in self.state.alarms() if a["alarm_id"] == alarm_id), None)
        self._entries.append({
            "entry_id": f"entry-{len(self._entries) + 1:06d}", "machine_id": self.machine, "part_id": part_id,
            "at": self._now(), "received_at": self._now(), "kind": kind, "text": text or "",
            "by": self.state.answerer(), "needs_repair": needs_repair, "alarm_id": alarm_id,
            "diagnosis": alarm["diagnosis"] if alarm else None, "source": source, "run_id": None,
            "simulated": bool(alarm and alarm.get("simulated")),
        })
        self.entries_changed.emit()

    def answer_alarm_for_part(self, alarm_id, verdict, part_id, finding=None, needs_repair=None) -> None:
        if not self.state.answer_alarm(alarm_id, verdict, finding):
            self.state.action_failed.emit(f"Answer not saved: no such alarm: {alarm_id}")
            return
        needs = False if verdict == "false" else (True if needs_repair is None else bool(needs_repair))
        self.add_entry("finding", finding, part_id, needs, alarm_id, source="alarm_answer")

    def start_review(self, part_id=None, mode="standin") -> None:
        """No agent runs: the call is remembered and the parts show as running until finish_review."""
        self.started_reviews.append((part_id, mode))
        self._running |= {part_id} if part_id else {p["part_id"] for p in self.parts()}
        self.reviews_changed.emit()

    def export_csv(self, path) -> None:
        names = {p["part_id"]: p["name"] for p in self._parts}
        with open(path, "w", newline="", encoding="utf-8-sig") as f:  # the mark makes a spreadsheet read accents
            out = csv.writer(f)
            out.writerow(CSV_COLUMNS)
            for e in self.entries():
                row = dict(e, part=names.get(e["part_id"], ""))
                out.writerow(["" if row[c] is None else row[c] for c in CSV_COLUMNS])
        self.exported.append(str(path))

    # ---- test helpers -----------------------------------------------------

    def finish_review(self, part_id=None) -> None:
        """End the pretend review of one part, or of all."""
        self._running -= {part_id} if part_id else set(self._running)
        self.reviews_changed.emit()

    def set_part_state(self, part_id, name, since=None, reason="", alarm_id=None, entry_id=None) -> None:
        """Show a part in another state, as the receiver would report it."""
        self._find(part_id)["state"] = {"name": name, "since": since, "reason": reason, "alarm_id": alarm_id,
                                        "entry_id": entry_id}
        self.parts_changed.emit()

    def clear(self) -> None:
        """No parts, entries or reviews yet."""
        self._parts, self._entries, self._reviews, self._selected = [], [], [], None
        self.parts_changed.emit()
        self.entries_changed.emit()
        self.reviews_changed.emit()
