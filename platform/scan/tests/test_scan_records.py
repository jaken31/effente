"""platform/scan/scan_records.py: receiving, keeping and answering for scans (plan 5.1, 5.3, 5.4, C1)."""

import io
import json
import os
import zipfile

import pytest

from scan_glb import box_glb
from scan_records import ScanError, ScanStore

NOW = 1794680102.0  # 2026-11-14T18:15:02Z


def usdz_bytes():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("scan.usda", "#usda 1.0\n")
    return buffer.getvalue()


class FakeConverter:
    """Stands in for scan_convert.Converter: remembers what it was asked, converts when told to."""

    def __init__(self):
        self.jobs = []
        self.store = None
        self.stopped = False

    def submit(self, scan_id, source, target):
        self.jobs.append((scan_id, source, target))
        return True

    def finish(self, ok=True, reason="Blender is not installed on this laptop"):
        scan_id, source, target = self.jobs.pop(0)
        self.store._on_convert(scan_id, "converting", {})
        if ok:
            with open(target, "wb") as f:
                f.write(box_glb())
            self.store._on_convert(scan_id, "ready", {"bytes": len(box_glb()),
                                                      "bounds": {"min": [-0.21, 0.0, -0.09], "max": [0.21, 0.14, 0.09]}})
        else:
            self.store._on_convert(scan_id, "failed", {"reason": reason})

    def stop(self):
        self.stopped = True


@pytest.fixture
def make_store(tmp_path):
    def make(**kw):
        events = []
        converter = FakeConverter()
        clock = [NOW]

        def now():
            clock[0] += 1
            return clock[0]

        store = ScanStore(tmp_path / "data", publish=lambda name, data: events.append((name, data)),
                          converter=converter, now=now, **kw)
        converter.store = store
        return store, converter, events

    return make


def send(store, body, machine="conveyor-07", length="auto", **query):
    query.setdefault("client_id", "test-0001")
    stream = io.BytesIO(body)
    return store.receive(machine, query, len(body) if length == "auto" else length, stream.read)


# ---- 5.1 ---------------------------------------------------------------------------------------------------


def test_hello(make_store):
    store, _, _ = make_store()
    assert store.hello() == {"ok": True, "service": "effente", "contract": "3.4", "now": "2026-11-14T18:15:03Z",
                             "max_scan_bytes": 268435456}


# ---- 5.3: good uploads ---------------------------------------------------------------------------------------


def test_a_good_usdz_is_stored_and_waits_for_conversion(make_store):
    store, converter, events = make_store()
    body = usdz_bytes()
    scan, duplicate = send(store, body, at="2026-11-14T18:14:40Z", name="scan.usdz", app="effente-scan 0.1")
    assert duplicate is False
    assert scan["scan_id"].startswith("scan-20261114181503-") and len(scan["scan_id"]) == len("scan-20261114181503-3fa9")
    assert scan == {
        "scan_id": scan["scan_id"], "machine_id": "conveyor-07", "client_id": "test-0001",
        "at": "2026-11-14T18:14:40Z", "received_at": "2026-11-14T18:15:03Z", "format": "usdz",
        "file": {"name": "scan.usdz", "bytes": len(body)}, "app": "effente-scan 0.1",
        "model": {"status": "waiting", "reason": ""},
    }
    with open(store.file_path(scan["scan_id"]), "rb") as f:
        assert f.read() == body
    assert [job[0] for job in converter.jobs] == [scan["scan_id"]]
    assert events == [("scan", scan)]
    assert os.listdir(store.incoming) == []


def test_conversion_moves_the_status_and_sends_an_event_each_time(make_store):
    store, converter, events = make_store()
    scan, _ = send(store, usdz_bytes())
    with pytest.raises(ScanError, match="the model is waiting"):
        store.model_path(scan["scan_id"])
    converter.finish(ok=True)
    assert [e[1]["model"]["status"] for e in events] == ["waiting", "converting", "ready"]
    ready = store.scan(scan["scan_id"])
    assert ready["model"] == {"status": "ready", "reason": "", "bytes": len(box_glb()),
                              "bounds": {"min": [-0.21, 0.0, -0.09], "max": [0.21, 0.14, 0.09]}}
    with open(store.model_path(scan["scan_id"]), "rb") as f:
        assert f.read() == box_glb()


