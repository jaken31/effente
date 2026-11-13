"""Is something wrong, and what: checks that need no setup, and checks against this machine's normal.

The answer is only whether something is wrong and what it is. There are no severity levels.

First, the sensor itself (sensor_problem): readings that no working accelerometer gives, so the data
can't be trusted. While it is on, the vibration checks and the model's diagnosis are set aside: they
would only be judging bad data. On 12 Nov corrupted samples (from a sensor buffer overflow) showed
0.1-2.2 g of "gravity" and 2 g of "vibration" on a device at rest, and the device said VIBRATION TOO HIGH:

  sensor_problem       gravity far from 1 g (outside 0.85-1.15 g) for an accelerometer that measures
                       gravity, like the MPU; under 0.1 mg of noise on every axis (no live sensor is
                       that quiet: stuck or disconnected); or an IMU temperature outside -40 to 125 C

Universal limits work from the moment the device is plugged in, in every mode, before anything has
been learned. Each is a reading that means trouble on practically any machine:

  vibration_over_limit vibration velocity over 11 mm/s (ISO 10816-like: past "unsatisfactory" for
                       most machine sizes; this device's band is 10-250 Hz)
  knocking             sustained impacts: impulsive vibration (kurtosis over 8; smooth running is
                       about 3) with peaks of 0.5 g or more
  too_hot              the IMU or the thermal camera over 100 degC
  heating_fast         rising more than 8 degC a minute
  repeated_jolts       5 or more sudden jolts in 10 minutes

Outside normal: once a model is trained, vibration, temperature and sound are also compared with
this machine's own normal (the highest, 99th percentile, while learning), so no one has to say
what kind of machine it is:

  vibration_above_normal, hotter_than_normal, louder_than_normal

Like is compared with like. Training learns the normal from the medians of its summary period (a
minute for a long recording), so each reading is judged on its median over the same period (the
baseline's "period_s"), the last period up to now. A forklift's reversing beeper, 83-94 dB for
9-18 s, passes the one-a-second level but not a minute's median (judged per second, the simulated
conveyor's held-out week gave 21 false LOUDER THAN NORMAL, every one a forklift; judged this way, none). A lasting change still shows: once it has
filled half the period and held (about 40 s for a minute). Nothing is judged until the period is
half covered. Baselines without "period_s" (models older than this, and recordings short enough to
learn from one-a-second rows) are judged a row at a time. The universal limits stay per row:
sudden trouble (knocking, jolts, over the limit) is theirs.

How far outside counts as wrong is OUTSIDE_NORMAL, in one place. These are starting values; how far
outside should count hasn't been decided. The learned model's line is there too ("model").
Sensitivity scales all of them at once and can be changed while the device runs (margins()). It
goes from 0 (least sensitive) to 1 (most), with 0.5 the default: the values below. Each 0.25 step
halves or doubles how far outside normal counts as wrong, so 1 flags a departure a quarter as big
and 0 needs one four times as big. It changes only the outside-normal checks and the model, never
the universal limits.

More impacts than normal (more_impacts, an addition of 14 Nov, system C of the conveyor search): the seconds in
the last SPIKE_WINDOW_S with an impulsiveness reading (SPIKE_FEATURES: 2-7 kHz sound, vibration kurtosis) over its
99.9th percentile while learning, against SPIKE_FACTOR x the most any such window had while learning. It catches
repeating knocks too short or too sparse for the minute model (on the simulated conveyor: splice damage, looseness).
Its levels are learned from the recording's one-a-second rows (learn_spike_levels) and kept in the baseline
("spikes"); a baseline without them has no such check. Sensitivity doesn't change it.

A check counts once it has held for most of HOLD_S (80 % of the rows) and clears once it has mostly
stopped (20 %), so a bump, a door slam or one odd reading never shows.

If the machine already went past a universal limit while learning, that limit is dropped for it:
it would otherwise show for ever. The outside-normal checks and the model watch that reading
instead, and training says so (learn_baseline's warnings).
"""

from __future__ import annotations

import math
from collections import deque

import numpy as np

from effente import fault_types as FT
from effente import features as F

