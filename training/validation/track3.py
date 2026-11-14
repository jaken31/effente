"""The library and calibration code under test: track 3's, behind the few names the checks use.

  library()                                   -> {entry name: effente-model-1 dict}, the committed entries
  manifest(name)                              -> that entry's manifest.json
  calibrate(entry, t, X, names)               -> (model file dict, report)   effente.calibration.calibrate
  fit_check(entry, manifest, t, X, names)     -> {"passes", "reason", "score_over_entry_heldout_median",
                                                  "score_over_entry_line"}   effente.calibration.fit_check

This is the only module that imports track 3's code, so a change of its layout is a change here.

Two things here are the harness's own choices, not track 3's defaults:
  - MIN_RUNNING_S. effente.calibration asks for 300 s of running unless told otherwise. The
    calibration-length check exists to measure shorter ones, so calibrate() asks for none and
    calibration's own hard floor is what refuses (effente.calibration.MIN_ROWS summary periods,
    and a held-out part no shorter than the entry's smoothing window).
  - score_over_entry_line. The fit check does not use it; it is reported next to the fit check's
    own ratio because it is the other number a rule could be set from: the calibration rows'
    median score on the entry as shipped, over the entry's threshold.

The fit check's pass rule is track 3's (effente.calibration.FIT_MAX_RATIO). It is a starting value
set on the simulators and the TRD leaves the rule open, so every pass or fail share is marked with
FIT_RULE.
"""

from __future__ import annotations

from pathlib import Path

from effente import calibration as CAL
from training.library import catalog as CAT
from training.library.load import load_entry
from training.library.sources import SimConveyor, SimServerRack

IS_STANDIN = False
NAME = "training/library/entries (track 3) calibrated by effente.calibration.calibrate (track 3)"
ENTRIES = ("conveyor", "generic_rotating")
ENTRIES_DIR = Path(__file__).resolve().parents[1] / "library" / "entries"
MIN_RUNNING_S = 0
FIT_RULE = (f"track 3's starting rule (effente.calibration.FIT_MAX_RATIO = {CAL.FIT_MAX_RATIO:g}), set on the "
            f"simulators only; the TRD leaves the rule open")


def library() -> dict:
    """The committed entries' model files (load_entry checks each one's sha1 and feature list)."""
    return {name: load_entry(ENTRIES_DIR, name)[1] for name in ENTRIES}


def manifest(name: str) -> dict:
    return load_entry(ENTRIES_DIR, name)[0]


def _training_seeds() -> dict:
    """The simulator seeds of the machines the entries were trained on, by the harness's machine
    names. A library source's simulator seed is its own seed plus 1000 times the build's seed
    (training/library/sources.py). Evaluation-only sources were never trained on, so they are not
    listed: no entry was trained on a server rack."""
    seeds = {"conveyor": set(), "server": set()}
    for name in ENTRIES:
        built = int(manifest(name)["built"]["seed"])
        for src in CAT.ENTRIES[name].sources:
            kind = "conveyor" if isinstance(src, SimConveyor) else "server" if isinstance(src, SimServerRack) else None
            if kind and src.used_for == "training":
                seeds[kind].add(src.seed + 1000 * built)
    return {kind: sorted(s) for kind, s in seeds.items()}


# The simulated machines the entries were built from: never tested on.
TRAINING_SEEDS = _training_seeds()


def calibrate(entry: dict, t, X, names, seed: int = 0):
    """Raises ValueError if calibration refuses the rows (too little running, or no threshold can
    be set). `seed` is for the fault classifier calibration fits."""
    return CAL.calibrate(entry, t, X, names, min_running_s=MIN_RUNNING_S, seed=seed)


def fit_check(entry: dict, manifest: dict, t, X, names) -> dict:
    """Track 3's fit check of these calibration rows against the entry, with the numbers behind it.
    A check that could not be made does not pass, and `reason` says why."""
    _, report = calibrate(entry, t, X, names)
    verdict = CAL.fit_check(report, manifest)
    out = {"passes": bool(verdict["ok"]), "reason": verdict["reason"]}
    if verdict["ratio"] == verdict["ratio"]:  # not NaN: the ratio could be worked out
        out["score_over_entry_heldout_median"] = round(float(verdict["ratio"]), 4)
    before = (report.get("score_quantiles_before_calibration") or {}).get("p50")
    if before is not None and report.get("entry_threshold"):
        out["score_over_entry_line"] = round(float(before) / float(report["entry_threshold"]), 4)
    return out
