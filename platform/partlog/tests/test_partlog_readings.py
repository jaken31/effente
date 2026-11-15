"""Visits and readings as plain functions (contract 9.3)."""

import time

import readings as rd

T0 = 1_800_000_000.0  # 2027-01-15T08:00:00Z
FAR_LATER = T0 + 10 * 86400  # every visit below is closed by then


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + f".{int(t * 1000) % 1000:03d}Z"


def rec(t, v=3.0, score=1.5, diagnosis="normal", seq=None, **over):
    r = {"part_id": "part-0001", "machine_id": "conveyor-07", "at": iso(t), "seq": seq if seq is not None else int(t),
         "diagnosis": diagnosis, "unknown_score": score, "readings": {"vib_velocity_mm_s": v},
         "alarm_line": 1.0, "model_version": "m1", "simulated": False}
    r.update(over)
    return r


def visit(start, n=10, **kw):
    return [rec(start + i, **kw) for i in range(n)]


def no_alarm(record):
    return None


def work_out(records, now=FAR_LATER):
    return rd.readings(sorted(records, key=rd.key), no_alarm, now)


def test_one_visit_makes_one_reading():
    [r] = work_out(visit(T0, 12))
    assert r == {
        "part_id": "part-0001", "machine_id": "conveyor-07", "start": iso(T0), "end": iso(T0 + 11), "records": 12,
        "velocity_mm_s": 3.0, "unknown_score": 1.5, "alarm_line": 1.0, "model_version": "m1",
        "abnormal": False, "alarm_ids": [], "open": False, "simulated": False,
    }


def test_a_gap_of_more_than_10_minutes_starts_a_new_visit():
    first = visit(T0)
    assert len(work_out(first + visit(T0 + 9 + 600))) == 1  # exactly 10 minutes: the same visit
    two = work_out(first + visit(T0 + 9 + 601))
    assert [r["start"] for r in two] == [iso(T0), iso(T0 + 610)]


def test_a_visit_too_short_makes_no_reading():
    assert work_out(visit(T0, 9)) == []
    assert len(work_out(visit(T0, 10))) == 1


def test_stopped_records_and_records_with_no_velocity_are_left_out():
    stopped = visit(T0 + 10, 5, diagnosis="stopped", v=0.0, score=None)
    no_velocity = [rec(T0 + 20 + i, readings={}) for i in range(3)] + [rec(T0 + 30, readings=None)]
    bad_velocity = [rec(T0 + 40, v=True), rec(T0 + 41, v=float("nan"))]
    [r] = work_out(visit(T0, 10, v=2.0) + stopped + no_velocity + bad_velocity)
    assert (r["records"], r["velocity_mm_s"]) == (10, 2.0)
    assert r["end"] == iso(T0 + 41)  # still part of the visit: it spans every stamped record
    assert work_out(visit(T0, 9) + stopped) == []  # they do not make up the count


def test_the_velocity_is_the_median_so_a_knock_does_not_move_it():
    records = visit(T0, 10, v=2.0)
    records[0]["readings"] = {"vib_velocity_mm_s": 40.0}  # the probe knocked while being placed
    records[1]["readings"] = {"vib_velocity_mm_s": 2.456}
    assert work_out(records)[0]["velocity_mm_s"] == 2.0
    even = [rec(T0 + i, v=v) for i, v in enumerate([1, 2, 3, 4, 5, 6, 7, 8, 9, 10.333])]
    assert work_out(even)[0]["velocity_mm_s"] == 5.5


def test_device_moved_records_are_left_out_when_enough_others_remain():
    placing = visit(T0, 4, v=9.0, diagnosis="device_moved")
    lifting = [rec(T0 + 20 + i, v=9.0, diagnosis="unbalance_like", diagnosis_this_minute="device_moved") for i in range(4)]
    [r] = work_out(placing + visit(T0 + 4, 10, v=2.0) + lifting)
    assert (r["records"], r["velocity_mm_s"]) == (10, 2.0)
    assert (r["start"], r["end"]) == (iso(T0), iso(T0 + 23))  # the visit still spans every stamped record


