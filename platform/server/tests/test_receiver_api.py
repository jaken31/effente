"""End-to-end tests: a real receiver on a free port, talked to over HTTP."""

import copy
import json
import socket
import threading
import time

from receiver_testkit import MODEL, make_record, make_run


def announce(client, model=MODEL):
    status, body = client.json("POST", "/api/model", model)
    assert (status, body) == (200, {"ok": True})


# ---- message 2: records ---------------------------------------------------


def test_post_one_record_and_read_back_by_state(client):
    announce(client)
    status, reply = client.json("POST", "/api/records", make_record())
    assert status == 200
    assert reply == {"ok": True, "stored": 1, "mask": [], "mask_version": 0, "need_model": False}

    status, state = client.json("GET", "/api/state")
    assert status == 200 and state["ok"] is True
    assert state["machines"] == ["conveyor-07"]
    assert state["models"] == {"fake-0.1": MODEL}
    dev = state["devices"]["effente-01"]
    assert dev["connected"] is True
    assert dev["model_version"] == "fake-0.1"
    assert dev["mask"] == [] and dev["mask_version"] == 0
    latest = dev["latest"]
    assert latest["diagnosis"] == "imbalance"
    assert len(latest["spectrum"]) == 64
    assert isinstance(latest["seq"], int)
    assert latest["received_at"].endswith("Z")
    assert dev["last_seen"].endswith("Z")
    assert state["now"].endswith("Z")


def test_post_array_assigns_rising_seq_across_devices(client):
    batch = [make_record(at=f"2026-11-14T16:02:{10 + i}Z") for i in range(3)]
    status, reply = client.json("POST", "/api/records", batch)
    assert status == 200 and reply["stored"] == 3
    status, reply = client.json("POST", "/api/records", make_record(device_id="effente-02", machine_id="fan-02"))
    assert reply["stored"] == 1

    _, hist = client.json("GET", "/api/machines/conveyor-07/records")
    seqs = [r["seq"] for r in hist["records"]]
    assert seqs == sorted(seqs) and len(set(seqs)) == 3
    _, other = client.json("GET", "/api/machines/fan-02/records")
    assert other["records"][0]["seq"] > seqs[-1]
    _, state = client.json("GET", "/api/state")
    assert state["machines"] == ["conveyor-07", "fan-02"]


def test_empty_array_stores_nothing(client):
    status, reply = client.json("POST", "/api/records", [])
    assert status == 200 and reply["stored"] == 0


def test_platform_overrides_seq_and_received_at(client):
    client.json("POST", "/api/records", make_record(seq=999999, received_at="1999-01-01T00:00:00Z"))
    _, hist = client.json("GET", "/api/machines/conveyor-07/records")
    assert hist["records"][0]["seq"] == 1
    assert hist["records"][0]["received_at"] != "1999-01-01T00:00:00Z"


def test_unknown_fields_kept_and_simulated_defaults_false(client):
    record = make_record(extra_field={"x": 1})
    del record["simulated"]
    client.json("POST", "/api/records", record)
    _, hist = client.json("GET", "/api/machines/conveyor-07/records")
    assert hist["records"][0]["extra_field"] == {"x": 1}
    assert hist["records"][0]["simulated"] is False


def test_missing_required_field_rejected(client):
    for field in ("device_id", "machine_id", "model_version", "at", "diagnosis"):
        record = make_record()
        del record[field]
        status, body = client.json("POST", "/api/records", record)
        assert status == 400, field
        assert body["ok"] is False and field in body["error"]


def test_bad_record_in_array_rejects_whole_batch(client):
    bad = make_record()
    del bad["at"]
    status, body = client.json("POST", "/api/records", [make_record(), bad])
    assert status == 400 and "record 1" in body["error"]
    _, state = client.json("GET", "/api/state")
    assert state["machines"] == []


def test_invalid_json_and_non_object_rejected(client):
    status, _, raw = client.request("POST", "/api/records", raw=b"{not json")
    body = json.loads(raw)
    assert status == 400 and body["ok"] is False and "JSON" in body["error"]
    status, body = client.json("POST", "/api/records", [1, 2])
    assert status == 400 and body["ok"] is False
    status, body = client.json("POST", "/api/records", "hello")
    assert status == 400


