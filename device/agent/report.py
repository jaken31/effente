"""The shape of a maintenance report, and the checks every report must pass."""

from __future__ import annotations

PRIORITIES = ["none", "low", "medium", "high"]

# A reading this many spreads from this machine's normal, either way, counts as
# moved (docs/TRD.md: "A reading 6 or more spreads from usual counts as it is").
EVIDENCE_SPREADS = 6.0

# Diagnoses that are not a fault type (platform/CONTRACT.md, Names). Readings
# that moved only in these windows justify no citation: the contract says a
# report for `normal` cites nothing, and `unrecognised_pattern` names no cause.
NOT_FAULT_DIAGNOSES = ("normal", "stopped", "warming_up", "unrecognised_pattern")


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

    The schema already limits which features can be cited. This also confirms
    each cited reading moved (`EVIDENCE_SPREADS` or more, either way) in the
    readings the agent was given, in a window diagnosed as a fault type:
    movement only in `normal`, `stopped`, `warming_up` or
    `unrecognised_pattern` windows does not justify a citation.
    """
    problems = []
    if report.get("priority") not in PRIORITIES:
        problems.append(f"unknown priority: {report.get('priority')!r}")
    for name in report.get("cited_features", []):
        active = [r for r in records if moved((r.get("features") or {}).get(name))]
        if name not in feature_names:
            problems.append(f"cites unverified feature: {name}")
        elif not active:
            problems.append(f"cites feature that did not move in the readings: {name}")
        elif all(r.get("diagnosis") in NOT_FAULT_DIAGNOSES for r in active):
            problems.append(f"cites feature that moved only in windows with no fault type: {name}")
    return problems
