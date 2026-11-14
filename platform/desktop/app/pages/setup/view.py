"""Setup page: what to show, as plain data and plain sentences. No widgets here, so
every rule on the page is tested without a window.

Rules kept in this file (platform/CONTRACT.md section 10, docs/scan-and-log-plan.md 16.3):

- A field nobody knows is "Empty". Never a guess, never a default.
- The starting model is picked by code. Its words come from the lookup's reply,
  never from the agent. "Trained on", "checked on" and "not checked on" are kept
  apart, and an empty `checked_on` reads "nothing yet".
- The wanted entry is never shown as the one in use. Both are shown until they match.
- The stand-in's result is labelled as the stand-in's, and as simulated.
- The local profile is a name and a role on this laptop. The page calls it
  "local profile" and nothing else.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

from app.data.setup import FIELDS

EMPTY = "Empty"
DONT_KNOW = "I don't know"

# ---- step 1: the questions -------------------------------------------------


@dataclass(frozen=True)
class Question:
    id: str
    text: str
    example: str  # the placeholder


# Plain words, one for each field of the machine profile. None asks for a library entry.
QUESTIONS = (
    Question("kind", "What kind of machine is it?", "For example: a belt conveyor, a fan, a pump"),
    Question("drive", "How does the motor turn it?", "Straight on the shaft, through a belt, or through a gearbox"),
    Question("speed", "How fast does it turn, in turns a minute?", "For example: 1450, or 1400 to 1500"),
    Question("mounting", "How is it held in place?", "Bolted down hard, or on rubber feet or springs"),
    Question("sensor", "Where is the sensor fixed?", "On the motor at the drive end, on the gearbox, or on the frame"),
)


def answers(typed: dict, dont_know: set) -> list[dict]:
    """What start_run gets: each question with what was typed. "I don't know", or
    nothing typed, is `None`: the agent then leaves that field empty."""
    out = []
    for q in QUESTIONS:
        text = " ".join(str(typed.get(q.id) or "").split())
        out.append({"id": q.id, "question": q.text, "answer": None if q.id in dont_know or not text else text})
    return out


def answered_text(items: list[dict]) -> str:
    known = sum(1 for a in items if a["answer"] is not None)
    unknown = len(items) - known
    if not known:
        return "Nothing answered yet"
    return f"{known} answered, {unknown} not known" if unknown else f"All {known} answered"


FILL = "Fill the profile"
FILLING = "Reading your answers…"
STANDIN_NOTE = ("No model server is running, so the stand-in fills it. It only matches words, and what it "
                "fills is marked as the stand-in's.")
NO_MACHINE = "No machine yet. One appears when a probe reports or a scan arrives."


def mode_note(ready: bool, model: str = "") -> str:
    if not ready:
        return STANDIN_NOTE
    return f"The local model{f' ({model})' if model else ''} fills it, on this laptop. Nothing leaves the site."


# ---- step 1, below the button: the run ---------------------------------------

NOT_RUN, RUNNING, FAILED, DONE = "not_run", "running", "failed", "done"


@dataclass(frozen=True)
class Run:
    status: str
    title: str
    text: str
    standin: bool = False


def run(r, running: bool) -> Run:
    if running:
        return Run(RUNNING, "Reading your answers", "The agent is filling the five fields. Nothing is stored or "
                   "sent while it runs.")
    if r is None:
        return Run(NOT_RUN, "", "")
    standin = bool(r.get("simulated"))
    if not r.get("ok") or not isinstance(r.get("setup"), dict):
        reason = str(r.get("reason") or "").strip().rstrip(".") or "it gave no reason"
        return Run(FAILED, "The agent could not fill the profile",
                   f"Why: {reason}. Nothing was filled. Fill the five fields by hand in step 2.", standin)
    text = str(r["setup"].get("explanation") or "").strip() or "The agent gave no explanation."
    return Run(DONE, "What the stand-in understood" if standin else "What the agent understood", text, standin)


# ---- step 2: the machine profile ---------------------------------------------

# Contract 10.1's value lists, in plain words. The stored value is the key.
CHOICES: dict[str, tuple[tuple[str, str], ...]] = {
    "machine_type": (("conveyor", "Conveyor"), ("fan", "Fan"), ("pump", "Pump"), ("motor", "Motor"),
                     ("other", "Other")),
    "drive": (("electric_motor_direct", "Motor, direct on the shaft"), ("belt", "Belt"), ("gearbox", "Gearbox")),
    "mounting": (("rigid", "Rigid, bolted down"), ("flexible", "Flexible, on rubber or springs")),
    "sensor_location": (("motor_drive_end", "Motor, drive end"), ("gearbox", "Gearbox"), ("frame", "Frame")),
}
LABELS = {
    "machine_type": "Machine type",
    "drive": "Drive",
    "speed_rpm": "Speed, rpm",
    "mounting": "Mounting",
    "sensor_location": "Sensor location",
}
assert tuple(LABELS) == FIELDS

SPEED_HINT = "A number above 0, or a range such as 1400 to 1500"
_RANGE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(?:-|–|to)\s*(\d+(?:\.\d+)?)\s*$")
_ONE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*$")


def _num(text: str):
    v = float(text)
    return int(v) if v.is_integer() else v


def parse_speed(text) -> tuple[object, str]:
    """(value, problem). "" is None, "1450" is 1450, "1400 to 1500" is [1400, 1500]."""
    text = str(text or "").strip()
    if not text:
        return None, ""
    m = _ONE.match(text)
    if m:
        v = _num(m.group(1))
        return (v, "") if v > 0 else (None, SPEED_HINT)
    m = _RANGE.match(text)
    if m:
        lo, hi = _num(m.group(1)), _num(m.group(2))
        return ([lo, hi], "") if 0 < lo < hi else (None, SPEED_HINT)
    return None, SPEED_HINT


def speed_text(value) -> str:
    if isinstance(value, list | tuple) and len(value) == 2:
        return f"{value[0]:g} to {value[1]:g}"
    if isinstance(value, int | float) and not isinstance(value, bool):
        return f"{value:g}"
    return ""


def fields_of(d) -> dict:
    return {k: (d or {}).get(k) for k in FIELDS}


def empty_count(draft: dict) -> int:
    return sum(1 for k in FIELDS if draft.get(k) is None)


NOT_CONFIRMED = "not confirmed yet"
CONFIRMED = "confirmed"
CHANGED = "changed since it was confirmed"


def profile_hint(profile, draft: dict) -> str:
    if profile is None:
        return NOT_CONFIRMED
    return CONFIRMED if fields_of(profile) == fields_of(draft) else CHANGED


def profile_note(draft: dict) -> str:
    n = empty_count(draft)
    if n == len(FIELDS):
        return ("Every field is empty. That is allowed: the general starting model is picked. "
                "Fill what you know.")
    if n:
        return f"{n} left empty. An empty field is not an error: nobody guesses it."
    return ""


# ---- step 3: the starting model ------------------------------------------------


def _sources(items, with_note: bool = False) -> str:
    out = []
    for s in items or []:
        if not isinstance(s, dict):
            continue
        text = str(s.get("name") or "?")
        if s.get("kind"):
            text += f" ({s['kind']})"
        if with_note and s.get("note"):
            text += f": {s['note']}"
        out.append(text)
    return "\n".join(out)


def choice_title(choice) -> str:
    if not choice:
        return ""
    entry = choice.get("library_entry") or "?"
    return f"{entry} ({choice['status']})" if choice.get("status") else entry


CHOICE_KEYS = ("Why", "Version", "Trained on", "Checked on", "Not checked on", "Fault types validated")


def choice_rows(choice) -> list[tuple[str, str]]:
    """In the contract's words. Trained on and checked on stay two rows."""
    if not choice:
        return []
    validated = [str(v) for v in choice.get("fault_types_validated") or []]
    not_validated = choice.get("fault_types_not_validated")
    total = len(validated) + (not_validated if isinstance(not_validated, int) else 0)
    rows = [
        ("Why", str(choice.get("reason") or "No reason given.")),
        ("Version", str(choice.get("version") or "Not given")),
        ("Trained on", _sources(choice.get("trained_on")) or "Nothing listed"),
        ("Checked on", _sources(choice.get("checked_on")) or "Nothing yet"),
        ("Not checked on", _sources(choice.get("not_checked_on"), with_note=True) or "Nothing listed"),
        ("Fault types validated", f"{len(validated)} of {total}" + (f": {', '.join(validated)}" if validated else "")),
    ]
    assert tuple(k for k, _ in rows) == CHOICE_KEYS
    return rows