HOLD_S = 10.0  # a check counts once it has held this long ...
ON_SHARE, OFF_SHARE = 0.8, 0.2  # ... for this share of the rows, and clears below this share

# Universal limits (no setup, any machine). Starting values.
VELOCITY_MM_S = 11.0
KURTOSIS = 8.0  # plain kurtosis; the feature is excess kurtosis (kurtosis minus 3)
KNOCK_PEAK_G = 0.5
HOT_C = 100.0
HEATING_C_PER_MIN = 8.0
JOLTS_10M = 5

# The sensor itself. Industrial vibration sensors (piezo, as in the NASA bearing data) remove gravity
# and read 0 g, so the gravity test is only for accelerometers that measure it (Safety(gravity_check=)).
GRAVITY_G = (0.85, 1.15)
FROZEN_G = 1e-4  # 0.1 mg rms: the MPU's own noise is 2-9 mg
IMU_TEMP_OK_C = (-40.0, 125.0)  # the IMU's rated range

# How far outside this machine's normal counts as wrong. Not decided yet: starting values.
OUTSIDE_NORMAL = {
    "vibration": 2.0,  # times its highest while learning (velocity, and overall level)
    "temperature": 15.0,  # degC above its highest while learning
    "sound": 6.0,  # dB above its loudest while learning
    "model": 1.0,  # the learned model's score, in units of its line (1.0: about one false alarm a week)
}
SENSITIVITY_RANGE = (0.0, 1.0)
DEFAULT_SENSITIVITY = 0.5  # OUTSIDE_NORMAL as written


def margins(sensitivity: float = DEFAULT_SENSITIVITY) -> dict[str, float]:
    """OUTSIDE_NORMAL at a sensitivity from 0 to 1: higher means a smaller departure from normal counts
    as wrong. 0.5 gives OUTSIDE_NORMAL itself; each 0.25 halves (up) or doubles (down) the departure."""
    s = float(min(max(sensitivity, SENSITIVITY_RANGE[0]), SENSITIVITY_RANGE[1]))
    k = 4.0 ** (2.0 * s - 1.0)  # 0 -> 1/4, 0.5 -> 1, 1 -> 4
    base = OUTSIDE_NORMAL
    return {"vibration": 1.0 + (base["vibration"] - 1.0) / k, "temperature": base["temperature"] / k,
            "sound": base["sound"] / k, "model": base["model"] / k}


# Vibration below these never counts as outside normal: the accelerometer's own noise is 4-9 mg.
VIB_FLOOR = {"vib_velocity_mm_s": 0.3, "vib_total_g": 0.015}
# The readings with a learned normal (baseline "levels"), judged on their median over the baseline's period.
LEVELS = ("vib_velocity_mm_s", "vib_total_g", "imu_temp_c", "th_max_c", "snd_level_db")
COVER_SHARE = 0.5  # judged once the values in the window span this share of the period

# More impacts than normal: seconds over the learned 99.9th percentile in the last 30 minutes, against 2x the most
# while learning (system C, simulated conveyor only: unverified on a real machine).
SPIKE_FEATURES = ("snd_kurt_hf", "vib_kurtosis")
SPIKE_Q, SPIKE_WINDOW_S, SPIKE_FACTOR = 99.9, 1800.0, 2.0

SENSOR = ("sensor_problem",)
UNIVERSAL = ("vibration_over_limit", "knocking", "too_hot", "heating_fast", "repeated_jolts")
RELATIVE = ("vibration_above_normal", "hotter_than_normal", "louder_than_normal")
RATES = ("more_impacts",)
CHECKS = SENSOR + UNIVERSAL + RELATIVE + RATES
# Old names of checks, as older saved models still list them in their baseline's "dropped".
# "vibration_too_high" is now only a model fault type (effente.fault_types).
RENAMED = {"vibration_too_high": "vibration_over_limit"}
# Judged from the accelerometer, so set aside while the sensor's readings make no sense.
FROM_VIBRATION = ("vibration_over_limit", "knocking", "repeated_jolts", "vibration_above_normal")

