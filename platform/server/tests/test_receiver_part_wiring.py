"""Step 0 of docs/scan-and-log-plan.md: the routes, hooks and helpers of contract
section 8 are wired in, with stubs behind them. Nothing here works yet, and
nothing that worked before may change."""

import http.client
import json

import pytest
import scan_api
from receiver_server import Handler
from receiver_store import BadRequest
from receiver_testkit import make_record

ROUTES = [
    ("GET", "/api/hello"),
    ("POST", "/api/machines/conveyor-07/scans?client_id=test-0001"),
    ("GET", "/api/machines/conveyor-07/scans"),
    ("GET", "/api/scans/scan-1"),
    ("GET", "/api/scans/scan-1/model.glb"),
    ("GET", "/api/scans/scan-1/file"),
    ("POST", "/api/machines/conveyor-07/parts"),
    ("GET", "/api/machines/conveyor-07/parts"),
    ("PUT", "/api/parts/part-0007"),
    ("POST", "/api/machines/conveyor-07/log"),
    ("GET", "/api/machines/conveyor-07/log"),
    ("GET", "/api/machines/conveyor-07/log.csv"),
]


@pytest.mark.parametrize("method,path", ROUTES)
def test_every_new_route_answers_not_built_yet(client, method, path):
    body = None if method == "GET" else {"name": "Drive roller"}
    assert client.json(method, path, body) == (400, {"ok": False, "error": "not built yet"})


def test_a_stubbed_route_leaves_the_connection_usable(client):
    """The stub drops the body it did not read, so the next request on the same connection is read whole."""
    conn = http.client.HTTPConnection("127.0.0.1", client.port, timeout=10)
    try:
        conn.request("POST", "/api/machines/conveyor-07/parts", body=json.dumps({"name": "Belt", "kind": "belt"}))
        first = conn.getresponse()
        assert (first.status, json.loads(first.read())["error"]) == (400, "not built yet")
        conn.request("GET", "/api/state")
        second = conn.getresponse()
        assert second.status == 200 and json.loads(second.read())["ok"] is True
    finally:
        conn.close()


def test_state_is_unchanged_while_the_stores_are_stubs(client):
    _, state = client.json("GET", "/api/state")
    assert set(state) == {"ok", "now", "devices", "models", "machines", "alarms"}


def test_state_takes_each_stores_part_and_its_machines(make_receiver):
    receiver, client = make_receiver()
    client.json("POST", "/api/records", make_record())
    receiver.store.scans.state_part = lambda: {"scans": {"press-01": {"scan_id": "scan-1"}}, "machines": ["press-01"]}
    receiver.store.partlog.state_part = lambda: {"parts": {"conveyor-07": []}, "machines": ["conveyor-07"]}
    _, state = client.json("GET", "/api/state")
    assert state["machines"] == ["conveyor-07", "press-01"]
    assert state["scans"] == {"press-01": {"scan_id": "scan-1"}} and state["parts"] == {"conveyor-07": []}


def test_the_log_store_hears_of_records_alarms_and_answers(make_receiver):
    receiver, client = make_receiver()
    heard = []
    log = receiver.store.partlog
    log.record_stored = lambda record: heard.append(("record", record["seq"]))
    log.alarm_changed = lambda alarm: heard.append(("alarm", alarm["alarm_id"], alarm["open"], len(alarm["answers"])))
    log.alarm_answered = lambda alarm, answer: heard.append(("answer", answer["verdict"], len(alarm["answers"])))
    client.json("POST", "/api/records", make_record())
    client.json("POST", "/api/records", make_record(diagnosis="normal", abnormal_minutes=0))
    status, _ = client.json("POST", "/api/alarms/alarm-1/answers", {"verdict": "real", "by": "Sam", "part_id": "part-0007"})
    assert status == 200
    assert heard == [
        ("alarm", "alarm-1", True, 0), ("record", 1),
        ("alarm", "alarm-1", False, 0), ("record", 2),
        ("answer", "real", 0), ("alarm", "alarm-1", False, 1),
    ]


def test_a_failing_hook_never_stops_a_record_but_can_refuse_an_answer(make_receiver, capsys):
    receiver, client = make_receiver()

    def broken(*args):
        raise RuntimeError("log store bug")

    def refuse(alarm, answer):
        raise BadRequest("no such part: part-9999")

    receiver.store.partlog.record_stored = broken
    receiver.store.partlog.alarm_changed = broken
    receiver.store.partlog.state_part = broken
    status, reply = client.json("POST", "/api/records", make_record())
    assert (status, reply["stored"]) == (200, 1)
    assert client.json("GET", "/api/state")[0] == 200
    assert "log store bug" in capsys.readouterr().err

    receiver.store.partlog.alarm_answered = refuse
    answer = {"verdict": "real", "by": "Sam", "part_id": "part-9999"}
    assert client.json("POST", "/api/alarms/alarm-1/answers", answer) == (400, {"ok": False, "error": "no such part: part-9999"})
    assert client.json("GET", "/api/machines/conveyor-07/alarms")[1]["alarms"][0]["answers"] == []


def test_a_raw_body_goes_to_a_file_and_a_file_comes_back(make_receiver, tmp_path, monkeypatch):
    kept = tmp_path / "upload.bin"

    def post_scan(request, query, machine_id):
        request.send_json(200, {"ok": True, "bytes": request.read_body_to_file(str(kept), 1000)})

    def get_scan_file(request, query, scan_id):
        request.send_file(str(kept), "model/gltf-binary")

    monkeypatch.setattr(scan_api, "post_scan", post_scan)
    monkeypatch.setattr(scan_api, "get_scan_file", get_scan_file)
    _, client = make_receiver()
    body = b"glTF" + bytes(range(256)) * 2
    status, _, raw = client.request("POST", "/api/machines/conveyor-07/scans", raw=body)
    assert (status, json.loads(raw)) == (200, {"ok": True, "bytes": len(body)})
    status, headers, raw = client.request("GET", "/api/scans/scan-1/file")
    assert (status, raw, headers["Content-Type"]) == (200, body, "model/gltf-binary")

    for bad, why in ((b"x" * 1001, "between 1 and 1000"), (b"", "between 1 and 1000")):
        kept.unlink(missing_ok=True)
        status, _, raw = client.request("POST", "/api/machines/conveyor-07/scans", raw=bad, headers={"Content-Length": str(len(bad))})
        assert status == 400 and why in json.loads(raw)["error"]
        assert not kept.exists()


def test_the_helpers_the_api_files_use_are_on_the_handler():
    for name in ("read_json", "read_body_to_file", "send_json", "send_bytes", "send_file", "discard_body"):
        assert callable(getattr(Handler, name))