PICKED_BY_CODE = "Picked by code from a table, not by the agent. Nothing here is the agent's."
LOOKING_UP = "Looking up the starting model…"
USE = "Use this model"
SAVING_LINE = "Not confirmed yet. Nothing has been sent to the device."


def choice_warning(choice) -> str:
    """A plain line when the entry has been checked on nothing."""
    if not choice:
        return ""
    if not choice.get("checked_on"):
        return ("This starting model has been checked on nothing yet. Its alarms are a starting point, "
                "not a proven result.")
    return ""


def parse_time(iso):
    if not isinstance(iso, str) or not iso:
        return None
    try:
        at = datetime.fromisoformat(iso)
    except ValueError:
        return None
    return at if at.tzinfo else at.replace(tzinfo=UTC)


def when(iso, tz=None) -> str:
    at = parse_time(iso)
    if at is None:
        return ""
    at = at.astimezone(tz)
    return f"{at.day} {at:%b %H:%M}"


def confirm_line(profile, draft: dict, tz=None) -> str:
    hint = profile_hint(profile, draft)
    if hint == NOT_CONFIRMED:
        return SAVING_LINE
    if hint == CHANGED:
        return "Changed since it was confirmed. The change is not sent until you use this model again."
    c = (profile or {}).get("confirmed") or {}
    who = str(c.get("by") or "").strip()
    at = when(c.get("at"), tz)
    return "Confirmed" + (f" by {who}" if who else "") + (f" on {at}" if at else "") + "."


