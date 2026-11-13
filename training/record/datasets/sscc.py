#!/usr/bin/env python3
"""Real chain-conveyor faults (SSCC dataset) -> effente features, as OUR device would see them.

SSCC: a real single-speed chain conveyor recorded in 5 s clips (6,669 of them) at five speeds (20-100,
nominal levels, not physical units), three loads (heavy, med, light), with and without factory noise
played through loudspeakers, normal and four faults: dry (too little chain lubrication), lean (guide
rail misaligned), loose (chains too slack) and screwdrop (a foreign object in the chain path).

What this does with each clip, the way the device would:
  - Vibration: the CSV's tri-axis sensor (at the far end of the conveyor, measuring the structure)
    as our MPU-6500 on the frame would see it: steady offset removed, converted to g, resampled
    100 kHz -> 1 kHz with an anti-aliasing low-pass, a 2 Hz high-pass (source drift), the chip's noise
    (300 ug/sqrt(Hz), white over 0-500 Hz) added BEFORE its 218 Hz low-pass (about 4.4 mg rms an axis
    after it), 1 g gravity on z, clipped at +-8 g and rounded to its counts (1/4096 g). A mirrored
    0.5 s lead-in lets the filters settle, so window 0 has no start-up transient the device never has.
    The single-axis sensor on the motor is not used: our device has one IMU.
  - Sound: the Zoom H5 recorder's WAV (a fixed recorder, the closest to our fixed microphone), its
    first channel, resampled 44.1 kHz -> 16 kHz, as 16-bit counts.
  - effente.features.extract on every whole 1 s window: 5 rows a clip. No gyro, thermal camera, IMU
    temperature or room sensor in SSCC: those features are NaN. History (trend_*) features are NaN too.

UNVERIFIED (see SSCC.md): the vibration CSV's column order (taken as: time, motor single-axis, tri-axis
x, y, z) and its unit (taken as g unless --vib-unit says otherwise). The paper says neither.

    nice -n 10 python3 -m training.record.datasets.sscc data/real/sscc/unpacked \\
        --cache data/real/sscc/cache --out data/real/sscc/features.npz --workers 2

Resumable: each condition folder's rows are cached in --cache; a rerun skips finished folders.

Source: Zhang Yucong et al., "Toward Multimodal Industrial Fault Analysis: A Single-Speed Chain
Conveyor Dataset with Audio and Vibration Signals", arXiv 2603.07130 (2026).
https://yucongzh.github.io/SSCC-Dataset/ . Licence CC BY 4.0: cite the source.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import zlib
from multiprocessing import Pool
from pathlib import Path

import numpy as np
from scipy import signal
from scipy.io import wavfile

from effente import features as F

VIB_RATE_IN, RATE_OUT, SND_RATE = 100000, 1000, 16000
# Datasheet noise density (300 ug/sqrt(Hz)) as white noise over the whole 0-500 Hz band, about 6.7 mg rms;
# it goes in BEFORE the 218 Hz low-pass, which leaves about 4.4 mg rms, as on the chip.
MPU6500_NOISE_G = 300e-6 * np.sqrt(RATE_OUT / 2)
RANGE_G = 8.0
LSB_G = 1 / 4096.0  # one count at +-8 g
SETTLE_S = 0.5  # mirrored lead-in so the filters start settled, as on a device that is always running
UNIT_TO_G = {"g": 1.0, "ms2": 1 / 9.80665, "mg": 1e-3}
COND_RE = re.compile(r"(heavy|med|light)_vel(\d+)_(clean|noise\w*)", re.I)
LABELS = ("normal", "dry", "lean", "loose", "screwdrop")


def parse_condition(cond_dir: Path) -> dict:
    """Speed, load, noise and label of a condition folder, from its name and where it sits:
    normal/<load>_vel<speed>_<clean|noiseX>/ or abnormal/<fault>/<load>_vel<speed>_<...>/."""
    m = COND_RE.search(cond_dir.name)
    if not m:
        raise ValueError(f"can't read speed/load/noise from folder name {cond_dir.name!r}")
    parts = [p.lower() for p in cond_dir.parts]
    label = parts[parts.index("abnormal") + 1] if "abnormal" in parts else "normal"
    noise = m.group(3).lower()
    return {"load": m.group(1).lower(), "speed": int(m.group(2)), "noise": "clean" if noise == "clean" else "noise",
            "noise_kind": noise, "label": label}


def find_conditions(root: Path) -> list[Path]:
    """Every folder that holds both audio/ and vibration/."""
    return sorted(d.parent for d in root.rglob("vibration") if d.is_dir() and (d.parent / "audio").is_dir())


def read_vibration(path: Path) -> np.ndarray:
    """The 4 signal columns of a vibration CSV, (n, 4). The first column is the timestamp; lines
    starting with # are comments (as the benchmark's make_csv.py reads them)."""
    import pandas as pd
    first = ""
    with open(path) as f:
        for line in f:
            if line.strip() and not line.lstrip().startswith("#"):
                first = line
                break
    try:
        [float(v) for v in first.split(",")]
        header = None
    except ValueError:
        header = 0  # counted after comment lines are dropped
    a = pd.read_csv(path, header=header, comment="#", engine="c", dtype=np.float64).to_numpy()
    if a.shape[1] < 5:
        raise ValueError(f"{path.name}: {a.shape[1]} columns, expected time + 4 signals")
    return a[:, 1:5]


