"""Run the maintenance agent for one machine and check what it returns."""

from __future__ import annotations

import inspect
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .data import load_feature_table, load_log, load_model_version, newest_model_version
from .report import build_report_schema, check_report
from .run import Run
from .tools import Tools

MODEL = "claude-opus-5-5"
MAX_ROUNDS = 6
TIMEOUT_SECONDS = 60.0
# Shown as the run's model when an injected `call` is not the real Claude call
# and does not name itself with a `model_name` attribute.
INJECTED_MODEL = "injected (no Claude call)"

SYSTEM = """You write maintenance reports for a technician, from vibration readings \
taken by a handheld diagnostic device.

Use get_readings to see what the device measured and get_feature_table to see the \
verified features and what each one means. A verified feature is one the team has \
proven the detection model relies on.

Cite a feature only if the readings show it was active. If the readings show an \
unknown pattern, say so plainly and recommend inspection: do not guess a cause.

Set priority by how soon the machine needs attention. Use search_parts only when a \
specific part would help, and leave part_reference empty if you have none."""


class AgentUnavailable(Exception):
    """The agent could not produce a report. Detection is unaffected."""


@dataclass
class AgentResult:
    ok: bool
    report: dict | None = None
    problems: list[str] = field(default_factory=list)
    reason: str = ""
    run_id: str = ""
    model_version: str = ""
    simulated: bool = False
    notes: list[str] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)


# (machine_id, records, features, schema) -> (report, usage).
# A call may also accept a `tools` keyword: it then gets a Tools object whose
# methods are the agent's tools, and every call it makes through them is
# recorded and posted as it happens. Calls without that keyword still work.
Call = Callable[..., tuple[dict, dict]]


def run_agent(
    machine_id: str,
    log_path: str | Path,
    model_path: str | Path,
    agent_log_path: str | Path,
    call: Call | None = None,
    platform_url: str | None = None,
    search: Callable[[str], str] | None = None,
) -> AgentResult:
    """Produce a checked maintenance report for one machine.

    Never raises for network or API trouble: the result says why there is no
    report. The run is posted to the platform (platform/CONTRACT.md message 4)
    at `platform_url`, or EFFENTE_PLATFORM_URL; with neither, nothing is posted.
    `search` replaces the Tavily parts lookup, for tests.
    """
    call = call or call_claude
    features = load_feature_table(model_path)
    file_version = load_model_version(model_path)
    all_records = load_log(log_path, machine_id)
    records, version, versions = newest_model_version(all_records)

    notes = []
    if len(versions) > 1:
        shown = ", ".join(v or "(none)" for v in versions)
        notes.append(
            f"readings span model versions {shown}: used only the {len(records)} of "
            f"{len(all_records)} readings from {version or '(none)'}, the newest"
        )
    if records and file_version and version != file_version:
        notes.append(
            f"model file is version {file_version} but the readings used are version "
            f"{version or '(none)'}: feature names may not mean the same thing"
        )

    url = platform_url if platform_url is not None else os.environ.get("EFFENTE_PLATFORM_URL", "")
    run = Run(
        machine_id,
        device_id=str(records[-1].get("device_id", "")) if records else "",
        model=getattr(call, "model_name", MODEL if call is call_claude else INJECTED_MODEL),
        platform_url=url,
    )
    run.body.update(
        model_version=version,
        notes=notes,
        simulated=call is not call_claude or any(r.get("simulated") is True for r in records),
    )
    run.post()

    try:
        result, usage = _produce(call, machine_id, records, features, run, search)
    except Exception as error:
        # A bug, not network trouble: close the run so the dashboard does not
        # show it running forever, log it, then let it surface.
        failed = AgentResult(ok=False, reason=f"agent error: {type(error).__name__}: {error}")
        _close(run, failed, {}, agent_log_path, machine_id)
        raise
    return _close(run, result, usage, agent_log_path, machine_id)


