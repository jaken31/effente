"""Early warning: a small change that persists or creeps up before the model says something is wrong.

The model (effente.model_runtime.Watcher) judges each summary period with a few periods of memory, on
a score averaged over all readings. A change that is small, or in one group of readings only, can sit
under its line for hours while it grows. This watches the same model's output with a longer memory:

  series     each group of readings in effente.fault_types.GROUPS (the groups describe() names
             symptoms by; a group's level is the mean of its 3 most-moved readings in its direction,
             from Model.deviation_vector: against this machine's normal GIVEN the other readings, so a
             warm week or a busy shift is not a change), plus the model's own score (over its line)
  blocks     the median of each block of BLOCK running periods (an hour for a model with 1-minute
             periods, a minute for a short demo model with 1-second ones); stopped periods are skipped
  against    each series' block values while learning (calibrate(): median and robust spread), so
             a block value becomes "spreads above normal"
  detector   one of METHODS (the default "line": the score's block median over a lower line). Sensitivity
             (effente.safety's 0 to 1, 0.5 the default) scales the decision limit: 1 halves it and
             0 doubles it (scale())
  wording    "<SYMPTOM> CREEPING UP" when the block values are still rising (Mann-Kendall test with
             a Theil-Sen slope), else "<SYMPTOM> SLIGHTLY UP"; the screen's short form is
             "EARLY: <...>" (19 characters at most, after "! ")

It never counts as wrong. While the model's alarm is on it is not shown (it keeps watching); it clears
when the change goes away; a new model (relearning) starts a new EarlyWarning. No countdown: at most a
coarse speed ("rising over hours" or "over days"), never a predicted failure time.

The methods, compared in training/record/conveyor_eval/early_warning_eval.py (EARLY_WARNING.md):
  cusum        CUSUM per group, and on the score (the default until 14 Nov)
  ewma         an exponentially weighted mean per group, against a limit
  trend        a significant rising trend (Mann-Kendall) over the last TREND_N blocks, per group
  cusum_score  CUSUM on the model's score only
  line         the model's score over a lower line (LINE_FRAC of it) for LINE_HOLD blocks of LINE_BLOCK
               periods (the default since 14 Nov: system C of training/record/conveyor_eval/SYSTEM_SEARCH.md,
               30-minute blocks over 0.3x the crossfit line, held 6 blocks = 3 hours)
  sensitivity  the model's own alarm at a higher sensitivity (no blocks: "just turn it up")
Starting values, tuned on normal training weeks only (unverified on real machines). NumPy only.
"""

from __future__ import annotations

import math
from collections import deque

import numpy as np

from effente import fault_types as FT

BLOCK = 60  # summary periods per block
MIN_BLOCKS = 8  # fewer blocks while learning: the spreads are a guess, so be more cautious (CAUTIOUS)
CAUTIOUS = 1.5
SPREAD_FLOOR = 0.25  # in the deviations' own spreads; most group levels are often exactly 0 when normal
SCORE_SPREAD_FLOOR = 0.02  # the score's, in units of the model's line
Z_CLIP = 6.0
NAMING_Z = 2.0  # for the score-only methods: the group named is the one this far up, else outside_normal

# Detector settings at sensitivity 0.5. Tuned on the normal training weeks (EARLY_WARNING.md).
CUSUM_K, CUSUM_H = 0.5, 5.0  # allowance and decision limit, in spreads (per block)
CUSUM_CAP, CUSUM_OFF = 1.5, 0.5  # the sum stops at 1.5 h (so it can clear); off below 0.5 h
EWMA_LAMBDA, EWMA_L = 0.2, 3.0  # on above L asymptotic sigmas of the mean; off below half of that
TREND_N, TREND_Z, TREND_RISE = 8, 2.0, 1.5  # blocks; Mann-Kendall z; and total rise, in spreads
# The "line" method, system C (simulated conveyor only, unverified on a real machine): 30-period blocks, held 6 in a
# row, over 0.3x the crossfit line. A model trained with the crossfit line stores line x effente.trainer.THRESHOLD_FACTOR
# (4 ** -0.3) as its threshold, and the score here is over that threshold, so the fraction of it is 0.3 / 4 ** -0.3.
# Sensitivity scales the fraction as the other limits (scale(): 2x at 0, 1x at 0.5, 0.5x at 1). Was 0.6 and 3.
LINE_BLOCK = 30
LINE_FRAC, LINE_HOLD = 0.3 / 4.0 ** -0.3, 6  # of the model's stored threshold; blocks in a row
SENSITIVITY_FOR_ALARM = 1.0  # the "sensitivity" method: the alarm's own test at this sensitivity

