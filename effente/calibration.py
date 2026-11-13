"""Calibrate a pretrained library entry to one machine. NumPy only, so the device can do it.

effente.trainer.train_model learns everything from a machine's own long recording. Calibration is
the quick way: the technician picks a library entry (a model file trained beforehand on that type of
machine), the device records about 5 minutes, and calibrate() returns a model file that

  keeps from the entry   the network, the alarm threshold, the score scales, the fault classifier,
                         the summary period, and which readings go in and in which order
  learns from this       only the usual level and spread of each reading (the preparation's centre
  machine                and scale) and the baseline for the outside-normal checks
  switches off           readings a short look cannot learn (the clock, the IMU's temperature and the
                         trends by default) and readings this machine does not have: centre 0 and a
                         huge spread, so they always read as "usual"

The threshold is NOT refitted on purpose. On the repo's simulators (not on real machines), a
threshold refitted from 300 calibration rows alarmed 5 to 74 percent of the time on unseen simulated
conveyors, and the entry's own threshold 0 to 0.1 percent. Unverified on real machines.

An entry is any effente-model-1 dict (a train_model result is one). Optional keys it may carry:
  library_entry, entry_version   its name and version, copied into the result
  pipeline                       kept as it is
  calibration                    {"widen": [one factor per prep.columns entry],
                                  "hold": ["time_", "trend_", "imu_temp_c"]}
    widen  multiplies the refitted spread of each reading: 5 minutes see less variation than a day.
           Without it every factor is 1.
    hold   readings to switch off. A name ending in "_" is a prefix, any other is a whole name.
           Without it, HOLD below.
"""

from __future__ import annotations

import copy
import warnings
from datetime import datetime, timezone

import numpy as np

from effente import features as F
from effente import model_runtime as RT
from effente import safety as SF
from effente import trainer as T
from effente.model_input import MAX_NAN_FRAC, _transform_raw

HOLD = ("time_", "trend_", "imu_temp_c")
OFF_SCALE = 1e12  # a spread this large makes any value read as 0 spreads from usual
MIN_ROWS = T.MIN_ROWS


def _held(columns, hold) -> np.ndarray:
    prefixes = tuple(h for h in hold if h.endswith("_"))
    whole = {h for h in hold if not h.endswith("_")}
    return np.array([c in whole or (bool(prefixes) and c.startswith(prefixes)) for c in columns], bool)


def _settings(entry: dict, columns: list[str]):
    """The entry's widen factors (one per column) and hold list, with the defaults."""
    cal = entry.get("calibration") or {}
    widen = cal.get("widen")
    widen = np.ones(len(columns)) if widen is None else np.asarray(widen, float)
    if widen.shape != (len(columns),):
        raise ValueError(f"the entry has {widen.size} widen factors for {len(columns)} readings")
    if not (np.isfinite(widen).all() and (widen > 0).all()):
        raise ValueError("the entry's widen factors must all be above 0")
    hold = cal.get("hold")
    return widen, [str(h) for h in (HOLD if hold is None else hold)]


def calibrate(entry: dict, t, X, names=None, source: str = "", min_rows: int = MIN_ROWS):
    """Fit a library entry to one machine from a short recording of it running normally.

    entry: an effente-model-1 dict (module notes). t, X, names: one-a-second feature rows, as for
    train_model (names None: the current feature list). source: where the rows came from.
    Returns (model file dict, report). Raises ValueError if fewer than min_rows rows of the entry's
    period are left after dropping the stopped ones. The entry is not changed."""
    model = RT.Model(entry)  # refuses another format or another feature list
    prep_d = entry["prep"]
    columns = list(prep_d["columns"])
    if list(prep_d["names_in"]) != list(F.FEATURE_NAMES):
        raise ValueError("the entry's preparation was made for another feature list")
    widen, hold = _settings(entry, columns)

    names = list(F.FEATURE_NAMES) if names is None else [str(n) for n in names]
    X = T.to_current(X, names)
    names = list(F.FEATURE_NAMES)
    t = np.asarray(t, float)
    ran = T.running_rows(X, names)
    t, X = t[ran], X[ran]
    period = model.period
    if len(t):
        t, X = T.summarise(t, X, period)
    if len(t) < min_rows:
        raise ValueError(f"too little recorded: {len(t)} rows of {period} s with the machine running; "
                         f"calibration needs at least {min_rows} (about {-(-min_rows * period // 60)} minutes)")

    # This machine's usual level and spread of each of the entry's readings, as Prep.fit computes them.
    raw = _transform_raw(X[:, [F.INDEX[c] for c in columns]], columns)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        centre = np.nanmedian(raw, 0)
        q1, q3 = np.nanpercentile(raw, [25, 75], 0)
        spread = (q3 - q1) / 1.349
        std = np.nanstd(raw, 0)
    scale = np.where(spread > 1e-9, spread, np.where(std > 1e-9, std, 1.0)) * widen
    held = _held(columns, hold)
    absent = np.isnan(raw).mean(0) > MAX_NAN_FRAC
    off = held | absent | ~np.isfinite(centre)
    centre = np.where(off, 0.0, centre)
    scale = np.where(off, OFF_SCALE, scale)

    d = copy.deepcopy(entry)
    d["prep"]["centre"] = [float(v) for v in centre]
    d["prep"]["scale"] = [float(v) for v in scale]
    baseline = SF.learn_baseline(X, names, running=ran)
    d.update(baseline=baseline, source="calibrated", library_entry=entry.get("library_entry"),
             entry_version=entry.get("entry_version"), trained_on=source,
             created=datetime.now(timezone.utc).isoformat(timespec="seconds"))

    out = RT.Model(d)
    width = int(d["autoencoder"][0]["shape"][0])
    if out.prep.n_out != model.prep.n_out or out.prep.n_out != width:
        raise ValueError(f"calibration changed the model's input width ({out.prep.n_out}, the entry has "
                         f"{model.prep.n_out} and its network takes {width})")
    level = out.score(X)[0] / out.threshold  # each row on its own, before the Watcher's persistence
    report = {"rows_used": int(len(t)), "period_s": int(period),
              "columns_off": {"held": [c for c, h in zip(columns, held) if h],
                              "absent": [c for c, a, h in zip(columns, absent, held) if a and not h]},
              "median_score_over_threshold": round(float(np.median(level)), 4),
              "share_rows_over_threshold": round(float((level > 1.0).mean()), 4),
              "threshold": float(out.threshold), "warnings": list(baseline["warnings"])}
    d["calibration_report"] = report
    return d, report
