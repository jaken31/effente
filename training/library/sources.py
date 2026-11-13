"""Where a library entry's rows come from: simulated machines, device recordings, public sets.

Every source has a name, a kind ("simulated", "public" or "device"), what it is used for
("training" or "evaluation") and the accelerometer its rows were made with or made to look like.
The TRD forbids mixing MPU6050 data into MPU-6500 training data, so `sensor` is checked by the
build (training/library/build.py): only MPU-6500 sources may train an entry.

  SimConveyor   effente.simulate_conveyor in realistic mode (MPU-6500 noise) with the site's
                disturbances (effente.site_noise), as training/record/conveyor_eval does. The
                simulator has one speed and one mount, so two small additions here make other
                machines of it: `speed` (the whole drive turns faster or slower) and `mount`
                (the sensor head turned, and vibration passed on more or less strongly).
                It can also give the same machine a little later with one fault switched on,
                for the scorecard.
  SimPump       effente.simulate_day's cooling-water pump. It models an MPU6050 (its noise,
                range and filter), so it is evaluation only.
  Recording     a learn-mode folder recorded with the device (device/intake/recorder.py).
  PublicSet     a public data set, named with the script that converts and evaluates it.
                Evaluation only: none of the conversions in training/record/datasets/ is an
                MPU-6500 one (ims.py is MPU6050-like; ottawa.py uses the MPU-6500's low-pass
                but the MPU6050's noise; dcase.py is sound only).

Everything simulated here is fake: plausible levels, not measured on a real machine (unverified).
"""

from __future__ import annotations

import copy
import math
import tempfile
from dataclasses import dataclass, field

import numpy as np

MPU6500, MPU6050 = "MPU-6500", "MPU6050"
LEAD_IN_S = 60  # normal running before a simulated fault begins
FAULT_S = 300  # how long the simulated fault then lasts (a wear fault grows over all of it)


@dataclass
class Rows:
    """One-a-second feature rows of one machine on one mount, and optionally the same machine a
    little later with a fault switched on: {fault kind: (t, X)}, each starting with LEAD_IN_S of
    normal running."""
    t: np.ndarray
    X: np.ndarray
    names: list
    faults: dict = field(default_factory=dict)


def _stream(source, history):
    """Every one-a-second feature row of a packet source: (t, X)."""
    from device.intake.extract import feature_stream
    rows = list(feature_stream(source, hop_s=1.0, history=history))
    return np.array([t for t, _ in rows], float), np.array([x for _, x in rows], float)


class Mounted:
    """A packet source seen through another mount: the sensor head turned by `turn_deg` (about x,
    then y, then z) and vibration passed on `gain` times as strongly. Gravity and the sensor's
    offsets turn with the head but are not scaled."""

    def __init__(self, source, turn_deg=(0.0, 0.0, 0.0), gain: float = 1.0):
        self.src, self.gain = source, gain
        ax, ay, az = (math.radians(a) for a in turn_deg)
        rx = np.array([[1, 0, 0], [0, math.cos(ax), -math.sin(ax)], [0, math.sin(ax), math.cos(ax)]])
        ry = np.array([[math.cos(ay), 0, math.sin(ay)], [0, 1, 0], [-math.sin(ay), 0, math.cos(ay)]])
        rz = np.array([[math.cos(az), -math.sin(az), 0], [math.sin(az), math.cos(az), 0], [0, 0, 1]])
        self.turn = rz @ ry @ rx

    def __getattr__(self, name):  # info, sensors, label_at, ... of the wrapped source
        return getattr(self.src, name)

    def packets(self):
        limit = float(self.src.info.accel_range_g)
        for p in self.src.packets():
            if p.sensor == "accel" and len(p.t):
                steady = p.values.mean(0)  # gravity and offsets over this second
                v = (steady + self.gain * (p.values - steady)) @ self.turn.T
                p = type(p)(p.sensor, p.t, np.clip(v, -limit, limit).astype(np.float32))
            elif p.sensor == "gyro" and len(p.t):
                p = type(p)(p.sensor, p.t, (p.values @ self.turn.T).astype(np.float32))
            yield p


def _drive(speed: float, **kw):
    """effente.simulate_conveyor.ConveyorSim with the whole drive turning `speed` times as fast
    (another motor or gearing). The simulator scales its vibration and sound with speed itself."""
    from effente import simulate_conveyor as SC

    class Drive(SC.ConveyorSim):
        def _timeline(self):
            super()._timeline()
            if speed != 1.0:
                self.speed = self.speed * speed
                self.belt_v = self.belt_v * speed
                self.c_motor = SC._cumulative(self.speed)
                self.dist = SC._cumulative(self.belt_v)

    return Drive(**kw)