WHAT = {  # what each problem is, for people: (sentence, the screen's short form after "! ", with its value)
    "sensor_problem": ("the sensor's readings make no sense: check its wires and that it is fixed down; "
                       "the vibration checks and the model are paused meanwhile", "SENSOR PROBLEM", None),
    "vibration_over_limit": ("vibration over 11 mm/s, too high for practically any machine", "VIB OVER LIMIT", None),
    "knocking": ("knocking: repeated impacts in the vibration", "KNOCKING", None),
    "too_hot": ("over 100 C", "TOO HOT", None),
    "heating_fast": ("heating up more than 8 C a minute", "HEATING FAST", None),
    "repeated_jolts": ("5 or more sudden jolts in 10 minutes", "REPEATED JOLTS", None),
    "vibration_above_normal": ("vibrating well beyond its normal", "VIB OVER NORMAL", "VIB {v:.1f}x NORMAL"),
    "hotter_than_normal": ("hotter than its normal", "HOTTER THAN NORMAL", "{v:+.0f} C OVER NORMAL"),
    "louder_than_normal": ("louder than its normal", "LOUDER THAN NORMAL", "{v:+.0f} dB OVER NORMAL"),
    "more_impacts": ("more knocks or clicks than its normal", "MORE IMPACTS", None),
}

# What each check means for people, in the same words as the model's fault types (effente.fault_types
# SYMPTOM_TEXT), so the screen never says "vibration too high" twice.
SYMPTOM = {"sensor_problem": "sensor_problem", "vibration_over_limit": "vibration_too_high",
           "knocking": "knocking_or_jolts", "too_hot": "too_hot", "heating_fast": "heating_fast",
           "repeated_jolts": "knocking_or_jolts", "vibration_above_normal": "vibration_too_high",
           "hotter_than_normal": "too_hot", "louder_than_normal": "louder_than_normal",
           "more_impacts": "knocking_or_jolts"}

_NAN = float("nan")


def _f(x, name) -> float:
    v = x[F.INDEX[name]]
    return float(v) if np.isfinite(v) else _NAN


def vib_total_g(x) -> float:
    """Overall vibration level, all three axes, in g (rms)."""
    rms = [_f(x, f"vib_rms_{a}") for a in "xyz"]
    return math.sqrt(sum(10 ** (2 * r) for r in rms)) if all(math.isfinite(r) for r in rms) else _NAN


def readings(x) -> dict[str, float]:
    """The readings the checks use, from one feature row (NaN where a sensor is missing)."""
    peak = _f(x, "vib_peak_g")
    kurt = _f(x, "vib_kurtosis")
    return {"vib_velocity_mm_s": _f(x, "vib_velocity_mm_s"), "vib_total_g": vib_total_g(x),
            "kurtosis": kurt + 3.0, "peak_g": 10 ** peak if math.isfinite(peak) else _NAN,
            "imu_temp_c": _f(x, "imu_temp_c"), "th_max_c": _f(x, "th_max_c"),
            "snd_level_db": _f(x, "snd_level_db"), "jolts_10m": _f(x, "trend_shocks_10m"),
            "th_rise_per_min": _f(x, "trend_heat_rise_per_min"), "gravity_g": gravity_g(x),
            "rms_max_g": _max(*(10 ** v for v in (_f(x, f"vib_rms_{a}") for a in "xyz") if math.isfinite(v)))}


def gravity_g(x) -> float:
    """How much gravity the accelerometer sees, in g (about 1 for an MPU, whatever its tilt)."""
    g = [_f(x, f"vib_gravity_{a}") for a in "xyz"]
    return math.sqrt(sum(v * v for v in g)) if all(math.isfinite(v) for v in g) else _NAN


def _sensor(r: dict, gravity_check: bool = True) -> tuple[bool, float]:
    """(the readings make no sense, the gravity it sees)."""
    g, temp = r["gravity_g"], r["imu_temp_c"]
    bad_gravity = gravity_check and math.isfinite(g) and not GRAVITY_G[0] <= g <= GRAVITY_G[1]
    frozen = math.isfinite(r["rms_max_g"]) and r["rms_max_g"] < FROZEN_G
    bad_temp = math.isfinite(temp) and not IMU_TEMP_OK_C[0] <= temp <= IMU_TEMP_OK_C[1]
    return bool(bad_gravity or frozen or bad_temp), g


