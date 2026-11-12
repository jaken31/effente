#!/usr/bin/env python3
"""Train a machine's model from its learn-mode recording and write one model file.

    python3 -m training.model.train --run data/fake/conveyor-2wk-faults --learn-days 7 --out models/conveyor.json
    python3 -m training.model.train --rows data/real/nasa-ims/test2_bearing1.npz --learn-hours 72 --out models/ims.json

What it does:
  1. Rows: the per-minute medians of a learn-mode folder (`--run`), or any rows file with t, X and
     feature_names (`--rows`). Running rows only.
  2. Split by time: the last running part (`--calib-share`, default 15 %) is held out for the
     threshold; never a random split, since neighbouring rows are near copies.
  3. Prepare (effente.model_input.Prep) and learn the normal model: a small denoising autoencoder.
  4. Whole-number weights (int8 with a scale factor per layer) and a check that the NumPy runtime
     on the device (effente.model_runtime) gives the same scores as the trained network.
  5. Threshold: from the held-out scores, smoothed over `--smooth` minutes, for one false alarm
     a week (gamma fit), never below the highest held-out score.
  6. Fault-type classifier: learns the general fault types (effente.fault_types) from fault
     patterns injected into this machine's own normal residuals. Until real labelled faults exist
     it learns what the signatures say, nothing more (stated in the model file).
  7. One JSON model file: weights and scales, normalisation statistics, threshold, fault types and
     the feature table. The device loads it with training/model/effente_loader.py.
No model is trained on the device. The numbers are starting values (unverified on real machines).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from effente.trainer import load_for_training, train_model


def train(t, X, names, smooth=5, calib_share=0.15, rows_per_week=None, seed=0, source="", period=None):
    """The shared trainer (effente.trainer, NumPy only), the same one the device runs."""
    return train_model(t, X, names, period=period, smooth=smooth, calib_share=calib_share, seed=seed, source=source)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--run", help="learn-mode recording folder (its per-minute medians if a day or more, "
                                   "else its one-a-second rows)")
    src.add_argument("--rows", help="a rows file with t, X and feature_names (any cadence)")
    ap.add_argument("--learn-days", type=float, help="use only the first N days of --run")
    ap.add_argument("--learn-hours", type=float, help="use only the first N hours of --rows")
    ap.add_argument("--smooth", type=int, default=5, help="rows of persistence before an alarm")
    ap.add_argument("--calib-share", type=float, default=0.15)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    period = None
    if args.run:
        t, X, names, period = load_for_training(args.run)
        if args.learn_days:
            keep = t < t.min() + args.learn_days * 86400
            t, X = t[keep], X[keep]
    else:
        z = np.load(args.rows)
        t, X, names = z["t"].astype(float), z["X"], [str(n) for n in z["feature_names"]]
        if args.learn_hours:
            keep = t < t.min() + args.learn_hours * 3600
            t, X = t[keep], X[keep]
    if args.rows:  # rows at their own cadence (one every 10 minutes for the NASA bearing): keep it
        period = max(1, int(round(float(np.median(np.diff(np.sort(t)))))))
    d, report = train(t, X, names, args.smooth, args.calib_share, source=args.run or args.rows, period=period)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(d))
    print(f"wrote {out} ({out.stat().st_size / 1e3:.0f} kB)")
    for k, v in report.items():
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
