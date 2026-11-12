#!/usr/bin/env python3
"""Generate one day of simulated *normal* sensor data for effente.

The machine is a factory cooling-water pump: a 7.5 kW, 4-pole AC induction
motor on 60 Hz mains, driving a centrifugal pump with a 6-vane impeller
(about 1,770 RPM). It runs on a shift schedule, so the day contains every
normal state a learn-normal model has to accept: off, start-up, coast-down,
light and heavy load, breaks, a room that warms and cools, and harmless
background events (compressor, forklifts, clangs, voices, people walking
past the thermal camera, a technician checking the pump).

Sensors, matching the team's device:
  MPU6050   accelerometer + gyroscope, 1 kHz, raw int16 counts
  INMP441   I2S microphone, 16 kHz, int16
  MLX90640  thermal camera (55 deg lens), 32x24 pixels, 4 frames/s, deg C

Output (in --out):
  hour_00.npz ... hour_23.npz   raw sensor data, TRD recording format
  truth.csv                     1 Hz ground truth (state, load, speed, temperatures, events)
  events.csv                    every background event with its time
  meta.json                     machine, sensor and thermal-view description

Usage:
  python3 -m effente.simulate_day --out fake-dataset/normal-day-01
  python3 -m effente.simulate_day --out /tmp/effente-clip --start 05:44 --minutes 3
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import time
from multiprocessing import get_context
from pathlib import Path

import numpy as np
from scipy import ndimage, signal

GENERATOR_VERSION = "1.0"
DAY_S = 86_400
CTRL_HZ = 10  # resolution of the slow "control" timeline (speed, load, temperatures)
DATE = "2026-11-13"  # simulated local factory date

# ----------------------------------------------------------------- machine
MAINS_HZ = 60.0
POLE_PAIRS = 2
SLIP_NO_LOAD = 0.008
SLIP_PER_LOAD = 0.012
IMPELLER_VANES = 6
FAN_BLADES = 10
ROTOR_BARS = 28
NOMINAL_SPEED_HZ = MAINS_HZ / POLE_PAIRS * (1 - SLIP_NO_LOAD - SLIP_PER_LOAD * 0.85)
START_S = 1.6  # direct-on-line start: time to reach speed
COAST_TAU_S = 2.5  # water brakes the pump quickly when power is cut
COMPRESSOR_HZ = 24.6  # nearby air compressor, felt through the floor

# ----------------------------------------------------------------- sensors
ACCEL_NOMINAL_HZ = 1000
ACCEL_TRUE_HZ = 1000 * (1 + 0.0021)  # the MPU6050's own oscillator runs 0.21 % fast
ACCEL_LSB_PER_G = 16384  # +-2 g range
GYRO_LSB_PER_DPS = 131.0  # +-250 deg/s range
DLPF_HZ = 260  # MPU6050 digital low-pass setting 0

AUDIO_NOMINAL_HZ = 16000
AUDIO_TRUE_HZ = 16000 * (1 - 0.00004)  # I2S clock from the microcontroller's PLL
AUDIO_BLOCK = 256
AUDIO_GAIN_DB = 12.0  # firmware: 24-bit sample -> int16 with +12 dB gain
INMP441_DBFS_AT_94DB = -26.0
MIC_HIGHPASS_HZ = 60  # INMP441 low-frequency roll-off

THERMAL_NOMINAL_HZ = 4
THERMAL_TRUE_HZ = 4 * (1 - 0.0045)
THERMAL_PHASE_S = 0.11
THERMAL_SHAPE = (24, 32)

WARMUP_S = 5.0  # extra signal generated before each hour so filters are settled

# Vibration amplitudes at nominal speed, per axis (x radial horizontal,
# y axial, z radial vertical), in g. Peak for tones, rms for noise.
A_1X = np.array([0.030, 0.012, 0.018])  # residual imbalance
A_2X = np.array([0.008, 0.012, 0.006])  # residual misalignment
A_3X = np.array([0.003, 0.003, 0.002])
A_VANE = np.array([0.010, 0.006, 0.012])  # 6x vane pass
A_LINE = np.array([0.006, 0.003, 0.004])  # 120 Hz magnetic force, scales with current
A_COMP = np.array([0.0015, 0.0006, 0.0010])  # compressor through the floor
A_FLOW = np.array([0.010, 0.008, 0.009])  # flow turbulence, rms
A_RES = np.array([0.004, 0.002, 0.005])  # base-frame resonance at 95 Hz, rms
ACCEL_NOISE_G = 0.0085  # white noise before the low-pass filter
GRAVITY = np.array([0.031, -0.018, 0.99935])  # device sits on top of the motor, slightly tilted
ACCEL_OFFSET_G = np.array([0.021, -0.035, 0.048])  # zero-g offsets, typical of the MPU6050
ACCEL_DRIFT_G_PER_C = np.array([0.00025, -0.00020, 0.00040])
GYRO_BIAS_DPS = np.array([1.8, -2.4, 0.9])
GYRO_DRIFT_DPS_PER_C = np.array([0.020, -0.015, 0.030])
GYRO_ROCK_DPS = np.array([0.03, 0.05, 0.02])
GYRO_NOISE_DPS = 0.15

THERMAL_LABELS = [
    "background", "floor", "cabinet", "fan_cover", "motor",
    "bearing_end", "coupling", "pump", "pipe", "base",
]
STATE_NAMES = ["off", "starting", "running", "stopping"]


# ======================================================================
# helpers
# ======================================================================

def hms(s: float) -> str:
    s = int(s)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def parse_hhmm(text: str) -> float:
    parts = [int(p) for p in text.split(":")]
    while len(parts) < 3:
        parts.append(0)
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def lowpass_noise(rng, n: int, tau_steps: float) -> np.ndarray:
    """Unit-variance first-order low-pass noise, started in its steady state."""
    a = math.exp(-1.0 / tau_steps)
    w = rng.standard_normal(n) * math.sqrt(1 - a * a)
    y, _ = signal.lfilter([1.0], [1.0, -a], w, zi=[a * rng.standard_normal()])
    return y


def lag_const(target: np.ndarray, tau_s: float, y0: float) -> np.ndarray:
    a = math.exp(-1.0 / (CTRL_HZ * tau_s))
    y, _ = signal.lfilter([1 - a], [1.0, -a], target, zi=[a * y0])
    return y


def lag_var(target: np.ndarray, tau_s: np.ndarray, passes: int = 2) -> np.ndarray:
    """First-order lag with a time-varying time constant.

    Run more than once so the start of the day continues from the end of
    the previous (identical) day.
    """
    alpha = np.exp(-1.0 / (CTRL_HZ * tau_s)).tolist()
    tl = target.tolist()
    y = tl[0]
    out = [0.0] * len(tl)
    for _ in range(passes):
        for k in range(len(tl)):
            y = tl[k] + (y - tl[k]) * alpha[k]
            out[k] = y
    return np.asarray(out)


def cumulative_cycles(freq_hz: np.ndarray) -> np.ndarray:
    dt = 1.0 / CTRL_HZ
    return np.concatenate([[0.0], np.cumsum((freq_hz[1:] + freq_hz[:-1]) * 0.5 * dt)])


def spl_rms_counts(level_db) -> np.ndarray:
    """Microphone rms in int16 counts for a sound level in dB SPL."""
    full_scale_rms = 32767 / math.sqrt(2)
    dbfs = np.asarray(level_db) - 94.0 + INMP441_DBFS_AT_94DB + AUDIO_GAIN_DB
    return full_scale_rms * 10.0 ** (dbfs / 20.0)


def tone(cycles: np.ndarray, phase: float) -> np.ndarray:
    return np.sin(2 * np.pi * np.mod(cycles, 1.0) + phase)


class Colored:
    """Stateful coloured-noise source with unit rms output."""

    def __init__(self, sos, rng, channels: int = 1):
        self.sos = sos
        self.rng = rng
        self.channels = channels
        probe = signal.sosfilt(sos, np.random.default_rng(12345).standard_normal(400_000))[50_000:]
        self.gain = 1.0 / probe.std()
        shape = (sos.shape[0], channels, 2) if channels > 1 else (sos.shape[0], 2)
        self.zi = np.zeros(shape)

    def next(self, n: int) -> np.ndarray:
        shape = (self.channels, n) if self.channels > 1 else (n,)
        w = self.rng.standard_normal(shape)
        y, self.zi = signal.sosfilt(self.sos, w, axis=-1, zi=self.zi)
        return y * self.gain


class Filter:
    """Stateful filter for zero-mean signals."""

    def __init__(self, sos, channels: int = 1):
        self.sos = sos
        shape = (sos.shape[0], channels, 2) if channels > 1 else (sos.shape[0], 2)
        self.zi = np.zeros(shape)

    def __call__(self, x: np.ndarray) -> np.ndarray:
        y, self.zi = signal.sosfilt(self.sos, x, axis=-1, zi=self.zi)
        return y


def unit_noise(rng, n: int, sos, channels: int = 1) -> np.ndarray:
    """Coloured noise for one event (filtered from rest, unit rms)."""
    shape = (channels, n) if channels > 1 else (n,)
    y = signal.sosfilt(sos, rng.standard_normal(shape), axis=-1)
    std = y.std()
    return y / std if std > 0 else y


# ======================================================================
# the day: schedule, slow physics, events
# ======================================================================

class Day:
    """Everything that changes slowly, on a 10 Hz timeline for the whole day."""

    def __init__(self, seed: int):
        self.seed = seed
        rng = np.random.default_rng([seed, 0])
        n = DAY_S * CTRL_HZ + 2
        tc = np.arange(n) / CTRL_HZ
        hours = tc / 3600.0
        self.tc = tc

        # ---- pump runs
        runs = []
        for h, m in [(0, 30), (2, 30), (4, 30), (23, 20)]:
            s = h * 3600 + m * 60 + rng.uniform(-120, 120)
            runs.append((s, s + 900 + rng.uniform(-60, 60), "night top-up"))
        prod = (5 * 3600 + 45 * 60 + rng.uniform(0, 60), 22 * 3600 + rng.uniform(0, 300), "production")
        runs.append(prod)
        runs.sort()
        self.runs = runs

        powered = np.zeros(n, bool)
        prod_mask = (tc >= prod[0]) & (tc < prod[1])
        for s, e, _ in runs:
            powered |= (tc >= s) & (tc < e)

        # ---- load (fraction of full flow)
        base = np.zeros(n)
        base[powered] = 0.50  # night top-ups
        lv = np.where(hours < 6, 0.60, np.where(hours < 14, 0.90, 0.75))
        lv = np.where((hours >= 12) & (hours < 12.5), 0.55, lv)
        lv = np.where((hours >= 18.5) & (hours < 19), 0.55, lv)
        base[prod_mask] = lv[prod_mask]
        steps = np.zeros(n)  # an operator trims the valve now and then
        t, level, self.valve_moves = prod[0] + rng.exponential(2400), 0.0, []
        while t < prod[1]:
            level = float(np.clip(level + rng.normal(0, 0.04), -0.08, 0.08))
            steps[tc >= t] = level
            self.valve_moves.append((t, level))
            t += rng.exponential(2400)
        target = base + steps * prod_mask + 0.015 * lowpass_noise(rng, n, 300 * CTRL_HZ) * powered
        target = np.clip(target, 0, 1) * powered
        self.load = np.clip(lag_const(target, 12.0, 0.0), 0, 1)

        # ---- speed and motor current
        self.mains_hz = MAINS_HZ + 0.012 * lowpass_noise(rng, n, 60 * CTRL_HZ)
        run_speed = self.mains_hz / POLE_PAIRS * (1 - SLIP_NO_LOAD - SLIP_PER_LOAD * self.load)
        i_run = 0.35 + 0.65 * self.load
        speed = np.zeros(n)
        current = np.zeros(n)
        state = np.zeros(n, np.int8)
        for s, e, _ in runs:
            m = (tc >= s) & (tc < e)
            x = np.clip((tc[m] - s) / START_S, 0, 1)
            speed[m] = run_speed[m] * (0.5 - 0.5 * np.cos(np.pi * x))
            current[m] = 6.0 * (1 - x) + i_run[m] * x
            state[m] = np.where(tc[m] - s < 2.0, 1, 2)
            mc = (tc >= e) & (tc < e + 15)
            v0 = run_speed[min(int(e * CTRL_HZ), n - 1)]
            sp = v0 * np.exp(-(tc[mc] - e) / COAST_TAU_S)
            sp[sp < 0.3] = 0.0
            speed[mc] = sp
            state[mc] = np.where(sp > 0, 3, 0)
        self.speed, self.current, self.state = speed, current, state
        self.powered = powered
        self.rot_cycles = cumulative_cycles(speed)
        self.mains_cycles = cumulative_cycles(self.mains_hz)

        # ---- factory around the pump
        shift = ((hours >= 6) & (hours < 22)).astype(float)
        self.shift = shift
        shift_heat = lag_const(1.5 * shift, 5400.0, 0.4)
        self.ambient = (21.0 + 3.0 * np.sin(2 * np.pi * (hours - 9) / 24) + shift_heat
                        + 0.25 * lowpass_noise(rng, n, 1200 * CTRL_HZ))
        bg = np.where(shift > 0, 66.0, 44.0)
        bg = np.where(((hours >= 12) & (hours < 12.5)) | ((hours >= 18.5) & (hours < 19)), 60.0, bg)
        self.background_db = lag_const(bg, 300.0, 44.0) + 1.5 * lowpass_noise(rng, n, 30 * CTRL_HZ)

        comp_on = np.zeros(n)
        self.compressor_runs = []
        t = rng.uniform(0, 1800)
        while t < DAY_S:
            if 6 <= t / 3600 < 22:
                on, off = rng.uniform(300, 420), rng.uniform(600, 1080)
            else:
                on, off = rng.uniform(150, 210), rng.uniform(3000, 4200)
            comp_on[(tc >= t) & (tc < t + on)] = 1.0
            self.compressor_runs.append((t, t + on))
            t += on + off
        self.comp_level = lag_const(comp_on, 1.5, 0.0)
        self.comp_cycles = cumulative_cycles(COMPRESSOR_HZ * self.comp_level)

        # ---- temperatures
        losses = np.where(current > 0, 0.25 + 0.75 * current ** 2, 0.0)
        self.motor_rise = lag_var(30.0 * losses, np.where(powered, 35 * 60.0, 70 * 60.0))
        friction = lag_var(3.0 * self.load * powered, np.full(n, 1500.0))
        self.bearing_rise = 0.85 * self.motor_rise + friction
        water = 16.0 + 0.8 * np.sin(2 * np.pi * (hours - 10) / 24)
        flowing = speed > 1.0
        casing_target = np.where(flowing, water, self.ambient)
        self.casing_c = lag_var(casing_target, np.where(flowing, 180.0, 3000.0))
        self.pipe_c = lag_var(casing_target, np.where(flowing, 120.0, 1800.0))
        self.device_c = self.ambient + 0.6 * self.motor_rise
        self.imb_wobble = lowpass_noise(rng, n, 120 * CTRL_HZ)

        self.events = make_events(rng)


def make_events(rng) -> list[dict]:
    events: list[dict] = []

    def add(kind, start, dur, **p):
        events.append(dict(id=len(events), type=kind, start=float(start), end=float(start + dur), **p))

    def times(rate_per_h, h0, h1, skip=()):
        out, t = [], h0 * 3600 + rng.exponential(3600 / rate_per_h)
        while t < h1 * 3600:
            if not any(a * 3600 <= t < b * 3600 for a, b in skip):
                out.append(t)
            t += rng.exponential(3600 / rate_per_h)
        return out

    for t in times(5, 6, 22, skip=[(12.0, 12.5)]):
        add("forklift", t, rng.uniform(8, 20), reversing=bool(rng.random() < 0.6),
            rumble_db=rng.uniform(72, 78), beep_db=rng.uniform(78, 84),
            beep_hz=rng.uniform(1000, 1150), vib_g=rng.uniform(0.004, 0.012))
    for t in times(4, 6, 22):
        add("clang", t, 1.5, level_db=rng.uniform(88, 96), base_hz=rng.uniform(900, 1400),
            vib_g=rng.uniform(0.002, 0.005), vib_hz=rng.uniform(140, 220))
    for t in times(0.3, 0, 6) + times(0.3, 22, 24):
        add("clang", t, 1.5, level_db=rng.uniform(78, 86), base_hz=rng.uniform(500, 900),
            vib_g=rng.uniform(0.001, 0.003), vib_hz=rng.uniform(140, 220))
    for t in times(3, 6, 22):
        add("voices", t, rng.uniform(8, 60), level_db=rng.uniform(58, 68))
    for t in times(4, 6, 22):
        add("person_passing", t, rng.uniform(2.5, 6), direction=int(rng.choice([-1, 1])),
            width_px=rng.uniform(4.5, 6.5), clothing_c=rng.uniform(28, 31))
    for h in (1 + 10 / 60, 3 + 40 / 60):  # night security rounds
        add("person_passing", h * 3600 + rng.uniform(-600, 600), rng.uniform(3, 6),
            direction=int(rng.choice([-1, 1])), width_px=rng.uniform(4.5, 6.5),
            clothing_c=rng.uniform(27, 30))
    for h in (10 + 5 / 60, 16 + 5 / 60):  # technician checks the pump
        t, d = h * 3600 + rng.uniform(-600, 600), rng.uniform(25, 45)
        add("technician_check", t, d, clothing_c=rng.uniform(29, 31))
        add("voices", t + 2, d - 4, level_db=rng.uniform(64, 70))
    events.sort(key=lambda e: e["start"])
    return events


# ======================================================================
# thermal view
# ======================================================================

def thermal_layout():
    lab = np.zeros(THERMAL_SHAPE, int)
    lab[19:, :] = 1  # floor
    lab[0:6, 0:7] = 2  # electrical cabinet on the wall behind
    lab[18:20, 3:31] = 9  # base plate
    lab[9:17, 4:7] = 3  # motor fan cover
    lab[8:18, 7:21] = 4  # motor body
    for r, c in [(8, 7), (8, 20), (17, 7), (17, 20)]:
        lab[r, c] = 0
    lab[9:17, 21:23] = 5  # drive-end bearing housing
    lab[10:16, 23:25] = 6  # coupling guard
    rr, cc = np.mgrid[0:24, 0:32]
    lab[((rr - 13) / 5.5) ** 2 + ((cc - 27.5) / 3.5) ** 2 <= 1] = 7  # pump casing
    lab[0:8, 27:30] = 8  # outlet pipe
    return lab


class ThermalScene:
    def __init__(self, seed: int):
        lab = thermal_layout()
        self.labels = lab
        is_ = {name: lab == i for i, name in enumerate(THERMAL_LABELS)}
        rows = np.arange(24)[:, None] * np.ones((1, 32))
        cols = np.ones((24, 1)) * np.arange(32)[None, :]

        motor_shape = 1 + np.interp(cols, [7, 20], [-0.2, 0.05]) + np.where(rows % 2 == 0, 0.03, -0.03)
        w_rise = (0.95 * motor_shape * is_["motor"] + 0.95 * 0.35 * is_["fan_cover"]
                  + 0.9 * 0.30 * is_["coupling"] + 0.9 * 0.12 * is_["base"])
        w_bear = 0.95 * is_["bearing_end"]
        w_casing = 0.90 * is_["pump"]
        w_pipe = 0.35 * is_["pipe"]  # bare steel: low emissivity, reads close to the room
        static = np.where(is_["background"], np.interp(rows, [0, 18], [0.6, 0.0]), 0.0)
        static = static - 0.8 * is_["floor"] + 6.0 * is_["cabinet"]
        static[1:3, 4:6] += 2.0  # cabinet vent

        self.raw = dict(rise=w_rise, bear=w_bear, casing=w_casing, pipe=w_pipe, static=static)
        blur = lambda a: ndimage.gaussian_filter(a.astype(float), 0.6, mode="nearest")
        self.blurred = {k: blur(v) for k, v in self.raw.items()}
        self.behind = is_["background"] | is_["floor"] | is_["cabinet"]
        r = np.hypot((rows - 11.5) / 11.5, (cols - 15.5) / 15.5) / math.sqrt(2)
        self.noise_sigma = 0.22 * (1 + 0.8 * r ** 2)
        self.fpn = np.random.default_rng([seed, 5]).normal(0, 0.15, THERMAL_SHAPE)
        self.blur = blur

    def base(self, which: str, amb, rise, bear, casing_d, pipe_d):
        w = self.blurred if which == "blurred" else self.raw
        return (amb[:, None, None] + rise[:, None, None] * w["rise"] + bear[:, None, None] * w["bear"]
                + casing_d[:, None, None] * w["casing"] + pipe_d[:, None, None] * w["pipe"]
                + w["static"][None])


def person_columns(x0: float, width: float) -> np.ndarray:
    c = np.arange(32)
    return np.clip(np.minimum(c + 1, x0 + width) - np.maximum(c, x0), 0, 1)


# ======================================================================
# per-hour rendering
# ======================================================================

_DAY: Day | None = None


def _init_worker(seed: int):
    global _DAY
    _DAY = Day(seed)


def ctrl_index(t: np.ndarray):
    u = t * CTRL_HZ
    i = np.floor(u).astype(np.int64)
    return i, u - i


def at(arr, i, f):
    return arr[i] * (1 - f) + arr[i + 1] * f


class HourRenderer:
    def __init__(self, day: Day, hour: int):
        self.d = day
        self.hour = hour
        seed = day.seed
        ph = np.random.default_rng([seed, 4])  # fixed phases: same in every hour
        self.ph = {k: ph.uniform(0, 2 * np.pi, 3) for k in
                   ["1x", "2x", "3x", "vane", "line", "comp", "comp2", "g1", "g2"]}
        self.aph = ph.uniform(0, 2 * np.pi, 40)

        self.rng_acc = np.random.default_rng([seed, 1, hour])
        self.rng_aud = np.random.default_rng([seed, 2, hour])
        self.rng_th = np.random.default_rng([seed, 3, hour])

        fa, fs = ACCEL_NOMINAL_HZ, AUDIO_NOMINAL_HZ
        self.dlpf_acc = Filter(signal.butter(2, DLPF_HZ, fs=fa, output="sos"), 3)
        self.dlpf_gyr = Filter(signal.butter(2, DLPF_HZ, fs=fa, output="sos"), 3)
        self.n_flow = Colored(signal.butter(2, [5, 400], "band", fs=fa, output="sos"), self.rng_acc, 3)
        self.n_res = Colored(signal.tf2sos(*signal.iirpeak(95, 15, fs=fa)), self.rng_acc, 3)

        pink_b = [0.049922035, -0.095993537, 0.050612699, -0.004408786]
        pink_a = [1, -2.494956002, 2.017265875, -0.522189400]
        pink = np.vstack([signal.tf2sos(pink_b, pink_a), signal.butter(2, 6000, fs=fs, output="sos")])
        self.n_bg = Colored(pink, self.rng_aud)
        self.n_fan = Colored(signal.butter(2, [400, 5000], "band", fs=fs, output="sos"), self.rng_aud)
        self.n_flowa = Colored(signal.butter(2, [60, 1500], "band", fs=fs, output="sos"), self.rng_aud)
        self.n_hiss = Colored(signal.butter(2, [4000, 7000], "band", fs=fs, output="sos"), self.rng_aud)
        self.n_hvac = Colored(signal.butter(2, [100, 1000], "band", fs=fs, output="sos"), self.rng_aud)
        self.n_comp = Colored(signal.butter(2, [100, 3000], "band", fs=fs, output="sos"), self.rng_aud)
        self.mic_hp = Filter(signal.butter(2, MIC_HIGHPASS_HZ, "high", fs=fs, output="sos"))

        self.sos_rumble_a = signal.butter(2, [8, 40], "band", fs=fa, output="sos")
        self.sos_rumble_s = signal.butter(2, [40, 400], "band", fs=fs, output="sos")
        self.sos_voice = signal.butter(2, [250, 3500], "band", fs=fs, output="sos")
        self.sos_syll = signal.butter(2, 4, fs=100, output="sos")
        self.event_cache: dict = {}
        self.scene = ThermalScene(seed)

    # ------------------------------------------------------------ events
    def _events_in(self, t0, t1, kinds, tail=0.0):
        return [e for e in self.d.events if e["type"] in kinds and e["start"] < t1 and e["end"] + tail > t0]

    def _event_noise(self, ev, sensor, fs, sos, idx, channels=1):
        key = (ev["id"], sensor)
        i_s = math.ceil(ev["start"] * fs)
        if key not in self.event_cache:
            n = max(1, math.ceil(ev["end"] * fs) - i_s)
            rng = np.random.default_rng([self.d.seed, {"acc": 101, "aud": 102}[sensor], ev["id"]])
            self.event_cache[key] = unit_noise(rng, n, sos, channels)
        arr = self.event_cache[key]
        k = idx - i_s
        ok = (k >= 0) & (k < arr.shape[-1])
        out = np.zeros((channels, len(idx)) if channels > 1 else len(idx))
        if channels > 1:
            out[:, ok] = arr[:, k[ok]]
        else:
            out[ok] = arr[k[ok]]
        return out

    def _syllables(self, ev, t):
        key = (ev["id"], "syll")
        if key not in self.event_cache:
            n = int((ev["end"] - ev["start"]) * 100) + 2
            rng = np.random.default_rng([self.d.seed, 300, ev["id"]])
            env = np.abs(signal.sosfilt(self.sos_syll, rng.standard_normal(n)))
            self.event_cache[key] = env / (np.sqrt(np.mean(env ** 2)) + 1e-12)
        env = self.event_cache[key]
        return np.interp((t - ev["start"]) * 100, np.arange(len(env)), env)

    def _drop_old_events(self, t0):
        for k in [k for k in self.event_cache if self.d.events_by_id[k[0]]["end"] + 2 < t0]:
            del self.event_cache[k]

    # ------------------------------------------------------------ IMU
    def imu_chunk(self, i0: int, i1: int):
        d, ph = self.d, self.ph
        idx = np.arange(i0, i1)
        t = idx / ACCEL_TRUE_HZ
        ci, cf = ctrl_index(t)
        spd, ld, cur = at(d.speed, ci, cf), at(d.load, ci, cf), at(d.current, ci, cf)
        rc, mc, cc = at(d.rot_cycles, ci, cf), at(d.mains_cycles, ci, cf), at(d.comp_cycles, ci, cf)
        cl, rise, dev = at(d.comp_level, ci, cf), at(d.motor_rise, ci, cf), at(d.device_c, ci, cf)
        s = spd / NOMINAL_SPEED_HZ
        s2 = s * s
        imb = 1 + 0.06 * rise / 30 + 0.02 * at(d.imb_wobble, ci, cf)
        n = len(t)

        acc = np.empty((3, n))
        for ax in range(3):
            acc[ax] = (A_1X[ax] * imb * s2 * tone(rc, ph["1x"][ax])
                       + A_2X[ax] * (0.8 + 0.3 * ld) * s2 * tone(2 * rc, ph["2x"][ax])
                       + A_3X[ax] * s2 * tone(3 * rc, ph["3x"][ax])
                       + A_VANE[ax] * (0.4 + 0.6 * ld) * s2 * tone(IMPELLER_VANES * rc, ph["vane"][ax])
                       + A_LINE[ax] * cur * tone(2 * mc, ph["line"][ax])
                       + A_COMP[ax] * cl * (tone(cc, ph["comp"][ax]) + 0.35 * tone(2 * cc, ph["comp2"][ax])))
        acc += A_FLOW[:, None] * ((0.3 + 0.7 * ld) * s)[None] * self.n_flow.next(n)
        acc += A_RES[:, None] * s[None] * self.n_res.next(n)
        weights = np.array([0.6, 0.5, 1.0])[:, None]
        for ev in self._events_in(t[0], t[-1], {"forklift"}):
            env = np.sin(np.pi * np.clip((t - ev["start"]) / (ev["end"] - ev["start"]), 0, 1)) ** 2
            acc += ev["vib_g"] * env[None] * self._event_noise(ev, "acc", ACCEL_TRUE_HZ, self.sos_rumble_a, idx, 3)
        for ev in self._events_in(t[0], t[-1], {"clang"}, tail=0.5):
            dt = t - ev["start"]
            m = (dt >= 0) & (dt < 0.4)
            hit = np.zeros(n)
            hit[m] = ev["vib_g"] * np.exp(-dt[m] / 0.03) * np.sin(2 * np.pi * ev["vib_hz"] * dt[m])
            acc += weights * hit[None]
        acc += self.rng_acc.standard_normal((3, n)) * ACCEL_NOISE_G
        acc = self.dlpf_acc(acc)
        acc += (GRAVITY + ACCEL_OFFSET_G)[:, None] + ACCEL_DRIFT_G_PER_C[:, None] * (dev - 25)[None]

        gyr = np.empty((3, n))
        for ax in range(3):
            gyr[ax] = (GYRO_ROCK_DPS[ax] * s2 * tone(rc, ph["g1"][ax])
                       + 0.3 * GYRO_ROCK_DPS[ax] * s2 * tone(2 * rc, ph["g2"][ax]))
        gyr += self.rng_acc.standard_normal((3, n)) * GYRO_NOISE_DPS
        gyr = self.dlpf_gyr(gyr)
        gyr += GYRO_BIAS_DPS[:, None] + GYRO_DRIFT_DPS_PER_C[:, None] * (dev - 25)[None]

        acc_counts = np.clip(np.round(acc.T * ACCEL_LSB_PER_G), -32768, 32767).astype(np.int16)
        gyr_counts = np.clip(np.round(gyr.T * GYRO_LSB_PER_DPS), -32768, 32767).astype(np.int16)
        t_us = np.round(t * 1e6).astype(np.int64)
        return acc_counts, gyr_counts, t_us

    # ------------------------------------------------------------ audio
    def audio_chunk(self, j0: int, j1: int):
        d, aph = self.d, self.aph
        idx = np.arange(j0 * AUDIO_BLOCK, j1 * AUDIO_BLOCK)
        t = idx / AUDIO_TRUE_HZ
        ci, cf = ctrl_index(t)
        spd, ld, cur = at(d.speed, ci, cf), at(d.load, ci, cf), at(d.current, ci, cf)
        rc, mc, cc = at(d.rot_cycles, ci, cf), at(d.mains_cycles, ci, cf), at(d.comp_cycles, ci, cf)
        cl, bg_db = at(d.comp_level, ci, cf), at(d.background_db, ci, cf)
        s = spd / NOMINAL_SPEED_HZ
        s2 = s * s
        fan = s ** 2.5
        n = len(t)
        R = spl_rms_counts
        sq2 = math.sqrt(2)

        hum = np.where(cur > 0, 0.6 + 0.4 * cur, 0.0)
        x = np.zeros(n)
        for k, (mult, L) in enumerate([(2, 74), (4, 70), (6, 66), (8, 62)]):
            x += R(L) * sq2 * hum * tone(mult * mc, aph[k])
        slot = ROTOR_BARS * rc
        x += R(66) * sq2 * (0.3 + 0.7 * ld) * hum * tone(slot, aph[5])
        x += R(62) * sq2 * hum * (tone(slot - 2 * mc, aph[6]) + tone(slot + 2 * mc, aph[7]))
        x += R(72) * sq2 * fan * tone(FAN_BLADES * rc, aph[8])
        x += R(64) * sq2 * fan * tone(2 * FAN_BLADES * rc, aph[9])
        x += R(80) * fan * self.n_fan.next(n)
        x += R(68) * sq2 * (0.4 + 0.6 * ld) * s2 * tone(IMPELLER_VANES * rc, aph[10])
        x += R(60) * sq2 * (0.4 + 0.6 * ld) * s2 * tone(2 * IMPELLER_VANES * rc, aph[11])
        x += R(72) * (0.3 + 0.7 * ld) * s * self.n_flowa.next(n)
        x += R(48) * s * self.n_hiss.next(n)
        x += R(62) * sq2 * s2 * tone(rc, aph[12]) + R(56) * sq2 * s2 * tone(2 * rc, aph[13])

        x += R(bg_db) * self.n_bg.next(n)
        x += R(48) * self.n_hvac.next(n) + R(50) * sq2 * tone(58.3 * t, aph[14])
        comp = np.zeros(n)
        for k in range(1, 9):
            comp += R(70 - 2.5 * k) * sq2 * tone(k * cc, aph[14 + k])
        x += cl * (comp + R(69) * self.n_comp.next(n))

        for ev in self._events_in(t[0], t[-1], {"forklift", "voices"}):
            frac = np.clip((t - ev["start"]) / (ev["end"] - ev["start"]), 0, 1)
            if ev["type"] == "forklift":
                env = np.sin(np.pi * frac) ** 2
                x += R(ev["rumble_db"]) * env * self._event_noise(ev, "aud", AUDIO_TRUE_HZ, self.sos_rumble_s, idx)
                if ev["reversing"]:
                    p = np.mod(t - ev["start"], 1.0)
                    gate = np.where(p < 0.5, np.clip(np.minimum(p, 0.5 - p) / 0.01, 0, 1), 0.0)
                    bt = 2 * np.pi * ev["beep_hz"] * (t - ev["start"])
                    x += R(ev["beep_db"]) * sq2 * env * gate * (np.sin(bt) + 0.3 * np.sin(3 * bt)) / 1.044
            else:
                fade = np.clip(np.minimum(t - ev["start"], ev["end"] - t) / 0.8, 0, 1)
                x += (R(ev["level_db"]) * fade * self._syllables(ev, t)
                      * self._event_noise(ev, "aud", AUDIO_TRUE_HZ, self.sos_voice, idx))
        for ev in self._events_in(t[0], t[-1], {"clang"}, tail=0.5):
            dt = t - ev["start"]
            m = (dt >= 0) & (dt < 1.5)
            if m.any():
                amp = R(ev["level_db"]) * sq2
                for ratio, rel, tau in [(1.0, 1.0, 0.35), (2.3, 0.6, 0.18), (3.9, 0.4, 0.09)]:
                    x[m] += amp * rel * np.exp(-dt[m] / tau) * np.sin(2 * np.pi * ev["base_hz"] * ratio * dt[m])

        x = self.mic_hp(x)
        x += R(33) * self.rng_aud.standard_normal(n)  # microphone self-noise
        samples = np.clip(np.round(x), -32768, 32767).astype(np.int16)
        block_t_us = np.round(np.arange(j0, j1) * AUDIO_BLOCK / AUDIO_TRUE_HZ * 1e6).astype(np.int64)
        return samples, block_t_us

    # ------------------------------------------------------------ thermal
    def thermal_chunk(self, k0: int, k1: int):
        d, sc = self.d, self.scene
        k = np.arange(k0, k1)
        t = k / THERMAL_TRUE_HZ + THERMAL_PHASE_S
        ci, cf = ctrl_index(t)
        amb = at(d.ambient, ci, cf)
        rise = at(d.motor_rise, ci, cf)
        bear = at(d.bearing_rise, ci, cf)
        cas = at(d.casing_c, ci, cf) - amb
        pip = at(d.pipe_c, ci, cf) - amb
        frames = sc.base("blurred", amb, rise, bear, cas, pip)

        for ev in self._events_in(t[0], t[-1], {"person_passing", "technician_check"}):
            fi = np.nonzero((t >= ev["start"]) & (t < ev["end"]))[0]
            if len(fi) == 0:
                continue
            raw = sc.base("raw", amb[fi], rise[fi], bear[fi], cas[fi], pip[fi])
            for j, f in enumerate(fi):
                frac = (t[f] - ev["start"]) / (ev["end"] - ev["start"])
                person = np.full(THERMAL_SHAPE, ev["clothing_c"])
                if ev["type"] == "person_passing":
                    w = ev["width_px"]
                    x0 = -w + (32 + w) * (frac if ev["direction"] > 0 else 1 - frac)
                    cover = person_columns(x0, w)[None, :] * sc.behind
                    person[0:4] = 33.5
                    person[16:] -= 1.0
                else:
                    step = float(np.clip(min(t[f] - ev["start"], ev["end"] - t[f]) / 2.0, 0, 1))
                    cover = np.zeros(THERMAL_SHAPE)
                    cover[3:, 1:9] = step
                    person[3:7, 3:7] = 34.0
                delta = cover * (person - raw[j])
                frames[f] += sc.blur(delta)

        frames += sc.fpn[None] + self.rng_th.standard_normal(frames.shape) * sc.noise_sigma[None]
        frames = np.round(frames, 2).astype(np.float32)
        t_us = np.round(t * 1e6).astype(np.int64)
        return frames, t_us


def render_hour(task):
    hour, t_from, t_to, out_dir = task
    d = _DAY
    if not hasattr(d, "events_by_id"):
        d.events_by_id = {e["id"]: e for e in d.events}
    r = HourRenderer(d, hour)
    started = time.time()
    lo, hi = max(t_from, hour * 3600.0), min(t_to, (hour + 1) * 3600.0)
    warm = max(0.0, lo - WARMUP_S)

    # IMU
    i_keep, i_end = math.ceil(lo * ACCEL_TRUE_HZ), math.ceil(hi * ACCEL_TRUE_HZ)
    i = math.ceil(warm * ACCEL_TRUE_HZ)
    acc_parts, gyr_parts, at_parts = [], [], []
    step = int(60 * ACCEL_TRUE_HZ)
    while i < i_end:
        j = min(i + step, i_end)
        a, g, tu = r.imu_chunk(i, j)
        keep = slice(max(0, i_keep - i), None)
        acc_parts.append(a[keep]); gyr_parts.append(g[keep]); at_parts.append(tu[keep])
        r._drop_old_events(i / ACCEL_TRUE_HZ)
        i = j

    # audio, whole blocks; a block belongs to the hour its first sample falls in
    b_keep = math.ceil(lo * AUDIO_TRUE_HZ / AUDIO_BLOCK)
    b_end = math.ceil(hi * AUDIO_TRUE_HZ / AUDIO_BLOCK)
    b = math.ceil(warm * AUDIO_TRUE_HZ / AUDIO_BLOCK)
    aud_parts, bt_parts = [], []
    bstep = int(60 * AUDIO_TRUE_HZ / AUDIO_BLOCK)
    while b < b_end:
        e = min(b + bstep, b_end)
        smp, bt = r.audio_chunk(b, e)
        skip_blocks = max(0, b_keep - b)
        aud_parts.append(smp[skip_blocks * AUDIO_BLOCK:]); bt_parts.append(bt[skip_blocks:])
        r._drop_old_events(b * AUDIO_BLOCK / AUDIO_TRUE_HZ)
        b = e

    # thermal
    k0 = math.ceil((lo - THERMAL_PHASE_S) * THERMAL_TRUE_HZ)
    k1 = math.ceil((hi - THERMAL_PHASE_S) * THERMAL_TRUE_HZ)
    th_parts, tt_parts = [], []
    k = k0
    while k < k1:
        e = min(k + 240, k1)
        fr, tu = r.thermal_chunk(k, e)
        th_parts.append(fr); tt_parts.append(tu)
        k = e

    full_hour = lo == hour * 3600.0 and hi == (hour + 1) * 3600.0
    name = f"hour_{hour:02d}.npz" if full_hour else f"clip_{hms(lo).replace(':', '')}.npz"
    path = Path(out_dir) / name
    np.savez(
        path,
        accel=np.concatenate(acc_parts), accel_t_us=np.concatenate(at_parts),
        accel_rate_hz=ACCEL_NOMINAL_HZ, accel_lsb_per_g=ACCEL_LSB_PER_G,
        gyro=np.concatenate(gyr_parts), gyro_lsb_per_dps=GYRO_LSB_PER_DPS,
        audio=np.concatenate(aud_parts), audio_t_us=np.concatenate(bt_parts),
        audio_block_len=AUDIO_BLOCK, audio_rate_hz=AUDIO_NOMINAL_HZ, audio_gain_db=AUDIO_GAIN_DB,
        thermal=np.concatenate(th_parts), thermal_t_us=np.concatenate(tt_parts),
        thermal_rate_hz=THERMAL_NOMINAL_HZ,
        label="normal", session_id="day01", mount_id="m1",
        rpm_nominal=round(NOMINAL_SPEED_HZ * 60, 1),
        recorded_at=f"{DATE}T{hms(lo)}", device_id="effente-sim",
        machine="cooling-water pump (simulated)",
    )
    return name, round(time.time() - started, 1), path.stat().st_size


# ======================================================================
# day-level outputs
# ======================================================================

def write_truth(day: Day, out: Path):
    secs = np.arange(DAY_S)
    k = secs * CTRL_HZ
    active = [[] for _ in range(DAY_S)]
    for e in day.events:
        for s in range(int(e["start"]), min(DAY_S, int(math.ceil(e["end"])))):
            active[s].append(e["type"])
    comp = day.comp_level[k] > 0.5
    with open(out / "truth.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["second", "time", "state", "load", "speed_hz", "rpm", "current_frac",
                    "ambient_c", "motor_surface_c", "bearing_c", "pump_casing_c",
                    "compressor_on", "background_db", "events"])
        for i, s in enumerate(secs):
            j = k[i]
            w.writerow([s, hms(s), STATE_NAMES[day.state[j]], f"{day.load[j]:.3f}",
                        f"{day.speed[j]:.3f}", f"{day.speed[j] * 60:.1f}", f"{day.current[j]:.3f}",
                        f"{day.ambient[j]:.2f}", f"{day.ambient[j] + day.motor_rise[j]:.2f}",
                        f"{day.ambient[j] + day.bearing_rise[j]:.2f}", f"{day.casing_c[j]:.2f}",
                        int(comp[i]), f"{day.background_db[j]:.1f}", ";".join(sorted(set(active[i])))])


def write_events(day: Day, out: Path):
    with open(out / "events.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "type", "start_s", "end_s", "start", "end", "details"])
        for s, e, kind in day.runs:
            w.writerow(["", "pump_" + kind.replace(" ", "_"), f"{s:.1f}", f"{e:.1f}", hms(s), hms(e), ""])
        for s, e in day.compressor_runs:
            w.writerow(["", "compressor_on", f"{s:.1f}", f"{e:.1f}", hms(s), hms(e), ""])
        for t, level in day.valve_moves:
            w.writerow(["", "valve_trim", f"{t:.1f}", f"{t:.1f}", hms(t), hms(t), json.dumps({"offset": round(level, 3)})])
        for ev in day.events:
            extra = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in ev.items()
                     if k not in ("id", "type", "start", "end")}
            w.writerow([ev["id"], ev["type"], f"{ev['start']:.1f}", f"{ev['end']:.1f}",
                        hms(ev["start"]), hms(ev["end"]), json.dumps(extra)])


def write_meta(day: Day, out: Path, seed: int):
    f1 = NOMINAL_SPEED_HZ
    meta = {
        "generator": f"effente.simulate_day {GENERATOR_VERSION}", "seed": seed, "date": DATE,
        "label": "normal",
        "machine": {
            "description": "Cooling-water pump: 7.5 kW 4-pole induction motor, 60 Hz mains, "
                           "centrifugal pump with a 6-vane impeller, direct-on-line start",
            "nominal_speed_hz": round(f1, 3), "nominal_rpm": round(f1 * 60, 1),
            "frequencies_hz_at_nominal": {
                "1x_rotation": round(f1, 2), "2x": round(2 * f1, 2), "3x": round(3 * f1, 2),
                "vane_pass_6x": round(6 * f1, 2), "line_2x_mains": 2 * MAINS_HZ,
                "fan_blade_pass_10x": round(10 * f1, 2), "rotor_slot_28x": round(28 * f1, 1),
                "compressor_floor": COMPRESSOR_HZ, "base_resonance": 95.0,
            },
            "schedule": [{"start": hms(s), "stop": hms(e), "kind": k} for s, e, k in day.runs],
        },
        "sensors": {
            "imu": {"part": "MPU6050", "range": "+-2 g, +-250 deg/s", "nominal_rate_hz": ACCEL_NOMINAL_HZ,
                    "true_rate_hz": round(ACCEL_TRUE_HZ, 3), "dlpf_hz": DLPF_HZ,
                    "accel_lsb_per_g": ACCEL_LSB_PER_G, "gyro_lsb_per_dps": GYRO_LSB_PER_DPS,
                    "axes": "x radial horizontal, y axial (along the shaft), z radial vertical (up)",
                    "mounting": "on top of the motor, slightly tilted"},
            "microphone": {"part": "INMP441", "nominal_rate_hz": AUDIO_NOMINAL_HZ,
                           "true_rate_hz": round(AUDIO_TRUE_HZ, 3), "block_len": AUDIO_BLOCK,
                           "sensitivity_dbfs_at_94_db_spl": INMP441_DBFS_AT_94DB,
                           "firmware_gain_db": AUDIO_GAIN_DB,
                           "db_spl_from_rms_counts": "20*log10(rms/23169.8) + 120 - gain_db"},
            "thermal": {"part": "MLX90640 (55 deg lens)", "shape": list(THERMAL_SHAPE),
                        "nominal_rate_hz": THERMAL_NOMINAL_HZ, "true_rate_hz": round(THERMAL_TRUE_HZ, 4),
                        "units": "deg C (apparent, emissivity not corrected)",
                        "labels": THERMAL_LABELS, "label_map": thermal_layout().tolist()},
        },
        "timestamps": "microcontroller clock, microseconds since 00:00:00 local; "
                      "sensor clocks differ slightly from nominal, so align by timestamp",
        "not_modelled": [
            "MLX90640 chess-pattern subpage artefacts", "I2C read gaps or dropped samples",
            "room humidity", "people standing in front of the machine other than the technician checks",
        ],
    }
    (out / "meta.json").write_text(json.dumps(meta, indent=1))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="output folder")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--start", default="00:00", help="start time HH:MM[:SS] (default 00:00)")
    ap.add_argument("--minutes", type=float, default=24 * 60, help="how much to render (default a full day)")
    ap.add_argument("--workers", type=int, default=max(1, min(6, (os.cpu_count() or 2) - 1)))
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    t_from = parse_hhmm(args.start)
    t_to = min(DAY_S, t_from + args.minutes * 60)

    t0 = time.time()
    day = Day(args.seed)
    write_truth(day, out)
    write_events(day, out)
    write_meta(day, out, args.seed)
    print(f"schedule, physics and events ready in {time.time() - t0:.1f} s "
          f"({len(day.events)} background events)", flush=True)

    hours = range(int(t_from // 3600), int(math.ceil(t_to / 3600)))
    tasks = [(h, t_from, t_to, str(out)) for h in hours]
    ctx = get_context("spawn")
    with ctx.Pool(min(args.workers, len(tasks)), initializer=_init_worker, initargs=(args.seed,)) as pool:
        total = 0
        for name, secs, size in pool.imap_unordered(render_hour, tasks):
            total += size
            print(f"  wrote {name}  {size / 1e6:7.1f} MB  in {secs:5.1f} s", flush=True)
    print(f"done: {len(tasks)} file(s), {total / 1e9:.2f} GB, {time.time() - t0:.0f} s total", flush=True)


if __name__ == "__main__":
    main()
