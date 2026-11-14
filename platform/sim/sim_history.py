"""Simulated per-part history for the sample machine (CONTRACT.md 9.6).

    python3 platform/sim/history.py [--url http://localhost:8000] [--yes]

Writes 60 days of made-up visits, past findings and a repair to the machine
`sample-machine`, through the receiver's ordinary routes, so the Machine page
has weeks of readings to draw. It takes no machine id: it never writes to any
other machine.

Everything here is MADE UP, like the rest of the simulator. The scores and
velocities follow shapes picked by hand so the page shows each case once; they
are not measurements and say nothing about any real machine. Every part,
record and log entry written carries "simulated": true.

There is no undo. Before writing, it reads the sample machine and refuses if it
holds any record that is not simulated, has an open alarm or a connected
device, and stops if simulated history is already there. Then it says what it
is about to write and where, and asks.
"""

from __future__ import annotations

import argparse
import os
import random
import sys
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import sim_model
from sim_link import Link

SAMPLE_MACHINE = "sample-machine"  # contract 9.6; the id itself is not yet confirmed by the team
HISTORY_DEVICE = "sim-history"  # contract 9.6: never the live simulated device
BY = "simulator"
DAYS = 60  # one visit a part a day (contract 9.6)
VISIT_RECORDS = 30  # a short visit: 30 records a second apart (contract 9.6)
BATCH = 500  # records per POST, at most (contract 9.6)
TIMEOUT_S = 15.0  # a batch of 500 is far bigger than the live device's one record
PAGE = 2000  # the records route's own maximum

# Exit codes.
WROTE, NOTHING_TO_DO, REFUSED = 0, 0, 2


# --------------------------------------------------------------- the plan
#
# Four parts, one of each shape the page has to draw (the same four as the
# desktop app's fake, plan 15.3). Every score stays under the alarm line (1.0 at
# sensitivity 0.5), so every record is judged normal and no alarm is made.

PARTS = [
    {"name": "Drive roller", "kind": "roller", "fault_types": ["unbalance_like"],
     "note": "Simulated: a rise, a repair and a new rise."},
    {"name": "Belt", "kind": "belt", "note": "Simulated: level."},
    {"name": "Motor mount", "kind": "mount", "note": "Simulated: one reading."},
    {"name": "Idle roller", "kind": "roller", "note": "Simulated: never visited."},
]
PART_NAMES = [p["name"] for p in PARTS]

REPAIR_DAY = 34  # the drive roller is repaired in the afternoon of this day
FIRST_RISE = (0.35, 0.88)  # its median score, day 0 to REPAIR_DAY
SECOND_RISE = (0.35, 0.65)  # and from the day after to the last day
LEVEL_SCORE = 0.40
ONE_SCORE = 0.45
VISIT_HOUR = {"Drive roller": 9, "Belt": 10, "Motor mount": 11}  # UTC; parts more than 10 minutes apart


def _rise(lo_hi, day, first, last):
    lo, hi = lo_hi
    return lo + (hi - lo) * (day - first) / (last - first)


def target_score(name: str, day: int) -> float | None:
    """The median score a part's visit on `day` aims at, or None for no visit."""
    if name == "Drive roller":
        if day <= REPAIR_DAY:
            return _rise(FIRST_RISE, day, 0, REPAIR_DAY)
        return _rise(SECOND_RISE, day, REPAIR_DAY + 1, DAYS - 1)
    if name == "Belt":
        return LEVEL_SCORE
    if name == "Motor mount":
        return ONE_SCORE if day == DAYS - 1 else None
    return None


def velocity_for(score: float) -> float:
    """mm/s moving with the score, so the chart and the projection tell one story. Made up."""
    return 1.3 + 5.0 * (score - 0.35)


@dataclass
class Plan:
    first_day: datetime  # 00:00 UTC of day 0
    records: list[dict] = field(default_factory=list)  # oldest first, part_id holds the part's name until sent
    entries: list[dict] = field(default_factory=list)  # oldest first, part_id holds the part's name until sent

    @property
    def last_day(self) -> datetime:
        return self.first_day + timedelta(days=DAYS - 1)

    def visits(self) -> int:
        return len(self.records) // VISIT_RECORDS


def _at(day0: datetime, day: int, hour: int, minute: int = 0, second: int = 0) -> datetime:
    return day0 + timedelta(days=day, hours=hour, minutes=minute, seconds=second)


def _visit(rng: random.Random, part: str, start: datetime, score: float) -> list[dict]:
    out = []
    for i in range(VISIT_RECORDS):
        r = sim_model.make_record("normal", rng, device_id=HISTORY_DEVICE, machine_id=SAMPLE_MACHINE,
                                  at=sim_model.iso(start + timedelta(seconds=i)))
        s = round(min(score + rng.uniform(-0.04, 0.04), sim_model.ALARM_LINE - 0.05), 2)
        lift = max(0.0, s - 0.35)  # the once-per-turn line grows with the score
        r["unknown_score"] = s
        r["confidence"] = round(1.0 - s, 2)
        r["features"]["vib_1x"] = round(r["features"]["vib_1x"] + 4.0 * lift, 2)
        r["spectrum"][sim_model._band_of(sim_model.TURN_HZ)] = round(
            r["spectrum"][sim_model._band_of(sim_model.TURN_HZ)] + 1.5 * lift, 3)
        r["readings"] = {"vib_velocity_mm_s": round(velocity_for(s) + rng.uniform(-0.1, 0.1), 2)}
        r["part_id"] = part
        out.append(r)
    return out


