"""Calibrate a library entry to one machine, and check the machine fits the entry. NumPy only.

A library entry is a folder with a model file (effente-model-1, trained on a machine type) and a
manifest.json. Calibration is what the device does in the minutes after an entry is picked (TRD,
"Model library and calibration"): it keeps the entry's network and refits what belongs to this
machine, from a few minutes of it running.

Interface

    entry = load_entry(folder)                      # Entry(model, manifest); ValueError if unusable
    d, report = calibrate(entry, t, X, names)       # model file dict and report; ValueError if too short
    report["fit_check"]["fits"]                     # False: the machine does not look like this type

  load_entry(folder) -> Entry
      Reads the folder's manifest.json and model.json (the library's layout). Raises
      ValueError if the folder is not an entry, the model is not an effente-model-1 file on this
      device's feature list, or the manifest has no held-out median score.

  calibrate(entry, t, X, names, min_running_s=None, calib_share=0.15, seed=0, source="",
            progress=None, time_limit_s=None) -> (model file dict, report)
      t, X, names: one-a-second feature rows of this machine, as for effente.trainer.train_model.
      min_running_s: refuse less running time than this. Default: the trainer's own minimum,
      MIN_ROWS summary periods (5 minutes at the 1 s period of a short recording).
      Raises ValueError, with the trainer's message, if the machine ran for less than that.
      The model file dict loads in effente.model_runtime.Model as it is. The entry is not changed.
      A failed fit check does not raise: the calibrated model is still returned, and the caller
      (the device screen, the CLI) says the machine does not look like that type.

  fit_check(scores, held_out_quantiles) -> {"fits", "median_ratio", "max_median_ratio", "reason", "note"}
      The rule below, on any scores from the entry as shipped. calibrate() calls it; it is public
      so the rule can be measured on recordings without calibrating.

  score_quantiles(scores) -> {"p50", "p90", "p99", "max"}
      The numbers an entry's manifest holds, from its model's scores on its own held-out rows.

What is kept and what is refitted

  kept from the entry       the network: the autoencoder layers (PCA patterns) and, for PCA, the
                            scales its score is measured in ("pca_score"); the columns it reads;
                            the summary period and the persistence
  refitted to this machine  the preparation statistics (Prep: median and spread of each reading),
                            the spread of each reading's residual, the alarm threshold, the fault
                            classifier (fault patterns injected into this machine's own normal
                            variation) and the safety baseline

Everything refitted is done by the trainer's own steps (effente.trainer.running_summaries,
time_split and fit_to_machine, which train_model runs too), so a calibrated model and one trained
on the machine alone differ only in where the network came from. The rows are summarised at the
entry's period, because that is what its network was trained on. A reading the entry uses that
this machine did not give while calibrating keeps the entry's median and spread
(effente.model_input.Prep.refit); the report lists them.

The fit check

The TRD leaves the rule open. This is a proposal, with a starting value set on simulated data.

    median_ratio = median score of the calibration rows on the entry as shipped
                   / the entry's own held-out median (manifest.json, "p50")
    fits         = median_ratio <= FIT_MAX_MEDIAN_RATIO

"As shipped" means the entry's own preparation statistics, before anything is refitted: how far
this machine's readings sit from where the entry's machines' readings sat, as the entry's network
scores it. That is the TRD's wording: the calibration data scores far worse on the entry than the
entry's own held-out data did. Why not the calibrated model's scores: they cannot tell machine
types apart. Once every reading is in spreads of its own machine's normal, a server rack scored
0.6 to 0.9 times the conveyor entry's held-out median, the same as another conveyor.
Why the median: a few odd seconds (a dropped tool) decide nothing, and five minutes give a steady
one. Why a ratio: score levels differ between entries.
A machine of the right type also scores above 1, because its levels differ from the entry's
machines'; that is what calibration then removes. The limit sits between the two groups measured
on the simulators (training/model/README.md has the runs). Unverified on real machines.

manifest.json (training/library's layout; the fields read here, it holds more)

    {"entry_id": "conveyor", "version": "0.1.0",
     "heldout_score_quantiles": {"p50": 2.09, "p90": 2.58, "p99": 26.3, "max": 40.0}}

The quantiles are score_quantiles() of the entry model's scores on its own held-out rows (the last
15 % of its training recording, which set its threshold). Only "p50" is needed.

Model file: no format change. The calibrated file has the entry's keys. "trained_on" is the
calibration data, "created" the calibration time, and "training_report" the calibration report,
which names the entry and holds the fit check.
"""

from __future__ import annotations

import copy
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

import numpy as np

from effente import model_runtime as RT
from effente import trainer as TR

# The fit check's rule: this machine's median score on the entry as shipped may be at most this many
# times the entry's own held-out median. On the simulators (training/model/README.md): other
# conveyors 3.7 to 12.9 times, server racks 33.8 to 60.3 times.
FIT_MAX_MEDIAN_RATIO = 20.0  # starting value, set on simulated data