def use_problem(*, machine, choice, running: bool, speed_problem: str, profile, draft: dict) -> str:
    """Why "Use this model" cannot be pressed now, or ""."""
    if not machine:
        return NO_MACHINE
    if running:
        return "Wait for the agent to finish."
    if speed_problem:
        return f"Speed: {speed_problem}."
    if choice is None:
        return LOOKING_UP
    if profile_hint(profile, draft) == CONFIRMED:
        return "This profile is already confirmed."
    return ""


# ---- step 4: the device's answer -----------------------------------------------

NOTHING_SENT, NO_DEVICE, WAITING, DIFFERS, TAKEN = "nothing_sent", "no_device", "waiting", "differs", "taken"


@dataclass(frozen=True)
class Device:
    status: str
    line: str
    note: str


def device(wanted, in_use, has_device: bool) -> Device:
    if not wanted:
        return Device(NOTHING_SENT, "Nothing has been sent to the device.",
                      "The device is told the starting model only after you use it in step 3.")
    if not has_device:
        return Device(NO_DEVICE, f"Wanted: {wanted}. No device reports for this machine yet.",
                      "A device gets the wanted entry the next time it reports for this machine.")
    if not in_use:
        return Device(WAITING, f"Wanted: {wanted}. The device has not said which entry it is using yet.",
                      "It says so when it next sends its model. Until then nothing shows it took the wanted one.")
    if in_use != wanted:
        return Device(DIFFERS, f"Wanted: {wanted}. Device is using: {in_use}.",
                      "The device has not taken the wanted entry. The device side of this is not built yet, so "
                      "it keeps its own. To change it now, pick the entry on the device with its button.")
    return Device(TAKEN, f"Device is using: {in_use}, as wanted.", "")


# ---- the local profile ---------------------------------------------------------

LOCAL = "Local profile"
LOCAL_ASK = "Who is setting this up?"
LOCAL_WHY = ("Your name goes on the answers, entries and profiles you confirm from this laptop. It is kept "
             "on this laptop only. There is no password: anyone here can change it.")
NAME_HINT, ROLE_HINT = "Your name", "Your role, for example technician"
SAVE, CHANGE, CANCEL = "Save", "Change", "Cancel"


def local_text(local) -> str:
    if not local:
        return "No local profile yet."
    name, role = local.get("name") or "", local.get("role") or ""
    return f"{name}, {role}" if role else name


def local_hint(local) -> str:
    """The header line: "Sam, technician · kept on this laptop only"."""
    return f"{local_text(local)} \u00b7 kept on this laptop only" if local else "kept on this laptop only"


def fallback_note(answerer: str) -> str:
    return f"Until one is saved, your name is sent as \u201c{answerer}\u201d." if answerer else ""
