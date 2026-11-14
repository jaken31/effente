#!/usr/bin/env python3
"""Check 7. NASA IMS run to failure with the PCA default: false alarms after learning, and the hours
between the first alarm and the failure. MPU6050-like conversion.

    python3 -m training.validation.check_ims --rows training/validation/data/nasa-ims/test2_bearing1.npz

The data is public and is not in the repo. To get the rows file (about 1 GB to download; needs 7z):

    mkdir -p training/validation/data/nasa-ims && cd training/validation/data/nasa-ims
    curl -L -o bearings.zip "https://phm-datasets.s3.amazonaws.com/NASA/4.+Bearings.zip"
    7z x bearings.zip && 7z x "4. Bearings/IMS.7z" 2nd_test.rar && 7z x 2nd_test.rar && cd -
    python3 -m training.record.datasets.ims training/validation/data/nasa-ims/2nd_test --bearing 1 \\
        --out training/validation/data/nasa-ims/test2_bearing1.npz

That conversion (track 2's, training/record/datasets/ims.py) turns each 20 kHz snapshot of test 2,
bearing 1 (984 snapshots, one every 10 minutes, until the outer race failed) into what an MPU6050
at 1 kHz would have recorded, with that sensor's noise, then effente's features. The device now
uses an MPU-6500, so every number here is labelled "MPU6050-like conversion".

What this check does with the rows:
  1. Learn: `python3 -m training.model.train --rows ... --learn-hours 72 --smooth 3`, the command
     in training/model/README.md, with the trainer's default normal model (PCA).
  2. Watch: the device runtime judges every snapshot after the learning hours.
  3. The failure is the last snapshot. The last alarm stretch is the one counted as the warning;
     every alarm stretch that ended before it began is a false alarm.

Without the rows file the result says "not run" and why; no number is written.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

import numpy as np

from effente import trainer as TR
from training.anomaly.bench import episodes
from training.model import train as train_cli
from training.validation import common as C

ROWS = "training/validation/data/nasa-ims/test2_bearing1.npz"
SOURCE = ("NASA IMS bearing data set, test 2, bearing 1 (phm-datasets.s3.amazonaws.com/NASA/4.+Bearings.zip), "
          "converted by training/record/datasets/ims.py --seed 0")
LABEL = "public, MPU6050-like conversion"


def run_outcome(hours, alarm, end_h: float) -> dict:
    """From the alarm flag of each judged snapshot (hours since the run began): the alarm stretches,
    how many ended before the last one began, and how long before the end the alarms came."""
    hours, alarm = np.asarray(hours, float), np.asarray(alarm, bool)
    eps = episodes(alarm)
    out = {"snapshots_judged": int(len(hours)), "alarm_stretches": len(eps), "false_alarm_stretches": max(len(eps) - 1, 0),
           "first_alarm_hours_before_failure": None, "last_stretch_hours_before_failure": None,
           "last_stretch_reaches_the_end": None, "quiet_hours_before_first_alarm": None}
    if eps:
        out.update(first_alarm_hours_before_failure=round(end_h - float(hours[eps[0][0]]), 2),
                   last_stretch_hours_before_failure=round(end_h - float(hours[eps[-1][0]]), 2),
                   last_stretch_reaches_the_end=bool(eps[-1][1] == len(alarm)),
                   quiet_hours_before_first_alarm=round(float(hours[eps[0][0]] - hours[0]), 2))
    return out


def judge(rows_path, learn_hours: float, smooth: int) -> dict:
    with np.load(rows_path) as z:
        t, X, names = z["t"].astype(float), z["X"], [str(n) for n in z["feature_names"]]
    with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
        model_path = Path(tmp) / "ims.json"
        train_cli.main(["--rows", str(rows_path), "--learn-hours", str(learn_hours), "--smooth", str(smooth),
                        "--out", str(model_path)])
        model = json.loads(model_path.read_text())
    X = TR.to_current(X, names)
    after = t >= t.min() + learn_hours * 3600
    w = C.watch(model, t[after], X[after])
    period = float(model["period_s"])
    at = {float(np.floor(v / period) * period): float(v) for v in t}  # period start -> its snapshot's time
    hours = np.array([(at[p] - t.min()) / 3600 for p in w["t"]])
    end_h = float(t.max() - t.min()) / 3600
    out = run_outcome(hours, w["alarm"], end_h)
    first = int(np.argmax(w["alarm"])) if w["alarm"].any() else None
    out.update(run_hours=round(end_h, 2), learn_hours=learn_hours, smooth=smooth, period_s=period,
               normal_model=model["normal_model"], hours_watched=round(end_h - float(hours[0]), 2),
               named_at_first_alarm=None if first is None else w["diagnosis_this_period"][first],
               training_report=model["training_report"])
    return out


def rows_of(d: dict) -> list[dict]:
    n = f"{d['snapshots_judged']} snapshots, {d['hours_watched']} h watched after {d['learn_hours']:g} h of learning"
    e = "run to failure"
    rows = [{"entry": e, "metric": "alarm stretches after learning that ended before the last one began (false alarms)",
             "value": d["false_alarm_stretches"], "n": n},
            {"entry": e, "metric": "hours from the first alarm after learning to the failure",
             "value": d["first_alarm_hours_before_failure"], "n": n},
            {"entry": e, "metric": "hours from the start of the last alarm stretch to the failure",
             "value": d["last_stretch_hours_before_failure"], "n": n},
            {"entry": e, "metric": "hours watched after learning before the first alarm",
             "value": d["quiet_hours_before_first_alarm"], "n": n}]
    return [dict(r, metric=r["metric"] + " (MPU6050-like conversion)") for r in rows]


def main(argv=None) -> int:
    def extra(ap):
        ap.add_argument("--rows", default=ROWS, help="the converted rows file (see the notes above)")
        ap.add_argument("--learn-hours", type=float, default=72.0)
        ap.add_argument("--smooth", type=int, default=3, help="snapshots of persistence before an alarm")
    args = C.args_for(__doc__, argv, extra)
    cmd = f"python3 -m training.validation.check_ims --rows {args.rows} --learn-hours {args.learn_hours:g} --smooth {args.smooth}"
    common = dict(command=cmd, seeds=[0], data_source=SOURCE, label=LABEL, uses_library=False)
    if not Path(args.rows).exists():
        C.write_result("ims", args.out, [], status="not run", **common,
                       reason=f"{args.rows} is not there; the download and conversion commands are in "
                              f"training/validation/check_ims.py")
        return 0
    d = judge(args.rows, args.learn_hours, args.smooth)
    C.write_result("ims", args.out, rows_of(d), detail=[d], **common)
    return 0


if __name__ == "__main__":
    sys.exit(main())
