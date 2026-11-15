"""The Machine page's logic, as plain Python with no Qt: which pins to show, how each part's state
looks and reads, what a click on the model means, and what the page shows when there is no model.
Everything here runs in the offscreen tests; the 3D view (view3d.py) only draws what this decides.

Parts and scans are plain dicts shaped as in docs/scan-and-log-plan.md, 5.4 and 5.5.

Say only what we know (plan section 3): `nothing_found` is not "healthy". It means the probe has been on
the machine and nothing points at this part. `not_checked` means the probe has not been there. Neither
is ever drawn in a colour that reads as "good", and a link the probe suggests is called "possible".
"""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime

STATE_WORDS = {
    "needs_repair": "needs repair",
    "possible": "possible",
    "nothing_found": "nothing found",
    "not_checked": "not checked",
}
STATES = tuple(STATE_WORDS)


def state_name(part: dict) -> str:
    name = (part.get("state") or {}).get("name")
    return name if name in STATE_WORDS else "not_checked"


def pin_look(state: str, palette: dict) -> dict:
    """A pin's fill and ring for a part state. `palette` holds the theme's colours: "red", "text",
    "muted" and "bg". Red is used for shapes only (the theme's rule), and only for the two states that
    point at a problem:

        needs_repair    a solid red dot
        possible        a red ring, empty inside: a suspect, not confirmed
        nothing_found   a solid light dot
        not_checked     a grey ring, empty inside
    """
    red, text, muted, bg = palette["red"], palette["text"], palette["muted"], palette["bg"]
    looks = {
        "needs_repair": {"colour": red, "ring": text},
        "possible": {"colour": bg, "ring": red},
        "nothing_found": {"colour": text, "ring": bg},
        "not_checked": {"colour": bg, "ring": muted},
    }
    return dict(looks.get(state, looks["not_checked"]))


