#!/usr/bin/env python3
"""Build model library entries: training/library/entries/<entry_id>/model.json and manifest.json.

    python3 -m training.library.build                       # every entry in training/library/catalog.py
    python3 -m training.library.build generic_rotating --seed 0

model.json is a plain effente-model-1 file, written by effente.trainer.train_model and loaded by
effente.model_runtime.Model. manifest.json says what the entry is: its sources, the scores of its
own held-out rows (the calibration fit check compares a new machine against them), a scorecard per
fault type, its status, how it was built, the model file's sha1, and what calibration needs to
know about the entry's readings (below).

How an entry is built:
  1. Rows. Every training source (training/library/sources.py) gives the one-a-second feature rows
     of one machine on one mount, from the accelerometer, the gyro and the IMU's temperature
     only. Running rows only, as the trainer.
  2. Several machines. Calibration keeps an entry's network and refits only Prep (each reading's
     median and spread) to the machine in front of it, so the network only ever sees how far each
     reading is from THAT machine's normal, in that machine's spreads. It has to be trained on the
     same thing. So each machine's rows are first re-expressed with that machine's own median and
     spread (effente.model_input.Prep, fitted on its training part) on the scale of the first
     source, and only then pooled. Pooling raw rows instead would teach the network
     the differences between machines (one runs faster, one is mounted harder), which calibration
     removes before the network sees anything. One source: nothing is re-expressed.
  3. One clock. Pooled rows are put in the order of how far through its own recording each row
     is, one period apart. The trainer's split by time (the last 15 %) then holds out the last
     15 % of every machine.
  4. effente.trainer.train_model on those rows, with period=1 said outright: calibration lasts
     minutes, so its rows are one-second rows, and the network must have been trained on the
     same kind of row. Left to itself the trainer picks 60-second periods for a recording of
     days (effente.trainer.choose_period), and calibration would then need 5 hours.
  5. Held-out scores: the trained model's scores on the trainer's held-out rows; their p50, p90,
     p99 and max go into the manifest.
  6. Evaluation sources are listed, not run: the build evaluates nothing, and the manifest says
     so for each one ("evaluated": false).
  7. Calibration notes (manifest "calibration"; the model file gets no new key):
       hold   readings a 5-minute calibration cannot refit: the time of day barely moves, the
              trends need minutes to hours of history, and the IMU's temperature is still
              warming up with the device. Name prefixes, or a whole name.
       widen  per reading the entry uses: how much wider its spread is over a whole training
              source than within one calibration-length window (widen_factors). Five minutes see
              too narrow a spread, because the load, the oil and the room barely change in them.
       prep, threshold   what the model file's own Prep and threshold stand for. With several
              machines pooled, Prep holds the FIRST source's medians and spreads (the scale the
              others were re-expressed onto, step 2): its medians are one machine's levels, not a
              type's, and its spreads are in that machine's units. The threshold is the trainer's,
              from the held-out rows of every machine.
     Calibration (effente/calibration.py) decides what to do with these; nothing here applies them.
  8. Scorecard: each fault check in the catalog switches a simulated fault on in the first
     training machine and runs the device's own Watcher. A fault type is "simulated only" if the
     Watcher stayed quiet before the fault and named that type at the end; otherwise "not validated".

Deterministic: the same seed and the same code give the same bytes (on one machine and one NumPy).
To keep it so, the two clock readings train_model puts in a model file are not kept: "created" is
the date of the git commit built from, and the training report's "fit_seconds" is null.

Everything here is from simulated data. Nothing has been checked on a real machine (unverified).
"""

from __future__ import annotations

import os

# One maths thread: the matrices are small, so more threads only fight each other (the classifier
# trained 9 times slower with 16), and a fixed count keeps the build the same from run to run.
for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

