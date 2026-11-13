"""Simulated belt conveyor, streamed as live sensor packets. For testing only.

Everything here is fake. Levels are plausible, not measured on a real conveyor.

The machine: a 4-pole motor (about 1,770 RPM) drives a two-stage gearbox
(4:1, then 5:1) and a 0.5 m drive pulley. The belt is 60 m long and runs at
about 2.3 m/s, so it laps every ~26 s, and its one splice gives a light knock
each lap. The device sits on the frame at the drive end: the accelerometer
feels the motor, gearbox, pulley and nearby idler rollers; the thermal camera
sees the motor, gearbox, pulley, belt, three idlers and the frame; the mic
hears all of it plus the factory.

The week: Monday to Friday two shifts 06:00-22:00 (lunch and an evening break
run the belt empty), Saturday 06:00-14:00, Sunday off. Material load varies
with the shift, with surges. The room follows day and night and the weather.
Operators walk past now and then.

Faults in the default scenario:
  idler_bearing  idler 2's bearing wears from Wednesday to Sunday: it slowly
                 heats up to 25 degC above normal and gets noisier (a slow drift)
  belt_rub       Friday 10:00-10:40 the belt runs off-track and rubs the frame:
                 a 2.8 kHz squeal and a hot spot at the belt edge
  splice_damage  from Saturday 07:00 the splice knock is 5x stronger

Realistic mode (ConveyorSim.realistic(...), 12 Nov): the device is fixed straight onto the drive-end
bearing housing, between the gearbox and the head pulley, and the thermal camera and microphone point
at the drive from close by. Levels follow published figures where there are any (sources below and in
training/record/conveyor_eval/REALISM.md); anything without a source is marked unverified there.
  - no room sensor (the device has none); the IMU's own temperature follows the housing it is fixed to
  - accelerometer noise 3.5 mg rms per axis (MPU-6500: 300 ug/sqrt(Hz) over the 218 Hz band is about 5 mg;
    our board measured 2-4 mg at rest)
  - healthy drive vibration about 2 mm/s (conveyor gearbox case studies); faults reach tens of mm/s
  - factory background 70 dB in shifts, strongest at 63-500 Hz and falling above about 2 kHz
  - 15 fault kinds (FAULT_KINDS), each with a severity: 1 is obvious, about 0.3 subtle. Slow faults
    (wear) grow over their whole window like the NASA run-to-failure bearing (slowly, then faster);
    the rest switch on within a minute

The source streams packets second by second, so any length can be tested
without writing raw files. It works anywhere a FileSource does:

  from effente.simulate_conveyor import ConveyorSim
  for p in ConveyorSim(days=7).packets(): ...
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy import ndimage, signal

from device.intake.source import Packet, SourceInfo

DAY = 86_400
MAINS_HZ = 60.0
GEAR1, GEAR2 = 4.0, 5.0
TEETH1, TEETH2 = 17, 23
PULLEY_D = 0.5
BELT_LEN = 60.0
IDLER_D = (0.127, 0.130, 0.124)
ACCEL_HZ = 1000 * (1 + 0.0021)
AUDIO_HZ = 16000 * (1 - 0.00004)
THERMAL_HZ = 4 * (1 - 0.0045)
GAIN_DB = 12.0
SHAPE = (24, 32)
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
IDLER2_CELL = 3 * 8 + 6  # grid cell (row 4, column 7) that holds idler 2 in the thermal image


@dataclass
class Fault:
    kind: str
    start_s: float
    end_s: float
    note: str
    severity: float = 1.0  # realistic mode: 1 obvious, about 0.3 subtle


# Realistic mode's fault kinds. SLOW ones grow over their window (wear); the rest switch on in a minute.
FAULT_KINDS = ("idler_bearing", "idler_seized", "belt_rub", "belt_slip", "unbalance", "looseness", "misalignment",
               "gear_wear", "motor_bearing", "splice_damage", "jam", "overload", "lubrication_loss",
               "cooling_blocked", "electrical")
SLOW = ("idler_bearing", "gear_wear", "motor_bearing", "lubrication_loss")
SUBTLE, OBVIOUS = 0.3, 1.0


def default_faults() -> list[Fault]:
    return [
        Fault("idler_bearing", 2 * DAY, 6 * DAY, "idler 2 bearing wears from Wednesday 00:00 to Sunday 00:00"),
        Fault("jam", 3 * DAY + 15 * 3600 + 20 * 60, 3 * DAY + 15 * 3600 + 23 * 60,
              "material jam, Thursday 15:20: hard stop with a jolt, restarted 3 minutes later"),
        Fault("belt_rub", 4 * DAY + 10 * 3600, 4 * DAY + 10 * 3600 + 40 * 60, "belt rubs the frame, Friday 10:00-10:40"),
        Fault("splice_damage", 5 * DAY + 7 * 3600, 99 * DAY, "splice knock 5x stronger from Saturday 07:00"),
    ]


def _lag(target, tau_s, y0=0.0):
    """First-order lag at 1 s steps with a time constant per step."""
    a = np.exp(-1.0 / np.asarray(tau_s, float)).tolist()
    tl = np.asarray(target, float).tolist()
    y, out = y0, [0.0] * len(tl)
    for _ in range(2):  # second pass starts from the end state of the first
        for k in range(len(tl)):
            y = tl[k] + (y - tl[k]) * a[k]
            out[k] = y
    return np.array(out)


def _lowpass_noise(rng, n, tau):
    a = math.exp(-1.0 / tau)
    w = rng.standard_normal(n) * math.sqrt(1 - a * a)
    return signal.lfilter([1.0], [1.0, -a], w)


def _cumulative(rate_hz):
    return np.concatenate([[0.0], np.cumsum((rate_hz[1:] + rate_hz[:-1]) * 0.5)])


def _rms_counts(level_db):
    return 32767 / math.sqrt(2) * 10 ** ((np.asarray(level_db) - 120 + GAIN_DB) / 20)


class ConveyorSim:
    sensors = ("accel", "gyro", "audio", "thermal", "room")

    @classmethod
    def realistic_sim(cls, **kw) -> "ConveyorSim":
        """The realistic setup (module notes): ConveyorSim(realistic=True, ...)."""
        return cls(realistic=True, **kw)

    def __init__(self, days: int = 7, seed: int = 1, faults: list[Fault] | None = None,
                 start_s: float = 0.0, end_s: float | None = None, week: int = 0, realistic: bool = False,
                 ambient_offset_c: float = 0.0, load_scale: float = 1.0, stops: tuple = ()):
        # `week` gives another week of the same machine and device: a different load schedule and
        # noise, the same camera (its fixed pattern stays). Week 0 is the original.
        self.days, self.seed, self.week = days, seed, week
        self.faults = default_faults() if faults is None else faults
        # realistic: device on the drive housing, no room sensor, the IMU's temperature, measured noise
        # levels (see the module notes). ambient_offset_c: another season or weather; load_scale: a
        # heavier or lighter week; stops: (start_s, end_s) planned stops (maintenance, no fault).
        self.realistic = realistic
        self.ambient_offset_c, self.load_scale, self.stops = ambient_offset_c, load_scale, tuple(stops)
        self.vib_gain, self.idler_gain = (2.2, 0.6) if realistic else (1.0, 1.0)
        self.accel_noise_g = 0.0035 if realistic else 0.0085
        self.bg_working_db, self.bg_quiet_db = (70.0, 50.0) if realistic else (62.0, 45.0)
        if realistic:
            self.sensors = ("accel", "gyro", "imu_temp", "audio", "thermal")
        self.start_s = start_s
        self.end_s = min(end_s if end_s is not None else days * DAY, days * DAY)
        # Recommended settings: accelerometer +-8 g, gyroscope +-250 deg/s. Time counts from Monday 00:00.
        self.info = SourceInfo(audio_rate_hz=AUDIO_HZ, audio_gain_db=GAIN_DB, accel_lsb_per_g=4096,
                               accel_range_g=8.0, gyro_lsb_per_dps=131.0, audio_block_len=256, week_offset_s=0.0)
        self.rng = np.random.default_rng(self._key(0))
        self._timeline()
        self.thermal_offset = float(np.random.default_rng([self.seed, 9]).uniform(-1.5, 1.5)) if realistic else 0.0
        self._banks()
        self._scene()

    # ------------------------------------------------------------ slow physics, 1 s steps
    def _timeline(self):
        rng = self.rng
        n = self.days * DAY + 2
        t = np.arange(n, dtype=float)
        day, hour = (t // DAY).astype(int), (t % DAY) / 3600
        wd = day % 7
        self.runs = []
        for d in range(self.days):
            if wd[d * DAY] == 6:
                continue
            end_h = 14.0 if wd[d * DAY] == 5 else 22.0
            s = d * DAY + 6 * 3600 + rng.uniform(-180, 180)
            e = d * DAY + end_h * 3600 + rng.uniform(-180, 300)
            self.runs.append((s, e))
        # A jam stops the belt dead in the middle of a run; it restarts when cleared.
        # A subtle jam is a stall: the belt bogs down for a few seconds and recovers (no stop).
        self.stalls = [f for f in self.faults if f.kind == "jam" and f.severity < 0.5]
        self.jams = [f for f in self.faults if f.kind == "jam" and f.severity >= 0.5]
        for f in self.jams:
            split = []
            for s, e in self.runs:
                if s < f.start_s < e:
                    split += [(s, f.start_s), (f.end_s, e)]
                else:
                    split.append((s, e))
            self.runs = split
        for a, b in self.stops:  # planned stops: no fault, the belt is simply off
            split = []
            for s, e in self.runs:
                if s < a < e:
                    split += [(s, a)] + ([(b, e)] if b < e else [])
                elif not (a <= s and e <= b):
                    split.append((s, e))
            self.runs = split
        abrupt = {f.start_s for f in self.jams}
        powered = np.zeros(n, bool)
        for s, e in self.runs:
            powered[int(s):int(e)] = True

        base = np.where(hour < 14, 0.75, 0.6)
        base = np.where(((hour >= 12) & (hour < 12.5)) | ((hour >= 18.5) & (hour < 19)), 0.05, base)
        surge = np.zeros(n)
        k = rng.exponential(1200)
        while k < n:
            surge[int(k):int(k + 120)] = 0.25
            k += rng.exponential(1200)
        self.fx = {k: np.zeros(n) for k in FAULT_KINDS}  # realistic mode: each fault's level over time (0..severity)
        for f in self.faults:
            if f.kind in self.fx and f.kind not in ("jam",):
                if f.kind in SLOW:  # wear: slowly, then faster (the shape of the NASA bearing run to failure)
                    u = np.clip((t - f.start_s) / max(1.0, f.end_s - f.start_s), 0, 1)
                    prof = (np.exp(3 * u) - 1) / (math.e ** 3 - 1) * (t >= f.start_s)
                else:
                    prof = np.clip((t - f.start_s) / 60.0, 0, 1) * (t < f.end_s)
                self.fx[f.kind] = np.maximum(self.fx[f.kind], f.severity * prof)
        load = np.clip(self.load_scale * (base + surge + 0.08 * _lowpass_noise(rng, n, 600)) + 0.6 * self.fx["overload"],
                       0, 1.6 if self.realistic else 1.0) * powered
        self.load = signal.lfilter([1 - math.exp(-1 / 20)], [1, -math.exp(-1 / 20)], load)

        ramp = np.zeros(n)
        for s, e in self.runs:
            i0, i1 = int(s), int(e)
            ramp[i0:i1] = np.clip((t[i0:i1] - s) / 8.0, 0, 1)  # soft starter, 8 s
            tail = np.arange(i1, min(n, i1 + 15))
            ramp[tail] = np.exp(-(tail - e) / (0.3 if e in abrupt else 1.5))  # a jam stops it dead
        slip = 0.01 + 0.02 * self.load
        for f in self.stalls:  # the belt bogs down for 5 s under a surge and recovers
            i = int(f.start_s)
            ramp[i:i + 5] *= 1 - 0.3 * np.sin(np.linspace(0, np.pi, len(ramp[i:i + 5])))
        self.speed = MAINS_HZ / 2 * (1 - slip) * ramp  # motor shaft, Hz
        self.running = ramp > 0.05
        self.current = np.where(powered, np.where(ramp < 1, 3.0 * (1 - ramp) + ramp, 1.0) * (0.35 + 0.65 * self.load), 0)
        for f in self.jams:  # the motor strains against the jam for a few seconds before it trips
            i = int(f.start_s)
            self.current[max(0, i - 4):i] = 3.5
        for f in self.stalls:
            i = int(f.start_s)
            self.current[i:i + 5] = np.maximum(self.current[i:i + 5], 1.8)
        fx = self.fx
        self.current = self.current * (1 + 0.10 * fx["idler_seized"] + 0.05 * fx["electrical"]
                                       + 0.05 * fx["lubrication_loss"] + 0.04 * fx["misalignment"])
        # belt slip on worn lagging: the belt runs slower than the pulley, more under load
        self.belt_slip = 0.10 * fx["belt_slip"] * (0.5 + 0.5 * np.minimum(self.load, 1.2))
        self.belt_v = math.pi * PULLEY_D * self.speed / (GEAR1 * GEAR2) * (1 - self.belt_slip)
        self.c_motor = _cumulative(self.speed)
        self.dist = _cumulative(self.belt_v)  # metres of belt travelled

        weekday_day = (wd < 5) & (hour >= 6) & (hour < 22)
        self.ambient = (20 + self.ambient_offset_c + 3 * np.sin(2 * np.pi * (hour - 9) / 24) + 1.5 * signal.lfilter(
            [1 - math.exp(-1 / 5400)], [1, -math.exp(-1 / 5400)], weekday_day.astype(float))
            + 2.0 * _lowpass_noise(rng, n, DAY) + 0.2 * _lowpass_noise(rng, n, 1200))
        self.bg_db = np.where(weekday_day | ((wd == 5) & (hour >= 6) & (hour < 14)), self.bg_working_db, self.bg_quiet_db)

        self.wear = np.zeros(n)
        self.rub = np.zeros(n, bool)
        self.rub_level = np.zeros(n)
        self.splice_gain = np.ones(n)
        for f in self.faults:
            i0, i1 = int(max(0, f.start_s)), int(min(n, f.end_s))
            if f.kind == "idler_bearing":
                if self.realistic:
                    self.wear = np.maximum(self.wear, self.fx["idler_bearing"])
                else:
                    self.wear = np.clip((t - f.start_s) / max(1, f.end_s - f.start_s), 0, 1) * (t >= f.start_s)
            elif f.kind == "belt_rub":
                self.rub[i0:i1] = True
                self.rub_level[i0:i1] = f.severity if self.realistic else 1.0
            elif f.kind == "splice_damage":
                self.splice_gain[i0:i1] = 1 + 4.0 * f.severity if self.realistic else 5.0
        run = self.running.astype(float)
        tau = lambda on, off: np.where(self.running, on, off)
        # Healthy, above the room: motor frame about +26 C at full load (TEFC frames run 40-50 C), gearbox
        # +16 C (mineral oil stays under 75-80 C), pulley +5 C, idlers +3 C (room + 20 C is the allowed limit).
        cur1 = np.minimum(self.current, 1.6 if self.realistic else 1.0)
        self.t_motor = _lag((26 * (0.25 + 0.75 * cur1 ** 2) + 25 * fx["cooling_blocked"] + 8 * fx["electrical"]
                             + 6 * fx["motor_bearing"]) * run, tau(2400, 4800))
        self.t_gear = _lag((16 * (0.4 + 0.6 * np.minimum(self.load, 1.6)) + 20 * fx["lubrication_loss"]
                            + 10 * fx["gear_wear"] + 5 * fx["misalignment"]) * run, tau(5400, 10800))
        self.t_pulley = _lag((5 * (0.3 + 0.7 * self.load) + 15 * fx["belt_slip"]) * run, tau(1800, 3600))
        idler_base = 3 * (0.5 + 0.5 * self.load) * run
        self.t_idler = [_lag(idler_base, tau(1500, 3000)),
                        _lag(idler_base + 25 * self.wear * run, tau(1500, 3000)),
                        _lag(idler_base + 35 * fx["idler_seized"] * run, tau(900, 3000))]
        self.t_rub = _lag(18.0 * self.rub_level * self.running, tau(240, 240))
        # The IMU sits in the device on the drive housing between gearbox and pulley: it follows that
        # surface with a lag, plus the device's own warmth (unverified: our board on a desk read +20 C,
        # fixed to a metal housing it should sink most of that).
        surface = 0.55 * self.t_gear + 0.3 * self.t_pulley + 0.15 * self.t_motor
        self.t_imu = _lag(surface + 9.0, np.full(n, 600.0))
        # Healthy vibration is never constant (13 Nov: without this the week's velocity varied by 1 %, so every
        # fault looked huge). Real constant-speed drives wander 10-20 % with load, oil temperature, belt tension
        # and material, which is why alarm levels sit at 2-2.5x baseline (SKF, ISO 20816 practice). Each source
        # gets its own slow drift: lognormal, about 18 % in all (two days, two hours and day to day), which with the
        # load effect gives a healthy velocity wandering about 10 % typically, +-30 % at the extremes (checked 13 Nov). Its own
        # random stream, so the loads, schedules and faults stay as they were.
        self.vib_drift = {}
        if self.realistic:
            drng = np.random.default_rng(self._key(5))
            per_day = np.repeat(drng.normal(0, 1, self.days + 1), DAY)[:n]
            for k in ("motor", "mesh", "out", "idler", "broad"):
                self.vib_drift[k] = np.exp(0.14 * _lowpass_noise(drng, n, 2 * DAY) + 0.09 * _lowpass_noise(drng, n, 7200)
                                           + 0.06 * per_day * drng.uniform(0.5, 1.0))
        self.people = []
        k = rng.exponential(900)
        while k < n:
            if weekday_day[int(k)]:
                self.people.append((k, rng.uniform(3, 6), int(rng.choice([-1, 1]))))
            k += rng.exponential(900)

    # ------------------------------------------------------------ noise banks
    def _banks(self):
        rng = np.random.default_rng(self._key(1))

        def bank(sos, n, ch=1):
            x = signal.sosfilt(sos, rng.standard_normal((ch, n) if ch > 1 else n), axis=-1)
            return x / x.std()

        fa, fs = 1000.0, 16000.0
        self.b_mat = bank(signal.butter(2, [5, 300], "band", fs=fa, output="sos"), 120_000, 3)
        self.b_res = bank(signal.tf2sos(*signal.iirpeak(45, 8, fs=fa)), 120_000, 3)
        self.b_brg = bank(signal.butter(2, [100, 250], "band", fs=fa, output="sos"), 120_000, 3)
        self.b_snoise = bank(signal.butter(2, 260, fs=fa, output="sos"), 120_000, 3)
        self.b_roll = bank(signal.butter(2, [200, 4000], "band", fs=fs, output="sos"), 960_000)
        self.b_matsnd = bank(signal.butter(2, [300, 6000], "band", fs=fs, output="sos"), 960_000)
        self.b_pink = bank(np.vstack([signal.tf2sos([0.049922035, -0.095993537, 0.050612699, -0.004408786],
                                                    [1, -2.494956002, 2.017265875, -0.522189400]),
                                      signal.butter(2, 6000, fs=fs, output="sos")]), 960_000)
        self.b_hiss = bank(signal.butter(2, [3000, 6000], "band", fs=fs, output="sos"), 960_000)
        self.b_click = bank(signal.butter(2, [500, 5000], "band", fs=fs, output="sos"), 960_000)
        self.nrng = np.random.default_rng(self._key(2))

    def _slice(self, bank, n):
        L = bank.shape[-1]
        i = int(self.nrng.integers(0, L - n))
        return bank[..., i:i + n]

    # ------------------------------------------------------------ thermal scene
    def _scene(self):
        lab = {}
        z = lambda: np.zeros(SHAPE)

        def box(name, r0, r1, c0, c1, w=1.0):
            a = lab.setdefault(name, z())
            a[r0:r1, c0:c1] = w

        box("motor", 9, 18, 1, 9)
        box("gear", 10, 19, 9, 15)
        box("pulley", 6, 19, 15, 20)
        box("belt", 11, 14, 20, 32)
        for i, (c0, c1) in enumerate(((21, 24), (25, 28), (29, 32))):
            box(f"idler{i}", 14, 16, c0, c1)
        box("rub", 11, 14, 30, 32)
        box("frame", 16, 18, 15, 32)
        box("cabinet", 1, 5, 24, 30)
        fg = sum(lab[k] for k in lab if k not in ("rub",)) > 0
        rows = np.arange(24)[:, None] * np.ones((1, 32))
        static = np.where(rows >= 20, -1.0, 0.0) * ~fg + 5.0 * lab["cabinet"] + 0.3 * lab["frame"]
        fins = np.where(rows % 2 == 0, 1.03, 0.97)
        self.w = {"motor": 0.95 * lab["motor"] * fins, "gear": 0.9 * lab["gear"], "pulley": 0.9 * lab["pulley"],
                  "idler0": 0.9 * lab["idler0"], "idler1": 0.9 * lab["idler1"], "idler2": 0.9 * lab["idler2"],
                  "rub": 0.9 * lab["rub"], "belt": 0.95 * lab["belt"]}
        blur = lambda a: ndimage.gaussian_filter(a, 0.6, mode="nearest")
        self.wb = {k: blur(v) for k, v in self.w.items()}
        self.static = blur(static)
        self.behind = ~fg
        r = np.hypot((rows - 11.5) / 11.5, (np.arange(32)[None, :] - 15.5) / 15.5) / math.sqrt(2)
        self.noise_sigma = 0.22 * (1 + 0.8 * r ** 2)
        self.fpn = np.random.default_rng([self.seed, 3]).normal(0, 0.15, SHAPE)
        self.blur = blur

    # ------------------------------------------------------------ labels and truth
    def _key(self, stream: int):
        return [self.seed, stream] if self.week == 0 else [self.seed, stream, self.week]

    def label_at(self, t: float) -> str:
        i = int(min(max(t, 0), len(self.wear) - 1))
        active = []
        if self.wear[i] > 0.05:
            active.append("idler_bearing")
        if any(f.start_s - 5 <= t < f.start_s + 30 for f in self.jams):
            active.append("jam")
        if self.rub[i] and self.running[i]:
            active.append("belt_rub")
        if self.splice_gain[i] > 1 and self.running[i]:
            active.append("splice_damage")
        if any(f.start_s - 5 <= t < f.start_s + 30 for f in self.stalls):
            active.append("jam")
        for k, v in self.fx.items():
            if k not in ("idler_bearing", "belt_rub", "splice_damage", "jam") and v[i] > 0.02:
                active.append(k)
        return "+".join(active) if active else "normal"

    def truth_at(self, t: float) -> dict:
        i = int(min(max(t, 0), len(self.wear) - 1))
        return {"running": bool(self.running[i]), "load": float(self.load[i]), "speed_hz": float(self.speed[i]),
                "ambient_c": float(self.ambient[i]), "idler2_above_ambient_c": float(self.t_idler[1][i]),
                "wear": float(self.wear[i]), "label": self.label_at(t)}

    # ------------------------------------------------------------ packets
    def packets(self):
        s = int(math.floor(self.start_s))
        while s < self.end_s:
            yield from self._second(s)
            s += 1

    def _interp(self, arr, t):
        i = np.floor(t).astype(int)
        f = t - i
        return arr[i] * (1 - f) + arr[i + 1] * f

    def _knocks(self, t0, t1):
        """Times in [t0, t1) when the splice passes (one per belt lap)."""
        d0, d1 = self._interp(self.dist, np.array([t0, t1]))
        laps = np.arange(math.ceil(d0 / BELT_LEN), math.ceil(d1 / BELT_LEN))
        if not len(laps) or d1 <= d0:
            return []
        return [t0 + (lap * BELT_LEN - d0) / (d1 - d0) * (t1 - t0) for lap in laps]

    def _second(self, s: int):
        i = s
        spd, ld, cur = self.speed[i], self.load[i], self.current[i]
        sc = spd / 29.5
        s2 = sc * sc
        wear, gain = self.wear[i], self.splice_gain[i]
        knocks = self._knocks(s, s + 1)
        tau2pi = 2 * np.pi

        # ---- vibration
        k0, k1 = math.ceil(s * ACCEL_HZ), math.ceil((s + 1) * ACCEL_HZ)
        t = np.arange(k0, k1) / ACCEL_HZ
        n = len(t)
        cm = self._interp(self.c_motor, t)
        dist = self._interp(self.dist, t)
        out_c = cm / (GEAR1 * GEAR2)
        inter_c = cm / GEAR1
        a = np.zeros((3, n))
        sin = lambda c, ph=0.0: np.sin(tau2pi * np.mod(c, 1.0) + ph)
        fx = {k: v[i] for k, v in self.fx.items()}
        if self.realistic:  # each source's drift; the motor and the gears work harder under load; warm oil damps the mesh
            dr = {k: v[i] for k, v in self.vib_drift.items()}
            lm = 0.8 + 0.4 * min(ld, 1.2)
            gm, gmesh = dr["motor"] * lm, dr["mesh"] * lm * (1 - 0.006 * self.t_gear[i])
            gout, gid, gbr = dr["out"], dr["idler"], dr["broad"]
        else:
            gm = gmesh = gout = gid = gbr = 1.0
        a[0] += gm * (0.012 * s2 * sin(cm) + 0.004 * s2 * sin(2 * cm, 1.0))
        a[1] += gm * 0.006 * s2 * sin(cm, 0.4)
        a[2] += gm * 0.009 * s2 * sin(cm, 2.0)
        a += gm * 0.003 * s2 * sin(inter_c, 0.7)[None]
        mesh = TEETH2 * inter_c
        gw = fx["gear_wear"]  # worn teeth: the mesh grows and sidebands at the intermediate shaft's rate grow more (SKF)
        a[0] += gmesh * ((1 + 1.2 * gw) * 0.007 * (0.5 + 0.5 * min(ld, 1.2)) * s2 * sin(mesh)
                 + (1 + 5 * gw) * 0.0015 * s2 * (sin(mesh - inter_c, 0.3) + sin(mesh + inter_c, 1.1))
                 + 0.004 * gw * s2 * sin(2 * mesh, 0.4))
        a[2] += gout * 0.004 * s2 * sin(out_c, 0.2)
        a[0] += gout * 0.002 * s2 * sin(out_c, 1.3)
        if self.realistic:
            a *= self.vib_gain  # on the drive housing: the motor, gearbox and pulley come through stronger
            if fx["unbalance"]:  # 1x, radial, the two radial axes 90 degrees apart (SKF)
                a[0] += 0.12 * fx["unbalance"] * s2 * sin(cm)  # obvious: about 7 mm/s (conveyor case studies: faults 5-30)
                a[1] += 0.09 * fx["unbalance"] * s2 * sin(cm, 1.57)
            if fx["misalignment"]:  # 2x above 1x, strong axially (z) (SKF)
                e = fx["misalignment"]
                a[2] += 0.13 * e * s2 * sin(2 * cm, 0.6) + 0.01 * e * s2 * sin(3 * cm, 0.2)
                a[0] += 0.02 * e * s2 * sin(2 * cm, 1.2) + 0.02 * e * s2 * sin(cm, 0.9)
            if fx["looseness"]:  # many harmonics, half order, and a rattle once a turn (SKF)
                e = fx["looseness"]
                for k in range(2, 11):
                    a[k % 3] += 0.05 * e / k ** 0.7 * s2 * sin(k * cm, 0.37 * k)
                a[2] += 0.02 * e * s2 * sin(0.5 * cm, 0.3)
                a[2] += 0.15 * e * sc * (0.5 + 0.5 * np.cos(tau2pi * np.mod(cm, 1.0))) ** 24
            if fx["electrical"] or cur > 0:  # twice-mains magnetic vibration, grows with a supply fault
                a[0] += (0.0012 + 0.006 * fx["electrical"]) * min(cur, 1.6) * np.sin(tau2pi * 2 * MAINS_HZ * t)
            if fx["motor_bearing"]:  # outer-race defect on the motor bearing, about 3.58x motor speed
                e = fx["motor_bearing"]
                bp = 3.58 * cm
                pulse_m = (0.5 + 0.5 * np.cos(tau2pi * np.mod(bp, 1.0))) ** 8
                a += 0.006 * e * s2 * self._slice(self.b_brg, n) * (0.3 + 3.0 * pulse_m)[None]
                a[0] += 0.003 * e * s2 * sin(bp)
            if fx["lubrication_loss"]:  # dry gears and bearings: broadband friction
                a += 0.004 * fx["lubrication_loss"] * sc * self._slice(self.b_brg, n)
        for j, d in enumerate(IDLER_D):
            if not (self.realistic and j == 2 and fx["idler_seized"] > 0.5):  # a seized idler stops turning
                a[2] += gid * 0.0012 * self.idler_gain * s2 * sin(dist / (math.pi * d), j)
        if self.realistic and fx["idler_seized"]:  # the belt drags over it
            a += 0.003 * fx["idler_seized"] * sc * self._slice(self.b_mat, n)
        rot = dist / (math.pi * IDLER_D[1])
        if wear > 0:
            # A spall on the outer race: every rolling element hits it, so the bearing's
            # noise comes in pulses at the defect rate (BPFO, about 3.6 x the idler's speed).
            bpfo = 3.6 * rot
            pulse = (0.5 + 0.5 * np.cos(tau2pi * np.mod(bpfo, 1.0))) ** 8
            a[2] += 0.006 * wear * s2 * sin(bpfo) * (1 + 0.6 * sin(rot))
            a += 0.004 * wear * s2 * self._slice(self.b_brg, n) * (0.3 + 3.0 * pulse)[None]
        a += gbr * 0.005 * ld * sc * self._slice(self.b_mat, n)
        a += gbr * 0.002 * sc * self._slice(self.b_res, n)
        for kt in knocks:  # the splice hitting the pulley: a short ringing knock
            dt = t - kt
            m = dt >= 0
            a[:, m] += (np.array([0.4, 0.3, 1.0])[:, None] * 0.03 * gain
                        * np.exp(-dt[m] / 0.025) * np.sin(tau2pi * 60 * dt[m]))
        jolt = np.zeros(n)
        for f in self.jams:  # the belt stops dead: a hard jolt through the frame
            dt = t - f.start_s
            m = (dt >= 0) & (dt < 0.5)
            jolt[m] = 1.6 * np.exp(-dt[m] / 0.06) * np.sin(tau2pi * 18 * dt[m])
        for f in self.stalls:  # bogging down: a smaller shudder
            dt = t - f.start_s
            m = (dt >= 0) & (dt < 0.5)
            jolt[m] = 0.35 * np.exp(-dt[m] / 0.08) * np.sin(tau2pi * 14 * dt[m])
        a += np.array([1.0, 0.7, 0.4])[:, None] * jolt[None]
        a += self.accel_noise_g * self._slice(self.b_snoise, n)
        a += np.array([0.02, -0.03, 1.04])[:, None]
        if self.realistic:
            a = np.clip(a, -8.0, 8.0)  # the MPU's +-8 g range
        yield Packet("accel", t, a.T.astype(np.float32))

        g = (0.05 + (0.3 * fx["looseness"] if self.realistic else 0.0)) * s2 * np.vstack([sin(cm, 0.3), sin(cm, 1.9), sin(2 * cm, 0.8)])
        g += 0.11 * self._slice(self.b_snoise, n) + np.array([1.5, -2.0, 0.8])[:, None]
        g += np.array([25.0, 40.0, 10.0])[:, None] * np.abs(jolt)[None]
        yield Packet("gyro", t, g.T.astype(np.float32))
        if self.realistic:  # the IMU's temperature sensor: raw / 340 + 36.53, so steps of 1/340 C
            v = self.ambient[i] + self.t_imu[i] + self.nrng.normal(0, 0.02)
            yield Packet("imu_temp", np.array([s + 0.5]), np.array([[round((v - 36.53) * 340) / 340 + 36.53]], np.float32))

        # ---- sound
        j0, j1 = math.ceil(s * AUDIO_HZ), math.ceil((s + 1) * AUDIO_HZ)
        ta = np.arange(j0, j1) / AUDIO_HZ
        m = len(ta)
        cm = self._interp(self.c_motor, ta)
        inter_c = cm / GEAR1
        R = _rms_counts
        sq2 = math.sqrt(2)
        hum = 0.6 + 0.4 * min(cur, 3) if cur > 0 else 0.0
        x = np.zeros(m)
        if cur > 0:
            eh = 10 ** (12 * self.fx["electrical"][i] / 20) if self.realistic else 1.0  # a supply fault: louder hum
            x += R(66) * sq2 * hum * eh * np.sin(tau2pi * 120 * ta) + R(60) * sq2 * hum * eh * np.sin(tau2pi * 240 * ta + 1)
        if spd > 0.5:
            x += R(64) * sq2 * s2 * sin(10 * cm, 0.5)
            x += R(70) * sq2 * (0.6 + 0.4 * ld) * s2 * sin(TEETH1 * cm, 0.2)
            x += R(62) * sq2 * (0.6 + 0.4 * ld) * s2 * sin(2 * TEETH1 * cm, 1.4)
            x += R(64) * sq2 * s2 * sin(TEETH2 * inter_c, 2.2)
            x += R(72) * (0.4 + 0.6 * min(ld, 1.6)) * sc * self._slice(self.b_roll, m)
            x += R(68) * min(ld, 1.6) * self._slice(self.b_matsnd, m)
            if self.realistic:
                fs_ = {k: v[i] for k, v in self.fx.items()}
                if fs_["gear_wear"]:  # the mesh whine grows, with sidebands
                    e = fs_["gear_wear"]
                    x += R(58 + 14 * e) * sq2 * s2 * (sin(TEETH2 * inter_c, 0.9) + 0.6 * sin(TEETH2 * inter_c - inter_c)
                                                       + 0.6 * sin(TEETH2 * inter_c + inter_c, 1.7))
                if fs_["motor_bearing"]:  # rough hiss in pulses at the motor bearing's defect rate
                    e = fs_["motor_bearing"]
                    pm = (0.5 + 0.5 * np.cos(tau2pi * np.mod(3.58 * cm, 1.0))) ** 8
                    x += R(45 + 30 * e) * sc * self._slice(self.b_hiss, m) * (0.3 + 3.0 * pm)
                if fs_["lubrication_loss"]:
                    x += R(50 + 22 * fs_["lubrication_loss"]) * sc * self._slice(self.b_hiss, m)
                if fs_["looseness"]:  # a rattle once a turn
                    e = fs_["looseness"]
                    ph = np.mod(cm, 1.0)
                    x += R(60 + 14 * e) * 3 * (ph < 0.03) * self._slice(self.b_click, m)
                if fs_["idler_seized"]:  # the belt scraping over a stopped roll: a squeal near 1.6 kHz (MDPI 2021)
                    e = fs_["idler_seized"]
                    wob = 1580 + 25 * np.sin(tau2pi * 0.5 * ta)
                    x += R(66 + 18 * e) * sq2 * np.sin(tau2pi * np.cumsum(wob) / AUDIO_HZ)
                if fs_["belt_slip"] and ld > 0.5:  # stick-slip squeal near 2.5 kHz, in bursts under load
                    e = fs_["belt_slip"]
                    burst = (np.sin(tau2pi * 0.23 * ta) > 0.3) * (0.5 + 0.5 * np.sin(tau2pi * 7 * ta))
                    x += R(68 + 20 * e) * sq2 * burst * np.sin(tau2pi * 2480 * ta)
            if wear > 0:
                rot_a = self._interp(self.dist, ta) / (math.pi * IDLER_D[1])
                pulse = (0.5 + 0.5 * np.cos(tau2pi * np.mod(3.6 * rot_a, 1.0))) ** 8
                x += R(40 + 32 * wear) * sc * self._slice(self.b_hiss, m) * (0.3 + 3.0 * pulse)
                x += R(45 + 30 * wear) * sq2 * sc * np.sin(tau2pi * 3150 * ta)
            if self.rub[i]:
                lv = 84 - (12 * (1 - self.rub_level[i]) if self.realistic else 0)
                wob = 2800 + 30 * np.sin(tau2pi * 0.7 * ta)
                x += R(lv) * sq2 * np.sin(tau2pi * np.cumsum(wob) / AUDIO_HZ)
                x += R(lv - 14) * sq2 * np.sin(tau2pi * np.cumsum(2 * wob) / AUDIO_HZ)
        for f in self.stalls:  # bogging down: a short belt squeal
            dt = ta - f.start_s
            mm = (dt >= 0) & (dt < 4)
            if mm.any():
                x[mm] += R(76) * sq2 * np.sin(tau2pi * 1900 * ta[mm])
        for f in self.jams:
            dt = ta - f.start_s
            slip = (dt >= -3) & (dt < 0)  # the belt slips on the pulley while the motor strains
            if slip.any():
                x[slip] += R(82) * sq2 * np.sin(tau2pi * 1900 * ta[slip])
            thump = (dt >= 0) & (dt < 0.3)
            if thump.any():
                x[thump] += R(92) * 3 * np.exp(-dt[thump] / 0.05) * self._slice(self.b_click, int(thump.sum()))
        x += R(self.bg_db[i]) * self._slice(self.b_pink, m)
        for kt in knocks:
            dt = ta - kt
            mm = (dt >= 0) & (dt < 0.2)
            if mm.any():
                lvl = 74 + 14 * math.log10(gain) / math.log10(5) if gain > 1 else 74
                x[mm] += R(lvl) * 3 * np.exp(-dt[mm] / 0.03) * self._slice(self.b_click, int(mm.sum()))
        x += R(33) * self.nrng.standard_normal(m)
        yield Packet("audio", ta, np.clip(np.round(x), -32768, 32767).astype(np.int16))

        # ---- heat
        f0 = math.ceil((s - 0.11) * THERMAL_HZ)
        f1 = math.ceil((s + 1 - 0.11) * THERMAL_HZ)
        tf = np.arange(f0, f1) / THERMAL_HZ + 0.11
        amb = self.ambient[i]
        amb = amb + self.thermal_offset  # the camera's own absolute error (MLX90640: +-1.5-2 C)
        temps = {"motor": self.t_motor[i], "gear": self.t_gear[i], "pulley": self.t_pulley[i],
                 "idler0": self.t_idler[0][i], "idler1": self.t_idler[1][i], "idler2": self.t_idler[2][i],
                 "rub": self.t_rub[i], "belt": 1.5 * ld}
        frame = amb + self.static + sum(v * self.wb[k] for k, v in temps.items())
        frames = np.repeat(frame[None], len(tf), 0)
        for (ps, dur, direction) in self.people:
            if ps - 1 <= s <= ps + dur + 1:
                for q, tq in enumerate(tf):
                    frac = (tq - ps) / dur
                    if 0 <= frac < 1:
                        x0 = -5 + 37 * (frac if direction > 0 else 1 - frac)
                        cols = np.clip(np.minimum(np.arange(32) + 1, x0 + 5) - np.maximum(np.arange(32), x0), 0, 1)
                        cover = cols[None, :] * self.behind
                        raw = amb + self.static
                        frames[q] += self.blur(cover * (30.0 - raw))
        frames += self.fpn[None] + self.nrng.standard_normal(frames.shape) * self.noise_sigma[None]
        yield Packet("thermal", tf, frames.astype(np.float32))

        # ---- room
        if self.realistic:
            return  # the device has no room sensor
        tr = np.array([s + 0.37])
        room_c = amb + 0.8 + 0.03 * self.t_motor[i] + self.nrng.normal(0, 0.03)
        hum_pct = 45 - 1.6 * (amb - 21) + self.nrng.normal(0, 0.3)
        yield Packet("room", tr, np.array([[room_c, hum_pct]], np.float32))
