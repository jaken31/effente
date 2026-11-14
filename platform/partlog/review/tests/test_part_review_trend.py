"""The part review and the projection (platform/CONTRACT.md section 9.7).

The review gets the readings summary and the projection. `trend` is copied by
code, word for word. The writer may explain the trend but never gives a number
the history does not hold, nor any time ahead: days, dates, remaining life.
"""

import copy

import pytest

from fake_part_receiver import FakeReceiver
from part_review_cases import MACHINE, history_of, load_case
from partlog.projection import trend_sentence
from partlog.review import history as histories
from partlog.review.agent import run_review
from partlog.review.history import fact_sheet, is_simulated
from partlog.review.review import check_draft, check_review, complete
from partlog.review.standin import stand_in_call

SUMMARY = {"count": 57, "average_mm_s": 2.14, "highest": {"velocity_mm_s": 3.1, "at": "2026-11-14T16:01:41Z"},
           "newest": {"velocity_mm_s": 3.1, "at": "2026-11-14T16:01:41Z"}, "simulated": False}
# Contract 9.5's example, with days shown: the shape, not a machine.
RISING = {"status": "rising", "fits": "unknown_score", "line": {"name": "alarm_line", "unknown_score": 1.0},
          "window": {"from": "2026-11-02T09:10:00Z", "to": "2026-11-14T16:01:41Z", "readings": 12, "days": 12},
          "now_score": 0.62, "slope_per_day": 0.005, "slope_range": [0.0036, 0.0075], "show_days": True,
          "reaches": {"days": [51, 106], "earliest": "2027-01-04", "latest": "2027-02-28"}, "simulated": False}
RISING_SENTENCE = "On this trend the model's score reaches its alarm line in 51 to 106 days (4 Jan to 28 Feb)"


def with_readings(name: str, projection: dict | None = RISING, summary: dict = SUMMARY) -> dict:
    """A fixed history whose part has this readings route reply."""
    data, expected = load_case(name)
    reply = {"part_id": expected["part_id"], "machine_id": MACHINE, "readings": [{"start": "x"}] * 3,
             "summary": copy.deepcopy(summary), "projection": copy.deepcopy(projection)}
    data["readings"] = {expected["part_id"]: reply}
    return histories.part_history(data, expected["part_id"])


def never_draft(**changes) -> dict:
    """A draft for the never-checked part (no alarm, no entry) that passes."""
    draft = {"what": "Idle roller: no alarm and no entry name it.",
             "why": "Its model score is rising toward the alarm line, but nobody has looked at it.",
             "needs_repair": "not_sure", "cited_alarms": [], "cited_entries": []}
    return {**draft, **changes}


def review_of(history: dict, **changes) -> dict:
    return complete(never_draft(**changes), fact_sheet(history))


# ---- what the writer is given

def test_the_fact_sheet_carries_the_summary_and_the_projection_not_the_readings():
    sheet = fact_sheet(with_readings("never_checked"))

    assert sheet["readings"] == {"summary": SUMMARY, "projection": RISING}


def test_no_readings_means_none_on_the_fact_sheet():
    assert fact_sheet(history_of("never_checked"))["readings"] is None
    empty = {**SUMMARY, "count": 0, "average_mm_s": None, "highest": None, "newest": None}
    assert fact_sheet(with_readings("never_checked", summary=empty))["readings"] is None


# ---- trend: copied by code, word for word

def test_trend_is_the_projections_sentence_word_for_word():
    history = with_readings("never_checked")
    result = run_review(MACHINE, "part-0004", load=lambda: history)

    assert result.ok, result
    assert result.review["trend"] == RISING_SENTENCE == trend_sentence(RISING)


@pytest.mark.parametrize("projection, sentence", [
    ({**RISING, "show_days": False, "reaches": None}, "Rising toward the model's alarm line"),
    ({**RISING, "simulated": True}, "Simulated: " + RISING_SENTENCE),
    ({**RISING, "status": "no_clear_rise", "reaches": None},
     "No clear rise yet: the change is inside the day-to-day spread"),
    ({**RISING, "status": "not_rising", "reaches": None}, "Level or falling over the last 12 days"),
    ({**RISING, "status": "over_line", "reaches": None}, "At or above the model's alarm line now"),
    ({**RISING, "status": "not_enough_history", "now_score": None, "slope_per_day": None, "slope_range": None,
      "reaches": None},
     "Not enough history to say where this is heading: a trend needs readings on at least 3 different days."),
])
def test_trend_for_each_status(projection, sentence):
    history = with_readings("never_checked", projection)

    assert review_of(history)["trend"] == sentence
    assert check_review(review_of(history), history) == []