def test_a_failed_conversion_keeps_the_scan_and_can_be_tried_again(make_store):
    store, converter, events = make_store()
    scan, _ = send(store, usdz_bytes())
    converter.finish(ok=False)
    failed = store.scan(scan["scan_id"])
    assert failed["model"] == {"status": "failed", "reason": "Blender is not installed on this laptop"}
    with pytest.raises(ScanError, match="the model is failed: Blender is not installed"):
        store.model_path(scan["scan_id"])
    assert os.path.isfile(store.file_path(scan["scan_id"]))
    assert store.convert_again(scan["scan_id"])["model"]["status"] == "waiting"
    converter.finish(ok=True)
    assert store.scan(scan["scan_id"])["model"]["status"] == "ready"
    assert [e[1]["model"]["status"] for e in events] == ["waiting", "converting", "failed", "waiting", "converting", "ready"]


def test_a_good_glb_is_ready_at_once(make_store):
    store, converter, events = make_store()
    scan, _ = send(store, box_glb(), name="model.glb")
    assert scan["format"] == "glb" and converter.jobs == []
    assert scan["model"] == {"status": "ready", "reason": "", "bytes": len(box_glb()),
                             "bounds": {"min": [-0.21, 0.0, -0.09], "max": [0.21, 0.14, 0.09]}}
    with open(store.model_path(scan["scan_id"]), "rb") as f:
        assert f.read() == box_glb()
    assert [e[1]["model"]["status"] for e in events] == ["waiting", "ready"]


def test_a_glb_that_cannot_be_read_is_kept_but_failed(make_store):
    store, _, _ = make_store()
    scan, _ = send(store, b"glTF" + b"\x00" * 40)
    assert scan["model"]["status"] == "failed" and scan["model"]["reason"].startswith("the GLB file cannot be read")


def test_missing_at_means_the_platforms_time_and_missing_name_gets_one(make_store):
    store, _, _ = make_store()
    scan, _ = send(store, usdz_bytes())
    assert scan["at"] == scan["received_at"] and scan["file"]["name"] == "scan.usdz" and scan["app"] == ""


def test_the_body_arrives_in_small_pieces(make_store):
    store, _, _ = make_store()
    body = box_glb()
    stream = io.BytesIO(body)
    scan, _ = store.receive("conveyor-07", {"client_id": "test-0001"}, len(body), lambda n: stream.read(min(n, 3)))
    with open(store.file_path(scan["scan_id"]), "rb") as f:
        assert f.read() == body


# ---- 5.3: bad uploads ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("body, length, query, machine, message", [
    (b"hello, I am a text file", "auto", {}, "conveyor-07", "not a scan file"),
    (b"", "auto", {}, "conveyor-07", "the scan file is empty"),
    (b"PK\x03\x04", 268435457, {}, "conveyor-07", "too large"),
    (b"PK\x03\x04", None, {}, "conveyor-07", "Content-Length is required"),
    (b"PK\x03\x04", "lots", {}, "conveyor-07", "Content-Length is not a number"),
    (b"PK\x03\x04", "auto", {}, "Conveyor 07", "machine id must be"),
    (b"PK\x03\x04", "auto", {}, "", "machine id must be"),
    (b"PK\x03\x04", "auto", {}, "x" * 41, "machine id must be"),
    (b"PK\x03\x04", "auto", {"client_id": ""}, "conveyor-07", "client_id is required"),
    (b"PK\x03\x04", "auto", {"client_id": "short"}, "conveyor-07", "client_id is required"),
    (b"PK\x03\x04", "auto", {"client_id": "has spaces in it"}, "conveyor-07", "client_id is required"),
    (b"PK\x03\x04", "auto", {"at": "yesterday"}, "conveyor-07", "at is not a time"),
    (b"PK\x03\x04 and then it stops", 4000, {}, "conveyor-07", "the upload was cut off after 22 of 4000 bytes"),
])
def test_bad_uploads_are_refused_and_nothing_is_kept(make_store, body, length, query, machine, message):
    store, converter, events = make_store()
    with pytest.raises(ScanError, match=message):
        send(store, body, machine=machine, length=length, **query)
    assert events == [] and converter.jobs == []
    assert store.state_part() == {"scans": {}}
    assert os.listdir(store.incoming) == []
    assert [n for n in os.listdir(store.folder) if n != "incoming"] == []
    assert not os.path.exists(store.log_path)


def test_a_body_over_a_small_limit_is_refused_before_it_is_read(make_store):
    store, _, _ = make_store(max_bytes=10)
    reads = []
    with pytest.raises(ScanError, match="too large: 11 bytes, and the limit is 10"):
        store.receive("conveyor-07", {"client_id": "test-0001"}, 11, lambda n: reads.append(n) or b"x")
    assert reads == []