def _time(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        when = datetime.fromisoformat(value)
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def ago(since, now: datetime | None = None) -> str:
    """How long a state has held, in the biggest whole unit: "40 min", "2 h", "3 d". "" if unknown."""
    when = _time(since)
    if when is None:
        return ""
    now = now or datetime.now(UTC)
    seconds = max(0, int((now - when).total_seconds()))
    if seconds < 60:
        return "under 1 min"
    if seconds < 3600:
        return f"{seconds // 60} min"
    if seconds < 86400:
        return f"{seconds // 3600} h"
    return f"{seconds // 86400} d"


def state_line(part: dict, now: datetime | None = None) -> str:
    """The line under a part's name: "needs repair, 2 h", "nothing found", "not checked"."""
    name = state_name(part)
    words = STATE_WORDS[name]
    held = ago((part.get("state") or {}).get("since"), now)
    return f"{words}, {held}" if held and name in ("needs_repair", "possible") else words


def pin_place(part: dict, scan: dict | None) -> str:
    """Where a part's pin is, relative to the scan shown: "here", "other" (it has a position, but on
    another scan, where it means nothing: plan 5.4) or "none"."""
    position = part.get("position")
    if not isinstance(position, dict) or not _point(position.get("at")):
        return "none"
    if scan is not None and position.get("scan_id") == scan.get("scan_id"):
        return "here"
    return "other"


def _point(value) -> tuple[float, float, float] | None:
    try:
        x, y, z = (float(v) for v in value)
    except (TypeError, ValueError):
        return None
    return (x, y, z) if all(math.isfinite(v) for v in (x, y, z)) else None


def pins(parts: list[dict], scan: dict | None, selected: str | None, palette: dict) -> list[dict]:
    """The pins for view3d.ModelView.set_pins: one per part that is not removed and has a position on
    the scan shown. The selected part's pin is last, so it is drawn on top."""
    out = []
    for part in parts:
        if part.get("removed") or pin_place(part, scan) != "here":
            continue
        x, y, z = _point(part["position"]["at"])
        look = pin_look(state_name(part), palette)
        out.append({"id": part["part_id"], "x": x, "y": y, "z": z, "colour": look["colour"], "ring": look["ring"],
                    "selected": part["part_id"] == selected, "label": str(part.get("name") or "")})
    out.sort(key=lambda p: p["selected"])
    return out


def rows(parts: list[dict], scan: dict | None, selected: str | None, palette: dict,
         now: datetime | None = None) -> list[dict]:
    """The parts list, in the order the parts were made: name, the state line, the dot's look, and a
    note with what can be done when the part has no pin on the scan shown."""
    out = []
    for part in parts:
        if part.get("removed"):
            continue
        place = pin_place(part, scan)
        look = pin_look(state_name(part), palette)
        if place == "here":
            note, action = "", ""
        elif scan is None:
            note, action = "", ""  # no scan to pin on: the list works without one
        elif place == "other":
            note, action = "no pin on this scan", "place"
        else:
            note, action = "no pin", "place"
        out.append({"part_id": part["part_id"], "name": str(part.get("name") or ""), "state": state_name(part),
                    "line": state_line(part, now), "colour": look["colour"], "ring": look["ring"],
                    "selected": part["part_id"] == selected, "note": note, "action": action})
    return out


def position(scan: dict, point) -> dict:
    """A part's `position` for a click on this scan's model, in the model's own coordinates."""
    x, y, z = _point(point)
    return {"scan_id": scan["scan_id"], "at": [round(x, 5), round(y, 5), round(z, 5)]}


# ---- what a click means ----------------------------------------------------------------------------------

IDLE = ("idle", None)


def adding() -> tuple:
    """After "Add part": the next click on the model is where the new part goes."""
    return ("adding", None)


def placing(part_id: str) -> tuple:
    """After "place" or "move pin": the next click on the model is where this part's pin goes."""
    return ("placing", part_id)


def hint(mode: tuple, part_name: str = "") -> str:
    kind = mode[0]
    if kind == "adding":
        return "Click the model where the new part is. Esc cancels."
    if kind == "placing":
        return f"Click the model where {part_name or 'the part'} is. Esc cancels."
    return ""


def on_model_click(mode: tuple, scan: dict | None, point) -> tuple[tuple, tuple]:
    """A click that hit the model. Returns (what to do, the mode afterwards):

        ("new_part", position)          open the name-and-kind form, then create_part(name, kind, position)
        ("move_pin", part_id, position) update_part(part_id, position=position)
        ("nothing",)                    a click with no mode set does not move anything
    """
    if scan is None or _point(point) is None:
        return ("nothing",), mode
    kind, part_id = mode
    if kind == "adding":
        return ("new_part", position(scan, point)), IDLE
    if kind == "placing" and part_id:
        return ("move_pin", part_id, position(scan, point)), IDLE
    return ("nothing",), mode


def on_miss(mode: tuple) -> tuple[tuple, tuple]:
    """A click beside the model: while adding or placing, stay in that mode and say so; otherwise
    clear the selection."""
    if mode[0] in ("adding", "placing"):
        return ("say", "That click missed the model. Click on the model itself."), mode
    return ("select", None), mode


def on_pin_click(mode: tuple, part_id: str) -> tuple[tuple, tuple]:
    """A click on a pin selects its part, and ends any adding or placing."""
    return ("select", part_id), IDLE


# ---- what the page shows in place of, or beside, the model -----------------------------------------------


def view_state(scan: dict | None, can_draw: bool, view_problem: str = "", model_on_disk: bool = True) -> dict:
    """Which of the page's states holds: {"name", "show_model", "title", "text", "retry"}.

    The parts list is shown in every state: the log works with no scan (plan section 3)."""
    def state(name, title, text, show_model=False, retry=False):
        return {"name": name, "show_model": show_model, "title": title, "text": text, "retry": retry}

    if scan is None:
        return state("no_scan", "No scan yet",
                     "Scan this machine with the phone app, or import a scan file. Parts can be added without one.")
    model = scan.get("model") or {}
    status = model.get("status")
    if status == "waiting":
        return state("arriving", "Scan received", "It is waiting to be converted.")
    if status == "converting":
        return state("converting", "Converting the scan", "This can take a minute for a large scan.")
    if status == "failed":
        return state("failed", "The scan could not be converted",
                     str(model.get("reason") or "No reason was given."), retry=True)
    if status != "ready":
        return state("failed", "The scan cannot be shown", f"Its status is {status!r}, which this app does not know.")
    if not can_draw or view_problem:
        return state("cannot_draw", "The 3D view cannot draw on this laptop",
                     view_problem or "The parts list and the log work without it.")
    if not model_on_disk:
        return state("fetching", "Fetching the model", "Copying the converted file from the receiver.")
    return state("ready", "", "", show_model=True)


def parts_note(parts: list[dict], scan: dict | None) -> str:
    """What the parts panel says when it would otherwise be empty."""
    if any(not p.get("removed") for p in parts):
        return ""
    if scan is not None and (scan.get("model") or {}).get("status") == "ready":
        return "No parts yet. Press Add part, then click the model."
    return "No parts yet. Press Add part to list one without a pin."


# ---- words the page shows ---------------------------------------------------------------------------------

# Contract section 8.5. The Log page keeps its own copy: the two pages share no code, only state.log.
PART_KINDS = ("motor", "bearing", "roller", "belt", "gearbox", "coupling", "fan", "pump", "mount", "frame",
              "probe_point", "other")
DEFAULT_PART_KIND = "other"

# What each state means, from the contract's rules (section 8.7). The app must use these words for
# `nothing_found`: it is not a health check.
STATE_MEANS = {
    "needs_repair": "A finding or an inspection says it needs repair, and no repair is logged since.",
    "possible": "An open alarm on this machine is of a kind linked to this part. Nobody has confirmed it.",
    "nothing_found": "The probe has been on the machine and nothing points at this part. "
                     "That is not a check of the part itself.",
    "not_checked": "The probe has not been on the machine since this part was added or repaired.",
}


def capitalise(text) -> str:
    text = str(text or "")
    return text[:1].upper() + text[1:]


def kind_label(kind) -> str:
    """"probe_point" -> "Probe point"."""
    return capitalise(str(kind or "").replace("_", " "))


def when(iso, tz=None) -> str:
    """"14 Nov 16:05" in local time (or `tz`). "" if unknown."""
    at = _time(iso)
    if at is None:
        return ""
    at = at.astimezone(tz)
    return f"{at.day} {at:%b %H:%M}"


def scan_line(scan: dict | None, tz=None) -> str:
    """The line beside the scan panel's title: when the shown scan was made, and its file's name."""
    if scan is None:
        return ""
    made = when(scan.get("at") or scan.get("received_at"), tz)
    name = str((scan.get("file") or {}).get("name") or "")
    return " \u00b7 ".join(x for x in (f"scanned {made}" if made else "", name) if x)


def scan_when(scan: dict | None, tz=None) -> str:
    """The short form beside the scan panel's title, where the whole line does not fit: "14 Nov". The time
    and the file's name are in its tooltip."""
    if scan is None:
        return ""
    return " ".join(when(scan.get("at") or scan.get("received_at"), tz).split()[:2])


# ---- a scan on its way ------------------------------------------------------------------------------------

STEP_LABELS = ("Received", "Converted", "On this laptop")


def steps(view_state_name: str) -> list[dict]:
    """The three steps a scan goes through before it can be shown, each with a mark: "done", "now",
    "failed" or "todo". [] for a state with no scan on its way."""
    marks = {
        "arriving": ("done", "todo", "todo"),
        "converting": ("done", "now", "todo"),
        "failed": ("done", "failed", "todo"),
        "fetching": ("done", "done", "now"),
    }.get(view_state_name)
    return [{"label": label, "mark": mark} for label, mark in zip(STEP_LABELS, marks, strict=True)] if marks else []


# ---- the New scan panel -----------------------------------------------------------------------------------

MACHINE_ID = re.compile(r"[a-z0-9-]{1,40}")
NO_MACHINE_ID = "Type the machine's id to get its code."
BAD_MACHINE_ID = "A machine id is 1 to 40 lowercase letters, digits or -."
NO_QR_TEXT = ("No pairing code: the phone could not reach this laptop. The laptop has no address on a local "
              "network, or its receiver listens on this laptop only. Join the WiFi the phone is on: the code shows "
              "by itself, or press Try again. A scan file can still be imported.")
CODE_ANYWAY_TEXT = ("This code may not work: no address was found at which the phone reaches this laptop, so the "
                    "code carries the nearest one there is. If the phone app cannot connect, join the WiFi the "
                    "phone is on, or import a scan file.")
SAME_CODE_TEXT = ("Looked again: this laptop's address has not changed, so the code is the same. If the phone "
                  "app cannot connect, put the phone on the WiFi this laptop is on, or import a scan file.")


def machine_id_problem(text) -> str:
    """Why this is not a machine id the QR code can carry (contract section 8.2). "" when it is one."""
    text = str(text or "")
    if not text:
        return NO_MACHINE_ID
    return "" if MACHINE_ID.fullmatch(text) else BAD_MACHINE_ID


def other_machine_note(machine_id: str, current) -> str:
    """A scan for a machine that is not the one shown arrives out of sight: say where it will be."""
    if not machine_id or machine_id == current or machine_id_problem(machine_id):
        return ""
    return f"The scan will arrive under {machine_id}. Pick that machine in the sidebar to see it."


# ---- the selected part ------------------------------------------------------------------------------------

SHOWN_ENTRIES = 3


def entry_text(entry: dict) -> str:
    """An entry's own words. A finding or inspection may have none: then what it decided."""
    text = str(entry.get("text") or "").strip()
    if text:
        return text
    if entry.get("needs_repair") is True:
        return "needs repair"
    if entry.get("needs_repair") is False:
        return "does not need repair"
    return ""


def detail(part: dict | None, entries: list[dict], scan: dict | None, model_shown: bool,
           now: datetime | None = None, tz=None) -> dict | None:
    """What the panel under the parts list says about the selected part. `entries` are that part's,
    newest first. None when no part is selected."""
    if part is None or part.get("removed"):
        return None
    name = state_name(part)
    since = when((part.get("state") or {}).get("since"), tz)
    held = ago((part.get("state") or {}).get("since"), now)
    headline = capitalise(STATE_WORDS[name])
    if since:
        headline += f" since {since}" + (f" ({held})" if held and name in ("needs_repair", "possible") else "")
    place = pin_place(part, scan)
    pin_note = {"here": "", "other": "Its pin is on an older scan, so it has none on this one.",
                "none": "It has no pin."}[place] if scan is not None else ""
    pin_action = ""
    if model_shown:
        pin_action = "Move pin" if place == "here" else "Place pin"
    shown = entries[:SHOWN_ENTRIES]
    more = len(entries) - len(shown)
    return {
        "part_id": part["part_id"],
        "name": str(part.get("name") or ""),
        "kind": kind_label(part.get("kind")),
        "state": name,
        "headline": headline,
        "short": capitalise(state_line(part, now)),  # where the headline does not fit: "Needs repair, 40 min"
        "means": STATE_MEANS[name],
        "pin_note": pin_note,
        "pin_action": pin_action,
        "entries": [{"when": when(e.get("at"), tz), "kind": kind_label(e.get("kind")), "text": entry_text(e)}
                    for e in shown],
        "more": f"and {more} more in the log" if more > 0 else "",
        "no_entries": "" if entries else "No entries yet. Open the log to add one.",
        "simulated": any(bool(e.get("simulated")) for e in shown),
    }


def new_part_id(before: set, parts: list[dict]):
    """The id of the part that was not there before, so a part just added can be selected."""
    fresh = [p["part_id"] for p in parts if p["part_id"] not in before]
    return fresh[-1] if fresh else None