RISING_N, RISING_Z = 12, 1.64  # the wording's slope test: last blocks, one-sided 5 %
METHODS = ("cusum", "ewma", "trend", "cusum_score", "line", "sensitivity")
DEFAULT_METHOD = "line"  # was "cusum": with an in-sample calibration it sat about +4 spreads up on any new week

GROUP_NAMES = tuple(g[0] for g in FT.GROUPS)
SERIES = GROUP_NAMES + ("score",)
SCORE = len(SERIES) - 1

# The screen's form after "EARLY: ", at most 12 characters: (slightly up, creeping up).
SHORT = {
    "vibration_too_high": ("VIB UP", "VIB RISING"),
    "unusual_vibration": ("VIB CHANGED", "VIB CHANGING"),
    "knocking_or_jolts": ("MORE KNOCKS", "KNOCKS GROW"),
    "speed_changed": ("SPEED OFF", "SPEED DRIFT"),
    "vibration_unsteady": ("UNSTEADY", "UNSTEADIER"),
    "louder_than_normal": ("LOUDER", "GETTING LOUD"),
    "new_tone": ("TONE UP", "TONE RISING"),
    "grinding_noise": ("GRINDING UP", "GRIND RISING"),
    "beeping": ("BEEPING", "BEEPING"),
    "too_hot": ("WARMER", "HEATING UP"),
    "heating_up": ("HEATING UP", "HEATING UP"),
    "heating_fast": ("HEATING", "HEATING"),
    "hot_spot": ("HOT SPOT", "SPOT HEATING"),
    "running_cold": ("COOLER AREA", "AREA COOLING"),
    "temperature_swings": ("TEMP SWINGS", "SWINGS GROW"),
    "room_too_hot_or_humid": ("ROOM WARMER", "ROOM HEATING"),
    "device_moved": ("TILT CHANGED", "TILTING"),
    "camera_moved": ("VIEW CHANGED", "VIEW DRIFT"),
    "outside_normal": ("OFF NORMAL", "DRIFTING OFF"),
}


def scale(sensitivity: float) -> float:
    """How far the decision limits are from their sensitivity-0.5 values: 2 at 0, 1 at 0.5, 0.5 at 1."""
    s = float(min(max(sensitivity, 0.0), 1.0))
    return 2.0 ** (1.0 - 2.0 * s)


# ----------------------------------------------------------------- per-period series values

class Series:
    """Turns the model's per-period output into one value per series (vectorised over rows)."""

    def __init__(self, model):
        self.m = model
        idx = {n: i for i, n in enumerate(model._dev_names)}
        self.groups = [([idx[n] for n in names if n in idx], direction) for _, names, direction in FT.GROUPS]

    def values(self, X) -> np.ndarray:
        """rows (current feature list) -> (rows, len(SERIES)): group levels in spreads, then score / line."""
        s, r, Z = self.m.score(np.atleast_2d(np.asarray(X, float)))
        return self.from_model(s, r, Z)

    def from_model(self, s, r, Z) -> np.ndarray:
        dev = self.m.deviation_vector(r, Z)
        out = np.full((dev.shape[0], len(SERIES)), np.nan)
        for j, (cols, direction) in enumerate(self.groups):
            if not cols:
                continue
            v = dev[:, cols]
            v = np.abs(v) if direction == 0 else np.maximum(direction * v, 0.0)
            v = np.where(np.isfinite(v), v, -np.inf)  # missing readings sort last
            top = -np.sort(-v, axis=1)[:, :3]
            ok = np.isfinite(top)
            n = ok.sum(1)
            with np.errstate(invalid="ignore"):
                out[:, j] = np.where(n > 0, np.where(ok, top, 0.0).sum(1) / np.maximum(n, 1), np.nan)
        out[:, SCORE] = np.asarray(s, float) / self.m.threshold
        return out


