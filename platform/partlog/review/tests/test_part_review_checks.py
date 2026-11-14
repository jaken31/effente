"""Every check of platform/CONTRACT.md section 8.8, each with a review that breaks it and is rejected."""

import pytest

from partlog.review.history import fact_sheet
from partlog.review.review import build_review_schema, check_draft, check_review, complete

HOW = "once-per-turn vibration grown (unbalance, a bent shaft, a lost blade)"


def good(history, **changes) -> dict:
    """A review of the confirmed history that passes, with `changes` applied on top."""
    draft = {
        "what": "Possible unbalance-like on the Drive roller, answered as a real problem by Sam.",
        "why": "1 alarm, a finding that says it needs repair, and 0 repairs since.",
        "needs_repair": "yes",
        "cited_alarms": ["alarm-1234"],
        "cited_entries": ["entry-000123"],
    }
    return {**complete(draft, fact_sheet(history)), **changes}


def test_a_good_review_passes(confirmed):
    review = good(confirmed)

    assert review["how"] == HOW and review["since"] == "2026-11-14T19:12:03Z"
    assert check_review(review, confirmed) == []


# ---- the shape: these keys, these types, nothing extra

def test_a_review_that_is_not_an_object_is_rejected(confirmed):
    assert check_review(["what"], confirmed) == ["review is not an object: list"]


@pytest.mark.parametrize("key", ["what", "how", "since", "needs_repair", "why", "cited_alarms", "cited_entries"])
def test_a_missing_key_is_rejected(confirmed, key):
    review = good(confirmed)
    del review[key]

    assert check_review(review, confirmed) == [f"missing key: {key}"]


def test_an_extra_key_is_rejected(confirmed):
    assert check_review(good(confirmed, priority="high"), confirmed) == ["unexpected key: priority"]


@pytest.mark.parametrize("change, problem", [
    ({"what": 7}, "what must be text: 7"),
    ({"why": None}, "why must be text: None"),
    ({"how": ["a cause"]}, "how must be text: ['a cause']"),
    ({"needs_repair": True}, "needs_repair must be text: True"),
    ({"since": 1763147523}, "since must be a time or null: 1763147523"),
    ({"cited_alarms": "alarm-1234"}, "cited_alarms must be a list of ids: 'alarm-1234'"),
    ({"cited_entries": [123]}, "cited_entries must be a list of ids: [123]"),
])
def test_a_wrong_type_is_rejected(confirmed, change, problem):
    assert check_review(good(confirmed, **change), confirmed) == [problem]


def test_an_unknown_needs_repair_is_rejected(confirmed):
    assert check_review(good(confirmed, needs_repair="maybe"), confirmed) == ["unknown needs_repair: 'maybe'"]


@pytest.mark.parametrize("key", ["what", "why"])
def test_a_blank_what_or_why_is_rejected(confirmed, key):
    assert check_review(good(confirmed, **{key: "  "}), confirmed) == [f"{key} is empty"]


# ---- how and since are the code's

def test_a_how_the_code_did_not_copy_is_rejected(confirmed):
    review = good(confirmed, how="the roller was dropped during installation")

    assert check_review(review, confirmed) == ["how is not the fault type's own text from the model announcement"]


def test_a_how_with_no_fault_type_must_be_empty():
    from part_review_cases import history_of

    repaired = history_of("repaired")
    review = {"what": "Drive roller was repaired.", "why": "A repair entry is the newest.", "needs_repair": "no",
              "cited_alarms": [], "cited_entries": ["entry-000131"], "how": HOW, "since": "2026-11-14T19:50:05Z"}

    assert check_review(review, repaired) == ["how is not the fault type's own text from the model announcement"]
    assert check_review({**review, "how": ""}, repaired) == []


def test_a_since_that_is_not_the_parts_state_since_is_rejected(confirmed):
    review = good(confirmed, since="2026-11-01T00:00:00Z")

    assert check_review(review, confirmed) == ["since is not the part's state.since"]


@pytest.mark.parametrize("key", ["how", "since"])
def test_a_draft_that_fills_how_or_since_itself_is_refused(confirmed, key):
    """Even with the right value: the writer may not fill it."""
    review = good(confirmed)
    draft = {k: v for k, v in review.items() if k not in ("how", "since")}

    assert check_draft(draft, confirmed) == []
    assert check_draft({**draft, key: review[key]}, confirmed) == [f"{key} is filled by the code, not by the writer"]