def _max(*v) -> float:
    v = [a for a in v if math.isfinite(a)]
    return max(v) if v else _NAN


def _universal(r: dict, imu_rise: float = _NAN) -> dict[str, tuple[bool, float]]:
    """Per universal check: (over its limit, the value that decides)."""
    hot = _max(r["imu_temp_c"], r["th_max_c"])
    rise = _max(imu_rise, r["th_rise_per_min"])
    return {"vibration_over_limit": (r["vib_velocity_mm_s"] > VELOCITY_MM_S, r["vib_velocity_mm_s"]),
            "knocking": (r["kurtosis"] > KURTOSIS and r["peak_g"] >= KNOCK_PEAK_G, r["kurtosis"]),
            "too_hot": (hot > HOT_C, hot),
            "heating_fast": (rise > HEATING_C_PER_MIN, rise),
            "repeated_jolts": (r["jolts_10m"] >= JOLTS_10M, r["jolts_10m"])}


def _relative(r: dict, levels: dict, m: dict) -> dict[str, tuple[bool, float]]:
    """Per outside-normal check: (outside normal, how far: times normal, or degrees or dB above it)."""
    out = {}
    k = m["vibration"]
    ratios, over = [], False
    for name in ("vib_velocity_mm_s", "vib_total_g"):
        lvl, v = levels.get(name), r[name]
        if lvl and lvl > 0 and math.isfinite(v):
            ratios.append(v / lvl)
            over |= v > max(k * lvl, VIB_FLOOR[name])
    if ratios:
        out["vibration_above_normal"] = (over, max(ratios))
    above = [r[n] - levels[n] for n in ("imu_temp_c", "th_max_c") if n in levels and math.isfinite(r[n])]
    if above:
        out["hotter_than_normal"] = (max(above) > m["temperature"], max(above))
    if "snd_level_db" in levels and math.isfinite(r["snd_level_db"]):
        d = r["snd_level_db"] - levels["snd_level_db"]
        out["louder_than_normal"] = (d > m["sound"], d)
    return out


