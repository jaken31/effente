"""One setup run: read the answers, fill the five fields, check them in code, and
then, after the writer is done, run the lookup on them (platform/CONTRACT.md
section 10.3).

This is the report agent's run flow (device/agent/harness.py, `run_agent`) for
a machine profile instead of a report. It reuses that agent's `Run`, its errors
and its agent log writer unchanged, as the part review does.

Nothing is stored or sent by the run except the run itself: the person confirms
the profile on the Setup page, and only that stores it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from device.agent.harness import INJECTED_MODEL, AgentUnavailable, _append_log
from device.agent.run import Run

from setup import lookup

from .checks import AnswersUnavailable, check_draft, draft_schema
from .standin import stand_in_call

AGENT = "setup"  # contract section 10.3
TOOL = "get_answers"

# (answers, schema, check) -> (draft, usage). `check(draft)` returns the
# problems, so a call can try again once. A call that asks a real model sets
# `live = True` on itself; a run from any other call is marked simulated.
Call = Callable[[list, dict, Callable], tuple[dict, dict]]


@dataclass
class SetupResult:
    ok: bool
    setup: dict | None = None
    problems: list[str] = field(default_factory=list)
    reason: str = ""
    run_id: str = ""
    machine_id: str = ""
    simulated: bool = False
    notes: list[str] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)


class SetupRun(Run):
    """The report agent's run, as contract section 10.3 changes it: `agent` is
    `setup`, and the result is in `setup` while `report` stays `null`."""

    def __init__(self, machine_id: str, model: str, platform_url: str = ""):
        super().__init__(machine_id, device_id="", model=model, platform_url=platform_url)
        for key in ("device_id", "model_version"):  # a setup run reads no device records
            del self.body[key]
        self.body.update(run_id=make_run_id(machine_id), agent=AGENT, setup=None)

    def finish_setup(self, *, ok: bool, setup: dict | None, reason: str, problems: list[str], usage: dict) -> None:
        self.body["setup"] = setup if ok else None
        self.finish(ok=ok, report=None, reason=reason, problems=problems, usage=usage)


def make_run_id(machine_id: str, at: datetime | None = None) -> str:
    """`setup-YYYYMMDD-HHMMSS-<machine>`, using only letters, digits, `-`, `_`, `.`."""
    at = at or datetime.now(timezone.utc)
    safe = re.sub(r"[^A-Za-z0-9_.-]", "-", machine_id) or "machine"
    return f"setup-{at.strftime('%Y%m%d-%H%M%S')}-{safe}"


def run_setup(
    machine_id: str,
    load: Callable[[], list[dict]],
    call: Call = stand_in_call,
    platform_url: str = "",
    agent_log_path: str | Path | None = None,
    entries_dir: Path = lookup.ENTRIES_DIR,
) -> SetupResult:
    """Fill a machine profile from a person's answers.

    `load` returns the answers (checks.py, `read_answers`) or raises
    `AnswersUnavailable`. It is called once, as the run's one tool call.

    Never raises for network or model trouble: the result says why there is no
    profile, and the person fills the five fields by hand. The run is posted to
    the platform at `platform_url` as it happens; with none, nothing is posted.
    """
    live = getattr(call, "live", False) is True
    run = SetupRun(machine_id, getattr(call, "model_name", INJECTED_MODEL), platform_url)
    run.body["simulated"] = not live
    run.post()
    try:
        result, usage = _produce(call, load, run, entries_dir)
    except Exception as error:
        # A bug, not network trouble: close the run so the app does not show it
        # running forever, log it, then let it surface.
        _close(run, SetupResult(ok=False, reason=f"agent error: {type(error).__name__}: {error}"), {}, agent_log_path)
        raise
    return _close(run, result, usage, agent_log_path)


def _produce(call: Call, load, run: SetupRun, entries_dir: Path) -> tuple[SetupResult, dict]:
    try:
        answers = load()
    except AnswersUnavailable as error:
        return SetupResult(ok=False, reason=f"no answers: {error}"), {}
    run.record_tool_call(TOOL, {}, json.dumps({"answers": len(answers)}))

    def check(draft) -> list[str]:
        return check_draft(draft, answers, entries_dir)

    try:
        draft, usage = call(answers, draft_schema(), check)
    except AgentUnavailable as error:
        return SetupResult(ok=False, reason=f"agent unavailable: {error}"), {}
    problems = check(draft)
    if problems:
        return SetupResult(ok=False, problems=problems, reason="profile failed checks"), usage
    profile = lookup.five_fields(draft["profile"])
    # The writer is done. Only now, and only in code, is a library entry picked.
    result = SetupResult(ok=True, setup={"profile": profile, "explanation": draft["explanation"], "choice": None})
    try:
        result.setup["choice"] = lookup.choose(profile, entries_dir)
    except lookup.LookupUnavailable as error:
        result.notes.append(f"no starting model: {error}")
    return result, usage


def _close(run: SetupRun, result: SetupResult, usage: dict, agent_log_path) -> SetupResult:
    result.run_id = run.run_id
    result.machine_id = run.body["machine_id"]
    result.simulated = run.body["simulated"]
    run.body.update(notes=list(result.notes))  # an extra field: stored and passed on
    run.finish_setup(ok=result.ok, setup=result.setup, reason=result.reason, problems=result.problems, usage=usage)
    result.tool_calls = list(run.tool_calls)
    if run.post_errors:
        result.notes.append(f"could not post to platform: {run.post_errors[0]}")
    if agent_log_path is not None:
        _append_log(agent_log_path, run.body["machine_id"], result, usage, run.body["model"])
    return result
