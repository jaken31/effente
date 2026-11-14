"""The shape of a part review, and the checks every review must pass.

The writer (a model or the stand-in) gives a draft: `what`, `why`,
`needs_repair` and what it cites. The code adds `how` and `since`
(`complete`). The checks follow the report agent's in device/agent/report.py
and are listed in platform/CONTRACT.md section 8.8.
"""

from __future__ import annotations

import re

from device.agent.report import NUMBER

from .history import fact_sheet

ADVICE = ("yes", "no", "not_sure")
# What the writer fills, and what the code fills. Together: every key a review has, and no other.
DRAFT_TEXT_KEYS = ("what", "why", "needs_repair")
DRAFT_LIST_KEYS = ("cited_alarms", "cited_entries")
DRAFT_KEYS = (*DRAFT_TEXT_KEYS, *DRAFT_LIST_KEYS)
CODE_KEYS = ("how", "since")
REVIEW_KEYS = ("what", "how", "since", "needs_repair", "why", "cited_alarms", "cited_entries")
# The keys a person reads as sentences. Numbers and names in them are checked.
FREE_TEXT_KEYS = ("what", "why")
# A diagnosis that is not a fault type but can still be named wrongly.
UNRECOGNISED = "unrecognised_pattern"


def build_review_schema(sheet: dict) -> dict:
    """The JSON schema a model must answer in: the draft keys only.

    `how` and `since` are not in it, so a server that forces the schema cannot
    let the model write them. The cited ids accept only ids on the fact sheet.
    """
    properties = {
        "what": {"type": "string"},
        "why": {"type": "string"},
        "needs_repair": {"type": "string", "enum": list(ADVICE)},
        "cited_alarms": _id_list([a["alarm_id"] for a in sheet["alarms"]]),
        "cited_entries": _id_list([e["entry_id"] for e in sheet["entries"]]),
    }
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def _id_list(ids: list[str]) -> dict:
    if not ids:  # an empty `enum` is not a valid schema
        return {"type": "array", "items": {"type": "string"}, "maxItems": 0}
    return {"type": "array", "items": {"type": "string", "enum": sorted(ids)}}


def complete(draft: dict, sheet: dict) -> dict:
    """Add the two fields the code fills, never the writer.

    `how` is the fault type's own text from the model announcement, or empty
    when there is no fault type. `since` is the part's `state.since`.
    """
    filled = {**draft, "how": expected_how(sheet), "since": sheet["state"]["since"]}
    review = {key: filled[key] for key in REVIEW_KEYS if key in filled}  # the contract's order
    review.update(filled)  # anything else the writer put in stays, for the shape check to refuse
    return review


def expected_how(sheet: dict) -> str:
    return sheet["fault_type"]["text"] if sheet["fault_type"] else ""


def check_draft(draft, history: dict) -> list[str]:
    """Return what is wrong with a writer's draft. An empty list means the review made from it passes.

    A draft that already holds `how` or `since` is refused: the writer may not fill them.
    """
    if not isinstance(draft, dict):
        return [f"review is not an object: {type(draft).__name__}"]
    filled = [f"{key} is filled by the code, not by the writer" for key in CODE_KEYS if key in draft]
    return filled or check_review(complete(draft, fact_sheet(history)), history)


def check_review(review, history: dict) -> list[str]:
    """Return what is wrong with a review. An empty list means it passes.

    Applies to every review, whoever wrote it. In order:

    1. Shape: the keys, their types and nothing extra. A review with the wrong
       shape is not checked further.
    2. `needs_repair` is one of `ADVICE`, and `what` and `why` are not blank.
    3. `how` and `since` are the values the code copies from the history.
    4. Every cited id is in the history supplied.
    5. Every number in `what` and `why` appears in the history supplied.
    6. `what` and `why` name no fault type and no part that is not in the
       history supplied.
    7. With no alarm and no entry that says `needs_repair: true`,
       `needs_repair` is not `yes`.
    """
    sheet = fact_sheet(history)
    problems = shape_problems(review)
    if problems:
        return problems
    if review["needs_repair"] not in ADVICE:
        problems.append(f"unknown needs_repair: {review['needs_repair']!r}")
    for key in FREE_TEXT_KEYS:
        if not review[key].strip():
            problems.append(f"{key} is empty")
    if review["how"] != expected_how(sheet):
        problems.append("how is not the fault type's own text from the model announcement")
    if review["since"] != sheet["state"]["since"]:
        problems.append("since is not the part's state.since")
    problems += _uncited(review["cited_alarms"], sheet["alarms"], "alarm_id", "an alarm")
    problems += _uncited(review["cited_entries"], sheet["entries"], "entry_id", "an entry")
    problems += unknown_number_problems(review, sheet)
    problems += unknown_name_problems(review, history, sheet)
    says_so = any(entry["needs_repair"] is True for entry in sheet["entries"])
    if review["needs_repair"] == "yes" and not sheet["alarms"] and not says_so:
        problems.append("needs_repair is yes with no alarm and no entry that says the part needs repair")
    return problems


