"""The library and calibration code under test: track 3's, behind the few names the checks use.

  library()                         -> {entry name: effente-model-1 dict}, the committed entries
  calibrate(entry, t, X, names)     -> (model file dict, report)        effente.calibration.calibrate
  fit_check(entry, t, X, names)     -> {"passes", "score_over_entry_line", "score_over_entry_heldout_median"}

This is the only module that imports track 3's code, so a change of its layout is a change here.

Two things here are the harness's own, not track 3's, and are marked as such in every result:
  - MIN_CALIB_ROWS. effente.calibration refuses under 300 rows (5 minutes) unless told otherwise.
    The calibration-length check exists to measure shorter ones, so calibrate() passes this floor.
  - The fit check's pass rule. Track 3 leaves it open (the entry manifests say "rule": "open") and
    gives only the numbers: the calibration rows' median score over the entry's threshold, and the
    entry's own held-out median score. FIT_RULE below is a placeholder so that check 6 can count
    passes and fails: a machine passes if its median calibration score is not over the entry's
    threshold. Unverified; set the real rule from recordings.
"""

from __future__ import annotations

from pathlib import Path

from effente import calibration as CAL
from training.library import simulators as SIM
from training.library.catalog import load_entry

IS_STANDIN = False
NAME = "training/library/entries (track 3) calibrated by effente.calibration.calibrate (track 3)"
ENTRIES = ("conveyor", "generic_rotating")
ENTRIES_DIR = Path(__file__).resolve().parents[1] / "library" / "entries"
MIN_CALIB_ROWS = 20
FIT_RULE = "placeholder pass rule (track 3 leaves it open): median calibration score not over the entry's threshold"
# The simulated machines the entries were built from (their seeds): never tested on.
TRAINING_SEEDS = {"conveyor": sorted(c.seed for c in SIM.CONVEYORS), "server": [SIM.SERVER_RACK.seed]}


def library() -> dict:
    """The newest version of each committed entry (the catalog checks its sha1 and feature list)."""
    return {name: load_entry(ENTRIES_DIR, name)[1] for name in ENTRIES}


def calibrate(entry: dict, t, X, names, seed: int = 0):
    """Raises ValueError if there are fewer than MIN_CALIB_ROWS running rows. `seed` is not used:
    calibration has nothing random in it."""
    return CAL.calibrate(entry, t, X, names, source="training/validation (simulated session)", min_rows=MIN_CALIB_ROWS)


def fit_check(entry: dict, t, X, names) -> dict:
    """The numbers track 3's calibration reports, and the placeholder rule's verdict (FIT_RULE)."""
    _, report = calibrate(entry, t, X, names)
    over = float(report["median_score_over_threshold"])
    out = {"passes": bool(over <= 1.0), "score_over_entry_line": round(over, 4),
           "share_rows_over_entry_line": float(report["share_rows_over_threshold"])}
    held = entry.get("training_report", {}).get("heldout_score_median")  # the library builder writes it
    if held:
        out["score_over_entry_heldout_median"] = round(over * float(report["threshold"]) / float(held), 4)
    return out
