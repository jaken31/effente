#!/usr/bin/env python3
"""Calibrate a library entry to one machine and write its model file.

    python3 -m training.model.calibrate --entry library/conveyor --run data/rec/conveyor-07 --out models/conveyor-07.json
    python3 -m training.model.calibrate --entry library/conveyor --rows data/rows/conveyor-07.npz --out models/conveyor-07.json

What it does (effente.calibration, the same code the device runs):
  1. Entry: a library entry folder, manifest.json and model.json.
  2. Rows: the one-a-second rows of a learn-mode folder (`--run`), or any rows file with t, X and
     feature_names (`--rows`), as for training.model.train. Running rows only, summarised at the
     entry's period.
  3. Keeps the entry's network. Refits this machine's preparation statistics, residual spreads,
     alarm threshold, fault classifier and safety baseline, the way the trainer fits them.
  4. Fit check: this machine's normal scores against the entry's own held-out scores. If it does not
     fit, the model file is still written and the verdict is printed and kept in its report.
Refuses (exit 1, nothing written) an unusable entry or less running time than `--min-running-s`.
The numbers are starting values (unverified on real machines).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

from effente.calibration import calibrate, load_entry
from effente.model_input import load_recording


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--entry", required=True, help="library entry folder (manifest.json and model.json)")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--run", help="learn-mode recording folder of this machine (its one-a-second rows)")
    src.add_argument("--rows", help="a rows file with t, X and feature_names")
    ap.add_argument("--min-running-s", type=float,
                    help="refuse less running time than this (default: the trainer's minimum, 300 summary periods)")
    ap.add_argument("--calib-share", type=float, default=0.15)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    try:
        entry = load_entry(args.entry)
        if args.run:
            X, t, names = load_recording(args.run)
        else:
            z = np.load(args.rows)
            t, X, names = z["t"].astype(float), z["X"], [str(n) for n in z["feature_names"]]
        d, report = calibrate(entry, t, X, names, min_running_s=args.min_running_s, calib_share=args.calib_share,
                              source=args.run or args.rows)
    except (ValueError, OSError) as e:
        print(f"not calibrated: {e}", file=sys.stderr)
        return 1
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(d))
    print(f"wrote {out} ({out.stat().st_size / 1e3:.0f} kB)")
    for k, v in report.items():
        print(f"  {k}: {v}")
    fc = report["fit_check"]
    print(("fits: " if fc["fits"] else "DOES NOT FIT: ") + fc["reason"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
