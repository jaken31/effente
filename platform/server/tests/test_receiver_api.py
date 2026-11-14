"""End-to-end tests: a real receiver on a free port, talked to over HTTP."""

import copy
import http.client
import json
import socket
import threading
import time

import pytest

from receiver_testkit import ABOVE_NORMAL, MODEL, TOO_HOT, make_check_in, make_record, make_run


def announce(client, model=MODEL):
    status, body = client.json("POST", "/api/model", model)
    assert (status, body) == (200, {"ok": True})


# ---- message 2: records ---------------------------------------------------


def test_post_one_record_and_read_back_by_state(client):
    announce(client)
    status, reply = client.json("POST", "/api/records", make_record())
    assert status == 200
    assert reply == {"ok": True, "stored": 1, "need_model": False}

    status, state = client.json("GET", "/api/state")
    assert status == 200 and state["ok"] is True
    assert state["machines"] == ["conveyor-07"]
    assert state["models"] == {"conveyor-07-20261114": MODEL}
    dev = state["devices"]["effente-01"]
    assert set(dev) == {"connected", "last_seen", "mode", "machine_id", "model_version", "wrong", "problems", "sensitivity", "settings", "latest", "readings"}
    assert dev["connected"] is True and dev["mode"] == "watching" and dev["machine_id"] == "conveyor-07"
    assert dev["model_version"] == "conveyor-07-20261114"
    assert dev["wrong"] is True and dev["problems"] == []
    latest = dev["latest"]
    assert latest["diagnosis"] == "unbalance_like"
    assert len(latest["spectrum"]) == 20
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
    for field in ("device_id", "machine_id", "model_version", "at", "diagnosis", "problems", "wrong"):
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
    _, reply = client.json("POST", "/api/records", make_record(model_version="conveyor-07-20261115"))
    assert reply["need_model"] is True


def test_model_announcement_validation(client):
    bad = copy.deepcopy(MODEL)
    del bad["model_version"]
    status, body = client.json("POST", "/api/model", bad)
    assert status == 400 and "model_version" in body["error"]
    bad = copy.deepcopy(MODEL)
    bad["features"] = [{"text": "no name"}]
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
    assert [len(r["spectrum"]) for r in hist["records"]] == [20, 20]

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


def test_history_since_reads_a_range_page_by_page(client):
    batch = [make_record(at=f"2026-11-14T16:{i // 60:02d}:{i % 60:02d}Z", n=i) for i in range(10)]
    client.json("POST", "/api/records", batch)
    client.json("POST", "/api/records", make_record(machine_id="other", at="2026-11-14T16:00:05Z"))

    _, hist = client.json("GET", "/api/machines/conveyor-07/records?since=2026-11-14T16:00:04Z&limit=3")
    page = hist["records"]
    assert [r["n"] for r in page] == [4, 5, 6]  # oldest 3 at or after since, oldest first

    _, hist = client.json(
        "GET", f"/api/machines/conveyor-07/records?since=2026-11-14T16:00:04Z&after_seq={page[-1]['seq']}&limit=3")
    assert [r["n"] for r in hist["records"]] == [7, 8, 9]

    _, hist = client.json("GET", f"/api/machines/conveyor-07/records?after_seq={page[-1]['seq']}&limit=2")
    assert [r["n"] for r in hist["records"]] == [7, 8]  # after_seq alone pages from there

    _, hist = client.json("GET", "/api/machines/conveyor-07/records?since=2026-11-14T16:00:04%2B00:00")
    assert [r["n"] for r in hist["records"]] == list(range(4, 10))  # an offset works too

    _, hist = client.json("GET", "/api/machines/conveyor-07/records?since=2026-11-14T17:00:00Z")
    assert hist == {"ok": True, "records": []}


def test_history_since_and_after_seq_reject_bad_values(client):
    for query in ("since=yesterday", "after_seq=abc", "after_seq=-1"):
        status, body = client.json("GET", f"/api/machines/conveyor-07/records?{query}")
        assert status == 400 and body["ok"] is False, query


def test_machine_id_with_url_escapes(client):
    client.json("POST", "/api/records", make_record(machine_id="line 3/fan"))
    _, hist = client.json("GET", "/api/machines/line%203%2Ffan/records")
    assert len(hist["records"]) == 1


