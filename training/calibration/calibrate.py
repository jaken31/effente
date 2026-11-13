#!/usr/bin/env python3
"""Calibrate a library entry to one machine on a laptop and write the model file.

    python3 -m training.calibration.calibrate --entry training/library/entries/conveyor --run <recording folder> --out models/conveyor-07.json
    python3 -m training.calibration.calibrate --entry training/library/entries/conveyor --rows rows.npz --seconds 600 --out models/conveyor-07.json

The same code the device runs (effente.calibration, NumPy only): the entry's network is kept, and
the preparation statistics and alarm threshold are fitted to the machine in the rows given. Then the fit check, against the entry's manifest.json. This is the laptop route of the
TRD's fallback table: calibrate here and copy the model file to the device.

Give it 10 minutes of the machine running. 5 minutes (300 s) is the least it accepts.

--entry is a library entry folder (model.json and manifest.json). --run is a learn-mode recording
folder (its one-a-second rows); --rows is any file with t, X and feature_names.
Exit code: 0 if the fit check passed, 2 if it failed (the model file is still written), 1 if there
was too little running. A pass only means no gross mismatch was found: the rule is a starting value
from simulated machines and does not show the machine is of the entry's type.
The numbers are starting values (unverified on real machines).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

from effente.calibration import MIN_RUNNING_S, RECOMMENDED_S, calibrate, fit_check
from effente.model_input import load_recording


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--entry", required=True, help="library entry folder with model.json and manifest.json")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--run", help="learn-mode recording folder (its one-a-second rows)")
    src.add_argument("--rows", help="a rows file with t, X and feature_names")
    ap.add_argument("--seconds", type=float, help="use only the first N seconds of the rows")
    ap.add_argument("--min-running-s", type=float, default=MIN_RUNNING_S,
                    help=f"refuse less running than this (default {MIN_RUNNING_S}); give it {RECOMMENDED_S} s if you can")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    entry = Path(args.entry)
    entry_model = json.loads((entry / "model.json").read_text())
    manifest = json.loads((entry / "manifest.json").read_text())
    if args.run:
        X, t, names = load_recording(args.run)
    else:
        with np.load(args.rows) as z:
            t, X, names = z["t"].astype(float), z["X"], [str(n) for n in z["feature_names"]]
    if args.seconds:
        keep = t < t.min() + args.seconds
        t, X = t[keep], X[keep]
    try:
        d, report = calibrate(entry_model, t, X, names, min_running_s=args.min_running_s, seed=args.seed)
    except ValueError as e:
        print(f"not calibrated: {e}", file=sys.stderr)
        return 1
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(d))
    print(f"wrote {out} ({out.stat().st_size / 1e3:.0f} kB), calibrated from entry "
          f"{manifest.get('entry_id', entry.name)} version {manifest.get('version', '?')} "
          f"(status: {manifest.get('status', 'unverified')})")
    for k, v in report.items():
        print(f"  {k}: {v}")
    if report["running_s"] < RECOMMENDED_S:
        print(f"note: calibrated on {report['running_s']} s of running; {RECOMMENDED_S} s (10 minutes) is asked for")
    verdict = fit_check(report, manifest)
    print(f"fit check: {'passed' if verdict['ok'] else 'FAILED'} - {verdict['reason']}")
    return 0 if verdict["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
