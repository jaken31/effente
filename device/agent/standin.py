"""A rule-based report writer used instead of Claude when there is no key or network.

It is not Claude and says so: its summary starts with "Stand-in report:" and
the run is marked simulated with model "stand-in (no Claude call)". It reads
the same summary the tools return, makes the same tool calls (except
search_parts, so it never touches the network) and its report goes through
the same checks as a real one.
"""

from __future__ import annotations

import json

from .report import ACTIVE_THRESHOLD
from .tools import Tools

MODEL_NAME = "stand-in (no Claude call)"

# Which verified feature backs each fault diagnosis.
FAULT_FEATURE = {"imbalance": "once_per_turn", "loose_mount": "rattle"}

ACTIONS = {
    "imbalance": "Check the rotor for imbalance: clean and rebalance the blades.",
    "loose_mount": "Check the mounting and tighten the bolts.",
    "unknown": "Inspect the machine: the readings match no known fault.",
    "normal": "No action needed.",
}


def stand_in_call(
    machine_id: str, records: list[dict], features: dict[str, str], schema: dict, tools: Tools
) -> tuple[dict, dict]:
    table = json.loads(tools.get_feature_table())
    summary = json.loads(tools.get_readings())
    windows = summary.get("windows", 0)
    diagnoses = summary.get("diagnoses", {})
    activity = summary.get("feature_activity", {})

    findings = {d: n for d, n in diagnoses.items() if d != "normal" and n > 0}
    # Most windows wins; on a tie, unknown first, so a guess is never preferred to "unknown".
    dominant = (
        max(findings, key=lambda d: (findings[d], d == "unknown")) if findings else "normal"
    )

    cited: list[str] = []
    if dominant == "normal":
        priority = "none"
        text = f"no fault on {machine_id} in {windows} windows."
    elif dominant == "unknown":
        priority = "medium"
        text = (
            f"unknown pattern on {machine_id} in {findings['unknown']} of {windows} windows. "
            "No known fault explains it."
        )
    else:
        feature = FAULT_FEATURE.get(dominant)
        peak = activity.get(feature, {}).get("peak", 0.0) if feature else 0.0
        if feature in table and peak >= ACTIVE_THRESHOLD:
            cited = [feature]
        cleared = summary.get("last_diagnosis") == "normal"
        priority = "low" if cleared else "medium"
        text = f"{dominant.replace('_', ' ')} on {machine_id} in {findings[dominant]} of {windows} windows."
        if cleared:
            text += " The latest window reads normal."
        if not cited:
            text += " No verified feature was clearly active, so none is cited."

    for name, count in sorted(summary.get("masked_windows", {}).items()):
        text += f" {name} was switched off in {count} windows, so a fault it detects could not show there."

    report = {
        "summary": f"Stand-in report: {text}",
        "priority": priority,
        "cited_features": cited,
        "recommended_action": ACTIONS[dominant if dominant in ACTIONS else "unknown"],
        "part_reference": "",
    }
    return report, {}


stand_in_call.model_name = MODEL_NAME
