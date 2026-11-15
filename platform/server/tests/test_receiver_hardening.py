"""Things a stress run found (14 Nov): one bad message or one failed log line must not
break the receiver for everything that comes after it."""

import json
import os
import time

from receiver_testkit import make_check_in, make_record


def strict(raw):
    """Parse as a browser does: NaN and Infinity are not JSON."""

    def refuse(name):
        raise ValueError(f"{name} is not JSON")

    return json.loads(raw, parse_constant=refuse)


def test_nan_and_infinity_are_stored_as_null_and_never_served(client):
    # What Python's json.dumps writes for float("nan") and float("inf"), and a number too big for a float.
    raw = json.dumps(make_record(unknown_score=float("nan"), features={"vib_1x": float("inf"), "ok": 1.5}))
    raw = raw.replace('"confidence": 0.71', '"confidence": 1e999').encode()
    status, reply = client.json("POST", "/api/records", raw=raw)
    assert status == 200 and reply["stored"] == 1
    for path in ("/api/state", "/api/machines/conveyor-07/records?spectrum=1"):
        status, _, body = client.request("GET", path)
        assert status == 200
        strict(body)
    record = client.json("GET", "/api/machines/conveyor-07/records")[1]["records"][0]
    assert record["unknown_score"] is None and record["confidence"] is None
    assert record["features"] == {"vib_1x": None, "ok": 1.5}


def test_nan_already_in_the_stored_file_is_read_as_null(make_receiver, tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "records.jsonl").write_text(json.dumps(dict(make_record(unknown_score=float("nan")), seq=1)) + "\n")
    _, client = make_receiver(data_dir=data)
    status, _, body = client.request("GET", "/api/state")
    assert status == 200
    assert strict(body)["devices"]["effente-01"]["latest"]["unknown_score"] is None


def test_nan_is_refused_where_null_means_something_else(client):
    for method, path, raw in (
        ("PUT", "/api/devices/effente-01/settings", b'{"sensitivity": NaN}'),
        ("PUT", "/api/devices/effente-01/settings", b'{"sensitivity": 1e999}'),
        ("PUT", "/api/agent/runs/run-1", b'{"run_id": "run-1", "machine_id": "m", "at": "2026-11-14T16:00:00Z", "status": "done", "x": Infinity}'),
        ("POST", "/api/machines/conveyor-07/log", b'{"kind": "note", "by": "Sam", "text": "x", "n": -Infinity}'),
    ):
        status, reply = client.json(method, path, raw=raw)
        assert status == 400 and reply["ok"] is False, raw
    status, _, body = client.request("GET", "/api/state")
    strict(body)


def test_json_nested_too_deeply_is_a_400(client):
    status, reply = client.json("POST", "/api/records", raw=b"[" * 100000 + b"]" * 100000)
    assert status == 400 and not reply["ok"]


def test_a_log_line_that_fails_does_not_fail_the_upload_or_the_quiet_watch(make_receiver, tmp_path):
    def broken(message):
        raise OSError("the terminal is gone")

    receiver, client = make_receiver(log=broken, quiet_after=0.3)
    status, reply = client.json("POST", "/api/records", make_record())
    assert status == 200 and reply["stored"] == 1  # "device connected" could not be logged
    assert client.json("GET", "/api/state")[1]["devices"]["effente-01"]["connected"] is True
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:  # "went quiet" could not be logged either
        if client.json("GET", "/api/state")[1]["devices"]["effente-01"]["connected"] is False:
            break
        time.sleep(0.05)
    else:
        raise AssertionError("the device was never marked as not connected")
    assert receiver._watcher.is_alive()


def test_the_quiet_watch_survives_an_error(make_receiver):
    receiver, client = make_receiver(quiet_after=0.3)
    real, calls = receiver.store.check_quiet, []

    def fails_once():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")
        real()

    receiver.store.check_quiet = fails_once
    client.json("POST", "/api/devices/effente-01", make_check_in())
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if client.json("GET", "/api/state")[1]["devices"]["effente-01"]["connected"] is False:
            return
        time.sleep(0.05)
    raise AssertionError("the watcher stopped after one error")


def test_half_a_character_in_a_name_does_not_fail_the_log_export(client):
    # "\ud800" is valid JSON and is half of a character pair: it cannot be written as UTF-8.
    raw = b'{"kind": "note", "by": "Sam \\ud800", "text": "belt checked"}'
    status, _ = client.json("POST", "/api/machines/conveyor-07/log", raw=raw)
    assert status == 200
    status, _, body = client.request("GET", "/api/machines/conveyor-07/log.csv")
    assert status == 200 and b"belt checked" in body
    status, reply = client.json("POST", "/api/records", raw=json.dumps(make_record()).replace("effente-01", "e\\ud800").encode())
    assert status == 200 and reply["stored"] == 1


def test_the_listen_queue_is_longer_than_the_default():
    from receiver_server import ReceiverHTTPServer

    assert ReceiverHTTPServer.request_queue_size >= 64