# ---- section 5: live stream -----------------------------------------------


def test_stream_delivers_model_device_record_and_run_events(make_receiver, open_stream):
    _, client = make_receiver()
    stream = open_stream(client.port)
    assert stream.resp.status == 200
    assert stream.resp.getheader("Content-Type").startswith("text/event-stream")
    assert stream.comments.get(timeout=5) == "connected"

    announce(client)
    assert stream.next("model") == MODEL

    client.json("POST", "/api/records", make_record())
    names = []
    while "record" not in names:
        name, data = stream.events.get(timeout=5)
        names.append(name)
        if name == "device":
            assert data == {"device_id": "effente-01", "connected": True, "last_seen": data["last_seen"], "mode": "watching"}
        if name == "record":
            assert data["seq"] == 1 and data["machine_id"] == "conveyor-07" and len(data["spectrum"]) == 20
    assert names == ["device", "record"]  # the device connects with its first record, not its announcement

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
    assert quiet == {"device_id": "effente-01", "connected": False, "last_seen": quiet["last_seen"], "mode": "watching"}
    _, state = client.json("GET", "/api/state")
    assert state["devices"]["effente-01"]["connected"] is False

    client.json("POST", "/api/devices/effente-01", make_check_in())  # a check-in counts as contact
    back = stream.next("device")
    assert back["connected"] is True and back["mode"] == "recording"


def test_announcement_keeps_a_device_connected_but_does_not_connect_it(make_receiver):
    receiver, client = make_receiver(quiet_after=0.6)
    announce(client)
    _, state = client.json("GET", "/api/state")
    dev = state["devices"]["effente-01"]
    assert dev["connected"] is False and dev["mode"] is None and dev["last_seen"].endswith("Z")

    client.json("POST", "/api/records", make_record())
    for _ in range(4):  # 1.2 s with only announcements, past quiet_after
        time.sleep(0.3)
        announce(client)
    _, state = client.json("GET", "/api/state")
    assert state["devices"]["effente-01"]["connected"] is True


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


# ---- message 3: check-in --------------------------------------------------


def test_check_in_sets_mode_and_problems_in_state(client):
    status, reply = client.json("POST", "/api/devices/effente-01", make_check_in(wrong=True, problems=[TOO_HOT]))
    assert (status, reply) == (200, {"ok": True})
    _, state = client.json("GET", "/api/state")
    dev = state["devices"]["effente-01"]
    assert dev["connected"] is True and dev["mode"] == "recording" and dev["machine_id"] == "conveyor-07"
    assert dev["wrong"] is True and dev["problems"] == [TOO_HOT]
    assert dev["latest"] is None
    assert state["machines"] == []  # a check-in is not a record


def test_check_in_then_record_switches_mode_to_watching(make_receiver, open_stream):
    _, client = make_receiver()
    stream = open_stream(client.port)
    for mode in ("display", "calibrating", "recording", "training"):
        assert client.json("POST", "/api/devices/effente-01", make_check_in(mode=mode))[0] == 200
        assert stream.next("device")["mode"] == mode
    client.json("POST", "/api/devices/effente-01", make_check_in(mode="training"))  # same mode: no event
    client.json("POST", "/api/records", make_record())
    assert stream.next("device") == {
        "device_id": "effente-01", "connected": True, "last_seen": stream_last_seen(client), "mode": "watching"}


def stream_last_seen(client):
    return client.json("GET", "/api/state")[1]["devices"]["effente-01"]["last_seen"]


def test_check_in_validation(client):
    for field in ("machine_id", "mode", "at", "wrong", "problems"):
        body = make_check_in()
        del body[field]
        status, reply = client.json("POST", "/api/devices/effente-01", body)
        assert status == 400 and reply["ok"] is False and field in reply["error"], field
    bad_bodies = [
        make_check_in(mode="watching"),  # watching devices send records, not check-ins
        make_check_in(mode="sleeping"),
        make_check_in(wrong="yes"),
        make_check_in(problems={"name": "too_hot"}),
        make_check_in(problems=[{"kind": "limit"}]),
        make_check_in(problems=[TOO_HOT, TOO_HOT]),
        ["recording"],
    ]
    for body in bad_bodies:
        status, reply = client.json("POST", "/api/devices/effente-01", body)
        assert status == 400 and reply["ok"] is False, body
    _, state = client.json("GET", "/api/state")
    assert state["devices"] == {}  # nothing refused counts as contact


