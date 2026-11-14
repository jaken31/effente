"""Step 2 of the SSCC benchmark: features from the cache (cheap to re-run; nothing is unpacked again).

Per condition folder in C:/Users/finnf/effente-sscc/cache it writes features/<folder>.npz with, per clip and
per 1-second window (5 a clip):
  dev    (n, 5, 285)  effente.features.extract on the signals AS THE DEVICE SEES THEM:
                      vibration: CSV channels 1-3 (taken as the tri-axis sensor), 16 kHz cache -> 1 kHz, a 2 Hz
                      high-pass, MPU-6500 noise (300 ug/sqrt(Hz)) before its 218 Hz low-pass, 1 g on z, +-8 g
                      range and 1/4096 g counts (the processing of training/record/datasets/sscc.py);
                      sound: the Zoom H5's first channel at 16 kHz as 16-bit counts.
  gen_v16 (n, 5, 4, 35)  generic vibration features at full bandwidth (16 kHz, all 4 channels, no added noise):
                      32 log10 band powers 5-8000 Hz, log10 rms, kurtosis, crest factor
  gen_v1  (n, 5, 3, 23)  the same on the device-style 1 kHz signal (channels 1-3): 20 bands 5-500 Hz + 3
  gen_v1c (n, 5, 3, 23)  the same on a 1 kHz signal WITHOUT the MPU noise and counts (bandwidth only)
  gen_a   (n, 5, 67)  generic audio features: 64 log10 band powers 50-8000 Hz, log10 rms, kurtosis, crest

    python -m training.record.sscc_eval.make_features --workers 14
"""

from __future__ import annotations

import argparse
import sys
import zlib
from multiprocessing import Pool
from pathlib import Path

import numpy as np

BASE = Path(r"C:\Users\finnf\effente-sscc")
CACHE, OUT = BASE / "cache", BASE / "features"
RATE16, RATE1 = 16000, 1000
MPU_NOISE_G = 300e-6 * np.sqrt(RATE1 / 2)
RANGE_G, LSB_G, SETTLE_S = 8.0, 1 / 4096.0, 0.5
UNIT_TO_G = 1.0  # the CSV unit is taken as g (UNVERIFIED: no gravity offset, so an AC-coupled sensor)
TRI = (1, 2, 3)  # CSV signal columns taken as the tri-axis sensor (UNVERIFIED)
V16_EDGES = np.geomspace(5.0, 8000.0, 33)
V1_EDGES = np.geomspace(5.0, 500.0, 21)
A_EDGES = np.geomspace(50.0, 8000.0, 65)


def device_vib(v16: np.ndarray, seed: int, noise: bool = True, unit_to_g: float = UNIT_TO_G) -> np.ndarray:
    """(80000, 3) tri-axis at 16 kHz (unit as the CSV) -> (5000, 3) at 1 kHz in g as the MPU-6500 would give."""
    from scipy import signal
    y = signal.resample_poly(v16 * unit_to_g, 1, 16, axis=0)
    lead = int(SETTLE_S * RATE1)
    y = np.concatenate([y[lead:0:-1], y])
    y = signal.sosfilt(signal.butter(2, 2.0, "highpass", fs=RATE1, output="sos"), y, axis=0)
    if noise:
        y = y + np.random.default_rng(seed).normal(0, MPU_NOISE_G, y.shape)
    y = signal.sosfilt(signal.butter(4, 218, fs=RATE1, output="sos"), y, axis=0)
    a = y[lead:].copy()
    if not noise:
        return a
    a[:, 2] += 1.0
    return np.round(np.clip(a, -RANGE_G, RANGE_G) / LSB_G) * LSB_G


