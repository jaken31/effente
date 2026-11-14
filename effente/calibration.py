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
  with the entry as guard  - spreads: never below the entry's own spreads (refit_prep)
                           - readings this machine did not give while calibrating are not judged
                             (if it gave none at all, calibration is refused)

The alarm threshold is this machine's own:

    threshold = threshold_median_multiple x median(held-out smoothed scores)

The held-out scores are the last 15 % of the running rows, scored with the calibrated model
and smoothed as the runtime smooths before it compares with the threshold (the median of the last
smooth_minutes scores). There is no gamma fit and no floor or ceiling from the entry: the entry's
own threshold plays no part. The multiple (THRESHOLD_MEDIAN_MULTIPLE = 5.0) is PROVISIONAL and
simulated only; the measurements behind it are in training/calibration/README.md. Unlike the
trainer's rule, the threshold can be below the highest held-out score; the report says when.

Only the model file is read, never the manifest. The library's entries (training/library) keep
their calibration.widen factors and calibration.hold list in manifest.json, so neither is applied
to them: the entry's spreads are the floor, and the clock, trend and IMU temperature readings are
fitted and judged like any other. A model file that itself carries a "calibration" key with widen
or hold is honoured (_entry_settings); no entry on master does.

Calibration needs 5 minutes of running at the least (min_running_s). Ask for 10.

It follows effente.trainer.train_model's steps and reuses its parts, so a calibrated model
and one trained on the machine alone differ in where the network came from and in the threshold
rule: running rows only, per-period medians, the last 15 % held out for the threshold (never a
random split; counted in running rows, which without a stop in between is the trainer's split by
time).

    model, report = calibrate(entry_model, t, X, names)
    verdict = fit_check(report, manifest)   # a gross mismatch with the entry, or not

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

MIN_RUNNING_S = 300  # the hard minimum (the trainer's 5 minutes). Ask people for 10 minutes: RECOMMENDED_S
RECOMMENDED_S = 600
MIN_ROWS = 60  # below this there are too few held-out rows to set a threshold at all, whatever min_running_s says
# Calibration is refused when fewer of the entry's readings than this were measured (a dead or
# unplugged sensor): the model would judge nothing and could never alarm. One is the least that
# means anything; how many readings an entry needs to be useful is not known (unverified).
MIN_READINGS = 1
# The alarm threshold is this many times the median of this machine's own held-out smoothed scores.
# PROVISIONAL, simulated only: 5.0 is a placeholder until it is set from measurements.
THRESHOLD_MEDIAN_MULTIPLE = 5.0
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


def _number(v) -> bool:
    """A real number (Python's or NumPy's), not a bool; it may still be NaN or infinite."""
    return isinstance(v, (int, float, np.integer, np.floating)) and not isinstance(v, (bool, np.bool_))