def test_trend_is_empty_with_no_readings():
    history = history_of("never_checked")

    assert review_of(history)["trend"] == ""
    assert check_review(review_of(history), history) == []


def test_a_changed_trend_is_rejected():
    history = with_readings("never_checked")
    review = {**review_of(history), "trend": RISING_SENTENCE.replace("51", "40")}

    assert check_review(review, history) == ["trend is not the projection's sentence, as the code writes it"]


def test_the_writer_may_not_write_trend():
    history = with_readings("never_checked")

    assert check_draft(never_draft(trend=RISING_SENTENCE), history) == ["trend is filled by the code, not by the writer"]


# ---- no figure the code did not supply

@pytest.mark.parametrize("why, number", [
    ("The model score rises 0.02 a day.", "0.02"),  # the slope is 0.005
    ("Its velocity averages 2.1 mm/s.", "2.1"),  # 2.14, rounded by the writer
    ("The score is at 0.7 of the alarm line.", "0.7"),  # 0.62
])
def test_a_review_with_a_figure_the_code_did_not_supply_is_rejected(why, number):
    history = with_readings("never_checked")

    assert check_review(review_of(history, why=why), history) == [
        f"why has a number that is not in the history: {number}"]


def test_quoting_the_summary_and_the_projection_exactly_passes():
    history = with_readings("never_checked")
    why = "57 readings average 2.14 mm/s. The model score is 0.62 and rises 0.005 a day; the alarm line is 1.0."

    assert check_review(review_of(history, why=why), history) == []


@pytest.mark.parametrize("why, said", [
    ("The score reaches the alarm line in 51 to 106 days.", "in 51 to 106 days"),  # the projection's own days
    ("It will reach the line in 40 days.", "will reach"),
    ("Expect an alarm within about 2 weeks.", "within about 2 weeks"),
    ("It has a few months left.", "a few months left"),
    ("Its remaining life is short.", "remaining life"),
    ("Plan a repair before it fails.", "before it fails"),
    ("Plan for the alarm on 4 Jan.", "4 Jan"),
    ("Plan for the alarm by 2027-01-04.", "2027-01-04"),
    ("Plan for the alarm around February 28.", "February 28"),
])
def test_a_time_ahead_is_rejected_even_with_supplied_numbers(why, said):
    history = with_readings("never_checked")

    problems = check_review(review_of(history, why=why), history)

    assert f"why gives a time ahead: {said!r}. Only trend says that, and code writes it" in problems


def test_the_past_is_not_a_time_ahead():
    history = with_readings("never_checked")
    why = "Rising over the last 12 days, after the readings of 14 Nov."

    assert check_review(review_of(history, why=why), history) == []


# ---- a trend alone is never a reason to repair

def test_a_rising_projection_with_no_alarm_and_no_entry_never_gives_yes():
    history = with_readings("never_checked")

    assert check_review(review_of(history, needs_repair="yes"), history) == [
        "needs_repair is yes with no alarm and no entry that says the part needs repair"]
    draft, _ = stand_in_call(fact_sheet(history), {})
    assert draft["needs_repair"] != "yes"
    assert check_draft(draft, history) == []


# ---- simulated travels

def test_a_simulated_projection_or_summary_makes_the_run_simulated():
    assert not is_simulated(with_readings("never_checked"))
    assert is_simulated(with_readings("never_checked", {**RISING, "simulated": True}))
    assert is_simulated(with_readings("never_checked", summary={**SUMMARY, "simulated": True}))


# ---- the readings route

def test_the_readings_route_is_read_for_the_part():
    data, expected = load_case("confirmed_waiting")
    data["readings"] = {expected["part_id"]: {"part_id": expected["part_id"], "readings": [],
                                              "summary": SUMMARY, "projection": RISING}}
    with FakeReceiver(data) as receiver:
        fetched = histories.fetch(receiver.url, MACHINE, expected["part_id"])

    assert fetched["readings"][expected["part_id"]]["projection"] == RISING
    sheet = fact_sheet(histories.part_history(fetched, expected["part_id"]))
    assert sheet["readings"] == {"summary": SUMMARY, "projection": RISING}


def test_a_readings_route_not_built_yet_means_no_readings():
    data, expected = load_case("confirmed_waiting")
    with FakeReceiver(data) as receiver:
        fetched = histories.fetch(receiver.url, MACHINE, expected["part_id"])
        result = run_review(MACHINE, expected["part_id"],
                            load=lambda: histories.part_history(fetched, expected["part_id"]))

    assert fetched["readings"] == {}
    assert result.ok and result.review["trend"] == ""
