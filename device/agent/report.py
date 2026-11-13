"""The shape of a maintenance report, and the checks every report must pass."""

from __future__ import annotations

import re

from .data import summarize_readings

PRIORITIES = ["none", "low", "medium", "high"]

# A reading this many spreads from this machine's normal, either way, counts as
# moved (docs/TRD.md: "A reading 6 or more spreads from usual counts as it is").
EVIDENCE_SPREADS = 6.0

# Diagnoses that are not a fault type (platform/CONTRACT.md, Names). Readings
# that moved only in these windows justify no citation: the contract says a
# report for `normal` cites nothing, and `unrecognised_pattern` names no cause.
NOT_FAULT_DIAGNOSES = ("normal", "stopped", "warming_up", "unrecognised_pattern")

# Every key a report has, and no other. `build_report_schema` describes the
# same shape for the API; `shape_problems` enforces it here.
STRING_KEYS = ("summary", "priority", "recommended_action", "part_reference")
LIST_KEY = "cited_features"
# The keys a person reads as sentences. Names and numbers in them are checked.
FREE_TEXT_KEYS = ("summary", "recommended_action")

NUMBER = re.compile(r"\d+(?:\.\d+)?")


def moved(value) -> bool:
    """Whether a reading, in spreads from normal, moved enough to count."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and abs(value) >= EVIDENCE_SPREADS


def build_report_schema(feature_names: list[str]) -> dict:
    """Return the JSON schema the agent must answer in.

    `cited_features` only accepts names from the verified feature table, so the
    API rejects a report that cites anything else.
    """
    if not feature_names:
        raise ValueError("no verified features: the agent has nothing it may cite")
    properties = {
        "summary": {"type": "string"},
        "priority": {"type": "string", "enum": PRIORITIES},
        "cited_features": {
            "type": "array",
            "items": {"type": "string", "enum": sorted(feature_names)},
        },
        "recommended_action": {"type": "string"},
        "part_reference": {"type": "string"},
    }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def check_report(report: dict, records: list[dict], feature_names: list[str]) -> list[str]:
    """Return what is wrong with a report. An empty list means it passes.

    Applies to every report, whoever wrote it. In order:

    1. Shape: the keys, their types and nothing extra. A report with the wrong
       shape is not checked further.
    2. `priority` is one of `PRIORITIES`. Each cited reading is in the feature
       table and moved (`EVIDENCE_SPREADS` or more, either way) in the readings
       the agent was given, in a window diagnosed as a fault type: movement
       only in `normal`, `stopped`, `warming_up` or `unrecognised_pattern`
       windows does not justify a citation.
    3. The summary and the recommended action name no reading that is not
       cited, unless it was switched off in some window.
    4. Every number in the summary and the recommended action appears in the
       readings the agent was given.
    """
    problems = shape_problems(report)
    if problems:
        return problems
    if report["priority"] not in PRIORITIES:
        problems.append(f"unknown priority: {report['priority']!r}")
    for name in report["cited_features"]:
        active = [r for r in records if moved((r.get("features") or {}).get(name))]
        if name not in feature_names:
            problems.append(f"cites unverified feature: {name}")
        elif not active:
            problems.append(f"cites feature that did not move in the readings: {name}")
        elif all(r.get("diagnosis") in NOT_FAULT_DIAGNOSES for r in active):
            problems.append(f"cites feature that moved only in windows with no fault type: {name}")
    problems += uncited_reading_problems(report, records, feature_names)
    problems += unknown_number_problems(report, records, feature_names)
    return problems


def shape_problems(report) -> list[str]:
    """Return what is wrong with a report's keys and their types.

    The API used to enforce `build_report_schema`. A report can now come from
    anywhere, so this does the same job. Allowed values (which priority, which
    readings) are left to `check_report`.
    """
    if not isinstance(report, dict):
        return [f"report is not an object: {type(report).__name__}"]
    problems = []
    for key in (*STRING_KEYS, LIST_KEY):
        if key not in report:
            problems.append(f"missing key: {key}")
    for key in STRING_KEYS:
        if key in report and not isinstance(report[key], str):
            problems.append(f"{key} must be text: {report[key]!r}")
    cited = report.get(LIST_KEY, [])
    if not isinstance(cited, list) or not all(isinstance(name, str) for name in cited):
        problems.append(f"{LIST_KEY} must be a list of names: {cited!r}")
    for key in report:
        if key not in STRING_KEYS and key != LIST_KEY:
            problems.append(f"unexpected key: {key}")
    return problems


def uncited_reading_problems(report: dict, records: list[dict], feature_names: list[str]) -> list[str]:
    """Return a problem for each reading the free text names without citing it.

    A reading is any name in the feature table, in a record's `features` or in
    a record's `mask`. A reading switched off in some window (in its `mask`)
    may be named without being cited: saying it was off does not claim it
    moved. Only exact names are found: a reading described in other words
    ("the once per turn vibration") is not.
    """
    allowed = set(report["cited_features"])
    for record in records:
        allowed.update(record.get("mask") or [])
    problems = []
    for key in FREE_TEXT_KEYS:
        for name in sorted(_reading_names(records, feature_names) - allowed):
            if _name_pattern(name).search(report[key]):
                problems.append(f"{key} names a reading that is not cited: {name}")
    return problems


def unknown_number_problems(report: dict, records: list[dict], feature_names: list[str]) -> list[str]:
    """Return a problem for each number in the free text that the readings do not hold.

    The readings are what `get_readings` returns for these records (window
    counts, peaks, means, times, the unknown score) and each record's own
    reading values. Sign is ignored, because peaks are given as sizes. Digits
    inside a reading name, a machine id or a device id are part of the name,
    not a number. Anything else must match exactly: "about 10" does not match
    a reading of 9.6, and "6 spreads" matches only if some value is 6.
    """
    given = _numbers_in(summarize_readings(records))
    for record in records:
        given |= _numbers_in(record.get("features") or {})
    names = _reading_names(records, feature_names)
    for record in records:
        names |= {str(record[key]) for key in ("machine_id", "device_id") if record.get(key)}
    problems = []
    for key in FREE_TEXT_KEYS:
        text = report[key]
        for name in sorted(names, key=len, reverse=True):  # longest first: one name may contain another
            text = _name_pattern(name).sub(" ", text)
        for number in dict.fromkeys(NUMBER.findall(text)):
            if float(number) not in given:
                problems.append(f"{key} has a number that is not in the readings: {number}")
    return problems


def _reading_names(records: list[dict], feature_names: list[str]) -> set[str]:
    names = set(feature_names)
    for record in records:
        names.update(record.get("features") or {})
        names.update(record.get("mask") or [])
    return names


def _name_pattern(name: str) -> re.Pattern:
    """Match a name on its own, not as part of a longer word."""
    return re.compile(rf"(?<!\w){re.escape(name)}(?!\w)")


def _numbers_in(value) -> set[float]:
    """Every number in a JSON-like value, as a size. Text is searched; keys are names and are not."""
    if isinstance(value, bool) or value is None:
        return set()
    if isinstance(value, (int, float)):
        return {abs(float(value))}
    if isinstance(value, str):
        return {float(number) for number in NUMBER.findall(value)}
    if isinstance(value, dict):
        value = value.values()
    return set().union(*(_numbers_in(item) for item in value))
