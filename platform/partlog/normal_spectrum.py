"""The normal spectrum: the usual level of each vibration band (platform/CONTRACT.md section 9.4).

One plain function over a machine's stored records. The judging is the
device's: a record counts as normal when it says so and carries no problem.
The numbers are starting values, unverified: whether 300 records give a
steady median is not checked.
"""

import math
import statistics

from readings import key

NEEDED = 300  # records judged normal before a normal is given: five minutes at one a second
NEWEST = 2000  # the normal is taken over at most this many of the newest
BANDS = 20


def judged_normal(record):
    """Normal by the device's own judging, with a full spectrum to take the bands from."""
    spectrum = record.get("spectrum")
    return (
        record.get("diagnosis") == "normal"
        and record.get("wrong") is False
        and isinstance(spectrum, list)
        and len(spectrum) == BANDS
        and all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in spectrum)
    )


def normal_spectrum(machine_id, records, part_id=None, edges_of=lambda model_version: None):
    """The reply of the normal spectrum route, without `ok`.

    `records` are the stored records of machine `machine_id`. `edges_of(model_version)` gives
    that model's band edges, or None if it was never announced.

    Real and simulated records are never mixed: if the machine has any real
    record judged normal under the newest model, wherever the probe was, only
    real ones are used, even when the part asked for has none of its own.
    """
    timed = [r for r in records if key(r)[0] is not None]
    newest = max(timed, key=key) if timed else None
    model_version = newest.get("model_version") if newest else None
    normal = [r for r in timed if r.get("model_version") == model_version and judged_normal(r)]
    real = [r for r in normal if r.get("simulated") is not True]
    simulated = not real and bool(normal)
    used = real or normal
    if part_id is not None:
        used = [r for r in used if r.get("part_id") == part_id]
    used = sorted(used, key=key)[-NEWEST:]
    ready = len(used) >= NEEDED
    return {
        "machine_id": machine_id,
        "part_id": part_id,
        "ready": ready,
        "records": len(used),
        "needed": NEEDED,
        "model_version": model_version,
        "spectrum": [statistics.median(band) for band in zip(*(r["spectrum"] for r in used))] if ready else None,
        "spectrum_edges_hz": edges_of(model_version) if model_version is not None else None,
        "from": used[0]["at"] if ready else None,
        "to": used[-1]["at"] if ready else None,
        "simulated": simulated,
    }
