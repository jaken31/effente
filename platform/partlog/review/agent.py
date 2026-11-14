"""Run the part review for one part and check what comes back.

This is the report agent's run flow (device/agent/harness.py, `run_agent`)
for a review instead of a report. It reuses that agent's `Run`, its errors
and its agent log writer unchanged. `run_agent` itself cannot be used: it is
tied to device log records and `check_report`.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from device.agent.harness import INJECTED_MODEL, AgentUnavailable, _append_log
from device.agent.run import Run

from .history import HistoryUnavailable, fact_sheet, is_simulated
from .review import build_review_schema, check_draft, complete
from .standin import stand_in_call

AGENT = "part_review"  # contract section 8.8
REVIEWER = "part review agent"  # the `by` of a review entry (contract section 8.6)
TOOL = "get_part_history"
ENTRY_TIMEOUT_SECONDS = 5.0
ADVICE_WORDS = {"yes": "yes", "no": "no", "not_sure": "not sure"}

# (fact sheet, schema, check) -> (draft, usage). `check(draft)` returns the
# problems the finished review would have, so a call can try again. A call
# that asks a real model sets `live = True` on itself; a run from any other
# call is marked simulated.
Call = Callable[[dict, dict, Callable], tuple[dict, dict]]


@dataclass
class ReviewResult:
    ok: bool
    review: dict | None = None
    problems: list[str] = field(default_factory=list)
    reason: str = ""
    run_id: str = ""
    part_id: str = ""
    entry_id: str = ""
    simulated: bool = False
    notes: list[str] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)


class ReviewRun(Run):
    """The report agent's run, as contract section 8.8 changes it: `agent` is
    `part_review`, there is a `part_id`, and the result is in `review` while
    `report` stays `null`."""

    def __init__(self, machine_id: str, part_id: str, model: str, platform_url: str = ""):
        super().__init__(machine_id, device_id="", model=model, platform_url=platform_url)
        for key in ("device_id", "model_version"):  # a review reads no device records
            del self.body[key]
        self.body.update(run_id=make_run_id(part_id), agent=AGENT, part_id=part_id, review=None)

    def finish_review(self, *, ok: bool, review: dict | None, reason: str, problems: list[str], usage: dict) -> None:
        self.body["review"] = review if ok else None
        self.finish(ok=ok, report=None, reason=reason, problems=problems, usage=usage)


def make_run_id(part_id: str, at: datetime | None = None) -> str:
    """`review-YYYYMMDD-HHMMSS-<part>`, using only letters, digits, `-`, `_`, `.`."""
    at = at or datetime.now(timezone.utc)
    safe_part = re.sub(r"[^A-Za-z0-9_.-]", "-", part_id) or "part"
    return f"review-{at.strftime('%Y%m%d-%H%M%S')}-{safe_part}"


def run_review(
    machine_id: str,
    part_id: str,
    load: Callable[[], dict],
    call: Call = stand_in_call,
    platform_url: str = "",
    agent_log_path: str | Path | None = None,
) -> ReviewResult:
    """Produce a checked review of one part.

    `load` returns the part's history (history.py, `part_history`) or raises
    `HistoryUnavailable`. It is called once, as the run's one tool call.

    Never raises for network or model trouble: the result says why there is
    no review. The run is posted to the platform at `platform_url` as it
    happens. A review that passes is also added to the part's log as a
    `review` entry. With no `platform_url`, nothing is posted.
    """
    live = getattr(call, "live", False) is True
    run = ReviewRun(machine_id, part_id, getattr(call, "model_name", INJECTED_MODEL), platform_url)
    run.body["simulated"] = not live  # until the history is read
    run.post()
    try:
        result, usage = _produce(call, load, run, live)
    except Exception as error:
        # A bug, not network trouble: close the run so the app does not show
        # it running forever, log it, then let it surface.
        failed = ReviewResult(ok=False, reason=f"agent error: {type(error).__name__}: {error}")
        _close(run, failed, {}, agent_log_path)
        raise
    return _close(run, result, usage, agent_log_path)


def _produce(call: Call, load: Callable[[], dict], run: ReviewRun, live: bool) -> tuple[ReviewResult, dict]:
    try:
        history = load()
    except HistoryUnavailable as error:
        return ReviewResult(ok=False, reason=f"no history: {error}"), {}
    sheet = fact_sheet(history)
    run.body["simulated"] = not live or is_simulated(history)
    run.record_tool_call(TOOL, {"part_id": run.body["part_id"]}, json.dumps(sheet))

    def check(draft) -> list[str]:
        return check_draft(draft, history)

    try:
        draft, usage = call(sheet, build_review_schema(sheet), check)
    except AgentUnavailable as error:
        return ReviewResult(ok=False, reason=f"agent unavailable: {error}"), {}
    problems = check(draft)
    if problems:
        return ReviewResult(ok=False, problems=problems, reason="review failed checks"), usage
    return ReviewResult(ok=True, review=complete(draft, sheet)), usage


def _close(run: ReviewRun, result: ReviewResult, usage: dict, agent_log_path) -> ReviewResult:
    result.run_id = run.run_id
    result.part_id = run.body["part_id"]
    result.simulated = run.body["simulated"]
    if result.ok and run.platform_url:
        result.entry_id, note = add_review_entry(run, result.review)
        if note:
            result.notes.append(note)
    run.body.update(notes=list(result.notes), entry_id=result.entry_id)  # extra fields: stored and passed on
    run.finish_review(
        ok=result.ok, review=result.review, reason=result.reason, problems=result.problems, usage=usage
    )
    result.tool_calls = list(run.tool_calls)
    if run.post_errors:
        result.notes.append(f"could not post to platform: {run.post_errors[0]}")
    if agent_log_path is not None:
        _append_log(agent_log_path, run.body["machine_id"], result, usage, run.body["model"])
    return result


def entry_text(review: dict) -> str:
    """A review as one line of the log: what, the advice labelled as advice, and why."""
    return f"{review['what']} Needs repair: {ADVICE_WORDS[review['needs_repair']]} (advice). {review['why']}"


def add_review_entry(run: ReviewRun, review: dict) -> tuple[str, str]:
    """Add the review to the part's log (contract section 8.6). Returns (entry id, note).

    Never raises: a receiver that is down or refuses only costs the log line,
    and the note says so. The review itself is still on the run.

    The contract's body for this route does not list `run_id` or `simulated`.
    Both are sent: the log store on branch track5/part-store keeps them
    (checked once by hand; the route was a stub on master at the time).
    """
    body = {
        "kind": "review",
        "by": REVIEWER,
        "part_id": run.body["part_id"],
        "text": entry_text(review),
        "at": run.body["at"],
        "run_id": run.run_id,
        "simulated": run.body["simulated"],
    }
    request = urllib.request.Request(
        f"{run.platform_url}/api/machines/{urllib.parse.quote(run.body['machine_id'], safe='')}/log",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=ENTRY_TIMEOUT_SECONDS) as response:
            entry = json.loads(response.read()).get("entry") or {}
        return str(entry.get("entry_id") or ""), ""
    except urllib.error.HTTPError as error:
        try:
            said = json.loads(error.read())["error"]
        except Exception:
            said = f"HTTP {error.code}"
        return "", f"review entry not added to the log: {said}"
    except Exception as error:  # down, slow, not JSON: never lose the review over the log line
        return "", f"review entry not added to the log: {type(error).__name__}: {error}"
