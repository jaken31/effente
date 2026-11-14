"""The checks every setup draft passes, whoever wrote it (contract 10.3, plan row U4)."""

import pytest
from setup_cases import answers

from setup.agent.checks import AnswersUnavailable, check_draft, read_answers

ANSWERS = answers("a belt conveyor", "belt", "about 1450 rpm", "bolted down", None)
PROFILE = {"machine_type": "conveyor", "drive": "belt", "speed_rpm": 1450, "mounting": "rigid", "sensor_location": None}
GOOD = {"profile": PROFILE, "explanation": "A belt conveyor turning at 1450 rpm, bolted down. Sensor place not given."}


def draft(explanation=None, **fields):
    return {"profile": {**PROFILE, **fields}, "explanation": explanation or GOOD["explanation"]}


def test_a_good_draft_passes():
    assert check_draft(GOOD, ANSWERS) == []


@pytest.mark.parametrize("speed", [1500, 1450.5, [1400, 1450], 29.5])
def test_a_run_that_invents_a_speed_is_rejected(speed):
    problems = check_draft(draft(speed_rpm=speed), ANSWERS)
    assert any("is not a number the person wrote" in p for p in problems), problems


def test_a_speed_worked_out_from_hz_is_rejected():
    hz = answers("a motor", None, "50 Hz", None, None)
    problems = check_draft({"profile": {**PROFILE, "machine_type": "motor", "drive": None, "speed_rpm": 3000,
                                        "mounting": None}, "explanation": "A motor."}, hz)
    assert problems == ["speed_rpm 3000 is not a number the person wrote: leave it null rather than guess"]


def test_i_dont_know_leaves_the_speed_empty_and_that_passes():
    unknown = answers("a belt conveyor", "belt", None, "bolted down", None)
    assert check_draft(draft("A belt conveyor, bolted down. Speed not known.", speed_rpm=None), unknown) == []


@pytest.mark.parametrize("bad,expected", [
    ({**GOOD, "library_entry": "conveyor"}, "exactly the keys profile and explanation"),
    ({**GOOD, "choice": {"library_entry": "generic_rotating"}}, "exactly the keys profile and explanation"),
    ({"profile": {**PROFILE, "library_entry": "conveyor"}, "explanation": "x"}, "profile must have exactly the keys"),
    ({"profile": {k: v for k, v in PROFILE.items() if k != "mounting"}, "explanation": "x"},
     "profile must have exactly the keys"),
    ({"profile": PROFILE}, "exactly the keys profile and explanation"),
    (["conveyor"], "exactly the keys profile and explanation"),
    ({"profile": PROFILE, "explanation": " "}, "explanation must be text"),
])
def test_a_run_with_an_extra_or_missing_key_is_rejected(bad, expected):
    problems = check_draft(bad, ANSWERS)
    assert any(expected in p for p in problems), problems


@pytest.mark.parametrize("field,value", [("machine_type", "press"), ("drive", "chain"), ("mounting", "glued"),
                                         ("sensor_location", "belt"), ("speed_rpm", -5), ("speed_rpm", "1450")])
def test_a_value_outside_its_list_is_rejected(field, value):
    problems = check_draft(draft(**{field: value}), ANSWERS)
    assert any(f"'{field}' must be" in p for p in problems), problems


def test_a_number_in_the_explanation_that_the_person_did_not_write_is_rejected():
    problems = check_draft(draft("A belt conveyor at 1450 rpm, about 24 hours a day."), ANSWERS)
    assert problems == ["explanation has the number 24, which the person did not write"]


@pytest.mark.parametrize("named", ["generic_rotating", "Generic rotating", "generic-rotating"])
def test_a_run_whose_explanation_names_an_entry_the_lookup_did_not_return_is_rejected(named):
    """The lookup returns `conveyor` for this profile. The writer never names an entry."""
    problems = check_draft(draft(f"A belt conveyor. I would start from the {named} model."), ANSWERS)
    assert problems == ["explanation names the library entry generic_rotating: the setup agent never names one, "
                        "the lookup picks it afterwards"]


def test_a_run_that_names_an_entry_is_rejected_even_when_it_matches_the_lookup():
    pump = answers("a pump", None, None, None, None)
    problems = check_draft({"profile": {**dict.fromkeys(PROFILE), "machine_type": "pump"},
                            "explanation": "A pump, so use generic_rotating."}, pump)
    assert len(problems) == 1 and "names the library entry generic_rotating" in problems[0]


def test_the_persons_own_words_and_the_profiles_values_are_not_entry_names():
    # `conveyor` is a machine type the person said, not the agent naming an entry.
    assert check_draft(draft("You said it is a belt conveyor at 1450 rpm."), ANSWERS) == []
    pump = answers("a pump, not a conveyor", None, None, None, None)
    assert check_draft({"profile": {**dict.fromkeys(PROFILE), "machine_type": "pump"},
                        "explanation": "A pump, you said not a conveyor."}, pump) == []


@pytest.mark.parametrize("raw,error", [
    ([], "non-empty JSON list"),
    ({"question": "q"}, "non-empty JSON list"),
    ([{"answer": "a"}], "needs a 'question'"),
    ([{"question": "q", "answer": 5}], "'answer' must be text"),
    ([{"question": "q", "answer": "a", "about": "colour"}], "'about' must be one of"),
])
def test_answers_in_the_wrong_shape_are_refused(raw, error):
    with pytest.raises(AnswersUnavailable, match=error):
        read_answers(raw)


def test_answers_keep_only_question_answer_and_about():
    raw = [{"question": "q", "answer": None, "library_entry": "conveyor"}]
    assert read_answers(raw) == [{"question": "q", "answer": None, "about": None}]
