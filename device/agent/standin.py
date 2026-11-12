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
    "unknown": "Inspect the machine: some readings match no known fault.",
    "normal": "No action needed.",
}


def stand_in_call(
    machine_id: str, records: list[dict], features: dict[str, str], schema: dict, tools: Tools
) -> tuple[dict, dict]:
    table = json.loads(tools.get_feature_table())
    summary = json.loads(tools.get_readings())
    windows = summary.get("windows", 0)
    counts = {d: n for d, n in summary.get("diagnoses", {}).items() if n > 0}
    peaks = summary.get("peak_by_diagnosis", {})
    unknown = counts.get("unknown", 0)

    # PLACEHOLDER RULE, not engineering advice: fix the fault seen in the most
    # windows first; ties go alphabetically.
    faults = sorted(
        (d for d in counts if d not in ("normal", "unknown")), key=lambda d: (-counts[d], d)
    )

    # Cite each fault's feature if it was active in a window diagnosed as that
    # fault, the same rule check_report applies.
    cited = []
    for fault in faults:
        feature = FAULT_FEATURE.get(fault)
        if feature in table and peaks.get(fault, {}).get(feature, 0.0) >= ACTIVE_THRESHOLD:
            cited.append(feature)

    # PLACEHOLDER RULE, not engineering advice: none if every window is normal;
    # high if more than one kind of finding (each fault, and unknown) was seen;
    # low if a single fault was seen and the latest window reads normal; else medium.
    cleared = summary.get("last_diagnosis") == "normal"
    kinds = len(faults) + (1 if unknown else 0)
    if kinds == 0:
        priority = "none"
    elif kinds > 1:
        priority = "high"
    elif faults and cleared:
        priority = "low"
    else:
        priority = "medium"

    order = faults + (["unknown"] if unknown else []) + (["normal"] if "normal" in counts else [])
    seen = ", ".join(f"{counts[d]} {name(d)}" for d in order)
    masked = sorted(summary.get("masked_windows", {}))
    if masked:
        seen += f", with {' and '.join(masked)} switched off in some windows"
    sentences = [f"{machine_id}, {windows} windows: {seen}."]
    if faults:
        fix = f"Fix {name(faults[0])}" + (" first" if len(faults) > 1 else "")
        fix += "".join(f", then {name(f)}" for f in faults[1:])
        if cleared:
            fix += "; the latest window reads normal"
        if not cited:
            fix += "; no verified feature was clearly active, so none is cited"
        sentences.append(fix + ".")
    if unknown:
        sentences.append(f"The cause of the {unknown} unknown windows is not known: inspect the machine.")
    if not faults and not unknown:
        sentences.append("No fault found.")

    actions = [ACTIONS.get(f, ACTIONS["unknown"]) for f in faults]
    if unknown or not actions:
        actions.append(ACTIONS["unknown"] if unknown else ACTIONS["normal"])

    report = {
        "summary": "Stand-in report: " + " ".join(sentences),
        "priority": priority,
        "cited_features": cited,
        "recommended_action": " ".join(actions),
        "part_reference": "",
    }
    return report, {}


def name(diagnosis: str) -> str:
    return diagnosis.replace("_", " ")


stand_in_call.model_name = MODEL_NAME
