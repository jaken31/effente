"""The Machine page's part readings, as plain Python with no Qt: the tiles, the chart's points and
marks, the projection's words, the evidence of the newest visit, and the review and log columns.

The data is the reply of `state.log.readings()` (contract 9.3), the normal spectrum (9.4) and the
part review run (8.8). This file works nothing out that the data does not hold:

- No fitting, no rounding of a projection number, no date worked out from a slope. The projection's
  sentence is the one contract 9.5 gives for its `status`, word for word, with the days and dates
  copied from its `reaches`. With `show_days` off it says "Trend" and shows no days and no date.
- The chart is in mm/s. Nothing from the projection, a score with no unit, is drawn on it.
- Every number from a simulated reading says so (contract 9.6).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.data import logic

DASH = "–"
SIMULATED = "simulated"

# ---- the chart ---------------------------------------------------------------------------------------------

# The `vibration_over_limit` limit in the problems table (contract 9, "Limit line"; effente/safety.py, a
# starting value there). Labelled as the contract says. It is not the alarm: that is the model's.
LIMIT_MM_S = 11.0
LIMIT_LABEL = "limit for any machine, 11 mm/s"

RANGES = (("7d", "7 d", 7), ("30d", "30 d", 30), ("60d", "60 d", 60), ("all", "All", None))
DEFAULT_RANGE = "30d"


def since(range_key: str, now: datetime) -> str | None:
    """The `since` to ask the readings route for: midnight UTC that many days back, or None for all."""
    days = next((d for k, _label, d in RANGES if k == range_key), None)
    if days is None:
        return None
    start = (now.astimezone(UTC) - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
    return start.strftime("%Y-%m-%dT%H:%M:%SZ")


def _time(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        at = datetime.fromisoformat(value)
    except ValueError:
        return None
    return at if at.tzinfo else at.replace(tzinfo=UTC)


def _num(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def number(value) -> str:
    """A number as the data holds it, never rounded further: 3.1 stays "3.1", 2.0 reads "2"."""
    if not _num(value):
        return DASH
    return repr(float(value)).removesuffix(".0")


def day(iso, tz=None) -> str:
    """"14 Nov". "" if unknown."""
    at = _time(iso)
    if at is None:
        return ""
    at = at.astimezone(tz)
    return f"{at.day} {at:%b}"


def clock(iso, tz=None) -> str:
    """"16:01". "" if unknown."""
    at = _time(iso)
    return at.astimezone(tz).strftime("%H:%M") if at else ""


def when(iso, tz=None) -> str:
    """"14 Nov 16:01". "" if unknown."""
    at = _time(iso)
    return f"{day(iso, tz)} {clock(iso, tz)}" if at else ""


@dataclass(frozen=True)
class Point:
    at: float  # seconds since 1970
    velocity: float  # mm/s
    simulated: bool
    abnormal: bool


def points(readings: list) -> list[Point]:
    """One point per reading with a velocity, oldest first."""
    out = []
    for r in readings or []:
        at = _time(r.get("start"))
        if at is None or not _num(r.get("velocity_mm_s")):
            continue
        out.append(Point(at.timestamp(), float(r["velocity_mm_s"]), r.get("simulated") is True,
                         r.get("abnormal") is True))
    return sorted(out, key=lambda p: p.at)


@dataclass(frozen=True)
class Mark:
    at: float
    label: str


def repair_marks(entries: list, tz=None) -> list[Mark]:
    """A dashed line on the chart for each repair entry of the part (contract 8.6)."""
    out = []
    for e in entries or []:
        at = _time(e.get("at"))
        if e.get("kind") == "repair" and at is not None:
            out.append(Mark(at.timestamp(), f"repair, {day(e['at'], tz)}"))
    return sorted(out, key=lambda m: m.at)


def x_span(pts: list[Point], marks: list[Mark], since_iso: str | None, now: datetime) -> tuple[float, float]:
    """The chart's time axis: from `since` (or the first point) to now."""
    end = now.timestamp()
    start = _time(since_iso)
    if start is not None:
        lo = start.timestamp()
    else:
        times = [p.at for p in pts] + [m.at for m in marks]
        lo = min(times) if times else end - 86400
    lo = min(lo, end - 86400)
    return lo, end


def y_top(pts: list[Point]) -> float:
    """The mm/s axis always reaches above the limit line, so the line is always drawn."""
    high = max([p.velocity for p in pts] + [LIMIT_MM_S])
    return float(math.ceil(high * 1.1))


