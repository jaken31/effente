#!/usr/bin/env python3
"""Real bearing wear (NASA / University of Cincinnati IMS, test 2) -> effente vibration features.

Four bearings on one shaft at 2,000 rpm under 6,000 lb, run for a week (12-19 February 2004) until
bearing 1's outer race failed. Every 10 minutes a 1 s snapshot of each bearing's accelerometer was
saved at 20 kHz (984 snapshots). This turns each snapshot into what OUR device would have seen on
that bearing: low-passed and resampled to 1 kHz like the MPU6050, with the MPU6050's own noise
(about 400 ug/sqrt(Hz)) and gravity added, then effente's per-window vibration features. Only
vibration: the test has no sound, heat or room data.

    python3 -m training.record.datasets.ims data/real/nasa-ims/test2/2nd_test --bearing 1 \\
        --out data/real/nasa-ims/test2_bearing1.npz

Source: NASA Prognostics Data Repository, "Bearing Data Set", IMS, University of Cincinnati
(Qiu et al., J. Sound and Vibration 289, 2006). No licence stated; cite the source.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from scipy import signal

from effente import features as F

RATE_IN, RATE_OUT = 20480.0, 1000.0  # snapshot rate; our accelerometer
MPU_NOISE_G = 400e-6 * np.sqrt(RATE_OUT / 2)  # noise density x sqrt(bandwidth): about 9 mg rms


def snapshot_time(name: str) -> float:
    return datetime.strptime(name, "%Y.%m.%d.%H.%M.%S").replace(tzinfo=timezone.utc).timestamp()


def as_mpu6050(x: np.ndarray, rng) -> np.ndarray:
    """One channel at 20 kHz -> 3-axis 1 kHz samples in g as our device would record them: the
    real signal on the radial axis, sensor noise on all three, and gravity on z."""
    y = signal.resample_poly(x, 25, 512)  # 20,480 Hz -> 1,000 Hz, with an anti-aliasing low-pass
    sos = signal.butter(4, 260, fs=RATE_OUT, output="sos")  # the MPU6050's digital low-pass setting
    y = signal.sosfilt(sos, y)
    a = rng.normal(0, MPU_NOISE_G, (len(y), 3))
    a[:, 0] += y
    a[:, 2] += 1.0
    return a


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", help="the extracted 2nd_test folder")
    ap.add_argument("--bearing", type=int, default=1, help="1-4 (bearing 1 failed in test 2)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)
    files = sorted(p for p in Path(args.folder).iterdir() if p.is_file() and p.name[:4].isdigit())
    rng = np.random.default_rng(args.seed)
    t, rows = [], []
    for k, p in enumerate(files):
        x = np.loadtxt(p, usecols=[args.bearing - 1])
        a = as_mpu6050(x, rng)
        ts = np.arange(len(a)) / RATE_OUT
        w = F.Window(t0=0.0, accel_g=a, accel_t=ts, accel_rate_hz=RATE_OUT, accel_range_g=8.0)
        rows.append(F.extract(w))
        t.append(snapshot_time(p.name))
        if k % 100 == 0:
            print(f"  {k}/{len(files)} snapshots", flush=True)
    np.savez_compressed(args.out, t=np.array(t), X=np.array(rows, np.float32), feature_names=np.array(F.FEATURE_NAMES),
                        bearing=args.bearing, files=np.array([p.name for p in files]))
    print(f"wrote {args.out}: {len(rows)} snapshots over {(t[-1] - t[0]) / 3600:.0f} h")
    return 0


if __name__ == "__main__":
    sys.exit(main())
