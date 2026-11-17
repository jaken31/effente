#!/usr/bin/env python3
"""lab.py's envelope methods (B whitened, C averaged) on NASA IMS run-to-failure bearings. Bars: RESULTS.md.

One shaft runs from healthy to failure with no remount, so early and late snapshots of one bearing differ by
wear (and slow drift in the rig, which the healthy control bearing measures). Two signals per snapshot:

  1 kHz    datasets/ims.as_mpu6050 (1 kHz, 260 Hz low-pass, sensor noise), the real axis only;
           envelope band 100-450 Hz, as today
  20 kHz   the raw snapshot; envelope band 2-9 kHz

    python3 -m training.record.noise_rejection.ims_check /path/to/nasa-ims --out results/ims_check.json

The folder holds extract/test1/1st_test, test2/2nd_test and extract/test3/4th_test/txt (as track 4 keeps them).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from effente import features as F
from training.record.datasets import ims as IMS
from training.record.noise_rejection.lab import auc, env_peak, envelope_spectrum, whiten

BPFO, BPFI, BSF = 236.4, 296.9, 139.9  # Hz at 2,000 rpm, ZA-2115 (secondary, see RESULTS.md)
TESTS = {  # folder below the root, columns per bearing (test 1 has two per bearing; the first is used)
    1: ("extract/test1/1st_test", 2),
    2: ("test2/2nd_test", 1),
    3: ("extract/test3/4th_test/txt", 1),
}
BEARINGS = [  # (name, test, bearing, rates that count as a hit)
    ("test 1, bearing 3 (inner race)", 1, 3, (BPFI,)),
    ("test 1, bearing 4 (roller)", 1, 4, (BSF, 2 * BSF)),
    ("test 2, bearing 1 (outer race)", 2, 1, (BPFO,)),
    ("test 3, bearing 3 (outer race)", 3, 3, (BPFO,)),
    ("control: test 2, bearing 3", 2, 3, (BPFO, BPFI, BSF, 2 * BSF)),
]
EARLY, LATE = 0.20, 0.10
MAX_SNAPSHOTS = 150
AVERAGE_OF = 6
HIT_TOL = 0.03
SIGNALS = {"1 kHz": (IMS.RATE_OUT, F.VIB_ENV_BAND_HZ), "20 kHz": (IMS.RATE_IN, (2000.0, 9000.0))}
# --posthoc (added after the run): the raw signal with the IIS3DWB's noise (75 ug/rtHz, datasheet) over its
# 6.3 kHz band, envelope band 2-6 kHz. Not the IIS3DWB's own response or rate; a rough stand-in.
IIS3DWB_NOISE_G = 75e-6 * np.sqrt(IMS.RATE_IN / 2)
POSTHOC = {"20 kHz, IIS3DWB-like noise, 2-6 kHz": (IMS.RATE_IN, (2000.0, 6000.0))}


def snapshots(folder: Path) -> list[Path]:
    return sorted(p for p in folder.iterdir() if p.is_file() and p.name[:4].isdigit())


def pick(files: list[Path], lo: float, hi: float) -> list[list[Path]]:
    """Up to MAX_SNAPSHOTS evenly spaced groups of AVERAGE_OF consecutive snapshots from [lo, hi) of the run."""
    a, b = int(lo * len(files)), int(hi * len(files)) - AVERAGE_OF
    starts = np.unique(np.linspace(a, b, min(MAX_SNAPSHOTS, b - a + 1)).astype(int))
    return [files[s:s + AVERAGE_OF] for s in starts]


def signal_of(path: Path, col: int, rate: float, rng, noisy: bool = False) -> np.ndarray:
    x = np.loadtxt(path, usecols=[col])
    if noisy:
        x = x + rng.normal(0, IIS3DWB_NOISE_G, len(x))
    if rate == IMS.RATE_OUT:
        x = IMS.as_mpu6050(x, rng)[:, 0]  # the real axis; the others are added noise only
    return (x - x.mean())[:, None]


def judge(groups, col, rates, rng, signals=SIGNALS) -> dict:
    """Per signal and envelope: hits of single snapshots and of hour averages, and single prominences."""
    out = {}
    for sname, (rate, band) in signals.items():
        for env in ("today", "whitened"):
            out[(sname, env)] = {"hit1": 0, "n1": 0, "hit_avg": 0, "n_avg": 0, "prom": []}
        for g in groups:
            specs = {"today": [], "whitened": []}
            for k, p in enumerate(g):
                x = signal_of(p, col, rate, rng, noisy=signals is POSTHOC)
                for env, y in (("today", x), ("whitened", whiten(x))):
                    spec, fe = envelope_spectrum(y, rate, band)
                    specs[env].append(spec)
                    if k == 0:  # single snapshots: the first of each group
                        hz, prom = env_peak(spec, fe)
                        r = out[(sname, env)]
                        r["hit1"] += any(abs(hz - t) <= HIT_TOL * t for t in rates)
                        r["n1"] += 1
                        r["prom"].append(prom)
            for env in specs:
                n = min(len(s) for s in specs[env])
                hz, _ = env_peak(np.mean([s[:n] for s in specs[env]], 0), fe[:n])
                r = out[(sname, env)]
                r["hit_avg"] += any(abs(hz - t) <= HIT_TOL * t for t in rates)
                r["n_avg"] += 1
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("root", type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--posthoc", action="store_true", help="the IIS3DWB-like check added after the run")
    args = ap.parse_args(argv)
    signals = POSTHOC if args.posthoc else SIGNALS
    rng = np.random.default_rng(0)
    files = {t: snapshots(args.root / sub) for t, (sub, _) in TESTS.items()}
    res = {}
    for name, test, bearing, rates in BEARINGS:
        col = (bearing - 1) * TESTS[test][1]
        fl = files[test]
        early = judge(pick(fl, 0.0, EARLY), col, rates, rng, signals)
        late = judge(pick(fl, 1 - LATE, 1.0), col, rates, rng, signals)
        row = {"snapshots in run": len(fl)}
        for key in early:
            e, la = early[key], late[key]
            row[f"{key[0]} {key[1]}"] = {
                "early hit %": round(100 * e["hit1"] / e["n1"], 1),
                "late hit %": round(100 * la["hit1"] / la["n1"], 1),
                "early hit % (hour average)": round(100 * e["hit_avg"] / e["n_avg"], 1),
                "late hit % (hour average)": round(100 * la["hit_avg"] / la["n_avg"], 1),
                "separation |2 AUC - 1|": round(abs(2 * auc(e["prom"], la["prom"]) - 1), 3),
            }
        res[name] = row
        print(name, json.dumps(row), flush=True)
    text = json.dumps(res, indent=1)
    if args.out:
        args.out.write_text(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
