"""A rule-based report writer used instead of Claude when there is no key or network.

It is not Claude and says so: its summary starts with "Stand-in report:" and
the run is marked simulated with model "stand-in (no Claude call)". It reads
the same summary the tools return, makes the same tool calls (except
search_parts, so it never touches the network) and its report goes through
the same checks as a real one.
"""

from __future__ import annotations

import json

from .report import EVIDENCE_SPREADS, NOT_FAULT_DIAGNOSES
from .tools import Tools

MODEL_NAME = "stand-in (no Claude call)"
UNRECOGNISED = "unrecognised_pattern"
CITE_PER_FAULT = 3  # at most this many readings cited for each fault type

# PLACEHOLDER actions, not engineering advice. Other fault types get a generic one.
ACTIONS = {
    "unbalance_like": "Check the rotor for unbalance: clean and rebalance it.",
    "looseness_or_rattle": "Check the mounting and tighten the bolts.",
    UNRECOGNISED: "Inspect the machine: some readings match no known fault type.",
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
    unknown = counts.get(UNRECOGNISED, 0)

    # PLACEHOLDER RULE, not engineering advice: fix the fault seen in the most
    # windows first; ties go alphabetically.
    faults = sorted((d for d in counts if d not in NOT_FAULT_DIAGNOSES), key=lambda d: (-counts[d], d))

    # Cite, for each fault, the readings in the feature table that moved most
    # in its windows, if they moved enough: the same rule check_report applies.
    cited = []
    for fault in faults:
        moved = sorted(
            ((peak, feature) for feature, peak in peaks.get(fault, {}).items()
             if feature in table and peak >= EVIDENCE_SPREADS),
            key=lambda pair: (-pair[0], pair[1]),
        )
        for _, feature in moved[:CITE_PER_FAULT]:
            if feature not in cited:
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

    order = faults + ([UNRECOGNISED] if unknown else []) + [d for d in ("normal", "stopped", "warming_up") if d in counts]
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
            fix += "; no reading in the feature table moved enough, so none is cited"
        sentences.append(fix + ".")
    if unknown:
        sentences.append(f"The cause of the {unknown} unrecognised pattern windows is not known: inspect the machine.")
    if not faults and not unknown:
        sentences.append("No fault found.")

    actions = [ACTIONS.get(f, f"Inspect the machine for {name(f)}.") for f in faults]
    if unknown or not actions:
        actions.append(ACTIONS[UNRECOGNISED] if unknown else ACTIONS["normal"])

    report = {
        "summary": "Stand-in report: " + " ".join(sentences),
        "priority": priority,
        "cited_features": cited,
        "recommended_action": " ".join(actions),
        "part_reference": "",
    }
    return report, {}


def name(diagnosis: str) -> str:
    """A diagnosis for people: `unbalance_like` reads "unbalance-like"."""
    return diagnosis.replace("_like", "-like").replace("_", " ")


stand_in_call.model_name = MODEL_NAME
