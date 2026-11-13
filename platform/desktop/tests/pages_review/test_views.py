"""Evidence, Agents and Device & model: what each page picks from the record."""

from __future__ import annotations

from typing import ClassVar

import pytest

pytest.importorskip("PySide6")

from app.pages.agents import view as agents
from app.pages.device import view as device
from app.pages.evidence import view as evidence

MODEL = {
    "model_version": "m-1",
    "library_entry": "conveyor",
    "source": "calibrated",
    "sensitivity": 0.5,
    "features_version": "abc",
    "features": [{"name": "vib_1x", "text": "vibration once per turn"}, {"name": "vib_2x", "text": "vibration twice per turn"}],
    "fault_types": [
        {"name": "unbalance_like", "text": "once-per-turn vibration grown", "group": "vibration", "validated": False},
        {"name": "misalignment_like", "text": "twice-per-turn vibration grown", "group": "vibration"},
        {"name": "device_moved", "text": "the device has been moved", "group": "sensing", "validated": True},
    ],
}


def record(diagnosis="unbalance_like", features=None, fault_types=None):
    r = {"diagnosis": diagnosis, "features": features if features is not None else {"vib_1x": 6.0, "vib_2x": -5.99}}
    if fault_types is not None:
        r["fault_types"] = fault_types
    return r


class TestMovedReadings:
    def test_six_spreads_counts_as_evidence_either_way(self):
        assert evidence.is_evidence(6.0)
        assert evidence.is_evidence(-6.0)
        assert not evidence.is_evidence(5.99)
        assert not evidence.is_evidence(-5.99)

    def test_rows_flag_only_readings_at_or_past_six(self):
        rows = evidence.moved_rows(record(features={"vib_2x": -5.99, "vib_1x": 6.0}), MODEL)
        assert [(r.name, r.evidence) for r in rows] == [("vib_1x", True), ("vib_2x", False)]
        assert rows[0].value_text == "+6.0 spreads"
        assert rows[1].value_text == "-6.0 spreads"  # rounded text, still not evidence

    def test_only_readings_in_the_record(self):
        rows = evidence.moved_rows(record(features={"vib_9x": 1.0}), MODEL)
        assert [(r.name, r.text) for r in rows] == [("vib_9x", "")]
        assert evidence.moved_rows(None, MODEL) == []
        assert evidence.moved_rows({"features": {"vib_1x": "x"}}, MODEL) == []

    def test_bar_is_signed_and_capped(self):
        rows = evidence.moved_rows(record(features={"a": -25.0, "b": 5.0}), None)
        assert [r.fraction for r in rows] == [-1.0, 0.5]


class TestFaultTypes:
    def test_every_type_possible_unless_validated(self):
        rows = evidence.fault_type_rows(record(), MODEL)
        assert [r.status for r in rows] == ["possible · not validated", "possible · not validated", "validated"]

    def test_evidence_now_only_while_abnormal(self):
        rows = evidence.fault_type_rows(record(fault_types={"unbalance_like": 9.0}), MODEL)
        assert [r.evidence for r in rows] == [9.0, None, None]
        rows = evidence.fault_type_rows(record("normal", fault_types={"unbalance_like": 9.0}), MODEL)
        assert [r.evidence for r in rows] == [None, None, None]

    def test_fault_evidence_hidden_while_not_abnormal(self):
        for diagnosis in ("normal", "stopped", "warming_up"):
            assert evidence.fault_evidence(record(diagnosis, fault_types={"unbalance_like": 3.0}), MODEL) == []

    def test_fault_evidence_strongest_first(self):
        rows = evidence.fault_evidence(record(fault_types={"misalignment_like": 1.2, "unbalance_like": 9.0}), MODEL)
        assert [r.name for r in rows] == ["unbalance_like", "misalignment_like"]
        assert rows[0].named and not rows[1].named
        assert all(r.status == "possible · not validated" for r in rows)

    def test_hint_counts_validated(self):
        assert "1 validated" in evidence.fault_types_hint(MODEL)
        none = {"fault_types": [{"name": "a"}]}
        assert "none validated" in evidence.fault_types_hint(none)


