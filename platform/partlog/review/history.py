"""One part's history, and the fact sheet built from it.

A machine's log data is read from a file (tests, `--history`) or from the
receiver. `part_history` keeps only what belongs to one part. `fact_sheet`
turns that into the plain facts the writer of a review is given. Every check
in review.py is made against that fact sheet: it is "the history supplied".
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

FETCH_TIMEOUT_SECONDS = 5.0
ENTRY_LIMIT = 2000  # the most the log route gives (contract section 8.6)
ALARM_LIMIT = 2000
# Entry and alarm fields a planner would read. Anything else stays out of the fact sheet.
ENTRY_FIELDS = ("entry_id", "at", "kind", "text", "by", "needs_repair", "alarm_id", "diagnosis", "source", "simulated")
ALARM_FIELDS = ("alarm_id", "start", "end", "open", "diagnosis", "periods", "peak_unknown_score", "simulated")
ANSWER_FIELDS = ("verdict", "by", "at", "finding")
STATE_FIELDS = ("name", "since", "reason", "alarm_id", "entry_id")


class HistoryUnavailable(Exception):
    """The part's history could not be read. The message is a plain reason."""


def read_file(path: str | Path, machine_id: str) -> dict:
    """A machine's log data from a JSON file: `parts`, `entries`, `alarms`, `fault_types`.

    `fault_types` is the list from the model announcement (contract message 1).
    """
    try:
        with open(path, encoding="utf-8") as history_file:
            data = json.load(history_file)
    except (OSError, ValueError) as error:
        raise HistoryUnavailable(f"could not read {path}: {error}") from error
    if not isinstance(data, dict) or data.get("machine_id") != machine_id:
        raise HistoryUnavailable(f"{path} holds no history for {machine_id}")
    return {
        "machine_id": machine_id,
        "parts": list(data.get("parts") or []),
        "entries": list(data.get("entries") or []),
        "alarms": list(data.get("alarms") or []),
        "fault_types": list(data.get("fault_types") or []),
    }


def fetch(platform_url: str, machine_id: str, part_id: str | None = None) -> dict:
    """The same data from the receiver (contract sections 5, 6, 8.5 and 8.6).

    With a `part_id`, only that part's entries are asked for. Without one, no
    entries are read: enough to list the machine's parts.

    UNVERIFIED on master: the parts and log routes were stubs there when this
    was written. Run once by hand against the routes of branch
    track5/part-store, and against a fake receiver in the tests.
    """
    base = platform_url.rstrip("/")
    machine = urllib.parse.quote(machine_id, safe="")
    parts = get_json(f"{base}/api/machines/{machine}/parts?removed=1").get("parts")
    entries = []
    if part_id is not None:
        part = urllib.parse.quote(part_id, safe="")
        entries = get_json(f"{base}/api/machines/{machine}/log?part={part}&limit={ENTRY_LIMIT}").get("entries")
    alarms = get_json(f"{base}/api/machines/{machine}/alarms?limit={ALARM_LIMIT}").get("alarms")
    models = get_json(f"{base}/api/state").get("models")
    return {
        "machine_id": machine_id,
        "parts": parts if isinstance(parts, list) else [],
        "entries": entries if isinstance(entries, list) else [],
        "alarms": alarms if isinstance(alarms, list) else [],
        "fault_types": _fault_types(models if isinstance(models, dict) else {}, machine_id),
    }


def _fault_types(models: dict, machine_id: str) -> list[dict]:
    """Every fault type this machine's model announcements name. A later announcement's text wins."""
    by_name: dict[str, dict] = {}
    for model in models.values():
        if isinstance(model, dict) and model.get("machine_id") == machine_id:
            for fault_type in model.get("fault_types") or []:
                if isinstance(fault_type, dict) and fault_type.get("name"):
                    by_name[str(fault_type["name"])] = fault_type
    return list(by_name.values())


def get_json(url: str) -> dict:
    try:
        with urllib.request.urlopen(url, timeout=FETCH_TIMEOUT_SECONDS) as response:
            reply = json.loads(response.read())
    except urllib.error.HTTPError as error:
        path = urllib.parse.urlsplit(url).path
        raise HistoryUnavailable(f"the receiver refused {path}: {_refusal(error)}") from error
    except (OSError, ValueError) as error:
        cause = getattr(error, "reason", error)
        if isinstance(cause, ConnectionRefusedError):
            raise HistoryUnavailable(f"no receiver is running at {_origin(url)}") from error
        raise HistoryUnavailable(f"could not read from the receiver at {_origin(url)}: {cause}") from error
    if not isinstance(reply, dict):
        raise HistoryUnavailable("the receiver's answer was not a JSON object")
    return reply


def _refusal(error: urllib.error.HTTPError) -> str:
    try:
        return str(json.loads(error.read())["error"])
    except Exception:
        return f"HTTP {error.code}"


def _origin(url: str) -> str:
    split = urllib.parse.urlsplit(url)
    return f"{split.scheme}://{split.netloc}"


def part_ids(data: dict) -> list[str]:
    """The machine's parts that are not removed, in the order they were made."""
    return [p["part_id"] for p in data["parts"] if isinstance(p, dict) and p.get("part_id") and not p.get("removed")]


