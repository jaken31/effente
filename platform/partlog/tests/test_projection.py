"""The projection (platform/CONTRACT.md section 9.5): fixed readings with known answers."""

import copy
import datetime as dt
import math

import pytest

import projection as P

NOW = "2026-11-14T18:00:00Z"
BASE = dt.datetime(2026, 11, 14, 12, 0, tzinfo=dt.timezone.utc)


def reading(days_ago, score, line=1.0, version="m1", simulated=False, hours=0.0):
    start = BASE - dt.timedelta(days=days_ago) + dt.timedelta(hours=hours)
    return {
        "part_id": "part-0007",
        "start": start.isoformat().replace("+00:00", "Z"),
        "unknown_score": score,
        "alarm_line": line,
        "model_version": version,
        "simulated": simulated,
    }


def series(scores, **kwargs):
    """One reading a day, the last one today: scores oldest first."""
    n = len(scores)
    return [reading(n - 1 - k, s, **kwargs) for k, s in enumerate(scores)]


def run(readings, repair=None, show_days=True, now=NOW):
    return P.project(readings, repair, now=now, show_days=show_days)


# ---- the five statuses ------------------------------------------------------


def test_rising_hand_worked():
    """Worked by hand: days -2, -1, 0 against 0.5, 0.6, 0.8.

    slope 0.15, now_score 0.7833, residual sum of squares 0.0016667, se
    0.028868, t(0.90, 1 df) 3.0777, range 0.15 -/+ 0.08885. Days to 1.0:
    0.2167 / 0.2388 = 0.91 -> 1, and 0.2167 / 0.0612 = 3.54 -> 4.
    """
    out = run(series([0.5, 0.6, 0.8]))
    assert out["status"] == "rising"
    assert out["fits"] == "unknown_score"
    assert out["line"] == {"name": "alarm_line", "unknown_score": 1.0}
    assert out["now_score"] == 0.78
    assert out["slope_per_day"] == 0.15
    assert out["slope_range"] == [0.0612, 0.239]
    assert out["reaches"] == {"days": [1, 4], "earliest": "2026-11-15", "latest": "2026-11-18"}
    assert out["window"] == {"from": "2026-11-12T12:00:00Z", "to": "2026-11-14T12:00:00Z", "readings": 3, "days": 3}
    assert out["show_days"] is True
    assert out["simulated"] is False


def test_over_line():
    out = run(series([0.5, 0.8, 1.2]))
    assert out["status"] == "over_line"
    assert out["reaches"] is None


def test_over_line_wins_over_falling():
    out = run(series([1.5, 1.3, 1.05]))
    assert out["status"] == "over_line"


def test_no_clear_rise():
    out = run(series([0.40, 0.55, 0.38, 0.52, 0.45, 0.50]))
    assert out["status"] == "no_clear_rise"
    low, high = out["slope_range"]
    assert low < 0 < high
    assert out["reaches"] is None


def test_not_rising():
    out = run(series([0.6, 0.55, 0.5, 0.45, 0.4]))
    assert out["status"] == "not_rising"
    assert out["slope_range"][1] <= 0
    assert out["reaches"] is None


def test_not_enough_history_nulls_every_field_after_window():
    out = run(series([0.5, 0.9]))
    assert out["status"] == "not_enough_history"
    assert out["window"]["days"] == 2
    assert out["line"] == {"name": "alarm_line", "unknown_score": 1.0}
    for key in ("now_score", "slope_per_day", "slope_range", "reaches"):
        assert out[key] is None
    assert out["show_days"] is True


def test_no_readings():
    out = run([])
    assert out["status"] == "not_enough_history"
    assert out["window"] == {"from": None, "to": None, "readings": 0, "days": 0}
    assert out["line"] is None
    assert out["simulated"] is False


# ---- the window -------------------------------------------------------------


def test_two_days_is_not_enough_three_is():
    two_days = [reading(1, 0.5), reading(1, 0.55, hours=3), reading(0, 0.6), reading(0, 0.62, hours=3)]
    assert run(two_days)["status"] == "not_enough_history"
    assert run(two_days)["window"]["readings"] == 4
    assert run(two_days + [reading(2, 0.4)])["status"] != "not_enough_history"


def test_days_are_utc_days_not_24_hour_spans():
    near_midnight = [reading(0, 0.5, hours=-12.5), reading(0, 0.6, hours=-11.5), reading(0, 0.7)]
    # 23:30 on the 13th, 00:30 on the 14th, 12:00 on the 14th: two UTC days.
    assert run(near_midnight)["window"]["days"] == 2


def test_older_than_14_days_left_out():
    readings = [reading(15, 5.0)] + series([0.5, 0.6, 0.8])
    assert run(readings) == run(series([0.5, 0.6, 0.8]))


def test_reading_after_now_left_out():
    readings = series([0.5, 0.6, 0.8]) + [reading(-1, 3.0)]
    assert run(readings) == run(series([0.5, 0.6, 0.8]))


def test_reading_with_no_score_left_out():
    readings = series([0.5, 0.6, 0.8])
    readings.insert(1, reading(1, None, hours=2))
    out = run(readings)
    assert out["window"]["readings"] == 3
    assert out == run(series([0.5, 0.6, 0.8]))


