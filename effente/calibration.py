"""Calibrate a library entry's model to one machine. NumPy only, so the device can do it.

A library entry (training/library/entries/<entry_id>/model.json) is a plain effente-model-1 file
trained on some machine type. Calibration keeps its network and fits the rest to the machine the
device has just been snapped onto, from a few minutes of that machine running (TRD, "Model library
and calibration"; docs/model-lifecycle.md, "Calibration" and "Fit check"):

  kept from the entry      the normal model's weights (and, for PCA, its score scales), which
                           readings it uses, the persistence, the feature table
  fitted to this machine   the preparation statistics (effente.model_input.Prep: the median and
                           spread of every reading the entry uses), the spreads of the residuals,
                           the alarm threshold, the baseline for the outside-normal checks
                           (effente.safety) and, if the entry carries a fault classifier, that
                           too (injected patterns on this machine's own normal variation, as in
                           full training)
  with the entry as guard  a few minutes cannot show how far slow readings wander in a day, so the
                           fitted spreads are widened by the entry's factors (or never taken below
                           the entry's own spreads), the threshold is never below the entry's, and
                           readings the entry holds or this machine did not give are not judged
                           (refit_prep). Without these guards the simulated machines alarmed most
                           of the time in the 40 minutes after a 5-minute calibration.

It follows effente.trainer.train_model step for step and reuses its parts, so a calibrated model
and one trained on the machine alone differ only in where the network came from: running rows
only, per-period medians, the last 15 % of the time held out for the threshold (never a random
split), a gamma fit set for one false alarm a week.

    model, report = calibrate(entry_model, t, X, names)
    verdict = fit_check(report, manifest)   # does this machine look like the entry's type?

The result is a valid effente-model-1 dict: effente.model_runtime.Model loads it unchanged. The
file format is not changed: "created" is the calibration time, "training_report" is the calibration
report (with "calibrated": true and the entry's own report under "entry_training_report"), and any
other key the entry carries is kept ("source", if the entry has it, becomes "calibrated").

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
# The alarm threshold is never set below this share of the entry's own threshold. 1.0: never below the
# entry's. 0: the threshold fitted to this machine alone. STARTING VALUE, simulated only: 1.0 keeps a
# short calibration quiet and costs sensitivity (training/calibration/README.md has the runs).
THRESHOLD_FLOOR_SHARE = 1.0
CALIB_SHARE = 0.15  # the last part of the calibration, held out for the threshold (as the trainer)

# Fit check: ok is False when this machine's median held-out score is more than this many times the
# entry's own held-out median. STARTING VALUE, SIMULATED ONLY, unverified. On the simulators it did
# not tell machine types apart: server racks passed on a conveyor entry (training/calibration/README.md).
# It can only reject a gross mismatch; a pass does not show the machine is of the entry's type. The
# TRD leaves the rule open until there are recordings of real machines.
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


NOT_JUDGED = 1e12  # a spread this large makes a reading always read as usual: it is not judged


def _entry_settings(entry_model: dict, columns: list[str]):
    """What the entry itself says about calibrating it: (widen factors or None, held mask).

    The library's entries (training/library) carry "calibration": {"widen": one factor per reading,
    "hold": names or prefixes ending in "_"}. widen multiplies the spread fitted here, because a few
    minutes see less variation than the hours the entry was built from. hold names readings a short
    calibration cannot fit (the clock, trends, the IMU's temperature): they are not judged.
    An entry without it (a plain trained model) gets no widening and nothing held; its own spreads
    are then the floor (refit_prep)."""
    cal = entry_model.get("calibration")
    cal = cal if isinstance(cal, dict) else {}
    widen = cal.get("widen")
    if widen is not None:
        widen = np.asarray(widen, float)
        if widen.shape != (len(columns),) or not (np.isfinite(widen).all() and (widen > 0).all()):
            raise ValueError(f"the entry's widen factors must be {len(columns)} numbers above 0")
    hold = [str(h) for h in (cal.get("hold") or ())]
    prefixes = tuple(h for h in hold if h.endswith("_"))
    whole = {h for h in hold if not h.endswith("_")}
    held = np.array([c in whole or (bool(prefixes) and c.startswith(prefixes)) for c in columns], bool)
    return widen, held


def refit_prep(entry_prep: Prep, X_fit: np.ndarray, widen=None, held=None) -> tuple[Prep, dict]:
    """The entry's preparation with this machine's median and spread for every reading it uses.

    The columns stay exactly the entry's (the network's inputs are fixed). Per reading:
      - fitted: this machine's median, and its spread (as Prep.fit works them out). The spread is
        multiplied by the entry's widen factor if it has them; if not, it is never taken below the
        entry's own spread. Either way a few minutes of calibration cannot make the model stricter
        about slow readings than the hours behind the entry justify.
      - constant here: this machine's value as the median, the entry's spread.
      - not judged: readings the entry holds, and readings this machine did not give while
        calibrating (NaN in more than half the rows: Prep.fit's rule). Their level here is unknown,
        so they get a huge spread and always read as usual, also when they appear later (a 10-minute
        trend that starts after a 5-minute calibration).
    Returns (prep, {"missing": [...], "constant": [...], "held": [...]})."""
    idx = {n: i for i, n in enumerate(entry_prep.names_in)}
    cols = entry_prep.columns
    held = np.zeros(len(cols), bool) if held is None else np.asarray(held, bool)
    T = _transform_raw(np.asarray(X_fit, float)[:, [idx[n] for n in cols]], cols)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        have = np.isfinite(T).mean(0)
        centre = np.nanmedian(T, 0)
        q1, q3 = np.nanpercentile(T, [25, 75], 0)
        std = np.nanstd(T, 0)
    spread = (q3 - q1) / 1.349
    scale = np.where(spread > 1e-9, spread, np.where(std > 1e-9, std, np.nan))  # as Prep.fit
    missing = ~held & (~(have >= 1 - MAX_NAN_FRAC) | ~np.isfinite(centre))
    constant = ~held & ~missing & ~np.isfinite(scale)
    off = held | missing
    scale = scale * widen if widen is not None else np.fmax(scale, entry_prep.scale)
    scale = np.where(constant, entry_prep.scale, scale)
    centre = np.where(off, entry_prep.centre, centre)
    scale = np.where(off, NOT_JUDGED, scale)
    prep = Prep(cols, centre, scale, entry_prep.axis_col, entry_prep.missing_cols, entry_prep.names_in,
                entry_prep.features_version)
    pick = lambda mask: [c for c, m in zip(cols, mask) if m]
    return prep, {"missing": pick(missing), "constant": pick(constant), "held": pick(held)}


def calibrate(entry_model: dict, t, X, names, *, min_running_s: float = MIN_RUNNING_S, seed: int = 0,
              threshold_floor_share: float = THRESHOLD_FLOOR_SHARE):
    """Fit a library entry's model to this machine from feature rows of it running.

    entry_model: the entry's effente-model-1 dict (not changed). t, X, names: one-a-second feature
    rows with their times and feature names, as for effente.trainer.train_model. Returns
    (model file dict, report). Raises ValueError if the entry is not a model this device can run,
    or the machine ran for less than min_running_s seconds in the rows given.
    threshold_floor_share: the alarm threshold is never set below this share of the entry's own
    (THRESHOLD_FLOOR_SHARE; 0 uses the threshold fitted here alone). The default of 1.0 costs
    sensitivity: see the module notes."""
    if not (isinstance(threshold_floor_share, (int, float)) and math.isfinite(threshold_floor_share)
            and threshold_floor_share >= 0):
        raise ValueError("threshold_floor_share must be a number of 0 or more")
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
    period = entry.period  # the entry's own summary period: what its network was trained on
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
    widen, held_cols = _entry_settings(entry_model, entry.prep.columns)
    prep, fell_back = refit_prep(entry.prep, X[fit], widen, held_cols)
    d = copy.deepcopy(entry_model)
    d.update(created=datetime.now(timezone.utc).isoformat(timespec="seconds"), prep=prep.to_dict(),
             period_s=int(period), baseline=baseline)
    if "source" in d:  # the library marks its files "entry"; this one no longer is
        d["source"] = "calibrated"
    model = RT.Model(d)
    s, r, Z = model.score(X)
    scale = np.std(r[fit], 0)
    d["residual_scale"] = [float(v) for v in np.where(scale > 1e-4, scale, 1e-4)]
    # The threshold: fitted to this machine's held-out scores as the trainer does, but never below
    # threshold_floor_share of the entry's own. A few dozen held-out seconds can show this machine is
    # noisier than the entry's machines; they cannot show a week of it would be quieter.
    fitted = TR.gamma_threshold(TR._causal_median(s[held], model.smooth), 7 * 86400 / period)
    d["threshold"] = max(fitted, float(threshold_floor_share) * float(entry_model["threshold"]))
    model = RT.Model(d)

    # The fault classifier, as in full training (this machine's own normal deviations with the fault-type
    # signatures added; no real labelled faults, unverified), for an entry that carries one. An entry
    # without one (the library's) gets none: faults are then named by the signatures alone. On the
    # simulated conveyor a classifier fitted to 5 minutes named an unbalance "impacts"; the signatures
    # alone named it unbalance_like (training/calibration/README.md).
    classes, acc = [], None
    if "fault_classifier" in entry_model:
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
        acc = round(float(((p > 0.5) == (Yc > 0.5)).all(1).mean()), 3)
    d["fault_types"] = [{"name": ft.name, "text": ft.text, "sensing": ft.sensing} for ft in FT.TYPES]

    notes = list(baseline["warnings"])
    if fell_back["missing"]:
        notes.append(f"{len(fell_back['missing'])} of the entry's {len(prep.columns)} readings were not measured "
                     f"while calibrating; they are not judged")
    report = {"calibrated": True, "features_version": d["features_version"],
              "normal_model": d.get("normal_model", "autoencoder"),
              "period_s": int(period), "running_s": int(running_s),
              "rows_fit": int(fit.sum()), "rows_held_out": int(held.sum()),
              "threshold": round(d["threshold"], 4), "threshold_fitted_here": round(fitted, 4),
              "threshold_floor_share": float(threshold_floor_share),
              "entry_threshold": round(float(entry_model["threshold"]), 4),
              "score_quantiles_held_out": _quantiles(s[held]), "score_quantiles_fit": _quantiles(s[fit]),
              "score_quantiles_before_calibration": _quantiles(s_before),
              "readings": len(prep.columns), "readings_missing": fell_back["missing"],
              "readings_constant": fell_back["constant"], "readings_held": fell_back["held"],
              "spreads": "widened by the entry's factors" if widen is not None else "never below the entry's",
              "fault_classes": len(classes), "classifier_exact_match": acc,
              "dropped_limits": baseline["dropped"], "warnings": notes,
              "note": "starting values, unverified on real machines"}
    d["training_report"] = dict(report, entry_training_report=entry_model.get("training_report"))
    return d, report


def _manifest_median(manifest: dict):
    """The entry's held-out median score from its manifest, or None. The manifest's exact key names
    are the library's; the usual spellings are accepted."""
    fc = manifest.get("fit_check")  # training/library's manifests: {"entry_heldout_score_median": ...}
    v = fc.get("entry_heldout_score_median") if isinstance(fc, dict) else None
    if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v > 0:
        return float(v)
    for key in _MANIFEST_QUANTILE_KEYS:
        q = manifest.get(key)
        if isinstance(q, dict):
            for k in _MEDIAN_KEYS:
                v = q.get(k)
                if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v > 0:
                    return float(v)
    return None


def fit_check(report: dict, manifest: dict) -> dict:
    """Is the machine just calibrated grossly unlike what the entry expects?

    report: calibrate()'s report. manifest: the entry's manifest.json. Compares the median score of
    the held-out calibration rows (this machine's levels, the entry's network) with the entry's own
    held-out median. Returns {"ok", "ratio", "reason"}; ratio is NaN when it could not be worked
    out, and then ok is False: a check that was not made is never reported as passed.

    ok False means a gross mismatch. ok True does NOT show the machine is of the entry's type: on
    the simulators a server rack passed on a conveyor entry. The rule (ratio <= FIT_MAX_RATIO) is a
    starting value, simulated only; the TRD leaves the rule open."""
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
    limit = f"limit {FIT_MAX_RATIO:g}, a starting value, simulated only"
    if ratio > FIT_MAX_RATIO:
        return no(f"this machine's normal scores {ratio:.1f} times the entry's own ({limit}): it does not look "
                  f"like what the entry expects; try generic_rotating or a long stay", ratio)
    return {"ok": True, "ratio": ratio,
            "reason": f"no gross mismatch: this machine's normal scores {ratio:.1f} times the entry's own ({limit}). "
                      f"This does not show the machine is of the entry's type"}
