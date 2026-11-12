#!/usr/bin/env python3
"""Real machine sounds (DCASE 2024 Task 2 development set) -> effente sound features, one row per clip.

The DCASE sets are 10 s, 16 kHz mono recordings of real machines (here fans) in a real factory's
background noise: 1,000 normal clips to learn from, and 100 normal plus 100 anomalous clips to test
(the anomalies are real faults such as blade damage, imbalance, clogging and over-voltage). Our
microphone also runs at 16 kHz, so each clip goes through exactly the device's sound features:
1 s windows, one row per window, summarised as the per-clip median (like the device's per-minute
median) and max.

    python3 -m training.record.datasets.dcase data/real/dcase2024-fan --out data/real/dcase2024-fan/features.npz

Licence: CC BY-NC-SA 4.0 (non-commercial; cite the DCASE 2024 Task 2 organisers).
"""

from __future__ import annotations

import argparse
import re
import sys
import wave
import zipfile
from pathlib import Path

import numpy as np

from effente import features as F


def read_wav(data: bytes) -> tuple[np.ndarray, int]:
    import io
    with wave.open(io.BytesIO(data)) as w:
        rate, width, ch = w.getframerate(), w.getsampwidth(), w.getnchannels()
        raw = w.readframes(w.getnframes())
    if width != 2:
        raise ValueError(f"expected 16-bit samples, got {8 * width}-bit")
    x = np.frombuffer(raw, "<i2").reshape(-1, ch)[:, 0]
    return x, rate


def clip_rows(x: np.ndarray, rate: int) -> np.ndarray:
    """Sound features of every whole 1 s window of a clip: (windows, features)."""
    n = int(rate)
    rows = []
    for k in range(len(x) // n):
        rows.append(F.extract(F.Window(t0=float(k), audio=x[k * n:(k + 1) * n], audio_rate_hz=float(rate))))
    return np.array(rows)


def parse(name: str) -> dict:
    m = re.search(r"section_(\d+)_(source|target)_(train|test)_(normal|anomaly)", name)
    if not m:
        return {}
    return {"section": m.group(1), "domain": m.group(2), "split": m.group(3), "label": m.group(4)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", help="folder holding the downloaded dev_*.zip")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    zips = sorted(Path(args.folder).glob("*.zip"))
    if not zips:
        raise SystemExit("no .zip in the folder")
    med, mx, meta = [], [], []
    for z in zips:
        with zipfile.ZipFile(z) as zf:
            names = [n for n in zf.namelist() if n.lower().endswith(".wav")]
            for i, name in enumerate(sorted(names)):
                info = parse(name)
                if not info:
                    continue
                x, rate = read_wav(zf.read(name))
                R = clip_rows(x, rate)
                with np.errstate(all="ignore"):
                    med.append(np.nanmedian(R, 0))
                    mx.append(np.nanmax(R, 0))
                meta.append((Path(name).name, info["section"], info["domain"], info["split"], info["label"]))
                if i % 200 == 0:
                    print(f"  {z.name}: {i}/{len(names)} clips", flush=True)
    meta = np.array(meta)
    np.savez_compressed(args.out, median=np.array(med, np.float32), max=np.array(mx, np.float32),
                        clip=meta[:, 0], section=meta[:, 1], domain=meta[:, 2], split=meta[:, 3], label=meta[:, 4],
                        feature_names=np.array(F.FEATURE_NAMES))
    print(f"wrote {args.out}: {len(meta)} clips")
    return 0


if __name__ == "__main__":
    sys.exit(main())
