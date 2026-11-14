"""Step 1 of the SSCC benchmark: unpack one condition folder at a time and keep a compact cache.

For every condition folder of the SSCC archives (normal/<cond>/ and abnormal/<fault>/<cond>/) this
  1. checks the C: drive keeps more than FLOOR_GB free once the folder is unpacked (stops otherwise),
  2. unpacks ONLY that folder's audio/ and vibration/ files (never the phone videos) into tmp/,
  3. converts every clip with a pool of worker processes:
       audio      the Zoom H5 WAV's first channel, 44.1 kHz -> 16 kHz (resample_poly), int16 (x 32767)
       vibration  all 4 CSV channels, 100 kHz -> 16 kHz (resample_poly, its FIR is the low-pass),
                  int16 with one scale per clip and channel (value = int16 * scale)
       raw facts  each raw channel's mean and standard deviation, the clip's first timestamp, sample count
  4. writes cache/<folder>.npz (uncompressed, written to a temp name then renamed), re-reads it to verify,
  5. deletes the unpacked folder.
A folder whose cache file exists and verifies is skipped, so the run resumes.

    python -m training.record.sscc_eval.build_cache --workers 14 [--only normal/] [--limit 1]
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np

BASE = Path(r"C:\Users\finnf\effente-sscc")
CACHE = BASE / "cache"
TMP = BASE / "tmp"
SEVENZIP = r"C:\Program Files\7-Zip\7z.exe"
ARCHIVES = {
    "normal": (Path(r"C:\Users\finnf\OneDrive\Desktop\normal\SSCC_dataset_normal.7z.001"), BASE / "sscc_normal_list.txt"),
    "abnormal": (Path(r"C:\Users\finnf\OneDrive\Desktop\abnormal\SSCC_dataset_abnormal.7z.001"), BASE / "sscc_abnormal_list.txt"),
}
FLOOR_GB = 20.0
MARGIN_GB = 1.0
VIB_IN, OUT_RATE, AUD_OUT = 100_000, 16_000, 16_000
COND_RE = re.compile(r"(heavy|med|light)_vel(\d+)_(clean|noise[a-z]*)", re.I)


def listing(kind: str) -> dict[str, dict]:
    """{condition path: {"n_audio", "n_vib", "bytes"}} from the saved `7z l` output."""
    out: dict[str, dict] = {}
    pat = re.compile(r"^\S+ \S+ (\S{5})\s+(\d+)\s+(\d*)\s+(\S.*)$")
    for line in open(ARCHIVES[kind][1], encoding="utf-8", errors="replace"):
        m = pat.match(line.rstrip())
        if not m or m.group(1).startswith("D"):
            continue
        parts = m.group(4).split(chr(92))
        if len(parts) < 3 or parts[-2] not in ("audio", "vibration"):
            continue
        cond = "/".join(parts[:-2])
        d = out.setdefault(cond, {"n_audio": 0, "n_vib": 0, "bytes": 0})
        d["n_audio" if parts[-2] == "audio" else "n_vib"] += 1
        d["bytes"] += int(m.group(2))
    return out


def meta_of(cond: str) -> dict:
    parts = cond.split("/")
    m = COND_RE.search(parts[-1])
    label = "normal" if parts[0] == "normal" else parts[1]
    nk = m.group(3).lower()
    return {"label": label, "load": m.group(1).lower(), "speed": int(m.group(2)),
            "noise": "clean" if nk == "clean" else "noise", "noise_kind": nk, "folder": cond}


def cache_path(cond: str) -> Path:
    return CACHE / (cond.replace("/", "__") + ".npz")


def free_gb() -> float:
    return shutil.disk_usage("C:\\").free / 2**30


def verify(path: Path, n_expected: int | None = None) -> bool:
    try:
        with np.load(path, allow_pickle=False) as d:
            n = len(d["clip_id"])
            ok = (d["audio"].shape == (n, 5 * AUD_OUT) and d["vib"].shape == (n, 5 * OUT_RATE, 4)
                  and d["vib_scale"].shape == (n, 4) and d["raw_mean"].shape == (n, 4) and n > 0)
            if n_expected is not None:
                ok = ok and n >= n_expected - 2
            return bool(ok)
    except Exception:
        return False


def fit_len(x: np.ndarray, n: int) -> np.ndarray:
    if len(x) >= n:
        return x[:n]
    pad = np.zeros((n - len(x),) + x.shape[1:], x.dtype)
    return np.concatenate([x, pad])


def convert_clip(job):
    """One clip -> (id, audio int16 (80000,), vib int16 (80000,4), scale (4,), raw mean, raw std, t0, n_raw, note)."""
    wav, csv = job
    import pyarrow.csv as pc
    import soundfile as sf
    from scipy import signal
    idx = Path(wav).stem.rsplit("_", 1)[-1]
    note = ""
    x, sr = sf.read(wav, dtype="float64", always_2d=True)
    a = x[:, 0]
    if sr != AUD_OUT:
        g = np.gcd(int(sr), AUD_OUT)
        a = signal.resample_poly(a, AUD_OUT // g, int(sr) // g)
    if len(a) < 5 * AUD_OUT - 16:
        note += f"short audio {len(a)}; "
    a16 = np.round(np.clip(fit_len(a, 5 * AUD_OUT), -1, 1) * 32767).astype(np.int16)
    tb = pc.read_csv(csv, parse_options=pc.ParseOptions(), read_options=pc.ReadOptions(use_threads=False))
    cols = [tb.column(i).to_numpy().astype(np.float64) for i in range(tb.num_columns)]
    t = cols[0]
    v = np.column_stack(cols[1:5])
    rmean, rstd = v.mean(0), v.std(0)
    if len(v) < 5 * VIB_IN - 100:
        note += f"short vib {len(v)}; "
    y = signal.resample_poly(v - rmean, OUT_RATE // 4000, VIB_IN // 4000, axis=0)  # 4/25, FIR low-pass at 8 kHz
    y = fit_len(y, 5 * OUT_RATE)
    sc = np.maximum(np.abs(y).max(0), 1e-12) / 32000.0
    y16 = np.round(y / sc).astype(np.int16)
    return idx, a16, y16, sc.astype(np.float64), rmean, rstd, float(t[0]), len(v), note


def do_folder(kind: str, cond: str, info: dict, pool: Pool) -> str:
    out = cache_path(cond)
    n_exp = min(info["n_audio"], info["n_vib"])
    if out.exists() and verify(out, n_exp):
        return f"{cond}: cached"
    need = info["bytes"] / 1e9 + MARGIN_GB
    if free_gb() - need < FLOOR_GB:
        raise SystemExit(f"STOP: {free_gb():.1f} GB free, {cond} needs {need:.1f} GB, floor {FLOOR_GB} GB")
    work = TMP / cond.replace("/", "__")
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    t0 = time.time()
    w = cond.replace("/", chr(92))
    r = subprocess.run([SEVENZIP, "x", str(ARCHIVES[kind][0]), f"-o{work}", f"{w}{chr(92)}audio{chr(92)}*",
                        f"{w}{chr(92)}vibration{chr(92)}*", "-y", "-bso0", "-bsp0"], capture_output=True, text=True)
    if r.returncode != 0:
        shutil.rmtree(work, ignore_errors=True)
        raise RuntimeError(f"7z failed on {cond}: {r.stderr[-500:]}")
    t1 = time.time()
    d = work / cond
    jobs = []
    for wav in sorted((d / "audio").glob("*.wav")):
        idx = wav.stem.rsplit("_", 1)[-1]
        csvs = list((d / "vibration").glob(f"*_{idx}.csv"))
        if csvs:
            jobs.append((str(wav), str(csvs[0])))
    res = pool.map(convert_clip, jobs, chunksize=1)
    t2 = time.time()
    m = meta_of(cond)
    notes = [f"{r_[0]}: {r_[8]}" for r_ in res if r_[8]]
    tmp = out.with_name(out.stem + ".part.npz")
    np.savez(tmp, clip_id=np.array([r_[0] for r_ in res]), audio=np.stack([r_[1] for r_ in res]),
             vib=np.stack([r_[2] for r_ in res]), vib_scale=np.stack([r_[3] for r_ in res]),
             raw_mean=np.stack([r_[4] for r_ in res]), raw_std=np.stack([r_[5] for r_ in res]),
             t_start=np.array([r_[6] for r_ in res]), n_raw=np.array([r_[7] for r_ in res]),
             notes=np.array(notes if notes else [""]), audio_rate=AUD_OUT, vib_rate=OUT_RATE,
             **{k: np.array(v) for k, v in m.items()})
    if not verify(tmp, n_exp):
        raise RuntimeError(f"{cond}: cache file did not verify")
    tmp.replace(out)
    shutil.rmtree(work)
    return (f"{cond}: {len(res)} clips (expected {n_exp}), unpack {t1 - t0:.0f}s, convert {t2 - t1:.0f}s, "
            f"{out.stat().st_size / 1e6:.0f} MB, free {free_gb():.1f} GB" + (f", notes: {notes[:3]}" if notes else ""))


def order(kind: str, conds: list[str]) -> list[str]:
    """Normal first; faults whose (load, speed, noise) has a normal folder; clean before noise."""
    def key(c):
        m = meta_of(c)
        return (m["noise"] != "clean", c)
    return sorted(conds, key=key)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=14)
    ap.add_argument("--only", default="")
    ap.add_argument("--kinds", default="normal,abnormal")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args(argv)
    CACHE.mkdir(parents=True, exist_ok=True)
    TMP.mkdir(parents=True, exist_ok=True)
    todo = []
    for kind in args.kinds.split(","):
        L = listing(kind)
        todo += [(kind, c, L[c]) for c in order(kind, list(L)) if args.only in c]
    if args.limit:
        todo = todo[:args.limit]
    print(f"{len(todo)} folders, {free_gb():.1f} GB free", flush=True)
    with Pool(args.workers) as pool:
        for k, (kind, cond, info) in enumerate(todo, 1):
            print(f"[{k}/{len(todo)}] {do_folder(kind, cond, info, pool)}", flush=True)
    print("ALL DONE", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
