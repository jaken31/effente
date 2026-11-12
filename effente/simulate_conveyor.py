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

    def __init__(self, days: int = 7, seed: int = 1, faults: list[Fault] | None = None,
                 start_s: float = 0.0, end_s: float | None = None):
        self.days, self.seed = days, seed
        self.faults = default_faults() if faults is None else faults
        self.start_s = start_s
        self.end_s = min(end_s if end_s is not None else days * DAY, days * DAY)
        # Recommended settings: accelerometer +-8 g, gyroscope +-250 deg/s. Time counts from Monday 00:00.
        self.info = SourceInfo(audio_rate_hz=AUDIO_HZ, audio_gain_db=GAIN_DB, accel_lsb_per_g=4096,
                               accel_range_g=8.0, gyro_lsb_per_dps=131.0, audio_block_len=256, week_offset_s=0.0)
        self.rng = np.random.default_rng([seed, 0])
        self._timeline()
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
        self.jams = [f for f in self.faults if f.kind == "jam"]
        for f in self.jams:
            split = []
            for s, e in self.runs:
                if s < f.start_s < e:
                    split += [(s, f.start_s), (f.end_s, e)]
                else:
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
        load = np.clip(base + surge + 0.08 * _lowpass_noise(rng, n, 600), 0, 1) * powered
        self.load = signal.lfilter([1 - math.exp(-1 / 20)], [1, -math.exp(-1 / 20)], load)

        ramp = np.zeros(n)
        for s, e in self.runs:
            i0, i1 = int(s), int(e)
            ramp[i0:i1] = np.clip((t[i0:i1] - s) / 8.0, 0, 1)  # soft starter, 8 s
            tail = np.arange(i1, min(n, i1 + 15))
            ramp[tail] = np.exp(-(tail - e) / (0.3 if e in abrupt else 1.5))  # a jam stops it dead
        slip = 0.01 + 0.02 * self.load
        self.speed = MAINS_HZ / 2 * (1 - slip) * ramp  # motor shaft, Hz
        self.running = ramp > 0.05
        self.current = np.where(powered, np.where(ramp < 1, 3.0 * (1 - ramp) + ramp, 1.0) * (0.35 + 0.65 * self.load), 0)
        for f in self.jams:  # the motor strains against the jam for a few seconds before it trips
            i = int(f.start_s)
            self.current[max(0, i - 4):i] = 3.5
        self.belt_v = math.pi * PULLEY_D * self.speed / (GEAR1 * GEAR2)
        self.c_motor = _cumulative(self.speed)
        self.dist = _cumulative(self.belt_v)  # metres of belt travelled

        weekday_day = (wd < 5) & (hour >= 6) & (hour < 22)
        self.ambient = (20 + 3 * np.sin(2 * np.pi * (hour - 9) / 24) + 1.5 * signal.lfilter(
            [1 - math.exp(-1 / 5400)], [1, -math.exp(-1 / 5400)], weekday_day.astype(float))
            + 2.0 * _lowpass_noise(rng, n, DAY) + 0.2 * _lowpass_noise(rng, n, 1200))
        self.bg_db = np.where(weekday_day | ((wd == 5) & (hour >= 6) & (hour < 14)), 62.0, 45.0)

        self.wear = np.zeros(n)
        self.rub = np.zeros(n, bool)
        self.splice_gain = np.ones(n)
        for f in self.faults:
            i0, i1 = int(max(0, f.start_s)), int(min(n, f.end_s))
            if f.kind == "idler_bearing":
                self.wear = np.clip((t - f.start_s) / max(1, f.end_s - f.start_s), 0, 1) * (t >= f.start_s)
            elif f.kind == "belt_rub":
                self.rub[i0:i1] = True
            elif f.kind == "splice_damage":
                self.splice_gain[i0:i1] = 5.0
        run = self.running.astype(float)
        tau = lambda on, off: np.where(self.running, on, off)
        self.t_motor = _lag(26 * (0.25 + 0.75 * np.minimum(self.current, 1) ** 2) * run, tau(2400, 4800))
        self.t_gear = _lag(16 * (0.4 + 0.6 * self.load) * run, tau(5400, 10800))
        self.t_pulley = _lag(5 * (0.3 + 0.7 * self.load) * run, tau(1800, 3600))
        idler_base = 3 * (0.5 + 0.5 * self.load) * run
        self.t_idler = [_lag(idler_base, tau(1500, 3000)),
                        _lag(idler_base + 25 * self.wear * run, tau(1500, 3000)),
                        _lag(idler_base, tau(1500, 3000))]
        self.t_rub = _lag(18.0 * (self.rub & self.running), tau(240, 240))
        self.people = []
        k = rng.exponential(900)
        while k < n:
            if weekday_day[int(k)]:
                self.people.append((k, rng.uniform(3, 6), int(rng.choice([-1, 1]))))
            k += rng.exponential(900)

    # ------------------------------------------------------------ noise banks
    def _banks(self):
        rng = np.random.default_rng([self.seed, 1])

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
        self.nrng = np.random.default_rng([self.seed, 2])

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
        a[0] += 0.012 * s2 * sin(cm) + 0.004 * s2 * sin(2 * cm, 1.0)
        a[1] += 0.006 * s2 * sin(cm, 0.4)
        a[2] += 0.009 * s2 * sin(cm, 2.0)
        a += 0.003 * s2 * sin(inter_c, 0.7)[None]
        mesh = TEETH2 * inter_c
        a[0] += (0.007 * (0.5 + 0.5 * ld) * s2 * sin(mesh)
                 + 0.0015 * s2 * (sin(mesh - inter_c, 0.3) + sin(mesh + inter_c, 1.1)))
        a[2] += 0.004 * s2 * sin(out_c, 0.2)
        a[0] += 0.002 * s2 * sin(out_c, 1.3)
        for j, d in enumerate(IDLER_D):
            a[2] += 0.0012 * s2 * sin(dist / (math.pi * d), j)
        rot = dist / (math.pi * IDLER_D[1])
        if wear > 0:
            # A spall on the outer race: every rolling element hits it, so the bearing's
            # noise comes in pulses at the defect rate (BPFO, about 3.6 x the idler's speed).
            bpfo = 3.6 * rot
            pulse = (0.5 + 0.5 * np.cos(tau2pi * np.mod(bpfo, 1.0))) ** 8
            a[2] += 0.006 * wear * s2 * sin(bpfo) * (1 + 0.6 * sin(rot))
            a += 0.004 * wear * s2 * self._slice(self.b_brg, n) * (0.3 + 3.0 * pulse)[None]
        a += 0.005 * ld * sc * self._slice(self.b_mat, n)
        a += 0.002 * sc * self._slice(self.b_res, n)
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
        a += np.array([1.0, 0.7, 0.4])[:, None] * jolt[None]
        a += 0.0085 * self._slice(self.b_snoise, n)
        a += np.array([0.02, -0.03, 1.04])[:, None]
        yield Packet("accel", t, a.T.astype(np.float32))

        g = 0.05 * s2 * np.vstack([sin(cm, 0.3), sin(cm, 1.9), sin(2 * cm, 0.8)])
        g += 0.11 * self._slice(self.b_snoise, n) + np.array([1.5, -2.0, 0.8])[:, None]
        g += np.array([25.0, 40.0, 10.0])[:, None] * np.abs(jolt)[None]
        yield Packet("gyro", t, g.T.astype(np.float32))

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
            x += R(66) * sq2 * hum * np.sin(tau2pi * 120 * ta) + R(60) * sq2 * hum * np.sin(tau2pi * 240 * ta + 1)
        if spd > 0.5:
            x += R(64) * sq2 * s2 * sin(10 * cm, 0.5)
            x += R(70) * sq2 * (0.6 + 0.4 * ld) * s2 * sin(TEETH1 * cm, 0.2)
            x += R(62) * sq2 * (0.6 + 0.4 * ld) * s2 * sin(2 * TEETH1 * cm, 1.4)
            x += R(64) * sq2 * s2 * sin(TEETH2 * inter_c, 2.2)
            x += R(72) * (0.4 + 0.6 * ld) * sc * self._slice(self.b_roll, m)
            x += R(68) * ld * self._slice(self.b_matsnd, m)
            if wear > 0:
                rot_a = self._interp(self.dist, ta) / (math.pi * IDLER_D[1])
                pulse = (0.5 + 0.5 * np.cos(tau2pi * np.mod(3.6 * rot_a, 1.0))) ** 8
                x += R(40 + 32 * wear) * sc * self._slice(self.b_hiss, m) * (0.3 + 3.0 * pulse)
                x += R(45 + 30 * wear) * sq2 * sc * np.sin(tau2pi * 3150 * ta)
            if self.rub[i]:
                wob = 2800 + 30 * np.sin(tau2pi * 0.7 * ta)
                x += R(84) * sq2 * np.sin(tau2pi * np.cumsum(wob) / AUDIO_HZ)
                x += R(70) * sq2 * np.sin(tau2pi * np.cumsum(2 * wob) / AUDIO_HZ)
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
        tr = np.array([s + 0.37])
        room_c = amb + 0.8 + 0.03 * self.t_motor[i] + self.nrng.normal(0, 0.03)
        hum_pct = 45 - 1.6 * (amb - 21) + self.nrng.normal(0, 0.3)
        yield Packet("room", tr, np.array([[room_c, hum_pct]], np.float32))