def build_plan(today: datetime, seed: int = 1) -> Plan:
    """Every record and entry the history writes. Pure: no network."""
    rng = random.Random(seed)
    day0 = today.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=DAYS)
    plan = Plan(first_day=day0)
    for day in range(DAYS):
        for name in PART_NAMES:
            score = target_score(name, day)
            if score is not None:
                plan.records += _visit(rng, name, _at(day0, day, VISIT_HOUR[name], rng.randrange(0, 20)), score)
    plan.records.sort(key=lambda r: r["at"])
    plan.entries = [
        {"kind": "inspection", "part_id": "Belt", "at": sim_model.iso(_at(day0, 20, 14)), "needs_repair": False,
         "text": "Belt tension and tracking checked. Nothing found."},
        {"kind": "finding", "part_id": "Drive roller", "at": sim_model.iso(_at(day0, REPAIR_DAY - 1, 14)),
         "needs_repair": True, "text": "Once-per-turn vibration up on the drive roller. Build-up on one side."},
        {"kind": "repair", "part_id": "Drive roller", "at": sim_model.iso(_at(day0, REPAIR_DAY, 15)),
         "text": "Drive roller cleaned and rebalanced."},
    ]
    for e in plan.entries:
        e.update(by=BY, simulated=True)
    return plan


def batches(records: list[dict], size: int = BATCH) -> list[list[dict]]:
    return [records[i:i + size] for i in range(0, len(records), size)]


# --------------------------------------------------------------- the checks

@dataclass
class Verdict:
    go: bool
    code: int
    message: str


def judge(state: dict, records: list[dict], parts: list[dict]) -> Verdict:
    """Whether history may be written to the sample machine, from what the receiver holds. Pure."""
    real = [r for r in records if r.get("simulated") is not True]
    if real:
        return Verdict(False, REFUSED, f"{SAMPLE_MACHINE} holds {len(real)} record(s) that are not simulated "
                                       f"(the first at {real[0].get('at')}). Simulated history is never written "
                                       "next to real records.")
    if records:
        return Verdict(False, NOTHING_TO_DO, f"{SAMPLE_MACHINE} already has simulated history "
                                             f"({len(records)} records). Nothing written.")
    open_alarms = sorted(a.get("alarm_id", "?") for a in (state.get("alarms") or {}).values()
                         if a.get("machine_id") == SAMPLE_MACHINE and a.get("open"))
    if open_alarms:
        return Verdict(False, REFUSED, f"{SAMPLE_MACHINE} has an open alarm ({', '.join(open_alarms)}). "
                                       "Past records stored now could close it.")
    connected = sorted(d for d, v in (state.get("devices") or {}).items()
                       if v.get("machine_id") == SAMPLE_MACHINE and v.get("connected"))
    if connected:
        return Verdict(False, REFUSED, f"a device is connected on {SAMPLE_MACHINE} ({', '.join(connected)}). "
                                       "Wait until it has been quiet for 5 seconds.")
    live = [p for p in parts if not p.get("removed")]
    if live:
        names = sorted(p.get("name", "") for p in live)
        ours = all(p.get("simulated") is True for p in live) and set(names) <= set(PART_NAMES)
        if not ours:
            return Verdict(False, REFUSED, f"{SAMPLE_MACHINE} already has parts this command did not make "
                                           f"({', '.join(names)}).")
    return Verdict(True, WROTE, "")