class Safety:
    """Runs every check on every row and keeps which problems are on.

    update(t0, x) for each feature row (any rate); problems() is what is wrong now."""

    def __init__(self, baseline: dict | None = None, hold_s: float = HOLD_S,
                 sensitivity: float = DEFAULT_SENSITIVITY, gravity_check: bool = True):
        self.hold_s = hold_s
        self.gravity_check = gravity_check  # False for accelerometers that don't measure gravity (piezo)
        self.sensitivity, self.margins = sensitivity, margins(sensitivity)
        self.hist: dict[str, deque] = {n: deque() for n in CHECKS}  # (t, over) for the last hold_s
        self.active: dict[str, dict] = {}
        self.temps: deque = deque()  # (t, IMU temperature), for the rise per minute
        self.set_baseline(baseline)

    def set_sensitivity(self, sensitivity: float) -> None:
        self.sensitivity, self.margins = sensitivity, margins(sensitivity)

    def set_baseline(self, baseline: dict | None) -> None:
        """This machine's normal, from training (None while nothing has been learned). Pass a model's
        baseline through baseline_of(), so older models get their period."""
        self.levels = dict((baseline or {}).get("levels", {}))
        self.dropped = {RENAMED.get(n, n) for n in (baseline or {}).get("dropped", [])}
        p = (baseline or {}).get("period_s")
        self.period_s = float(p) if p and float(p) > 1.0 else None  # None: judged a row at a time
        self.recent: dict[str, deque] = {n: deque() for n in LEVELS}  # (t, value) over the last period
        self.spikes = (baseline or {}).get("spikes") or None
        self.spike_secs: dict[str, deque] = {n: deque() for n in SPIKE_FEATURES}  # whole seconds over the level
        for n in RELATIVE + RATES + tuple(self.dropped):
            self.hist[n].clear()
            self.active.pop(n, None)

    def _medians(self, t0: float, r: dict) -> dict:
        """r with each learned reading replaced by its median over the last period, (t0 - period, t0]
        (NaN until the values there span COVER_SHARE of it). Missing values are left out."""
        if self.period_s is None:
            return r
        out = dict(r)
        for name in LEVELS:
            if name not in self.levels:
                continue
            d, v = self.recent[name], r[name]
            if math.isfinite(v):
                d.append((t0, v))
            while d and t0 - d[0][0] >= self.period_s:
                d.popleft()
            if d and t0 - d[0][0] >= COVER_SHARE * self.period_s:
                s = sorted(x for _, x in d)
                n = len(s)
                out[name] = s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])
            else:
                out[name] = _NAN
        return out

    def _impacts(self, t0: float, x) -> tuple[bool, float]:
        """(more impacts than normal, the highest count over its limit). One count per whole second, so the row
        rate doesn't matter; running rows only (trend_running NaN counts as running)."""
        sec, over, ratio = math.floor(t0 + 1e-6), False, 0.0
        running = not (x[F.INDEX["trend_running"]] < 0.5)
        window = float(self.spikes.get("window_s", SPIKE_WINDOW_S))
        factor = float(self.spikes.get("factor", SPIKE_FACTOR))
        for name, lv in self.spikes.get("features", {}).items():
            d, v = self.spike_secs.setdefault(name, deque()), _f(x, name)
            if running and math.isfinite(v) and v > lv["level"] and (not d or d[-1] != sec):
                d.append(sec)
            while d and d[0] <= t0 - window:
                d.popleft()
            limit = factor * max(float(lv["count"]), 1.0)
            over |= len(d) > limit
            ratio = max(ratio, len(d) / limit)
        return over, ratio

    def _imu_rise(self, t0: float, temp: float) -> float:
        """IMU temperature rise over the last minute, in degC a minute (NaN for the first minute)."""
        if not math.isfinite(temp):
            return _NAN
        if not self.temps or t0 - self.temps[-1][0] >= 1.0:
            self.temps.append((t0, temp))
        while len(self.temps) > 1 and t0 - self.temps[1][0] >= 60.0:
            self.temps.popleft()
        t_old, old = self.temps[0]
        return (temp - old) * 60.0 / (t0 - t_old) if t0 - t_old >= 50.0 else _NAN

    def measure(self, t0: float, x) -> dict[str, tuple[bool, float]]:
        r = readings(x)
        m = {"sensor_problem": _sensor(r, self.gravity_check)}
        m.update({k: v for k, v in _universal(r, self._imu_rise(t0, r["imu_temp_c"])).items() if k not in self.dropped})
        if self.levels:
            m.update(_relative(self._medians(t0, r), self.levels, self.margins))
        if self.spikes:
            m["more_impacts"] = self._impacts(t0, x)
        return m

    def update(self, t0: float, x) -> tuple[list[dict], list[str]]:
        """Judge one row. Returns (problems that just started, names of problems that just cleared)."""
        started, cleared = [], []
        m = self.measure(t0, x)
        for name in self.active:  # a sensor that went missing clears its problem, in time
            m.setdefault(name, (False, math.nan))
        for name, (over, value) in m.items():  # sensor_problem first, so it can set the others aside
            if name in FROM_VIBRATION and "sensor_problem" in self.active:
                over = False  # nothing judged from bad data: counts as clear until the sensor is fine
            h = self.hist[name]
            h.append((t0, bool(over)))
            while h and t0 - h[0][0] > self.hold_s:
                h.popleft()
            share = sum(o for _, o in h) / len(h)
            covered = t0 - h[0][0] >= 0.9 * self.hold_s
            if name in self.active:
                if share <= OFF_SHARE:
                    del self.active[name]
                    cleared.append(name)
                elif math.isfinite(value):
                    self.active[name]["value"] = _round(value)
            elif covered and share >= ON_SHARE:
                self.active[name] = {"name": name, "symptom": SYMPTOM[name], "what": WHAT[name][0],
                                     "value": _round(value), "since": t0}
                started.append(self.active[name])
                if name == "sensor_problem":  # anything just judged from the bad data goes with it
                    for n in FROM_VIBRATION:
                        self.hist[n].clear()
                        if self.active.pop(n, None) is not None:
                            cleared.append(n)
        return started, cleared

    def problems(self) -> list[dict]:
        """What is wrong now, in the order of CHECKS (universal limits first)."""
        return [dict(self.active[n]) for n in CHECKS if n in self.active]