def test_need_model_until_announced(client):
    _, reply = client.json("POST", "/api/records", make_record())
    assert reply["need_model"] is True
    announce(client)
    _, reply = client.json("POST", "/api/records", make_record())
    assert reply["need_model"] is False
    _, reply = client.json("POST", "/api/records", make_record(model_version="fake-0.2"))
    assert reply["need_model"] is True


def test_model_announcement_validation(client):
    bad = copy.deepcopy(MODEL)
    del bad["model_version"]
    status, body = client.json("POST", "/api/model", bad)
    assert status == 400 and "model_version" in body["error"]
    bad = copy.deepcopy(MODEL)
    bad["features"] = [{"sentence": "no name"}]
    status, body = client.json("POST", "/api/model", bad)
    assert status == 400


# ---- section 5: history ---------------------------------------------------


def test_machine_history_spectrum_and_limit(client):
    batch = [make_record(at=f"2026-11-14T16:{i // 60:02d}:{i % 60:02d}Z", n=i) for i in range(10)]
    client.json("POST", "/api/records", batch)
    client.json("POST", "/api/records", make_record(machine_id="other"))

    _, hist = client.json("GET", "/api/machines/conveyor-07/records")
    records = hist["records"]
    assert hist["ok"] is True and len(records) == 10
    assert [r["n"] for r in records] == list(range(10))  # oldest first
    assert all("spectrum" not in r for r in records)

    _, hist = client.json("GET", "/api/machines/conveyor-07/records?limit=3")
    assert [r["n"] for r in hist["records"]] == [7, 8, 9]  # newest 3, oldest first

    _, hist = client.json("GET", "/api/machines/conveyor-07/records?limit=2&spectrum=1")
    assert [len(r["spectrum"]) for r in hist["records"]] == [64, 64]

    _, hist = client.json("GET", "/api/machines/nobody/records")
    assert hist == {"ok": True, "records": []}


def test_machine_history_leaves_out_heat_frames_unless_asked(client):
    frame = {"at": "2026-11-14T16:00:00Z", "min_c": 24.0, "step_c": 0.1, "data": "AAAA"}
    heat = {"source": "camera", "imu_temp_c": 42.6, "max_c": 61.2, "frames": [frame]}
    client.json("POST", "/api/records", [make_record(heat=heat), make_record(heat={"source": "imu", "imu_temp_c": 42.0})])

    _, hist = client.json("GET", "/api/machines/conveyor-07/records")
    cam, imu = (r["heat"] for r in hist["records"])
    assert cam == {"source": "camera", "imu_temp_c": 42.6, "max_c": 61.2}
    assert imu == {"source": "imu", "imu_temp_c": 42.0}

    _, hist = client.json("GET", "/api/machines/conveyor-07/records?frames=1")
    assert hist["records"][0]["heat"]["frames"] == [frame]
    assert "spectrum" not in hist["records"][0]

    _, state = client.json("GET", "/api/state")
    assert state["devices"]["effente-01"]["latest"]["heat"]["source"] == "imu"
    _, again = client.json("GET", "/api/machines/conveyor-07/records?frames=1")
    assert again["records"][0]["heat"]["frames"] == [frame]  # leaving them out did not change the stored copy

    status, body = client.json("GET", "/api/machines/conveyor-07/records?limit=abc")
    assert status == 400 and body["ok"] is False


def test_history_limit_capped_at_2000(client):
    client.json("POST", "/api/records", [make_record(n=i) for i in range(2100)])
    _, hist = client.json("GET", "/api/machines/conveyor-07/records?limit=5000")
    assert len(hist["records"]) == 2000
    assert hist["records"][-1]["n"] == 2099
    _, hist = client.json("GET", "/api/machines/conveyor-07/records")
    assert len(hist["records"]) == 300


def test_machine_id_with_url_escapes(client):
    client.json("POST", "/api/records", make_record(machine_id="line 3/fan"))
    _, hist = client.json("GET", "/api/machines/line%203%2Ffan/records")
    assert len(hist["records"]) == 1


# ---- section 5: live stream -----------------------------------------------


