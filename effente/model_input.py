"""Turn feature rows into model inputs: the same preparation in training and on the device.

No model here. Feature rows (effente.features + effente.history) are meant to be read by people
and the agent: they hold NaN for missing sensors and young history, integer "which cell" indices,
heavy-tailed values and seconds-since counters. A model needs plain, bounded numbers. `Prep`
does that in one place, so training and the device cannot drift apart:

    prep = Prep.fit(X_train, names)        # statistics from training rows only
    Z = prep.transform(X)                  # (rows, prep.n_out) float32, no NaN, within +-CLIP
    prep.save("prep.json")                 # goes with the model file (the TRD's "normalisation
    prep = Prep.load("prep.json")          #  statistics"); the device loads the same one

What it does, and why (sources in device/intake/README.md):
  - Columns. Leaves out "which cell / band" indices (an arbitrary number: cell 47 is not "more"
    than cell 0, and when nothing is rising it flips at random); gives the jolt axis as three 0/1
    columns. Leaves out any feature that is NaN in more than half the training rows: trends longer
    than the learn period never had a value to learn from, and would look abnormal the day they
    appear. Leaves out the weekday unless training covers 3 weeks (otherwise each weekday is
    seen too few times). Leaves out constant columns.
  - Transforms. Seconds since the last jolt or bang: log of the time, capped at an hour (NaN,
    "never", counts as an hour). Kurtosis: log10 of (kurtosis + 3), which tames values in the
    thousands.
  - Scaling. Median and interquartile range of the training rows (robust to the odd spike), then
    clipped to +-CLIP. `out_of_range(X)` counts the values beyond the clip before clipping, so a
    reading far outside anything seen in training is not hidden by the clip.
  - Missing sensors. A missing value becomes 0 (the training median), and one 0/1 column per
    sensor says it was missing, so "no data" is never mistaken for "normal".
  - Running only. `fit(..., running_only=True)` learns from running rows only (trend_running), as
    stopped and running are different machines to a model.

Starting values, not tuned on real data.
"""

from __future__ import annotations

import hashlib
import json
import re
import warnings
from pathlib import Path

import numpy as np

from effente import features as F

CLIP = 8.0  # robust z beyond this is clipped (int8 inputs over +-8 step 1/16 of a spread)
MAX_NAN_FRAC = 0.5  # a feature NaN in more training rows than this is left out
SINCE_CAP_S = 3600.0
WEEKDAY_NEEDS_DAYS = 21
SENSORS = ("vibration", "rotation", "sound", "heat", "room")
_HOUR_FILE = re.compile(r"features_(\d+)\.npz")


def _transform_raw(X: np.ndarray, names: list[str]) -> np.ndarray:
    """Per-column transforms that need no statistics. Returns a float64 copy."""
    X = np.array(X, float)
    for j, n in enumerate(names):
        if "_since_" in n:
            v = X[:, j]
            X[:, j] = np.log1p(np.where(np.isnan(v), SINCE_CAP_S, np.minimum(np.maximum(v, 0.0), SINCE_CAP_S)))
        elif "kurt" in n:
            X[:, j] = np.log10(np.maximum(X[:, j] + 3.0, 1e-3))
    return X


