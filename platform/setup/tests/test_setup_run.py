"""A setup run through the stand-in, and what it posts (contract 10.3, plan row U4)."""

import json
import urllib.request

import pytest
from setup_cases import CASES, MACHINE, answers

from setup import lookup
from setup.agent.agent import run_setup
from setup.agent.checks import AnswersUnavailable, read_answers
from setup.agent.standin import stand_in_call


def run(given, **kw):
    return run_setup(MACHINE, load=lambda: read_answers(given), **kw)


@pytest.mark.parametrize("case", sorted(CASES))
def test_fixed_answers_give_the_right_profile_through_the_stand_in(case):
    given, right = CASES[case]
    result = run(given)
    assert result.ok, result
    assert result.setup["profile"] == right


@pytest.mark.parametrize("case", sorted(CASES))
def test_the_runs_choice_is_the_lookups_for_its_profile(case):
    result = run(CASES[case][0])
    assert result.setup["choice"] == lookup.choose(result.setup["profile"])


def test_i_dont_know_leaves_every_field_empty_and_says_so():
    result = run(answers(None, "I don't know", "dont know", "not sure", "no idea"))
    assert result.ok and result.setup["profile"] == dict.fromkeys(lookup.FIELDS)
    assert result.setup["explanation"].count("you did not know") == 5
    assert result.setup["choice"]["library_entry"] == "generic_rotating"
    assert result.setup["choice"]["reason"] == "machine type not given, so the general one"


def test_the_stand_ins_run_is_simulated_and_its_explanation_begins_stand_in():
    result = run(CASES["belt_conveyor"][0])
    assert result.simulated is True
    assert result.setup["explanation"].startswith("Stand-in:")
    assert result.setup["explanation"] == ("Stand-in: from your answers I filled machine type (conveyor), drive (belt) "
                                           "and mounting (rigid). Left empty: speed, because you did not know; "
                                           "sensor location, because you did not know.")


def test_the_writer_is_given_the_answers_and_a_schema_with_no_place_for_an_entry():
    seen = {}

    def call(given, schema, check):
        seen.update(answers=given, schema=schema)
        return stand_in_call(given, schema, check)

    given = CASES["pump_direct"][0]
    assert run(given, call=call).ok
    assert seen["answers"] == read_answers(given)
    assert sorted(seen["schema"]["properties"]) == ["explanation", "profile"]
    assert sorted(seen["schema"]["properties"]["profile"]["properties"]) == sorted(lookup.FIELDS)
    assert seen["schema"]["additionalProperties"] is False
    assert "generic_rotating" not in json.dumps(seen)


@pytest.mark.parametrize("bad,reason", [
    ({"profile": {**dict.fromkeys(lookup.FIELDS), "speed_rpm": 1500}, "explanation": "x"},
     "speed_rpm 1500 is not a number the person wrote"),
    ({"profile": dict.fromkeys(lookup.FIELDS), "explanation": "x", "library_entry": "conveyor"},
     "exactly the keys profile and explanation"),
    ({"profile": dict.fromkeys(lookup.FIELDS), "explanation": "Start from generic_rotating."},
     "names the library entry generic_rotating"),
])
def test_a_run_that_fails_its_checks_is_discarded_and_says_why(bad, reason):
    result = run(CASES["belt_conveyor"][0], call=lambda given, schema, check: (bad, {}))
    assert not result.ok and result.setup is None and result.reason == "profile failed checks"
    assert any(reason in p for p in result.problems), result.problems


def test_answers_that_cannot_be_read_end_the_run_with_the_reason():
    def load():
        raise AnswersUnavailable("cannot read answers.json")

    result = run_setup(MACHINE, load=load)
    assert not result.ok and result.reason == "no answers: cannot read answers.json" and result.tool_calls == []


def test_the_run_is_posted_as_message_4_with_agent_setup(receiver, tmp_path):
    result = run(CASES["belt_conveyor"][0], platform_url=receiver, agent_log_path=tmp_path / "setup.jsonl")
    assert result.ok and result.notes == []
    with urllib.request.urlopen(f"{receiver}/api/machines/{MACHINE}/agent-runs") as response:
        (posted,) = json.loads(response.read())["runs"]
    assert posted["run_id"] == result.run_id and posted["run_id"].startswith("setup-") and posted["run_id"].endswith(MACHINE)
    assert posted["agent"] == "setup" and posted["status"] == "done" and posted["ok"] is True
    assert posted["report"] is None and posted["setup"] == result.setup
    assert posted["simulated"] is True and posted["model"] == "stand-in (no model call)"
    assert [c["name"] for c in posted["tool_calls"]] == ["get_answers"]
    assert posted["tool_calls"][0]["output"] == '{"answers": 5}'
    assert "device_id" not in posted
    logged = json.loads((tmp_path / "setup.jsonl").read_text())
    assert logged["run_id"] == result.run_id and logged["setup"] == result.setup


def test_the_run_stores_no_profile_and_sends_nothing_to_a_device(receiver):
    assert run(CASES["belt_conveyor"][0], platform_url=receiver).ok
    with urllib.request.urlopen(f"{receiver}/api/machines/{MACHINE}/profile") as response:
        assert json.loads(response.read())["profile"] is None


def test_a_failed_run_is_posted_with_no_result(receiver):
    result = run(CASES["belt_conveyor"][0], platform_url=receiver,
                 call=lambda given, schema, check: ({"profile": {}, "explanation": ""}, {}))
    with urllib.request.urlopen(f"{receiver}/api/machines/{MACHINE}/agent-runs") as response:
        (posted,) = json.loads(response.read())["runs"]
    assert posted["ok"] is False and posted["setup"] is None and posted["reason"] == "profile failed checks"
    assert posted["problems"] == result.problems


def test_a_receiver_that_is_down_costs_only_the_live_view():
    result = run(CASES["belt_conveyor"][0], platform_url="http://127.0.0.1:9")
    assert result.ok and result.notes[0].startswith("could not post to platform:")
