"""Run the maintenance agent for one machine and check what it returns."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .data import load_feature_table, load_log, summarize_readings
from .report import build_report_schema, check_report

MODEL = "claude-opus-5-5"
MAX_ROUNDS = 6
TIMEOUT_SECONDS = 60.0

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


# (machine_id, records, features, schema) -> (report, usage)
Call = Callable[[str, list[dict], dict[str, str], dict], tuple[dict, dict]]


def run_agent(
    machine_id: str,
    log_path: str | Path,
    model_path: str | Path,
    agent_log_path: str | Path,
    call: Call | None = None,
) -> AgentResult:
    """Produce a checked maintenance report for one machine.

    Never raises for network or API trouble: the result says why there is no report.
    """
    features = load_feature_table(model_path)
    records = load_log(log_path, machine_id)
    if not records:
        return AgentResult(ok=False, reason="no readings for this machine")
    try:
        schema = build_report_schema(list(features))
    except ValueError as error:
        return AgentResult(ok=False, reason=str(error))

    usage: dict = {}
    try:
        report, usage = (call or call_claude)(machine_id, records, features, schema)
    except AgentUnavailable as error:
        result = AgentResult(ok=False, reason=f"agent unavailable: {error}")
    else:
        problems = check_report(report, records, list(features))
        if problems:
            result = AgentResult(ok=False, problems=problems, reason="report failed checks")
        else:
            result = AgentResult(ok=True, report=report)

    _append_log(agent_log_path, machine_id, result, usage)
    return result


def call_claude(
    machine_id: str, records: list[dict], features: dict[str, str], schema: dict
) -> tuple[dict, dict]:
    """Ask Claude for the report, letting it call the tools it needs."""
    import anthropic
    from anthropic import beta_tool

    @beta_tool
    def get_readings(since: str = "") -> str:
        """Summarise the device's readings for this machine.

        Args:
            since: Optional ISO 8601 UTC time. Only readings at or after it are used.
        """
        chosen = [r for r in records if not since or r.get("at", "") >= since]
        return json.dumps(summarize_readings(chosen))

    @beta_tool
    def get_feature_table() -> str:
        """List the verified features and the sentence that explains each one."""
        return json.dumps(features)

    @beta_tool
    def search_parts(query: str) -> str:
        """Look up a replacement part or a manual.

        Args:
            query: What to look for, for example a motor model and the part name.
        """
        # TODO: connect Tavily.
        return "Parts lookup is not connected yet. Leave part_reference empty."

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


def _append_log(path: str | Path, machine_id: str, result: AgentResult, usage: dict) -> None:
    entry = {
        "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "machine_id": machine_id,
        "model": MODEL,
        "usage": usage,
        **asdict(result),
    }
    with open(path, "a", encoding="utf-8") as log:
        log.write(json.dumps(entry) + "\n")
