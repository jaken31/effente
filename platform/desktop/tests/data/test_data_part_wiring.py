"""Step 0 of docs/scan-and-log-plan.md: `state.scans` and `state.log` exist with the
names of INTERFACES.md additions 18 and 19, as stubs on the live state and with
the sample of addition 20 on the fake one."""

import csv
import json
import struct
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from app.data.api import ApiClient, ApiError, quote
from app.data.partlog import PartLog
from app.data.scans import Scans
from fake_partlog import (
    BELT,
    CSV_COLUMNS,
    DRIVE_ROLLER,
    IDLE_ROLLER,
    MOTOR_MOUNT,
    FakeLog,
)
from fake_scans import BOX_MAX, BOX_MIN, FakeScans, box_glb
from fakes import ANSWERED_ALARM, OPEN_ALARM, FakeState

SCANS = {"signals": ("scans_changed", "model_ready"),
         "calls": ("current", "all", "model_path", "qr_text", "import_file")}
LOG = {"signals": ("parts_changed", "entries_changed", "selection_changed", "reviews_changed"),
       "calls": ("parts", "part", "entries", "suspects", "waiting_alarms", "selected_part", "review",
                 "review_running", "select_part", "create_part", "update_part", "add_entry",
                 "answer_alarm_for_part", "start_review", "export_csv")}


@pytest.mark.parametrize("cls,names", [(Scans, SCANS), (FakeScans, SCANS), (PartLog, LOG), (FakeLog, LOG)])
def test_both_objects_have_the_agreed_names(cls, names):
    for name in names["signals"] + names["calls"] + ("apply_snapshot", "apply_event"):
        assert hasattr(cls, name), name


def test_the_live_state_has_stubs_that_get_every_snapshot_and_event(make_host, make_state, qtbot):
    host = make_host()
    state = make_state(host, wait=False)
    seen = []
    state.scans.apply_snapshot = lambda snap: seen.append(("scans", "snapshot"))
    state.log.apply_snapshot = lambda snap: seen.append(("log", "snapshot"))
    state.scans.apply_event = lambda name, data: seen.append(("scans", name))
    state.log.apply_event = lambda name, data: seen.append(("log", name))
    qtbot.waitUntil(lambda: ("log", "snapshot") in seen, timeout=5000)
    ApiClient(host.url).put("/api/devices/effente-01/settings", {"sensitivity": 0.7})
    qtbot.waitUntil(lambda: ("log", "settings") in seen, timeout=5000)
    assert ("scans", "snapshot") in seen and ("scans", "settings") in seen


def test_the_stubs_read_empty_and_say_not_built(make_host, make_state, qtbot):
    state = make_state(make_host())
    assert type(state.scans) is Scans and type(state.log) is PartLog
    assert state.scans.current() is None and state.scans.all() == [] and state.scans.model_path("scan-1") is None
    assert state.log.parts() == [] and state.log.entries() == [] and state.log.selected_part() is None
    assert state.log.review("part-0001") is None and state.log.review_running("part-0001") is False
    with qtbot.waitSignal(state.action_failed) as said:
        state.log.create_part("Drive roller", "roller")  # state.log is built: test_data_partlog.py
    assert said.args == ["Part not added: no machine is shown."]
    with qtbot.waitSignal(state.action_failed) as said:
        state.scans.import_file("scan.usdz")
    assert said.args == ["Scans are not built yet."]


def test_the_general_api_calls(make_host, tmp_path, monkeypatch):
    host = make_host()
    api = ApiClient(host.url)
    assert api.get("/api/state")["ok"] is True
    assert api.put(f"/api/devices/{quote('effente 01')}/settings", {"sensitivity": 0.3})["settings"] == {"sensitivity": 0.3}
    assert api.get("/api/machines/conveyor-07/log", part="part-0007", kind=None, limit=5)["entries"] == []
    assert api.post("/api/machines/conveyor-07/parts", {"name": "Belt", "kind": "belt", "by": "Sam"})["part"]["name"] == "Belt"
    with pytest.raises(ApiError, match="no such part: part-0007"):
        api.put("/api/parts/part-0007", {"by": "Sam", "note": "x"})
    with pytest.raises(ApiError, match="not built yet"):
        api.get_bytes("/api/scans/scan-1/model.glb")

    # A file goes up as raw bytes and comes back the same, through the receiver's two file helpers.
    import scan_api

    kept = tmp_path / "kept.glb"
    seen = {}

    def post_scan(request, query, machine_id):
        seen.update(query=query, type=request.headers["Content-Type"])
        request.send_json(200, {"ok": True, "bytes": request.read_body_to_file(str(kept), 10**6)})

    monkeypatch.setattr(scan_api, "post_scan", post_scan)
    monkeypatch.setattr(scan_api, "get_scan_file", lambda request, query, scan_id: request.send_file(str(kept), "model/gltf-binary"))
    source = tmp_path / "box.glb"
    source.write_bytes(box_glb())
    reply = api.post("/api/machines/conveyor-07/scans", file=source, content_type="model/gltf-binary", client_id="test-0001")
    assert reply == {"ok": True, "bytes": len(box_glb())}
    assert seen == {"query": {"client_id": ["test-0001"]}, "type": "model/gltf-binary"}
    assert api.get_bytes("/api/scans/scan-1/file") == box_glb()
    assert api.get_bytes("/api/scans/scan-1/file", to=tmp_path / "back.glb") == len(box_glb())
    assert (tmp_path / "back.glb").read_bytes() == box_glb()


