"""Log page: what to show, as plain data and plain sentences. No widgets here, so every
rule on the page is tested without a window.

Three rules from docs/scan-and-log-plan.md are kept in this file:

- `nothing_found` reads "Nothing found", and its note says it does not mean healthy.
- A part is a suspect only because a person linked a fault type to it, so a suspect
  is always written "possible".
- A review is advice. `agree_draft` only fills a form; a person adds the entry.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from app.data import logic

DASH = "–"

# Contract section 8.5.
PART_KINDS = ("motor", "bearing", "roller", "belt", "gearbox", "coupling", "fan", "pump", "mount", "frame",
              "probe_point", "other")
# Contract section 8.6. A person writes the first four; `review` is the agent's.
PERSON_KINDS = ("finding", "inspection", "repair", "note")
ENTRY_KINDS = (*PERSON_KINDS, "review")
NEEDS_ANSWER = ("finding", "inspection")  # these must say whether the part needs repair
DEFAULT_KIND = "note"
ALL = "all"

STATE_TEXT = {
    "needs_repair": "Needs repair",
    "possible": "Possible",
    "nothing_found": "Nothing found",
    "not_checked": "Not checked",
}
STATE_NOTE = {
    "needs_repair": "A finding or an inspection says it needs repair, and no repair is logged since.",
    "possible": "An open or unanswered alarm has a fault type that a person linked to this part. "
                "Not confirmed: the probe does not locate a fault inside the machine.",
    "nothing_found": "The probe has been on the machine and nothing points at this part. "
                     "That does not mean the part is healthy.",
    "not_checked": "The probe has not been on this machine since the part was made or repaired.",
}
NO_STATE = "State not reported"

WHOLE_MACHINE = "Whole machine"
WHOLE_NOTE = "Every entry for this machine: each part's, and those about the machine as a whole."
NO_MACHINE = "No machine yet. One appears when a probe reports or a scan arrives."
NO_PARTS = ("No parts yet. Mark them on the scan on the Machine page, or add one here: "
            "a part needs no pin to have a log.")
OPEN_MACHINE = "Open Machine page"
ADD_PART = "Add part"
SHOW_ON_MACHINE = "Show on machine"
EXPORT = "Export CSV"

NO_ALARMS = "No alarm is waiting for a part."
NO_SUSPECT = "No part is linked to this fault type."
ANSWER = "Answer"
SAVE_ANSWER = "Save answer"
PICK_PART = "Which part?"
FINDING_HINT = "What was found (optional)"

MODES = [("standin", "Stand-in"), ("local", "Local model")]
RUN_REVIEW = "Run part review"
RUN_ALL = "Review every part"
REVIEWING = "Reviewing…"
AGREE = "Agree: add a finding"
ADVICE_NOTE = "Advice, not a decision. Nothing is added to the log unless you add it."
ADVICE = {"yes": "Yes", "no": "No", "not_sure": "Not sure"}
SIMULATED = "simulated"


def parse_time(iso) -> datetime | None:
    if not isinstance(iso, str) or not iso:
        return None
    try:
        at = datetime.fromisoformat(iso)
    except ValueError:
        return None
    return at if at.tzinfo else at.replace(tzinfo=UTC)


def when(iso, tz=None) -> str:
    """"14 Nov 16:05" in local time (or `tz`), or a dash."""
    at = parse_time(iso)
    if at is None:
        return DASH
    at = at.astimezone(tz)
    return f"{at.day} {at:%b %H:%M}"


def words(name) -> str:
    """"probe_point" -> "probe point"."""
    return str(name or "").replace("_", " ")


def kind_label(kind) -> str:
    return logic.capitalise(words(kind))


def state_name(part) -> str:
    state = (part or {}).get("state")
    return state.get("name") or "" if isinstance(state, dict) else ""


def state_text(name) -> str:
    """The words for a part state. A state this app does not know is shown as sent."""
    return STATE_TEXT.get(name) or (logic.capitalise(words(name)) if name else NO_STATE)


def state_line(part, tz=None) -> str:
    """"Needs repair since 14 Nov 16:05". A state with no `since` is its words alone."""
    name = state_name(part)
    since = ((part or {}).get("state") or {}).get("since")
    text = state_text(name)
    return f"{text} since {when(since, tz)}" if parse_time(since) else text


def state_note(part) -> str:
    return STATE_NOTE.get(state_name(part), "")


# ---- the parts list ---------------------------------------------------------


@dataclass(frozen=True)
class PartRow:
    part_id: str | None  # None is the whole machine
    name: str
    state: str  # the state's name, for the mark; "" draws none
    line: str
    selected: bool


def part_rows(parts: list, selected, entry_count: int, tz=None) -> list[PartRow]:
    """One row a part, then the whole machine. Nothing when there are no parts."""
    if not parts:
        return []
    rows = []
    for p in parts:
        line = state_line(p, tz) + ("" if p.get("position") else ", no pin")
        rows.append(PartRow(p["part_id"], p.get("name") or p["part_id"], state_name(p), line,
                            p["part_id"] == selected))
    count = "no entries yet" if entry_count == 0 else "1 entry" if entry_count == 1 else f"{entry_count} entries"
    rows.append(PartRow(None, WHOLE_MACHINE, "", count, selected is None))
    return rows


@dataclass(frozen=True)
class Heading:
    title: str
    hint: str
    state: str
    line: str
    note: str


def heading(part, machine, tz=None) -> Heading:
    """The log panel's title and the state line under it, for a part or the whole machine."""
    if part is None:
        return Heading(WHOLE_MACHINE, str(machine or ""), "", "", WHOLE_NOTE if machine else NO_MACHINE)
    return Heading(part.get("name") or part["part_id"], words(part.get("kind")), state_name(part),
                   state_line(part, tz), state_note(part))


