"""The scan routes (platform/CONTRACT.md sections 8.1, 8.3 and 8.4), over HTTP.

The scan store's own rules are tested in platform/scan/tests. These tests are
about what the receiver adds: the routes, the body going to disk, what happens
to a body nobody read, the state, the stream and a restart.

Blender is a stand-in here (platform/scan/tests/fake_blender.py). No test runs
a real Blender, and none uses a real phone scan (unverified).
"""

import http.client
import io
import json
import os
import socket
import sys
import zipfile

import pytest
import scan_store  # puts platform/scan on the path
from receiver_testkit import make_record

import scan_convert  # noqa: E402
from scan_glb import box_glb  # noqa: E402

M = "conveyor-07"
FAKE_BLENDER = [sys.executable, os.path.join(scan_store.SCAN_DIR, "tests", "fake_blender.py")]
BOUNDS = {"min": [-0.21, 0.0, -0.09], "max": [0.21, 0.14, 0.09]}
USDZ_TYPE = "model/vnd.usdz+zip"
GLB_TYPE = "model/gltf-binary"


@pytest.fixture(autouse=True)
def blender(monkeypatch):
    """Every test converts with the stand-in. `blender(None)` is a laptop with no Blender."""
    def use(command):
        monkeypatch.setattr(scan_convert, "find_blender", lambda env=None: command)

    use(FAKE_BLENDER)
    return use


def usdz_bytes(padding=0):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("scan.usda", "#usda 1.0\n" + "#" * padding)
    return buffer.getvalue()


def one_second_apart(receiver):
    """Scans stored in the same second have no newest: give each its own second."""
    clock = [1794680102.0]  # 2026-11-14T18:15:02Z

    def now():
        clock[0] += 1
        return clock[0]

    receiver.store.scans._now = now


def ok(client, method, path, body=None, **kw):
    status, reply = client.json(method, path, body, **kw)
    assert status == 200, reply
    return reply


def upload(client, body, machine=M, client_id="test-0001", more=""):
    return client.json("POST", f"/api/machines/{machine}/scans?client_id={client_id}{more}", raw=body)


def ask(conn, method, path, body=None):
    """One request on a connection that is kept open. Returns (status, reply)."""
    conn.request(method, path, body=body)
    reply = conn.getresponse()
    return reply.status, json.loads(reply.read())


def raw_exchange(port, head, body=b""):
    """Send exactly these bytes, then read until the receiver closes the connection.
    A receiver that kept it open would make this time out."""
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        sock.sendall(head.encode() + b"\r\n" + body)
        got = b""
        while chunk := sock.recv(65536):
            got += chunk
    status = int(got.split(b" ", 2)[1])
    return status, json.loads(got.split(b"\r\n\r\n", 1)[1])


def status_events(stream, scan_id, until):
    """The model statuses of this scan's `scan` events, up to and including `until`."""
    seen = []
    while not seen or seen[-1] != until:
        scan = stream.next("scan")
        if scan["scan_id"] == scan_id:
            seen.append(scan["model"]["status"])
    return seen


# ---- 8.1 -------------------------------------------------------------------


def test_hello(client):
    hello = ok(client, "GET", "/api/hello")
    assert hello.pop("now").endswith("Z")
    assert hello == {"ok": True, "service": "effente", "contract": "3.4", "max_scan_bytes": 268435456}


# ---- 8.3 and 8.4: good uploads ----------------------------------------------


def test_a_usdz_is_stored_converted_and_served(make_receiver, open_stream):
    receiver, client = make_receiver()
    stream = open_stream(client.port)
    assert stream.comments.get(timeout=5) == "connected"
    body = usdz_bytes()
    status, reply = upload(client, body, more="&at=2026-11-14T18:14:40Z&name=scan.usdz&app=effente-scan%200.1")
    assert status == 200 and reply["ok"] is True and reply["duplicate"] is False
    scan = reply["scan"]
    scan_id = scan["scan_id"]
    assert scan["model"]["status"] in ("waiting", "converting", "ready")  # the converter is already at work
    assert dict(scan, model=None) == {
        "scan_id": scan_id, "machine_id": M, "client_id": "test-0001", "at": "2026-11-14T18:14:40Z",
        "received_at": scan["received_at"], "format": "usdz", "file": {"name": "scan.usdz", "bytes": len(body)},
        "app": "effente-scan 0.1", "model": None,
    }

    assert status_events(stream, scan_id, "ready") == ["waiting", "converting", "ready"]
    ready = ok(client, "GET", f"/api/scans/{scan_id}")["scan"]
    assert ready == dict(scan, model={"status": "ready", "reason": "", "bytes": len(box_glb()), "bounds": BOUNDS})
    assert ok(client, "GET", f"/api/machines/{M}/scans")["scans"] == [ready]

    status, headers, raw = client.request("GET", f"/api/scans/{scan_id}/model.glb")
    assert (status, raw, headers["Content-Type"]) == (200, box_glb(), GLB_TYPE)
    status, headers, raw = client.request("GET", f"/api/scans/{scan_id}/file")
    assert (status, raw, headers["Content-Type"]) == (200, body, USDZ_TYPE)
    assert os.listdir(receiver.store.scans.incoming) == []


