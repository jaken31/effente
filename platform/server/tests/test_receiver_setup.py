"""Machine profiles in the receiver (platform/CONTRACT.md section 10.1, plan section 16 row U1)."""

import json
import shutil

import pytest
from setup_store import lookup

PROFILE = "/api/machines/conveyor-07/profile"
FIVE = {"machine_type": "conveyor", "drive": "belt", "speed_rpm": None, "mounting": "rigid", "sensor_location": None}


def put(client, body, path=PROFILE):
    return client.json("PUT", path, body)


def test_a_profile_is_stored_and_read_back(client, open_stream):
    stream = open_stream(client.port)
    status, reply = put(client, {**FIVE, "by": "Sam", "run_id": "setup-20261114-200300-conveyor-07"})
    assert status == 200 and reply["ok"] is True
    profile = reply["profile"]
    assert {k: profile[k] for k in FIVE} == FIVE
    assert profile["machine_id"] == "conveyor-07" and profile["run_id"] == "setup-20261114-200300-conveyor-07"
    assert profile["confirmed"]["by"] == "Sam" and profile["confirmed"]["at"].endswith("Z")
    assert "by" not in profile
    assert client.json("GET", PROFILE) == (200, {"ok": True, "profile": profile})
    assert stream.next("profile") == profile
    _, state = client.json("GET", "/api/state")
    assert state["profiles"] == {"conveyor-07": profile} and "conveyor-07" in state["machines"]


def test_a_machine_with_no_profile_reads_null(client):
    assert client.json("GET", PROFILE) == (200, {"ok": True, "profile": None})
    _, state = client.json("GET", "/api/state")
    assert state["profiles"] == {} and state["machines"] == []


def test_a_field_left_out_is_empty_and_a_hand_filled_profile_has_no_run(client):
    _, reply = put(client, {"machine_type": "pump", "by": "Sam"})
    profile = reply["profile"]
    assert [profile[k] for k in FIVE] == ["pump", None, None, None, None]
    assert profile["run_id"] is None


@pytest.mark.parametrize("body,error", [
    ({"machine_type": "press", "by": "Sam"}, "'machine_type' must be one of conveyor, fan, pump, motor, other, or null"),
    ({"drive": "chain", "by": "Sam"}, "'drive' must be one of electric_motor_direct, belt, gearbox, or null"),
    ({"mounting": "glued", "by": "Sam"}, "'mounting' must be one of rigid, flexible, or null"),
    ({"sensor_location": "belt", "by": "Sam"}, "'sensor_location' must be one of motor_drive_end, gearbox, frame, or null"),
    ({"speed_rpm": 0, "by": "Sam"}, "'speed_rpm' must be a number above 0"),
    ({"speed_rpm": "1450", "by": "Sam"}, "'speed_rpm' must be a number above 0"),
    ({"speed_rpm": True, "by": "Sam"}, "'speed_rpm' must be a number above 0"),
    ({"speed_rpm": [1500, 1400], "by": "Sam"}, "'speed_rpm' must be a number above 0"),
    ({"speed_rpm": [1400, 1500, 1600], "by": "Sam"}, "'speed_rpm' must be a number above 0"),
    ({"machine_type": "conveyor"}, "missing required field 'by'"),
    ({"machine_type": "conveyor", "by": "  "}, "missing required field 'by'"),
    ({"machine_type": "conveyor", "by": "Sam", "run_id": 7}, "'run_id' must be"),
    (["conveyor"], "must be a JSON object"),
])
def test_a_bad_profile_is_a_400_and_nothing_is_stored(client, body, error):
    status, reply = put(client, body)
    assert status == 400 and reply["ok"] is False and error in reply["error"], reply
    assert client.json("GET", PROFILE)[1]["profile"] is None


@pytest.mark.parametrize("speed", [1450, 29.5, [1400, 1500], [1450, 1450]])
def test_a_speed_is_a_number_or_a_range(client, speed):
    assert put(client, {"speed_rpm": speed, "by": "Sam"})[1]["profile"]["speed_rpm"] == speed


def test_a_second_put_replaces_the_first_and_both_are_kept_in_the_file(make_receiver, tmp_path):
    _, client = make_receiver()
    put(client, {**FIVE, "speed_rpm": 1450, "by": "Sam"})
    _, reply = put(client, {"machine_type": "conveyor", "by": "Alex"})
    profile = client.json("GET", PROFILE)[1]["profile"]
    assert profile == reply["profile"]
    assert profile["drive"] is None and profile["speed_rpm"] is None and profile["confirmed"]["by"] == "Alex"
    lines = (tmp_path / "data" / "machine_profiles.jsonl").read_text().splitlines()
    assert [json.loads(line)["confirmed"]["by"] for line in lines] == ["Sam", "Alex"]


