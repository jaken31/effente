"""Text for the shell's chrome: times, durations, freshness and the device card.

Only formatting lives here. Judging (what is stale, which device is shown) comes
from DashboardState.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def fmt_dur(seconds: float) -> str:
    """As the mockup: "42s", "3m 5s"."""
    s = max(0, round(seconds))
    return f"{s // 60}m {s % 60}s" if s >= 60 else f"{s}s"


def to_datetime(value: Any) -> datetime | None:
    """An ISO string (with Z), epoch seconds or a datetime, as an aware datetime."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=UTC)
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def fmt_time(value: Any) -> str:
    """Local wall-clock time, HH:MM:SS, or an en dash."""
    dt = to_datetime(value)
    return dt.astimezone().strftime("%H:%M:%S") if dt else "–"


def freshness_text(fresh: Any) -> tuple[str, bool]:
    """The top bar's freshness line and whether it is stale.

    `fresh` is DashboardState.freshness(): a dict with
      stale   bool         no record for too long (the logic's rule, not ours)
      age_s   float|None   seconds since the newest record
      last_at str|None     the newest record's time (ISO)
    """
    if not fresh:
        return "No reading yet", True
    stale = bool(fresh.get("stale"))
    last = fresh.get("last_at")
    if stale:
        age = fresh.get("age_s")
        return (f"No reading for {fmt_dur(age)}" if age is not None else "No reading yet"), True
    return (f"Updated {fmt_time(last)}" if last else "No reading yet"), last is None


def records_per_s(records: list[dict]) -> float | None:
    """Records a second over the newest ten intervals, or None if it cannot be told."""
    times = [t for t in (to_datetime(r.get("at")) for r in records[-11:]) if t]
    if len(times) < 2:
        return None
    span = (times[-1] - times[0]).total_seconds()
    return (len(times) - 1) / span if span > 0 else None


def _per_s(rate: float, unit: str) -> str:
    return f"{rate:.0f} {unit}/ s" if rate >= 0.95 or rate == 0 else f"{rate:.1f} {unit}/ s"


def record_rate(records: list[dict], stale: bool) -> str:
    """"1 / s". "0 / s" while stale."""
    if stale:
        return _per_s(0, "")
    rate = records_per_s(records)
    return "\u2013" if rate is None else _per_s(rate, "")


def thermal_rate(latest: dict | None, records: list[dict], stale: bool) -> str:
    """"2 frames / s": frames in the newest record times the record rate."""
    heat = (latest or {}).get("heat")
    if not heat:
        return "none"
    if heat.get("source") != "camera":
        return "no camera"
    if stale:
        return _per_s(0, "frames ")
    rate = records_per_s(records)
    if rate is None:
        return "\u2013"
    return _per_s(rate * len(heat.get("frames") or []), "frames ")


def model_text(model: dict | None) -> str:
    """"conveyor, calibrated": the library entry and how it was fitted."""
    if not model:
        return "–"
    parts = [p for p in (model.get("library_entry"), model.get("source")) if p]
    return ", ".join(parts) or str(model.get("model_version") or "–")