def test_a_repeated_client_id_stores_nothing_and_returns_the_first_scan(make_store):
    store, converter, events = make_store()
    first, duplicate = send(store, usdz_bytes())
    assert duplicate is False
    reads = []
    again, duplicate = store.receive("conveyor-07", {"client_id": "test-0001"}, 5, lambda n: reads.append(n) or b"")
    assert duplicate is True and again == first and reads == []
    assert len(events) == 1 and len(converter.jobs) == 1
    assert len(store.scans("conveyor-07")) == 1
    converter.finish(ok=True)  # a retry after conversion returns the scan as it is now
    again, duplicate = send(store, usdz_bytes())
    assert duplicate is True and again["model"]["status"] == "ready"


# ---- 5.4: reading --------------------------------------------------------------------------------------------


def test_scans_are_listed_newest_first_per_machine(make_store):
    store, _, _ = make_store()
    a, _ = send(store, box_glb(), client_id="test-0001")
    b, _ = send(store, box_glb(), client_id="test-0002")
    c, _ = send(store, box_glb(), client_id="test-0003", machine="pump-01")
    assert [s["scan_id"] for s in store.scans("conveyor-07")] == [b["scan_id"], a["scan_id"]]
    assert [s["scan_id"] for s in store.scans("conveyor-07", limit="1")] == [b["scan_id"]]
    assert store.scans("nothing-here") == []
    assert store.state_part() == {"scans": {"conveyor-07": b, "pump-01": c}}
    assert store.machines() == ["conveyor-07", "pump-01"]
    for bad in ("0", "201", "many"):
        with pytest.raises(ScanError, match="limit must be"):
            store.scans("conveyor-07", limit=bad)


def test_an_unknown_scan(make_store):
    store, _, _ = make_store()
    for call in (store.scan, store.model_path, store.file_path, store.convert_again):
        with pytest.raises(ScanError, match="no such scan"):
            call("scan-20261114181502-0000")
    with pytest.raises(ScanError, match="no such scan"):
        store.scan(None)


# ---- a restart -----------------------------------------------------------------------------------------------


def test_scans_survive_a_restart(make_store):
    store, converter, _ = make_store()
    ready, _ = send(store, box_glb(), client_id="test-0001")
    waiting, _ = send(store, usdz_bytes(), client_id="test-0002")
    converting, _ = send(store, usdz_bytes(), client_id="test-0003")
    failed, _ = send(store, usdz_bytes(), client_id="test-0004")
    converter.jobs.insert(0, converter.jobs.pop())          # the failed one first
    converter.finish(ok=False)
    store._on_convert(converting["scan_id"], "converting", {})  # the receiver stops in the middle of this one
    store.close()
    assert converter.stopped

    again, converter2, events = make_store()
    assert again.scan(ready["scan_id"])["model"]["status"] == "ready"
    assert again.scan(failed["scan_id"])["model"]["status"] == "failed"
    assert again.scan(waiting["scan_id"])["model"]["status"] == "waiting"
    assert again.scan(converting["scan_id"])["model"]["status"] == "waiting"  # back to waiting
    assert sorted(job[0] for job in converter2.jobs) == sorted([waiting["scan_id"], converting["scan_id"]])
    assert [s["scan_id"] for s in again.scans("conveyor-07")] == \
        [failed["scan_id"], converting["scan_id"], waiting["scan_id"], ready["scan_id"]]
    _, duplicate = send(again, usdz_bytes(), client_id="test-0002")  # the same phone retries after the restart
    assert duplicate is True
    with open(again.model_path(ready["scan_id"]), "rb") as f:
        assert f.read() == box_glb()


def test_a_cut_off_line_and_a_scan_whose_file_is_gone_are_skipped(make_store):
    store, _, _ = make_store()
    kept, _ = send(store, box_glb(), client_id="test-0001")
    gone, _ = send(store, box_glb(), client_id="test-0002")
    os.remove(store.file_path(gone["scan_id"]))
    with open(store.log_path, "a", encoding="utf-8") as f:
        f.write('{"scan_id": "scan-2026111418')  # the power went here
    again, _, _ = make_store()
    assert [s["scan_id"] for s in again.scans("conveyor-07")] == [kept["scan_id"]]


def test_a_half_arrived_upload_is_cleared_at_start(make_store):
    store, _, _ = make_store()
    with open(os.path.join(store.incoming, "abc.part"), "wb") as f:
        f.write(b"PK\x03\x04 half a scan")
    again, _, _ = make_store()
    assert os.listdir(again.incoming) == []


def test_the_log_holds_one_whole_scan_per_line(make_store):
    store, converter, _ = make_store()
    scan, _ = send(store, usdz_bytes())
    converter.finish(ok=True)
    with open(store.log_path, encoding="utf-8") as f:
        lines = [json.loads(line) for line in f]
    assert [line["model"]["status"] for line in lines] == ["waiting", "converting", "ready"]
    assert all(line["scan_id"] == scan["scan_id"] for line in lines)
