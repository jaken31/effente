"""Extra per-second features for the second round of the SSCC benchmark (14 Nov, morning), from the cache.

Writes features2/<folder>.npz, per clip and 1-second window (5 a clip), all on the signals the device would see
(sound: the Zoom's first channel at 16 kHz; vibration: the MPU-style 1 kHz tri-axis of make_features.device_vib,
same noise seed, so it is the same signal as gen_v1):
  a_fr   (n, 5, 79)   sound in 32 ms frames (hop 16 ms, 61 frames) and 11 bands 62-8000 Hz:
                      per band std of the frame log-energy (11), per band max minus median (11),
                      spectral flux mean and max (2), per band modulation power of the log-energy in
                      1-3, 3-6, 6-12, 12-24, 24-31 Hz (55)
  a128   (n, 5, 128)  128 log10 band powers 50-8000 Hz (band-count test)
  v_env  (n, 5, 3, 8) per axis: envelope spectrum (20-400 Hz band, Hilbert envelope) in 8 log bands 2-200 Hz
  v_fr   (n, 5, 3)    per axis: std of log rms over 10 frames of 100 ms
  v_coh  (n, 5, 18)   coherence of the axis pairs xy, xz, yz (Welch, 128 samples) in 6 log bands 8-500 Hz
  v40    (n, 5, 3, 40) 40 log10 band powers 5-500 Hz per axis

    python -m training.record.sscc_eval.make_features2 --workers 14
"""

from __future__ import annotations

import argparse
import sys
import zlib
from multiprocessing import Pool
from pathlib import Path

import numpy as np

from training.record.sscc_eval import make_features as MF

BASE = Path(r"C:\Users\finnf\effente-sscc")
OUT = BASE / "features2"
FR_N, FR_HOP = 512, 256
FR_EDGES = np.array([62.0, 125, 250, 500, 750, 1000, 1500, 2000, 3000, 4000, 5500, 8000])
MOD_EDGES = np.array([1.0, 3, 6, 12, 24, 31.5])
A128_EDGES = np.geomspace(50.0, 8000.0, 129)
V40_EDGES = np.geomspace(5.0, 500.0, 41)
ENV_EDGES = np.geomspace(2.0, 200.0, 9)
COH_EDGES = np.geomspace(8.0, 500.0, 7)


def band_sum(p, f, edges):
    idx = np.searchsorted(f, edges)
    cs = np.concatenate([np.zeros_like(p[..., :1]), np.cumsum(p, -1)], -1)
    return cs[..., idx[1:]] - cs[..., idx[:-1]]


def sound_frames(x: np.ndarray, rate: float = 16000.0) -> np.ndarray:
    x = x - x.mean()
    nf = 1 + (len(x) - FR_N) // FR_HOP
    fr = np.lib.stride_tricks.sliding_window_view(x, FR_N)[::FR_HOP][:nf]
    w = np.hanning(FR_N)
    p = np.abs(np.fft.rfft(fr * w, axis=1)) ** 2
    f = np.fft.rfftfreq(FR_N, 1 / rate)
    E = np.log10(band_sum(p, f, FR_EDGES) + 1e-12)  # (frames, 11)
    std = E.std(0)
    peak = E.max(0) - np.median(E, 0)
    dE = np.diff(E, axis=0)
    flux = np.maximum(dE, 0).sum(1)
    M = np.abs(np.fft.rfft((E - E.mean(0)) * np.hanning(nf)[:, None], axis=0)) ** 2
    mf = np.fft.rfftfreq(nf, FR_HOP / rate)
    mod = np.log10(band_sum(M.T, mf, MOD_EDGES) + 1e-9).ravel()  # band-major: 11 x 5
    return np.r_[std, peak, flux.mean(), flux.max(), mod]


def band_log(x, rate, edges):
    x = x - x.mean(0)
    w = np.hanning(len(x))
    p = np.abs(np.fft.rfft(x * w[:, None], axis=0)) ** 2
    f = np.fft.rfftfreq(len(x), 1 / rate)
    return np.log10(band_sum(p.T, f, edges) / (2 * w.sum() ** 2) + 1e-20)  # (channels, bands)


