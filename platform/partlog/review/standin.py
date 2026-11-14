"""A rule-based review writer used instead of a model.

It is not a model and says so: `what` starts with "Stand-in review:", the
run's model is "stand-in (no model call)" and the run is marked simulated,
as the report agent's stand-in does (device/agent/standin.py). It reads the
same fact sheet a model is given and its review goes through the same checks.

PLACEHOLDER RULES, not engineering advice: the advice follows the part's
state as the platform worked it out (contract section 8.7). `needs_repair`
gives yes, `possible` and `not_checked` give not_sure, `nothing_found` gives no.
"""

from __future__ import annotations

from device.agent.standin import MODEL_NAME, name as readable

LABEL = "Stand-in review: "
DECIDING_KINDS = ("finding", "inspection", "repair")


def stand_in_call(sheet: dict, schema: dict, check=None) -> tuple[dict, dict]:
    part = sheet["part"]["name"] or sheet["part"]["part_id"]
    state = sheet["state"]
    entries = {entry["entry_id"]: entry for entry in sheet["entries"]}
    alarms = {alarm["alarm_id"]: alarm for alarm in sheet["alarms"]}
    fault = f"possible {readable(sheet['fault_type']['name'])}" if sheet["fault_type"] else ""
    cited_alarms: list[str] = []
    cited_entries: list[str] = []

    def cite(entry: dict | None = None, alarm_id: str | None = None) -> None:
        if entry and entry["entry_id"] not in cited_entries:
            cited_entries.append(entry["entry_id"])
        for item in (alarm_id, entry["alarm_id"] if entry else None):
            if item in alarms and item not in cited_alarms:
                cited_alarms.append(item)

    # The newest finding, inspection or repair: what a person last settled about the part.
    deciding = [entry for entry in sheet["entries"] if entry["kind"] in DECIDING_KINDS]
    last = deciding[-1] if deciding else None

    if state["name"] == "needs_repair":
        entry = entries.get(state["entry_id"])
        cite(entry, state["alarm_id"])
        what = f"{part} needs repair"
        if entry:
            what += f": {entry['by']} recorded {_article(entry['kind'])} {entry['kind']} that says so"
            if entry["text"]:
                what += f' ("{entry["text"]}")'
        if fault:
            what += f", {fault}"
        advice, why = "yes", "The newest finding or inspection says it needs repair, and no repair entry is newer."
    elif state["name"] == "possible":
        for alarm in sheet["alarms"]:
            if alarm["open"] is True or alarm["answer"] is None:
                cite(alarm_id=alarm["alarm_id"])
        count = sheet["counts"]["alarms_waiting"]
        what = f"{fault or 'a possible fault'} on {part}: {count} alarm{'' if count == 1 else 's'} open or not answered"
        advice = "not_sure"
        why = ("An alarm points at this part only because its fault type is linked to it. Nobody has "
               "confirmed it: inspect the part and answer the alarm.")
    elif state["name"] == "nothing_found":
        advice = "no"
        why = "Nothing in the log points at this part now. That does not mean it is healthy."
        if last and last["kind"] == "repair":
            cite(last)
            what = f"{part} was repaired by {last['by']} and nothing has pointed at it since"
        elif last and last["needs_repair"] is False:
            cite(last)
            what = f"{last['by']} recorded {_article(last['kind'])} {last['kind']} that says {part} does not need repair"
            answer = (alarms.get(last["alarm_id"]) or {}).get("answer") or {}
            if answer.get("verdict") == "false":
                what += ", answering an alarm as a false alarm"
        else:
            what = f"nothing points at {part}: the probe has been on the machine and no alarm or finding names it"
    else:  # not_checked, or a state this code does not know
        if last and last["kind"] == "repair":
            cite(last)
        what = f"{part} has not been checked: the probe has not been on this machine since the part was made or repaired"
        advice, why = "not_sure", "There are no readings to judge it by. Put the probe on the machine."

    draft = {
        "what": f"{LABEL}{what}.",
        "why": why,
        "needs_repair": advice,
        "cited_alarms": cited_alarms,
        "cited_entries": cited_entries,
    }
    return draft, {}


def _article(kind: str) -> str:
    return "an" if kind[:1] in "aeiou" else "a"


stand_in_call.model_name = MODEL_NAME
