"""The Machine page's readings logic (readings.py), with no widgets: the projection's words, the tiles,
the chart's axis, the probe line, the evidence and the review and log columns."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.pages.machine import readings as rd

NOW = datetime(2026, 11, 14, 18, 7, tzinfo=UTC)


def proj(status, **fields):
    out = {"status": status, "fits": "unknown_score", "line": {"name": "alarm_line", "unknown_score": 1.0},
           "window": {"from": "2026-11-01T09:10:00Z", "to": "2026-11-14T09:10:00Z", "readings": 12, "days": 12},
           "now_score": 0.62, "slope_per_day": 0.005, "slope_range": [0.0036, 0.0075], "show_days": True,
           "reaches": None, "simulated": False}
    out.update(fields)
    return out


RISING = {"days": [51, 106], "earliest": "2027-01-04", "latest": "2027-02-28"}


# ---- the projection is the contract's sentence for its status, never the page's own -----------------------


@pytest.mark.parametrize("status, fields, sentence", [
    ("rising", {"reaches": RISING},
     "On this trend the model's score reaches its alarm line in 51 to 106 days (4 Jan to 28 Feb)"),
    ("rising", {"reaches": {"days": [None, None], "earliest": None, "latest": None}},
     "On this trend the model's score reaches its alarm line in more than a year"),
    ("over_line", {}, "At or above the model's alarm line now"),
    ("no_clear_rise", {}, "No clear rise yet: the change is inside the day-to-day spread"),
    ("not_rising", {}, "Level or falling over the last 12 days"),
    ("not_enough_history", {}, rd.NOT_ENOUGH),
])
def test_each_status_reads_as_the_contract_says(status, fields, sentence):
    p = rd.projection(proj(status, **fields))
    assert p.sentence == sentence and p.title == "Projection" and not p.simulated


def test_the_line_under_says_what_the_sentence_is_and_that_it_is_not_checked():
    p = rd.projection(proj("rising", reaches=RISING))
    assert p.under == ("A straight line through the model's score over the last 12 days, worked out by code, "
                       "not checked on real faults.")
    assert rd.projection(proj("not_enough_history")).under == ""  # no line was fitted


def test_with_show_days_off_it_is_a_trend_with_no_days_and_no_date():
    for status in ("rising", "over_line", "no_clear_rise", "not_rising", "not_enough_history"):
        p = rd.projection(proj(status, show_days=False, reaches=None))
        assert p.title == "Trend"
        assert "day" not in p.sentence.replace("day-to-day", "").replace("different days", "").replace(
            "last 12 days", "")
        assert not any(month in p.sentence for month in ("Jan", "Feb", "Nov", "Dec"))
    assert rd.projection(proj("rising", show_days=False)).sentence == "Rising toward the model's alarm line"


def test_rising_with_show_days_off_never_shows_days_even_if_the_data_has_them():
    assert rd.projection(proj("rising", show_days=False, reaches=RISING)).sentence == rd.RISING_NO_DAYS


def test_a_simulated_projection_begins_with_simulated():
    p = rd.projection(proj("no_clear_rise", simulated=True))
    assert p.sentence.startswith("Simulated: No clear rise yet") and p.simulated


def test_the_numbers_are_copied_not_worked_out():
    p = rd.projection(proj("rising", reaches={"days": [7, 7], "earliest": "2026-11-21", "latest": "2026-11-21"}))
    assert p.sentence.endswith("in 7 days (21 Nov)")
    p = rd.projection(proj("rising", reaches={"days": [40, None], "earliest": "2026-12-24", "latest": None}))
    assert p.sentence.endswith("in 40 days to more than a year (from 24 Dec)")


def test_no_projection_means_no_sentence():
    assert rd.projection(None) is None
    assert rd.projection({"status": "something new"}) is None


# ---- tiles -------------------------------------------------------------------------------------------------


def reply_for(part_id):
    from fakes import FakeState

    s = FakeState()
    return s.log.readings(part_id), s.log.part(part_id)


def test_tiles_from_the_fake_drive_roller(qapp):
    from fake_partlog import DRIVE_ROLLER

    reply, part = reply_for(DRIVE_ROLLER)
    now, avg, high, change = rd.tiles(reply, part, UTC)
    assert (now.label, now.value, now.sub) == ("Now, mm/s", "2.94", "14 Nov 09:10")
    assert (avg.label, avg.value, avg.sub) == ("Average, mm/s", "2.27", "20 readings")
    assert (high.value, high.sub) == ("2.94", "14 Nov")
    assert (change.label, change.value, change.sub) == ("Change a day", "+0.012", "model score")  # never mm/s
    assert all(tile.simulated for tile in (now, avg, high, change))


def test_tiles_with_one_reading_have_no_change(qapp):
    from fake_partlog import MOTOR_MOUNT

    reply, part = reply_for(MOTOR_MOUNT)
    change = rd.tiles(reply, part, UTC)[3]
    assert (change.value, change.sub, change.simulated) == (rd.DASH, "not enough history", False)


def test_an_open_visit_says_it_is_still_going():
    part = {"reading": {"velocity_mm_s": 3.1, "at": "2026-11-14T16:01:41Z", "open": True, "simulated": False}}
    now = rd.tiles(None, part, UTC)[0]
    assert now.sub == "14 Nov 16:01, still going" and not now.simulated


def test_numbers_are_shown_as_given():
    assert [rd.number(v) for v in (3.1, 2.0, 0.012, None, float("nan"))] == ["3.1", "2", "0.012", rd.DASH, rd.DASH]


# ---- the chart ---------------------------------------------------------------------------------------------


def test_the_mm_s_axis_always_reaches_the_limit_line():
    pts = rd.points([{"start": "2026-11-14T09:10:00Z", "velocity_mm_s": 2.9, "simulated": True}])
    assert rd.y_top(pts) >= rd.LIMIT_MM_S
    assert rd.LIMIT_LABEL == "limit for any machine, 11 mm/s"
    assert rd.y_top(rd.points([{"start": "2026-11-14T09:10:00Z", "velocity_mm_s": 14.0}])) > 14


def test_points_skip_readings_with_no_velocity_and_keep_the_simulated_mark():
    pts = rd.points([
        {"start": "2026-11-13T09:10:00Z", "velocity_mm_s": 2.0, "simulated": True},
        {"start": "2026-11-14T09:10:00Z", "velocity_mm_s": None},
        {"start": "2026-11-12T09:10:00Z", "velocity_mm_s": 1.0, "simulated": False, "abnormal": True},
    ])
    assert [(p.velocity, p.simulated, p.abnormal) for p in pts] == [(1.0, False, True), (2.0, True, False)]


def test_ranges_ask_from_midnight_that_many_days_back():
    assert rd.since("7d", NOW) == "2026-11-07T00:00:00Z"
    assert rd.since("all", NOW) is None


def test_repairs_are_marked():
    marks = rd.repair_marks([{"kind": "repair", "at": "2026-11-14T15:55:00Z"}, {"kind": "note", "at": "x"}], UTC)
    assert [m.label for m in marks] == ["repair, 14 Nov"]


# ---- the parts list and the probe --------------------------------------------------------------------------


def test_a_simulated_number_in_the_list_says_so():
    assert rd.part_number({"reading": {"velocity_mm_s": 2.94, "simulated": True}}) == "2.94 simulated"
    assert rd.part_number({"reading": {"velocity_mm_s": 3.1, "simulated": False}}) == "3.1"
    assert rd.part_number({"reading": None}) == rd.DASH


def test_the_probe_line():
    parts = [{"part_id": "p1", "name": "Drive roller"}]
    assert rd.probe_line(parts, None) == rd.NO_PROBE
    assert rd.probe_line(parts, None, cleared="device moved") == f"The pick was cleared: device moved. {rd.NO_PROBE}"
    assert rd.probe_line(parts, "p1", cleared="device moved") == "Probe on: Drive roller"  # a pick is set: no old reason
    assert rd.probe_line(parts, "p1") == "Probe on: Drive roller"  # no `since` given: no time stood in
    since = {"part_id": "p1", "since": "2026-11-14T16:01:30Z"}
    assert rd.probe_line(parts, "p1", since, UTC) == "Probe on: Drive roller, since 16:01"
    assert rd.probe_line(parts, "p1", dict(since, part_id="p2"), UTC) == "Probe on: Drive roller"


# ---- evidence ----------------------------------------------------------------------------------------------


def test_the_visit_record_is_the_newest_stamped_one_inside_the_newest_reading():
    reply = {"readings": [{"start": "2026-11-14T16:00:00Z", "end": "2026-11-14T16:00:09Z"}]}
    records = [
        {"part_id": "p1", "at": "2026-11-14T16:00:03Z", "n": 1},
        {"part_id": "p1", "at": "2026-11-14T16:00:08Z", "n": 2},
        {"part_id": "p2", "at": "2026-11-14T16:00:09Z", "n": 3},
        {"part_id": "p1", "at": "2026-11-14T16:00:20Z", "n": 4},  # after the visit
        {"at": "2026-11-14T16:00:09Z", "n": 5},  # no part: never given one
    ]
    assert rd.visit_record(records, "p1", reply)["n"] == 2
    assert rd.visit_record(records, "p1", {"readings": []}) is None


def test_moved_keeps_the_order_sent_and_the_announced_text():
    rows = rd.moved({"features": {"vib_band_17": 7.06, "vib_kurtosis": -1.7}}, {"vib_band_17": "vibration 251-315 Hz"})
    assert [(m.text, m.spreads) for m in rows] == [("Vibration 251-315 Hz", 7.06), ("Vibration kurtosis", -1.7)]


def test_spectrum_normal_own_then_machine_then_none():
    rec = {"spectrum": [-4.0] * 20, "simulated": False}
    ready = {"ready": True, "spectrum": [-4.5] * 20, "spectrum_edges_hz": list(range(21)), "simulated": False}
    waiting = {"ready": False, "spectrum": None, "records": 120, "needed": 300, "simulated": False}
    own = rd.spectrum(rec, ready, None)
    assert own.note == "normal" and len(own.normal) == 20 and len(own.edges) == 21
    machine = rd.spectrum(rec, waiting, dict(ready, simulated=True))
    assert machine.note == "normal for the whole machine, not for this part" and machine.simulated
    none = rd.spectrum(rec, waiting, waiting)
    assert none.normal == () and none.note == "Normal not known yet: 120 of 300 normal records"
    assert rd.spectrum({"spectrum": None}, ready, ready) is None


# ---- review and log ----------------------------------------------------------------------------------------


def test_review_rows_show_trend_only_when_the_review_has_one():
    run = {"status": "done", "ok": True, "simulated": True,
           "review": {"what": "w", "how": "h", "since": "2026-11-14T16:05:00Z", "needs_repair": "yes",
                      "cited_alarms": ["alarm-5"], "cited_entries": []}}
    r = rd.review({"name": "Drive roller"}, run, False, UTC)
    assert [k for k, _ in r.rows] == ["Needs repair", "What", "How it comes about", "Since", "Cited"]
    assert r.simulated and r.can_agree and r.rows[0][1] == "Yes (advice)"
    run["review"]["trend"] = "Simulated: Rising toward the model's alarm line"
    assert ("Trend", "Simulated: Rising toward the model's alarm line") in rd.review({}, run, False).rows
    assert rd.agree_draft(run) == ("finding", "w", True)


def test_review_states():
    assert rd.review({"name": "Belt"}, None, False).message.startswith("No review of Belt yet")
    assert rd.review({"name": "Belt"}, None, True).message == "Reviewing Belt…"
    failed = rd.review({"name": "Belt"}, {"status": "done", "ok": False, "reason": "a number not in the history"},
                       False)
    assert "discarded: a number not in the history" in failed.message and not failed.can_agree


def test_entry_rules():
    assert rd.entry_problem("finding", "", None) == "Say whether it needs repair."
    assert rd.entry_problem("finding", "", False) == ""
    assert rd.entry_problem("note", "  ", None) == "Type what happened."
    e = rd.entries([{"at": "2026-11-14T16:05:00Z", "kind": "finding", "text": "", "needs_repair": True,
                     "by": "Sam", "simulated": True}], UTC)[0]
    assert (e.head, e.text, e.simulated) == ("14 Nov 16:05 · Finding · Sam · simulated", "needs repair", True)


def test_the_grid_never_has_more_than_about_a_dozen_lines():
    # Found by a stress run: the grid was one line every 5 mm/s whatever the top, so a wild reading froze the window.
    from app.pages.machine.charts import grid_step

    assert grid_step(4.5) == 2 and grid_step(14) == 2 and grid_step(15) == 5 and grid_step(60) == 5
    for top in (61, 130, 700, 5000, 1e9, 1e300):
        assert top / grid_step(top) <= 12
    assert grid_step(float("inf")) == 1 and grid_step(float("nan")) == 1