def test_a_glb_is_ready_at_once(make_receiver, open_stream):
    _, client = make_receiver()
    stream = open_stream(client.port)
    assert stream.comments.get(timeout=5) == "connected"
    status, reply = upload(client, box_glb(), more="&name=model.glb")
    scan = reply["scan"]
    assert status == 200 and scan["format"] == "glb"
    assert scan["model"] == {"status": "ready", "reason": "", "bytes": len(box_glb()), "bounds": BOUNDS}
    assert status_events(stream, scan["scan_id"], "ready") == ["waiting", "ready"]
    for name in ("model.glb", "file"):
        status, headers, raw = client.request("GET", f"/api/scans/{scan['scan_id']}/{name}")
        assert (status, raw, headers["Content-Type"]) == (200, box_glb(), GLB_TYPE)


def test_a_body_of_many_pieces_arrives_whole(client):
    body = usdz_bytes(padding=3_000_000)  # many times what the receiver reads at once
    scan = upload(client, body)[1]["scan"]
    assert scan["file"]["bytes"] == len(body)
    assert client.request("GET", f"/api/scans/{scan['scan_id']}/file")[2] == body


def test_scans_are_listed_newest_first_per_machine(make_receiver):
    receiver, client = make_receiver()
    one_second_apart(receiver)
    first = upload(client, box_glb(), client_id="test-0001")[1]["scan"]
    second = upload(client, box_glb(), client_id="test-0002")[1]["scan"]
    other = upload(client, box_glb(), client_id="test-0003", machine="pump-01")[1]["scan"]
    assert ok(client, "GET", f"/api/machines/{M}/scans")["scans"] == [second, first]
    assert ok(client, "GET", f"/api/machines/{M}/scans?limit=1")["scans"] == [second]
    assert ok(client, "GET", "/api/machines/pump-01/scans")["scans"] == [other]
    assert ok(client, "GET", "/api/machines/nothing-here/scans")["scans"] == []
    assert client.json("GET", f"/api/machines/{M}/scans?limit=0") == (400, {"ok": False, "error": "limit must be from 1 to 200"})


# ---- the state ---------------------------------------------------------------


def test_state_has_each_machines_newest_scan_and_a_machine_with_only_a_scan(make_receiver):
    receiver, client = make_receiver()
    one_second_apart(receiver)
    state = ok(client, "GET", "/api/state")
    assert set(state) == {"ok", "now", "devices", "models", "machines", "alarms", "parts", "probe", "scans", "profiles"}
    assert state["scans"] == {} and state["machines"] == []

    ok(client, "POST", "/api/records", make_record())  # conveyor-07, from a probe
    upload(client, box_glb(), client_id="test-0001", machine="press-01")
    newest = upload(client, box_glb(), client_id="test-0002", machine="press-01")[1]["scan"]
    state = ok(client, "GET", "/api/state")
    assert state["machines"] == [M, "press-01"]
    assert state["scans"] == {"press-01": newest}


# ---- a repeat ----------------------------------------------------------------


def test_a_repeated_client_id_stores_nothing_and_the_connection_stays_usable(make_receiver):
    receiver, client = make_receiver()
    first = upload(client, usdz_bytes())[1]["scan"]
    conn = http.client.HTTPConnection("127.0.0.1", client.port, timeout=10)
    try:
        again = usdz_bytes(padding=300_000)  # not read by the store: dropped, a piece at a time
        status, reply = ask(conn, "POST", f"/api/machines/{M}/scans?client_id=test-0001", again)
        assert (status, reply["duplicate"], reply["scan"]["scan_id"]) == (200, True, first["scan_id"])
        assert reply["scan"]["file"]["bytes"] == first["file"]["bytes"]
        assert ask(conn, "GET", "/api/hello")[0] == 200  # the dropped body was not taken as a request
    finally:
        conn.close()
    assert len(ok(client, "GET", f"/api/machines/{M}/scans")["scans"]) == 1
    assert len([n for n in os.listdir(receiver.store.scans.folder) if n != "incoming"]) == 1


# ---- refused uploads ---------------------------------------------------------


@pytest.mark.parametrize("path, body, error", [
    (f"/api/machines/{M}/scans?client_id=test-0001", b"hello, I am a text file" * 20_000, "not a scan file"),
    (f"/api/machines/{M}/scans?client_id=test-0001", b"", "the scan file is empty"),
    (f"/api/machines/{M}/scans?client_id=test-0001", b"{}", "not a scan file"),
    (f"/api/machines/{M}/scans", box_glb(), "client_id is required"),
    (f"/api/machines/{M}/scans?client_id=short", box_glb(), "client_id is required"),
    ("/api/machines/Conveyor%2007/scans?client_id=test-0001", box_glb(), "machine id must be"),
    (f"/api/machines/{M}/scans?client_id=test-0001&at=yesterday", box_glb(), "at is not a time"),
])
def test_a_refused_upload_keeps_nothing_and_the_connection_stays_usable(make_receiver, path, body, error):
    receiver, client = make_receiver()
    conn = http.client.HTTPConnection("127.0.0.1", client.port, timeout=10)
    try:
        status, reply = ask(conn, "POST", path, body)
        assert status == 400 and reply["ok"] is False and error in reply["error"]
        status, state = ask(conn, "GET", "/api/state")  # the body it did not read was dropped
        assert status == 200 and state["scans"] == {} and state["machines"] == []
    finally:
        conn.close()
    scans = receiver.store.scans
    assert os.listdir(scans.incoming) == [] and os.listdir(scans.folder) == ["incoming"]
    assert not os.path.exists(scans.log_path)


