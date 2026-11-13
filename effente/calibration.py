"""Calibrate a library entry's model to one machine. NumPy only, so the device can do it.

A library entry (training/library/entries/<entry_id>/model.json) is a plain effente-model-1 file
trained on some machine type. Calibration keeps its network and fits the rest to the machine the
device has just been snapped onto, from a few minutes of that machine running (TRD, "Model library
and calibration"; docs/model-lifecycle.md, "Calibration" and "Fit check"):

  kept from the entry      the normal model's weights (and, for PCA, its score scales), which
                           readings it uses, the persistence, the fault-type and feature tables
  fitted to this machine   the preparation statistics (effente.model_input.Prep: the median and
                           spread of every reading the entry uses), the spreads of the residuals,
                           the alarm threshold, the fault classifier (injected patterns on this
                           machine's own normal variation, as in full training) and the baseline
                           for the outside-normal checks (effente.safety)

It follows effente.trainer.train_model step for step and reuses its parts, so a calibrated model
and one trained on the machine alone differ only in where the network came from: running rows
only, per-period medians, the last 15 % of the time held out for the threshold (never a random
split), a gamma fit set for one false alarm a week.

    model, report = calibrate(entry_model, t, X, names)
    verdict = fit_check(report, manifest)   # does this machine look like the entry's type?

The result is a valid effente-model-1 dict: effente.model_runtime.Model loads it unchanged. No new
keys are added to the file: "created" is the calibration time, "training_report" is the calibration
report (with "calibrated": true and the entry's own report under "entry_training_report").

Starting values throughout. Nothing here has been checked on a real machine (unverified): the
numbers behind FIT_MAX_RATIO come from the simulators only (training/calibration/README.md).
"""

from __future__ import annotations

import copy
import math
import warnings
from datetime import datetime, timezone

import numpy as np

from effente import fault_types as FT
from effente import features as F
from effente import model_runtime as RT
from effente import safety as SF
from effente import trainer as TR
from effente.model_input import MAX_NAN_FRAC, Prep, _transform_raw

MIN_RUNNING_S = 300  # the trainer's minimum (TRD: starting value, 5 minutes of running)
MIN_ROWS = 60  # below this there are too few held-out rows to fit a threshold at all, whatever min_running_s says
CALIB_SHARE = 0.15  # the last part of the calibration, held out for the threshold (as the trainer)

# Fit check: the machine "does not look like the entry's type" when its median held-out score is
# more than this many times the entry's own held-out median. A starting value, UNVERIFIED: set from
# simulated machines only (see training/calibration/README.md for the runs behind it). The TRD
# leaves the rule open until there are recordings of real machines.
FIT_MAX_RATIO = 3.0

QUANTILES = (("p50", 50), ("p90", 90), ("p99", 99))
_MANIFEST_QUANTILE_KEYS = ("held_out_score_quantiles", "heldout_score_quantiles", "score_quantiles")
_MEDIAN_KEYS = ("p50", "q50", "0.5", "50", "median")


def _quantiles(s) -> dict:
    s = np.asarray(s, float)
    s = s[np.isfinite(s)]
    if not len(s):
        return {}
    out = {k: round(float(np.percentile(s, q)), 6) for k, q in QUANTILES}
    out["max"] = round(float(s.max()), 6)
    return out


SPREAD_FLOOR = True
MUTE_MISSING = True
NOT_JUDGED = 1e12
THRESHOLD_FLOOR = True


