"""DashboardState against a real Receiver on a free port, fed with records
from platform/server/tests/receiver_testkit.py."""

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from app.data import logic
from app.data.host import ReceiverHost
from receiver_testkit import ABOVE_NORMAL, MODEL, TOO_HOT, Client, make_record

DEVICE, MACHINE = MODEL["device_id"], MODEL["machine_id"]


def normal(**over):
    return make_record(**{"diagnosis": "normal", "wrong": False, "unknown_score": 0.4, "abnormal_minutes": 0, **over})


@pytest.fixture
def host(make_host):
    return make_host()


@pytest.fixture
def client(host):
    c = Client(host.port)
    assert c.json("POST", "/api/model", MODEL)[0] == 200
    return c


def test_snapshot_records_and_reads(qtbot, host, client, make_state):
    client.json("POST", "/api/records", normal(at="2026-11-14T16:02:10Z"))
    state = make_state(host)
    qtbot.waitUntil(lambda: len(state.records()) == 1, timeout=5000)
    assert state.machines() == [MACHINE]
    assert state.current_machine() == MACHINE
    assert state.device_id() == DEVICE
    assert state.model()["model_version"] == MODEL["model_version"]
    assert state.headline() == {"state": "ok", "text": "Nothing is wrong"}
    assert state.model_line()["label"] == "Model: nothing unusual yet"
    assert state.freshness()["status"] == "live"
    assert state.freshness()["platform_up"] is True
    assert state.simulated_sources() == ["readings", "model"]

    with qtbot.waitSignal(state.record_added, timeout=5000) as sig:
        client.json("POST", "/api/records", normal(at="2026-11-14T16:02:11Z"))
    assert sig.args[0]["seq"] == 2
    assert state.latest()["seq"] == 2
    assert "spectrum" in state.latest()
    assert [r["seq"] for r in state.records()] == [1, 2]
    assert "spectrum" not in state.records()[0]
    assert "spectrum" in state.records(spectrum=True)[0]
    assert len(state.records(limit=1)) == 1


def test_problems_follow_records_and_events(qtbot, host, client, make_state):
    client.json("POST", "/api/records", normal())
    state = make_state(host)
    with qtbot.waitSignal(state.problems_changed, timeout=5000):
        client.json("POST", "/api/records", normal(problems=[ABOVE_NORMAL], wrong=True))
    # The new limit arrives as a `problem` event after the record; the order stays the contract's.
    client.json("POST", "/api/records", normal(problems=[TOO_HOT, ABOVE_NORMAL], wrong=True))
    qtbot.waitUntil(lambda: len(state.problems()) == 2, timeout=5000)
    qtbot.wait(100)
    assert [p["name"] for p in state.problems()] == ["too_hot", "vibration_above_normal"]
    assert state.headline()["state"] == "wrong"
    assert state.model_line()["label"] == "Model: nothing unusual yet"  # problems never hide the model's panel
    assert [logic.kind_label(p["kind"]) for p in state.problems()] == [
        "limit for any machine", "beyond this machine's normal"]
    client.json("POST", "/api/records", normal(problems=[TOO_HOT], wrong=True))
    qtbot.waitUntil(lambda: [p["name"] for p in state.problems()] == ["too_hot"], timeout=5000)


