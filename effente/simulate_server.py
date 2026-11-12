"""Simulated server rack in a data-center room, streamed as live sensor packets. For testing only.

Everything here is fake. Levels are plausible, not measured on real equipment.

The equipment: a 2U server with six cooling fans (7 blades, 6,000-14,000 RPM,
following CPU load and room temperature), two power-supply fans, a 7,200 RPM
hard drive with seek clicks, and coil whine that follows load. The room's
cooling unit (CRAC) cycles every 15 minutes. The device is clipped to the rack:
the accelerometer feels the fans and the drive through the rack, the thermal
camera looks at the server's rear (six fan exhaust zones, the power supplies, a
power connector, cables) with neighbouring servers above and below, and the
mic hears the server and the room.

Days: busy 08:00-19:00, quiet at night, a backup job 02:00-03:00 (heavy disk).

Faults in the default scenario (three days, Monday to Wednesday):
  ups_alarm          Mon 10:00-10:05  a UPS in the room beeps (2 kHz, 0.5 s on, 0.5 s off)
  rack_bump          Mon 16:00:00     someone bumps the rack (a jolt and a thump)
  fan3_bearing       Tue 00:00 to Tue 18:00  fan 3's bearing wears: it slows, grinds and
                                     shakes, and its exhaust zone warms (a slow drift; the
                                     worn bearing is not replaced, so it stays worn)
  connector_heat     Tue 18:00 to Wed 24:00  a power connector slowly heats up (+18 degC)
  hdd_clicking       Wed 03:00-05:00  the hard drive's "click of death"
  fan5_failure       Wed 14:00        fan 5 stops (sudden); the others speed up to cover
  cooling_failure    Wed 20:00-23:00  the room's cooling fails: the room heats from 22 to
                                     31 degC over 3 hours and the fans run flat out

The two slow faults wear one after the other, not together, so the coverage
check can tell which feature changes belong to which.
"""

from __future__ import annotations

import math

import numpy as np
from scipy import ndimage, signal

from device.intake.source import Packet, SourceInfo
from effente.simulate_conveyor import (ACCEL_HZ, AUDIO_HZ, DAY, GAIN_DB, SHAPE, THERMAL_HZ, Fault,
                                       _cumulative, _lag, _lowpass_noise, _rms_counts)

N_FANS = 6
FAN_BLADES = 7
PSU_RPM = 5200
HDD_HZ = 120.0
FAN5_CELLS = (2 * 8 + 4, 2 * 8 + 5)  # thermal grid cells over fan 5's exhaust
CONNECTOR_CELLS = (3 * 8 + 7, 4 * 8 + 7)  # thermal grid cells over the power connector


def default_faults() -> list[Fault]:
    h = 3600
    return [
        Fault("ups_alarm", 10 * h, 10 * h + 300, "UPS in the room beeps, Monday 10:00-10:05"),
        Fault("rack_bump", 16 * h, 16 * h + 2, "someone bumps the rack, Monday 16:00"),
        Fault("fan3_bearing", DAY, DAY + 18 * h, "fan 3 bearing wears, Tuesday 00:00 to 18:00"),
        Fault("connector_heat", DAY + 18 * h, 3 * DAY, "power connector slowly heats, Tuesday 18:00 to Wednesday 24:00"),
        Fault("hdd_clicking", 2 * DAY + 3 * h, 2 * DAY + 5 * h, "hard drive clicks, Wednesday 03:00-05:00"),
        Fault("fan5_failure", 2 * DAY + 14 * h, 99 * DAY, "fan 5 stops, Wednesday 14:00"),
        Fault("cooling_failure", 2 * DAY + 20 * h, 2 * DAY + 23 * h, "room cooling fails, Wednesday 20:00-23:00"),
    ]