def refit_prep(entry_prep: Prep, X_fit: np.ndarray) -> tuple[Prep, dict]:
    """The entry's preparation with this machine's median and spread for every reading it uses.

    The columns stay exactly the entry's (the network's inputs are fixed). A reading the entry uses
    that this machine doesn't have (NaN in more than half the rows: the same rule Prep.fit uses)
    keeps the entry's median and spread. One that never changes here takes this machine's value as
    its median and keeps the entry's spread, so a later change is measured in the entry's spreads.
    Returns (prep, {"missing": [names], "constant": [names]})."""
    idx = {n: i for i, n in enumerate(entry_prep.names_in)}
    cols = entry_prep.columns
    T = _transform_raw(np.asarray(X_fit, float)[:, [idx[n] for n in cols]], cols)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        have = np.isfinite(T).mean(0)
        centre = np.nanmedian(T, 0)
        q1, q3 = np.nanpercentile(T, [25, 75], 0)
        std = np.nanstd(T, 0)
    spread = (q3 - q1) / 1.349
    scale = np.where(spread > 1e-9, spread, np.where(std > 1e-9, std, np.nan))  # as Prep.fit
    missing = ~(have >= 1 - MAX_NAN_FRAC) | ~np.isfinite(centre)
    constant = ~missing & ~np.isfinite(scale)
    centre = np.where(missing, entry_prep.centre, centre)
    scale = np.where(constant, entry_prep.scale, scale)
    if SPREAD_FLOOR:
        scale = np.maximum(scale, entry_prep.scale)
    scale = np.where(missing, NOT_JUDGED if MUTE_MISSING else entry_prep.scale, scale)
    prep = Prep(cols, centre, scale, entry_prep.axis_col, entry_prep.missing_cols, entry_prep.names_in,
                entry_prep.features_version)
    return prep, {"missing": [c for c, m in zip(cols, missing) if m],
                  "constant": [c for c, m in zip(cols, constant) if m]}


def calibrate(entry_model: dict, t, X, names, *, min_running_s: float = MIN_RUNNING_S, seed: int = 0):
    """Fit a library entry's model to this machine from feature rows of it running.

    entry_model: the entry's effente-model-1 dict (not changed). t, X, names: one-a-second feature
    rows with their times and feature names, as for effente.trainer.train_model. Returns
    (model file dict, report). Raises ValueError if the entry is not a model this device can run,
    or the machine ran for less than min_running_s seconds in the rows given."""
    entry = RT.Model(entry_model)  # refuses another format or another feature list
    X = TR.to_current(X, names)
    names = list(F.FEATURE_NAMES)
    t = np.asarray(t, float)
    if len(t) != len(X):
        raise ValueError(f"{len(t)} times for {len(X)} rows")
    run = TR.running_rows(X, names)
    ran = run.copy()
    t, X = t[run], X[run]
    if len(t) < 2:
        raise ValueError("no running data recorded")
    order = np.argsort(t, kind="stable")
    t, X = t[order], X[order]
    period = TR.choose_period(float(t[-1] - t[0]))  # 1 s for a calibration of minutes, as the trainer
    t, X = TR.summarise(t, X, period)
    running_s = len(t) * period
    need = max(float(min_running_s), MIN_ROWS * period)
    if running_s < need:
        raise ValueError(f"too little recorded: {running_s} s of the machine running; "
                         f"calibration needs at least {math.ceil(need)} s")
    baseline = SF.learn_baseline(X, names, running=ran)
    cut = t[0] + (t[-1] - t[0]) * (1 - CALIB_SHARE)
    fit, held = t < cut, t >= cut

    s_before = entry.score(X)[0]  # this machine through the entry as shipped, for the report only
    prep, fell_back = refit_prep(entry.prep, X[fit])
    d = copy.deepcopy(entry_model)
    d.update(created=datetime.now(timezone.utc).isoformat(timespec="seconds"), prep=prep.to_dict(),
             period_s=int(period), baseline=baseline)
    model = RT.Model(d)
    s, r, Z = model.score(X)
    scale = np.std(r[fit], 0)
    d["residual_scale"] = [float(v) for v in np.where(scale > 1e-4, scale, 1e-4)]
    d["threshold"] = TR.gamma_threshold(TR._causal_median(s[held], model.smooth), 7 * 86400 / period)
    if THRESHOLD_FLOOR:
        d["threshold"] = max(d["threshold"], float(entry_model["threshold"]))
    model = RT.Model(d)

    # The fault classifier, exactly as in full training: this machine's own normal deviations with the
    # fault-type signatures added. No real labelled faults (unverified).
    rng = np.random.default_rng(seed)
    dev_cols = [c for c in prep.names_out if c in prep.columns]
    base = model.deviation_vector(r[fit], Z[fit])
    Xc, Yc, classes = TR.inject(base, dev_cols, rng)
    acts = ["relu", "sigmoid"]
    clf, _ = TR.fit_mlp(Xc, Yc, [Xc.shape[1], 32, Yc.shape[1]], acts, loss="bce", seed=seed, epochs=150)
    d["fault_classifier"] = {"classes": classes, "inputs": dev_cols,
                             "layers": [RT.quantize(W, b, a) for (W, b), a in zip(clf, acts)],
                             "note": "learned from injected signature patterns only; no real labelled faults yet"}
    p = 1 / (1 + np.exp(-np.clip(np.maximum(Xc @ clf[0][0] + clf[0][1], 0) @ clf[1][0] + clf[1][1], -30, 30)))
    acc = float(((p > 0.5) == (Yc > 0.5)).all(1).mean())
    d["fault_types"] = [{"name": ft.name, "text": ft.text, "sensing": ft.sensing} for ft in FT.TYPES]

    notes = list(baseline["warnings"])
    entry_period = int(entry_model.get("period_s", 60))
    if entry_period != period:
        notes.append(f"the entry was trained on {entry_period} s summaries, this calibration used {period} s ones: "
                     f"the fit check compares scores of different summaries")
    if fell_back["missing"]:
        notes.append(f"{len(fell_back['missing'])} of the entry's {len(prep.columns)} readings are not measured "
                     f"here; they keep the entry's levels")
    report = {"calibrated": True, "features_version": d["features_version"],
              "normal_model": d.get("normal_model", "autoencoder"),
              "period_s": int(period), "entry_period_s": entry_period, "running_s": int(running_s),
              "rows_fit": int(fit.sum()), "rows_held_out": int(held.sum()),
              "threshold": round(d["threshold"], 4), "entry_threshold": round(float(entry_model["threshold"]), 4),
              "score_quantiles_held_out": _quantiles(s[held]), "score_quantiles_fit": _quantiles(s[fit]),
              "score_quantiles_before_calibration": _quantiles(s_before),
              "readings": len(prep.columns), "readings_missing": fell_back["missing"],
              "readings_constant": fell_back["constant"],
              "fault_classes": len(classes), "classifier_exact_match": round(acc, 3),
              "dropped_limits": baseline["dropped"], "warnings": notes,
              "note": "starting values, unverified on real machines"}
    d["training_report"] = dict(report, entry_training_report=entry_model.get("training_report"))
    return d, report


