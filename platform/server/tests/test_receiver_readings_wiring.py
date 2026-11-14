"""Row R0 of docs/scan-and-log-plan.md section 15.2: the routes and calls of contract
sections 9 and 10 are wired in, with stubs behind them. Nothing that worked before may change."""

import pytest
from receiver_store import BadRequest
from receiver_testkit import make_check_in, make_record

NOT_BUILT = (400, {"ok": False, "error": "not built yet"})
ROUTES = [
    ("PUT", "/api/machines/conveyor-07/profile", {"machine_type": "conveyor", "by": "Sam"}),
    ("GET", "/api/machines/conveyor-07/profile", None),
    ("POST", "/api/library/lookup", {"machine_type": "conveyor"}),
]


@pytest.mark.parametrize("method,path,body", ROUTES)
def test_every_new_route_answers_not_built_yet(client, method, path, body):
    assert client.json(method, path, body) == NOT_BUILT
    assert client.json("DELETE", path.split("?")[0])[0] == 405


def test_until_built_the_state_and_the_replies_are_as_before(client):
    status, reply = client.json("POST", "/api/records", make_record())
    assert (status, reply) == (200, {"ok": True, "stored": 1, "need_model": True})
    assert client.json("POST", "/api/devices/effente-01", make_check_in()) == (200, {"ok": True})
    _, state = client.json("GET", "/api/state")
    assert state["probe"] == {} and "profiles" not in state
    device = state["devices"]["effente-01"]
    assert device["settings"] is None and "library_entry" not in device
    assert "part_id" not in client.json("GET", "/api/machines/conveyor-07/records")[1]["records"][0]


def test_the_log_store_stamps_a_record_before_it_is_stored(make_receiver, open_stream):
    receiver, client = make_receiver()
    order = []
    log = receiver.store.partlog

    def stamp(record):
        order.append(("stamp", "seq" in record))
        record["part_id"] = "part-0007"

    log.stamp_record = stamp
    log.record_stored = lambda record: order.append(("stored", record["part_id"]))
    stream = open_stream(receiver.port)
    assert client.json("POST", "/api/records", [make_record(), make_record()])[0] == 200
    assert order == [("stamp", False), ("stamp", False), ("stored", "part-0007"), ("stored", "part-0007")]
    assert stream.next("record")["part_id"] == "part-0007"
    _, client2 = make_receiver()  # the same data dir: the stamp is in the file
    records = client2.json("GET", "/api/machines/conveyor-07/records")[1]["records"]
    assert [r["part_id"] for r in records] == ["part-0007", "part-0007"]


def test_the_stamp_can_refuse_a_batch_and_a_broken_one_stops_nothing(make_receiver, capsys):
    receiver, client = make_receiver()

    def refuse(record):
        raise BadRequest("a record for sample-machine must be simulated")

    def broken(record):
        raise RuntimeError("log store bug")

    receiver.store.partlog.stamp_record = refuse
    assert client.json("POST", "/api/records", [make_record(), make_record()]) == (
        400, {"ok": False, "error": "a record for sample-machine must be simulated"})
    assert client.json("GET", "/api/state")[1]["machines"] == []
    receiver.store.partlog.stamp_record = broken
    status, reply = client.json("POST", "/api/records", make_record())
    assert (status, reply["stored"]) == (200, 1)
    assert client.json("GET", "/api/machines/conveyor-07/records")[1]["records"][0]["seq"] == 1
    assert "log store bug" in capsys.readouterr().err


def test_the_log_store_is_told_the_time_and_a_broken_tick_stops_nothing(make_receiver, capsys):
    receiver, client = make_receiver(quiet_after=0.3)
    ticks = []
    receiver.store.partlog.tick = lambda: ticks.append(1)
    receiver.store.check_quiet()
    assert ticks

    def broken():
        raise RuntimeError("tick bug")

    client.json("POST", "/api/records", make_record())
    receiver.store.partlog.tick = broken
    receiver.store.check_quiet()
    assert "tick bug" in capsys.readouterr().err
    assert client.json("GET", "/api/state")[0] == 200


def test_the_log_store_can_put_the_probe_in_the_state(make_receiver):
    receiver, client = make_receiver()
    probe = {"conveyor-07": {"machine_id": "conveyor-07", "part_id": "part-0007", "since": "2026-11-14T16:01:30Z", "by": "Sam"}}
    receiver.store.partlog.state_part = lambda: {"parts": {}, "probe": probe}
    assert client.json("GET", "/api/state")[1]["probe"] == probe


def test_the_setup_store_adds_to_the_settings_and_to_the_state(make_receiver, open_stream):
    receiver, client = make_receiver()
    setup = receiver.store.setup
    setup.wanted_settings = lambda machine_id: {"library_entry": "conveyor"} if machine_id == "conveyor-07" else {}
    setup.device_part = lambda device: {"library_entry": "generic_rotating"}
    setup.state_part = lambda: {"profiles": {"press-01": {"machine_id": "press-01"}}, "machines": ["press-01"]}
    wanted = {"library_entry": "conveyor"}
    assert client.json("POST", "/api/records", make_record())[1]["settings"] == wanted
    assert client.json("POST", "/api/devices/effente-01", make_check_in())[1]["settings"] == wanted
    stream = open_stream(receiver.port)
    both = {"sensitivity": 0.7, "library_entry": "conveyor"}
    assert client.json("PUT", "/api/devices/effente-01/settings", {"sensitivity": 0.7}) == (200, {"ok": True, "settings": both})
    event = stream.next("settings")
    assert (event["settings"], event["library_entry"]) == (both, "generic_rotating")
    assert client.json("POST", "/api/records", make_record())[1]["settings"] == both
    # A device on a machine with no wanted entry gets only what was set for it, or nothing.
    assert "settings" not in client.json("POST", "/api/records", make_record(device_id="effente-02", machine_id="press-02"))[1]
    _, state = client.json("GET", "/api/state")
    assert state["devices"]["effente-01"]["settings"] == both and state["devices"]["effente-02"]["settings"] is None
    assert state["devices"]["effente-01"]["library_entry"] == "generic_rotating"
    assert state["profiles"] == {"press-01": {"machine_id": "press-01"}}
    assert state["machines"] == ["conveyor-07", "press-01", "press-02"]


def test_the_setup_store_can_refuse_settings_and_a_broken_one_stops_nothing(make_receiver, capsys):
    receiver, client = make_receiver()
    setup = receiver.store.setup

    def refuse(device_id, body):
        if "library_entry" in body:
            raise BadRequest("'library_entry' is set by confirming a machine profile, not here")

    def broken(*args):
        raise RuntimeError("setup store bug")

    setup.check_settings = refuse
    body = {"sensitivity": 0.7, "library_entry": "conveyor"}
    assert client.json("PUT", "/api/devices/effente-01/settings", body) == (
        400, {"ok": False, "error": "'library_entry' is set by confirming a machine profile, not here"})
    assert client.json("GET", "/api/state")[1]["devices"] == {}
    setup.check_settings = setup.wanted_settings = setup.device_part = setup.state_part = broken
    assert client.json("PUT", "/api/devices/effente-01/settings", {"sensitivity": 0.7}) == (
        200, {"ok": True, "settings": {"sensitivity": 0.7}})
    status, reply = client.json("POST", "/api/records", make_record())
    assert (status, reply["settings"]) == (200, {"sensitivity": 0.7})
    assert client.json("GET", "/api/state")[1]["devices"]["effente-01"]["settings"] == {"sensitivity": 0.7}
    assert "setup store bug" in capsys.readouterr().err