def _produce(call, machine_id, records, features, run, search) -> tuple[AgentResult, dict]:
    if not records:
        return AgentResult(ok=False, reason="no readings for this machine"), {}
    try:
        schema = build_report_schema(list(features))
    except ValueError as error:
        return AgentResult(ok=False, reason=str(error)), {}
    tools = Tools(records, features, run=run, search=search)
    try:
        if _accepts_tools(call):
            report, usage = call(machine_id, records, features, schema, tools=tools)
        else:
            report, usage = call(machine_id, records, features, schema)
    except AgentUnavailable as error:
        return AgentResult(ok=False, reason=f"agent unavailable: {error}"), {}
    problems = check_report(report, records, list(features))
    if problems:
        return AgentResult(ok=False, problems=problems, reason="report failed checks"), usage
    return AgentResult(ok=True, report=report), usage


def _close(run: Run, result: AgentResult, usage: dict, agent_log_path, machine_id: str) -> AgentResult:
    run.finish(ok=result.ok, report=result.report, reason=result.reason, problems=result.problems, usage=usage)
    result.run_id = run.run_id
    result.model_version = run.body["model_version"]
    result.simulated = run.body["simulated"]
    result.notes = list(run.body["notes"])
    result.tool_calls = list(run.tool_calls)
    if run.post_errors:
        result.notes.append(f"could not post to platform: {run.post_errors[0]}")
    _append_log(agent_log_path, machine_id, result, usage, run.body["model"])
    return result


def _accepts_tools(call) -> bool:
    try:
        parameters = inspect.signature(call).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(p.name == "tools" or p.kind is p.VAR_KEYWORD for p in parameters)


def call_claude(
    machine_id: str,
    records: list[dict],
    features: dict[str, str],
    schema: dict,
    tools: Tools | None = None,
) -> tuple[dict, dict]:
    """Ask Claude for the report, letting it call the tools it needs.

    NEVER RUN: this live path has not been run against the API. See README.md.
    """
    import anthropic
    from anthropic import beta_tool

    tools = tools or Tools(records, features)

    @beta_tool
    def get_readings(since: str = "") -> str:
        """Summarise the device's readings for this machine.

        Args:
            since: Optional ISO 8601 UTC time. Only readings at or after it are used.
        """
        return tools.get_readings(since)

    @beta_tool
    def get_feature_table() -> str:
        """List the verified features and the sentence that explains each one."""
        return tools.get_feature_table()

    @beta_tool
    def search_parts(query: str) -> str:
        """Look up a replacement part or a manual.

        Args:
            query: What to look for, for example a motor model and the part name.
        """
        return tools.search_parts(query)

    client = anthropic.Anthropic(timeout=TIMEOUT_SECONDS, max_retries=1)
    try:
        runner = client.beta.messages.tool_runner(
            model=MODEL,
            max_tokens=16000,
            system=SYSTEM,
            tools=[get_readings, get_feature_table, search_parts],
            max_iterations=MAX_ROUNDS,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={
                "effort": "medium",
                "format": {"type": "json_schema", "schema": schema},
            },
            messages=[
                {
                    "role": "user",
                    "content": f"Write the maintenance report for machine {machine_id}.",
                }
            ],
        )
        final = runner.until_done()
    except anthropic.RateLimitError as error:
        raise AgentUnavailable("rate limited") from error
    except anthropic.APIStatusError as error:
        raise AgentUnavailable(f"API error {error.status_code}") from error
    except anthropic.APIConnectionError as error:
        raise AgentUnavailable("no connection") from error

    if final.stop_reason != "end_turn":
        raise AgentUnavailable(f"stopped early: {final.stop_reason}")
    text = next((block.text for block in final.content if block.type == "text"), None)
    if text is None:
        raise AgentUnavailable("no report in the response")
    try:
        report = json.loads(text)
    except json.JSONDecodeError as error:
        raise AgentUnavailable("report was not valid JSON") from error

    usage = {
        "input_tokens": final.usage.input_tokens,
        "output_tokens": final.usage.output_tokens,
    }
    return report, usage


def _append_log(
    path: str | Path, machine_id: str, result: AgentResult, usage: dict, model: str = MODEL
) -> None:
    entry = {
        "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "machine_id": machine_id,
        "model": model,
        "usage": usage,
        **asdict(result),
    }
    with open(path, "a", encoding="utf-8") as log:
        log.write(json.dumps(entry) + "\n")
