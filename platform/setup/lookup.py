"""The machine profile's five fields (platform/CONTRACT.md section 10.1).

Plain code, standard library only. The receiver checks a stored profile with
`field_problems`, and the setup agent checks the profile it writes with the same.
"""

from __future__ import annotations

import math

# Contract 10.1. The value lists are the lifecycle doc's examples, taken as
# starting lists. Nobody has checked that they cover real machines (unverified).
VALUES = {
    "machine_type": ("conveyor", "fan", "pump", "motor", "other"),
    "drive": ("electric_motor_direct", "belt", "gearbox"),
    "mounting": ("rigid", "flexible"),
    "sensor_location": ("motor_drive_end", "gearbox", "frame"),
}
FIELDS = ("machine_type", "drive", "speed_rpm", "mounting", "sensor_location")


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def speed_ok(value) -> bool:
    """A number above 0, or a range as `[low, high]` of two such numbers with low <= high."""
    if _number(value):
        return True
    return isinstance(value, list) and len(value) == 2 and all(map(_number, value)) and value[0] <= value[1]


def field_problems(body: dict) -> list[str]:
    """What is wrong with the five fields in `body`, in words a person can act on.

    A field that is missing or `null` is empty, which is never a problem. Keys
    other than the five are not looked at.
    """
    problems = []
    for name in FIELDS:
        value = body.get(name)
        if value is None:
            continue
        if name == "speed_rpm":
            if not speed_ok(value):
                problems.append("'speed_rpm' must be a number above 0, a range as [low, high], or null")
        elif value not in VALUES[name]:
            problems.append(f"'{name}' must be one of {', '.join(VALUES[name])}, or null")
    return problems


def five_fields(body: dict) -> dict:
    """The five fields of `body`, in order, a missing one as `None`: unknown stays empty."""
    return {name: body.get(name) for name in FIELDS}