def test_a_visit_that_is_nearly_all_device_moved_keeps_those_records():
    """A part at another tilt than the device learned at: fewer than 10 other records, so nothing is left out."""
    [r] = work_out(visit(T0, 9, v=2.0) + visit(T0 + 9, 30, v=4.0, diagnosis="device_moved"))
    assert (r["records"], r["velocity_mm_s"]) == (39, 4.0)


def test_the_score_is_the_median_without_warming_up_records_and_null_when_none_has_one():
    warming = visit(T0, 6, score=None, diagnosis="warming_up")
    scored = [rec(T0 + 10 + i, score=s) for i, s in enumerate([0.5, 0.7, 0.9, 5.0])]
    [r] = work_out(warming + scored)
    assert (r["records"], r["unknown_score"]) == (10, 0.8)
    assert work_out(visit(T0, 10, score=None, diagnosis="warming_up"))[0]["unknown_score"] is None


def test_the_alarm_line_and_model_come_from_the_newest_record():
    records = visit(T0, 10)
    records[-1].update(alarm_line=0.8, model_version="m2")
    r = work_out(records)[0]
    assert (r["alarm_line"], r["model_version"]) == (0.8, "m2")
    records[-1].pop("alarm_line")
    assert work_out(records)[0]["alarm_line"] is None


def test_abnormal_and_the_alarms_it_belongs_to():
    records = visit(T0, 10) + [rec(T0 + 10 + i, diagnosis="unbalance_like", seq=100 + i) for i in range(3)]
    alarm_of = lambda r: "alarm-100" if r["seq"] < 102 else "alarm-102"
    [r] = rd.readings(sorted(records, key=rd.key), alarm_of, FAR_LATER)
    assert (r["abnormal"], r["alarm_ids"]) == (True, ["alarm-100", "alarm-102"])
    assert work_out(visit(T0, 10, diagnosis="warming_up") + visit(T0 + 20, 3, diagnosis="stopped"))[0]["abnormal"] is False


def test_a_reading_is_open_for_10_minutes_after_its_newest_record():
    records = visit(T0)
    assert work_out(records, now=T0 + 9 + 599)[0]["open"] is True
    assert work_out(records, now=T0 + 9 + 600)[0]["open"] is False


def test_one_simulated_record_marks_the_reading_simulated():
    records = visit(T0)
    assert work_out(records)[0]["simulated"] is False
    records[4]["simulated"] = True
    assert work_out(records)[0]["simulated"] is True


def test_the_newest_reading_skips_a_newest_visit_that_makes_none():
    records = sorted(visit(T0, 10, v=1.0) + visit(T0 + 3600, 4, v=9.0), key=rd.key)
    assert rd.newest_reading(records, no_alarm, FAR_LATER)["velocity_mm_s"] == 1.0
    assert rd.newest_reading([], no_alarm, FAR_LATER) is None
    assert rd.short(rd.newest_reading(records, no_alarm, T0 + 60)) == {
        "velocity_mm_s": 1.0, "at": iso(T0), "open": True, "simulated": False}


def test_the_summary_follows_since_and_limit():
    days = [visit(T0 + d * 86400, v=v) for d, v in enumerate([1.0, 4.0, 2.0, 3.0])]
    every = work_out([r for day in days for r in day])
    assert rd.summary(rd.chosen(every)) == {
        "count": 4, "average_mm_s": 2.5, "highest": {"velocity_mm_s": 4.0, "at": iso(T0 + 86400)},
        "newest": {"velocity_mm_s": 3.0, "at": iso(T0 + 3 * 86400)}, "simulated": False}
    since = rd.chosen(every, since=T0 + 2 * 86400)
    assert rd.summary(since) == {
        "count": 2, "average_mm_s": 2.5, "highest": {"velocity_mm_s": 3.0, "at": iso(T0 + 3 * 86400)},
        "newest": {"velocity_mm_s": 3.0, "at": iso(T0 + 3 * 86400)}, "simulated": False}
    assert [r["velocity_mm_s"] for r in rd.chosen(every, limit=2)] == [2.0, 3.0]
    assert rd.chosen(every, limit=0) == []
    assert rd.summary([]) == {"count": 0, "average_mm_s": None, "highest": None, "newest": None, "simulated": False}
