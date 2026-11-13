"""What the Agents page shows, worked out from the runs (CONTRACT.md section 4).

No Qt here, so it can be tested without a display.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

FLEET = [
    ("maintenance_report", True),
    ("setup (later)", False),
    ("review (later)", False),
]
MODES = [("standin", "Stand-in"), ("live", "Live Claude")]
IO_EMPTY = "Select a tool call to see its full input and output."
NO_CALLS = "This run made no tool calls."
NO_RUNS = "No runs yet for this machine."


def parse_time(iso) -> datetime | None:
    if not isinstance(iso, str) or not iso:
        return None
    try:
        return datetime.fromisoformat(iso).astimezone()
    except ValueError:
        return None


def clock(iso, seconds: bool = True) -> str:
    t = parse_time(iso)
    if t is None:
        return "–"
    return t.strftime("%H:%M:%S" if seconds else "%H:%M")


def run_status(run: dict) -> str:
    if run.get("status") == "running":
        return "running"
    return "report ready" if run.get("ok") is True and run.get("report") else "no report"


@dataclass(frozen=True)
class RunRow:
    run_id: str
    time: str
    who: str
    status: str


def run_rows(runs: list[dict]) -> list[RunRow]:
    """Newest first, as the state lists them."""
    out = []
    for r in runs or []:
        if not isinstance(r, dict) or not r.get("run_id"):
            continue
        who = " · ".join(x for x in (r.get("agent") or "", r.get("model") or "") if x)
        out.append(RunRow(r["run_id"], clock(r.get("at"), seconds=False), who, run_status(r)))
    return out


def usage_hint(run: dict | None) -> str:
    if not run:
        return ""
    parts = [x for x in (run.get("agent") or "", run.get("model") or "") if x]
    usage = run.get("usage") or {}
    if isinstance(usage.get("input_tokens"), int) and isinstance(usage.get("output_tokens"), int):
        parts.append(f"{usage['input_tokens']} in / {usage['output_tokens']} out tokens")
    return " · ".join(parts)


def compact(value) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


@dataclass(frozen=True)
class CallRow:
    time: str
    name: str
    input: str
    output: str


def call_rows(run: dict | None) -> list[CallRow]:
    out = []
    for c in (run or {}).get("tool_calls") or []:
        if isinstance(c, dict):
            out.append(CallRow(clock(c.get("at")), str(c.get("name", "")), compact(c.get("input", {})), str(c.get("output", ""))))
    return out


def call_io(run: dict | None, index: int | None) -> str:
    calls = (run or {}).get("tool_calls") or []
    if index is None or not 0 <= index < len(calls):
        return IO_EMPTY
    c = calls[index]
    pretty = json.dumps(c.get("input", {}), indent=2, ensure_ascii=False)
    return f"{c.get('name', '')}\n\ninput\n{pretty}\n\noutput\n{c.get('output', '')}"


def report_fields(run: dict | None, texts: dict[str, str]) -> list[tuple[str, object]]:
    """(label, value) rows for the report panel. Evidence's value is a list of (text, feature name)."""
    if not run:
        return []
    rep = run.get("report")
    if run.get("status") == "running":
        return [("Status", "running")]
    if run.get("ok") is True and isinstance(rep, dict):
        cited = [n for n in rep.get("cited_features") or [] if isinstance(n, str)]
        return [
            ("Priority", (rep.get("priority") or "").capitalize() or "–"),
            ("Summary", rep.get("summary") or ""),
            ("Evidence", [(texts.get(n, ""), n) for n in cited] or "none"),
            ("Action", rep.get("recommended_action") or "–"),
            ("Part", rep.get("part_reference") or "none found"),
            ("Checks", "all passed"),
        ]
    problems = [str(p) for p in run.get("problems") or []]
    return [
        ("Status", "no report"),
        ("Reason", run.get("reason") or "–"),
        ("Problems", "; ".join(problems) or "none"),
    ]
