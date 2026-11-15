"""The library's entries: what each is built from. training/library/build.py builds them.

An entry is a declared list of sources (training/library/sources.py). Changing what an entry is
trained on is a change to its list here, nothing else.

Simulated machine settings below (speeds, loads, mounts) are choices made to spread the machines
out, not measurements of real machines.
"""

from __future__ import annotations

from dataclasses import dataclass

from training.library.sources import MPU6050, MPU6500, PublicSet, Recording, SimConveyor, SimPump, SimServerRack  # noqa: F401

# How long each simulated machine is watched: two whole days from Monday 08:00, so every part of a
# working day (both shifts, the breaks, the run-down, the night stop, the start-up) is in the
# training rows twice. README.md, "Training length", has the run behind it (simulated data only).
TRAIN_HOURS = 48.0
# generic_rotating pools six machines, so each is watched for one whole day (144 machine-hours in
# all): two days each did not fit in the memory of the machine it was built on.
GENERIC_HOURS = 24.0


@dataclass(frozen=True)
class FaultCheck:
    """One scorecard check: a simulated fault, and the fault type the device should name for it."""
    fault: str  # a fault the first training source can switch on (effente.simulate_conveyor.FAULT_KINDS, or
    # for a server rack effente.simulate_server's)
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


# Another type of machine, never trained on: what an entry should not fit. Evaluation only, because
# its simulator's accelerometer noise is not the MPU-6500's (sources.py).
WRONG_MACHINE = SimServerRack("sim_server_rack", note="wrong machine: a server rack, for the fit check")

GENERIC_ROTATING = Entry(
    "generic_rotating", "0.1.0", "unverified",
    sources=(
        # Six simulated drives: speed from 0.55x to 1.55x, load from 0.7x to 1.2x, six mounts.
        SimConveyor("sim_drive_a", seed=101, hours=GENERIC_HOURS),
        SimConveyor("sim_drive_b", seed=102, hours=GENERIC_HOURS, speed=0.70, load=0.85, turn_deg=(90, 0, 0), gain=0.6, ambient_c=3),
        SimConveyor("sim_drive_c", seed=103, hours=GENERIC_HOURS, speed=1.30, load=1.10, turn_deg=(0, 90, 0), gain=1.5, ambient_c=-3),
        SimConveyor("sim_drive_d", seed=104, hours=GENERIC_HOURS, speed=0.55, load=1.20, turn_deg=(0, 0, 90), gain=0.8, ambient_c=6),
        SimConveyor("sim_drive_e", seed=105, hours=GENERIC_HOURS, speed=1.55, load=0.70, turn_deg=(30, 45, 0), gain=1.2),
        SimConveyor("sim_drive_f", seed=106, hours=GENERIC_HOURS, speed=0.85, load=0.95, turn_deg=(180, 0, 20), gain=2.0, ambient_c=-5),
        SimPump("sim_pump"),  # models an MPU6050: evaluation only
        WRONG_MACHINE,
    ) + PUBLIC_EVALUATION,
    fault_checks=SIM_FAULT_CHECKS,
)

CONVEYOR = Entry(
    "conveyor", "0.1.0", "demo",
    sources=(
        # Recordings of the demo conveyor replace this line, one Recording per session:
        #   Recording("conveyor_session_01", "data/real/conveyor/session-01"),
        SimConveyor("sim_conveyor", seed=21, hours=TRAIN_HOURS),
        WRONG_MACHINE,
    ),
    fault_checks=SIM_FAULT_CHECKS,
)

# For fan and pump: a conveyor is the other type of machine. Evaluation only, never run by the build.
WRONG_MACHINE_CONVEYOR = SimConveyor("sim_conveyor", used_for="evaluation", seed=21,
                                     note="wrong machine: a conveyor, for the fit check")

# The only simulated fans in the repo are a server's cooling fans (effente.simulate_server: six of
# them at 6,000-14,000 RPM, two power-supply fans and a hard drive, felt through the rack). That is
# what this entry is trained on: not an industrial fan. Two whole days, as the conveyor entry.
# Of the rack's faults only the worn fan bearing has a fault type that plainly matches and lasts to
# the end of the check (a bump is over in a second; a stopped fan, a hot connector, a failed room
# cooler and a clicking drive have no single matching type on the accelerometer and gyro).
FAN = Entry(
    "fan", "0.1.0", "unverified",
    sources=(
        SimServerRack("sim_rack_fans", used_for="training", mpu6500=True, seed=31, hours=TRAIN_HOURS),
        WRONG_MACHINE_CONVEYOR,
        PUBLIC_EVALUATION[2],  # dcase2024_fan_sound: sound only
    ),
    fault_checks=(FaultCheck("fan3_bearing", "bearing_defect_like"),),
)

# effente.simulate_day's cooling-water pump, through the MPU-6500. The simulator makes one day per
# seed, midnight to midnight, so two days are two seeds (another day's schedule, valve moves and
# background events, on the same mount), pooled as two machines. It has no faults, so no fault
# check: every fault type stays "not validated".
PUMP = Entry(
    "pump", "0.1.0", "unverified",
    sources=(
        SimPump("sim_pump_day_a", used_for="training", imu=MPU6500, seed=41, start_h=0.0, minutes=24 * 60.0),
        SimPump("sim_pump_day_b", used_for="training", imu=MPU6500, seed=42, start_h=0.0, minutes=24 * 60.0),
        WRONG_MACHINE_CONVEYOR,
    ),
)

ENTRIES = {e.entry_id: e for e in (GENERIC_ROTATING, CONVEYOR, FAN, PUMP)}
