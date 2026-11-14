"""A rule-based profile filler used instead of a model.

It is not a model and says so: the explanation begins "Stand-in:", the run's
model is "stand-in (no model call)" and the run is marked simulated, as the
report agent's stand-in does (device/agent/standin.py). Its draft goes through
the same checks as a model's.

It reads only the answers whose `about` names a field, and looks for a fixed
list of words in them. A field is filled only when exactly one value's words are
found; otherwise it is left empty and the explanation says why. It never
converts or works out a number: a speed is kept only when the person wrote it
as one or two numbers, not in Hz. Not handled: a negation ("not belt-driven"
reads as belt).
"""

from __future__ import annotations

import re

from device.agent.standin import MODEL_NAME

from setup import lookup

from .checks import dont_know, numbers_in

LABEL = "Stand-in: "
# Words per value. Starting lists written for this stand-in, not checked on real answers.
WORDS = {
    "machine_type": {
        "conveyor": ("conveyor", "conveyer"),
        "fan": ("fan", "blower"),
        "pump": ("pump",),
        "motor": ("motor",),
    },
    "drive": {
        "electric_motor_direct": ("direct", "directly", "direct drive", "coupled", "coupling"),
        "belt": ("belt", "belts", "pulley", "pulleys", "v-belt"),
        "gearbox": ("gearbox", "gear box", "gear", "gears", "geared", "reducer"),
    },
    "mounting": {
        "rigid": ("rigid", "rigidly", "bolted", "bolts", "welded", "concrete", "solid"),
        "flexible": ("flexible", "rubber", "spring", "springs", "isolator", "isolators", "anti-vibration", "dampers"),
    },
    "sensor_location": {
        "motor_drive_end": ("drive end", "motor"),
        "gearbox": ("gearbox", "gear box"),
        "frame": ("frame", "base", "chassis"),
    },
}
# A value that counts only when no other value of its field matched: most
# machines have a motor, so "a pump with its motor" is a pump.
WEAK = {("machine_type", "motor")}
NAMES = {"machine_type": "machine type", "drive": "drive", "speed_rpm": "speed",
         "mounting": "mounting", "sensor_location": "sensor location"}


def _has(text: str, word: str) -> bool:
    return re.search(r"(?<![a-z])" + re.escape(word).replace(r"\ ", r"\s+") + r"(?![a-z])", text) is not None


def _words(field: str, texts: list[str]) -> tuple[str | None, str]:
    """(value, why it is empty) for one field from the texts about it."""
    found = [v for v, words in WORDS[field].items() if any(_has(t, w) for t in texts for w in words)]
    strong = [v for v in found if (field, v) not in WEAK]
    found = strong or found
    if len(found) == 1:
        return found[0], ""
    if found:
        return None, "your answer fits more than one: " + " and ".join(_readable(v) for v in found)
    return None, "no word I look for was in your answer"


def _speed(texts: list[str]) -> tuple[float | list | None, str]:
    if any(re.search(r"\bhz\b|\bhertz\b|\brps\b", t) for t in texts):
        return None, "your answer was not in rpm, and I do not convert"
    numbers = sorted({n for t in texts for n in numbers_in(t)})
    numbers = [int(n) if n.is_integer() else n for n in numbers]
    if any(n <= 0 for n in numbers) or len(numbers) > 2:
        return None, "your answer held numbers I cannot read as one speed or a range"
    if not numbers:
        return None, "your answer held no number"
    return (numbers[0] if len(numbers) == 1 else numbers), ""


def _readable(value) -> str:
    if isinstance(value, list):
        return f"{value[0]:g} to {value[1]:g} rpm"
    if isinstance(value, (int, float)):
        return f"{value:g} rpm"
    return value.replace("_", " ")


def stand_in_call(answers: list[dict], schema: dict, check=None) -> tuple[dict, dict]:
    profile, empty = {}, []
    for field in lookup.FIELDS:
        about = [a["answer"] for a in answers if a["about"] == field]
        known = [text.lower() for text in about if not dont_know(text)]
        if not about:
            value, why = None, "no question asked about it"
        elif not known:
            value, why = None, "you did not know"
        elif field == "speed_rpm":
            value, why = _speed(known)
        else:
            value, why = _words(field, known)
        profile[field] = value
        if value is None:
            empty.append(f"{NAMES[field]}, because {why}")

    filled = [f"{NAMES[f]} ({_readable(v)})" for f, v in profile.items() if v is not None]
    if filled:
        explanation = f"{LABEL}from your answers I filled {_join(filled)}."
    else:
        explanation = f"{LABEL}I could fill nothing from your answers."
    if empty:
        explanation += f" Left empty: {'; '.join(empty)}."
    return {"profile": profile, "explanation": explanation}, {}


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


stand_in_call.model_name = MODEL_NAME
