"""Changes to the fake sample that only the Machine page's tests and pictures need. Written by hand, not
by real code, and all of it simulated unless a test says otherwise.

The fake's readings end on 14 Nov at 09:20 and its records are from 16:00 that day, so no record
belongs to a visit. `stamp_visit` makes the fake's records one more visit to a part, as the receiver
would after "Probe is here": each record gets the part's id, and the part gets one reading made of
them. Its velocity is the median of theirs; nothing else is worked out.
"""

from __future__ import annotations

import statistics


def stamp_visit(state, part_id: str, *, simulated: bool = True) -> dict:
    log = state.log
    history = state._s["history"][state.current_machine()]
    for record in history:
        record["part_id"] = part_id
    velocities = [r["readings"]["vib_velocity_mm_s"] for r in history if (r.get("readings") or {}).get(
        "vib_velocity_mm_s") is not None]
    reading = {
        "part_id": part_id, "machine_id": log.machine, "start": history[0]["at"], "end": history[-1]["at"],
        "records": len(history), "velocity_mm_s": round(statistics.median(velocities), 2),
        "unknown_score": None, "alarm_line": 1.0, "model_version": history[-1].get("model_version"),
        "abnormal": any(r.get("wrong") for r in history), "alarm_ids": [], "open": False, "simulated": simulated,
    }
    log._readings[part_id].append(reading)
    part = log._find(part_id)
    part["reading"] = {"velocity_mm_s": reading["velocity_mm_s"], "at": reading["start"], "open": False,
                       "simulated": simulated}
    log.readings_changed.emit(part_id)
    log.parts_changed.emit()
    return reading


def no_normal(state) -> None:
    """The normal spectrum not ready for any part, nor for the whole machine."""
    real = state.log.normal_spectrum

    def waiting(part_id=None):
        reply = real(part_id)
        return reply and dict(reply, ready=False, spectrum=None, records=120, **{"from": None, "to": None})

    state.log.normal_spectrum = waiting