def test_alarms_open_and_take_answers(qtbot, host, client, make_state, monkeypatch):
    monkeypatch.setenv("EFFENTE_ANSWERER", "Sam")
    state = make_state(host)
    with qtbot.waitSignal(state.alarms_changed, timeout=5000):
        client.json("POST", "/api/records", make_record(abnormal_minutes=1))
    [alarm] = state.unanswered_alarms()
    assert alarm["alarm_id"] == "alarm-1" and alarm["open"] is True
    assert state.answerer() == "Sam"

    with qtbot.waitSignal(state.alarms_changed, timeout=5000):
        state.answer_alarm("alarm-1", "real", finding="loose bolt")
    assert state.unanswered_alarms() == []
    answer = state.alarms()[0]["answer"]
    assert (answer["verdict"], answer["by"], answer["finding"]) == ("real", "Sam", "loose bolt")
    assert logic.verdict_text(answer["verdict"]) == "Real problem"
    stored = client.json("GET", f"/api/machines/{MACHINE}/alarms")[1]["alarms"][0]
    assert stored["answer"]["by"] == "Sam"

    # A second answer replaces the verdict; the closed alarm stays answered.
    state.answer_alarm("alarm-1", "false")
    qtbot.waitUntil(lambda: state.alarms()[0]["answer"]["verdict"] == "false", timeout=5000)
    qtbot.wait(200)  # the reply and the stream event both arrive; neither may undo the other
    assert state.alarms()[0]["answer"]["verdict"] == "false"
    assert len(state.alarms()[0]["answers"]) == 2

    # Newest start first; a new stretch opens a second alarm.
    client.json("POST", "/api/records", normal())
    client.json("POST", "/api/records", make_record(at="2026-11-14T16:10:00Z", abnormal_minutes=1))
    qtbot.waitUntil(lambda: [a["alarm_id"] for a in state.alarms()] == ["alarm-3", "alarm-1"], timeout=5000)
    assert [a["alarm_id"] for a in state.unanswered_alarms()] == ["alarm-3"]


def test_answer_errors_are_reported(qtbot, host, client, make_state):
    state = make_state(host)
    with qtbot.waitSignal(state.action_failed, timeout=5000) as sig:
        state.answer_alarm("alarm-404", "real")
    assert "no such alarm" in sig.args[0]
    with qtbot.waitSignal(state.action_failed, timeout=1000) as sig:
        state.answer_alarm("alarm-1", "maybe")
    assert "verdict" in sig.args[0]


def test_sensitivity_round_trip(qtbot, host, client, make_state):
    client.json("POST", "/api/records", normal(sensitivity=0.5, alarm_line=1.0))
    state = make_state(host)
    assert state.sensitivity() == {"reported": 0.5, "wanted": None}

    with qtbot.waitSignal(state.settings_changed, timeout=5000):
        state.set_sensitivity(0.7)
    qtbot.waitUntil(lambda: state.sensitivity() == {"reported": 0.5, "wanted": 0.7}, timeout=5000)
    assert client.json("GET", "/api/state")[1]["devices"][DEVICE]["settings"] == {"sensitivity": 0.7}
    # The reply to the device's next record carries it; the device then reports it.
    reply = client.json("POST", "/api/records", normal(sensitivity=0.5))[1]
    assert reply["settings"] == {"sensitivity": 0.7}
    with qtbot.waitSignal(state.settings_changed, timeout=5000):
        client.json("POST", "/api/records", normal(sensitivity=0.7, alarm_line=logic.alarm_line(0.7)))
    qtbot.waitUntil(lambda: state.sensitivity()["reported"] == 0.7, timeout=5000)

    with qtbot.waitSignal(state.settings_changed, timeout=5000):
        state.set_sensitivity(None)
    qtbot.waitUntil(lambda: state.sensitivity() == {"reported": 0.7, "wanted": None}, timeout=5000)

    with qtbot.waitSignal(state.action_failed, timeout=1000):
        state.set_sensitivity(1.5)


def test_reconnect_reads_state_again(qtbot, tmp_path, make_host, make_state):
    """The platform restarts. The stream reconnects and GET /api/state is read
    again, so a record stored while the app was not listening still shows."""
    data = tmp_path / "data"
    first = make_host(data_dir=data)
    port = first.port
    client = Client(port)
    client.json("POST", "/api/model", MODEL)
    client.json("POST", "/api/records", normal())
    state = make_state(first)
    connects = state.reader.connects
    with qtbot.waitSignal(state.connection_changed, timeout=5000, check_params_cb=lambda up: up is False):
        first.stop()
    assert state.headline()["state"] == "not_live"

    # Stored while nobody listens: no stream event will ever carry it.
    from receiver_store import Store

    Store(str(data)).add_records(normal(at="2026-11-14T16:03:00Z", problems=[TOO_HOT], wrong=True))
    second = make_host(data_dir=data, port=port)
    with qtbot.waitSignal(state.connection_changed, timeout=10000, check_params_cb=lambda up: up is True):
        pass
    qtbot.waitUntil(lambda: state.latest() is not None and state.latest()["seq"] == 2, timeout=5000)
    assert state.reader.connects == connects + 1
    qtbot.waitUntil(lambda: [r["seq"] for r in state.records()] == [1, 2], timeout=5000)
    # And the stream works again.
    with qtbot.waitSignal(state.record_added, timeout=5000):
        Client(second.port).json("POST", "/api/records", normal(at="2026-11-14T16:03:01Z"))