import argparse
import json
import subprocess
import sys
import warnings
import hashlib
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from effente import fault_types as FT
from effente import features as F
from effente import model_runtime as RT
from effente import trainer as TR
from effente.model_input import Prep, _transform_raw
from training.library.catalog import ENTRIES, Entry
from training.library.sources import LEAD_IN_S, MPU6500

ROOT = Path(__file__).resolve().parents[2]
ENTRIES_DIR = Path(__file__).resolve().parent / "entries"
PERIOD_S = 1  # seconds per row, the period calibration uses
CALIB_SHARE = 0.15  # the trainer's held-out share
NORMAL_MODEL = "pca"
TARGET_BYTES = 50_000  # the TRD: a model file is about 50 kB
QUIET = ("normal", "warming_up", "stopped")
CALIBRATION_S = 300  # the calibration length the entries are built for (the trainer's 300 rows at 1 s)
HOLD = ("time_", "trend_", "imu_temp_c")  # readings 5 minutes cannot refit: name prefixes, or a whole name
WIDEN_RANGE = (1.0, 5.0)


# ----------------------------------------------------------------- rows

def prepare(rows, period: int = PERIOD_S):
    """A source's rows as the trainer takes them: current feature list, running rows only, in time
    order, per-period medians. (t, X)."""
    X = TR.to_current(rows.X, rows.names)
    t = np.asarray(rows.t, float)
    run = TR.running_rows(X, list(F.FEATURE_NAMES))
    order = np.argsort(t[run], kind="stable")
    return TR.summarise(t[run][order], X[run][order], period)


def held_out(t, calib_share: float = CALIB_SHARE) -> np.ndarray:
    """The trainer's split by time (effente.trainer.train_model): True for the rows it holds out.
    tests/test_library.py proves it is the trainer's, through the threshold."""
    return t >= t[0] + (t[-1] - t[0]) * (1 - calib_share)


def _raw_from_transformed(T: np.ndarray, names) -> np.ndarray:
    """Undo effente.model_input._transform_raw (tests/test_library.py checks the round trip)."""
    X = np.array(T, float)
    for j, n in enumerate(names):
        if "_since_" in n:
            X[:, j] = np.expm1(X[:, j])
        elif "kurt" in n:
            X[:, j] = 10.0 ** X[:, j] - 3.0
    return X


def pool(machines, period: int = PERIOD_S, calib_share: float = CALIB_SHARE):
    """Rows of several machines as one recording for the trainer: (t, X, who, express).

    machines: [(t, X)] from prepare(). who: which machine each pooled row came from. express[m](X)
    puts further rows of machine m (a fault run) on the same scale as its pooled rows. See the
    module notes, steps 2 and 3."""
    if len(machines) == 1:
        t, X = machines[0]
        return t, X, np.zeros(len(t), int), [lambda X: np.asarray(X, float)]
    names = list(F.FEATURE_NAMES)
    who = np.concatenate([np.full(len(t), m) for m, (t, _) in enumerate(machines)])
    through = np.concatenate([(t - t[0]) / max(float(t[-1] - t[0]), 1e-9) for t, _ in machines])
    order = np.lexsort((who, through))  # how far through its own recording, then which machine
    who, X = who[order], np.vstack([X for _, X in machines])[order]
    clock = period * np.arange(len(who), dtype=float)
    fit = ~held_out(clock, calib_share)

    # Each machine's own normal: Prep's median and spread, from its training part.
    preps = [Prep.fit(X[fit & (who == m)], names, running_only=False) for m in range(len(machines))]
    pooled = Prep.fit(X[fit], names, running_only=False)  # every reading that could tell machines apart
    cols = sorted({names.index(c) for p in preps + [pooled] for c in p.columns})
    col_names = [names[j] for j in cols]
    T = _transform_raw(X[:, cols], col_names)
    centre = np.full((len(machines), len(cols)), np.nan)
    scale = np.full((len(machines), len(cols)), np.nan)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # a reading that is all NaN on one machine
        for m, p in enumerate(preps):
            centre[m] = np.nanmedian(T[fit & (who == m)], 0)  # also for readings this machine's Prep left out
            at = [col_names.index(c) for c in p.columns]
            centre[m, at], scale[m, at] = p.centre, p.scale
        # The reference is the first machine (a real set of levels, so the trainer's baseline makes
        # sense); a reading it has no level or spread for takes the median of the machines that do.
        ref_centre = np.where(np.isfinite(centre[0]), centre[0], np.nanmedian(centre, 0))
        ref_scale = np.where(np.isfinite(scale[0]), scale[0], np.nanmedian(scale, 0))

    def express_for(m):
        kept = np.isfinite(scale[m])  # readings with a spread on this machine

        def express(X_m):
            X_m = np.array(X_m, float)
            T_m = _transform_raw(X_m[:, cols], col_names)
            with np.errstate(invalid="ignore"):
                moved = ref_centre + (T_m - centre[m]) * (ref_scale / scale[m])
            # A reading that never changes on this machine (or is mostly missing) sits at the reference's normal.
            out = np.where(kept, moved, np.where(np.isfinite(T_m), ref_centre, np.nan))
            X_m[:, cols] = _raw_from_transformed(out, col_names)
            return X_m
        return express

    express = [express_for(m) for m in range(len(machines))]
    for m in range(len(machines)):
        X[who == m] = express[m](X[who == m])
    return clock, X, who, express