def _round(v: float):
    return round(float(v), 2) if math.isfinite(v) else None


def screen_text(problem: dict) -> str:
    """The screen's short form of a problem, at most 19 characters: what is going on, in plain words."""
    return FT.SYMPTOM_TEXT[SYMPTOM[problem["name"]]][0]


# ----------------------------------------------------------------- the baseline, when a model is trained

def learn_baseline(X, names, running=None, period_s=None) -> dict:
    """This machine's normal from its training rows, and what was odd about the learning.

    X: the rows the model learns from, in time order (running rows, current feature list).
    running: optional, for every recorded row whether the machine ran (to say how much it stood).
    period_s: the period X's rows summarise (their medians), so Safety judges each reading on its
    median over the same period; None or 1 for one-a-second rows.
    Returns {"levels", "dropped", "warnings", "warnings_screen", "period_s"}: levels are the highest (99th
    percentile) of each reading; dropped are universal limits the machine already went past while learning."""
    X = np.asarray(X, float)
    R = [readings(x) for x in X]
    col = lambda k: np.array([r[k] for r in R], float)
    levels, warnings, dropped, short = {}, [], [], []
    for k in LEVELS:
        v = col(k)
        v = v[np.isfinite(v)]
        if len(v) >= max(10, len(X) // 2):  # the sensor was there for most of the learning
            levels[k] = round(float(np.percentile(v, 99)), 4)
    # 1. Did the machine run?
    if running is not None and len(running) and np.mean(running) < 0.5:
        warnings.append(f"the machine was stopped for {100 * (1 - np.mean(running)):.0f} % of the learning")
        short.append("MOSTLY STOPPED")
    vel, tot = col("vib_velocity_mm_s"), col("vib_total_g")
    if np.isfinite(tot).any() and np.nanmedian(tot) < 0.012 and not np.nanmedian(vel) >= 0.2:
        warnings.append("hardly any vibration while learning: was the machine running?")
        short.append("HARDLY VIBRATED")
    # 2. Did it stay under the universal limits? A limit it was over for 5 % of the time is dropped.
    g = col("gravity_g")
    measures_gravity = np.isfinite(g).any() and np.nanmedian(g) > 0.5  # an MPU, not a piezo sensor
    bad = np.mean([_sensor(r, measures_gravity)[0] for r in R]) if R else 0.0
    if bad >= 0.05:
        warnings.append(f"the sensor's readings made no sense for {100 * bad:.0f} % of the learning (gravity "
                        f"off, or no signal): check its wires and that it is fixed down, then learn again")
        short.append("SENSOR PROBLEM")
    over = {n: np.mean([_universal(r)[n][0] for r in R]) for n in UNIVERSAL if n != "heating_fast"}
    for n, share in over.items():
        if share >= 0.05:
            dropped.append(n)
            warnings.append(f"{WHAT[n][0]} for {100 * share:.0f} % of the learning: if that is normal for this "
                            f"machine, fine; if not, fix it and learn again")
            short.append("NORMAL OVER LIMIT")
    # 3. Was it steady? The last fifth against the fifth before it (warming up, or still changing).
    if len(X) >= 50:
        a, b = slice(3 * len(X) // 5, 4 * len(X) // 5), slice(4 * len(X) // 5, None)
        temp = col("th_max_c") if "th_max_c" in levels else col("imu_temp_c")
        if np.isfinite(temp[a]).any() and np.isfinite(temp[b]).any():
            d = float(np.nanmedian(temp[b]) - np.nanmedian(temp[a]))
            if abs(d) > 3.0:
                warnings.append(f"temperature still changing at the end ({d:+.1f} C): learning longer gives a better normal")
                short.append("STILL WARMING UP" if d > 0 else "STILL COOLING")
        if np.isfinite(tot[a]).any() and np.isfinite(tot[b]).any():
            ratio = float(np.nanmedian(tot[b]) / max(np.nanmedian(tot[a]), 1e-6))
            if not 1 / 1.3 <= ratio <= 1.3:
                warnings.append(f"vibration still changing at the end ({ratio:.2f}x): learning longer gives a better normal")
                short.append("VIB STILL CHANGING")
    return {"levels": levels, "dropped": dropped, "warnings": warnings, "warnings_screen": short,
            "period_s": int(period_s) if period_s else 1,
            "note": "levels are the 99th percentile while learning, of medians over period_s; readings are "
                    "judged on their median over the same period; how far outside counts as wrong is "
                    "effente.safety.OUTSIDE_NORMAL (starting values)"}


def learn_spike_levels(t, values: dict, running) -> dict | None:
    """The more_impacts check's levels from one-a-second rows (t in seconds, values {feature: array}, running per
    row): per SPIKE_FEATURES reading its SPIKE_Q percentile while running, and the most seconds over it in any
    SPIKE_WINDOW_S. None if under an hour of running seconds (too few to say what is rare)."""
    t = np.asarray(t, float)
    run = np.asarray(running, bool)
    order = np.argsort(t, kind="stable")
    t, run = t[order], run[order]
    feats = {}
    for name in SPIKE_FEATURES:
        if name not in values:
            continue
        v = np.asarray(values[name], float)[order]
        ok = run & np.isfinite(v)
        if ok.sum() < 3600:
            continue
        level = float(np.percentile(v[ok], SPIKE_Q))
        e = ok & (v > level)
        cs = np.r_[0, np.cumsum(e)]
        count = int((cs[1:] - cs[np.searchsorted(t, t - SPIKE_WINDOW_S, "right")]).max())
        feats[name] = {"level": round(level, 4), "count": count}
    if not feats:
        return None
    return {"features": feats, "window_s": SPIKE_WINDOW_S, "factor": SPIKE_FACTOR,
            "note": f"seconds over the learned p{SPIKE_Q} in the last {SPIKE_WINDOW_S / 60:.0f} min, alert above "
                    f"{SPIKE_FACTOR:g}x the most while learning"}


def baseline_of(model: dict | None) -> dict | None:
    """A model file's baseline, for Safety.set_baseline. Models saved before the baseline kept its
    period get the model's own summary period ("period_s"), which their levels were learned over."""
    base = (model or {}).get("baseline")
    if not base:
        return None
    base = dict(base)
    if "period_s" not in base and (model or {}).get("period_s"):
        base["period_s"] = int(model["period_s"])
    return base


def verdict(model_state: dict | None, problems: list[dict]) -> dict:
    """The one answer: is something wrong, and what is going on, in plain words (symptoms, effente.
    fault_types.SYMPTOM_TEXT), not a guess at the cause. The model's comes first, then the no-setup
    checks, each said once; "details" keeps the specific names (fault type and checks) for the data.
    While the sensor's readings make no sense, the model is left out: it would be judging bad data."""
    diag = (model_state or {}).get("diagnosis")
    bad_data = any(p["name"] == "sensor_problem" for p in problems)
    model_wrong = diag not in (None, "normal", "warming_up", "stopped") and not bad_data
    details = ([diag] if model_wrong else []) + [p["name"] for p in problems]
    # the model's description straight from what moved, when it gives one; else from its fault type
    said = list((model_state or {}).get("symptoms") or []) if model_wrong else []
    if model_wrong and not said:
        said = [FT.symptom_of(diag) or "outside_normal"]
    said += [SYMPTOM[p["name"]] for p in problems]
    what = []
    for sym in said:
        if sym not in what:
            what.append(sym)
    covered = {c for s in what for c in FT.COVERS.get(s, ())}
    what = [s for s in what if s not in covered]
    if "sensor_problem" in what:
        what = ["sensor_problem"] + [s for s in what if s != "sensor_problem"]
    return {"wrong": bool(what), "what": what, "details": details}


def say(symptoms: list[str]) -> list[str]:
    """The screen's lines for a verdict's "what"."""
    return [FT.SYMPTOM_TEXT[s][0] for s in symptoms]