def _manifest_median(manifest: dict):
    """The entry's held-out median score from its manifest, or None. The manifest's exact key names
    are the library's; the usual spellings are accepted."""
    for key in _MANIFEST_QUANTILE_KEYS:
        q = manifest.get(key)
        if isinstance(q, dict):
            for k in _MEDIAN_KEYS:
                v = q.get(k)
                if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v > 0:
                    return float(v)
    return None


def fit_check(report: dict, manifest: dict) -> dict:
    """Does the machine just calibrated look like the entry's machine type?

    report: calibrate()'s report. manifest: the entry's manifest.json. Compares the median score of
    the held-out calibration rows (this machine's levels, the entry's network) with the entry's own
    held-out median. Returns {"ok", "ratio", "reason"}; ratio is NaN when it could not be worked
    out, and then ok is False: a fit that was not checked is never reported as a fit.

    The rule (ratio <= FIT_MAX_RATIO) is a starting value set on simulated machines. Unverified."""
    def no(reason, ratio=float("nan")):
        return {"ok": False, "ratio": ratio, "reason": reason}

    for key in ("features_version", "normal_model"):
        if manifest.get(key) is not None and report.get(key) is not None and manifest[key] != report[key]:
            return no(f"not checked: the manifest says {key} {manifest[key]}, the calibrated model {report[key]}")
    ref = _manifest_median(manifest)
    if ref is None:
        return no("not checked: the entry's manifest has no held-out median score")
    mine = (report.get("score_quantiles_held_out") or {}).get("p50")
    if not isinstance(mine, (int, float)) or isinstance(mine, bool) or not math.isfinite(mine):
        return no("not checked: the calibration report has no held-out median score")
    ratio = float(mine) / ref
    if ratio > FIT_MAX_RATIO:
        return no(f"this machine does not look like the entry's type: its normal scores {ratio:.1f} times the "
                  f"entry's own (limit {FIT_MAX_RATIO:g}, unverified); try generic_rotating or a long stay", ratio)
    return {"ok": True, "ratio": ratio,
            "reason": f"fits: its normal scores {ratio:.1f} times the entry's own (limit {FIT_MAX_RATIO:g}, unverified)"}