def _spread(T: np.ndarray) -> np.ndarray:
    """Each column's spread as Prep.fit takes it: IQR / 1.349, else the standard deviation, else NaN."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # a reading that is all NaN in one window
        q1, q3 = np.nanpercentile(T, [25, 75], 0)
        std = np.nanstd(T, 0)
    spread = (q3 - q1) / 1.349
    return np.where(spread > 1e-9, spread, np.where(std > 1e-9, std, np.nan))


def widen_factors(machines, columns, window: int = CALIBRATION_S // PERIOD_S) -> dict:
    """{reading: factor} for the readings the entry uses: the median, over every `window`-row window
    of every machine, of (spread over the whole machine / spread within the window), kept within
    WIDEN_RANGE. 1.0 where no window has a spread. machines: [(t, X)] from prepare()."""
    names = list(F.FEATURE_NAMES)
    at = [names.index(c) for c in columns]
    ratios = []
    for _, X in machines:
        T = _transform_raw(X[:, at], columns)
        whole = _spread(T)
        ratios += [whole / _spread(T[a:a + window]) for a in range(0, len(T) - window + 1, window)]
    if not ratios:
        return {c: 1.0 for c in columns}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        r = np.nanmedian(np.array(ratios), 0)
    r = np.clip(np.where(np.isfinite(r), r, 1.0), *WIDEN_RANGE)
    return {c: round(float(v), 3) for c, v in zip(columns, r)}


# ----------------------------------------------------------------- scorecard

def check_fault(model: RT.Model, t, X, expects: str) -> dict:
    """Run the device's Watcher over a fault run (LEAD_IN_S of normal, then the fault)."""
    watcher = RT.Watcher(model)
    onset = float(t[0]) + LEAD_IN_S
    early, late, level = [], [], 0.0
    for ti, x in zip(t, X):
        state = watcher.predict_at(float(ti), x)
        (early if ti < onset else late).append(state["diagnosis"])
        if ti >= onset:
            level = max(level, state.get("unknown_score") or 0.0)
    quiet_before = all(d in QUIET for d in early)
    return {"expects": expects, "quiet_before": quiet_before, "said_at_end": late[-1] if late else None,
            "highest_level": level,  # the smoothed score over the alarm line: above 1 is an alarm
            "passed": bool(quiet_before and late and late[-1] == expects)}


def scorecard(checks: dict) -> dict:
    """Per fault type: "simulated only" if one of its simulated checks passed, else "not validated".
    "validated on <data>" needs device recordings of real faults; there are none yet."""
    card = {name: "not validated" for name in FT.NAMES}
    for c in checks.values():
        if c["passed"]:
            card[c["expects"]] = "simulated only"
    return card


