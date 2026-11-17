#!/usr/bin/env python3
"""lab.py's methods on real bearing faults: the University of Ottawa set (UORED-VAFCLS).

B, the whitened envelope, and A, speed from the harmonic family (the files log the true speed).

Each file goes through datasets/ottawa.py's conversion to what our device would record (1 kHz, the MPU's
218 Hz low-pass, its noise, +-8 g). Windows of 1 s every 0.25 s. For each window, today's envelope
(features.envelope_peak on the 100-450 Hz band) and the whitened one, and today's speed and the family's. The bars and the two measures are in
RESULTS.md. The dataset gives no bearing geometry, so the true defect rate is not checked.

    python3 -m training.record.noise_rejection.ottawa_check /path/to/folder-holding-a-subfolder-of-.mat-files
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import numpy as np
from scipy import io as sio

from effente import features as F
from training.record.datasets import ottawa as O
from training.record.datasets.ims import RATE_OUT
from training.record.noise_rejection.lab import SPEED_TOL, V1, auc, speed_family, today, whiten

HOP_S = 0.25
# Checked after B failed here (post hoc): is the MPU's 218 Hz low-pass the reason? Bands inside it too.
POSTHOC_BANDS = ((100.0, 450.0), (100.0, 210.0), (30.0, 210.0))
OFF_ORDER = 0.1  # an envelope rate this far (in orders) from every whole multiple of the speed is "off the orders"


def speed_hz(folder: Path, name: str) -> float:
    m = sio.loadmat(next(folder.glob(f"*/{name}.mat")))[name]
    return float(m[0, 2]) / 60.0  # column 3 holds the speed in rpm, in its first row


def envelopes(a: np.ndarray, band=F.VIB_ENV_BAND_HZ) -> dict[str, list[tuple[float, float]]]:
    """(rate Hz, prominence) per 1 s window, today's and whitened."""
    n = int(RATE_OUT)
    out = {"today": [], "whitened": []}
    for i in range(0, len(a) - n + 1, int(HOP_S * RATE_OUT)):
        dyn = a[i:i + n] - a[i:i + n].mean(0)
        for k, x in (("today", dyn), ("whitened", whiten(dyn))):
            out[k].append(F.envelope_peak(x, RATE_OUT, band, F.ENV_SEARCH_HZ))
    return out


SPEEDS = ("today", "family v1", "family")


def speed_errors(a: np.ndarray, truth: float) -> list[int]:
    """[windows, then per SPEEDS the windows more than SPEED_TOL off]."""
    n = int(RATE_OUT)
    out = [0] * (1 + len(SPEEDS))
    for i in range(0, len(a) - n + 1, int(HOP_S * RATE_OUT)):
        x = a[i:i + n]
        got = (float(today(x, RATE_OUT)[F.INDEX["vib_speed_hz"]]), speed_family(x, RATE_OUT, **V1),
               speed_family(x, RATE_OUT))
        out[0] += 1
        for j, v in enumerate(got, 1):
            out[j] += abs(v - truth) > SPEED_TOL * truth
    return out


def off_order(rates: list[float], speed: float) -> tuple[bool, float]:
    """Whether the most common rate (to 0.5 Hz) is off the whole orders, and that rate as an order."""
    common = Counter(round(r * 2) / 2 for r in rates).most_common(1)[0][0]
    order = common / speed
    return abs(order - round(order)) > OFF_ORDER, round(order, 2)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("folder", type=Path)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    rng = np.random.default_rng(0)
    files, aucs = [], {"today": [], "whitened": []}
    off = {s: {"today": 0, "whitened": 0} for s in ("healthy", "fault")}
    speed = {g: [0] * (1 + len(SPEEDS)) for g in ["healthy", *O.KINDS.values()]}
    sep = {(band, k): [] for band in POSTHOC_BANDS for k in ("today", "whitened")}

    def add_speed(group, a, truth):
        for j, v in enumerate(speed_errors(a, truth)):
            speed[group][j] += v
    for b in range(1, 21):
        kind = O.kind_of(b)
        h_name = f"H_{b}_0"
        h_a = O.load(args.folder, h_name, rng)
        h = envelopes(h_a)
        h_speed = speed_hz(args.folder, h_name)
        add_speed("healthy", h_a, h_speed)
        h_bands = {band: envelopes(h_a, band) for band in POSTHOC_BANDS}
        row = {"bearing": b, "kind": O.KINDS[kind], "speed_hz": round(h_speed, 2)}
        for k in ("today", "whitened"):
            o, order = off_order([r for r, _ in h[k]], h_speed)
            off["healthy"][k] += o
            row[f"healthy order {k}"] = order
        for level in (1, 2):
            name = f"{kind}_{b}_{level}"
            sp = speed_hz(args.folder, name)
            f_a = O.load(args.folder, name, rng)
            e = envelopes(f_a)
            add_speed(O.KINDS[kind], f_a, sp)
            for band in POSTHOC_BANDS:
                eb = envelopes(f_a, band)
                for k in ("today", "whitened"):
                    sep[(band, k)].append(abs(2 * auc([p for _, p in h_bands[band][k]], [p for _, p in eb[k]]) - 1))
            for k in ("today", "whitened"):
                a = auc([p for _, p in h[k]], [p for _, p in e[k]])
                aucs[k].append(a)
                o, order = off_order([r for r, _ in e[k]], sp)
                off["fault"][k] += o
                row[f"{level} AUC {k}"] = round(a, 3)
                row[f"{level} order {k}"] = order
        files.append(row)
    res = {
        "median AUC, 40 fault files": {k: round(float(np.median(v)), 3) for k, v in aucs.items()},
        "fault files off the shaft orders (of 40)": off["fault"],
        "healthy files off the shaft orders (of 20)": off["healthy"],
        "speed: % of windows more than 3 % off": {
            g: {k: round(100 * v[j] / v[0], 1) for j, k in enumerate(SPEEDS, 1)} for g, v in speed.items()},
        "speed overall %": {k: round(100 * sum(v[j] for v in speed.values()) / sum(v[0] for v in speed.values()), 1)
                            for j, k in enumerate(SPEEDS, 1)},
        "post hoc: median separation |2 AUC - 1| by envelope band": {
            f"{band[0]:g}-{band[1]:g} Hz {k}": round(float(np.median(v)), 3) for (band, k), v in sep.items()},
        "per bearing": files,
    }
    text = json.dumps(res, indent=1)
    print(text)
    if args.out:
        args.out.write_text(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
