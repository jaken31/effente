"""Tests for the dashboard dev stub: routes follow CONTRACT.md shapes, and a
mask PUT changes what the stub emits."""

import http.client
import json
import threading

import pytest

import dev_stub


@pytest.fixture
def stub():
    platform = dev_stub.StubPlatform(mask_delay_s=0.0)
    server = dev_stub.make_server(platform, host="127.0.0.1", port=0)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield platform, server.server_address[1]
    server.stopping = True
    server.shutdown()
    server.server_close()


def call(port, method, path, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    data = None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
    headers = {"Content-Type": "application/json"} if data is not None else {}
    conn.request(method, path, body=data, headers=headers)
    res = conn.getresponse()
    raw = res.read()
    conn.close()
    ctype = res.getheader("Content-Type", "")
    return res.status, (json.loads(raw) if "json" in ctype else raw)


RECORD_KEYS = {
    "device_id", "machine_id", "model_version", "at", "diagnosis", "confidence",
    "unknown_score", "reason", "features", "mask", "spectrum", "simulated", "seq", "received_at",
}


def test_state_empty_before_any_record(stub):
    _, port = stub
    status, body = call(port, "GET", "/api/state")
    assert status == 200
    assert body["ok"] is True
    assert body["devices"] == {} and body["models"] == {} and body["machines"] == []
    assert isinstance(body["now"], str)


def test_state_after_records(stub):
    platform, port = stub
    platform.tick()
    rec = platform.tick()
    _, body = call(port, "GET", "/api/state")
    dev = body["devices"][dev_stub.DEVICE_ID]
    assert set(dev) == {"connected", "last_seen", "model_version", "mask", "mask_version", "latest"}
    assert dev["connected"] is True and dev["mask"] == [] and dev["mask_version"] == 0
    assert dev["latest"]["seq"] == rec["seq"]
    model = body["models"][dev_stub.MODEL_VERSION]
    assert model["simulated"] is True
    assert model["spectrum_hz_max"] == 500
    assert [f["name"] for f in model["features"]] == dev_stub.FEATURE_NAMES
    assert body["machines"] == [dev_stub.MACHINE_ID]


def test_record_shape():
    p = dev_stub.StubPlatform()
    seqs = []
    for _ in range(4 * p.records_per_phase):
        r = p.tick()
        assert set(r) == RECORD_KEYS
        assert r["simulated"] is True
        assert len(r["spectrum"]) == 64 and all(0 <= v <= 1 for v in r["spectrum"])
        assert set(r["features"]) == set(dev_stub.FEATURE_NAMES)
        assert r["diagnosis"] in ("normal", "imbalance", "loose_mount", "unknown")
        assert (r["reason"] == "") == (r["diagnosis"] in ("normal", "unknown"))
        seqs.append(r["seq"])
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)


def test_cycles_all_four_diagnoses():
    p = dev_stub.StubPlatform()
    seen = {p.tick()["diagnosis"] for _ in range(4 * p.records_per_phase)}
    assert seen == {"normal", "imbalance", "loose_mount", "unknown"}


def test_mask_put_get_and_effect(stub):
    platform, port = stub
    platform.tick()
    status, reply = call(port, "PUT", f"/api/devices/{dev_stub.DEVICE_ID}/mask", {"mask": ["once_per_turn"]})
    assert status == 200
    assert reply == {"ok": True, "device_id": dev_stub.DEVICE_ID, "mask": ["once_per_turn"], "mask_version": 1}
    status, got = call(port, "GET", f"/api/devices/{dev_stub.DEVICE_ID}/mask")
    assert got == reply
    # same mask again: version does not move
    _, again = call(port, "PUT", f"/api/devices/{dev_stub.DEVICE_ID}/mask", {"mask": ["once_per_turn"]})
    assert again["mask_version"] == 1

    # Run a full cycle: the masked feature reads 0, records acknowledge the
    # mask, and imbalance never appears while its feature is off.
    recs = [platform.tick() for _ in range(4 * platform.records_per_phase)]
    assert all(r["mask"] == ["once_per_turn"] for r in recs)
    assert all(r["features"]["once_per_turn"] == 0.0 for r in recs)
    diags = {r["diagnosis"] for r in recs}
    assert "imbalance" not in diags
    assert "loose_mount" in diags  # the other fault stays

    _, cleared = call(port, "PUT", f"/api/devices/{dev_stub.DEVICE_ID}/mask", {"mask": []})
    assert cleared["mask"] == [] and cleared["mask_version"] == 2
    recs = [platform.tick() for _ in range(4 * platform.records_per_phase)]
    assert "imbalance" in {r["diagnosis"] for r in recs}
    assert all(r["mask"] == [] for r in recs)


def test_mask_delay_keeps_old_mask_until_due():
    p = dev_stub.StubPlatform(mask_delay_s=10.0)
    p.tick(now=1000.0)
    p.set_mask(["rattle"], now=1000.0)
    assert p.tick(now=1005.0)["mask"] == []
    assert p.tick(now=1010.5)["mask"] == ["rattle"]


