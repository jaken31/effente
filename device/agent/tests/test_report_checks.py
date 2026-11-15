"""The three checks every report must pass besides its citations: shape, names and numbers."""

from pathlib import Path

import pytest

from device.agent.data import load_log
from device.agent.report import FAULT_WORDS, check_report

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
    assert check(report(cited_features=[])) == []


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


# 4. What the prose, the priority and the part reference may claim.
# The first four are reports a reviewer found passing on the fake logs (15 Nov 2026).

LOGS = Path(__file__).parent / "logs"
FINDING = "unbalance_like"


def log(name, machine_id="conveyor-07"):
    return load_log(LOGS / name, machine_id)


def test_rejects_an_invented_cause_and_a_movement_in_words_with_nothing_cited():
    candidate = report(
        summary="The gearbox bearing is cracked and the shaft is bent; the once per turn vibration rose to "
        "seven times its usual spread. Motor current also doubled.",
        cited_features=[],
        recommended_action="Inspect the machine.",
    )
    problems = check(candidate, log("imbalance.jsonl"))
    assert "summary names a fault the records do not show: bearing" in problems
    assert "summary names a fault the records do not show: cracked" in problems
    assert "summary gives a reading's movement that no cited reading has: seven" in problems


def test_rejects_an_alarming_report_on_a_log_that_is_all_normal():
    candidate = report(
        summary="The drive bearing is failing and the motor is overheating.",
        priority="high",
        cited_features=[],
        recommended_action="Shut the line down and replace the motor.",
        part_reference="SKF 6205",
    )
    problems = check(candidate, log("healthy.jsonl"))
    assert "priority must be none: no record shows a fault type or an unrecognised pattern" in problems
    assert "part_reference must be empty: no parts lookup gave it" in problems
    assert "summary names a fault the records do not show: bearing" in problems
    assert "summary names a fault the records do not show: overheat" in problems


@pytest.mark.parametrize("claim, number", [("vib_1x peaked at 2026 spreads.", "2026"), ("vib_1x moved 20 spreads.", "20")])
def test_rejects_a_movement_made_of_a_timestamp_or_a_window_count(claim, number):
    # 2026 is in every `at`, 20 is the window count: neither is a value of vib_1x.
    problems = check(report(summary=claim), log("imbalance.jsonl"))
    assert problems == [f"summary gives a reading's movement that no cited reading has: {number}"]


def test_rejects_a_fault_type_the_log_was_not_diagnosed_with():
    candidate = report(summary="Looseness of the motor mount confirmed by vib_1x.")
    assert check(candidate, log("imbalance.jsonl")) == ["summary names a fault the records do not show: loose"]
    looseness = report(summary="Looseness of the motor mount.", cited_features=["vib_harm_3_10"],
                       recommended_action="Tighten the bolts.")
    assert check(looseness, log("loose_mount.jsonl")) == []


def test_priority_must_be_none_when_no_record_shows_a_finding():
    quiet = [record("normal", vib_1x=0.2), record("stopped"), record("warming_up")]
    calm = report(summary="No fault found.", cited_features=[], recommended_action="No action needed.")
    assert check(dict(calm, priority="none"), quiet) == []
    for priority in ("low", "medium", "high"):
        assert check(dict(calm, priority=priority), quiet) == [
            "priority must be none: no record shows a fault type or an unrecognised pattern"
        ]


@pytest.mark.parametrize("diagnosis", [FINDING, "unrecognised_pattern"])
def test_priority_must_not_be_none_while_the_newest_record_shows_a_finding(diagnosis):
    records = [record("normal", vib_1x=0.2), record(diagnosis, vib_1x=9.6)]
    candidate = report(summary="Inspect it.", cited_features=[], recommended_action="Inspect the machine.")
    assert check(dict(candidate, priority="none"), records) == [
        f"priority must not be none: the newest record is diagnosed {diagnosis}"
    ]
    assert check(dict(candidate, priority="low"), records) == []


def test_priority_is_free_once_a_finding_has_cleared():
    records = [record(FINDING, vib_1x=9.6), record("normal", vib_1x=0.2)]
    for priority in ("none", "low", "medium", "high"):
        assert check(report(priority=priority), records) == []


def test_part_reference_must_come_from_a_parts_lookup():
    assert check(report(part_reference="SKF 6204-2RS")) == ["part_reference must be empty: no parts lookup gave it"]
    found = '{"results": [{"title": "SKF 6204-2RS deep groove bearing"}]}'
    assert check_report(report(part_reference="SKF 6204-2RS"), RECORDS, FEATURES, parts_found=found) == []
    assert check_report(report(part_reference="FAG 6305"), RECORDS, FEATURES, parts_found=found) == [
        "part_reference must be empty: no parts lookup gave it"
    ]


def test_movement_numbers_pass_only_for_a_cited_reading():
    assert check(report(summary="vib_1x rose to 9.6 spreads, 9.6 times its usual spread, 9.6x normal.")) == []
    uncited = report(summary="The rattle reading reached 0.24 spreads.")  # 0.24 is vib_harm_3_10's, not cited
    assert check(uncited) == [
        "summary names a fault the records do not show: rattl",
        "summary gives a reading's movement that no cited reading has: 0.24",
    ]


def test_plain_numbers_pass_for_counts_times_and_cited_readings_only():
    records = [record(vib_1x=9.6, vib_harm_3_10=0.7), record(vib_1x=-8.4, vib_harm_3_10=0.3)]
    assert check(report(summary="2 windows on 2026-11-14 at 16:00, peak 9.6, unknown score 0.1."), records) == []
    # 0.7 is a value of a reading that is not cited; 16 alone is a piece of a time, not a time.
    assert check(report(summary="A peak of 0.7 after 16 windows."), records) == [
        "summary has a number that is not in the readings: 0.7",
        "summary has a number that is not in the readings: 16",
    ]


def test_fault_words_follow_the_diagnoses_in_the_records():
    records = [record("bearing_defect_like", vib_1x=9.6), record("imbalance", vib_1x=9.6)]  # an older name
    candidate = report(summary="Bearing wear and imbalance.", recommended_action="Replace the bearing, rebalance.")
    assert check(candidate, records) == []
    assert check(dict(candidate, recommended_action="Realign the coupling."), records) == [
        "recommended_action names a fault the records do not show: realign",
        "recommended_action names a fault the records do not show: coupling",
    ]


def test_a_cited_readings_own_sentence_may_be_quoted_with_its_digits():
    table = {"vib_1x": "vibration once per turn", "vib_harm_3_10": "vibration at 3-10x running speed (rattle)"}
    records = [record("looseness_or_rattle", vib_harm_3_10=6.44)]
    quoting = report(summary="Vibration at 3-10x running speed peaked at 6.44 spreads.", priority="medium",
                     cited_features=["vib_harm_3_10"], recommended_action="Check the fasteners.")
    assert check_report(quoting, records, table) == []
    assert check_report(quoting, records, list(table)) == [  # without the sentences, 10x reads as a movement
        "summary gives a reading's movement that no cited reading has: 10",
        "summary has a number that is not in the readings: 3",
    ]
    uncited = dict(quoting, cited_features=[], summary="Vibration at 3-10x running speed is up.")
    assert "summary gives a reading's movement that no cited reading has: 10" in check_report(uncited, records, table)


def test_every_fault_type_has_words():
    fault_types = pytest.importorskip("effente.fault_types")  # needs numpy
    assert set(FAULT_WORDS) == set(fault_types.NAMES)