# ---- the tiles ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Tile:
    label: str
    value: str
    sub: str
    simulated: bool


def tiles(reply: dict | None, part: dict | None, tz=None) -> list[Tile]:
    """Now, Average, Highest and Change. Now is the part's newest reading (contract 9.3), whatever the
    range. Average and Highest are the `summary`, which follows the range asked for. Change is the
    projection's `slope_per_day`: a model score a day, not mm/s (contract 9.5)."""
    summary = (reply or {}).get("summary") or {}
    projection = (reply or {}).get("projection") or {}
    sim = summary.get("simulated") is True
    newest = (part or {}).get("reading") or {}
    highest = summary.get("highest") or {}
    count = summary.get("count") or 0
    now_sub = when(newest.get("at"), tz) + (", still going" if newest.get("open") else "")
    out = [
        Tile("Now, mm/s", number(newest.get("velocity_mm_s")), now_sub if newest else "no reading yet",
             newest.get("simulated") is True),
        Tile("Average, mm/s", number(summary.get("average_mm_s")), f"{count} reading{'' if count == 1 else 's'}",
             sim and count > 0),
        Tile("Highest, mm/s", number(highest.get("velocity_mm_s")), day(highest.get("at"), tz) or "none",
             sim and bool(highest)),
    ]
    slope = projection.get("slope_per_day")
    if _num(slope):
        text = number(slope)
        out.append(Tile("Change a day", text if text.startswith("-") else f"+{text}", "model score",
                        projection.get("simulated") is True))
    else:
        out.append(Tile("Change a day", DASH, "not enough history" if projection else "no projection", False))
    return out


# ---- the projection, in the contract's words ----------------------------------------------------------------

NOT_ENOUGH = ("Not enough history to say where this is heading: a trend needs readings on at least 3 "
              "different days.")
SENTENCES = {
    "over_line": "At or above the model's alarm line now",
    "no_clear_rise": "No clear rise yet: the change is inside the day-to-day spread",
    "not_enough_history": NOT_ENOUGH,
}
RISING_NO_DAYS = "Rising toward the model's alarm line"
NOT_CHECKED = ", not checked on real faults"


@dataclass(frozen=True)
class Projection:
    title: str  # "Projection", or "Trend" when `show_days` is off
    sentence: str
    under: str  # what the sentence is, or ""
    simulated: bool


def _reaches(reaches: dict | None, tz=None) -> str:
    """"in 51 to 106 days (4 Jan to 28 Feb)", from `reaches` as given. Past 365 days an end is null."""
    days = list((reaches or {}).get("days") or [None, None]) + [None, None]
    soon, late = days[0], days[1]
    first, last = day((reaches or {}).get("earliest"), UTC), day((reaches or {}).get("latest"), UTC)
    if soon is None:
        return "in more than a year"
    if late is None:
        return f"in {soon} days to more than a year (from {first})" if first else f"in {soon} days to more than a year"
    span = f"in {soon} days" if soon == late else f"in {soon} to {late} days"
    dates = first if first == last else f"{first} to {last}"
    return f"{span} ({dates})" if dates else span


def projection(projection_: dict | None, tz=None) -> Projection | None:
    """The sentence under the chart, or None when there is no projection to show (then the chart has
    its points and the limit line, and no sentence: plan 15.2)."""
    p = projection_ or {}
    status = p.get("status")
    if status not in (*SENTENCES, "rising", "not_rising"):
        return None
    show_days = p.get("show_days") is True
    if status == "rising":
        reaches = p.get("reaches")
        sentence = (f"On this trend the model's score reaches its alarm line {_reaches(reaches, tz)}"
                    if show_days and reaches else RISING_NO_DAYS)
    elif status == "not_rising":
        days_ = (p.get("window") or {}).get("days")
        sentence = f"Level or falling over the last {days_} days" if _num(days_) else "Level or falling"
    else:
        sentence = SENTENCES[status]
    simulated = p.get("simulated") is True
    if simulated:
        sentence = f"Simulated: {sentence}"
    under = ""
    if status != "not_enough_history":
        days_ = (p.get("window") or {}).get("days")
        over = f"over the last {days_} days" if _num(days_) else "over its window"
        under = f"A straight line through the model's score {over}, worked out by code{NOT_CHECKED}."
    return Projection("Projection" if show_days else "Trend", sentence, under, simulated)