def blocks_of(values: np.ndarray, block: int = BLOCK) -> np.ndarray:
    """Medians of consecutive whole blocks of rows (the rows already running-only)."""
    n = len(values) // block
    if n == 0:
        return np.empty((0, values.shape[1]))
    with np.errstate(all="ignore"):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            return np.nanmedian(values[:n * block].reshape(n, block, -1), axis=1)


def _robust_sd(v) -> float:
    v = np.asarray(v, float)
    v = v[np.isfinite(v)]
    if len(v) < 2:
        return math.nan
    mad = 1.4826 * float(np.median(np.abs(v - np.median(v))))
    q = np.percentile(v, [25, 75])
    return max(mad, float(q[1] - q[0]) / 1.349)


def calibrate(model, t, X, names=None) -> dict:
    """This machine's normal for each series, from the rows it learned from (as effente.trainer does:
    running rows, per-period medians at the model's period). JSON-able; EarlyWarning(model, it)."""
    from effente import trainer as TR
    X = np.asarray(X, float)
    if names is not None:
        X = TR.to_current(X, names)
    t = np.asarray(t, float)
    from effente import features as F
    run = TR.running_rows(X, list(F.FEATURE_NAMES))
    t, X = TR.summarise(t[run], X[run], model.period)
    return calibrate_values(Series(model).values(X), model.period)


def calibrate_values(values: np.ndarray, period_s: float, block: int = BLOCK) -> dict:
    """calibrate() from per-period series values already computed (running periods, in time order)."""
    B = blocks_of(values, block)
    centre, spread = [], []
    for j in range(values.shape[1]):
        b = B[:, j] if len(B) else np.empty(0)
        b = b[np.isfinite(b)]
        per = values[:, j][np.isfinite(values[:, j])]
        if len(b) < 2 or len(per) < block:
            centre.append(None)
            spread.append(None)
            continue
        floor = SCORE_SPREAD_FLOOR if j == SCORE else SPREAD_FLOOR
        sd = max(_robust_sd(b), _robust_sd(per) / math.sqrt(block), floor)
        if len(b) < MIN_BLOCKS:
            sd *= CAUTIOUS
        centre.append(round(float(np.median(b)), 5))
        spread.append(round(float(sd), 5))
    return {"format": "effente-early-1", "series": list(SERIES), "block": block, "period_s": float(period_s),
            "blocks_learned": int(len(B)), "centre": centre, "spread": spread,
            "note": "median and robust spread of each series' block medians while learning"}


# ----------------------------------------------------------------- tests on a few block values

def mann_kendall_z(y) -> float:
    """Mann-Kendall trend statistic, as a z (positive: rising). NaN with fewer than 4 values."""
    y = np.asarray(y, float)
    y = y[np.isfinite(y)]
    n = len(y)
    if n < 4:
        return math.nan
    s = float(np.sign(y[None, :] - y[:, None])[np.triu_indices(n, 1)].sum())
    _, counts = np.unique(y, return_counts=True)
    var = (n * (n - 1) * (2 * n + 5) - float((counts * (counts - 1) * (2 * counts + 5)).sum())) / 18.0
    if var <= 0:
        return 0.0
    return (s - np.sign(s)) / math.sqrt(var)