def test_the_fake_box_is_a_glb_file(qapp):
    raw = box_glb()
    magic, version, total = struct.unpack_from("<4sII", raw)
    assert (magic, version, total) == (b"glTF", 2, len(raw))
    size, kind = struct.unpack_from("<I4s", raw, 12)
    doc = json.loads(raw[20:20 + size])
    assert kind == b"JSON" and doc["accessors"][0]["min"] == list(BOX_MIN) and doc["accessors"][0]["max"] == list(BOX_MAX)
    assert raw[20 + size + 4:20 + size + 8] == b"BIN\x00"
    assert doc["buffers"][0]["byteLength"] == len(raw) - (20 + size + 8)


def test_the_fake_scan_sample(qapp):
    state = FakeState()
    scan = state.scans.current()
    assert state.scans.all() == [scan]
    assert scan["machine_id"] == state.current_machine() and scan["model"]["status"] == "ready"
    path = state.scans.model_path(scan["scan_id"])
    assert Path(path).read_bytes() == box_glb() and scan["model"]["bytes"] == len(box_glb())
    assert state.scans.qr_text("conveyor-07", "Conveyor 7") == (
        "effente://scan?v=1&base=http%3A%2F%2F192.168.43.10%3A8000&machine=conveyor-07&name=Conveyor%207")
    state.scans.set_status("failed", "Blender is not installed on this laptop")
    assert state.scans.current()["model"] == {"status": "failed", "reason": "Blender is not installed on this laptop"}
    assert state.scans.model_path(scan["scan_id"]) is None
    state.scans.clear()
    assert state.scans.current() is None


def test_the_fake_log_sample(qapp):
    state = FakeState()
    log = state.log
    parts = {p["name"]: p for p in log.parts()}
    assert [(n, p["kind"], p["state"]["name"]) for n, p in parts.items()] == [
        ("Drive roller", "roller", "needs_repair"), ("Motor mount", "mount", "possible"),
        ("Belt", "belt", "nothing_found"), ("Idle roller", "roller", "not_checked")]
    assert parts["Idle roller"]["position"] is None
    assert all(p["position"]["scan_id"] == state.scans.current()["scan_id"] for n, p in parts.items() if n != "Idle roller")
    entries = log.entries()
    assert len(entries) == 6 and [e["at"] for e in entries] == sorted((e["at"] for e in entries), reverse=True)
    assert {e["kind"] for e in entries} == {"finding", "inspection", "repair", "note", "review"}
    assert [e["alarm_id"] for e in entries if e["source"] == "alarm_answer"] == [ANSWERED_ALARM]
    assert [e["kind"] for e in log.entries(part_id=BELT)] == ["inspection", "repair"]
    assert [e["part_id"] for e in log.entries(kind="note")] == [MOTOR_MOUNT, None]
    alarms = {a["alarm_id"]: a for a in state.alarms()}
    assert [p["part_id"] for p in log.suspects(alarms[ANSWERED_ALARM])] == [DRIVE_ROLLER, IDLE_ROLLER]
    assert [a["alarm_id"] for a in log.waiting_alarms()] == [OPEN_ALARM]
    run = log.review(DRIVE_ROLLER)
    assert run["status"] == "done" and run["simulated"] is True and run["report"] is None
    assert run["review"]["cited_entries"] == ["entry-000004"] and log.review(BELT) is None
    assert run["review"]["how"] == next(f["text"] for f in state.model()["fault_types"] if f["name"] == "unbalance_like")
    state.set_machine("another-machine")
    assert log.parts() == [] and log.entries() == [] and state.scans.current() is None


def test_the_fake_log_actions(qapp, qtbot, tmp_path):
    state = FakeState()
    log = state.log
    with qtbot.waitSignal(log.selection_changed):
        log.select_part(BELT)
    assert log.selected_part() == BELT
    with qtbot.waitSignal(log.parts_changed):
        log.create_part("Gearbox", "gearbox", {"scan_id": "scan-1", "at": [0.0, 0.1, 0.0]})
    new = log.parts()[-1]
    assert (new["name"], new["state"]["name"], new["created"]["by"]) == ("Gearbox", "not_checked", state.answerer())
    log.update_part(new["part_id"], removed=True)
    assert len(log.parts()) == 4 and log.part(new["part_id"])["removed"] is True
    with qtbot.waitSignal(log.entries_changed):
        log.answer_alarm_for_part(OPEN_ALARM, "real", MOTOR_MOUNT, finding="two screws loose")
    newest = log.entries(part_id=MOTOR_MOUNT, kind="finding")[0]
    assert (newest["text"], newest["source"], newest["needs_repair"], newest["diagnosis"]) == (
        "two screws loose", "alarm_answer", True, "unrecognised_pattern")
    assert log.waiting_alarms() == [] and state.unanswered_alarms() == []
    log.start_review(DRIVE_ROLLER)
    assert log.review_running(DRIVE_ROLLER) and log.started_reviews == [(DRIVE_ROLLER, "standin")]
    log.finish_review(DRIVE_ROLLER)
    assert not log.review_running(DRIVE_ROLLER)
    log.set_part_state(BELT, "needs_repair", since="2026-11-14T16:20:00Z")
    assert log.part(BELT)["state"]["name"] == "needs_repair"
    out = tmp_path / "log.csv"
    log.export_csv(out)
    with out.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    assert tuple(rows[0]) == CSV_COLUMNS and len(rows) == 8 and {r[1] for r in rows[1:]} == {"", "Belt", "Drive roller", "Motor mount"}
