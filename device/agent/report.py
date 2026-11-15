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

# Diagnoses that are no finding at all. A log with nothing else needs no attention.
QUIET_DIAGNOSES = ("normal", "stopped", "warming_up")

# Words that name a fault type or one of its usual causes, by fault type (the names and the causes
# in brackets in effente/fault_types.py TYPES; a test keeps the names in step). The free text may
# use a word only if the records it read were diagnosed as one of the types listed under it. Each
# word is the start of a word: "loose" also finds "looseness" and "loosened".
FAULT_WORDS = {
    "vibration_too_high": (),  # "vibration" alone names no cause
    "unbalance_like": ("unbalanc", "imbalanc", "out of balance", "rebalanc", "bent", "lost blade"),
    "misalignment_like": ("misalign", "realign", "coupling"),
    "looseness_or_rattle": ("loose", "rattl", "soft foot", "cracked"),
    "repeating_knock": ("knock", "click", "belt splice", "gear tooth"),
    "impacts": ("impact", "jolt", "bang", "collision", "loose"),
    "bearing_defect_like": ("bearing",),
    "speed_change": ("speed change", "belt slip", "slipping"),
    "electrical_motor": ("electrical", "winding"),
    "new_tone_whine_squeal": ("whin", "squeal", "bearing"),
    "grinding_or_hiss": ("grind", "hiss", "lubricat", "cavitat", "arcing"),
    "alarm_or_beeping": ("beep",),
    "overheating": ("overheat", "too hot"),
    "heating_up_over_time": ("heating up", "overheat"),
    "local_hot_spot": ("hot spot", "hotspot", "bearing"),
    "area_running_cold": ("running cold", "blocked fins"),
    "excess_temperature_fluctuation": ("temperature swing", "temperature fluctuat"),
    "unsteady_vibration_or_sound": ("unsteady", "intermittent"),
    "room_too_hot_or_humid": ("humid", "condensation"),
    "device_moved": ("device moved", "device has been moved", "device was moved"),
    "camera_moved_or_blocked": ("camera",),
}
# Diagnosis names of contract version 1, still in older logs.
OLD_DIAGNOSES = {"imbalance": "unbalance_like", "loose_mount": "looseness_or_rattle"}

# A number said to be how far a reading moved: "7 spreads", "7 times its usual spread", "7x".
# Such a number must be a value of a cited reading; a window count or a year is not one.
_WORD_NUMBERS = ("one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
                 "fifteen sixteen seventeen eighteen nineteen twenty thirty forty fifty hundred").split()
_WORD_VALUES = dict(zip(_WORD_NUMBERS, [*range(1, 21), 30, 40, 50, 100]))
MOVEMENT = re.compile(
    r"(?<![\w.])(\d+(?:\.\d+)?|" + "|".join(_WORD_NUMBERS) + r")"
    r"(?:\s*[x\u00d7](?!\w)|[\s-]*(?:spreads?|sigmas?|\u03c3)(?!\w)"
    r"|\s+times\s+(?:(?:its|the|their)\s+)?(?:usual|normal|typical))",
    re.IGNORECASE,
)


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


def check_report(report: dict, records: list[dict], feature_names, parts_found: str = "") -> list[str]:
    """Return what is wrong with a report. An empty list means it passes.

    Applies to every report, whoever wrote it. In order:

    1. Shape: the keys, their types and nothing extra. A report with the wrong
       shape is not checked further.
    2. `priority` is one of `PRIORITIES`. Each cited reading is in the feature
       table and moved (`EVIDENCE_SPREADS` or more, either way) in the readings
       the agent was given, in a window diagnosed as a fault type: movement
       only in `normal`, `stopped`, `warming_up` or `unrecognised_pattern`
       windows does not justify a citation.
    3. `priority` agrees with the diagnoses (`priority_problems`), and
       `part_reference` is empty unless it is in `parts_found`, the text a
       parts lookup returned for this run. No lookup runs today, so it is empty.
    4. The summary and the recommended action name no reading that is not
       cited, unless it was switched off in some window.
    5. They use no word for a fault type the records were not diagnosed with
       (`undiagnosed_fault_problems`).
    6. Every number in them is a count, a time or the unknown score the agent
       was given, or a value of a cited reading; a number said to be a
       reading's movement must be a value of a cited reading
       (`unknown_number_problems`).

    `feature_names` is the feature table: its names, or name -> sentence. With
    the sentences, a cited reading's own sentence may be quoted with the digits
    in it ("vibration at 3-10x running speed").

    Not checked, because code cannot read prose: a cause or a part named in
    words outside `FAULT_WORDS` ("replace the motor"), a reading described in
    its own words without a number, a plain number that happens to equal a
    count, and whether the recommended action is sound. A word in
    `FAULT_WORDS` is refused even in a denial ("no sign of looseness").
    """
    problems = shape_problems(report)
    if problems:
        return problems
    sentences = feature_names if isinstance(feature_names, dict) else {}
    feature_names = list(feature_names)
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
    problems += priority_problems(report, records)
    if report["part_reference"].strip() and report["part_reference"].strip().lower() not in parts_found.lower():
        problems.append("part_reference must be empty: no parts lookup gave it")
    problems += uncited_reading_problems(report, records, feature_names)
    problems += undiagnosed_fault_problems(report, records, feature_names)
    problems += unknown_number_problems(report, records, feature_names, sentences)
    return problems


