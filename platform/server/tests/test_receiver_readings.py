"""Readings, and one part's history (contract 9.3), over HTTP.

Uses the fake-clock receiver of test_receiver_probe.py. Records are sent at
the fake clock's time, one a second, as the device sends them.
"""

import pytest
from receiver_testkit import make_record
from test_receiver_probe import T0, iso, ok, rig  # noqa: F401  (rig is a fixture)

M = "conveyor-07"


def velocity_record(t, v=3.0, **over):
    body = dict(diagnosis="normal", abnormal_minutes=0, unknown_score=0.5, wrong=False, readings={"vib_velocity_mm_s": v})
    body.update(over)
    return make_record(at=iso(t), **body)


def send_visit(rig, n, v=3.0, **over):
    """n records one a second, at the fake clock, moving it on."""
    for _ in range(n):
        ok(rig.client, "POST", "/api/records", velocity_record(rig.clock.t, v, **over))
        rig.clock.t += 1


def get_readings(rig, part_id, query=""):
    return ok(rig.client, "GET", f"/api/parts/{part_id}/readings{query}")


def part_reading(rig, part_id):
    parts = ok(rig.client, "GET", f"/api/machines/{M}/parts?removed=1")["parts"]
    return next(p for p in parts if p["part_id"] == part_id)["reading"]


def test_a_visit_makes_a_reading_on_the_part(rig):
    assert part_reading(rig, rig.roller) is None
    rig.pick(rig.roller)
    send_visit(rig, 12, v=2.5)
    reply = get_readings(rig, rig.roller)
    [r] = reply["readings"]
    assert (reply["part_id"], reply["machine_id"], reply["projection"]) == (rig.roller, M, None)
    assert (r["start"], r["end"], r["records"], r["velocity_mm_s"], r["unknown_score"]) == (
        iso(T0), iso(T0 + 11), 12, 2.5, 0.5)
    assert (r["open"], r["simulated"], r["abnormal"]) == (True, True, False)  # make_record is simulated
    assert part_reading(rig, rig.roller) == {"velocity_mm_s": 2.5, "at": iso(T0), "open": True, "simulated": True}
    assert ok(rig.client, "GET", "/api/state")["parts"][M][0]["reading"]["velocity_mm_s"] == 2.5


def test_a_record_with_no_part_is_in_no_parts_history(rig):
    send_visit(rig, 15, v=9.0)  # no pick yet
    rig.pick(rig.roller)
    send_visit(rig, 10, v=1.0)
    rig.pick(None)
    send_visit(rig, 15, v=9.0)
    [r] = get_readings(rig, rig.roller)["readings"]
    assert (r["records"], r["velocity_mm_s"]) == (10, 1.0)
    assert get_readings(rig, rig.belt)["readings"] == []
    assert part_reading(rig, rig.belt) is None
    assert len(ok(rig.client, "GET", f"/api/machines/{M}/records?limit=100")["records"]) == 40  # still stored


def test_the_abnormal_records_name_their_alarm(rig):
    rig.pick(rig.roller)
    send_visit(rig, 10)
    send_visit(rig, 3, diagnosis="unbalance_like", abnormal_minutes=3)
    [r] = get_readings(rig, rig.roller)["readings"]
    alarm = ok(rig.client, "GET", f"/api/machines/{M}/alarms")["alarms"][0]
    assert (r["abnormal"], r["alarm_ids"], r["records"]) == (True, [alarm["alarm_id"]], 13)


def test_a_restart_gives_the_same_readings(rig):
    rig.pick(rig.roller)
    send_visit(rig, 10, v=2.0)
    rig.clock.t += 3600
    rig.pick(rig.roller)  # the first pick cleared itself in the hour
    send_visit(rig, 10, v=3.0)
    before = get_readings(rig, rig.roller)
    rig.restart()
    after = get_readings(rig, rig.roller)
    assert after == before and len(after["readings"]) == 2
    assert part_reading(rig, rig.roller)["velocity_mm_s"] == 3.0


def test_the_summary_follows_since_and_limit(rig):
    rig.pick(rig.roller)
    for v in (1.0, 4.0, 2.0):
        send_visit(rig, 10, v=v)
        rig.clock.t += 3600
        rig.pick(rig.roller)  # the pick would have cleared itself in the hour
    every = get_readings(rig, rig.roller)
    assert every["summary"]["count"] == 3 and every["summary"]["average_mm_s"] == 2.33
    second = every["readings"][1]["start"]
    since = get_readings(rig, rig.roller, f"?since={second}")
    assert [r["velocity_mm_s"] for r in since["readings"]] == [4.0, 2.0]
    assert since["summary"]["highest"] == {"velocity_mm_s": 4.0, "at": second}
    assert get_readings(rig, rig.roller, "?limit=1")["summary"]["newest"]["velocity_mm_s"] == 2.0


@pytest.mark.parametrize("query,error", [
    ("?since=yesterday", "since must be an ISO 8601 time"),
    ("?limit=lots", "limit must be a whole number"),
    ("?limit=-1", "limit must be 0 or more"),
])
def test_a_bad_query_is_refused(rig, query, error):
    assert rig.client.json("GET", f"/api/parts/{rig.roller}/readings{query}") == (400, {"ok": False, "error": error})


def test_an_unknown_part_is_refused_and_a_removed_one_still_answers(rig):
    assert rig.client.json("GET", "/api/parts/part-9999/readings") == (400, {"ok": False, "error": "no such part: part-9999"})
    rig.pick(rig.roller)
    send_visit(rig, 10)
    ok(rig.client, "PUT", f"/api/parts/{rig.roller}", {"removed": True, "by": "Sam"})
    assert len(get_readings(rig, rig.roller)["readings"]) == 1


def test_a_reading_with_one_real_record_among_simulated_ones_is_simulated(rig):
    rig.pick(rig.roller)
    send_visit(rig, 9, simulated=False)
    send_visit(rig, 1, simulated=True)
    reply = get_readings(rig, rig.roller)
    assert reply["readings"][0]["simulated"] is True and reply["summary"]["simulated"] is True
    rig.clock.t += 3600
    rig.pick(rig.belt)
    send_visit(rig, 10, simulated=False)
    reply = get_readings(rig, rig.belt)
    assert reply["readings"][0]["simulated"] is False and reply["summary"]["simulated"] is False


def test_the_reading_event_is_rarer_than_records_and_announces_the_close(rig, open_stream):
    stream = open_stream(rig.receiver.port)
    rig.pick(rig.roller)
    send_visit(rig, 9)
    send_visit(rig, 1)  # the tenth record makes the reading
    first = stream.next("reading")
    assert (first["part_id"], first["machine_id"], first["reading"]["records"]) == (rig.roller, M, 10)
    send_visit(rig, 25)  # 25 seconds of growth: at most one event every 10 of them
    grown = []
    while True:
        try:
            grown.append(stream.next("reading", timeout=0.5)["reading"]["records"])
        except AssertionError:
            break
    assert 1 <= len(grown) <= 3, grown
    rig.clock.t += 10
    rig.tick()  # the growth held back is sent once its 10 seconds are up
    assert stream.next("reading")["reading"]["records"] == 35
    rig.tick()
    rig.clock.t += 600
    rig.tick()
    closed = stream.next("reading")["reading"]
    assert (closed["records"], closed["open"]) == (35, False)
    assert part_reading(rig, rig.roller)["open"] is False
    rig.tick()
    with pytest.raises(AssertionError):
        stream.next("reading", timeout=0.5)  # closed once, announced once