def vib_extra(a: np.ndarray, rate: float = 1000.0):
    """a: (1000, 3) one second in g."""
    from scipy import signal
    a = a - a.mean(0)
    n = len(a)
    f = np.fft.rfftfreq(n, 1 / rate)
    X = np.fft.rfft(a, axis=0)
    X[(f < 20) | (f > 400)] = 0
    bp = np.fft.irfft(X, n, axis=0)
    env = np.abs(signal.hilbert(bp, axis=0))
    env_b = band_log(env, rate, ENV_EDGES)  # (3, 8)
    fr = a[: n // 100 * 100].reshape(10, -1, 3)
    vfr = np.log10(fr.std(1) + 1e-9).std(0)  # (3,)
    coh = []
    for i, j in ((0, 1), (0, 2), (1, 2)):
        fc, c = signal.coherence(a[:, i], a[:, j], fs=rate, nperseg=128)
        idx = np.searchsorted(fc, COH_EDGES)
        coh.append([c[idx[k]:idx[k + 1]].mean() if idx[k + 1] > idx[k] else 0.0 for k in range(len(COH_EDGES) - 1)])
    return env_b, vfr, np.array(coh).ravel(), band_log(a, rate, V40_EDGES)


def do_folder(job) -> str:
    path, out_dir = job
    out = out_dir / path.name
    if out.exists():
        return f"{path.name}: done"
    d = np.load(path)
    n = len(d["clip_id"])
    a_fr = np.zeros((n, 5, 79), np.float32)
    a128 = np.zeros((n, 5, 128), np.float32)
    v_env = np.zeros((n, 5, 3, 8), np.float32)
    v_fr = np.zeros((n, 5, 3), np.float32)
    v_coh = np.zeros((n, 5, 18), np.float32)
    v40 = np.zeros((n, 5, 3, 40), np.float32)
    for i in range(n):
        vib = d["vib"][i].astype(np.float64) * d["vib_scale"][i][None, :]
        aud = d["audio"][i].astype(np.float64)
        seed = zlib.crc32(f"{d['folder']}/{d['clip_id'][i]}".encode())
        a1 = MF.device_vib(vib[:, list(MF.TRI)], seed)
        for k in range(5):
            acc = a1[k * 1000:(k + 1) * 1000]
            snd = aud[k * 16000:(k + 1) * 16000] / 32767.0
            a_fr[i, k] = sound_frames(snd)
            a128[i, k] = band_log(snd[:, None], 16000.0, A128_EDGES)[0]
            v_env[i, k], v_fr[i, k], v_coh[i, k], v40[i, k] = vib_extra(acc)
    tmp = out.with_name(out.stem + ".part.npz")
    np.savez(tmp, a_fr=a_fr, a128=a128, v_env=v_env, v_fr=v_fr, v_coh=v_coh, v40=v40, clip_id=d["clip_id"],
             folder=d["folder"])
    tmp.replace(out)
    return f"{path.name}: {n} clips"


A256_EDGES = np.geomspace(50.0, 8000.0, 257)
ALIN_EDGES = np.arange(50.0, 8001.0, 50.0)
A128L_EDGES = np.geomspace(20.0, 8000.0, 129)
SET4 = (("alin25", np.arange(50.0, 8001.0, 25.0)), ("alin100", np.arange(50.0, 8001.0, 100.0)),
        ("alin200", np.arange(50.0, 8001.0, 200.0)))


def do_folder4(job) -> str:
    """features4/<folder>.npz: linear sound bands of 25, 100 and 200 Hz from 50 Hz to 8 kHz."""
    path, out_dir = job
    out = out_dir / path.name
    if out.exists():
        return f"{path.name}: done"
    d = np.load(path)
    n = len(d["clip_id"])
    res = {k: np.zeros((n, 5, len(e) - 1), np.float32) for k, e in SET4}
    for i in range(n):
        aud = d["audio"][i].astype(np.float64)
        for k in range(5):
            snd = aud[k * 16000:(k + 1) * 16000, None] / 32767.0
            for key, e in SET4:
                res[key][i, k] = band_log(snd, 16000.0, e)[0]
    tmp = out.with_name(out.stem + ".part.npz")
    np.savez(tmp, clip_id=d["clip_id"], **res)
    tmp.replace(out)
    return f"{path.name}: {n} clips"


def do_folder3(job) -> str:
    """features3/<folder>.npz: more sound band layouts (a256: 256 log bands 50-8000 Hz; alin: 159 bands of
    50 Hz from 50 to 8000 Hz; a128l: 128 log bands 20-8000 Hz)."""
    path, out_dir = job
    out = out_dir / path.name
    if out.exists():
        return f"{path.name}: done"
    d = np.load(path)
    n = len(d["clip_id"])
    res = {k: np.zeros((n, 5, len(e) - 1), np.float32) for k, e in (("a256", A256_EDGES), ("alin", ALIN_EDGES),
                                                                    ("a128l", A128L_EDGES))}
    for i in range(n):
        aud = d["audio"][i].astype(np.float64)
        for k in range(5):
            snd = aud[k * 16000:(k + 1) * 16000, None] / 32767.0
            for key, e in (("a256", A256_EDGES), ("alin", ALIN_EDGES), ("a128l", A128L_EDGES)):
                res[key][i, k] = band_log(snd, 16000.0, e)[0]
    tmp = out.with_name(out.stem + ".part.npz")
    np.savez(tmp, clip_id=d["clip_id"], **res)
    tmp.replace(out)
    return f"{path.name}: {n} clips"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=14)
    ap.add_argument("--only", default="")
    ap.add_argument("--set3", action="store_true", help="the extra sound band layouts, to features3/")
    ap.add_argument("--set4", action="store_true", help="linear sound bands of 25/100/200 Hz, to features4/")
    args = ap.parse_args(argv)
    out_dir = BASE / "features4" if args.set4 else BASE / "features3" if args.set3 else OUT
    fn = do_folder4 if args.set4 else do_folder3 if args.set3 else do_folder
    out_dir.mkdir(parents=True, exist_ok=True)
    files = [p for p in sorted(MF.CACHE.glob("*.npz")) if not p.name.endswith(".part.npz") and args.only in p.name]
    with Pool(args.workers) as pool:
        for k, msg in enumerate(pool.imap_unordered(fn, [(f, out_dir) for f in files]), 1):
            print(f"[{k}/{len(files)}] {msg}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