def generic(x: np.ndarray, rate: float, edges: np.ndarray) -> np.ndarray:
    """(n,) or (n, c) one window -> (c, bands + 3): log10 band powers, log10 rms, kurtosis, crest factor."""
    x = np.atleast_2d(x.T).T if x.ndim == 1 else x
    x = x - x.mean(0)
    n = len(x)
    w = np.hanning(n)
    p = np.abs(np.fft.rfft(x * w[:, None], axis=0)) ** 2
    f = np.fft.rfftfreq(n, 1 / rate)
    idx = np.searchsorted(f, edges)
    cs = np.vstack([np.zeros((1, p.shape[1])), np.cumsum(p, 0)])
    bands = np.log10(np.maximum(cs[idx[1:]] - cs[idx[:-1]], 1e-30) / (2 * w.sum() ** 2) + 1e-20)
    rms = x.std(0) + 1e-12
    kurt = (x ** 4).mean(0) / rms ** 4 - 3
    crest = np.abs(x).max(0) / rms
    return np.vstack([bands, np.log10(rms), kurt, crest]).T


def do_folder(job) -> str:
    path, out_dir, unit = job
    from effente import features as F
    out = out_dir / path.name
    if out.exists():
        try:
            with np.load(out) as d:
                if d["dev"].ndim == 3:
                    return f"{path.name}: done"
        except Exception:
            pass
    d = np.load(path)
    n = len(d["clip_id"])
    dev = np.full((n, 5, F.N_FEATURES), np.nan, np.float32)
    v16f = np.zeros((n, 5, 4, 35), np.float32)
    v1f = np.zeros((n, 5, 3, 23), np.float32)
    v1c = np.zeros((n, 5, 3, 23), np.float32)
    af = np.zeros((n, 5, 67), np.float32)
    for i in range(n):
        vib = d["vib"][i].astype(np.float64) * d["vib_scale"][i][None, :]
        aud = d["audio"][i].astype(np.float64)
        seed = zlib.crc32(f"{d['folder']}/{d['clip_id'][i]}".encode())
        a1 = device_vib(vib[:, list(TRI)], seed, unit_to_g=unit)
        a1c = device_vib(vib[:, list(TRI)], seed, noise=False, unit_to_g=unit)
        for k in range(5):
            acc = a1[k * RATE1:(k + 1) * RATE1]
            snd = aud[k * RATE16:(k + 1) * RATE16]
            w = F.Window(t0=float(k), accel_g=acc, accel_t=np.arange(len(acc)) / RATE1, accel_rate_hz=float(RATE1),
                         accel_range_g=RANGE_G, audio=snd, audio_rate_hz=float(RATE16))
            dev[i, k] = F.extract(w)
            v16f[i, k] = generic(vib[k * RATE16:(k + 1) * RATE16], RATE16, V16_EDGES)
            v1f[i, k] = generic(acc, RATE1, V1_EDGES)
            v1c[i, k] = generic(a1c[k * RATE1:(k + 1) * RATE1], RATE1, V1_EDGES)
            af[i, k] = generic(snd / 32767.0, RATE16, A_EDGES)[0]
    meta = {k: d[k] for k in ("clip_id", "t_start", "raw_mean", "raw_std", "label", "load", "speed", "noise",
                              "noise_kind", "folder")}
    tmp = out.with_name(out.stem + ".part.npz")
    np.savez(tmp, dev=dev, gen_v16=v16f, gen_v1=v1f, gen_v1c=v1c, gen_a=af, **meta)
    tmp.replace(out)
    return f"{path.name}: {n} clips"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=14)
    ap.add_argument("--only", default="")
    ap.add_argument("--unit-to-g", type=float, default=UNIT_TO_G, help="what-if: the CSV unit times this is g")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args(argv)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    files = [p for p in sorted(CACHE.glob("*.npz")) if not p.name.endswith(".part.npz") and args.only in p.name]
    with Pool(args.workers) as pool:
        for k, msg in enumerate(pool.imap_unordered(do_folder, [(f, out_dir, args.unit_to_g) for f in files]), 1):
            print(f"[{k}/{len(files)}] {msg}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