def _entry_settings(entry_model: dict, columns: list[str]):
    """What the entry itself says about calibrating it: (widen factors or None, held mask).

    A model file may carry "calibration": {"widen": one factor per reading, "hold": names or prefixes
    ending in "_"}. widen multiplies the spread fitted here; hold names readings that are not judged.
    The library's entries on master do not carry it (theirs is in manifest.json, which calibrate
    never reads), so for them this returns no widening and nothing held, and the entry's own spreads
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
              threshold_median_multiple: float = THRESHOLD_MEDIAN_MULTIPLE):
    """Fit a library entry's model to this machine from feature rows of it running.

    entry_model: the entry's effente-model-1 dict (not changed). t, X, names: one-a-second feature
    rows with their times and feature names, as for effente.trainer.train_model. Returns
    (model file dict, report). Raises ValueError if the entry is not a model this device can run or
    is itself a calibrated model, a time is not a finite number,
    the machine ran for less than min_running_s seconds in the rows given, no reading the entry
    uses was measured, or no threshold can be
    set from the held-out rows (fewer of them than the entry's smoothing window, or a median score
    that is zero or not a number).
    threshold_median_multiple: the alarm threshold is this many times the median of the held-out
    smoothed scores (THRESHOLD_MEDIAN_MULTIPLE, provisional)."""
    if not (_number(threshold_median_multiple) and math.isfinite(threshold_median_multiple)
            and threshold_median_multiple > 0):
        raise ValueError("threshold_median_multiple must be a number above 0")
    if not (_number(min_running_s) and math.isfinite(min_running_s) and min_running_s >= 0):
        raise ValueError("min_running_s must be a number of seconds, 0 or more")
    report_in = entry_model.get("training_report") if isinstance(entry_model, dict) else None
    if isinstance(report_in, dict) and report_in.get("calibrated"):
        # Its "not judged" readings carry a huge spread as if it were the entry's, so a second
        # calibration would keep them unjudged for good and report nothing missing.
        raise ValueError("this model is already calibrated to a machine; calibrate the library entry it came from")
    entry = RT.Model(entry_model)  # refuses another format or another feature list
    X = TR.to_current(X, names)
    names = list(F.FEATURE_NAMES)
    t = np.asarray(t, float)
    if len(t) != len(X):
        raise ValueError(f"{len(t)} times for {len(X)} rows")
    if not np.isfinite(t).all():
        raise ValueError(f"{int((~np.isfinite(t)).sum())} of the {len(t)} times are not finite numbers")
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
                         f"calibration needs at least {math.ceil(need)} s, and 10 minutes is asked for")
    baseline = SF.learn_baseline(X, names, running=ran)
    # The last 15 % of the running rows are held out, counted in rows and not in clock time: a stop
    # in the middle of the calibration must not leave a handful of rows to fit on. Without a gap in
    # the running this is the trainer's split by time. MIN_ROWS keeps at least 51 rows on the fit side.
    n_fit = math.ceil((len(t) - 1) * (1 - CALIB_SHARE))
    fit = np.arange(len(t)) < n_fit
    held = ~fit

    s_before = entry.score(X)[0]  # this machine through the entry as shipped, for the report only
    widen, held_cols = _entry_settings(entry_model, entry.prep.columns)
    prep, fell_back = refit_prep(entry.prep, X[fit], widen, held_cols)
    judged = len(prep.columns) - len(fell_back["missing"]) - len(fell_back["held"])
    if judged < MIN_READINGS:
        raise ValueError(f"no reading the entry uses was measured while calibrating ({len(fell_back['missing'])} of "
                         f"{len(prep.columns)} missing, {len(fell_back['held'])} held): nothing could alarm; check the sensor")
    d = copy.deepcopy(entry_model)
    d.update(created=datetime.now(timezone.utc).isoformat(timespec="seconds"), prep=prep.to_dict(),
             period_s=int(period), baseline=baseline)
    if "source" in d:  # the library marks its files "entry"; this one no longer is
        d["source"] = "calibrated"
    model = RT.Model(d)
    s, r, Z = model.score(X)
    scale = np.std(r[fit], 0)
    d["residual_scale"] = [float(v) for v in np.where(scale > 1e-4, scale, 1e-4)]
    # The threshold: threshold_median_multiple times the median of this machine's own held-out scores,
    # smoothed as the runtime smooths them before comparing with the threshold (Watcher: the median of
    # the last `smooth` scores, fewer at the start; effente.trainer._causal_median is the same thing).
    # No gamma fit, no floor and no ceiling from the entry.
    if int(held.sum()) < model.smooth:
        raise ValueError(f"too little recorded: {int(held.sum())} held-out rows, fewer than the entry's smoothing "
                         f"window of {model.smooth}; calibrate for longer")
    smoothed = TR._causal_median(s[held], model.smooth)
    median = float(np.median(smoothed))
    if not (math.isfinite(median) and median > 0):
        raise ValueError(f"no threshold can be set: the median held-out score is {median}; check the sensor "
                         f"and calibrate again")
    d["threshold"] = float(threshold_median_multiple) * median
    model = RT.Model(d)

    # The fault classifier, as in full training (this machine's own normal deviations with the fault-type
    # signatures added; no real labelled faults, unverified), for an entry that carries one: the
    # library's entries do. An entry without one gets none, and faults are then named by the
    # signatures alone.
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
              "threshold": round(d["threshold"], 4), "held_out_smoothed_median": round(median, 6),
              "threshold_median_multiple": float(threshold_median_multiple),
              "held_out_smoothed_max": round(float(np.max(smoothed)), 6),
              "threshold_below_highest_held_out": bool(d["threshold"] < float(np.max(smoothed))),
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