class TestAgents:
    RUN: ClassVar[dict] = {
        "run_id": "run-1", "agent": "maintenance_report", "machine_id": "m", "at": "2026-11-14T16:03:00Z",
        "status": "done", "ok": True, "model": "stand-in (no Claude call)", "simulated": True, "usage": {},
        "tool_calls": [{"at": "2026-11-14T16:03:01Z", "name": "get_readings", "input": {"since": ""}, "output": "{}"}],
        "report": {"summary": "Stand-in report: x", "priority": "medium", "cited_features": ["vib_1x"],
                   "recommended_action": "Check it.", "part_reference": ""},
    }

    def test_report_fields(self):
        fields = dict(agents.report_fields(self.RUN, {"vib_1x": "vibration once per turn"}))
        assert fields["Summary"] == "Stand-in report: x"
        assert fields["Priority"] == "Medium"
        assert fields["Evidence"] == [("vibration once per turn", "vib_1x")]
        assert fields["Part"] == "none found"
        assert fields["Checks"] == "all passed"

    def test_no_report_shows_reason(self):
        run = dict(self.RUN, ok=False, report=None, reason="agent unavailable: ANTHROPIC_API_KEY is not set")
        fields = dict(agents.report_fields(run, {}))
        assert fields["Status"] == "no report"
        assert fields["Reason"] == "agent unavailable: ANTHROPIC_API_KEY is not set"

    def test_io_pane(self):
        assert agents.call_io(self.RUN, None) == agents.IO_EMPTY
        text = agents.call_io(self.RUN, 0)
        assert text.startswith("get_readings\n\ninput\n{\n  \"since\": \"\"\n}")
        assert text.endswith("output\n{}")

    def test_run_rows(self):
        [row] = agents.run_rows([self.RUN])
        assert row.status == "report ready"
        assert row.who == "maintenance_report · stand-in (no Claude call)"
        assert agents.run_status(dict(self.RUN, status="running")) == "running"


class TestDevice:
    def test_protection_steps(self):
        head, levels = device.protection("watching", MODEL)
        assert head.startswith("Calibrated.")
        assert [lv.on for lv in levels] == [False, True, False, False]
        assert levels[1].text == "Calibrated · library entry conveyor"
        _, levels = device.protection("calibrating", MODEL)
        assert [lv.on for lv in levels] == [True, False, False, False]

    def test_model_fields_only_what_the_model_says(self):
        fields = dict(device.model_fields(MODEL, {"sensitivity": 0.5, "alarm_line": 1.0}))
        assert fields["Fault types"] == "3 · 1 validated"
        assert fields["Features"] == "2 readings · abc"
        assert fields["Fit check"] == "not reported"
        assert fields["Calibration"] == "not reported"
        assert fields["Alarm line"].startswith("score 1.00 · sensitivity 0.50")

    def test_events_from_alarms_and_runs(self):
        alarm = {"start": "2026-11-14T16:00:00Z", "end": "2026-11-14T16:05:00Z", "open": False,
                 "diagnosis": "unbalance_like", "periods": 5,
                 "answers": [{"verdict": "false", "at": "2026-11-14T16:06:00Z"}]}
        evs = device.events([alarm], [TestAgents.RUN])
        assert [e.kind for e in evs] == ["answer", "back to normal", "agent", "alarm"]
        assert evs[1].detail == "after 5 periods"
        assert evs[2].detail == "report ready · priority medium"

    def test_device_rows(self):
        rows = device.device_rows({
            "b": {"connected": True, "last_seen": "2026-11-14T16:00:00Z", "mode": "watching", "machine_id": "m"},
            "a": {"connected": False, "last_seen": None},
        })
        assert [(r.device_id, r.status) for r in rows] == [("a", "never seen"), ("b", "connected")]
