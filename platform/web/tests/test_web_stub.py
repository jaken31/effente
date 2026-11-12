"""Tests for the dashboard dev stub: the real receiver fed by a scripted
simulated device, plus the stub's /dev/ controls."""

import http.client
import json
import time

import pytest

import dev_stub


@pytest.fixture
def stub():
    s = dev_stub.Stub(host="127.0.0.1", port=0, step_s=2)
    s.receiver.start()
    yield s
    s.stop()


def call(port, method, path, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    data = None if body is None else json.dumps(body).encode()
    conn.request(method, path, body=data, headers={"Content-Type": "application/json"} if data else {})
    res = conn.getresponse()
    raw = res.read()
    conn.close()
    ctype = res.getheader("Content-Type", "")
    return res.status, (json.loads(raw) if "json" in ctype else raw)


def device_state(port):
    return call(port, "GET", "/api/state")[1]["devices"][dev_stub.DEVICE_ID]


def test_state_empty_before_the_first_tick(stub):
    status, body = call(stub.port, "GET", "/api/state")
    assert status == 200 and body["ok"] is True
    assert body["devices"] == {} and body["models"] == {} and body["machines"] == []


def test_script_walks_through_every_state(stub):
    seen = []
    for _ in range(len(dev_stub.SCRIPT) * stub.device.step_s):
        sent = stub.device.tick()
        dev = device_state(stub.port)
        latest = dev["latest"] or {}
        seen.append((dev["mode"], latest.get("diagnosis") if dev["mode"] == "watching" else None,
                     tuple(p["name"] for p in dev["problems"]), dev["wrong"]))
        assert dev["connected"] is True
        assert dev["problems"] == sent["problems"]
    assert ("watching", "normal", (), False) in seen
    assert ("watching", "normal", ("too_hot",), True) in seen  # a problem while the model says normal
    assert ("watching", "unbalance_like", ("too_hot",), True) in seen
    assert ("watching", "unbalance_like", (), True) in seen
    assert ("watching", "stopped", ("too_hot",), True) in seen
    assert ("recording", None, ("too_hot",), True) in seen  # check-ins
    assert ("watching", "warming_up", (), False) in seen
    assert ("watching", "unrecognised_pattern", ("vibration_above_normal",), True) in seen
    _, body = call(stub.port, "GET", "/api/state")
    model = body["models"][dev_stub.sim_model.MODEL_VERSION]
    assert model["simulated"] is True and len(model["spectrum_edges_hz"]) == 21
    assert body["machines"] == [dev_stub.MACHINE_ID]


def test_problem_keeps_its_since_while_it_stays_on(stub):
    stub.device.jump(1)
    a = stub.device.tick()
    b = stub.device.tick()
    assert a["problems"][0]["since"] == b["problems"][0]["since"]


def test_heat_sources_and_history_without_frames(stub):
    port = stub.port
    stub.device.tick()
    assert call(port, "POST", "/dev/heat?source=imu")[0] == 200
    stub.device.tick()
    assert call(port, "POST", "/dev/heat?source=none")[0] == 200
    stub.device.tick()
    assert call(port, "POST", "/dev/heat?source=laser")[0] == 400
    _, body = call(port, "GET", f"/api/machines/{dev_stub.MACHINE_ID}/records")
    cam, imu, none = body["records"]
    assert cam["heat"]["source"] == "camera" and "frames" not in cam["heat"] and len(cam["heat"]["cells"]) == 48
    assert imu["heat"] == {"source": "imu", "imu_temp_c": imu["heat"]["imu_temp_c"]}
    assert "heat" not in none
    _, body = call(port, "GET", f"/api/machines/{dev_stub.MACHINE_ID}/records?frames=1")
    assert len(body["records"][0]["heat"]["frames"]) == 2


def test_check_ins_carry_no_heat(stub):
    stub.device.jump(5)  # recording: check-ins, not records
    sent = stub.device.tick()
    assert sent["mode"] == "recording" and "heat" not in sent


@pytest.mark.parametrize("method,path", [
    ("POST", "/dev/pause"),  # stub control, takes no body
    ("POST", "/dev/nothing"),
    ("PUT", "/api/nowhere"),
])
def test_unread_body_does_not_spoil_the_next_request(stub, method, path):
    conn = http.client.HTTPConnection("127.0.0.1", stub.port, timeout=5)
    try:
        conn.request(method, path, body=b'{"mode": "recording"}', headers={"Content-Type": "application/json"})
        conn.getresponse().read()
        conn.request("GET", "/api/state")  # same keep-alive connection
        res = conn.getresponse()
        assert res.status == 200 and json.loads(res.read())["ok"] is True
    finally:
        conn.close()


def read_events(port, act, want, timeout=5.0):
    """Open the stream, run act(), collect events until want(events) is true."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    conn.request("GET", "/api/stream")
    res = conn.getresponse()
    assert res.status == 200
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


def test_stream_has_device_mode_and_problem_events(stub):
    def act():
        for _ in range(6 * stub.device.step_s):  # through the recording step
            stub.device.tick()

    events = read_events(stub.port, act, lambda ev: any(n == "device" and d["mode"] == "recording" for n, d in ev))
    names = [n for n, _ in events]
    assert names[:3] == ["model", "device", "record"]
    assert events[1][1]["mode"] == "watching"
    appeared = [d["problem"]["name"] for n, d in events if n == "problem"]
    cleared = [d["name"] for n, d in events if n == "problem_cleared"]
    assert appeared[:2] == ["too_hot", "too_hot"] and cleared[:1] == ["too_hot"]


def test_dev_controls(stub):
    port = stub.port
    assert call(port, "POST", "/dev/step?n=5") == (200, {"ok": True})
    assert stub.device.step() == dev_stub.SCRIPT[5]
    assert call(port, "POST", "/dev/step?n=x")[0] == 400
    assert call(port, "POST", "/dev/pause")[0] == 200 and stub.device.paused is True
    assert call(port, "POST", "/dev/resume")[0] == 200 and stub.device.paused is False
    assert call(port, "POST", "/dev/nothing")[0] == 404
    # The receiver's own routes still work through the stub's handler.
    assert call(port, "POST", "/api/devices/x", {"machine_id": "m", "mode": "display", "at": "t", "wrong": False,
                                                  "problems": []}) == (200, {"ok": True})


def test_device_goes_quiet_when_paused():
    s = dev_stub.Stub(host="127.0.0.1", port=0, quiet_after=0.3)
    s.receiver.start()
    try:
        s.device.jump(1)
        s.device.tick()
        assert device_state(s.port)["wrong"] is True
        time.sleep(1.0)
        dev = device_state(s.port)
        assert dev["connected"] is False and dev["problems"] == [] and dev["wrong"] is False
    finally:
        s.stop()


def test_agent_runs(stub):
    for _ in range(3 * stub.device.step_s):
        stub.device.tick()
    first = stub.device.run_agent()
    steps = list(stub.device.agent_run_steps())
    assert steps[0]["status"] == "running" and steps[0]["tool_calls"] == []
    assert [len(x["tool_calls"]) for x in steps] == [0, 1, 2, 3, 3]
    third = stub.device.run_agent()
    for run in (first, third):
        assert run["status"] == "done" and run["simulated"] is True
    assert first["ok"] is True and set(first["report"]) == {
        "summary", "priority", "cited_features", "recommended_action", "part_reference"}
    assert first["report"]["cited_features"] and set(first["report"]["cited_features"]) <= set(dev_stub.sim_model.FEATURE_NAMES)
    assert third["ok"] is False and third["report"] is None and third["reason"]
    _, body = call(stub.port, "GET", f"/api/machines/{dev_stub.MACHINE_ID}/agent-runs?limit=2")
    assert body["ok"] is True and len(body["runs"]) == 2


def test_static_files_and_traversal(stub):
    status, body = call(stub.port, "GET", "/")
    assert status == 200 and b"dashboard.js" in body
    assert call(stub.port, "GET", "/dashboard_logic.js")[0] == 200
    assert call(stub.port, "GET", "/../../AGENTS.md")[0] == 404
    assert call(stub.port, "GET", "/%2e%2e/%2e%2e/AGENTS.md")[0] == 404


def test_stop_removes_the_data_folder():
    import os

    s = dev_stub.Stub(host="127.0.0.1", port=0)
    s.receiver.start()
    path = s.data_dir
    assert os.path.isdir(path)
    s.stop()
    assert not os.path.exists(path)