# ---- the parts list ----------------------------------------------------------------------------------------


def part_number(part: dict) -> str:
    """The number beside a part in the list: its newest reading's velocity, "simulated" after it when
    the reading is (contract 9.6). A dash when it has none."""
    reading = part.get("reading") or {}
    if not _num(reading.get("velocity_mm_s")):
        return DASH
    text = number(reading["velocity_mm_s"])
    return f"{text} {SIMULATED}" if reading.get("simulated") is True else text


def never_read(part: dict | None) -> bool:
    """The probe has never been on this part: it has no reading at all."""
    return part is not None and not part.get("reading")


# ---- the probe ---------------------------------------------------------------------------------------------

NO_PROBE = "Probe on no part. Records that arrive now are kept for the machine, not for a part."
PROBE_HERE = "Probe is here"
PROBE_OFF = "Take probe off"
CLEAR = "Clear"


def probe_line(parts: list, probe_part_id, selection: dict | None = None, tz=None, cleared: str = "") -> str:
    """"Probe on: Drive roller, since 16:01". The time is shown only when the store gives the
    selection's `since` (contract 9.1): the app never stands in a time of its own. With no part
    picked, `cleared` is the reason the platform gave for clearing the last pick, shown as it came."""
    if not probe_part_id:
        return f"The pick was cleared: {cleared}. {NO_PROBE}" if cleared else NO_PROBE
    part = next((p for p in parts if p.get("part_id") == probe_part_id), None)
    name = (part or {}).get("name") or probe_part_id
    started = clock((selection or {}).get("since"), tz) if (selection or {}).get("part_id") == probe_part_id else ""
    return f"Probe on: {name}, since {started}" if started else f"Probe on: {name}"


# ---- the evidence of the newest visit ------------------------------------------------------------------------


def visit_record(records: list, part_id: str, reply: dict | None):
    """The newest record of the part's newest visit, from the records on this laptop: stamped with the
    part and inside the newest reading's `start` to `end`. None when it is not here."""
    readings = (reply or {}).get("readings") or []
    if not readings or not part_id:
        return None
    newest = readings[-1]
    lo, hi = _time(newest.get("start")), _time(newest.get("end"))
    if lo is None:
        return None
    best = None
    for r in records or []:
        at = _time(r.get("at"))
        if r.get("part_id") != part_id or at is None or at < lo or (hi is not None and at > hi):
            continue
        if best is None or at >= _time(best["at"]):
            best = r
    return best


@dataclass(frozen=True)
class Moved:
    name: str
    text: str
    spreads: float


def moved(record: dict | None, texts: dict) -> list[Moved]:
    """The record's `features`: the readings that moved most, in spreads from normal, in the order sent,
    each with the text the model announced for it."""
    feats = (record or {}).get("features")
    if not isinstance(feats, dict):
        return []
    return [Moved(n, logic.reading_name(n, texts), float(v)) for n, v in feats.items() if _num(v)]


@dataclass(frozen=True)
class Spectrum:
    bars: tuple  # the record's 20 band levels, log10
    normal: tuple  # the normal's, or () when not known
    edges: tuple  # band edges in Hz, 21 of them, or ()
    note: str  # what the normal line is, or why there is none
    simulated: bool


def _levels(value, n=None) -> tuple:
    if not isinstance(value, list) or not value or not all(_num(v) for v in value):
        return ()
    return tuple(float(v) for v in value) if n is None or len(value) == n else ()


def spectrum(record: dict | None, own: dict | None, machine: dict | None, edges=None) -> Spectrum | None:
    """The record's bars against the normal (contract 9.4). The part's own normal first; if that is not
    ready, the machine's, said so; with neither, no line and how far along it is."""
    bars = _levels((record or {}).get("spectrum"))
    if not bars:
        return None
    normal, note, simulated = (), "", record.get("simulated") is True
    for which, reply in (("own", own), ("machine", machine)):
        if reply and reply.get("ready") is True and _levels(reply.get("spectrum"), len(bars)):
            normal = _levels(reply["spectrum"], len(bars))
            note = "normal" if which == "own" else "normal for the whole machine, not for this part"
            simulated = simulated or reply.get("simulated") is True
            edges = reply.get("spectrum_edges_hz") or edges
            break
    if not normal:
        reply = own or machine
        if reply and _num(reply.get("records")) and _num(reply.get("needed")):
            note = f"Normal not known yet: {int(reply['records'])} of {int(reply['needed'])} normal records"
        else:
            note = "Normal not known yet"
    edges = _levels(edges, len(bars) + 1)
    return Spectrum(bars, normal, edges, note, simulated)


