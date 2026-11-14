"""The run posted as contract message 4, the review entry added to the log, and the history read over HTTP."""

import json
import sys

import pytest
from conftest import MACHINE, PLATFORM, load_case
from fake_receiver import FakeReceiver

from device.agent.standin import MODEL_NAME
from partlog.review import history as histories
from partlog.review.agent import REVIEWER, run_review

PART = "part-0007"


def review_over_http(receiver, part_id=PART, **kw):
    return run_review(MACHINE, part_id,
                      load=lambda: histories.part_history(histories.fetch(receiver.url, MACHINE, part_id), part_id),
                      platform_url=receiver.url, **kw)


def test_the_run_is_posted_at_start_after_the_tool_call_and_at_the_end():
    data, expected = load_case("confirmed_waiting")
    with FakeReceiver(data) as receiver:
        result = review_over_http(receiver)

    assert result.ok and result.notes == []
    start, called, done = receiver.runs
    assert [run["status"] for run in receiver.runs] == ["running", "running", "done"]
    assert {run["run_id"] for run in receiver.runs} == {result.run_id}
    assert (start["tool_calls"], start["review"], start["ok"]) == ([], None, False)
    assert [call["name"] for call in called["tool_calls"]] == ["get_part_history"]
    assert called["tool_calls"][0]["input"] == {"part_id": PART}
    assert called["tool_calls"][0]["output"].startswith('{"counts": {"entries": 2, ')
    assert len(called["tool_calls"][0]["output"]) <= 2000


def test_the_finished_run_is_message_4_with_the_three_differences():
    data, expected = load_case("confirmed_waiting")
    with FakeReceiver(data) as receiver:
        result = review_over_http(receiver)

    done = receiver.runs[-1]
    assert done["agent"] == "part_review"
    assert done["part_id"] == PART and done["machine_id"] == MACHINE
    assert done["report"] is None
    assert done["review"] == result.review
    assert list(done["review"]) == ["what", "how", "since", "needs_repair", "why", "cited_alarms", "cited_entries"]
    assert (done["status"], done["ok"], done["reason"], done["problems"]) == ("done", True, "", [])
    assert done["model"] == MODEL_NAME and done["simulated"] is True
    assert done["run_id"].startswith("review-") and done["run_id"].endswith(PART)
    for key in ("run_id", "agent", "machine_id", "part_id", "at", "status", "model", "ok", "reason", "problems",
                "tool_calls", "report", "review", "simulated"):
        assert key in done, key
    assert "device_id" not in done and "model_version" not in done


def test_a_review_that_passes_is_added_to_the_parts_log():
    data, _ = load_case("confirmed_waiting")
    with FakeReceiver(data) as receiver:
        result = review_over_http(receiver)

    (entry,) = receiver.posted
    assert (entry["kind"], entry["by"], entry["part_id"]) == ("review", REVIEWER, PART)
    assert entry["run_id"] == result.run_id and entry["simulated"] is True
    assert entry["text"] == (f"{result.review['what']} Needs repair: yes (advice). {result.review['why']}")
    assert result.entry_id == entry["entry_id"] == receiver.runs[-1]["entry_id"]


def test_an_earlier_review_is_part_of_the_next_runs_history():
    data, _ = load_case("confirmed_waiting")
    with FakeReceiver(data) as receiver:
        first = review_over_http(receiver)
        sheet = histories.fact_sheet(histories.part_history(histories.fetch(receiver.url, MACHINE, PART), PART))
        second = review_over_http(receiver)

    (earlier,) = [entry for entry in sheet["entries"] if entry["kind"] == "review"]
    assert earlier["entry_id"] == first.entry_id and sheet["counts"]["entries"] == 3
    assert second.ok and second.review["needs_repair"] == "yes"  # a review changes no state and no advice
    assert len(receiver.posted) == 2


def test_a_review_that_fails_its_checks_adds_no_entry():
    data, _ = load_case("confirmed_waiting")

    def wrong(sheet, schema, check):
        return {"what": "Possible looseness or rattle.", "why": "It rattles.", "needs_repair": "yes",
                "cited_alarms": [], "cited_entries": []}, {}

    with FakeReceiver(data) as receiver:
        result = review_over_http(receiver, call=wrong)

    done = receiver.runs[-1]
    assert (result.ok, done["ok"], done["review"], done["reason"]) == (False, False, None, "review failed checks")
    assert done["problems"] == ["what names a fault type that is not in the history: looseness_or_rattle"]
    assert receiver.posted == []