def test_a_body_that_cannot_be_dropped_closes_the_connection(make_receiver):
    receiver, client = make_receiver()
    receiver.store.scans.max_bytes = 100
    post = f"POST /api/machines/{M}/scans?client_id=test-0001 HTTP/1.1\r\nHost: x\r\n"
    # Over the limit: refused before a byte of it is read, so none is sent here.
    status, reply = raw_exchange(client.port, post + "Content-Length: 101\r\n")
    assert status == 400 and "too large: 101 bytes, and the limit is 100" in reply["error"]
    status, reply = raw_exchange(client.port, post)
    assert status == 400 and "Content-Length is required" in reply["error"]
    status, reply = raw_exchange(client.port, post + "Transfer-Encoding: chunked\r\n", b"4\r\nglTF\r\n0\r\n\r\n")
    assert status == 400 and "chunked uploads are not accepted" in reply["error"]
    status, reply = raw_exchange(client.port, post + "Content-Length: lots\r\n")
    assert status == 400 and "Content-Length is not a number" in reply["error"]
    assert ok(client, "GET", "/api/state")["scans"] == {}


def test_an_upload_that_is_cut_off_keeps_nothing(make_receiver):
    receiver, client = make_receiver()
    with socket.create_connection(("127.0.0.1", client.port), timeout=5) as sock:
        sock.sendall(f"POST /api/machines/{M}/scans?client_id=test-0001 HTTP/1.1\r\nHost: x\r\n"
                     f"Content-Length: 4000\r\n\r\n".encode() + b"PK\x03\x04 and then it stops")
        sock.shutdown(socket.SHUT_WR)
        got = b""
        while chunk := sock.recv(65536):
            got += chunk
    assert b" 400 " in got.split(b"\r\n", 1)[0] and b"the upload was cut off after 22 of 4000 bytes" in got
    assert os.listdir(receiver.store.scans.incoming) == []
    assert ok(client, "GET", "/api/state")["scans"] == {}
    assert upload(client, box_glb())[1]["duplicate"] is False  # the same client_id can be sent again


# ---- reading what is not there -----------------------------------------------


def test_the_model_is_a_400_with_its_status_until_it_is_ready(make_receiver, open_stream, blender):
    blender(None)
    _, client = make_receiver()
    stream = open_stream(client.port)
    assert stream.comments.get(timeout=5) == "connected"
    scan = upload(client, usdz_bytes())[1]["scan"]
    assert status_events(stream, scan["scan_id"], "failed") == ["waiting", "converting", "failed"]
    status, reply = client.json("GET", f"/api/scans/{scan['scan_id']}/model.glb")
    assert status == 400 and reply["error"].startswith("the model is failed: Blender is not installed on this laptop")
    assert client.request("GET", f"/api/scans/{scan['scan_id']}/file")[0] == 200  # the scan is kept


@pytest.mark.parametrize("path", ["/api/scans/scan-1", "/api/scans/scan-1/model.glb", "/api/scans/scan-1/file"])
def test_an_unknown_scan(client, path):
    assert client.json("GET", path) == (400, {"ok": False, "error": "no such scan"})


# ---- stopping and starting -----------------------------------------------------


def test_scans_survive_a_restart(make_receiver, tmp_path):
    data = tmp_path / "kept"
    receiver, client = make_receiver(data_dir=data)
    glb = upload(client, box_glb(), client_id="test-0001")[1]["scan"]
    receiver.stop()

    _, client = make_receiver(data_dir=data)
    assert ok(client, "GET", "/api/state")["scans"] == {M: glb}
    assert ok(client, "GET", "/api/state")["machines"] == [M]
    assert client.request("GET", f"/api/scans/{glb['scan_id']}/model.glb")[2] == box_glb()
    status, reply = upload(client, box_glb(), client_id="test-0001")  # the phone retries after the restart
    assert (status, reply["duplicate"], reply["scan"]) == (200, True, glb)


def test_stopping_the_receiver_stops_the_converter(make_receiver):
    receiver, _ = make_receiver()
    converter = receiver.store.scans._converter
    assert converter.submit("scan-x", "missing.usdz", "model.glb") is True
    receiver.stop()
    assert converter.submit("scan-y", "missing.usdz", "model.glb") is False  # no conversion starts after a stop
