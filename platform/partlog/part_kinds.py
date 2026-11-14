"""Which fault types point at which kind of part (platform/CONTRACT.md section 8.5).

STARTING VALUES, NOT CHECKED ON REAL FAULTS (unverified). Nobody has measured
which fault type a failing roller or mount raises on this probe: the lists are
a first guess from each fault type's own description in effente/fault_types.py.
So every link this table makes is shown as "possible", and a person can edit a
part's list.

The fault type names are read from effente/fault_types.py, not kept here: a
name below that the module does not have is dropped, and PROBLEM says so.
That module needs NumPy. Without it the table is empty and parts are made with
no fault types, which a person can still fill in by hand.
"""

import os
import sys

KINDS = ("motor", "bearing", "roller", "belt", "gearbox", "coupling", "fan", "pump", "mount", "frame", "probe_point", "other")

_STARTING_VALUES = {  # unverified, see above
    "motor": ("electrical_motor", "overheating", "heating_up_over_time", "local_hot_spot", "bearing_defect_like",
              "unbalance_like", "new_tone_whine_squeal"),
    "bearing": ("bearing_defect_like", "grinding_or_hiss", "new_tone_whine_squeal", "local_hot_spot", "overheating"),
    "roller": ("unbalance_like", "bearing_defect_like", "repeating_knock"),
    "belt": ("repeating_knock", "speed_change", "new_tone_whine_squeal", "unsteady_vibration_or_sound"),
    "gearbox": ("repeating_knock", "grinding_or_hiss", "new_tone_whine_squeal", "overheating", "impacts"),
    "coupling": ("misalignment_like", "looseness_or_rattle"),
    "fan": ("unbalance_like", "area_running_cold", "new_tone_whine_squeal"),
    "pump": ("grinding_or_hiss", "unbalance_like", "speed_change", "bearing_defect_like"),
    "mount": ("looseness_or_rattle", "impacts"),
    "frame": ("looseness_or_rattle", "vibration_too_high", "impacts"),
    "probe_point": ("device_moved", "camera_moved_or_blocked"),
    "other": (),
}


def _build():
    """The table with only names effente/fault_types.py has, and what went wrong, if anything."""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if root not in sys.path:
        sys.path.append(root)  # at the end: the repo's platform/ folder must never hide the standard library's
    try:
        from effente.fault_types import NAMES
    except ImportError as e:
        return {kind: () for kind in KINDS}, f"fault type names not read from effente/fault_types.py ({e}): new parts get no fault types"
    table = {kind: tuple(n for n in _STARTING_VALUES[kind] if n in NAMES) for kind in KINDS}
    unknown = sorted({n for names in _STARTING_VALUES.values() for n in names if n not in NAMES})
    return table, f"not in effente/fault_types.py, left out: {', '.join(unknown)}" if unknown else ""


FAULT_TYPES_BY_KIND, PROBLEM = _build()


def fault_types_for(kind):
    """The fault types a new part of this kind starts with. A starting value, unverified."""
    return list(FAULT_TYPES_BY_KIND.get(kind, ()))