def test_stream_delivers_record_model_mask_and_run_events(make_receiver, open_stream):
    _, client = make_receiver()
    stream = open_stream(client.port)
    assert stream.resp.status == 200
    assert stream.resp.getheader("Content-Type").startswith("text/event-stream")
    assert stream.comments.get(timeout=5) == "connected"

    announce(client)
    device = stream.next("device")  # first contact comes before the model event
    assert device["device_id"] == "effente-01" and device["connected"] is True
    assert stream.next("model") == MODEL

    client.json("POST", "/api/records", make_record())
    record = stream.next("record")
    assert record["seq"] == 1 and record["machine_id"] == "conveyor-07" and len(record["spectrum"]) == 64

    _, reply = client.json("PUT", "/api/devices/effente-01/mask", {"mask": ["rattle"]})
    assert stream.next("mask") == reply

    run = make_run()
    client.json("PUT", f"/api/agent/runs/{run['run_id']}", run)
    assert stream.next("agent_run") == run


def test_stream_keepalive_comment(make_receiver, open_stream):
    _, client = make_receiver(keepalive=0.3)
    stream = open_stream(client.port)
    assert stream.comments.get(timeout=5) == "connected"
    assert stream.comments.get(timeout=5) == "keep-alive"


def test_default_keepalive_is_5_seconds_and_quiet_rule_5_seconds():
    import receiver_server
    import receiver_store

    assert receiver_server.KEEPALIVE_S == 5.0
    assert receiver_store.QUIET_AFTER_S == 5.0


def test_device_goes_quiet_then_reconnects(make_receiver, open_stream):
    _, client = make_receiver(quiet_after=0.5)
    stream = open_stream(client.port)
    client.json("POST", "/api/records", make_record())
    assert stream.next("device")["connected"] is True
    quiet = stream.next("device", timeout=5)
    assert quiet == {"device_id": "effente-01", "connected": False, "last_seen": quiet["last_seen"]}
    _, state = client.json("GET", "/api/state")
    assert state["devices"]["effente-01"]["connected"] is False

    client.json("GET", "/api/devices/effente-01/mask")  # a mask poll counts as contact
    assert stream.next("device")["connected"] is True


def test_many_streams_and_abrupt_disconnects_do_not_stall_uploads(make_receiver, open_stream):
    _, client = make_receiver()
    # Ten tabs open the stream and vanish without reading or closing politely.
    for _ in range(10):
        s = socket.create_connection(("127.0.0.1", client.port))
        s.sendall(b"GET /api/stream HTTP/1.1\r\nHost: x\r\n\r\n")
        s.close()
    # A slow client that never reads anything.
    slow = socket.create_connection(("127.0.0.1", client.port))
    slow.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
    slow.sendall(b"GET /api/stream HTTP/1.1\r\nHost: x\r\n\r\n")
    good = open_stream(client.port)

    big = [make_record(n=i, pad="x" * 2000) for i in range(50)]
    start = time.monotonic()
    for _ in range(20):
        status, reply = client.json("POST", "/api/records", big)
        assert status == 200 and reply["stored"] == 50
    assert time.monotonic() - start < 10
    # The live client still got everything, in order.
    seqs = [good.next("record")["seq"] for _ in range(1000)]
    assert seqs == list(range(1, 1001))
    slow.close()


