"""The machine profile's five fields (platform/CONTRACT.md section 10.1) and the
lookup that picks a library entry from them (section 10.2).

Plain code, standard library only. The receiver checks a stored profile with
`field_problems` and picks its entry with `choose`. The setup agent's checks use
the same two. No model call is anywhere near the lookup.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

# Contract 10.1. The value lists are the lifecycle doc's examples, taken as
# starting lists. Nobody has checked that they cover real machines (unverified).
VALUES = {
    "machine_type": ("conveyor", "fan", "pump", "motor", "other"),
    "drive": ("electric_motor_direct", "belt", "gearbox"),
    "mounting": ("rigid", "flexible"),
    "sensor_location": ("motor_drive_end", "gearbox", "frame"),
}
FIELDS = ("machine_type", "drive", "speed_rpm", "mounting", "sensor_location")


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def speed_ok(value) -> bool:
    """A number above 0, or a range as `[low, high]` of two such numbers with low <= high."""
    if _number(value):
        return True
    return isinstance(value, list) and len(value) == 2 and all(map(_number, value)) and value[0] <= value[1]


def field_problems(body: dict) -> list[str]:
    """What is wrong with the five fields in `body`, in words a person can act on.

    A field that is missing or `null` is empty, which is never a problem. Keys
    other than the five are not looked at.
    """
    problems = []
    for name in FIELDS:
        value = body.get(name)
        if value is None:
            continue
        if name == "speed_rpm":
            if not speed_ok(value):
                problems.append("'speed_rpm' must be a number above 0, a range as [low, high], or null")
        elif value not in VALUES[name]:
            problems.append(f"'{name}' must be one of {', '.join(VALUES[name])}, or null")
    return problems


def five_fields(body: dict) -> dict:
    """The five fields of `body`, in order, a missing one as `None`: unknown stays empty."""
    return {name: body.get(name) for name in FIELDS}


# ---- the lookup (contract 10.2) ---------------------------------------------
#
# A profile in, a library entry out, picked by this table and nothing else. No
# model takes part. The table is a starting value kept in the platform until
# track 3 adds a field to the manifest saying which profiles an entry suits
# (contract 10.2: where the table lives is open).

ENTRIES_DIR = Path(__file__).resolve().parents[2] / "training" / "library" / "entries"
GENERAL = "generic_rotating"
# (machine_type, drive or None for any drive, entry_id), first match wins.
# `drive` decides nothing until two entries share a machine type.
# The `fan` and `pump` entries are simulator-only and unverified (training/library/README.md,
# "fan and pump"); `fan` is trained on a simulated server's cooling fans, not an industrial fan.
# The reply carries each entry's status and what it was trained on, as for every entry.
TABLE = (("conveyor", None, "conveyor"), ("fan", None, "fan"), ("pump", None, "pump"))

# The fixed reasons. Each names the profile field that decided.
REASON_MATCH = "machine type is {machine_type}"
REASON_MATCH_DRIVE = "machine type is {machine_type} and drive is {drive}"
REASON_NO_ENTRY = "no entry for machine type {machine_type}, so the general one"
REASON_NOT_GIVEN = "machine type not given, so the general one"
REASON_UNREADABLE = "the {entry} entry cannot be read, so the general one"
NOT_VALIDATED = "not validated"


class LookupUnavailable(Exception):
    """Not even the general entry's manifest can be read: no entry can be picked."""


def entry_ids(entries_dir: Path = ENTRIES_DIR) -> list[str]:
    """Every library entry that has a manifest file, readable or not."""
    try:
        return sorted(p.name for p in Path(entries_dir).iterdir() if (p / "manifest.json").is_file())
    except OSError:
        return []


def read_entry(entry_id: str, entries_dir: Path = ENTRIES_DIR) -> dict | None:
    """What contract 10.2 copies from the entry's manifest, or None if it cannot be read.

    Copied as it is: nothing is rounded, reworded or filled in.
    """
    try:
        manifest = json.loads((Path(entries_dir) / entry_id / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not _readable(manifest, entry_id):
        return None
    sources = manifest["sources"]
    evaluation = [s for s in sources if s["used_for"] == "evaluation"]
    scorecard = manifest["scorecard"]
    return {
        "library_entry": entry_id,
        "version": manifest["version"],
        "status": manifest["status"],
        "trained_on": [_source(s) for s in sources if s["used_for"] == "training"],
        "checked_on": [_source(s, note=True) for s in evaluation if s.get("evaluated") is True],
        "not_checked_on": [_source(s, note=True) for s in evaluation if s.get("evaluated") is not True],
        "fault_types_validated": [name for name, value in scorecard.items() if value != NOT_VALIDATED],
        "fault_types_not_validated": sum(1 for value in scorecard.values() if value == NOT_VALIDATED),
    }


def _readable(manifest, entry_id: str) -> bool:
    """Every manifest field the lookup copies is there, with the type it needs."""
    if not isinstance(manifest, dict) or manifest.get("entry_id") != entry_id:
        return False
    if not all(isinstance(manifest.get(k), str) for k in ("version", "status")):
        return False
    sources, scorecard = manifest.get("sources"), manifest.get("scorecard")
    if not isinstance(sources, list) or not isinstance(scorecard, dict):
        return False
    good_source = all(
        isinstance(s, dict) and isinstance(s.get("name"), str) and isinstance(s.get("kind"), str)
        and s.get("used_for") in ("training", "evaluation")
        for s in sources
    )
    return good_source and all(isinstance(v, str) for v in scorecard.values())


def _source(source: dict, note: bool = False) -> dict:
    out = {"name": source["name"], "kind": source["kind"]}
    if note and isinstance(source.get("note"), str):
        out["note"] = source["note"]
    return out


def choose(profile: dict, entries_dir: Path = ENTRIES_DIR) -> dict:
    """The lookup: the library entry for this profile, why, and what its manifest
    says it was trained and checked on (contract 10.2).

    Only `machine_type` and `drive` are read. The same profile and manifests
    always give the same choice. An entry whose manifest cannot be read is
    never picked. Raises LookupUnavailable when the general one cannot be read.
    """
    machine_type, drive = profile.get("machine_type"), profile.get("drive")
    picked, reason = GENERAL, REASON_NOT_GIVEN if machine_type is None else REASON_NO_ENTRY
    for row_type, row_drive, entry in TABLE:
        if row_type == machine_type and row_drive in (None, drive):
            picked, reason = entry, REASON_MATCH if row_drive is None else REASON_MATCH_DRIVE
            break
    summary = read_entry(picked, entries_dir)
    if summary is None and picked != GENERAL:
        reason = REASON_UNREADABLE.format(entry=picked)
        picked, summary = GENERAL, read_entry(GENERAL, entries_dir)
    if summary is None:
        raise LookupUnavailable(f"the {GENERAL} entry's manifest cannot be read, so no library entry can be picked")
    reason = reason.format(machine_type=machine_type, drive=drive)
    return {"library_entry": picked, "version": summary["version"], "reason": reason,
            **{k: v for k, v in summary.items() if k not in ("library_entry", "version")}}