@dataclass(frozen=True)
class SimConveyor:
    name: str
    used_for: str = "training"
    speed: float = 1.0  # times the simulator's own drive speed (motor shaft about 29.5 Hz)
    load: float = 1.0  # times the simulator's own material load
    turn_deg: tuple = (0.0, 0.0, 0.0)  # mount: the sensor head turned about x, y, z
    gain: float = 1.0  # mount: how strongly vibration is passed on
    ambient_c: float = 0.0  # warmer or cooler room
    seed: int = 1
    start_h: float = 8.0  # Monday 08:00, mid-shift: steady running
    minutes: float = 30.0
    kind = "simulated"
    sensor = MPU6500
    can_fault = True

    def _source(self, seed: int, start_s: float, end_s: float, faults):
        from effente.site_noise import SiteNoise
        sim = _drive(self.speed, days=1, seed=self.seed + 1000 * seed, faults=list(faults), start_s=start_s,
                     end_s=end_s, realistic=True, ambient_offset_c=self.ambient_c, load_scale=self.load)
        site = SiteNoise(sim, profile="factory", seed=self.seed + 1000 * seed)
        return Mounted(site, self.turn_deg, self.gain)

    def load_rows(self, seed: int = 0, faults=()) -> Rows:
        from effente import features as F
        from effente.history import History
        from effente.simulate_conveyor import Fault
        start = self.start_h * 3600.0
        end = start + self.minutes * 60.0
        history = History()
        t, X = _stream(self._source(seed, start, end, []), history)
        later = {}
        for kind in faults:  # the same machine, carrying on from the same history, with one fault
            onset = end + LEAD_IN_S
            fault = Fault(kind, onset, onset + FAULT_S, f"{kind}, simulated", 1.0)
            later[kind] = _stream(self._source(seed, end, onset + FAULT_S, [fault]), copy.deepcopy(history))
        return Rows(t, X, list(F.FEATURE_NAMES), later)


@dataclass(frozen=True)
class SimPump:
    name: str
    used_for: str = "evaluation"
    seed: int = 1
    start_h: float = 8.0
    minutes: float = 10.0
    kind = "simulated"
    sensor = MPU6050  # effente.simulate_day models the MPU6050: evaluation only
    can_fault = False

    def load_rows(self, seed: int = 0, faults=()) -> Rows:
        from device.intake.source import FileSource
        from effente import features as F
        from effente import simulate_day as SD
        from effente.history import History
        if faults:
            raise ValueError("the pump simulator has no faults")
        t_from = self.start_h * 3600.0
        t_to = min(float(SD.DAY_S), t_from + self.minutes * 60.0)
        SD._init_worker(self.seed + 1000 * seed)  # the day's schedule and slow physics
        with tempfile.TemporaryDirectory() as tmp:  # the simulator writes raw files (about 4 MB a minute)
            for hour in range(int(t_from // 3600), int(math.ceil(t_to / 3600))):
                SD.render_hour((hour, t_from, t_to, tmp))
            t, X = _stream(FileSource(tmp, t_from, t_to), History())
        return Rows(t, X, list(F.FEATURE_NAMES))


@dataclass(frozen=True)
class Recording:
    """A learn-mode folder recorded with the device on a machine (one session on one mount)."""
    name: str
    folder: str
    used_for: str = "training"
    kind = "device"
    sensor = MPU6500  # the TRD: every device recording is made with the MPU-6500
    can_fault = False

    def load_rows(self, seed: int = 0, faults=()) -> Rows:
        from effente.model_input import load_recording
        if faults:
            raise ValueError("a recording cannot have a fault switched on")
        X, t, names = load_recording(self.folder)
        return Rows(np.asarray(t, float), np.asarray(X, float), names)


@dataclass(frozen=True)
class PublicSet:
    """A public data set, for held-out evaluation only. `how` is the existing script that converts
    and evaluates it; the library build does not run it."""
    name: str
    how: str
    sensor: str | None = MPU6050  # None: no accelerometer in the set
    used_for: str = "evaluation"
    kind = "public"
    can_fault = False

    def load_rows(self, seed: int = 0, faults=()) -> Rows:
        raise NotImplementedError(f"{self.name} is evaluated by its own script: {self.how}")
