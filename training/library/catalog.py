"""The library's entries: what each is built from. training/library/build.py builds them.

An entry is a declared list of sources (training/library/sources.py). Changing what an entry is
trained on is a change to its list here, nothing else.

Simulated machine settings below (speeds, loads, mounts) are choices made to spread the machines
out, not measurements of real machines.
"""

from __future__ import annotations

from dataclasses import dataclass

from training.library.sources import MPU6050, PublicSet, Recording, SimConveyor, SimPump  # noqa: F401


@dataclass(frozen=True)
class FaultCheck:
    """One scorecard check: a simulated fault, and the fault type the device should name for it."""
    fault: str  # effente.simulate_conveyor.FAULT_KINDS
    expects: str  # effente.fault_types.NAMES


@dataclass(frozen=True)
class Entry:
    entry_id: str
    version: str
    status: str  # "demo", "unverified" or "verified"
    sources: tuple
    fault_checks: tuple = ()


# Simulated faults with a fault type that plainly matches (effente.fault_types texts). The other
# simulated faults (gear wear, overload, a jam, a seized idler, idler bearing wear) have no single
# matching type and are left out, so their types stay "not validated".
SIM_FAULT_CHECKS = (
    FaultCheck("unbalance", "unbalance_like"),
    FaultCheck("misalignment", "misalignment_like"),
    FaultCheck("looseness", "looseness_or_rattle"),
    FaultCheck("splice_damage", "repeating_knock"),
    FaultCheck("motor_bearing", "bearing_defect_like"),
    FaultCheck("belt_slip", "speed_change"),
    FaultCheck("electrical", "electrical_motor"),
    FaultCheck("belt_rub", "new_tone_whine_squeal"),
    FaultCheck("lubrication_loss", "grinding_or_hiss"),
    FaultCheck("cooling_blocked", "overheating"),
)

# Public sets: evaluation only (the TRD forbids MPU6050 data in MPU-6500 training data, and no
# MPU-6500 conversion of any of them exists in training/record/datasets/).
PUBLIC_EVALUATION = (
    PublicSet("nasa_ims_test2_bearing1",
              "python3 -m training.record.datasets.ims <2nd_test> --bearing 1 --out data/real/nasa-ims/test2_bearing1.npz",
              sensor=MPU6050),
    PublicSet("ottawa_uods_vafdc", "python3 -m training.record.datasets.ottawa <unpacked folder>", sensor=MPU6050),
    PublicSet("dcase2024_fan_sound", "python3 -m training.record.datasets.dcase data/real/dcase2024-fan --out ...",
              sensor=None),
)

GENERIC_ROTATING = Entry(
    "generic_rotating", "0.1.0", "unverified",
    sources=(
        # Six simulated drives: speed from 0.55x to 1.55x, load from 0.7x to 1.2x, six mounts.
        SimConveyor("sim_drive_a", seed=101),
        SimConveyor("sim_drive_b", seed=102, speed=0.70, load=0.85, turn_deg=(90, 0, 0), gain=0.6, ambient_c=3),
        SimConveyor("sim_drive_c", seed=103, speed=1.30, load=1.10, turn_deg=(0, 90, 0), gain=1.5, ambient_c=-3),
        SimConveyor("sim_drive_d", seed=104, speed=0.55, load=1.20, turn_deg=(0, 0, 90), gain=0.8, ambient_c=6),
        SimConveyor("sim_drive_e", seed=105, speed=1.55, load=0.70, turn_deg=(30, 45, 0), gain=1.2),
        SimConveyor("sim_drive_f", seed=106, speed=0.85, load=0.95, turn_deg=(180, 0, 20), gain=2.0, ambient_c=-5),
        SimPump("sim_pump"),  # models an MPU6050: evaluation only
    ) + PUBLIC_EVALUATION,
    fault_checks=SIM_FAULT_CHECKS,
)

CONVEYOR = Entry(
    "conveyor", "0.1.0", "demo",
    sources=(
        # Recordings of the demo conveyor replace this line, one Recording per session:
        #   Recording("conveyor_session_01", "data/real/conveyor/session-01"),
        SimConveyor("sim_conveyor", seed=21),
    ),
    fault_checks=SIM_FAULT_CHECKS,
)

ENTRIES = {e.entry_id: e for e in (GENERIC_ROTATING, CONVEYOR)}