def test_check_in_route_methods(client):
    status, _ = client.json("GET", "/api/devices/effente-01")
    assert status == 405
    status, _ = client.json("POST", "/api/devices/", make_check_in())
    assert status == 404
    status, _ = client.json("GET", "/api/devices/effente-01/mask")  # contract version 1 only
    assert status == 404


def test_problem_names_are_not_checked_against_a_list(client):
    new = {"name": "smells_of_smoke", "kind": "limit", "text": "smoke", "value": None, "since": "2026-11-14T16:00:00Z"}
    status, _ = client.json("POST", "/api/records", make_record(problems=[new], wrong=True))
    assert status == 200
    status, _ = client.json("POST", "/api/devices/effente-01", make_check_in(problems=[{"name": "x"}], wrong=True))
    assert status == 200


def test_record_problems_rules(client):
    for problems in ("too_hot", [{"text": "no name"}], [TOO_HOT, dict(TOO_HOT, value=1)]):
        status, reply = client.json("POST", "/api/records", make_record(problems=problems))
        assert status == 400 and "problem" in reply["error"], problems
    status, reply = client.json("POST", "/api/records", make_record(wrong=None))
    assert status == 400 and "wrong" in reply["error"]
    status, reply = client.json("POST", "/api/records", [make_record(), make_record(problems=None)])
    assert status == 400 and "record 1" in reply["error"]


# ---- section 5: problem events -------------------------------------------


def test_problem_events_compare_by_name(make_receiver, open_stream):
    _, client = make_receiver()
    stream = open_stream(client.port)

    def problem_events(body, path="/api/records"):
        assert client.json("POST", path, body)[0] == 200
        client.json("POST", "/api/model", MODEL)  # marker: everything before it has been sent
        out = []
        while True:
            name, data = stream.events.get(timeout=5)
            if name == "model":
                return out
            if name.startswith("problem"):
                out.append((name, data))

    # A problem while the diagnosis is normal.
    got = problem_events(make_record(diagnosis="normal", at="2026-11-14T16:00:01Z", problems=[TOO_HOT], wrong=True))
    assert got == [("problem", {"device_id": "effente-01", "machine_id": "conveyor-07",
                                "at": "2026-11-14T16:00:01Z", "problem": TOO_HOT})]
    # Same name, new value: no event, but state shows the new value.
    hotter = dict(TOO_HOT, value=110.0)
    assert problem_events(make_record(at="2026-11-14T16:00:02Z", problems=[hotter])) == []
    _, state = client.json("GET", "/api/state")
    assert state["devices"]["effente-01"]["problems"] == [hotter]
    # One more appears.
    got = problem_events(make_record(at="2026-11-14T16:00:03Z", problems=[hotter, ABOVE_NORMAL]))
    assert got == [("problem", {"device_id": "effente-01", "machine_id": "conveyor-07",
                                "at": "2026-11-14T16:00:03Z", "problem": ABOVE_NORMAL})]
    # Leaving watching: only the limit stays (outside-normal checks stop), from a check-in.
    got = problem_events(make_check_in(at="2026-11-14T16:00:04Z", problems=[hotter], wrong=True), "/api/devices/effente-01")
    assert got == [("problem_cleared", {"device_id": "effente-01", "machine_id": "conveyor-07",
                                        "at": "2026-11-14T16:00:04Z", "name": "vibration_above_normal"})]
    # It cools down while recording.
    got = problem_events(make_check_in(at="2026-11-14T16:00:05Z"), "/api/devices/effente-01")
    assert got == [("problem_cleared", {"device_id": "effente-01", "machine_id": "conveyor-07",
                                        "at": "2026-11-14T16:00:05Z", "name": "too_hot"})]
    _, state = client.json("GET", "/api/state")
    assert state["devices"]["effente-01"]["problems"] == [] and state["devices"]["effente-01"]["wrong"] is False