# ----------------------------------------------------------------- build

def _git() -> tuple[str, str | None]:
    """(commit built from, with "-dirty" if the code differs from it; that commit's date)."""
    def run(*args):
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    try:
        commit = run("rev-parse", "HEAD")
        when = datetime.fromtimestamp(int(run("show", "-s", "--format=%ct", "HEAD")), timezone.utc)
        # Tracked code only: a new file matters only once a tracked one imports it, and that shows here.
        dirty = run("status", "--porcelain", "--untracked-files=no", "--", "effente", "device/intake",
                    "training/library", ":(exclude)training/library/entries")
    except (OSError, subprocess.CalledProcessError, ValueError):
        return "unknown", None
    return commit + ("-dirty" if dirty else ""), when.isoformat(timespec="seconds")


def _load(job):
    source, seed, faults = job
    return source.load_rows(seed, faults)


def build(entry: Entry, seed: int = 0, out_dir=ENTRIES_DIR, jobs: int | None = None) -> dict:
    """Build one entry into out_dir/<entry_id>/. Returns what was built: the model, the manifest,
    the fault checks behind the scorecard, the file paths and the model file's size."""
    training = [s for s in entry.sources if s.used_for == "training"]
    if not training:
        raise ValueError(f"{entry.entry_id} has no training source")
    for s in training:  # the TRD: never mix MPU6050 data into MPU-6500 training data
        if s.sensor != MPU6500:
            raise ValueError(f"{s.name} is not MPU-6500 data ({s.sensor}): it may be used for evaluation only")
    for c in entry.fault_checks:
        if c.expects not in FT.NAMES:
            raise ValueError(f"fault check {c.fault}: effente.fault_types has no type {c.expects!r}")
    checks = entry.fault_checks if training[0].can_fault else ()  # a recording cannot have a fault switched on
    work = [(s, seed, tuple(c.fault for c in checks) if i == 0 else ()) for i, s in enumerate(training)]
    if len(work) > 1 and (jobs is None or jobs > 1):
        with ProcessPoolExecutor(max_workers=jobs) as ex:  # each machine is seeded on its own: order-free
            loaded = list(ex.map(_load, work))
    else:
        loaded = [_load(w) for w in work]

    machines = [prepare(r) for r in loaded]
    rows_per_machine = [len(r.t) for r in loaded]
    for r in loaded:  # days of one-second rows: keep the prepared copy only
        r.t = r.X = None
    t, X, _, express = pool(machines)
    names = list(F.FEATURE_NAMES)
    d, report = TR.train_model(t, X, names, period=PERIOD_S, calib_share=CALIB_SHARE, seed=seed,
                               source=f"library entry {entry.entry_id} {entry.version}", normal_model=NORMAL_MODEL)
    commit, commit_date = _git()
    if commit_date:
        d["created"] = commit_date
    d["training_report"]["fit_seconds"] = None
    model = RT.Model(d)

    held = model.score(X)[0][held_out(t)]  # every row scored together, then split: as the trainer does
    quantiles = {"p50": float(np.percentile(held, 50)), "p90": float(np.percentile(held, 90)),
                 "p99": float(np.percentile(held, 99)), "max": float(held.max())}
    results = {}
    for c in checks:  # the first training machine, a little later, with the fault switched on
        t_fault, X_fault = loaded[0].faults[c.fault]
        results[c.fault] = check_fault(model, t_fault, express[0](TR.to_current(X_fault, loaded[0].names)), c.expects)
    raw = json.dumps(d, separators=(",", ":")).encode()  # the trainer's dict, with no spaces: a quarter smaller
    manifest = {
        "entry_id": entry.entry_id,
        "version": entry.version,
        "normal_model": d["normal_model"],
        "features_version": d["features_version"],
        # The build runs no evaluation: every evaluation source says so.
        "sources": [{"name": s.name, "kind": s.kind, "used_for": s.used_for}
                    | ({} if s.used_for == "training" else {"evaluated": False})
                    | ({"note": s.note} if getattr(s, "note", "") else {}) for s in entry.sources],
        "heldout_score_quantiles": quantiles,
        "scorecard": scorecard(results),
        "status": entry.status,
        "built": {"command": f"python3 -m training.library.build {entry.entry_id} --seed {seed}", "seed": seed,
                  "git_commit": commit},
        "model_sha1": hashlib.sha1(raw).hexdigest(),
        "calibration": {
            "min_s": CALIBRATION_S, "hold": list(HOLD), "widen": widen_factors(machines, d["prep"]["columns"]),
            # What the model file's own numbers stand for, for anything that uses them as a floor.
            "prep": (f"the medians and spreads of {training[0].name}, the first training source; the other "
                     f"{len(training) - 1} were re-expressed onto them before pooling" if len(training) > 1 else
                     f"the medians and spreads of {training[0].name}, the only training source"),
            "threshold": (f"from the held-out rows (the last {CALIB_SHARE:.0%} by time) of "
                          + ("every training source" if len(training) > 1 else "the training source")
                          + ", scored together"),
        },
    }
    folder = Path(out_dir) / entry.entry_id
    folder.mkdir(parents=True, exist_ok=True)
    model_path, manifest_path = folder / "model.json", folder / "manifest.json"
    model_path.write_bytes(raw)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    return {"model": d, "manifest": manifest, "report": report, "fault_checks": results,
            "rows_per_machine": rows_per_machine, "held_out_rows": int(len(held)),
            "model_path": model_path, "manifest_path": manifest_path, "model_bytes": model_path.stat().st_size}


