"""Contract sections 6 (alarms and answers) and 7 (settings), against a real receiver."""

from receiver_testkit import MODEL, make_check_in, make_record


def post(client, *records):
    status, reply = client.json("POST", "/api/records", list(records))
    assert status == 200, reply
    return reply


def normal(**over):
    return make_record(diagnosis="normal", abnormal_minutes=0, unknown_score=0.4, fault_types=None, **over)


def abnormal(minutes, **over):
    return make_record(abnormal_minutes=minutes, **over)


def alarms_of(client, machine="conveyor-07", limit=None):
    path = f"/api/machines/{machine}/alarms" + (f"?limit={limit}" if limit is not None else "")
    status, body = client.json("GET", path)
    assert status == 200 and body["ok"] is True
    return body["alarms"]


# ---- section 6: alarms ------------------------------------------------------


def test_abnormal_stretch_is_one_alarm(client):
    client.json("POST", "/api/model", MODEL)
    post(
        client,
        normal(at="2026-11-14T16:00:00Z"),
        abnormal(1, at="2026-11-14T16:01:00Z", unknown_score=1.5, sensitivity=0.5, alarm_line=1.0),
        abnormal(2, at="2026-11-14T16:02:00Z", unknown_score=2.4),
        abnormal(3, at="2026-11-14T16:03:00Z", unknown_score=2.0, diagnosis="looseness_or_rattle"),
    )
    [alarm] = alarms_of(client)
    assert alarm["alarm_id"] == "alarm-2"  # seq of the record that opened it
    assert alarm["open"] is True
    assert (alarm["start"], alarm["end"]) == ("2026-11-14T16:01:00Z", "2026-11-14T16:03:00Z")
    assert alarm["diagnosis"] == "looseness_or_rattle"
    assert alarm["periods"] == 3 and alarm["peak_unknown_score"] == 2.4
    assert (alarm["sensitivity"], alarm["alarm_line"]) == (0.5, 1.0)
    assert alarm["library_entry"] == "conveyor" and alarm["simulated"] is True
    assert alarm["answer"] is None and alarm["answers"] == []

    post(client, normal(at="2026-11-14T16:04:00Z"))
    [alarm] = alarms_of(client)
    assert alarm["open"] is False and alarm["end"] == "2026-11-14T16:03:00Z"


def test_stopped_and_warming_up_close_and_problems_do_not_open(client):
    post(client, abnormal(1), make_record(diagnosis="stopped", unknown_score=None, abnormal_minutes=0))
    post(client, normal(problems=[{"name": "too_hot", "kind": "limit", "text": "over 100 C", "value": 104.5, "since": "x"}]))
    post(client, abnormal(1), make_record(diagnosis="warming_up", unknown_score=None, abnormal_minutes=0))
    alarms = alarms_of(client)
    assert len(alarms) == 2 and not any(a["open"] for a in alarms)


def test_restart_new_device_or_new_model_splits_the_alarm(client):
    post(client, abnormal(1), abnormal(2), abnormal(1))  # minutes went down: device restarted
    post(client, abnormal(2, device_id="effente-02"))
    post(client, abnormal(3, device_id="effente-02", model_version="conveyor-07-20261115"))
    alarms = alarms_of(client)
    assert [a["alarm_id"] for a in alarms] == ["alarm-5", "alarm-4", "alarm-3", "alarm-1"]
    assert [a["open"] for a in alarms] == [True, False, False, False]
    assert alarms[3]["periods"] == 2


def test_alarm_events_and_state(make_receiver, open_stream):
    receiver, client = make_receiver()
    stream = open_stream(receiver.port)
    post(client, abnormal(1))
    opened = stream.next("alarm")
    assert opened["alarm_id"] == "alarm-1" and opened["open"] is True
    post(client, abnormal(1, at="2026-11-14T16:02:12Z"))  # only `end` moves: no event
    post(client, abnormal(2, at="2026-11-14T16:02:13Z"))
    assert stream.next("alarm")["periods"] == 2
    post(client, normal())
    assert stream.next("alarm")["open"] is False

    _, state = client.json("GET", "/api/state")
    assert list(state["alarms"]) == ["alarm-1"]
    assert state["alarms"]["alarm-1"]["end"] == "2026-11-14T16:02:13Z"


def test_state_keeps_open_alarm_and_twenty_newest_per_machine(client):
    for i in range(25):
        post(client, abnormal(1, at=f"2026-11-14T16:{i:02d}:00Z"), normal(at=f"2026-11-14T16:{i:02d}:30Z"))
    post(client, abnormal(1, machine_id="fan-02"))
    _, state = client.json("GET", "/api/state")
    ids = set(state["alarms"])
    assert len(ids) == 21 and "alarm-1" not in ids and "alarm-49" in ids and "alarm-51" in ids
    assert len(alarms_of(client)) == 20
    assert len(alarms_of(client, limit=2000)) == 25
    assert alarms_of(client, limit=2)[0]["start"] == "2026-11-14T16:24:00Z"