def test_each_record_in_a_batch_is_compared_with_the_one_before(make_receiver, open_stream):
    _, client = make_receiver()
    stream = open_stream(client.port)
    batch = [
        make_record(at="2026-11-14T16:00:01Z", problems=[TOO_HOT]),
        make_record(at="2026-11-14T16:00:02Z", problems=[]),
        make_record(at="2026-11-14T16:00:03Z", problems=[TOO_HOT]),
    ]
    client.json("POST", "/api/records", batch)
    got = [stream.next_of(("problem", "problem_cleared")) for _ in range(3)]
    assert [(n, d["at"]) for n, d in got] == [
        ("problem", "2026-11-14T16:00:01Z"), ("problem_cleared", "2026-11-14T16:00:02Z"), ("problem", "2026-11-14T16:00:03Z")]


def test_quiet_device_clears_its_problems_and_they_come_back_as_new(make_receiver, open_stream):
    _, client = make_receiver(quiet_after=0.5)
    stream = open_stream(client.port)
    client.json("POST", "/api/devices/effente-01", make_check_in(problems=[TOO_HOT], wrong=True))
    assert stream.next("problem")["problem"] == TOO_HOT
    cleared = stream.next("problem_cleared", timeout=5)
    assert cleared["reason"] == "device quiet" and cleared["name"] == "too_hot"
    assert cleared["device_id"] == "effente-01" and cleared["machine_id"] == "conveyor-07"
    assert cleared["at"].endswith("Z")
    _, state = client.json("GET", "/api/state")
    dev = state["devices"]["effente-01"]
    assert dev["connected"] is False and dev["wrong"] is False and dev["problems"] == []

    client.json("POST", "/api/devices/effente-01", make_check_in(at="2026-11-14T15:41:00Z", problems=[TOO_HOT], wrong=True))
    again = stream.next("problem")
    assert again["at"] == "2026-11-14T15:41:00Z" and again["problem"] == TOO_HOT


def test_wrong_and_problems_hidden_while_not_connected(make_receiver):
    _, client = make_receiver(quiet_after=0.3)
    client.json("POST", "/api/records", make_record(problems=[TOO_HOT]))
    time.sleep(1.0)
    _, state = client.json("GET", "/api/state")
    dev = state["devices"]["effente-01"]
    assert dev["connected"] is False and dev["wrong"] is False and dev["problems"] == []
    assert dev["latest"]["problems"] == [TOO_HOT]  # the stored record is unchanged


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
        "cited_features": ["vib_1x"],
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


def test_restart_keeps_history_and_forgets_problems(make_receiver, tmp_path):
    data = tmp_path / "shared-data"
    receiver, client = make_receiver(data_dir=data)
    announce(client)
    client.json("POST", "/api/records", [make_record(n=i, problems=[TOO_HOT]) for i in range(5)])
    run = make_run()
    client.json("PUT", f"/api/agent/runs/{run['run_id']}", run)
    run["status"] = "done"
    client.json("PUT", f"/api/agent/runs/{run['run_id']}", run)
    receiver.stop()

    _, client = make_receiver(data_dir=data)
    _, state = client.json("GET", "/api/state")
    dev = state["devices"]["effente-01"]
    assert dev["connected"] is False and dev["mode"] is None and dev["machine_id"] == "conveyor-07"
    assert dev["wrong"] is False and dev["problems"] == []
    assert dev["latest"]["n"] == 4
    assert state["models"] == {"conveyor-07-20261114": MODEL}
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


# ---- connection handling ----------------------------------------------------


@pytest.mark.parametrize("method,path", [
    ("POST", "/api/devices/effente-01/mask"),  # no such route (the mask went in contract 3)
    ("PUT", "/api/records"),  # route exists, method does not
    ("POST", "/index.html"),  # dashboard files take no POST
])
def test_unread_body_does_not_spoil_the_next_request(client, method, path):
    conn = http.client.HTTPConnection("127.0.0.1", client.port, timeout=5)
    try:
        conn.request(method, path, body=b'{"mode": "recording"}', headers={"Content-Type": "application/json"})
        assert conn.getresponse().read()
        conn.request("GET", "/api/state")  # same keep-alive connection
        res = conn.getresponse()
        assert res.status == 200 and json.loads(res.read())["ok"] is True
    finally:
        conn.close()
