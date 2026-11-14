"""The answers a setup run reads, the draft it writes, and the checks in code that
every draft passes, whoever wrote it (platform/CONTRACT.md section 10.3).

A draft is `{"profile": {the five fields}, "explanation": "..."}`. The writer (a
model or the stand-in) is given the answers and nothing else: not the library,
not the lookup table, not a manifest. Its draft has no place to name an entry.
"""

from __future__ import annotations

import re
from pathlib import Path

from setup import lookup

# A question the page asks. `about` names the profile field it asks about, so the
# stand-in knows where to look; the model reads every answer. `answer` is the
# person's words, or None for "I don't know".
DONT_KNOW = re.compile(r"\b(?:i\s+)?(?:don'?t|do\s+not)\s+know\b|\bnot\s+sure\b|\bunknown\b|\bno\s+idea\b", re.I)
NUMBER = re.compile(r"\d+(?:\.\d+)?")
THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}\b)")


class AnswersUnavailable(Exception):
    """The answers cannot be read, or are not in the shape below."""


def read_answers(raw) -> list[dict]:
    """The answers, checked: a non-empty list of `{"question", "answer", "about"?}`.

    `answer` is text or null ("I don't know"). `about` is one of the five fields
    or null. Other keys are dropped: only these reach the writer.
    """
    if not isinstance(raw, list) or not raw:
        raise AnswersUnavailable("the answers must be a non-empty JSON list")
    answers = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict) or not isinstance(item.get("question"), str) or not item["question"].strip():
            raise AnswersUnavailable(f"answer {i} needs a 'question' (text)")
        answer, about = item.get("answer"), item.get("about")
        if answer is not None and not isinstance(answer, str):
            raise AnswersUnavailable(f"answer {i}: 'answer' must be text, or null for \"I don't know\"")
        if about is not None and about not in lookup.FIELDS:
            raise AnswersUnavailable(f"answer {i}: 'about' must be one of {', '.join(lookup.FIELDS)}, or null")
        answers.append({"question": item["question"], "answer": answer, "about": about})
    return answers


def dont_know(answer: str | None) -> bool:
    return answer is None or not answer.strip() or bool(DONT_KNOW.search(answer))


def numbers_in(text: str) -> list[float]:
    """Every number written in `text`, "1,450" read as 1450."""
    return [float(n) for n in NUMBER.findall(THOUSANDS.sub("", text))]


def answer_numbers(answers: list[dict]) -> set[float]:
    return {n for a in answers if a["answer"] for n in numbers_in(a["answer"])}


def draft_schema() -> dict:
    """The JSON schema a model is asked to answer in. It has no place for an entry."""
    nullable = {name: {"type": ["string", "null"], "enum": [*values, None]} for name, values in lookup.VALUES.items()}
    speed = {"anyOf": [{"type": "number"}, {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
                       {"type": "null"}]}
    profile = {"type": "object", "additionalProperties": False, "required": list(lookup.FIELDS),
               "properties": {name: speed if name == "speed_rpm" else nullable[name] for name in lookup.FIELDS}}
    return {"type": "object", "additionalProperties": False, "required": ["profile", "explanation"],
            "properties": {"profile": profile, "explanation": {"type": "string"}}}


def _spellings(entry_id: str) -> re.Pattern:
    """An entry id in any case, with `_`, `-` or a space between its words."""
    words = [re.escape(w) for w in re.split(r"[_\-\s]+", entry_id) if w]
    return re.compile(r"(?<![A-Za-z0-9])" + r"[_\-\s]+".join(words) + r"(?![A-Za-z0-9])", re.I)


def check_draft(draft, answers: list[dict], entries_dir: Path = lookup.ENTRIES_DIR) -> list[str]:
    """What is wrong with a draft, in words a model can act on. [] when it passes."""
    if not isinstance(draft, dict) or set(draft) != {"profile", "explanation"}:
        keys = sorted(draft) if isinstance(draft, dict) else type(draft).__name__
        return [f"the answer must have exactly the keys profile and explanation, not {keys}"]
    profile, explanation = draft["profile"], draft["explanation"]
    problems = []
    if not isinstance(profile, dict) or set(profile) != set(lookup.FIELDS):
        keys = sorted(profile) if isinstance(profile, dict) else type(profile).__name__
        problems.append(f"profile must have exactly the keys {', '.join(lookup.FIELDS)}, not {keys}")
        profile = {}
    if not isinstance(explanation, str) or not explanation.strip():
        problems.append("explanation must be text saying what was understood")
        explanation = ""
    problems += lookup.field_problems(profile)

    said = answer_numbers(answers)
    speed = profile.get("speed_rpm")
    if speed is not None and lookup.speed_ok(speed):
        for value in speed if isinstance(speed, list) else [speed]:
            if float(value) not in said:
                problems.append(f"speed_rpm {value:g} is not a number the person wrote: leave it null rather than guess")
    for value in numbers_in(explanation):
        if value not in said:
            problems.append(f"explanation has the number {value:g}, which the person did not write")

    # The agent never names a library entry. A word that is also the person's own
    # (in an answer) or a value the profile holds (machine type conveyor) is theirs.
    theirs = " ".join(a["answer"] or "" for a in answers)
    values = {v for v in profile.values() if isinstance(v, str)}
    for entry_id in lookup.entry_ids(entries_dir):
        pattern = _spellings(entry_id)
        if pattern.search(explanation) and entry_id not in values and not pattern.search(theirs):
            problems.append(f"explanation names the library entry {entry_id}: the setup agent never names one, "
                            "the lookup picks it afterwards")
    return problems