def test_stream_drop_reconnects(qtbot, host, client, make_state):
    state = make_state(host)
    seen = []
    state.connection_changed.connect(seen.append)
    state.reader._drop()
    qtbot.waitUntil(lambda: seen[-2:] == [False, True], timeout=10000)
    with qtbot.waitSignal(state.record_added, timeout=5000):
        client.json("POST", "/api/records", normal())


def test_state_waits_for_a_receiver_that_is_not_up_yet(qtbot, tmp_path, make_state):
    probe = ReceiverHost(data_dir=tmp_path / "d", port=0, host="127.0.0.1")
    probe.start()
    port = probe.port
    probe.stop()
    late = ReceiverHost(data_dir=tmp_path / "d", port=port, host="127.0.0.1")
    state = make_state(late, wait=False)
    qtbot.waitUntil(lambda: state.view()["platform_lost"], timeout=5000)
    late.start()
    try:
        qtbot.waitUntil(lambda: state.view()["platform_up"], timeout=10000)
    finally:
        late.stop()


def test_set_machine_switches_everything(qtbot, host, client, make_state):
    client.json("POST", "/api/records", normal())
    other = dict(MODEL, device_id="effente-02", machine_id="fan-02", model_version="fan-02-v1")
    client.json("POST", "/api/model", other)
    client.json("POST", "/api/records", normal(device_id="effente-02", machine_id="fan-02", model_version="fan-02-v1",
                                              problems=[TOO_HOT], wrong=True))
    state = make_state(host)
    assert state.machines() == [MACHINE, "fan-02"] or state.machines() == sorted([MACHINE, "fan-02"])
    assert state.current_machine() == "fan-02"  # most recently heard
    with qtbot.waitSignal(state.snapshot_changed, timeout=5000):
        state.set_machine(MACHINE)
    assert state.device_id() == DEVICE
    assert state.problems() == []
    qtbot.waitUntil(lambda: [r["machine_id"] for r in state.records()] == [MACHINE], timeout=5000)
    with qtbot.assertNotEmitted(state.record_added, wait=300):
        client.json("POST", "/api/records", normal(device_id="effente-02", machine_id="fan-02",
                                                  model_version="fan-02-v1"))
    assert state.current_machine() == MACHINE  # picked by hand: stays


def test_fake_state_has_the_same_interface():
    from app.data.state import DashboardState
    from fakes import FakeState

    public = {n for n in dir(DashboardState) if not n.startswith("_")}
    missing = {n for n in public - set(dir(FakeState)) if n not in ("start", "stop", "reader", "api", "host")}
    assert missing == set()


def test_stored_count_reads_the_embedded_receiver_beyond_the_history_cap(qtbot, host, client, make_state):
    batch = [normal(at=f"2026-11-14T16:{i // 60:02d}:{i % 60:02d}Z") for i in range(logic.HISTORY_CAP + 20)]
    client.json("POST", "/api/records", batch)
    state = make_state(host)
    qtbot.waitUntil(lambda: len(state.records()) == logic.HISTORY_CAP, timeout=5000)
    assert state.stored_count() == logic.HISTORY_CAP + 20

    from app.data.state import DashboardState

    elsewhere = DashboardState(host.url)  # a receiver in another process: no count to read
    assert elsewhere.stored_count() is None