def test_the_schema_given_to_a_model_has_no_how_and_no_since(confirmed):
    schema = build_review_schema(fact_sheet(confirmed))

    assert sorted(schema["properties"]) == ["cited_alarms", "cited_entries", "needs_repair", "what", "why"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["cited_alarms"]["items"]["enum"] == ["alarm-1234"]
    assert schema["properties"]["cited_entries"]["items"]["enum"] == ["entry-000100", "entry-000123"]


def test_the_schema_allows_no_citation_when_the_history_has_nothing_to_cite():
    from part_review_cases import history_of

    schema = build_review_schema(fact_sheet(history_of("never_checked")))

    assert schema["properties"]["cited_alarms"] == {"type": "array", "items": {"type": "string"}, "maxItems": 0}


# ---- every cited id was in the history supplied

def test_a_cited_alarm_that_is_not_in_the_history_is_rejected(confirmed):
    """alarm-2301 is real, on the same machine, but it is another part's."""
    review = good(confirmed, cited_alarms=["alarm-1234", "alarm-2301"])

    assert check_review(review, confirmed) == ["cites an alarm that is not in the history: alarm-2301"]


def test_a_cited_entry_that_is_not_in_the_history_is_rejected(confirmed):
    """entry-000090 is the belt's."""
    review = good(confirmed, cited_entries=["entry-000090"])

    assert check_review(review, confirmed) == ["cites an entry that is not in the history: entry-000090"]


# ---- every number in what and why appears in the history supplied

@pytest.mark.parametrize("key", ["what", "why"])
def test_a_number_that_is_not_in_the_history_is_rejected(confirmed, key):
    review = good(confirmed, **{key: "The roller has run 300 hours with this fault."})

    assert check_review(review, confirmed) == [f"{key} has a number that is not in the history: 300"]


def test_numbers_copied_from_the_history_pass(confirmed):
    review = good(confirmed, why="The alarm lasted 7 periods and peaked at 2.4, on 2026-11-14 at 19:02.")

    assert check_review(review, confirmed) == []


def test_a_rounded_number_is_rejected(confirmed):
    review = good(confirmed, why="The alarm peaked at about 2.5.")

    assert check_review(review, confirmed) == ["why has a number that is not in the history: 2.5"]


def test_digits_inside_an_id_are_not_a_number_on_either_side(confirmed):
    """`alarm-1234` may be named, but it does not make 1234 a number of the history."""
    named = good(confirmed, why="See alarm-1234 and entry-000123 on conveyor-07.")
    counted = good(confirmed, why="The roller turned 1234 times.")

    assert check_review(named, confirmed) == []
    assert check_review(counted, confirmed) == ["why has a number that is not in the history: 1234"]


def test_an_id_that_is_not_in_the_history_is_caught_as_a_number(confirmed):
    review = good(confirmed, why="As in alarm-2301.")

    assert check_review(review, confirmed) == ["why has a number that is not in the history: 2301"]


# ---- what and why name no fault type and no part that is not in the history supplied

@pytest.mark.parametrize("written", ["looseness_or_rattle", "looseness or rattle", "Looseness-or-rattle"])
def test_a_fault_type_that_is_not_in_the_history_is_rejected(confirmed, written):
    review = good(confirmed, what=f"Possible {written} on the Drive roller.")

    assert check_review(review, confirmed) == ["what names a fault type that is not in the history: looseness_or_rattle"]


def test_an_unrecognised_pattern_that_is_not_in_the_history_is_rejected(confirmed):
    review = good(confirmed, why="The alarm was an unrecognised pattern.")

    assert check_review(review, confirmed) == ["why names a fault type that is not in the history: unrecognised_pattern"]


@pytest.mark.parametrize("written", ["Motor mount", "the motor  mount", "part-0002"])
def test_a_part_that_is_not_in_the_history_is_rejected(confirmed, written):
    review = good(confirmed, why=f"The weight may have come from {written}.")

    problems = check_review(review, confirmed)

    assert "why names a part that is not in the history: " + ("part-0002" if "part-" in written else "Motor mount") in problems


def test_the_parts_own_name_and_fault_type_may_be_named(confirmed):
    review = good(confirmed, what="unbalance_like on the DRIVE ROLLER (part-0007).")

    assert check_review(review, confirmed) == []


def test_another_part_named_in_this_parts_own_entries_may_be_named(confirmed):
    """What a person wrote in this part's log is in the history supplied."""
    confirmed["entries"][0]["text"] = "moved here from the Motor mount side"
    review = good(confirmed, why="Sam noted it was moved from the Motor mount side.")

    assert check_review(review, confirmed) == []


def test_a_part_whose_name_is_inside_this_parts_name_is_not_found_there(confirmed):
    confirmed["other_parts"].append({"part_id": "part-0009", "name": "Drive"})

    assert check_review(good(confirmed), confirmed) == []
    review = good(confirmed, why="The drive behind the Drive roller may be the cause.")
    assert check_review(review, confirmed) == ["why names a part that is not in the history: Drive"]


# ---- with no alarm and no entry that says needs_repair: true, needs_repair must not be yes

def test_yes_with_no_alarm_and_no_entry_that_says_so_is_rejected():
    from part_review_cases import history_of

    never = history_of("never_checked")
    review = {"what": "Idle roller needs repair.", "why": "It looks worn.", "needs_repair": "yes",
              "cited_alarms": [], "cited_entries": [], "how": "", "since": None}

    assert check_review(review, never) == [
        "needs_repair is yes with no alarm and no entry that says the part needs repair"]
    assert check_review({**review, "needs_repair": "not_sure"}, never) == []


def test_yes_is_allowed_with_an_entry_that_says_so_and_no_alarm():
    from part_review_cases import history_of

    never = history_of("never_checked")
    never["entries"].append({"entry_id": "entry-000300", "part_id": "part-0004", "at": "2026-11-14T20:30:00Z",
                             "kind": "inspection", "text": "bearing seized", "by": "Sam", "needs_repair": True})
    review = {"what": "Idle roller needs repair.", "why": "Sam's inspection says so.", "needs_repair": "yes",
              "cited_alarms": [], "cited_entries": ["entry-000300"], "how": "", "since": None}

    assert check_review(review, never) == []


def test_every_problem_is_reported_at_once(confirmed):
    review = good(confirmed, what="Possible looseness or rattle after 300 hours.", cited_alarms=["alarm-9"])

    assert check_review(review, confirmed) == [
        "cites an alarm that is not in the history: alarm-9",
        "what has a number that is not in the history: 300",
        "what names a fault type that is not in the history: looseness_or_rattle",
    ]