def test_repair_inside_the_window_cuts_it():
    before = [reading(d, 0.9) for d in (6, 5, 4)]
    after = series([0.5, 0.6, 0.8])
    repair = (BASE - dt.timedelta(days=3)).isoformat()
    out = run(before + after, repair)
    assert out["window"]["readings"] == 3
    assert out == run(after)
    # The repair at 12:00 on the 12th also cuts a reading that started at that moment.
    assert run(after, (BASE - dt.timedelta(days=2)).isoformat())["window"]["readings"] == 2


def test_new_model_version_cuts_it():
    old = [reading(d, 2.0, version="m0") for d in (6, 5, 4)]
    new = series([0.5, 0.6, 0.8], version="m1")
    assert run(old + new) == run(new)


def test_newest_model_version_with_a_score_decides():
    new = series([0.5, 0.6, 0.8], version="m1")
    unscored = reading(0, None, version="m2", hours=2)
    assert run(new + [unscored]) == run(new)


def test_line_is_the_newest_readings_alarm_line():
    readings = series([0.5, 0.6, 0.8])
    readings[-1]["alarm_line"] = 0.75  # a changed sensitivity moved the line
    out = run(readings)
    assert out["line"]["unknown_score"] == 0.75
    assert out["status"] == "over_line"


def test_no_alarm_line_is_not_enough_history():
    readings = series([0.5, 0.6, 0.8])
    readings[-1]["alarm_line"] = None
    out = run(readings)
    assert out["status"] == "not_enough_history"
    assert out["line"] is None
    assert out["now_score"] is None


# ---- reaches ----------------------------------------------------------------


def test_reaches_past_a_year_is_null():
    out = run(series([0.10, 0.101, 0.102, 0.103, 0.1041]))
    assert out["status"] == "rising"
    assert out["reaches"]["days"][1] is None
    assert out["reaches"]["latest"] is None


def test_show_days_off_no_status_carries_reaches():
    cases = [[0.5, 0.6, 0.8], [0.5, 0.8, 1.2], [0.4, 0.55, 0.38, 0.52], [0.6, 0.5, 0.4], [0.5, 0.9]]
    statuses = set()
    for scores in cases:
        out = run(series(scores), show_days=False)
        statuses.add(out["status"])
        assert out["reaches"] is None
        assert out["show_days"] is False
    assert statuses == set(P.STATUSES)


def test_default_switch_is_the_module_constant():
    assert P.project(series([0.5, 0.6, 0.8]), now=NOW)["show_days"] is P.SHOW_DAYS


# ---- the rest ---------------------------------------------------------------


def test_simulated_if_any_reading_in_the_window_is():
    readings = series([0.5, 0.6, 0.8])
    readings[0]["simulated"] = True
    assert run(readings)["simulated"] is True
    old = reading(20, 0.5, simulated=True)
    assert run([old] + series([0.5, 0.6, 0.8]))["simulated"] is False


def test_same_input_same_output_and_input_untouched():
    readings = series([0.5, 0.62, 0.58, 0.71, 0.8])
    kept = copy.deepcopy(readings)
    first = run(readings)
    assert run(list(reversed(readings))) == first
    assert run(readings) == first
    assert readings == kept


def test_now_must_be_a_time():
    with pytest.raises(ValueError):
        P.project([], now="yesterday")


@pytest.mark.parametrize("df, expected", [(1, 3.0777), (2, 1.8856), (5, 1.4759), (10, 1.3722), (30, 1.3104)])
def test_t_quantile_against_the_table(df, expected):
    assert P.t_quantile(0.90, df) == pytest.approx(expected, abs=1e-4)


def test_t_quantile_large_df_is_normal():
    assert P.t_quantile(0.90, 100000) == pytest.approx(1.28155, abs=1e-4)
    assert math.isfinite(P.t_quantile(0.999, 1))


# ---- trend_sentence ---------------------------------------------------------


def test_trend_sentences_word_for_word():
    on = {"show_days": True, "simulated": False}
    assert P.trend_sentence({**on, "status": "over_line"}) == "At or above the model's alarm line now"
    assert P.trend_sentence({**on, "status": "no_clear_rise"}) == (
        "No clear rise yet: the change is inside the day-to-day spread")
    assert P.trend_sentence({**on, "status": "not_rising", "window": {"days": 12}}) == (
        "Level or falling over the last 12 days")
    assert P.trend_sentence({**on, "status": "not_enough_history"}) == (
        "Not enough history to say where this is heading: a trend needs readings on at least 3 different days.")
    rising = {**on, "status": "rising",
              "reaches": {"days": [51, 106], "earliest": "2027-01-04", "latest": "2027-02-28"}}
    assert P.trend_sentence(rising) == (
        "On this trend the model's score reaches its alarm line in 51 to 106 days (4 Jan to 28 Feb)")
    far = {**rising, "reaches": {"days": [None, None], "earliest": None, "latest": None}}
    assert P.trend_sentence(far) == "On this trend the model's score reaches its alarm line in more than a year"
    half = {**rising, "reaches": {"days": [51, None], "earliest": "2027-01-04", "latest": None}}
    assert P.trend_sentence(half).endswith("in 51 days to more than a year (from 4 Jan)")


def test_trend_with_show_days_off_has_no_days():
    out = run(series([0.5, 0.6, 0.8]), show_days=False)
    assert P.trend_sentence(out) == "Rising toward the model's alarm line"


def test_trend_simulated_prefix_and_empty():
    out = run(series([0.5, 0.6, 0.8], simulated=True), show_days=False)
    assert P.trend_sentence(out) == "Simulated: Rising toward the model's alarm line"
    assert P.trend_sentence(None) == ""
