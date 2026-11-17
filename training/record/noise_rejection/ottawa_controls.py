#!/usr/bin/env python3
"""Controls for ottawa_check.py's envelope separation (post hoc, added after its run).

Is "separates healthy from faulty" about the fault, or just about two different recordings? Per bearing, the
median |2 AUC - 1| of envelope prominence for: healthy against each fault file; the healthy file's first half
against its second half; and the healthy file against the next bearing's healthy file (a different
recording with no fault). Three signals, axis 0 only (the one real axis): the 1 kHz device conversion with and
without the MPU's noise, and the raw 42 kHz file over a 2-10 kHz envelope band (first 5 s, for time).

    python3 -m training.record.noise_rejection.ottawa_controls /path/to/ottawa-uods --out results/ottawa_controls.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from scipy import io as sio
from scipy import signal

from effente import features as F
from training.record.datasets import ottawa as O
from training.record.noise_rejection.lab import auc, whiten

HOP_S = 0.25


def raw(folder: Path, name: str) -> np.ndarray:
    return np.asarray(sio.loadmat(next(folder.glob(f"*/{name}.mat")))[name], float)[:, 0]


def device_no_noise(x: np.ndarray) -> np.ndarray:
    """datasets/ottawa.as_device's filtering, without the added noise, gravity or clipping."""
    y = signal.resample_poly((x - x.mean()) / 9.81, 1, 42)
    y = signal.sosfilt(signal.butter(4, 218, fs=1000, output="sos"), y)
    return signal.sosfilt(signal.butter(2, 2.0, "highpass", fs=1000, output="sos"), y)


def proms(y: np.ndarray, rate: float, band, whitened: bool) -> list[float]:
    n, out = int(rate), []
    for i in range(0, len(y) - n + 1, int(HOP_S * rate)):
        d = (y[i:i + n] - y[i:i + n].mean())[:, None]
        out.append(F.envelope_peak(whiten(d) if whitened else d, rate, band, F.ENV_SEARCH_HZ)[1])
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("folder", type=Path)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    rng = np.random.default_rng(0)
    variants = {
        "1 kHz, MPU noise": lambda x: (O.as_device(x, rng)[:, 0], 1000.0, F.VIB_ENV_BAND_HZ),
        "1 kHz, no noise": lambda x: (device_no_noise(x), 1000.0, F.VIB_ENV_BAND_HZ),
        "42 kHz raw, 2-10 kHz band": lambda x: (x[:42000 * 5], 42000.0, (2000.0, 10000.0)),
    }
    res = {}
    for vname, conv in variants.items():
        for whitened in (False, True):
            fault, half, other = [], [], []
            for b in range(1, 21):
                kind = O.kind_of(b)
                y, rate, band = conv(raw(args.folder, f"H_{b}_0"))
                h = proms(y, rate, band, whitened)
                for level in (1, 2):
                    yf, _, _ = conv(raw(args.folder, f"{kind}_{b}_{level}"))
                    fault.append(abs(2 * auc(h, proms(yf, rate, band, whitened)) - 1))
                m = len(h) // 2
                half.append(abs(2 * auc(h[:m], h[m:]) - 1))
                yo, _, _ = conv(raw(args.folder, f"H_{b % 20 + 1}_0"))
                other.append(abs(2 * auc(h, proms(yo, rate, band, whitened)) - 1))
            res[f"{vname}, {'whitened' if whitened else 'today'}"] = {
                "healthy vs fault": round(float(np.median(fault)), 2),
                "healthy half vs half": round(float(np.median(half)), 2),
                "healthy vs another healthy bearing": round(float(np.median(other)), 2)}
    text = json.dumps(res, indent=1)
    print(text)
    if args.out:
        args.out.write_text(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