@pytest.mark.parametrize("body", [{"mask": ["no_such_feature"]}, {"mask": "rattle"}, {"nope": []}, b"not json"])
def test_mask_put_rejects_bad_body(stub, body):
    _, port = stub
    status, reply = call(port, "PUT", f"/api/devices/{dev_stub.DEVICE_ID}/mask", body)
    assert status == 400
    assert reply["ok"] is False and reply["error"]


def test_mask_unknown_device(stub):
    _, port = stub
    status, reply = call(port, "PUT", "/api/devices/other/mask", {"mask": []})
    assert status == 400 and reply["ok"] is False


def test_history_records(stub):
    platform, port = stub
    for _ in range(10):
        platform.tick()
    _, body = call(port, "GET", f"/api/machines/{dev_stub.MACHINE_ID}/records?limit=4")
    recs = body["records"]
    assert body["ok"] is True and len(recs) == 4
    assert [r["seq"] for r in recs] == [7, 8, 9, 10]  # newest 4, oldest first
    assert all("spectrum" not in r for r in recs)
    _, body = call(port, "GET", f"/api/machines/{dev_stub.MACHINE_ID}/records?limit=2&spectrum=1")
    assert all(len(r["spectrum"]) == 64 for r in body["records"])
    _, body = call(port, "GET", "/api/machines/unknown-machine/records")
    assert body == {"ok": True, "records": []}


def test_agent_runs(stub):
    platform, port = stub
    for _ in range(5):
        platform.tick()
    steps = list(platform.agent_run_steps())
    assert steps[0]["status"] == "running" and steps[0]["tool_calls"] == []
    assert [len(s["tool_calls"]) for s in steps] == [0, 1, 2, 3, 3]
    assert len({s["run_id"] for s in steps}) == 1
    first = platform.run_agent()
    second = platform.run_agent()
    third = platform.run_agent()
    for run in (first, second, third):
        assert run["status"] == "done" and run["simulated"] is True
        assert {"run_id", "machine_id", "at", "status", "ok", "reason", "problems", "tool_calls", "report"} <= set(run)
    assert first["ok"] is True and set(first["report"]) == {
        "summary", "priority", "cited_features", "recommended_action", "part_reference"}
    assert set(first["report"]["cited_features"]) <= set(dev_stub.FEATURE_NAMES)
    assert third["ok"] is False and third["report"] is None and third["reason"]
    _, body = call(port, "GET", f"/api/machines/{dev_stub.MACHINE_ID}/agent-runs?limit=2")
    assert body["ok"] is True and len(body["runs"]) == 2
    ats = [r["at"] for r in body["runs"]]
    assert ats == sorted(ats, reverse=True)  # newest first


def read_events(port, platform, act, want, timeout=5.0):
    """Open the stream, run act(), collect events until want(events) is true."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    conn.request("GET", "/api/stream")
    res = conn.getresponse()
    assert res.status == 200
    assert res.getheader("Content-Type").startswith("text/event-stream")
    assert res.fp.readline() == b": connected\n"
    act()
    events, name = [], None
    try:
        while not want(events):
            line = res.fp.readline().decode().rstrip("\n")
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                events.append((name, json.loads(line[6:])))
    finally:
        conn.close()
    return events


def test_stream_emits_contract_events(stub):
    platform, port = stub

    def act():
        platform.tick()
        call(port, "PUT", f"/api/devices/{dev_stub.DEVICE_ID}/mask", {"mask": ["rattle"]})
        platform.run_agent()

    events = read_events(port, platform, act, lambda ev: any(n == "agent_run" and d["status"] == "done" for n, d in ev))
    names = [n for n, _ in events]
    assert names[:3] == ["model", "device", "record"]
    rec = dict(events)["record"]
    assert rec["simulated"] is True and "seq" in rec
    mask = dict(events)["mask"]
    assert mask == {"ok": True, "device_id": dev_stub.DEVICE_ID, "mask": ["rattle"], "mask_version": 1}
    assert dict(events)["device"]["connected"] is True


def test_device_goes_quiet():
    p = dev_stub.StubPlatform()
    q = p.subscribe()
    p.tick(now=1000.0)
    p.check_connected(now=1004.0)
    p.check_connected(now=1006.0)
    events = []
    while not q.empty():
        events.append(q.get())
    dev = [d for n, d in events if n == "device"]
    assert [d["connected"] for d in dev] == [True, False]
    assert p.state()["devices"][dev_stub.DEVICE_ID]["connected"] is False


def test_static_files_and_traversal(stub):
    _, port = stub
    status, body = call(port, "GET", "/")
    assert status == 200 and b"dashboard.js" in body
    status, _ = call(port, "GET", "/dashboard_logic.js")
    assert status == 200
    status, _ = call(port, "GET", "/../../AGENTS.md")
    assert status == 404
    status, _ = call(port, "GET", "/%2e%2e/%2e%2e/AGENTS.md")
    assert status == 404