def priority_problems(report: dict, records: list[dict]) -> list[str]:
    """Return a problem if the priority contradicts the diagnoses.

    A finding is a record diagnosed as a fault type or as `unrecognised_pattern`.
    With no finding in any record the priority must be `none`. While the newest
    record is a finding it must not be `none`. After a finding has cleared (the
    newest record is quiet) any priority passes: how soon is a judgement.
    """
    if report["priority"] not in PRIORITIES or not records:
        return []
    if all(r.get("diagnosis") in QUIET_DIAGNOSES for r in records):
        if report["priority"] != "none":
            return ["priority must be none: no record shows a fault type or an unrecognised pattern"]
    elif report["priority"] == "none" and records[-1].get("diagnosis") not in QUIET_DIAGNOSES:
        return [f"priority must not be none: the newest record is diagnosed {records[-1].get('diagnosis')}"]
    return []


def undiagnosed_fault_problems(report: dict, records: list[dict], feature_names: list[str]) -> list[str]:
    """Return a problem for each word in the free text that names a fault the records do not show.

    The words are `FAULT_WORDS`. A word passes if any fault type listed under
    it is a diagnosis in the records. Reading names are left out first, so a
    reading called `bearing_wear` is not the word "bearing".
    """
    diagnosed = {OLD_DIAGNOSES.get(d, d) for d in (r.get("diagnosis") for r in records)}
    owners: dict[str, set[str]] = {}
    for fault, words in FAULT_WORDS.items():
        for word in words:
            owners.setdefault(word, set()).add(fault)
    names = sorted(_reading_names(records, feature_names), key=len, reverse=True)
    problems = []
    for key in FREE_TEXT_KEYS:
        text = report[key]
        for name in names:
            text = _name_pattern(name).sub(" ", text)
        text = " ".join(text.lower().replace("-", " ").split())
        for word, faults in owners.items():
            if not faults & diagnosed and re.search(rf"(?<![a-z]){re.escape(word)}", text):
                problems.append(f"{key} names a fault the records do not show: {word}")
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


def unknown_number_problems(
    report: dict, records: list[dict], feature_names: list[str], sentences: dict[str, str] | None = None
) -> list[str]:
    """Return a problem for each number in the free text that the readings do not back.

    Two kinds of number pass:

    - what the agent was given about the session: the window count, the count
      of each diagnosis, the count of windows a reading was switched off in,
      the highest unknown score, and the first and last times (whole, or their
      date, or their hour and minute; one piece such as the year is not a time);
    - a value of a cited reading: its peak and mean from `get_readings`, and
      its value in any one record.

    A number said to be a reading's movement (`MOVEMENT`: "7 spreads", "seven
    times its usual spread", "7x") must be of the second kind. Sign is ignored,
    because peaks are given as sizes. Digits inside a reading name, a machine
    id or a device id are part of the name, and so are digits in a cited
    reading's sentence from the feature table (`sentences`), quoted whole or
    without its bracketed ending. Anything else must match exactly:
    "about 10" does not match a reading of 9.6.
    """
    given = summarize_readings(records)
    session = _numbers_in([given.get(key) for key in ("windows", "diagnoses", "masked_windows", "max_unknown_score")])
    cited = set(report["cited_features"])
    values = _numbers_in([activity for name, activity in (given.get("feature_activity") or {}).items() if name in cited])
    for by_reading in (given.get("peak_by_diagnosis") or {}).values():
        values |= _numbers_in([peak for name, peak in by_reading.items() if name in cited])
    for record in records:
        values |= _numbers_in([v for name, v in (record.get("features") or {}).items() if name in cited])
    times = set()
    for at in (given.get("first_at"), given.get("last_at")):
        if isinstance(at, str) and at:
            times |= {at, at[:10], at[11:19], at[11:16]} - {""}
    names = _reading_names(records, feature_names) | times
    for record in records:
        names |= {str(record[key]) for key in ("machine_id", "device_id") if record.get(key)}
    quoted = set()
    for name in cited:
        sentence = str((sentences or {}).get(name) or "").strip()
        quoted |= {sentence, sentence.split("(")[0].strip()} - {""}
    problems = []
    for key in FREE_TEXT_KEYS:
        text = report[key]
        for sentence in sorted(quoted, key=len, reverse=True):
            text = re.sub(re.escape(sentence), " ", text, flags=re.IGNORECASE)
        for name in sorted(names, key=len, reverse=True):  # longest first: one name may contain another
            text = _name_pattern(name).sub(" ", text)
        for said in dict.fromkeys(match.group(1) for match in MOVEMENT.finditer(text)):
            if float(_WORD_VALUES.get(said.lower(), said)) not in values:
                problems.append(f"{key} gives a reading's movement that no cited reading has: {said}")
        text = MOVEMENT.sub(" ", text)
        for number in dict.fromkeys(NUMBER.findall(text)):
            if float(number) not in session | values:
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