# ---- the review column -------------------------------------------------------------------------------------

ADVICE = {"yes": "Yes", "no": "No", "not_sure": "Not sure"}
ADVICE_NOTE = "Advice, not a decision. Nothing is added to the log unless you add it."
AGREE = "Agree: add a finding"
RUN_REVIEW = "Run review"
REVIEWING = "Reviewing…"


@dataclass(frozen=True)
class Review:
    message: str
    rows: tuple = ()  # (label, value)
    simulated: bool = False
    can_agree: bool = False


def review(part: dict | None, run: dict | None, running: bool, tz=None) -> Review:
    """The newest part review of this part, as advice. `trend` is shown as the review gives it: code
    built it from the projection (contract 9.7)."""
    name = (part or {}).get("name") or "this part"
    passed = bool(run and run.get("status") == "done" and run.get("ok") is True and isinstance(run.get("review"), dict))
    rows: tuple = ()
    if passed:
        r = run["review"]
        cited = [*(r.get("cited_alarms") or []), *(r.get("cited_entries") or [])]
        pairs = [
            ("Needs repair", f"{ADVICE.get(r.get('needs_repair'), DASH)} (advice)"),
            ("What", str(r.get("what") or DASH)),
            ("How it comes about", str(r.get("how") or DASH)),
            ("Since", when(r.get("since"), tz) or DASH),
        ]
        if r.get("trend"):
            pairs.append(("Trend", str(r["trend"])))
        pairs.append(("Cited", ", ".join(str(c) for c in cited) or "nothing"))
        rows = tuple(pairs)
    simulated = bool(passed and run.get("simulated") is True)
    if running or (run and run.get("status") == "running"):
        return Review(f"Reviewing {name}…" + (" The review below is the previous one." if passed else ""),
                      rows, simulated)
    if not run:
        return Review(f"No review of {name} yet. It reads only this part's own history.")
    if not passed:
        reason = str(run.get("reason") or "").strip() or "no reason was given"
        return Review(f"The last review was discarded: {reason}. Nothing was added to the log.")
    return Review(ADVICE_NOTE, rows, simulated, True)


def agree_draft(run: dict | None) -> tuple[str, str, object] | None:
    """What "Agree" puts in the add-entry form: (kind, text, needs_repair). It adds nothing itself."""
    r = (run or {}).get("review") or {}
    if not r:
        return None
    return "finding", str(r.get("what") or ""), {"yes": True, "no": False}.get(r.get("needs_repair"))


# ---- the log column ----------------------------------------------------------------------------------------

PERSON_KINDS = ("note", "finding", "inspection", "repair")  # contract 8.6; `review` is the agent's
NEEDS_ANSWER = ("finding", "inspection")  # these must say whether the part needs repair


@dataclass(frozen=True)
class Entry:
    head: str  # "14 Nov 11:06 · Review · Sam", with "simulated" last when it is
    text: str
    simulated: bool


def entry_text(entry: dict) -> str:
    text = str(entry.get("text") or "").strip()
    if text:
        return text
    return {True: "needs repair", False: "does not need repair"}.get(entry.get("needs_repair"), "")


def entries(items: list, tz=None) -> list[Entry]:
    """The part's entries, newest first, as the log column shows them."""
    out = []
    for e in items or []:
        kind = str(e.get("kind") or "").replace("_", " ")
        sim = e.get("simulated") is True
        head = " · ".join(x for x in (when(e.get("at"), tz), kind[:1].upper() + kind[1:], str(e.get("by") or ""),
                                      SIMULATED if sim else "") if x)
        out.append(Entry(head, entry_text(e), sim))
    return out


def entry_problem(kind, text, needs_repair) -> str:
    """Why this entry cannot be added yet, or "" when it can (contract section 8.6)."""
    if kind in NEEDS_ANSWER:
        return "" if needs_repair in (True, False) else "Say whether it needs repair."
    return "" if str(text or "").strip() else "Type what happened."

NO_ENTRIES_FOR = "No entries for this part yet."
NO_PART_LOG = "Pick a part to see its log."