MANIFEST, MODEL = "manifest.json", "model.json"  # an entry folder's two files
QUANTILES = (50, 90, 99)


class Entry(NamedTuple):
    model: dict  # the entry's effente-model-1 model file
    manifest: dict  # its manifest.json


def score_quantiles(scores) -> dict:
    """The fit check numbers of a set of scores: {"p50", "p90", "p99", "max"}."""
    s = np.asarray(scores, float)
    s = s[np.isfinite(s)]
    if not len(s):
        raise ValueError("no scores to take quantiles of")
    return {**{f"p{q}": float(np.percentile(s, q)) for q in QUANTILES}, "max": float(s.max())}


def _held_out_median(held_out_quantiles) -> float:
    """The entry's own held-out median score, from its manifest's quantiles, checked."""
    v = held_out_quantiles.get("p50") if isinstance(held_out_quantiles, dict) else None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0:
        raise ValueError('the entry\'s manifest needs "heldout_score_quantiles" with a "p50" above 0')
    return float(v)


def load_entry(folder) -> Entry:
    """A library entry from its folder: manifest.json and model.json."""
    folder = Path(folder)
    try:
        manifest = json.loads((folder / MANIFEST).read_text())
        model = json.loads((folder / MODEL).read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise ValueError(f"not a library entry: {folder} ({e})") from e
    if not isinstance(manifest, dict):
        raise ValueError(f"not a library entry: {folder} ({MANIFEST} is not an object)")
    _held_out_median(manifest.get("heldout_score_quantiles"))
    RT.Model(model)  # refuses another format or another feature list
    return Entry(model, manifest)


def fit_check(scores, held_out_quantiles: dict) -> dict:
    """Does a machine with these scores on the entry as shipped look like the entry's type?
    scores: the entry's own model on this machine's rows. held_out_quantiles: from its manifest."""
    ratio = score_quantiles(scores)["p50"] / _held_out_median(held_out_quantiles)
    fits = ratio <= FIT_MAX_MEDIAN_RATIO
    return {"fits": bool(fits), "median_ratio": round(ratio, 3), "max_median_ratio": FIT_MAX_MEDIAN_RATIO,
            "reason": (f"this machine scores {ratio:.1f} times the entry's own held-out median on the entry as "
                       f"shipped (limit {FIT_MAX_MEDIAN_RATIO:g}): "
                       + ("it looks like that type" if fits else
                          "it does not look like that type; pick generic_rotating or leave the device on "
                          "for a long stay")),
            "note": "starting value, set on simulated data; unverified on real machines"}


def calibrate(entry: Entry, t, X, names, min_running_s: float | None = None, calib_share: float = 0.15,
              seed: int = 0, source: str = "", progress=None, time_limit_s: float | None = None):
    """Refit a library entry to this machine from one-a-second feature rows of it running.
    Returns (model file dict, report). Raises ValueError if it ran for less than min_running_s."""
    held_out = entry.manifest.get("heldout_score_quantiles")
    _held_out_median(held_out)  # before any work: an entry without it cannot be fit-checked
    d = copy.deepcopy(entry.model)
    shipped = RT.Model(d)  # refuses another format or another feature list
    period = shipped.period  # the entry's network was trained on summaries of this length
    min_rows = TR.MIN_ROWS if min_running_s is None else math.ceil(min_running_s / period)
    t, X, period, baseline = TR.running_summaries(t, X, names, period, min_rows)
    fit, calib = TR.time_split(t, calib_share)
    say = progress or (lambda stage, share: None)
    say("preparing", 0.05)
    fits = fit_check(shipped.score(X)[0], held_out)  # before anything is refitted
    prep = shipped.prep.refit(X[fit])
    # Readings this machine gave no spread for (absent or constant while calibrating): the entry's is kept.
    kept = [c for c, new, old in zip(prep.columns, prep.scale, shipped.prep.scale) if new == old]
    d.update(created=datetime.now(timezone.utc).isoformat(timespec="seconds"), trained_on=source,
             prep=prep.to_dict(), baseline=baseline)
    s, _, machine = TR.fit_to_machine(d, X, fit, calib, seed, time_limit_s, say)
    report = {"calibrated": True, "entry": entry.manifest.get("entry_id"), "entry_version": entry.manifest.get("version"),
              "period_s": int(period), "rows_learned": int(fit.sum()), "rows_calibration": int(calib.sum()),
              "inputs": prep.n_out, "features_kept": len(prep.columns), "readings_not_refitted": kept,
              "normal_model": d.get("normal_model", "autoencoder"), **machine,
              "score_quantiles": score_quantiles(s), "fit_check": fits,
              "dropped_limits": baseline["dropped"], "warnings": baseline["warnings"]}
    d["training_report"] = report
    say("done", 1.0)
    return d, report