def test_a_restart_gives_the_same_profiles(make_receiver):
    _, client = make_receiver()
    put(client, {**FIVE, "by": "Sam"})
    put(client, {"machine_type": "fan", "by": "Sam"}, "/api/machines/fan-02/profile")
    _, before = client.json("GET", "/api/state")
    _, client2 = make_receiver()  # the same data dir
    _, after = client2.json("GET", "/api/state")
    assert after["profiles"] == before["profiles"] and set(after["profiles"]) == {"conveyor-07", "fan-02"}


def test_the_other_lifecycle_fields_are_stored_and_passed_on(make_receiver):
    other = {"size_class": "small", "operating_modes": ["run", "idle"], "schedule": "two shifts",
             "room_noise": "loud", "site_id": "plant-1"}
    _, client = make_receiver()
    _, reply = put(client, {**FIVE, **other, "by": "Sam"})
    assert {k: reply["profile"][k] for k in other} == other
    _, client2 = make_receiver()
    assert {k: client2.json("GET", PROFILE)[1]["profile"][k] for k in other} == other


def test_the_platform_sets_who_confirmed_and_which_machine_never_the_client(client):
    _, reply = put(client, {**FIVE, "by": "Sam", "machine_id": "other-01",
                            "confirmed": {"at": "2020-01-01T00:00:00Z", "by": "Mallory"}})
    profile = reply["profile"]
    assert profile["machine_id"] == "conveyor-07"
    assert profile["confirmed"]["by"] == "Sam" and profile["confirmed"]["at"] != "2020-01-01T00:00:00Z"


# ---- the lookup (contract 10.2, plan row U2) ---------------------------------

LOOKUP = "/api/library/lookup"


def library_copy(tmp_path):
    """A copy of the real library's manifests, to break."""
    root = tmp_path / "library"
    for entry_id in lookup.entry_ids():
        (root / entry_id).mkdir(parents=True)
        shutil.copy(lookup.ENTRIES_DIR / entry_id / "manifest.json", root / entry_id / "manifest.json")
    return root


def test_the_lookup_route_answers_the_choice_and_stores_nothing(client, open_stream):
    stream = open_stream(client.port)
    status, reply = client.json("POST", LOOKUP, {"machine_type": "conveyor", "drive": "belt"})
    assert status == 200 and reply == {"ok": True, "choice": lookup.choose({"machine_type": "conveyor"})}
    assert reply["choice"]["library_entry"] == "conveyor"
    assert client.json("POST", LOOKUP, {})[1]["choice"]["library_entry"] == "generic_rotating"
    assert client.json("GET", PROFILE)[1]["profile"] is None
    _, state = client.json("GET", "/api/state")
    assert state["profiles"] == {}
    with pytest.raises(AssertionError):
        stream.next("profile", timeout=0.5)


@pytest.mark.parametrize("body,error", [({"machine_type": "press"}, "'machine_type' must be one of"),
                                        ({"speed_rpm": -1}, "'speed_rpm' must be"), ([], "must be a JSON object")])
def test_the_lookup_route_refuses_a_value_outside_its_list(client, body, error):
    status, reply = client.json("POST", LOOKUP, body)
    assert status == 400 and error in reply["error"]


def test_a_stored_profile_carries_the_lookups_choice_never_the_clients(client):
    sent = {"library_entry": "conveyor", "reason": "the agent said so"}
    _, reply = put(client, {"machine_type": "fan", "by": "Sam", "choice": sent})
    choice = reply["profile"]["choice"]
    assert choice == client.json("POST", LOOKUP, {"machine_type": "fan"})[1]["choice"]
    assert choice["library_entry"] == "generic_rotating"


def test_with_no_readable_library_entry_nothing_is_stored(make_receiver, tmp_path):
    receiver, client = make_receiver()
    root = library_copy(tmp_path)
    (root / "generic_rotating" / "manifest.json").write_text("{")
    receiver.store.setup.entries_dir = root
    status, reply = put(client, {"machine_type": "pump", "by": "Sam"})
    assert status == 500 and "generic_rotating" in reply["error"] and reply["ok"] is False
    assert client.json("POST", LOOKUP, {"machine_type": "pump"})[0] == 500
    assert client.json("GET", PROFILE)[1]["profile"] is None
    assert put(client, {"machine_type": "conveyor", "by": "Sam"})[1]["profile"]["choice"]["library_entry"] == "conveyor"
