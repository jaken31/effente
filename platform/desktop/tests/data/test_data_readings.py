"""`state.log` part readings (INTERFACES.md addition 22, contract section 9) on a
DashboardState against a real receiver."""

import datetime as dt

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from receiver_testkit import MODEL, Client, make_record

MACHINE = MODEL["machine_id"]


def now_iso(seconds=0):
    at = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=seconds)
    return at.strftime("%Y-%m-%dT%H:%M:%S.") + f"{at.microsecond // 1000:03d}Z"


def normal(**over):
    return make_record(**{"diagnosis": "normal", "wrong": False, "abnormal_minutes": 0, "unknown_score": 0.4,
                          "readings": {"vib_velocity_mm_s": 2.5}, **over})


@pytest.fixture
def client(make_host):
    host = make_host()
    c = Client(host.port)
    c.host = host
    assert c.json("POST", "/api/model", MODEL)[0] == 200
    assert c.json("POST", "/api/records", normal(at="2020-01-01T00:00:00Z"))[0] == 200  # so the machine is known
    for name, kind in (("Drive roller", "roller"), ("Belt", "belt")):
        assert c.json("POST", f"/api/machines/{MACHINE}/parts", {"name": name, "kind": kind, "by": "Ana"})[0] == 200
    return c


@pytest.fixture
def state(client, make_state):
    return make_state(client.host)


ROLLER, BELT = "part-0001", "part-0002"


def visit(client, n=12, start=0):
    """n records one a second, about now, so the probe stamps them."""
    batch = [normal(at=now_iso(start + i - n)) for i in range(n)]
    assert client.json("POST", "/api/records", batch)[0] == 200


def test_the_probe_is_set_changed_and_cleared(qtbot, state, client):
    log = state.log
    assert log.probe_part() is None
    with qtbot.waitSignal(log.probe_changed, timeout=5000):
        log.set_probe_part(ROLLER)
    assert log.probe_part() == ROLLER
    stored = client.json("GET", "/api/state")[1]["probe"][MACHINE]
    assert (stored["part_id"], stored["by"]) == (ROLLER, state.answerer())
    with qtbot.waitSignal(log.probe_changed, timeout=5000):  # someone else moves it: the stream says so
        client.json("PUT", f"/api/machines/{MACHINE}/probe", {"part_id": BELT, "by": "Ana"})
    qtbot.waitUntil(lambda: log.probe_part() == BELT, timeout=5000)
    with qtbot.waitSignal(log.probe_changed, timeout=5000):
        log.set_probe_part(None)
    assert log.probe_part() is None
    state.set_machine("another-machine")
    client.json("PUT", f"/api/machines/{MACHINE}/probe", {"part_id": ROLLER, "by": "Ana"})
    qtbot.waitUntil(lambda: state.log._probes.get(MACHINE) is not None, timeout=5000)
    assert log.probe_part() is None  # the probe is on a part of the machine shown, or on none


def test_a_refused_probe_part_says_why(qtbot, state):
    with qtbot.waitSignal(state.action_failed, timeout=5000) as failed:
        state.log.set_probe_part("part-9999")
    assert failed.args == [f"Probe part not set: no such part on {MACHINE}: part-9999"]
    assert state.log.probe_part() is None


def test_the_probe_comes_with_the_snapshot(qtbot, client, make_state):
    client.json("PUT", f"/api/machines/{MACHINE}/probe", {"part_id": ROLLER, "by": "Ana"})
    assert make_state(client.host).log.probe_part() == ROLLER


def test_readings_are_fetched_once_and_again_on_a_reading_event(qtbot, state, client):
    log = state.log
    assert log.readings(ROLLER) is None  # asking starts the fetch
    with qtbot.waitSignal(log.readings_changed, timeout=5000) as changed:
        pass
    assert changed.args == [ROLLER]
    first = log.readings(ROLLER)
    assert (first["part_id"], first["readings"], first["summary"]["count"]) == (ROLLER, [], 0)

    with qtbot.waitSignal(log.probe_changed, timeout=5000):
        log.set_probe_part(ROLLER)
    with qtbot.waitSignals([log.readings_changed, log.parts_changed], timeout=5000):
        visit(client)  # the tenth record makes a reading: its event fetches again
    qtbot.waitUntil(lambda: log.readings(ROLLER)["summary"]["count"] == 1, timeout=5000)
    reading = log.readings(ROLLER)["readings"][0]
    assert (reading["velocity_mm_s"], reading["records"], reading["open"]) == (2.5, 12, True)
    assert log.part(ROLLER)["reading"] == {"velocity_mm_s": 2.5, "at": reading["start"], "open": True, "simulated": True}
    assert log.readings(BELT) is None  # another part is its own fetch


def test_readings_since_is_its_own_fetch(qtbot, state, client):
    log = state.log
    client.json("PUT", f"/api/machines/{MACHINE}/probe", {"part_id": ROLLER, "by": "Ana"})
    visit(client)
    log.readings(ROLLER)
    qtbot.waitUntil(lambda: log.readings(ROLLER) is not None, timeout=5000)
    assert log.readings(ROLLER, "2099-01-01T00:00:00Z") is None
    qtbot.waitUntil(lambda: log.readings(ROLLER, "2099-01-01T00:00:00Z") is not None, timeout=5000)
    assert log.readings(ROLLER, "2099-01-01T00:00:00Z")["readings"] == []
    assert len(log.readings(ROLLER)["readings"]) == 1


def test_a_failed_fetch_is_not_asked_for_again_until_a_snapshot(qtbot, state):
    log = state.log
    failed = []
    state.action_failed.connect(failed.append)
    log.readings("part-9999")
    qtbot.waitUntil(lambda: bool(failed), timeout=5000)
    assert failed == ["Readings not loaded: no such part: part-9999"]
    for _ in range(5):
        assert log.readings("part-9999") is None
    qtbot.wait(300)
    assert len(failed) == 1


def test_the_normal_spectrum_of_a_part_and_of_the_machine(qtbot, state, client):
    log = state.log
    assert log.normal_spectrum(ROLLER) is None and log.normal_spectrum() is None
    qtbot.waitUntil(lambda: log.normal_spectrum(ROLLER) is not None and log.normal_spectrum() is not None, timeout=5000)
    own, whole = log.normal_spectrum(ROLLER), log.normal_spectrum()
    assert (own["part_id"], own["ready"], own["records"], own["needed"]) == (ROLLER, False, 0, 300)
    assert (whole["part_id"], whole["machine_id"], whole["records"]) == (None, MACHINE, 1)
    assert own["spectrum_edges_hz"] == MODEL["spectrum_edges_hz"]

    client.json("PUT", f"/api/machines/{MACHINE}/probe", {"part_id": ROLLER, "by": "Ana"})
    visit(client)  # its reading event fetches the machine's normals again
    qtbot.waitUntil(lambda: log.normal_spectrum(ROLLER)["records"] == 12, timeout=5000)
    assert log.normal_spectrum()["records"] == 13
    state.set_machine("another-machine")
    assert log.normal_spectrum() is None  # a new machine is a new fetch