def theil_sen(y) -> float:
    """Median slope of all pairs, per step. NaN with fewer than 2 values."""
    y = np.asarray(y, float)
    x = np.arange(len(y), dtype=float)
    ok = np.isfinite(y)
    x, y = x[ok], y[ok]
    if len(y) < 2:
        return math.nan
    i, j = np.triu_indices(len(y), 1)
    return float(np.median((y[j] - y[i]) / (x[j] - x[i])))


# ----------------------------------------------------------------- detectors (per series, per block)

class Cusum:
    def __init__(self, n, k=CUSUM_K, h=CUSUM_H):
        self.k, self.h = k, h
        self.s = np.zeros(n)
        self.on = np.zeros(n, bool)

    def step(self, z):
        z = np.where(np.isfinite(z), z, 0.0)
        self.s = np.minimum(np.maximum(0.0, self.s + z - self.k), CUSUM_CAP * self.h)
        self.on = np.where(self.on, self.s > CUSUM_OFF * self.h, self.s > self.h)
        return self.on

    def strength(self):
        return self.s / self.h


class Ewma:
    def __init__(self, n, lam=EWMA_LAMBDA, L=EWMA_L):
        self.lam = lam
        self.limit = L * math.sqrt(lam / (2 - lam))
        self.e = np.zeros(n)
        self.on = np.zeros(n, bool)

    def step(self, z):
        z = np.where(np.isfinite(z), z, 0.0)
        self.e = self.lam * z + (1 - self.lam) * self.e
        self.on = np.where(self.on, self.e > 0.5 * self.limit, self.e > self.limit)
        return self.on

    def strength(self):
        return self.e / self.limit