def test_a_log_that_is_not_built_yet_ends_the_run_with_a_plain_reason():
    data, _ = load_case("confirmed_waiting")
    with FakeReceiver(data, log_built=False) as receiver:
        result = review_over_http(receiver)

    done = receiver.runs[-1]
    assert result.ok is False and done["status"] == "done"
    assert done["reason"] == "no history: the receiver refused /api/machines/conveyor-07/parts: not built yet"
    assert done["tool_calls"] == []


def test_a_refused_log_entry_only_adds_a_note():
    data, _ = load_case("confirmed_waiting")
    with FakeReceiver(data) as receiver:
        receiver_data = histories.fetch(receiver.url, MACHINE, PART)
        receiver.log_built = False
        result = run_review(MACHINE, PART, load=lambda: histories.part_history(receiver_data, PART),
                            platform_url=receiver.url)

    assert result.ok and result.entry_id == ""
    assert result.notes == ["review entry not added to the log: not built yet"]
    assert receiver.runs[-1]["notes"] == result.notes and receiver.runs[-1]["review"] == result.review


def test_a_platform_that_is_down_loses_only_the_live_view(tmp_path):
    data, _ = load_case("confirmed_waiting")

    result = run_review(MACHINE, PART, load=lambda: histories.part_history(data, PART),
                        platform_url="http://127.0.0.1:9", agent_log_path=tmp_path / "log.jsonl")

    assert result.ok and result.review["needs_repair"] == "yes"
    assert any(note.startswith("review entry not added to the log") for note in result.notes)
    assert any(note.startswith("could not post to platform") for note in result.notes)
    assert json.loads((tmp_path / "log.jsonl").read_text())["ok"] is True


def test_only_the_parts_own_entries_are_asked_for():
    data, _ = load_case("confirmed_waiting")
    with FakeReceiver(data) as receiver:
        fetched = histories.fetch(receiver.url, MACHINE, PART)

    assert f"/api/machines/{MACHINE}/log?part={PART}&limit=2000" in receiver.gets
    assert {entry["part_id"] for entry in fetched["entries"]} == {PART}
    assert [f["name"] for f in fetched["fault_types"]] == [f["name"] for f in data["fault_types"]]


def test_no_receiver_running_is_a_plain_reason():
    with pytest.raises(histories.HistoryUnavailable, match="no receiver is running at http://127.0.0.1:9"):
        histories.fetch("http://127.0.0.1:9", MACHINE, PART)


# ---- against the real receiver (platform/server)

@pytest.fixture
def receiver(tmp_path):
    server_dir = str(PLATFORM / "server")
    if server_dir not in sys.path:
        sys.path.insert(0, server_dir)
    from receiver_server import Receiver

    real = Receiver("127.0.0.1", 0, str(tmp_path / "data"), str(tmp_path / "web"), log=lambda m: None)
    real.start()
    yield real
    real.stop()


def test_the_real_receiver_keeps_a_review_run_and_gives_it_back(receiver):
    data, _ = load_case("confirmed_waiting")
    url = f"http://127.0.0.1:{receiver.port}"

    result = run_review(MACHINE, PART, load=lambda: histories.part_history(data, PART), platform_url=url)

    (run,) = histories.get_json(f"{url}/api/machines/{MACHINE}/agent-runs")["runs"]
    assert run["run_id"] == result.run_id and run["status"] == "done"
    assert (run["agent"], run["part_id"], run["report"]) == ("part_review", PART, None)
    assert run["review"] == result.review


def test_the_real_receivers_log_routes_answer_as_the_reader_expects(receiver):
    """While the routes are stubs this is "not built yet". Once track5/part-store
    merges, the parts route answers and this machine simply has no such part."""
    url = f"http://127.0.0.1:{receiver.port}"

    result = run_review(MACHINE, PART, platform_url=url,
                        load=lambda: histories.part_history(histories.fetch(url, MACHINE, PART), PART))

    assert result.ok is False
    assert result.reason in (
        "no history: the receiver refused /api/machines/conveyor-07/parts: not built yet",
        "no history: conveyor-07 has no part part-0007",
    )