def describe(built: dict) -> str:
    """What a build made, for the terminal: sizes, held-out scores, the scorecard's checks."""
    m, rep = built["manifest"], built["report"]
    size = built["model_bytes"]
    lines = [f"{m['entry_id']} {m['version']} ({m['status']})",
             f"  model.json: {size} bytes ({size / 1e3:.1f} kB; target about {TARGET_BYTES // 1000} kB)",
             f"  rows: {built['rows_per_machine']} per training machine; {rep['rows_learned']} learned, "
             f"{rep['rows_calibration']} held out; {rep['inputs']} inputs, {rep['patterns_kept']} patterns",
             f"  held-out scores ({' and '.join(sorted({s['kind'] for s in m['sources'] if s['used_for'] == 'training'}))} "
             "data): " + ", ".join(f"{k} {v:.3f}" for k, v in m["heldout_score_quantiles"].items())]
    for fault, c in built["fault_checks"].items():
        lines.append(f"  simulated {fault}: expects {c['expects']}, {'passed' if c['passed'] else 'failed'} "
                     f"(said at the end: {c['said_at_end']}; score reached {c['highest_level']:.2f} of the alarm line; "
                     f"quiet before the fault: {'yes' if c['quiet_before'] else 'no'})")
    counts = Counter(m["scorecard"].values())
    lines.append("  scorecard: " + ", ".join(f"{n} {k}" for k, n in sorted(counts.items())))
    for w in rep["warnings"]:
        lines.append(f"  trainer warning: {w}")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("entries", nargs="*", help=f"entries to build (default all: {', '.join(ENTRIES)})")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default=str(ENTRIES_DIR), help="folder that holds one folder per entry")
    ap.add_argument("--jobs", type=int, help="processes for simulating machines (default: one per core)")
    args = ap.parse_args(argv)
    for name in args.entries:
        if name not in ENTRIES:
            ap.error(f"no entry {name!r}; the catalog has {', '.join(ENTRIES)}")
    for name in args.entries or list(ENTRIES):
        print(describe(build(ENTRIES[name], args.seed, args.out, args.jobs)), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