class Trend:
    def __init__(self, n, window=TREND_N, zc=TREND_Z, rise=TREND_RISE):
        self.zc, self.rise = zc, rise
        self.hist = deque(maxlen=window)
        self.n = n
        self.on = np.zeros(n, bool)
        self._st = np.zeros(n)

    def step(self, z):
        self.hist.append(np.asarray(z, float))
        H = np.array(self.hist)
        on = np.zeros(self.n, bool)
        st = np.zeros(self.n)
        if len(H) >= max(4, self.hist.maxlen // 2):
            for j in range(self.n):
                mk = mann_kendall_z(H[:, j])
                ts = theil_sen(H[:, j])
                if not (math.isfinite(mk) and math.isfinite(ts)):
                    continue
                total = ts * (len(H) - 1)
                st[j] = min(mk / self.zc, total / self.rise)
                on[j] = mk >= self.zc and total >= self.rise and H[-1, j] >= 1.0
        self.on, self._st = on, st
        return on

    def strength(self):
        return self._st


class Line:
    """Only the score: its block median over a fraction of the model's line, LINE_HOLD blocks in a row."""

    def __init__(self, n, frac=LINE_FRAC, hold=LINE_HOLD):
        self.frac, self.hold, self.n = frac, hold, n
        self.run = 0
        self.on = np.zeros(n, bool)
        self.raw = 0.0

    def step_raw(self, score_block):
        self.raw = score_block if math.isfinite(score_block) else 0.0
        self.run = self.run + 1 if self.raw > self.frac else 0
        self.on = np.zeros(self.n, bool)
        self.on[SCORE] = self.run >= self.hold
        return self.on

    def strength(self):
        st = np.zeros(self.n)
        st[SCORE] = self.raw / self.frac
        return st


# ----------------------------------------------------------------- the early warning itself

class EarlyWarning:
    """observe() once per summary period, with the model's state for it; state() is what is shown.

    calib: calibrate()'s dict for this model (None: early warning stays off, say so in the state)."""

    def __init__(self, model, calib: dict | None, method: str = DEFAULT_METHOD, sensitivity: float = 0.5,
                 block: int | None = None, alarm_sensitivity: float = SENSITIVITY_FOR_ALARM):
        if method not in METHODS:
            raise ValueError(f"method must be one of {METHODS}")
        self.m, self.method = model, method
        self.series = Series(model)
        self.ready = bool(calib) and list(calib.get("series", [])) == list(SERIES)
        self.block = int(block or (LINE_BLOCK if method == "line" else (calib or {}).get("block", BLOCK)))
        c = calib if self.ready else {"centre": [None] * len(SERIES), "spread": [None] * len(SERIES)}
        self.centre = np.array([np.nan if v is None else v for v in c["centre"]], float)
        self.spread = np.array([np.nan if v is None else v for v in c["spread"]], float)
        self.alarm_sensitivity = alarm_sensitivity
        self.sensitivity = float(sensitivity)
        self.reset()

    def set_sensitivity(self, sensitivity: float) -> None:
        self.sensitivity = float(sensitivity)
        f = scale(sensitivity)
        n = len(SERIES)
        if self.method in ("cusum", "cusum_score"):
            new = Cusum(n, h=CUSUM_H * f)
        elif self.method == "ewma":
            new = Ewma(n, L=EWMA_L * f)
        elif self.method == "trend":
            new = Trend(n, zc=TREND_Z * f, rise=TREND_RISE * f)
        elif self.method == "line":
            new = Line(n, frac=LINE_FRAC * f)
        else:
            new = None
        old = getattr(self, "det", None)
        if old is not None and new is not None and type(old) is type(new):  # keep what it has seen
            for a in ("s", "e", "hist", "run", "raw"):
                if hasattr(old, a):
                    setattr(new, a, getattr(old, a))
            new.on = old.on
        self.det = new

    def reset(self) -> None:
        self.buf: list[np.ndarray] = []
        self.blocks = 0
        self.zhist: deque = deque(maxlen=RISING_N)
        self.last_z = np.full(len(SERIES), np.nan)
        self.on = False  # the warning, whether or not shown
        self.named: str | None = None  # the series it names (sticks while it stays on)
        self.symptom: str | None = None
        self.rising = False
        self.speed: str | None = None
        self.since: float | None = None
        self.strength = 0.0
        self.alarm = False
        self.shown = False
        self.levels: deque = deque(maxlen=int(getattr(self.m, "smooth", 5)))
        self._said = None
        self.det = None
        self.set_sensitivity(self.sensitivity)

    # one period ---------------------------------------------------------------------------------
    def observe(self, t: float, running: bool, values=None, row=None, level: float | None = None,
                alarm: bool = False) -> list[tuple[str, dict]]:
        """One summary period: its series values (or its median row, to compute them), the Watcher's
        level (its unknown_score) and whether the model's alarm is on. Returns events to publish."""
        if not running:
            self.levels.clear()
            return self._show(t, alarm=False, stopped=True)
        if not self.ready:
            return []
        if values is None:
            values = self.series.values(row[None])[0]
        values = np.asarray(values, float)
        if self.method == "sensitivity":
            from effente import safety as SF
            v = float(values[SCORE])
            if math.isfinite(v):
                self.levels.append(v)
            lvl = float(np.median(self.levels)) if self.levels else 0.0
            on = lvl > SF.margins(self.alarm_sensitivity)["model"]
            self.last_z = self._z(values)
            self._set(on, SCORE, t, lvl / SF.margins(self.alarm_sensitivity)["model"])
            return self._show(t, alarm)
        self.buf.append(values)
        if len(self.buf) >= self.block:
            self._close_block(t)
        return self._show(t, alarm)

    def _z(self, b):
        with np.errstate(invalid="ignore"):
            return np.clip((b - self.centre) / self.spread, -Z_CLIP, Z_CLIP)

    def _close_block(self, t):
        with np.errstate(all="ignore"):
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                b = np.nanmedian(np.array(self.buf), axis=0)
        self.buf = []
        self.blocks += 1
        z = self._z(b)
        self.last_z = z
        self.zhist.append(z)
        if self.method == "line":
            on = self.det.step_raw(float(b[SCORE]))
        else:
            zz = z.copy()
            if self.method == "cusum_score":
                zz[:SCORE] = 0.0
            on = self.det.step(zz)
        st = self.det.strength()
        if on.any():
            if self.named is not None and on[SERIES.index(self.named)]:
                j = SERIES.index(self.named)
            else:
                cand = np.where(on, st, -np.inf)
                j = int(np.argmax(cand))
            self._set(True, j, t, float(st[j]))
        else:
            self._set(False, None, t, 0.0)

    def _set(self, on, j, t, strength):
        if not on:
            self.on, self.named, self.symptom, self.since = False, None, None, None
            self.rising, self.speed, self.strength = False, None, 0.0
            return
        name = SERIES[j]
        fresh = not self.on or name != self.named
        if fresh:
            self.since = float(t)
        self.on, self.named, self.strength = True, name, round(float(strength), 2)
        if j == SCORE and (fresh or self.symptom == "outside_normal"):  # name what moved most, if anything clearly did
            g = self.last_z[:SCORE]
            k = int(np.nanargmax(np.where(np.isfinite(g), g, -np.inf))) if np.isfinite(g).any() else -1
            self.symptom = GROUP_NAMES[k] if k >= 0 and g[k] >= NAMING_Z else "outside_normal"
            col = j
        elif j == SCORE:
            col = j
        else:
            self.symptom, col = name, j
        y = np.array([z[col] for z in self.zhist]) if self.zhist else np.empty(0)
        mk, ts = mann_kendall_z(y), theil_sen(y)
        self.rising = bool(math.isfinite(mk) and mk >= RISING_Z and ts > 0)
        self.speed = None
        if self.rising:
            hours = self.block * self.m.period / 3600.0 / ts  # to rise one more spread
            self.speed = "rising over minutes" if hours < 1 else "rising over hours" if hours < 24 else "rising over days"

    def _show(self, t, alarm, stopped=False) -> list[tuple[str, dict]]:
        """What changed on the screen, as events."""
        self.alarm = bool(alarm)
        shown = self.on and not alarm and not stopped
        was, said = self.shown, self._said
        self.shown = shown
        self._said = (self.symptom, self.rising) if shown else None
        if shown and (not was or said != self._said):
            return [("early_warning", self.details())]
        if was and not shown:
            return [("early_warning_cleared", {"reason": "the model's alarm is on" if alarm else
                                               "the machine stopped" if stopped else "the change went away",
                                               "symptom": said[0] if said else None})]
        return []

    # what is shown --------------------------------------------------------------------------------
    def text(self) -> str | None:
        if not self.on or self.symptom is None:
            return None
        return f"{FT.SYMPTOM_TEXT[self.symptom][0]} {'CREEPING UP' if self.rising else 'SLIGHTLY UP'}"

    def screen(self) -> str | None:
        """The screen line's text after "! " (at most 19 characters), or None when nothing is shown."""
        if not self.shown or self.symptom is None:
            return None
        return f"EARLY: {SHORT[self.symptom][1 if self.rising else 0]}"[:19]

    def details(self) -> dict:
        d = {"symptom": self.symptom, "text": self.text(), "screen": self.screen() if self.shown else None,
             "rising": self.rising, "speed": self.speed, "since": self.since, "series": self.named,
             "strength": self.strength, "method": self.method}
        if self.symptom:
            d["what"] = f"{FT.SYMPTOM_TEXT[self.symptom][1]}: a small change that has lasted, not (yet) wrong"
        return d

    def state(self) -> dict:
        """For status: on (shown), what, and why not shown."""
        s = {"on": self.shown, "method": self.method, "blocks": self.blocks, "block_periods": self.block}
        if not self.ready:
            s["note"] = "not calibrated for this model (learn again to turn it on)"
            return s
        if self.on:
            s.update({k: v for k, v in self.details().items() if k != "method"})
            if self.alarm:
                s["note"] = "set aside while the model says something is wrong"
        return s
