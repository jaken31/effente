"""Visits and readings: one point on a part's chart per stay of the probe on it
(platform/CONTRACT.md section 9.3).

Plain functions over a part's stamped records. They are worked out, never
stored, so the receiver works them out again from its stored records at start.
Every number here is a starting value picked by reasoning, unverified.

`records` is always one part's stamped records, sorted by `key` (the record's
`at`, then its `seq`), with any record whose `at` is not a time left out.
`alarm_of(record)` names the alarm an abnormal record belongs to, or None.
`now` is a UTC timestamp.
"""

import bisect
import datetime as dt
import math
import statistics

VISIT_GAP_S = 10 * 60  # a longer gap between two neighbours starts a new visit
MIN_RECORDS = 10  # a visit needs this many usable records to make a reading
OPEN_FOR_S = 10 * 60  # a reading is open while its newest record is younger than this
NOT_ABNORMAL = ("normal", "stopped", "warming_up")  # as in section 6; any other diagnosis is abnormal
STOPPED = "stopped"
VELOCITY = "vib_velocity_mm_s"


def timestamp(text):
    """ISO time to a UTC timestamp, or None if it is not one. No zone means UTC."""
    if not isinstance(text, str):
        return None
    try:
        value = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return value.timestamp()


def key(record):
    """Where a record sorts in a part's records: by `at`, then by `seq`."""
    return (timestamp(record.get("at")), record.get("seq", 0))


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) else None


def velocity(record):
    """The record's `readings.vib_velocity_mm_s` (contract 9.2), or None."""
    block = record.get("readings")
    return _number(block.get(VELOCITY)) if isinstance(block, dict) else None


def visit_around(records, i):
    """The visit that holds records[i], as (lo, hi): records[lo:hi]."""
    lo = i
    while lo > 0 and key(records[lo])[0] - key(records[lo - 1])[0] <= VISIT_GAP_S:
        lo -= 1
    hi = i + 1
    while hi < len(records) and key(records[hi])[0] - key(records[hi - 1])[0] <= VISIT_GAP_S:
        hi += 1
    return lo, hi


def visits(records):
    """Every visit, oldest first, each a list of records."""
    out = []
    i = 0
    while i < len(records):
        hi = visit_around(records, i)[1]
        out.append(records[i:hi])
        i = hi
    return out


def reading(visit, alarm_of, now):
    """The reading one visit makes, or None if it has too few usable records."""
    used = [r for r in visit if r.get("diagnosis") != STOPPED and velocity(r) is not None]
    if len(used) < MIN_RECORDS:
        return None
    first, newest = visit[0], visit[-1]
    scores = [s for s in (_number(r.get("unknown_score")) for r in used) if s is not None]
    abnormal = [r for r in visit if r.get("diagnosis") not in NOT_ABNORMAL]
    alarm_ids = list(dict.fromkeys(a for a in map(alarm_of, abnormal) if a is not None))
    return {
        "part_id": first["part_id"],
        "machine_id": first["machine_id"],
        "start": first["at"],
        "end": newest["at"],
        "records": len(used),
        "velocity_mm_s": round(statistics.median(velocity(r) for r in used), 2),
        "unknown_score": round(statistics.median(scores), 2) if scores else None,
        "alarm_line": _number(newest.get("alarm_line")),
        "model_version": newest.get("model_version"),
        "abnormal": bool(abnormal),
        "alarm_ids": alarm_ids,
        "open": now - key(newest)[0] < OPEN_FOR_S,
        "simulated": any(r.get("simulated") is True for r in visit),
    }


def readings(records, alarm_of, now):
    """Every reading of a part, oldest first."""
    made = (reading(v, alarm_of, now) for v in visits(records))
    return [r for r in made if r is not None]


def reading_around(records, i, alarm_of, now):
    """The reading of the visit that holds records[i], or None."""
    lo, hi = visit_around(records, i)
    return reading(records[lo:hi], alarm_of, now)


def reading_starting(records, start, alarm_of, now):
    """The reading of the visit whose first record is at `start`, or None."""
    i = bisect.bisect_left(records, (timestamp(start), float("-inf")), key=key)
    return reading_around(records, i, alarm_of, now) if i < len(records) else None


def newest_reading(records, alarm_of, now):
    """The newest reading, working back from the newest visit only as far as needed."""
    i = len(records) - 1
    while i >= 0:
        lo, hi = visit_around(records, i)
        made = reading(records[lo:hi], alarm_of, now)
        if made is not None:
            return made
        i = lo - 1
    return None


def short(reading_):
    """The part's `reading` (contract 9.3): its newest reading in brief."""
    if reading_ is None:
        return None
    return {"velocity_mm_s": reading_["velocity_mm_s"], "at": reading_["start"], "open": reading_["open"],
            "simulated": reading_["simulated"]}


def chosen(all_readings, since=None, limit=300):
    """The newest `limit` readings whose `start` is at or after `since` (a timestamp), oldest first."""
    kept = [r for r in all_readings if since is None or timestamp(r["start"]) >= since]
    return kept[-limit:] if limit > 0 else []


def summary(chosen_readings):
    """`summary` of the readings route, over the readings it returns."""
    if not chosen_readings:
        return {"count": 0, "average_mm_s": None, "highest": None, "newest": None, "simulated": False}
    highest = max(chosen_readings, key=lambda r: r["velocity_mm_s"])  # the first of equals
    newest = chosen_readings[-1]
    average = sum(r["velocity_mm_s"] for r in chosen_readings) / len(chosen_readings)
    return {
        "count": len(chosen_readings),
        "average_mm_s": round(average, 2),
        "highest": {"velocity_mm_s": highest["velocity_mm_s"], "at": highest["start"]},
        "newest": {"velocity_mm_s": newest["velocity_mm_s"], "at": newest["start"]},
        "simulated": any(r["simulated"] for r in chosen_readings),
    }