def shape_problems(review) -> list[str]:
    """Return what is wrong with a review's keys and their types."""
    if not isinstance(review, dict):
        return [f"review is not an object: {type(review).__name__}"]
    problems = [f"missing key: {key}" for key in REVIEW_KEYS if key not in review]
    for key in (*DRAFT_TEXT_KEYS, "how"):
        if key in review and not isinstance(review[key], str):
            problems.append(f"{key} must be text: {review[key]!r}")
    if "since" in review and review["since"] is not None and not isinstance(review["since"], str):
        problems.append(f"since must be a time or null: {review['since']!r}")
    for key in DRAFT_LIST_KEYS:
        cited = review.get(key, [])
        if not isinstance(cited, list) or not all(isinstance(item, str) for item in cited):
            problems.append(f"{key} must be a list of ids: {cited!r}")
    problems += [f"unexpected key: {key}" for key in review if key not in REVIEW_KEYS]
    return problems


def _uncited(cited: list[str], rows: list[dict], id_key: str, what: str) -> list[str]:
    known = {row[id_key] for row in rows}
    return [f"cites {what} that is not in the history: {item}" for item in cited if item not in known]


def unknown_number_problems(review: dict, sheet: dict) -> list[str]:
    """Return a problem for each number in `what` and `why` that the fact sheet does not hold.

    The fact sheet's numbers are its counts, its scores, and the digits in its
    times and texts. Digits inside an id (`alarm-1234`, `conveyor-07`) are
    part of a name, not a number, on both sides. Sign is ignored. The match is
    exact: "about 2" does not match a score of 2.4. Not handled: a number
    written as a word ("one alarm").
    """
    ids = _ids(sheet)
    given = _numbers(sheet, ids)
    problems = []
    for key in FREE_TEXT_KEYS:
        for number in dict.fromkeys(NUMBER.findall(_without(review[key], ids))):
            if float(number) not in given:
                problems.append(f"{key} has a number that is not in the history: {number}")
    return problems


def unknown_name_problems(review: dict, history: dict, sheet: dict) -> list[str]:
    """Return a problem for each fault type or part that `what` and `why` name without the fact sheet naming it.

    The names looked for are every fault type in the model announcement,
    `unrecognised_pattern`, and the name and id of every other part of the
    machine. One is allowed only if it appears somewhere in the fact sheet.
    A name matches whatever its case, with `_`, `-` or a space between its
    words: `unbalance_like` is also found as "unbalance-like". Not handled: a
    fault type or a part described in other words.
    """
    # This part's own names are taken out first, on both sides, so a part
    # called "Roller" is not found inside "Drive roller".
    own = [_name_pattern(str(sheet["part"][key])) for key in ("name", "part_id") if sheet["part"].get(key)]
    sheet_text = _cleared("\n".join(_strings(sheet)), own)
    fault_types = {str(f["name"]) for f in history["fault_types"] if f.get("name")} | {UNRECOGNISED}
    parts = {str(p[key]) for p in history["other_parts"] for key in ("name", "part_id") if p.get(key)}
    kinds = [("a fault type", name) for name in fault_types] + [("a part", name) for name in parts]
    patterns = {name: _name_pattern(name) for _, name in kinds}
    allowed = {name for name, pattern in patterns.items() if pattern.search(sheet_text)}
    clear = own + [patterns[name] for name in sorted(allowed, key=len, reverse=True)]
    problems = []
    for key in FREE_TEXT_KEYS:
        text = _cleared(review[key], clear)
        for what, name in sorted(kinds):
            if name not in allowed and patterns[name].search(text):
                problems.append(f"{key} names {what} that is not in the history: {name}")
    return problems


def _cleared(text: str, patterns: list[re.Pattern]) -> str:
    for pattern in patterns:
        text = pattern.sub(" ", text)
    return text


def _name_pattern(name: str) -> re.Pattern:
    """Match a name on its own, whatever its case, with `_`, `-` or spaces between its words."""
    words = [re.escape(word) for word in re.split(r"[\s_-]+", name.strip()) if word]
    return re.compile(r"(?<!\w)" + r"[\s_-]+".join(words) + r"(?!\w)", re.IGNORECASE)


def _ids(value) -> list[str]:
    """Every id in a JSON-like value (the text under a key ending in `_id`), longest first."""
    found: set[str] = set()

    def walk(item, key=""):
        if isinstance(item, dict):
            for name, inner in item.items():
                walk(inner, name)
        elif isinstance(item, list):
            for inner in item:
                walk(inner, key)
        elif isinstance(item, str) and item and key.endswith("_id"):
            found.add(item)

    walk(value)
    return sorted(found, key=len, reverse=True)


def _without(text: str, ids: list[str]) -> str:
    for item in ids:
        text = text.replace(item, " ")
    return text


def _strings(value) -> list[str]:
    """Every text value in a JSON-like value. Keys are names and are left out."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        value = list(value.values())
    if isinstance(value, list):
        return [text for item in value for text in _strings(item)]
    return []


def _numbers(value, ids: list[str]) -> set[float]:
    """Every number in a JSON-like value, as a size. Text is searched once its ids are taken out."""
    if isinstance(value, bool) or value is None:
        return set()
    if isinstance(value, (int, float)):
        return {abs(float(value))}
    if isinstance(value, str):
        return {float(number) for number in NUMBER.findall(_without(value, ids))}
    if isinstance(value, dict):
        value = list(value.values())
    return set().union(*(_numbers(item, ids) for item in value))