def describe(plan: Plan, url: str, parts_to_make: list[str]) -> str:
    counts = {n: sum(1 for r in plan.records if r["part_id"] == n) // VISIT_RECORDS for n in PART_NAMES}
    made = (f"{len(parts_to_make)} parts: {', '.join(parts_to_make)}" if parts_to_make
            else "no parts (the four simulated parts are already there)")
    lines = [
        f"About to write simulated history to {url}, machine {SAMPLE_MACHINE}:",
        f"  {made}",
        f"  {len(plan.records)} records in {plan.visits()} visits of {VISIT_RECORDS}, as device {HISTORY_DEVICE}, "
        f"{plan.first_day:%d %b %Y} to {plan.last_day:%d %b %Y}:",
    ]
    lines += [f"    {n}: {counts[n]} visit(s)" for n in PART_NAMES]
    lines.append(f"  {len(plan.entries)} log entries:")
    lines += [f"    {e['at'][:10]} {e['kind']} on {e['part_id']}" for e in plan.entries]
    lines += [
        "Every part, record and entry is marked simulated. None of it is a measurement.",
        "There is no undo. The only way to remove it is to clear the receiver's data folder,",
        "which removes every real record too.",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------- the network

class Refused(Exception):
    pass


def _q(text: str) -> str:
    return urllib.parse.quote(text, safe="")


def _get(link: Link, path: str) -> dict:
    res = link.call("GET", path)
    if not res.ok:
        raise Refused(f"GET {path} failed: {res.error or 'no reply'}")
    return res.body


def read_records(link: Link) -> list[dict]:
    """Every record of the sample machine, page by page, without spectra."""
    out, after = [], None
    while True:
        path = f"/api/machines/{SAMPLE_MACHINE}/records?limit={PAGE}&since=1970-01-01T00:00:00Z"
        if after is not None:
            path += f"&after_seq={after}"
        page = _get(link, path).get("records") or []
        out += page
        if len(page) < PAGE:
            return out
        after = page[-1]["seq"]


def _send(link: Link, method: str, path: str, payload) -> dict:
    res = link.call(method, path, payload)
    if not res.ok:
        raise Refused(f"{method} {path} failed: {res.error or 'no reply'}")
    return res.body


def write(link: Link, plan: Plan, parts: list[dict], say) -> dict[str, str]:
    """Send the plan. Returns part name to part_id."""
    ids = {p["name"]: p["part_id"] for p in parts if not p.get("removed")}
    _send(link, "POST", "/api/model", sim_model.model_announcement(HISTORY_DEVICE, SAMPLE_MACHINE))
    made_at = sim_model.iso(plan.first_day)  # no later than the oldest record, so records count for part state (8.7)
    for spec in PARTS:
        if spec["name"] not in ids:
            body = _send(link, "POST", f"/api/machines/{SAMPLE_MACHINE}/parts",
                         dict(spec, by=BY, simulated=True, at=made_at))
            ids[spec["name"]] = body["part"]["part_id"]
    sent = 0
    for batch in batches(plan.records):
        payload = [dict(r, part_id=ids[r["part_id"]]) for r in batch]
        reply = _send(link, "POST", "/api/records", payload)
        if reply.get("need_model"):
            _send(link, "POST", "/api/model", sim_model.model_announcement(HISTORY_DEVICE, SAMPLE_MACHINE))
        sent += len(batch)
        say(f"  records {sent} of {len(plan.records)}")
    for e in plan.entries:
        _send(link, "POST", f"/api/machines/{SAMPLE_MACHINE}/log", dict(e, part_id=ids[e["part_id"]]))
    return ids


def report(link: Link, ids: dict[str, str], say) -> None:
    """What the receiver now says about each part's readings (contract 9.3, 9.5)."""
    for name in PART_NAMES:
        res = link.call("GET", f"/api/parts/{_q(ids[name])}/readings")
        if not res.ok:
            say(f"  {name}: readings not available ({res.error})")
            continue
        readings = res.body.get("readings") or []
        projection = res.body.get("projection") or {}
        say(f"  {name}: {len(readings)} reading(s), projection {projection.get('status', 'none')}")


# --------------------------------------------------------------- command

def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description=f"Write {DAYS} days of simulated per-part history to the machine {SAMPLE_MACHINE} "
                    "(contract 9.6). Takes no machine id: it never writes anywhere else.")
    p.add_argument("--url", default=os.environ.get("EFFENTE_PLATFORM_URL", "http://localhost:8000"))
    p.add_argument("--seed", type=int, default=1, help="repeatable records (default 1)")
    p.add_argument("--yes", action="store_true", help="write without asking (for scripts)")
    return p.parse_args(argv)


def main(argv=None, stdin=None, now: datetime | None = None) -> int:
    args = parse_args(argv)
    stdin = stdin or sys.stdin
    say = lambda m: print(m, flush=True)  # noqa: E731
    link = Link(args.url, HISTORY_DEVICE, timeout=TIMEOUT_S)
    try:
        state = _get(link, "/api/state")
        records = read_records(link)
        parts = _get(link, f"/api/machines/{SAMPLE_MACHINE}/parts?removed=1").get("parts") or []
    except Refused as e:
        say(f"Cannot read the receiver at {args.url}: {e}")
        return REFUSED
    verdict = judge(state, records, parts)
    if not verdict.go:
        say(("Refused: " if verdict.code == REFUSED else "") + verdict.message)
        return verdict.code

    plan = build_plan(now or datetime.now(timezone.utc), args.seed)
    have = {p["name"] for p in parts if not p.get("removed")}
    say(describe(plan, args.url, [n for n in PART_NAMES if n not in have]))
    if not args.yes:
        if not stdin.isatty():
            say("Not a terminal, so not asking: run again with --yes to write it. Nothing written.")
            return REFUSED
        print("Write it? [y/N] ", end="", flush=True)
        if stdin.readline().strip().lower() not in ("y", "yes"):
            say("Nothing written.")
            return REFUSED
    try:
        ids = write(link, plan, parts, say)
    except Refused as e:
        say(f"Stopped part way: {e}")
        say("What was sent before this stays on the receiver: there is no undo.")
        return REFUSED
    say(f"Done: {SAMPLE_MACHINE} now has {DAYS} days of simulated history.")
    report(link, ids, say)
    return WROTE
