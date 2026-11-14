"""What the Agents page shows, worked out from the runs (CONTRACT.md section 4).

No Qt here, so it can be tested without a display.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.data import model_server
from app.data.model_settings import SIZE_NAMES

FLEET = [
    ("maintenance_report", True),
    ("setup (later)", False),
    ("review (later)", False),
]
MODES = [("standin", "Stand-in"), ("local", "Local model")]
MODE_NOTES = {
    "standin": "Fixed rules write the report. No model is used, so it is labelled simulated.",
    "local": "A model on this laptop writes the report. Nothing leaves the site. It can take a minute or two.",
}
RUN = "Run Report Agent"
RUNNING = "Writing the report…"
RUNS_HEADING = "Reports for this machine"
SIZES = SIZE_NAMES
START_SERVER = "Start model server"
STOP_SERVER = "Stop model server"
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


def short_model(model) -> str:
    """A run's model with a file path cut to the file's name: 'local: /a/b/small.gguf' is 'local: small.gguf'."""
    text = str(model or "")
    head, sep, rest = text.partition(": ")
    if sep and "/" in rest:
        return head + sep + rest.rsplit("/", 1)[1]
    return text


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
        who = " · ".join(x for x in (r.get("agent") or "", short_model(r.get("model"))) if x)
        out.append(RunRow(r["run_id"], clock(r.get("at"), seconds=False), who, run_status(r)))
    return out


def usage_hint(run: dict | None) -> str:
    if not run:
        return ""
    parts = [x for x in (run.get("agent") or "", short_model(run.get("model"))) if x]
    usage = run.get("usage") or {}
    if isinstance(usage.get("input_tokens"), int) and isinstance(usage.get("output_tokens"), int):
        parts.append(f"{usage['input_tokens']} in / {usage['output_tokens']} out tokens")
    return " · ".join(parts)


def compact(value) -> str:
    """One line of JSON for the table; an input with nothing in it is a dash."""
    if value in ({}, [], None, ""):
        return "–"
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
    reason = run.get("reason") or ""
    rows = [
        ("Status", "no report"),
        ("Reason", reason or "–"),
        ("Problems", "\n".join(problems) or "none"),
    ]
    step = next_step(reason)
    if step:
        rows.append(("Next", step))
    return rows


def next_step(reason: str) -> str:
    """What the person can do about a run with no report, for the reasons the agent gives
    (device/agent/README.md). Empty when the reason is not one of them."""
    if "no model server is running" in reason:
        return "Start the model server under Local model, then run again. Stand-in needs no model."
    if "did not answer within" in reason:
        return "The model took too long. Run again, or pick the Small model and restart its server."
    if "report failed checks" in reason:
        return "The model's report broke a check, so it was not shown. Run again."
    return ""


# ---- the local model --------------------------------------------------------


def short_folder(folder: str) -> str:
    """The folder with the home folder written as ~."""
    home = str(Path.home())
    text = str(folder)
    if text == home or text.startswith(home + "/"):
        return "~" + text[len(home):]
    return text


def model_rows(folder: str, size: str, status) -> list[tuple[str, str]]:
    """(label, value) rows for the local model: its folder, its size and the server's state."""
    file = model_server.model_file(folder, size).name
    server = status.message
    if status.ours and status.model and status.model != file:
        server += f" Stop and start it to load {file}."
    return [
        ("Folder", short_folder(folder)),
        ("Size", f"{dict(SIZES).get(size, size)}, {file}"),
        ("Server", server),
    ]


def server_button(status) -> tuple[str, bool]:
    """(text, enabled) for the model server's button. A server this app did not
    start is not this app's to stop, and a second one is never started."""
    if status.ours:
        return STOP_SERVER, True
    return START_SERVER, status.state not in (model_server.READY, model_server.LOADING)