def test_answers_are_added_and_newest_counts(make_receiver, open_stream):
    receiver, client = make_receiver()
    stream = open_stream(receiver.port)
    post(client, abnormal(1))
    stream.next("alarm")
    status, reply = client.json("POST", "/api/alarms/alarm-1/answers", {"verdict": "false", "by": "Sam"})
    assert status == 200 and reply["ok"] is True
    first = reply["alarm"]["answer"]
    assert first["verdict"] == "false" and first["at"].endswith("Z")
    assert (first["alarm_id"], first["machine_id"], first["model_version"]) == ("alarm-1", "conveyor-07", "conveyor-07-20261114")
    assert "received_at" in first and first["library_entry"] is None
    assert stream.next("alarm")["answer"]["verdict"] == "false"

    answer = {"verdict": "real", "by": "Sam", "at": "2026-11-14T16:12:03Z", "finding": "loose bolt"}
    _, reply = client.json("POST", "/api/alarms/alarm-1/answers", answer)
    alarm = reply["alarm"]
    assert alarm["answer"]["finding"] == "loose bolt" and alarm["answer"]["at"] == "2026-11-14T16:12:03Z"
    assert [a["verdict"] for a in alarm["answers"]] == ["false", "real"]


def test_bad_answers_are_refused(client):
    post(client, abnormal(1))
    for body in (
        {"by": "Sam"},
        {"verdict": "maybe", "by": "Sam"},
        {"verdict": "real"},
        {"verdict": "real", "by": "  "},
        {"verdict": "real", "by": "Sam", "finding": 3},
        ["real"],
    ):
        status, reply = client.json("POST", "/api/alarms/alarm-1/answers", body)
        assert status == 400 and reply["ok"] is False, body
    status, reply = client.json("POST", "/api/alarms/alarm-99/answers", {"verdict": "real", "by": "Sam"})
    assert status == 400 and "alarm-99" in reply["error"]


def test_alarms_and_answers_survive_a_restart(make_receiver, tmp_path):
    data = tmp_path / "kept"
    receiver, client = make_receiver(data_dir=data)
    post(client, abnormal(1), abnormal(2), normal(), abnormal(1))
    client.json("POST", "/api/alarms/alarm-1/answers", {"verdict": "real", "by": "Sam"})
    before = alarms_of(client)
    receiver.stop()

    _, client = make_receiver(data_dir=data)
    after = alarms_of(client)
    assert [a["alarm_id"] for a in after] == ["alarm-4", "alarm-1"]
    assert after[1]["answer"]["verdict"] == "real" and after[1]["periods"] == 2
    assert [{k: v for k, v in a.items()} for a in after] == before


# ---- section 7: settings ----------------------------------------------------


def test_sensitivity_round_trip(make_receiver, open_stream):
    receiver, client = make_receiver()
    stream = open_stream(receiver.port)
    post(client, abnormal(1, sensitivity=0.5, alarm_line=1.0))
    assert stream.next("settings") == {"device_id": "effente-01", "sensitivity": 0.5, "settings": None}
    assert "settings" not in post(client, abnormal(2, sensitivity=0.5))

    status, reply = client.json("PUT", "/api/devices/effente-01/settings", {"sensitivity": 0.7})
    assert (status, reply) == (200, {"ok": True, "settings": {"sensitivity": 0.7}})
    assert stream.next("settings") == {"device_id": "effente-01", "sensitivity": 0.5, "settings": {"sensitivity": 0.7}}
    assert post(client, abnormal(3, sensitivity=0.5))["settings"] == {"sensitivity": 0.7}
    _, reply = client.json("POST", "/api/devices/effente-01", make_check_in(sensitivity=0.7))
    assert reply == {"ok": True, "settings": {"sensitivity": 0.7}}
    assert stream.next("settings")["sensitivity"] == 0.7  # the device applied it

    _, state = client.json("GET", "/api/state")
    dev = state["devices"]["effente-01"]
    assert (dev["sensitivity"], dev["settings"]) == (0.7, {"sensitivity": 0.7})

    status, reply = client.json("PUT", "/api/devices/effente-01/settings", {"sensitivity": None})
    assert (status, reply) == (200, {"ok": True, "settings": None})
    _, reply = client.json("POST", "/api/devices/effente-01", make_check_in(sensitivity=0.7))
    assert reply == {"ok": True}


def test_bad_settings_are_refused(client):
    for body in ({}, {"sensitivity": 1.5}, {"sensitivity": -0.1}, {"sensitivity": "0.7"}, {"sensitivity": True}, [0.7]):
        status, reply = client.json("PUT", "/api/devices/effente-01/settings", body)
        assert status == 400 and reply["ok"] is False, body
    status, _ = client.json("PUT", "/api/devices/effente-01/settings", raw=b'{"sensitivity": NaN}')
    assert status == 400


def test_unseen_device_can_be_set_and_settings_survive_restart(make_receiver, tmp_path):
    data = tmp_path / "kept"
    receiver, client = make_receiver(data_dir=data)
    client.json("PUT", "/api/devices/effente-09/settings", {"sensitivity": 0.2})
    _, state = client.json("GET", "/api/state")
    dev = state["devices"]["effente-09"]
    assert dev["connected"] is False and dev["sensitivity"] is None and dev["settings"] == {"sensitivity": 0.2}
    post(client, abnormal(1, sensitivity=0.6))
    receiver.stop()

    _, client = make_receiver(data_dir=data)
    _, state = client.json("GET", "/api/state")
    assert state["devices"]["effente-09"]["settings"] == {"sensitivity": 0.2}
    assert state["devices"]["effente-01"]["sensitivity"] == 0.6  # kept while not connected
    assert post(client, make_record(device_id="effente-09"))["settings"] == {"sensitivity": 0.2}