# ---- the entry table --------------------------------------------------------


@dataclass(frozen=True)
class EntryRow:
    entry_id: str
    when: str
    part: str
    kind: str
    text: str
    needs_repair: str
    flag: bool  # this entry says the part needs repair
    by: str
    alarm: str
    tag: str  # "simulated" or ""


def entry_rows(entries: list, part_names: dict, tz=None) -> list[EntryRow]:
    rows = []
    for e in entries:
        needs = e.get("needs_repair")
        rows.append(EntryRow(
            entry_id=str(e.get("entry_id") or ""),
            when=when(e.get("at"), tz),
            part=part_names.get(e.get("part_id"), WHOLE_MACHINE if e.get("part_id") is None else str(e["part_id"])),
            kind=kind_label(e.get("kind")),
            text=str(e.get("text") or "") or DASH,
            needs_repair="yes" if needs is True else "no" if needs is False else DASH,
            flag=needs is True,
            by=str(e.get("by") or DASH),
            alarm=str(e.get("alarm_id") or DASH),
            tag=SIMULATED if e.get("simulated") is True else "",
        ))
    return rows


def count_text(shown: int, kind: str) -> str:
    what = "entry" if kind == ALL else f"{words(kind)} entry"
    return f"{shown} {what}" if shown == 1 else f"{shown} {what[:-1]}ies"


def empty_text(part, kind: str, machine) -> str:
    """Why the table is empty, and what to do about it."""
    if not machine:
        return NO_MACHINE
    whose = f"for {part.get('name') or part['part_id']}" if part else "for this machine"
    if kind != ALL:
        return f"No {words(kind)} entries {whose}."
    return f"No entries yet {whose}. Add the first one above: pick a kind, type what happened, press Enter."


def entry_problem(kind, text, needs_repair) -> str:
    """Why this entry cannot be added yet, or "" when it can (contract section 8.6)."""
    if kind in NEEDS_ANSWER:
        return "" if needs_repair in (True, False) else "Say whether it needs repair."
    return "" if str(text or "").strip() else "Type what happened."


def entry_placeholder(kind, part) -> str:
    about = (part.get("name") or part["part_id"]) if part else "the whole machine"
    optional = " (optional)" if kind in NEEDS_ANSWER else ""
    return f"{kind_label(kind)} about {about}{optional}"


def csv_name(machine) -> str:
    return f"{machine or 'machine'}-log.csv"


# ---- alarms waiting for a part ----------------------------------------------


@dataclass(frozen=True)
class AlarmRow:
    alarm_id: str
    title: str
    when: str
    suspects: str
    simulated: bool


