"""The three checks every report must pass besides its citations: shape, names and numbers."""

import pytest

from device.agent.report import check_report

FEATURES = ["vib_1x", "vib_harm_3_10"]


def record(diagnosis="unbalance_like", machine_id="conveyor-07", **features):
    return {
        "device_id": "effente-01",
        "machine_id": machine_id,
        "at": "2026-11-14T16:00:00Z",
        "diagnosis": diagnosis,
        "unknown_score": 0.1,
        "features": features,
    }


# Two unbalance-like windows: vib_1x moved (peak 9.6), vib_harm_3_10 did not (peak 0.6).
RECORDS = [record(vib_1x=9.6, vib_harm_3_10=0.6), record(vib_1x=-8.4, vib_harm_3_10=0.24)]


def report(**changes):
    good = {
        "summary": "Imbalance on the fan.",
        "priority": "medium",
        "cited_features": ["vib_1x"],
        "recommended_action": "Clean and rebalance the blades.",
        "part_reference": "",
    }
    return {**good, **changes}


def check(candidate, records=RECORDS):
    return check_report(candidate, records, FEATURES)


# 1. Shape


def test_shape_passes_a_report_with_every_key_and_the_right_types():
    assert check(report()) == []
    assert check(report(cited_features=[], priority="none")) == []


@pytest.mark.parametrize("key", ["summary", "priority", "cited_features", "recommended_action", "part_reference"])
def test_shape_rejects_a_missing_key(key):
    candidate = report()
    del candidate[key]
    assert check(candidate) == [f"missing key: {key}"]


@pytest.mark.parametrize(
    "changes, problem",
    [
        ({"summary": None}, "summary must be text: None"),
        ({"priority": 2}, "priority must be text: 2"),
        ({"recommended_action": ["Clean the blades."]}, "recommended_action must be text: ['Clean the blades.']"),
        ({"part_reference": 6204}, "part_reference must be text: 6204"),
        ({"cited_features": "vib_1x"}, "cited_features must be a list of names: 'vib_1x'"),
        ({"cited_features": ["vib_1x", 3]}, "cited_features must be a list of names: ['vib_1x', 3]"),
    ],
)
def test_shape_rejects_a_wrong_type(changes, problem):
    assert check(report(**changes)) == [problem]


def test_shape_rejects_an_extra_key():
    assert check(report(confidence=0.9)) == ["unexpected key: confidence"]


@pytest.mark.parametrize("not_a_report", [None, "Imbalance on the fan.", [report()]])
def test_shape_rejects_a_report_that_is_not_an_object(not_a_report):
    assert check(not_a_report) == [f"report is not an object: {type(not_a_report).__name__}"]


def test_shape_lists_every_problem_and_stops_the_other_checks():
    candidate = report(priority=None, summary="vib_harm_3_10 moved 99 spreads.", extra=1)
    assert check(candidate) == ["priority must be text: None", "unexpected key: extra"]


def test_allowed_values_are_still_checked_on_a_well_shaped_report():
    assert check(report(priority="urgent")) == ["unknown priority: 'urgent'"]
    assert check(report(cited_features=["bearing_wear"])) == ["cites unverified feature: bearing_wear"]


# 2. A reading named in the free text must be cited


def test_names_passes_a_cited_reading_in_the_summary_and_the_action():
    candidate = report(summary="vib_1x moved on the fan.", recommended_action="Rebalance, then recheck vib_1x.")
    assert check(candidate) == []


def test_names_rejects_an_uncited_reading_in_the_summary():
    candidate = report(summary="vib_1x and vib_harm_3_10 both moved.")
    assert check(candidate) == ["summary names a reading that is not cited: vib_harm_3_10"]


def test_names_rejects_an_uncited_reading_in_the_recommended_action():
    candidate = report(cited_features=[], recommended_action="Watch vib_1x.")
    assert check(candidate) == ["recommended_action names a reading that is not cited: vib_1x"]


def test_names_rejects_an_uncited_reading_that_is_not_in_the_feature_table():
    records = [record(vib_1x=9.6, bearing_wear=7.0)]
    assert check(report(summary="bearing_wear is high."), records) == [
        "summary names a reading that is not cited: bearing_wear"
    ]


def test_names_passes_an_uncited_reading_that_was_switched_off():
    # Saying a reading was off does not claim it moved.
    records = [record(vib_1x=9.6), dict(record(vib_1x=9.6, vib_harm_3_10=0.0), mask=["vib_harm_3_10"])]
    candidate = report(summary="Imbalance, with vib_harm_3_10 switched off in some windows.")
    assert check(candidate, records) == []
    assert check(candidate) == ["summary names a reading that is not cited: vib_harm_3_10"]


def test_names_ignores_a_reading_name_inside_a_longer_word():
    records = [record(vib_1x=9.6, temp=7.0)]
    assert check(report(summary="The temperature looks fine."), records) == []


def test_names_does_not_check_part_reference():
    assert check(report(cited_features=[], part_reference="vib_1x sensor bracket")) == []


# 3. A number in the free text must be in the readings


def test_numbers_passes_values_the_readings_hold():
    candidate = report(
        summary="2 windows, both unbalance-like. vib_1x peaked at 9.6 and averaged 0.6.",
        recommended_action="Rebalance. Readings from 2026-11-14T16:00:00Z.",
    )
    assert check(candidate) == []


def test_numbers_passes_a_single_window_value_with_its_sign():
    assert check(report(summary="vib_1x read -8.4 in one window.")) == []


def test_numbers_rejects_a_value_in_the_summary_the_readings_do_not_hold():
    candidate = report(summary="vib_1x peaked at 12.5 over 2 windows.")
    assert check(candidate) == ["summary has a number that is not in the readings: 12.5"]


def test_numbers_rejects_a_value_in_the_recommended_action():
    candidate = report(recommended_action="Rebalance within 30 days.")
    assert check(candidate) == ["recommended_action has a number that is not in the readings: 30"]


def test_numbers_rejects_a_rounded_value():
    assert check(report(summary="vib_1x peaked near 10.")) == ["summary has a number that is not in the readings: 10"]


def test_numbers_reports_a_repeated_number_once():
    candidate = report(summary="77 here and 77 there.")
    assert check(candidate) == ["summary has a number that is not in the readings: 77"]


def test_numbers_ignores_digits_inside_names_the_agent_was_given():
    # 07 in the machine id, 01 in the device id, 1, 3 and 10 in the reading names.
    candidate = report(summary="conveyor-07 (effente-01): vib_1x moved.", cited_features=["vib_1x"])
    assert check(candidate) == []
    assert check(report(cited_features=[], summary="vib_harm_3_10 stayed put.")) == [
        "summary names a reading that is not cited: vib_harm_3_10"
    ]


def test_numbers_does_not_take_a_machine_id_from_another_machine():
    candidate = report(summary="Same fault as conveyor-12.")
    assert check(candidate) == ["summary has a number that is not in the readings: 12"]


def test_numbers_does_not_check_part_reference():
    assert check(report(part_reference="SKF 6204-2RS")) == []
