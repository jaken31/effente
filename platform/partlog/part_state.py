"""A part's state, worked out from its entries, the machine's alarms and the
machine's records (platform/CONTRACT.md section 8.7). Never stored.

The four rules, first that fits wins:

    needs_repair   the newest finding or inspection says so, and no repair is newer
    possible       an alarm that is open or has no answer, started after the newest
                   repair, names a fault type on the part's list
    nothing_found  the probe has sent a record since the newest repair,
                   or since the part was made if it was never repaired
    not_checked    otherwise

`nothing_found` does not mean healthy: only that the probe has been on the
machine and nothing points at this part. A `note` or a `review` changes nothing.
"""

import datetime as dt

NEEDS_REPAIR, POSSIBLE, NOTHING_FOUND, NOT_CHECKED = "needs_repair", "possible", "nothing_found", "not_checked"
VERDICT_KINDS = ("finding", "inspection")  # the entries that say whether the part needs repair


def timestamp(text):
    """ISO time to a UTC timestamp. No zone means UTC. Anything else sorts first."""
    try:
        value = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return float("-inf")
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return value.timestamp()


def _state(name, since=None, reason="", alarm_id=None, entry_id=None):
    return {"name": name, "since": since, "reason": reason, "alarm_id": alarm_id, "entry_id": entry_id}


def work_out(part, entries, alarms, first_record_after):
    """The state of `part`.

    `entries` are the part's own, in the order they were added: of two with the
    same `at`, the one added later is the newer. `alarms` are the machine's.
    `first_record_after(timestamp)` gives the `at` of the machine's oldest
    record newer than that time, or None.
    """
    repair = verdict = None  # (how new, the entry)
    for order, entry in enumerate(entries):
        newness = (timestamp(entry.get("at")), order)
        if entry.get("kind") == "repair":
            if repair is None or newness > repair[0]:
                repair = (newness, entry)
        elif entry.get("kind") in VERDICT_KINDS:
            if verdict is None or newness > verdict[0]:
                verdict = (newness, entry)

    if verdict and verdict[1].get("needs_repair") is True and (repair is None or repair[0] < verdict[0]):
        entry = verdict[1]
        return _state(NEEDS_REPAIR, entry["at"], f"{entry['kind']}: needs repair", entry.get("alarm_id"), entry["entry_id"])

    repaired = repair[0][0] if repair else None
    suspects = [
        a for a in alarms
        if (a["open"] or a["answer"] is None)
        and a["diagnosis"] in part["fault_types"]
        and (repaired is None or timestamp(a["start"]) > repaired)
    ]
    if suspects:
        alarm = min(suspects, key=lambda a: timestamp(a["start"]))
        why = "alarm open" if alarm["open"] else "alarm not answered"
        return _state(POSSIBLE, alarm["start"], f"{why}: {alarm['diagnosis']}", alarm["alarm_id"])

    since = first_record_after(repaired if repair else timestamp(part["created"]["at"]))
    if since is not None:
        return _state(NOTHING_FOUND, since, "the probe has been on the machine and nothing points at this part")
    after = "the repair" if repair else "the part was made"
    return _state(NOT_CHECKED, None, f"the probe has not been on the machine since {after}")