def suspects_text(suspects: list) -> str:
    """Every suspect is "possible": the link is a person's, and nobody has confirmed it."""
    names = [p.get("name") or p["part_id"] for p in suspects]
    return "possible: " + ", ".join(names) if names else NO_SUSPECT


def alarm_rows(alarms: list, suspects_of, tz=None) -> list[AlarmRow]:
    rows = []
    for a in alarms:
        at = when(a.get("start"), tz)
        rows.append(AlarmRow(a["alarm_id"], logic.display_name(a.get("diagnosis")) or "Alarm",
                             f"{at}, still going" if a.get("open") else at,
                             suspects_text(suspects_of(a)), a.get("simulated") is True))
    return rows


def part_choices(parts: list, suspects: list) -> list[tuple[str, str]]:
    """(part_id, label) for the answer form: suspects first and marked, then the rest."""
    suspect_ids = [p["part_id"] for p in suspects]
    first = [p for p in parts if p["part_id"] in suspect_ids]
    rest = [p for p in parts if p["part_id"] not in suspect_ids]
    return ([(p["part_id"], f"{p.get('name') or p['part_id']} (possible)") for p in first]
            + [(p["part_id"], p.get("name") or p["part_id"]) for p in rest])


def answer_problem(verdict, part_id, parts: list) -> str:
    if not parts:
        return "Add a part first: the answer names one."
    if verdict not in logic.VERDICT_TEXT:
        return "Say whether it was a real problem."
    return "" if part_id else "Pick the part."


# ---- the review panel -------------------------------------------------------

NO_PART, NO_REVIEW, RUNNING, FAILED, DONE = "no_part", "no_review", "running", "failed", "done"


@dataclass(frozen=True)
class Review:
    status: str
    message: str
    rows: tuple = ()  # (label, value) pairs of a review that passed its checks
    simulated: bool = False
    can_agree: bool = False


def _review_rows(run: dict, tz=None) -> tuple:
    r = run["review"]
    cited = [*(r.get("cited_alarms") or []), *(r.get("cited_entries") or [])]
    advice = ADVICE.get(r.get("needs_repair"), DASH)
    return (
        ("Needs repair", f"{advice} (advice)"),
        ("What", str(r.get("what") or DASH)),
        ("How it comes about", str(r.get("how") or DASH)),
        ("Since", when(r.get("since"), tz)),
        ("Why", str(r.get("why") or DASH)),
        ("Cited", ", ".join(str(c) for c in cited) or "nothing"),
        ("Written", f"{when(run.get('at'), tz)} by {run.get('model') or 'an unnamed model'}"),
    )


def review(part, run, running: bool, tz=None, *, has_parts: bool = True) -> Review:
    """What the review panel shows. A finished review stays on screen while the next
    one runs, and a discarded one says why."""
    if part is None:
        return Review(NO_PART, "Select a part to read its review. A review is written for one part at a time."
                      if has_parts else "No parts yet, so there is nothing to review.")
    name = part.get("name") or part["part_id"]
    passed = bool(run and run.get("status") == "done" and run.get("ok") is True and isinstance(run.get("review"), dict))
    rows = _review_rows(run, tz) if passed else ()
    simulated = bool(passed and run.get("simulated") is True)
    if running or (run and run.get("status") == "running"):
        tail = " The review below is the previous one." if passed else ""
        return Review(RUNNING, f"Reviewing {name}…{tail}", rows, simulated, False)
    if not run:
        return Review(NO_REVIEW, f"No review yet for {name}. Run the part review to get advice on this part. "
                                 "It reads only this part's own history.")
    if not passed:
        reason = str(run.get("reason") or "").strip() or "no reason was given"
        return Review(FAILED, f"The last review of {name} was discarded: {reason}. Nothing was added to the log.")
    return Review(DONE, ADVICE_NOTE, rows, simulated, True)


def agree_draft(run) -> tuple[str, bool | None]:
    """The finding a person may add after reading a review: its text, to be edited, and
    whether it needs repair. `not_sure` leaves that for the person to choose."""
    r = (run or {}).get("review") or {}
    needs = {"yes": True, "no": False}.get(r.get("needs_repair"))
    return str(r.get("what") or ""), needs
