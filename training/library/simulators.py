"""Simulated machines for library entries, seen as the device sees a machine today.

Everything here is fake: plausible levels, not measured on a real machine (unverified). The design
numbers below (gear ratios, tooth counts, pulley sizes, belt lengths, loads, room temperatures) are
choices made to spread the machines out, not measurements of real conveyors.

  ConveyorDesign   effente.simulate_conveyor in realistic mode, with another drive. The simulator
                   keeps its drive design in module constants, so each design runs in its own
                   private copy of that module with the constants changed. effente/ is not edited.
  ServerRack       effente.simulate_server, as it is.

Both are wrapped in effente.site_noise (people, neighbouring machines, knocks, dropouts) and then
cut down to the sensors the device has today: accelerometer, gyro and the IMU's temperature.
The microphone, thermal camera and room sensor are absent, so their readings are missing in every
row and Prep leaves their columns out.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass

import numpy as np

DEVICE_SENSORS = ("accel", "gyro", "imu_temp")  # what the device has today
START_H = 8.0  # Monday 08:00: mid-shift for the conveyor, busy hours for the server
_MODULES: dict = {}


class DeviceSensors:
    """A packet source with only the sensors the device has today."""

    def __init__(self, source, keep=DEVICE_SENSORS):
        self.src = source
        self.sensors = tuple(s for s in source.sensors if s in keep)
        self.info = source.info

    def packets(self):
        for p in self.src.packets():
            if p.sensor in self.sensors:
                yield p


def _conveyor_module(**constants):
    """A private copy of effente.simulate_conveyor with some of its design constants changed."""
    from effente import simulate_conveyor as SC
    key = tuple(sorted(constants.items()))
    if key not in _MODULES:
        name = f"training.library._conveyor_design_{len(_MODULES)}"
        spec = importlib.util.spec_from_file_location(name, SC.__file__)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod  # dataclasses look their module up while it is being defined
        spec.loader.exec_module(mod)
        for k, v in constants.items():
            if not hasattr(mod, k):
                raise AttributeError(f"effente.simulate_conveyor has no constant {k}")
            setattr(mod, k, v)
        _MODULES[key] = mod
    return _MODULES[key]


@dataclass(frozen=True)
class ConveyorDesign:
    name: str
    gear1: float = 4.0  # first gearbox stage, to 1
    gear2: float = 5.0  # second stage, to 1
    teeth1: int = 17
    teeth2: int = 23
    pulley_d: float = 0.5  # drive pulley diameter, m
    belt_len: float = 60.0  # m
    load: float = 1.0  # times the simulator's own material load
    ambient_c: float = 0.0  # warmer or cooler room, degC
    seed: int = 1
    profile = "factory"

    @property
    def description(self) -> str:
        return (f"simulated conveyor (effente.simulate_conveyor, realistic mode, with site disturbances): gearbox "
                f"{self.gear1:g}:1 then {self.gear2:g}:1, {self.teeth1} and {self.teeth2} teeth, {self.pulley_d:g} m "
                f"pulley, {self.belt_len:g} m belt, load x{self.load:g}, room {self.ambient_c:+g} degC, seed {self.seed}")

    def source(self, start_s: float, end_s: float, seed: int = 0):
        mod = _conveyor_module(GEAR1=self.gear1, GEAR2=self.gear2, TEETH1=self.teeth1, TEETH2=self.teeth2,
                               PULLEY_D=self.pulley_d, BELT_LEN=self.belt_len)
        return mod.ConveyorSim(days=1, seed=self.seed + 1000 * seed, faults=[], start_s=start_s, end_s=end_s,
                               realistic=True, ambient_offset_c=self.ambient_c, load_scale=self.load)


@dataclass(frozen=True)
class ServerRack:
    name: str
    seed: int = 2
    profile = "datacenter"

    @property
    def description(self) -> str:
        return (f"simulated server rack (effente.simulate_server, with site disturbances): six cooling fans, "
                f"two power-supply fans and a hard drive, seed {self.seed}")

    def source(self, start_s: float, end_s: float, seed: int = 0):
        from effente.simulate_server import ServerRackSim
        return ServerRackSim(days=1, seed=self.seed + 1000 * seed, faults=[], start_s=start_s, end_s=end_s)


def packet_source(machine, hours: float, seed: int = 0, start_h: float = START_H):
    """The machine running normally for `hours`, with site disturbances, through the device's sensors."""
    from effente.site_noise import SiteNoise
    start = start_h * 3600.0
    sim = machine.source(start, start + hours * 3600.0, seed)
    return DeviceSensors(SiteNoise(sim, profile=machine.profile, seed=machine.seed + 1000 * seed))


def simulated_rows(machine, hours: float, seed: int = 0, start_h: float = START_H):
    """One-a-second feature rows of the machine: (t, X), made by the one signal pipeline."""
    from device.intake.extract import feature_stream
    from effente.history import History
    rows = list(feature_stream(packet_source(machine, hours, seed, start_h), hop_s=1.0, history=History()))
    return np.array([t for t, _ in rows], float), np.array([x for _, x in rows], float)


# Five conveyor drives. The first is the simulator's own design.
CONVEYORS = (
    ConveyorDesign("sim_conveyor_a", seed=11),
    ConveyorDesign("sim_conveyor_b", 3.0, 6.0, 19, 29, 0.4, 40.0, load=0.8, ambient_c=5.0, seed=12),
    ConveyorDesign("sim_conveyor_c", 5.0, 4.0, 21, 25, 0.63, 90.0, load=1.15, ambient_c=-4.0, seed=13),
    ConveyorDesign("sim_conveyor_d", 2.5, 5.0, 15, 31, 0.32, 25.0, load=0.9, ambient_c=8.0, seed=14),
    ConveyorDesign("sim_conveyor_e", 6.3, 5.0, 13, 37, 0.8, 150.0, load=1.05, ambient_c=-8.0, seed=15),
)
SERVER_RACK = ServerRack("sim_server_rack", seed=21)