def test_parallel_uploads_get_unique_seq(client):
    errors = []

    def upload(device):
        try:
            for i in range(20):
                status, _ = client.json("POST", "/api/records", make_record(device_id=device, n=i))
                assert status == 200
        except Exception as e:  # pragma: no cover - reported below
            errors.append(e)

    threads = [threading.Thread(target=upload, args=(f"dev-{d}",)) for d in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    _, hist = client.json("GET", "/api/machines/conveyor-07/records?limit=2000")
    seqs = [r["seq"] for r in hist["records"]]
    assert sorted(seqs) == list(range(1, 161))
    assert seqs == sorted(seqs)


# ---- message 3: mask ------------------------------------------------------


def test_mask_put_arrives_in_next_upload_reply_and_get(client):
    """Done-when test: a mask set through the dashboard route reaches a test device."""
    device = DeviceClient(client)
    device.announce()
    assert device.upload()["mask"] == []

    status, reply = client.json("PUT", "/api/devices/effente-01/mask", {"mask": ["once_per_turn"]})
    assert status == 200
    assert reply == {"ok": True, "device_id": "effente-01", "mask": ["once_per_turn"], "mask_version": 1}

    up = device.upload()
    assert up["mask"] == ["once_per_turn"] and up["mask_version"] == 1
    assert device.poll() == {"ok": True, "device_id": "effente-01", "mask": ["once_per_turn"], "mask_version": 1}

    # Device acknowledges by echoing the mask in the next record.
    device.upload(mask=["once_per_turn"], features={"once_per_turn": 0.0, "rattle": 0.03})
    _, state = client.json("GET", "/api/state")
    assert state["devices"]["effente-01"]["latest"]["mask"] == ["once_per_turn"]
    assert state["devices"]["effente-01"]["mask"] == ["once_per_turn"]

    # Clearing the mask bumps the version again.
    _, reply = client.json("PUT", "/api/devices/effente-01/mask", {"mask": []})
    assert reply["mask"] == [] and reply["mask_version"] == 2
    assert device.upload()["mask_version"] == 2


def test_mask_version_only_moves_on_change(client):
    announce(client)
    _, a = client.json("PUT", "/api/devices/effente-01/mask", {"mask": ["rattle", "once_per_turn"]})
    _, b = client.json("PUT", "/api/devices/effente-01/mask", {"mask": ["once_per_turn", "rattle", "rattle"]})
    assert a["mask_version"] == b["mask_version"] == 1


def test_unknown_feature_rejected(client):
    announce(client)
    status, body = client.json("PUT", "/api/devices/effente-01/mask", {"mask": ["once_per_turn", "made_up"]})
    assert status == 400 and body["ok"] is False and "made_up" in body["error"]
    _, mask = client.json("GET", "/api/devices/effente-01/mask")
    assert mask["mask"] == [] and mask["mask_version"] == 0


def test_mask_for_device_without_model(client):
    status, body = client.json("PUT", "/api/devices/ghost/mask", {"mask": ["rattle"]})
    assert status == 400 and "has not announced" in body["error"]
    status, body = client.json("PUT", "/api/devices/ghost/mask", {"mask": []})
    assert status == 200 and body["mask_version"] == 0


def test_mask_validated_against_latest_announced_model(client):
    announce(client)
    newer = copy.deepcopy(MODEL)
    newer["model_version"] = "fake-0.2"
    newer["features"] = [{"name": "rattle", "sentence": "rattling pattern"}]
    announce(client, newer)
    status, _ = client.json("PUT", "/api/devices/effente-01/mask", {"mask": ["once_per_turn"]})
    assert status == 400
    status, _ = client.json("PUT", "/api/devices/effente-01/mask", {"mask": ["rattle"]})
    assert status == 200


def test_bad_mask_body_rejected(client):
    announce(client)
    for body in ({"mask": "rattle"}, {"nope": []}, {"mask": [1]}, ["rattle"]):
        status, reply = client.json("PUT", "/api/devices/effente-01/mask", body)
        assert status == 400 and reply["ok"] is False, body


# ---- message 4: agent runs ------------------------------------------------


def test_agent_run_upsert_and_listing(client):
    run = make_run()
    path = f"/api/agent/runs/{run['run_id']}"
    assert client.json("PUT", path, run) == (200, {"ok": True})
    run["tool_calls"].append({"at": "2026-11-14T16:03:01Z", "name": "get_readings", "input": {}, "output": "{}"})
    client.json("PUT", path, run)
    run["status"] = "done"
    run["ok"] = True
    run["report"] = {
        "summary": "s",
        "priority": "medium",
        "cited_features": ["once_per_turn"],
        "recommended_action": "a",
        "part_reference": "",
    }
    client.json("PUT", path, run)

    older = make_run("run-older", at="2026-11-14T15:00:00Z")
    client.json("PUT", "/api/agent/runs/run-older", older)
    client.json("PUT", "/api/agent/runs/run-other", make_run("run-other", machine_id="fan-02"))

    _, listing = client.json("GET", "/api/machines/conveyor-07/agent-runs")
    assert listing["ok"] is True
    assert [r["run_id"] for r in listing["runs"]] == [run["run_id"], "run-older"]  # newest first
    assert listing["runs"][0] == run  # latest copy kept

    _, listing = client.json("GET", "/api/machines/conveyor-07/agent-runs?limit=1")
    assert len(listing["runs"]) == 1


def test_agent_run_validation(client):
    run = make_run()
    status, body = client.json("PUT", "/api/agent/runs/some-other-id", run)
    assert status == 400 and "match" in body["error"]
    status, body = client.json("PUT", "/api/agent/runs/bad!id", make_run("bad!id"))
    assert status == 400
    bad = make_run()
    del bad["status"]
    status, body = client.json("PUT", f"/api/agent/runs/{bad['run_id']}", bad)
    assert status == 400 and "status" in body["error"]
    status, body = client.json("PUT", f"/api/agent/runs/{run['run_id']}", make_run(status="thinking"))
    assert status == 400


# ---- routing --------------------------------------------------------------


def test_unknown_api_route_and_wrong_method(client):
    status, body = client.json("GET", "/api/nothing")
    assert status == 404 and body["ok"] is False
    status, body = client.json("DELETE", "/api/state")
    assert status == 405 and body["ok"] is False
    status, body = client.json("POST", "/index.html", {})
    assert status == 405


# ---- restart --------------------------------------------------------------


def test_restart_keeps_history_and_resets_masks(make_receiver, tmp_path):
    data = tmp_path / "shared-data"
    receiver, client = make_receiver(data_dir=data)
    announce(client)
    client.json("POST", "/api/records", [make_record(n=i) for i in range(5)])
    client.json("PUT", "/api/devices/effente-01/mask", {"mask": ["rattle"]})
    run = make_run()
    client.json("PUT", f"/api/agent/runs/{run['run_id']}", run)
    run["status"] = "done"
    client.json("PUT", f"/api/agent/runs/{run['run_id']}", run)
    receiver.stop()

    _, client = make_receiver(data_dir=data)
    _, state = client.json("GET", "/api/state")
    dev = state["devices"]["effente-01"]
    assert dev["connected"] is False
    assert dev["mask"] == [] and dev["mask_version"] == 0
    assert dev["latest"]["n"] == 4
    assert state["models"] == {"fake-0.1": MODEL}
    assert state["machines"] == ["conveyor-07"]
    _, hist = client.json("GET", "/api/machines/conveyor-07/records")
    assert [r["n"] for r in hist["records"]] == [0, 1, 2, 3, 4]
    _, runs = client.json("GET", "/api/machines/conveyor-07/agent-runs")
    assert len(runs["runs"]) == 1 and runs["runs"][0]["status"] == "done"

    # seq keeps rising after restart, and the model is still known.
    _, reply = client.json("POST", "/api/records", make_record(n=5))
    assert reply["need_model"] is False
    _, hist = client.json("GET", "/api/machines/conveyor-07/records")
    assert hist["records"][-1]["seq"] == 6
    # The mask can be set again against the reloaded feature table.
    status, _ = client.json("PUT", "/api/devices/effente-01/mask", {"mask": ["rattle"]})
    assert status == 200


def test_corrupt_last_line_does_not_stop_start(make_receiver, tmp_path):
    data = tmp_path / "corrupt"
    receiver, client = make_receiver(data_dir=data)
    client.json("POST", "/api/records", [make_record(n=0), make_record(n=1)])
    receiver.stop()
    with open(data / "records.jsonl", "a") as f:
        f.write('{"device_id": "effente-01", "machine_id": "conv')  # half-written, no newline
    with open(data / "agent_runs.jsonl", "w") as f:
        f.write("garbage\n")

    receiver, client = make_receiver(data_dir=data)
    _, hist = client.json("GET", "/api/machines/conveyor-07/records")
    assert [r["n"] for r in hist["records"]] == [0, 1]
    client.json("POST", "/api/records", make_record(n=2))
    receiver.stop()

    _, client = make_receiver(data_dir=data)
    _, hist = client.json("GET", "/api/machines/conveyor-07/records")
    assert [r["n"] for r in hist["records"]] == [0, 1, 2]


# ---- a test device --------------------------------------------------------


class DeviceClient:
    """Acts like the handheld: announces its model, uploads records, polls the mask."""

    def __init__(self, client, device_id="effente-01"):
        self.client = client
        self.device_id = device_id

    def announce(self):
        model = dict(MODEL, device_id=self.device_id)
        assert self.client.json("POST", "/api/model", model) == (200, {"ok": True})

    def upload(self, **over):
        status, reply = self.client.json("POST", "/api/records", make_record(device_id=self.device_id, **over))
        assert status == 200 and reply["ok"] is True
        if reply["need_model"]:
            self.announce()
        return reply

    def poll(self):
        status, reply = self.client.json("GET", f"/api/devices/{self.device_id}/mask")
        assert status == 200
        return reply
