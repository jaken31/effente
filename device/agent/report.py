"""The shape of a maintenance report, and the checks every report must pass."""

from __future__ import annotations

PRIORITIES = ["none", "low", "medium", "high"]

# Starting value. Tune once real feature activity exists.
ACTIVE_THRESHOLD = 0.5

# Diagnoses whose report must cite no feature (platform/CONTRACT.md, message 4).
NO_CITATION_DIAGNOSES = ("normal", "unknown")


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
    each cited feature was active in the readings the agent was given, in a
    window diagnosed as a fault: the contract says `normal` and `unknown`
    cite nothing, so activity in those windows alone does not justify a citation.
    """
    problems = []
    if report.get("priority") not in PRIORITIES:
        problems.append(f"unknown priority: {report.get('priority')!r}")
    for name in report.get("cited_features", []):
        active = [r for r in records if r.get("features", {}).get(name, 0.0) >= ACTIVE_THRESHOLD]
        if name not in feature_names:
            problems.append(f"cites unverified feature: {name}")
        elif not active:
            problems.append(f"cites feature that was not active in the readings: {name}")
        elif all(r.get("diagnosis") in NO_CITATION_DIAGNOSES for r in active):
            problems.append(f"cites feature that was only active in normal or unknown windows: {name}")
    return problems