class ServerRackSim:
    sensors = ("accel", "gyro", "audio", "thermal", "room")

    def __init__(self, days: int = 3, seed: int = 2, faults: list[Fault] | None = None,
                 start_s: float = 0.0, end_s: float | None = None, week: int = 0):
        # `week` gives another week of the same machine and device: a different load schedule and
        # noise, the same camera (its fixed pattern stays). Week 0 is the original.
        self.days, self.seed, self.week = days, seed, week
        self.faults = default_faults() if faults is None else faults
        self.start_s = start_s
        self.end_s = min(end_s if end_s is not None else days * DAY, days * DAY)
        self.info = SourceInfo(audio_rate_hz=AUDIO_HZ, audio_gain_db=GAIN_DB, accel_lsb_per_g=4096,
                               accel_range_g=8.0, gyro_lsb_per_dps=131.0, audio_block_len=256, week_offset_s=0.0)
        self.rng = np.random.default_rng(self._key(0))
        self.f = {k.kind: k for k in self.faults}
        self._timeline()
        self._banks()
        self._scene()

    def _active(self, kind, t):
        f = self.f.get(kind)
        return f is not None and f.start_s <= t < f.end_s

    def _ramp(self, kind, t):
        f = self.f.get(kind)
        if f is None:
            return np.zeros_like(t)
        return np.clip((t - f.start_s) / max(1.0, f.end_s - f.start_s), 0, 1) * (t >= f.start_s)

    # ------------------------------------------------------------ slow physics
    def _timeline(self):
        rng = self.rng
        n = self.days * DAY + 2
        t = np.arange(n, dtype=float)
        hour = (t % DAY) / 3600
        busy = ((hour >= 8) & (hour < 19)).astype(float)
        backup = ((hour >= 2) & (hour < 3)).astype(float)
        self.load = np.clip(0.2 + 0.55 * signal.lfilter([1 - math.exp(-1 / 900)], [1, -math.exp(-1 / 900)], busy)
                            + 0.25 * backup + 0.08 * _lowpass_noise(rng, n, 300), 0.05, 1.0)
        self.disk = 0.1 + 0.9 * backup + 0.2 * busy  # disk activity

        # Room: the cooling unit cycles every 15 minutes; when it fails the room climbs to 31 degC.
        cycle = 0.8 * signal.sawtooth(2 * np.pi * t / 900)
        cf = self.f.get("cooling_failure")
        failing = np.zeros(n)
        if cf is not None:
            seg = (t >= cf.start_s)
            failing = np.where(seg, np.clip((t - cf.start_s) / (cf.end_s - cf.start_s), 0, 1), 0)
            after = t >= cf.end_s
            failing[after] = np.exp(-(t[after] - cf.end_s) / 1800)  # repaired: cools back down
        self.room = 22 + cycle * (1 - (failing > 0)) + 9 * failing + 0.1 * _lowpass_noise(rng, n, 600)
        self.humidity = 45 - 1.2 * (self.room - 22) + 0.5 * _lowpass_noise(rng, n, 3600)

        # Fans: the controller speeds them up with load and room temperature.
        duty = np.clip(0.3 + 0.55 * self.load + 0.06 * (self.room - 22), 0.25, 1.0)
        self.fan5_dead = t >= self.f["fan5_failure"].start_s if "fan5_failure" in self.f else np.zeros(n, bool)
        duty = np.where(self.fan5_dead, np.minimum(1.0, duty + 0.35), duty)  # the others cover for fan 5
        self.duty = signal.lfilter([1 - math.exp(-1 / 8)], [1, -math.exp(-1 / 8)], duty)
        self.wear3 = self._ramp("fan3_bearing", t)
        spread = np.array([1.0, 0.985, 1.012, 0.995, 1.008, 0.99])
        self.fan_hz = (6000 + 8000 * self.duty)[None, :] / 60 * spread[:, None]
        self.fan_hz[2] *= 1 - 0.08 * self.wear3  # the worn fan slows
        dead = self.fan5_dead
        stop = np.exp(-np.maximum(0, t - (self.f["fan5_failure"].start_s if "fan5_failure" in self.f else n)) / 3)
        self.fan_hz[4] = np.where(dead, self.fan_hz[4] * stop, self.fan_hz[4])
        self.fan_c = np.array([_cumulative(row) for row in self.fan_hz])

        # Heat (degrees above the room).
        cpu = 18 * self.load + 4 * np.maximum(0, self.room - 22) * 0.3
        self.zone = []
        for k in range(N_FANS):
            target = 6 + cpu * (1.15 - 0.4 * self.duty)
            if k == 2:
                target = target + 4 * self.wear3
            if k == 4:
                target = np.where(dead, 2.0, target)  # no airflow through this grille any more
            elif "fan5_failure" in self.f:
                target = target + np.where(dead, 3.0, 0.0)  # the rest of the server runs hotter
            self.zone.append(_lag(target, np.full(n, 120.0)))
        self.psu = _lag(6 + 4 * self.load, np.full(n, 600.0))
        self.connector = _lag(3 + 3 * self.load + 18 * self._ramp("connector_heat", t), np.full(n, 1800.0))
        self.neighbours = _lag(4 + 6 * busy, np.full(n, 900.0))

        self.clicks = []  # hard-drive "click of death": bursts of 3 clicks every 4 s
        if "hdd_clicking" in self.f:
            f = self.f["hdd_clicking"]
            for s in np.arange(f.start_s, f.end_s, 4.0):
                self.clicks += [s, s + 0.12, s + 0.24]
        self.clicks = np.array(self.clicks)

    # ------------------------------------------------------------ noise and scene
    def _banks(self):
        rng = np.random.default_rng(self._key(1))

        def bank(sos, n, ch=1):
            x = signal.sosfilt(sos, rng.standard_normal((ch, n) if ch > 1 else n), axis=-1)
            return x / x.std()

        fa, fs = 1000.0, 16000.0
        self.b_rack = bank(signal.tf2sos(*signal.iirpeak(35, 6, fs=fa)), 120_000, 3)
        self.b_snoise = bank(signal.butter(2, 260, fs=fa, output="sos"), 120_000, 3)
        self.b_fan = bank(signal.butter(2, [300, 6000], "band", fs=fs, output="sos"), 960_000)
        self.b_room = bank(np.vstack([signal.tf2sos([0.049922035, -0.095993537, 0.050612699, -0.004408786],
                                                    [1, -2.494956002, 2.017265875, -0.522189400]),
                                      signal.butter(2, 6000, fs=fs, output="sos")]), 960_000)
        self.b_grind = bank(signal.butter(2, [2000, 4000], "band", fs=fs, output="sos"), 960_000)
        self.b_click = bank(signal.butter(2, [800, 6000], "band", fs=fs, output="sos"), 960_000)
        self.nrng = np.random.default_rng(self._key(2))

    def _slice(self, bank, n):
        i = int(self.nrng.integers(0, bank.shape[-1] - n))
        return bank[..., i:i + n]

    def _scene(self):
        z = lambda: np.zeros(SHAPE)
        w = {}
        for k in range(N_FANS):
            a = z()
            a[8:13, 2 + 4 * k:5 + 4 * k] = 0.9
            w[f"zone{k}"] = a
        w["psu"] = z()
        w["psu"][8:15, 27:32] = 0.9
        w["connector"] = z()
        w["connector"][15:17, 28:30] = 0.9
        w["neighbours"] = z()
        w["neighbours"][0:5, :] = 0.9
        w["neighbours"][19:24, :] = 0.9
        static = z()
        static[14:19, 2:26] = 1.0  # cable bundle, slightly warm
        blur = lambda a: ndimage.gaussian_filter(a, 0.6, mode="nearest")
        self.wb = {k: blur(v) for k, v in w.items()}
        self.static = blur(static)
        rows = np.arange(24)[:, None] * np.ones((1, 32))
        r = np.hypot((rows - 11.5) / 11.5, (np.arange(32)[None, :] - 15.5) / 15.5) / math.sqrt(2)
        self.noise_sigma = 0.22 * (1 + 0.8 * r ** 2)
        self.fpn = np.random.default_rng([self.seed, 3]).normal(0, 0.15, SHAPE)

    # ------------------------------------------------------------ labels
    def _key(self, stream: int):
        return [self.seed, stream] if self.week == 0 else [self.seed, stream, self.week]

    def label_at(self, t: float) -> str:
        active = [f.kind for f in self.faults if f.start_s <= t < f.end_s
                  and (f.kind != "fan3_bearing" or self.wear3[int(t)] > 0.05)]
        return "+".join(active) if active else "normal"

    def truth_at(self, t: float) -> dict:
        i = int(t)
        return {"load": float(self.load[i]), "room_c": float(self.room[i]), "duty": float(self.duty[i]),
                "label": self.label_at(t)}

    # ------------------------------------------------------------ packets
    def packets(self):
        s = int(math.floor(self.start_s))
        while s < self.end_s:
            yield from self._second(s)
            s += 1

    @staticmethod
    def _interp(arr, t):
        i = np.floor(t).astype(int)
        f = t - i
        return arr[..., i] * (1 - f) + arr[..., i + 1] * f

    def _second(self, s: int):
        i = s
        tau = 2 * np.pi
        sin = lambda c, ph=0.0: np.sin(tau * np.mod(c, 1.0) + ph)
        duty, wear3 = self.duty[i], self.wear3[i]
        bump = self.f.get("rack_bump")

        # ---- vibration, through the rack
        k0, k1 = math.ceil(s * ACCEL_HZ), math.ceil((s + 1) * ACCEL_HZ)
        t = np.arange(k0, k1) / ACCEL_HZ
        n = len(t)
        fc = self._interp(self.fan_c, t)
        a = np.zeros((3, n))
        for k in range(N_FANS):
            alive = self.fan_hz[k, i] > 1
            if not alive:
                continue
            amp = 0.0015 * duty ** 2 * (1 + 3 * wear3 if k == 2 else 1)
            a[k % 3] += amp * sin(fc[k], k)
        a[1] += 0.001 * sin(PSU_RPM / 60 * t, 0.4)
        a[2] += 0.0008 * sin(HDD_HZ * t, 1.1)
        a += 0.001 * self._slice(self.b_rack, n)
        if bump is not None:
            dt = t - bump.start_s
            m = (dt >= 0) & (dt < 0.6)
            a[:, m] += np.array([0.6, 0.3, 0.2])[:, None] * np.exp(-dt[m] / 0.08) * np.sin(tau * 12 * dt[m])
        a += 0.0085 * self._slice(self.b_snoise, n) + np.array([-0.98, 0.05, 0.12])[:, None]  # on a vertical rail
        yield Packet("accel", t, a.T.astype(np.float32))
        g = 0.11 * self._slice(self.b_snoise, n) + np.array([0.9, -1.4, 0.6])[:, None]
        if bump is not None:
            dt = t - bump.start_s
            m = (dt >= 0) & (dt < 0.6)
            g[:, m] += np.array([8.0, 20.0, 5.0])[:, None] * np.exp(-dt[m] / 0.1)
        yield Packet("gyro", t, g.T.astype(np.float32))

        # ---- sound
        j0, j1 = math.ceil(s * AUDIO_HZ), math.ceil((s + 1) * AUDIO_HZ)
        ta = np.arange(j0, j1) / AUDIO_HZ
        m = len(ta)
        R, sq2 = _rms_counts, math.sqrt(2)
        fc = self._interp(self.fan_c, ta)
        x = np.zeros(m)
        alive = 0
        for k in range(N_FANS):
            if self.fan_hz[k, i] <= 1:
                continue
            alive += 1
            x += R(52) * sq2 * duty ** 2.5 * sin(FAN_BLADES * fc[k], k)
            if k == 2 and wear3 > 0:  # the worn fan: harmonics, and grinding that pulses once per turn
                x += R(40 + 15 * wear3) * sq2 * (sin(2 * fc[k], 0.3) + sin(3 * fc[k], 1.2))
                x += (R(30 + 28 * wear3) * self._slice(self.b_grind, m)
                      * (0.4 + 1.6 * (0.5 + 0.5 * np.cos(tau * np.mod(fc[k], 1.0))) ** 6))
        x += R(64) * (alive / N_FANS) * duty ** 2.5 * self._slice(self.b_fan, m)
        x += R(50) * sq2 * (sin(9 * PSU_RPM / 60 * ta) + 0.5 * sin(18 * PSU_RPM / 60 * ta, 0.7))
        x += R(40) * sq2 * np.sin(tau * HDD_HZ * ta)
        x += R(35 + 15 * self.load[i]) * sq2 * np.sin(tau * (6200 + 200 * self.load[i]) * ta)  # coil whine
        x += R(58) * self._slice(self.b_room, m)  # the room's air handling
        for c in self.nrng.uniform(s, s + 1, self.nrng.poisson(2 + 18 * self.disk[i])):  # ordinary seeks
            dt = ta - c
            mm = (dt >= 0) & (dt < 0.01)
            if mm.any():
                x[mm] += R(46) * 2 * np.exp(-dt[mm] / 0.002) * self._slice(self.b_click, int(mm.sum()))
        if len(self.clicks):
            for c in self.clicks[(self.clicks >= s) & (self.clicks < s + 1)]:
                dt = ta - c
                mm = (dt >= 0) & (dt < 0.05)
                x[mm] += R(70) * 3 * np.exp(-dt[mm] / 0.008) * self._slice(self.b_click, int(mm.sum()))
        if self._active("ups_alarm", s):
            on = np.mod(ta - self.f["ups_alarm"].start_s, 1.0) < 0.5
            x += on * R(72) * sq2 * np.sin(tau * 2000 * ta)
        if bump is not None and bump.start_s <= s < bump.start_s + 1:
            dt = ta - bump.start_s
            mm = (dt >= 0) & (dt < 0.4)
            x[mm] += R(88) * 3 * np.exp(-dt[mm] / 0.06) * self._slice(self.b_click, int(mm.sum()))
        x += R(33) * self.nrng.standard_normal(m)
        yield Packet("audio", ta, np.clip(np.round(x), -32768, 32767).astype(np.int16))

        # ---- heat, looking at the server's rear
        f0 = math.ceil((s - 0.11) * THERMAL_HZ)
        f1 = math.ceil((s + 1 - 0.11) * THERMAL_HZ)
        tf = np.arange(f0, f1) / THERMAL_HZ + 0.11
        room = self.room[i]
        frame = room + self.static + sum(self.zone[k][i] * self.wb[f"zone{k}"] for k in range(N_FANS))
        frame = frame + self.psu[i] * self.wb["psu"] + self.connector[i] * self.wb["connector"]
        frame = frame + self.neighbours[i] * self.wb["neighbours"]
        frames = np.repeat(frame[None], len(tf), 0)
        frames += self.fpn[None] + self.nrng.standard_normal(frames.shape) * self.noise_sigma[None]
        yield Packet("thermal", tf, frames.astype(np.float32))

        # ---- room sensor, on the rack door (it feels a little exhaust heat)
        room_c = room + 0.6 + 0.05 * float(np.mean([z[i] for z in self.zone])) + self.nrng.normal(0, 0.03)
        yield Packet("room", np.array([s + 0.37]),
                     np.array([[room_c, self.humidity[i] + self.nrng.normal(0, 0.3)]], np.float32))