class Prep:
    def __init__(self, columns, centre, scale, axis_col, missing_cols, names_in, version):
        self.columns = list(columns)  # names of the feature columns kept, in output order
        self.centre = np.asarray(centre, float)
        self.scale = np.asarray(scale, float)
        self.axis_col = axis_col  # input name of the jolt axis, given as 3 one-hot columns, or None
        self.missing_cols = list(missing_cols)  # sensors with a "missing" flag column
        self.names_in = list(names_in)
        self.features_version = version
        idx = {n: i for i, n in enumerate(self.names_in)}
        self._cols = [idx[n] for n in self.columns]
        self._axis = idx.get(axis_col) if axis_col else None
        self._sensor_cols = {s: [idx[n] for n in self.names_in if F.FEATURE_SENSOR.get(n) == s
                                 and not n.startswith(("trend_", "slow_", "knock_"))] for s in self.missing_cols}

    # ------------------------------------------------------------ fitting
    @classmethod
    def fit(cls, X, names=None, t0=None, running_only: bool = True) -> "Prep":
        names = [str(n) for n in (names if names is not None else F.FEATURE_NAMES)]
        X = np.asarray(X)
        if running_only and "trend_running" in names:
            run = X[:, names.index("trend_running")] == 1
            if run.sum() >= 100:  # too few running rows: use them all
                X = X[run]
                t0 = None if t0 is None else np.asarray(t0)[run]
        days = (float(np.max(t0) - np.min(t0)) / 86400.0) if t0 is not None and len(t0) else 0.0
        T = _transform_raw(X, names)
        keep = []
        for j, n in enumerate(names):
            kind = F.FEATURE_KIND.get(n, "")
            if kind == "index" or n == "trend_running":
                continue  # "which" indices are dropped; the axis comes back one-hot below
            if n.startswith("time_weekday") and days < WEEKDAY_NEEDS_DAYS:
                continue
            col = T[:, j]
            ok = ~np.isnan(col)
            if ok.mean() < 1 - MAX_NAN_FRAC:
                continue
            if np.nanmax(col) - np.nanmin(col) <= 0:
                continue  # constant in training: nothing to learn from it
            keep.append(j)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            centre = np.nanmedian(T[:, keep], 0)
            q1, q3 = np.nanpercentile(T[:, keep], [25, 75], 0)
            spread = (q3 - q1) / 1.349
            std = np.nanstd(T[:, keep], 0)
        scale = np.where(spread > 1e-9, spread, np.where(std > 1e-9, std, 1.0))  # mostly-constant counts
        missing = [s for s in SENSORS if any(F.FEATURE_SENSOR.get(n) == s for n in names)]
        axis = "vib_shock_axis" if "vib_shock_axis" in names else None
        return cls([names[j] for j in keep], centre, scale, axis, missing, names, _version(names))

    # ------------------------------------------------------------ applying
    @property
    def names_out(self) -> list[str]:
        axis = [f"{self.axis_col}_{a}" for a in "xyz"] if self.axis_col else []
        return self.columns + axis + [f"missing_{s}" for s in self.missing_cols]

    @property
    def n_out(self) -> int:
        return len(self.names_out)

    def _scaled(self, X) -> np.ndarray:
        X = np.atleast_2d(np.asarray(X))
        if X.shape[1] != len(self.names_in):
            raise ValueError(f"rows have {X.shape[1]} features, this preparation expects {len(self.names_in)}")
        T = _transform_raw(X[:, self._cols], self.columns)
        return (T - self.centre) / self.scale

    def transform(self, X) -> np.ndarray:
        X = np.atleast_2d(np.asarray(X))
        Z = self._scaled(X)
        Z = np.clip(np.nan_to_num(Z, nan=0.0), -CLIP, CLIP)
        parts = [Z]
        if self._axis is not None:
            a = X[:, self._axis]
            parts.append(np.stack([(a == k).astype(float) for k in range(3)], 1))
        parts.append(np.stack([np.isnan(X[:, c]).all(1).astype(float) if c else np.zeros(len(X))
                               for c in self._sensor_cols.values()], 1) if self._sensor_cols else np.zeros((len(X), 0)))
        return np.hstack(parts).astype(np.float32)

    def out_of_range(self, X) -> np.ndarray:
        """Per row: how many kept features were beyond +-CLIP before clipping (far outside training)."""
        with np.errstate(invalid="ignore"):
            return (np.abs(self._scaled(X)) > CLIP).sum(1)

    # ------------------------------------------------------------ saving
    def to_dict(self) -> dict:
        return {"features_version": self.features_version, "names_in": self.names_in, "columns": self.columns,
                "centre": [float(v) for v in self.centre], "scale": [float(v) for v in self.scale],
                "axis_col": self.axis_col, "missing_cols": self.missing_cols, "clip": CLIP,
                "names_out": self.names_out}

    def save(self, path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=1))

    @classmethod
    def from_dict(cls, d: dict) -> "Prep":
        return cls(d["columns"], d["centre"], d["scale"], d["axis_col"], d["missing_cols"], d["names_in"],
                   d["features_version"])

    @classmethod
    def load(cls, path) -> "Prep":
        return cls.from_dict(json.loads(Path(path).read_text()))


def _version(names) -> str:
    return hashlib.sha1("|".join(names).encode()).hexdigest()[:12]


# ----------------------------------------------------------------- training data

def load_recording(folder, every_s: float = 1.0):
    """All feature rows of a learn-mode folder: (X float32, t0, names). Checks every hour file
    was made with the same features; thinned hours simply have fewer rows."""
    files = sorted(((int(m.group(1)), p) for p in (Path(folder) / "features").glob("features_*.npz")
                    if (m := _HOUR_FILE.fullmatch(p.name))))
    Xs, ts, names, version = [], [], None, None
    for _, p in files:
        with np.load(p) as d:
            v = str(d["features_version"])
            if version is None:
                version, names = v, [str(n) for n in d["feature_names"]]
            elif v != version:
                raise ValueError(f"{p.name} was made with features {v}, earlier hours with {version}")
            Xs.append(d["X"])
            ts.append(d["t0"])
    if not Xs:
        raise FileNotFoundError(f"no hour files in {Path(folder) / 'features'}")
    X, t0 = np.concatenate(Xs), np.concatenate(ts)
    if every_s > 1.0:
        sel = np.r_[True, np.diff(np.floor(t0 / every_s)) > 0]
        X, t0 = X[sel], t0[sel]
    return X, t0, names


def day_split(t0, test_days: float = 1.0, gap_s: float = 3600.0):
    """Train and test masks for a continuous recording: the last `test_days` are the test set, and
    `gap_s` before them is left out of both, so no near-identical neighbouring rows sit on both
    sides. Never split rows at random: neighbouring rows are almost copies of each other."""
    t0 = np.asarray(t0, float)
    cut = float(t0.max()) - test_days * 86400.0
    return t0 < cut - gap_s, t0 >= cut


def persistent(flags, k: int = 8, n: int = 12) -> np.ndarray:
    """True where at least k of the last n rows were flagged. At 4 rows a second a per-row
    threshold that is wrong 0.1 % of the time still fires hundreds of times a day."""
    f = np.asarray(flags, float)
    c = np.convolve(f, np.ones(n), mode="full")[:len(f)]
    return c >= k