def as_mpu6500(xyz: np.ndarray, unit: str, rng) -> np.ndarray:
    """(n, 3) tri-axis vibration at 100 kHz -> (m, 3) 1 kHz samples in g as our MPU-6500 would record."""
    y = (xyz - xyz.mean(0)) * UNIT_TO_G[unit]
    y = signal.resample_poly(y, 1, VIB_RATE_IN // RATE_OUT, axis=0)  # with an anti-aliasing low-pass
    lead = int(SETTLE_S * RATE_OUT)
    y = np.concatenate([y[lead:0:-1], y])  # mirrored lead-in, dropped below: no start-up transient in window 0
    y = signal.sosfilt(signal.butter(2, 2.0, "highpass", fs=RATE_OUT, output="sos"), y, axis=0)  # source drift only
    y = y + rng.normal(0, MPU6500_NOISE_G, y.shape)  # the chip's noise, before its own low-pass
    y = signal.sosfilt(signal.butter(4, 218, fs=RATE_OUT, output="sos"), y, axis=0)  # the MPU-6500's low-pass
    a = y[lead:]
    a[:, 2] += 1.0
    return np.round(np.clip(a, -RANGE_G, RANGE_G) / LSB_G) * LSB_G  # its +-8 g range and 16-bit counts


def read_audio(path: Path) -> np.ndarray:
    """First channel of a WAV, resampled to 16 kHz, as 16-bit counts (float)."""
    rate, x = wavfile.read(path)
    if x.ndim > 1:
        x = x[:, 0]
    if x.dtype.kind == "f":
        x = x.astype(np.float64)
    else:
        x = x.astype(np.float64) / float(np.iinfo(x.dtype).max + 1)  # 24-bit comes back as int32, left-justified
    if rate != SND_RATE:
        g = np.gcd(int(rate), SND_RATE)
        x = signal.resample_poly(x, SND_RATE // g, int(rate) // g)
    return np.clip(x, -1.0, 1.0) * 32767.0


def clip_rows(audio_wav: Path, vib_csv: Path, vib_unit: str, axes: tuple[int, int, int], seed: int) -> tuple[np.ndarray, dict]:
    """effente features of every whole 1 s window of one clip, plus raw-signal facts for checking."""
    v = read_vibration(vib_csv)
    rng = np.random.default_rng(seed)
    a = as_mpu6500(v[:, list(axes)], vib_unit, rng)
    snd = read_audio(audio_wav)
    n = min(len(a) // RATE_OUT, len(snd) // SND_RATE)
    rows = []
    for k in range(n):
        acc = a[k * RATE_OUT:(k + 1) * RATE_OUT]
        w = F.Window(t0=float(k), accel_g=acc, accel_t=np.arange(len(acc)) / RATE_OUT, accel_rate_hz=float(RATE_OUT),
                     accel_range_g=RANGE_G, audio=snd[k * SND_RATE:(k + 1) * SND_RATE], audio_rate_hz=float(SND_RATE))
        rows.append(F.extract(w))
    facts = {"vib_mean": v.mean(0), "vib_std": v.std(0), "vib_n": len(v)}
    return np.array(rows, np.float32).reshape(-1, F.N_FEATURES), facts


def do_condition(job) -> str:
    cond, root, cache, vib_unit, axes = job
    info = parse_condition(cond)
    rel = cond.relative_to(root)
    out = cache / (str(rel).replace(os.sep, "__") + ".npz")
    if out.exists():
        return f"{rel}: cached"
    X, clip, win, ids, means, stds = [], [], [], [], [], []
    wavs = sorted((cond / "audio").glob("audio_part_*.wav"))
    bad = 0
    for j, wav in enumerate(wavs):
        idx = wav.stem.replace("audio_part_", "")
        csv = cond / "vibration" / f"vibration_part_{idx}.csv"
        if not csv.exists():
            bad += 1
            continue
        try:
            R, facts = clip_rows(wav, csv, vib_unit, axes, seed=zlib.crc32(f"{rel}/{idx}".encode()))
        except Exception as e:  # noqa: BLE001 - report and go on with the other clips
            print(f"  {rel} {idx}: {e}", flush=True)
            bad += 1
            continue
        X.append(R)
        clip += [len(ids)] * len(R)
        win += list(range(len(R)))
        ids.append(f"{rel}/{idx}")
        means.append(facts["vib_mean"])
        stds.append(facts["vib_std"])
    tmp = out.with_suffix(".tmp.npz")
    np.savez_compressed(tmp, X=np.concatenate(X) if X else np.zeros((0, F.N_FEATURES), np.float32),
                        clip=np.array(clip, np.int32), window=np.array(win, np.int8), clip_id=np.array(ids),
                        vib_mean=np.array(means), vib_std=np.array(stds), **{k: v for k, v in info.items()})
    tmp.rename(out)
    return f"{rel}: {len(ids)} clips, {bad} skipped"


def benchmark_id(clip_id: str, label: str) -> str:
    """The id the official benchmark (make_csv.py) gives a clip, e.g. normal/heavy_vel100_clean_0001 or
    abnormal/dry/med_vel80_noiseB_0012, so its condition-level splits (make_splits_*.py) can be reused."""
    cond, idx = clip_id.rsplit("/", 2)[-2:]
    return f"normal/{cond}_{idx}" if label == "normal" else f"abnormal/{label}/{cond}_{idx}"


def combine(cache: Path, out: str) -> None:
    X, clip, win = [], [], []
    meta = {k: [] for k in ("clip_id", "benchmark_id", "speed", "load", "noise", "noise_kind", "label", "condition")}
    vm, vs = [], []
    for p in sorted(cache.glob("*.npz")):
        if p.name.endswith(".tmp.npz"):
            continue
        d = np.load(p)
        n = len(d["clip_id"])
        X.append(d["X"])
        clip.append(d["clip"] + len(meta["clip_id"]))
        win.append(d["window"])
        meta["clip_id"] += list(d["clip_id"])
        meta["benchmark_id"] += [benchmark_id(c, d["label"].item()) for c in d["clip_id"]]
        for k in ("speed", "load", "noise", "noise_kind", "label"):
            meta[k] += [d[k].item()] * n
        meta["condition"] += [p.stem.replace("__", "/")] * n
        vm.append(d["vib_mean"].reshape(-1, 4))
        vs.append(d["vib_std"].reshape(-1, 4))
    np.savez_compressed(out, X=np.concatenate(X), clip=np.concatenate(clip), window=np.concatenate(win),
                        feature_names=np.array(F.FEATURE_NAMES), vib_raw_mean=np.concatenate(vm),
                        vib_raw_std=np.concatenate(vs), **{k: np.array(v) for k, v in meta.items()})
    labels, counts = np.unique(meta["label"], return_counts=True)
    print(f"wrote {out}: {sum(len(x) for x in X)} rows from {len(meta['clip_id'])} clips; "
          + ", ".join(f"{l} {c}" for l, c in zip(labels, counts)))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", help="the unpacked dataset (searched for folders holding audio/ and vibration/)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--cache", required=True, help="folder for each condition's rows (makes reruns resume)")
    ap.add_argument("--vib-unit", choices=sorted(UNIT_TO_G), default="g", help="the CSV's vibration unit (unverified)")
    ap.add_argument("--axes", default="1,2,3", help="CSV signal columns (0-3, after the timestamp) used as x,y,z")
    ap.add_argument("--workers", type=int, default=2, help="at most 2 on the shared machine")
    ap.add_argument("--only", default="", help="process only condition folders whose path contains this")
    args = ap.parse_args(argv)
    root, cache = Path(args.root), Path(args.cache)
    cache.mkdir(parents=True, exist_ok=True)
    axes = tuple(int(c) for c in args.axes.split(","))
    conds = [c for c in find_conditions(root) if args.only in str(c)]
    if not conds:
        raise SystemExit(f"no condition folders under {root}")
    jobs = [(c, root, cache, args.vib_unit, axes) for c in conds]
    print(f"{len(conds)} condition folders", flush=True)
    with Pool(max(1, min(args.workers, 2))) as pool:
        for k, msg in enumerate(pool.imap_unordered(do_condition, jobs), 1):
            print(f"  [{k}/{len(jobs)}] {msg}", flush=True)
    combine(cache, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
