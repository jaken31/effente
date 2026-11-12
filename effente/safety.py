"""Is something wrong, and what: checks that need no setup, and checks against this machine's normal.

The answer is only whether something is wrong and what it is. There are no severity levels.

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

How far outside counts as wrong is OUTSIDE_NORMAL, in one place. These are starting values; how far
outside should count hasn't been decided. The learned model's line is there too ("model").
Sensitivity scales all of them at once and can be changed while the device runs (margins()). It
goes from 0 (least sensitive) to 1 (most), with 0.5 the default: the values below. Each 0.25 step
halves or doubles how far outside normal counts as wrong, so 1 flags a departure a quarter as big
and 0 needs one four times as big. It changes only the outside-normal checks and the model, never
the universal limits.

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

UNIVERSAL = ("vibration_over_limit", "knocking", "too_hot", "heating_fast", "repeated_jolts")
RELATIVE = ("vibration_above_normal", "hotter_than_normal", "louder_than_normal")
CHECKS = UNIVERSAL + RELATIVE
# Old names of checks, as older saved models still list them in their baseline's "dropped".
# "vibration_too_high" is now only a model fault type (effente.fault_types).
RENAMED = {"vibration_too_high": "vibration_over_limit"}

WHAT = {  # what each problem is, for people: (sentence, the screen's short form after "! ", with its value)
    "vibration_over_limit": ("vibration over 11 mm/s, too high for practically any machine", "VIB OVER LIMIT", None),
    "knocking": ("knocking: repeated impacts in the vibration", "KNOCKING", None),
    "too_hot": ("over 100 C", "TOO HOT", None),
    "heating_fast": ("heating up more than 8 C a minute", "HEATING FAST", None),
    "repeated_jolts": ("5 or more sudden jolts in 10 minutes", "REPEATED JOLTS", None),
    "vibration_above_normal": ("vibrating well beyond its normal", "VIB OVER NORMAL", "VIB {v:.1f}x NORMAL"),
    "hotter_than_normal": ("hotter than its normal", "HOTTER THAN NORMAL", "{v:+.0f} C OVER NORMAL"),
    "louder_than_normal": ("louder than its normal", "LOUDER THAN NORMAL", "{v:+.0f} dB OVER NORMAL"),
}

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
            "th_rise_per_min": _f(x, "trend_heat_rise_per_min")}


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
                 sensitivity: float = DEFAULT_SENSITIVITY):
        self.hold_s = hold_s
        self.sensitivity, self.margins = sensitivity, margins(sensitivity)
        self.hist: dict[str, deque] = {n: deque() for n in CHECKS}  # (t, over) for the last hold_s
        self.active: dict[str, dict] = {}
        self.temps: deque = deque()  # (t, IMU temperature), for the rise per minute
        self.set_baseline(baseline)

    def set_sensitivity(self, sensitivity: float) -> None:
        self.sensitivity, self.margins = sensitivity, margins(sensitivity)

    def set_baseline(self, baseline: dict | None) -> None:
        """This machine's normal, from training (None while nothing has been learned)."""
        self.levels = dict((baseline or {}).get("levels", {}))
        self.dropped = {RENAMED.get(n, n) for n in (baseline or {}).get("dropped", [])}
        for n in RELATIVE + tuple(self.dropped):
            self.hist[n].clear()
            self.active.pop(n, None)

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
        m = {k: v for k, v in _universal(r, self._imu_rise(t0, r["imu_temp_c"])).items() if k not in self.dropped}
        if self.levels:
            m.update(_relative(r, self.levels, self.margins))
        return m

    def update(self, t0: float, x) -> tuple[list[dict], list[str]]:
        """Judge one row. Returns (problems that just started, names of problems that just cleared)."""
        started, cleared = [], []
        m = self.measure(t0, x)
        for name in self.active:  # a sensor that went missing clears its problem, in time
            m.setdefault(name, (False, math.nan))
        for name, (over, value) in m.items():
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
                self.active[name] = {"name": name, "what": WHAT[name][0], "value": _round(value), "since": t0}
                started.append(self.active[name])
        return started, cleared

    def problems(self) -> list[dict]:
        """What is wrong now, in the order of CHECKS (universal limits first)."""
        return [dict(self.active[n]) for n in CHECKS if n in self.active]


def _round(v: float):
    return round(float(v), 2) if math.isfinite(v) else None


def screen_text(problem: dict) -> str:
    """The screen's short form of a problem, at most 19 characters (it follows "! ")."""
    _, short, with_value = WHAT[problem["name"]]
    v = problem.get("value")
    return (with_value.format(v=v) if with_value and v is not None else short)[:19]


# ----------------------------------------------------------------- the baseline, when a model is trained

def learn_baseline(X, names, running=None) -> dict:
    """This machine's normal from its training rows, and what was odd about the learning.

    X: the rows the model learns from, in time order (running rows, current feature list).
    running: optional, for every recorded row whether the machine ran (to say how much it stood).
    Returns {"levels", "dropped", "warnings", "warnings_screen"}: levels are the highest (99th percentile) of each
    reading; dropped are universal limits the machine already went past while learning."""
    X = np.asarray(X, float)
    R = [readings(x) for x in X]
    col = lambda k: np.array([r[k] for r in R], float)
    levels, warnings, dropped, short = {}, [], [], []
    for k in ("vib_velocity_mm_s", "vib_total_g", "imu_temp_c", "th_max_c", "snd_level_db"):
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
            "note": "levels are the 99th percentile while learning; how far outside counts as wrong is "
                    "effente.safety.OUTSIDE_NORMAL (starting values)"}


def verdict(model_state: dict | None, problems: list[dict]) -> dict:
    """The one answer: is something wrong, and what. The model's named fault type comes first (it says
    what kind of fault), then the no-setup checks."""
    diag = (model_state or {}).get("diagnosis")
    what = [diag] if diag not in (None, "normal", "warming_up", "stopped") else []
    what += [p["name"] for p in problems]
    return {"wrong": bool(what), "what": what}
