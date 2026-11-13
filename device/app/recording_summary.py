#!/usr/bin/env python3
"""What a recording pulled from the board looks like (device/app/pull_recording.sh): rows and gaps,
which sensors and columns are empty, vibration and temperature levels, the raw clips' sample rates,
the events, and the model's training report. Reads only; prints text.

  python3 device/app/recording_summary.py data/real/board/rec-20261113-141500
"""

from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from effente import features as F  # noqa: E402
from effente import safety as SF  # noqa: E402
from effente.model_input import load_minutes, load_recording  # noqa: E402


def spread(v) -> str:
    v = np.asarray(v, float)
    v = v[np.isfinite(v)]
    if not len(v):
        return "no values"
    p = np.percentile(v, [5, 50, 95])
    return f"median {p[1]:.4g} (5-95 %: {p[0]:.4g} to {p[2]:.4g}; min {v.min():.4g}, max {v.max():.4g})"


def main(argv=None) -> int:
    root = Path((argv or sys.argv[1:])[0])
    rec = root / "recording" if (root / "recording").is_dir() else root
    X, t, names = load_recording(rec)
    X = np.asarray(X, float)
    col = lambda n: X[:, names.index(n)]  # noqa: E731
    span = float(t[-1] - t[0]) if len(t) > 1 else 0.0
    print(f"recording {rec}")
    print(f"rows: {len(t)} one-a-second rows over {span:.0f} s ({len(t) / max(span + 1, 1):.3f} a second), "
          f"{X.shape[1]} features; all on whole seconds: {bool(np.allclose(t, np.round(t)))}")
    d = np.diff(t)
    gaps = [(float(t[i]), float(d[i])) for i in np.nonzero(d > 1.5)[0]]
    print(f"gaps between rows (over 1.5 s): {len(gaps)}" + "".join(f"\n  after t={a:.0f}: {g:.0f} s" for a, g in gaps[:20]))
    try:
        mt, stats, _ = load_minutes(rec, stats=("median",))
        print(f"minute summaries: {len(mt)}")
    except Exception as e:  # noqa: BLE001
        print(f"minute summaries: not readable ({e})")

    print("sensors (share of rows where every column of the sensor is empty):")
    for sensor, sl in F.SLICES.items():
        print(f"  {sensor:9s} {100 * np.isnan(X[:, sl]).all(1).mean():5.1f} %")
    allnan = [n for j, n in enumerate(names) if np.isnan(X[:, j]).all()]
    some = [(n, float(np.isnan(X[:, j]).mean())) for j, n in enumerate(names)
            if 0 < np.isnan(X[:, j]).mean() < 1]
    by = collections.Counter(n.split("_")[0] for n in allnan)
    print(f"columns empty in every row: {len(allnan)} of {len(names)} ({dict(by)})")
    kept = [n for n in allnan if n.split("_")[0] in ("vib", "gyr", "imu", "slow", "time")]
    if kept:
        print(f"  of the IMU's own columns: {kept}")
    if some:
        print("columns empty in some rows: " + ", ".join(f"{n} {100 * s:.0f} %" for n, s in some[:40])
              + (" ..." if len(some) > 40 else ""))

    R = [SF.readings(x) for x in X]
    print("levels:")
    for label, key, k in (("vibration, total (mg)", "vib_total_g", 1000.0), ("vibration velocity (mm/s)", "vib_velocity_mm_s", 1.0),
                          ("gravity (g)", "gravity_g", 1.0), ("IMU temperature (C)", "imu_temp_c", 1.0)):
        print(f"  {label:27s} {spread([k * r[key] for r in R])}")
    print(f"  {'peak (g)':27s} {spread(10 ** col('vib_peak_g'))}")
    print(f"  {'running speed (Hz)':27s} {spread(col('vib_speed_hz'))}")
    for a in "xyz":
        print(f"  {'rms ' + a + ' (mg)':27s} {spread(1000 * 10 ** col('vib_rms_' + a))}")
    run = col("trend_running")
    print(f"counted as running: {100 * np.mean(~(run < 0.5)):.0f} % of rows (not known yet: {100 * np.isnan(run).mean():.0f} %)")

    clips = sorted((rec / "raw").glob("clip_*.npz"))
    print(f"raw clips: {len(clips)}")
    for p in clips:
        with np.load(p) as c:
            ta = c["accel_t_us"] / 1e6
            n = len(ta)
            dt = np.diff(ta) * 1000
            s = c["samples"].astype(float) / float(c["accel_lsb_per_g"])
            gy = len(c["gyro"]) if "gyro" in c.files else 0
            it = len(c["imu_temp_c"]) if "imu_temp_c" in c.files else 0
            print(f"  {p.name}: {n} accel samples over {ta[-1] - ta[0]:.2f} s = {(n - 1) / max(ta[-1] - ta[0], 1e-9):.1f} a second; "
                  f"steps over 2 ms: {int((dt > 2).sum())} (longest {dt.max():.1f} ms); mean {np.round(s.mean(0), 3).tolist()} g, "
                  f"rms about the mean {np.round(1000 * s.std(0), 1).tolist()} mg; gyro samples {gy}; temperature readings {it}; "
                  f"audio {c['audio'].size}, thermal frames {len(c['thermal'])}")

    ev = rec / "events.jsonl"
    if ev.exists():
        events = [json.loads(line) for line in ev.read_text().splitlines() if line.strip()]
        print(f"events.jsonl: {dict(collections.Counter(e['event'] for e in events))}")
        for e in events:
            if e["event"] in ("gap", "sensor_lost", "sensor_back", "sensor_missing_at_start", "sensor_stuck", "sensor_clipping",
                              "device_moved", "device_dropped", "recording_stopped"):
                print(f"  {e}")
    for m in sorted((root / "models").glob("*.json")) if (root / "models").is_dir() else []:
        if m.name.endswith(".early.json"):
            continue
        dmodel = json.loads(m.read_text())
        print(f"model {m.name}: {json.dumps(dmodel.get('training_report'), indent=1)}")
        b = dmodel.get("baseline") or {}
        print(f"  baseline levels {b.get('levels')}; screen notes (not shown) {b.get('warnings_screen')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