def part_history(data: dict, part_id: str) -> dict:
    """Only what belongs to one part: the part, its own entries, the alarms linked to it.

    An alarm is linked when one of the part's entries or its state names it,
    or when it is waiting on this part: open or unanswered, started after the
    part's newest repair, with a diagnosis in the part's `fault_types` (the
    `possible` rule of contract section 8.7).

    The other parts' names and the announced fault type names are kept too,
    for the checks only. They never reach the fact sheet.
    """
    parts = [p for p in data["parts"] if isinstance(p, dict)]
    part = next((p for p in parts if p.get("part_id") == part_id), None)
    if part is None:
        raise HistoryUnavailable(f"{data['machine_id']} has no part {part_id}")
    entries = sorted(
        (e for e in data["entries"] if isinstance(e, dict) and e.get("part_id") == part_id),
        key=lambda e: parse_time(e.get("at")),
    )
    state = part.get("state") if isinstance(part.get("state"), dict) else {}
    named = {e.get("alarm_id") for e in entries} | {state.get("alarm_id")}
    repairs = [parse_time(e.get("at")) for e in entries if e.get("kind") == "repair"]
    last_repair = max(repairs, default=None)
    suspects = set(part.get("fault_types") or [])

    def waiting(alarm: dict) -> bool:
        return (
            (alarm.get("open") is True or alarm.get("answer") is None)
            and alarm.get("diagnosis") in suspects
            and (last_repair is None or parse_time(alarm.get("start")) > last_repair)
        )

    alarms = sorted(
        (a for a in data["alarms"] if isinstance(a, dict) and a.get("alarm_id")
         and (a["alarm_id"] in named or waiting(a))),
        key=lambda a: parse_time(a.get("start")),
    )
    return {
        "machine_id": data["machine_id"],
        "part": part,
        "entries": entries,
        "alarms": alarms,
        "fault_types": [f for f in data["fault_types"] if isinstance(f, dict)],
        "other_parts": [{"part_id": p.get("part_id"), "name": p.get("name")} for p in parts if p is not part],
    }


def fact_sheet(history: dict) -> dict:
    """The plain facts about one part that the writer of a review is given.

    Counts come first, so they survive when a run's tool output is cut short.
    `fault_type` is the fault type behind the part's state, with its own text
    from the model announcement, or `None` when there is none.
    """
    part, entries, alarms = history["part"], history["entries"], history["alarms"]
    state = part.get("state") if isinstance(part.get("state"), dict) else {}
    answered = [a for a in alarms if isinstance(a.get("answer"), dict)]
    return {
        "counts": {
            "entries": len(entries),
            "findings": sum(e.get("kind") == "finding" for e in entries),
            "inspections": sum(e.get("kind") == "inspection" for e in entries),
            "repairs": sum(e.get("kind") == "repair" for e in entries),
            "entries_saying_needs_repair": sum(e.get("needs_repair") is True for e in entries),
            "alarms": len(alarms),
            "alarms_open": sum(a.get("open") is True for a in alarms),
            "alarms_unanswered": len(alarms) - len(answered),
            "alarms_waiting": sum(a.get("open") is True or not isinstance(a.get("answer"), dict) for a in alarms),
            "alarms_answered_real": sum(a["answer"].get("verdict") == "real" for a in answered),
            "alarms_answered_false": sum(a["answer"].get("verdict") == "false" for a in answered),
        },
        "machine_id": history["machine_id"],
        "part": {
            "part_id": part.get("part_id"),
            "name": part.get("name"),
            "kind": part.get("kind"),
            "fault_types": list(part.get("fault_types") or []),
            "note": part.get("note") or "",
        },
        "state": {key: state.get(key) for key in STATE_FIELDS},
        "fault_type": _fault_type(history, state),
        "entries": [{key: entry.get(key) for key in ENTRY_FIELDS} for entry in entries],
        "alarms": [
            {
                **{key: alarm.get(key) for key in ALARM_FIELDS},
                "answer": {key: alarm["answer"].get(key) for key in ANSWER_FIELDS}
                if isinstance(alarm.get("answer"), dict) else None,
            }
            for alarm in alarms
        ],
    }


def _fault_type(history: dict, state: dict) -> dict | None:
    """The diagnosis behind the part's state: its entry's if it has one, else its alarm's.

    The text is the fault type's own, from the model announcement. A diagnosis
    the announcement does not list, such as `unrecognised_pattern`, has no text.
    """
    entry = next((e for e in history["entries"] if e.get("entry_id") == state.get("entry_id")), None)
    alarm = next((a for a in history["alarms"] if a.get("alarm_id") == state.get("alarm_id")), None)
    diagnosis = (entry or {}).get("diagnosis") or (alarm or {}).get("diagnosis")
    if not diagnosis:
        return None
    announced = next((f for f in history["fault_types"] if f.get("name") == diagnosis), {})
    return {
        "name": diagnosis,
        "text": str(announced.get("text") or ""),
        "validated": announced.get("validated") is True,
    }


def is_simulated(history: dict) -> bool:
    """Whether any alarm or person's entry in the history is simulated. Earlier reviews do not count."""
    return any(a.get("simulated") is True for a in history["alarms"]) or any(
        e.get("simulated") is True for e in history["entries"] if e.get("kind") != "review"
    )


def parse_time(value) -> datetime:
    """An ISO 8601 time for sorting. A missing or unreadable one sorts first."""
    try:
        at = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)
