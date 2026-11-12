#!/usr/bin/env python3
"""Turn recordings into training-ready features. No model here.

  python3 -m device.intake.extract --data data/fake/normal-day-01 \\
      --out data/fake/normal-day-01/features.npz

Reads every recording file in --data (hour_*.npz or clip_*.npz), aligns the
sensors into one-second windows by timestamp (a new window every 0.25 s by
default), computes effente.features for each window, and writes one .npz the
training side can load directly:

  X               float32 (windows, features); NaN where a sensor was missing
  t0              float64 (windows,) window start, seconds on the device clock
                  (overlapping windows share data, so neighbouring rows are
                  similar: use --hop 1 or --stride 4 for independent rows)
  feature_names   (features,) in effente.features order
  feature_sensor  (features,) vibration, sound, heat or room
  feature_text    (features,) what each feature means
  feature_kind    (features,) units: log (log10), hz, ratio, g, db, c, pct
  label           (windows,) label of the recording the window came from
  truth_state, truth_load, truth_events
                  (windows,) only when the folder has truth.csv (fake data)
  window_s, hop_s, features_version, source, created_at

`features_version` changes whenever the feature list changes, so training
and the device can check they agree. The same `feature_stream()` will feed
a model on the device later.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import numpy as np

from effente import features as F
from effente.history import FeatureEngine, History
from device.intake.source import FileSource, SourceInfo
from device.intake.windowing import HOP_S, WINDOW_S, Windower


def features_version() -> str:
    return hashlib.sha1("|".join(F.FEATURE_NAMES).encode()).hexdigest()[:12]


def to_window(t0: float, parts: dict, info: SourceInfo) -> F.Window:
    w = F.Window(t0=t0, audio_rate_hz=info.audio_rate_hz, audio_gain_db=info.audio_gain_db,
                 audio_full_scale=float(2 ** (getattr(info, "audio_bits", 16) - 1) - 1),
                 accel_range_g=info.accel_range_g,
                 week_t=None if info.week_offset_s is None else t0 + info.week_offset_s)
    if parts.get("accel") is not None:
        t, v = parts["accel"]
        w.accel_g, w.accel_t = v, t
        if len(t) > 1:
            w.accel_rate_hz = (len(t) - 1) / (t[-1] - t[0])  # real rate, not nominal
    if parts.get("gyro") is not None:
        w.gyro_dps = parts["gyro"][1]
    if parts.get("audio") is not None:
        w.audio = parts["audio"][1]
    if parts.get("thermal") is not None:
        w.thermal_c = parts["thermal"][1]
    if parts.get("room") is not None:
        w.room = parts["room"][1]
    return w


def feature_stream(source, window_s: float = WINDOW_S, hop_s: float = HOP_S, stride: int = 1,
                   timing: dict | None = None, tap=None, packets=None, history: History | None = None
                   ) -> Iterator[tuple[float, np.ndarray]]:
    """Yield (window start, full feature row) for every `stride`-th window.

    Windows are `window_s` long and start every `hop_s`, so the default gives
    4 updates a second, each over a full second of data. Rows include the
    history features, computed by `history` (a fresh one if not given; pass a
    loaded one to continue after a restart). `tap(packet)` sees every raw
    packet first (the recorder uses it for raw snippets), and `packets`
    replaces `source.packets()` (for example a throttled replay). `source`
    is anything with `packets()`, `info` and `sensors`: a FileSource, a
    simulator, or the UNO Q source later.
    """
    wd = Windower(window_s=window_s, hop_s=hop_s, sensors=source.sensors)
    engine = FeatureEngine(history)
    k = 0

    def drain():
        nonlocal k
        for t0, parts in wd.ready():
            k += 1
            if (k - 1) % stride:
                continue
            t = time.perf_counter()
            x = engine.update(to_window(t0, parts, source.info))
            if timing is not None:
                timing["seconds"] = timing.get("seconds", 0.0) + time.perf_counter() - t
                timing["windows"] = timing.get("windows", 0) + 1
            yield t0, x

    for p in (packets if packets is not None else source.packets()):
        if tap is not None:
            tap(p)
        wd.push(p)
        yield from drain()
    wd.finish()
    yield from drain()


def read_truth(folder: Path):
    path = folder / "truth.csv"
    if not path.exists():
        return None
    with open(path) as f:
        return {int(r["second"]): (r["state"], float(r["load"]), r["events"]) for r in csv.DictReader(f)}


def parse_time(text: str) -> float:
    """HH:MM, or D:HH:MM for a day of a multi-day run (day 0 is the first)."""
    parts = [int(v) for v in text.split(":")]
    d, hh, mm = ([0] + parts)[-3:] if len(parts) < 3 else parts
    return d * 86400 + hh * 3600 + mm * 60


def add_source_args(ap) -> None:
    ap.add_argument("--data", help="folder of recording files to replay")
    ap.add_argument("--sim", choices=["conveyor", "server"], help="stream a simulated machine instead of files")
    ap.add_argument("--sim-days", type=int, default=7, help="length of the simulated week")
    ap.add_argument("--from", dest="t_from", default="00:00", help="HH:MM, or D:HH:MM for day D of a simulation")
    ap.add_argument("--to", dest="t_to", default=None, help="HH:MM or D:HH:MM (default: the end)")


def make_source(args):
    t_from = parse_time(args.t_from)
    if args.sim == "conveyor":
        from effente.simulate_conveyor import DAY, ConveyorSim
        t_to = parse_time(args.t_to) if args.t_to else args.sim_days * DAY
        return ConveyorSim(days=args.sim_days, start_s=t_from, end_s=t_to)
    if args.sim == "server":
        from effente.simulate_server import DAY, ServerRackSim
        days = min(args.sim_days, 3)
        t_to = parse_time(args.t_to) if args.t_to else days * DAY
        return ServerRackSim(days=days, start_s=t_from, end_s=t_to)
    if not args.data:
        raise SystemExit("give --data FOLDER or --sim conveyor")
    return FileSource(args.data, t_from, parse_time(args.t_to) if args.t_to else 86400.0)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", required=True, help="folder of recording files")
    ap.add_argument("--out", required=True, help="output .npz")
    ap.add_argument("--from", dest="t_from", default="00:00")
    ap.add_argument("--to", dest="t_to", default="24:00")
    ap.add_argument("--window", type=float, default=WINDOW_S, help="window length in seconds (default 1)")
    ap.add_argument("--hop", type=float, default=HOP_S,
                    help="seconds between window starts (default 0.25, so 4 windows a second)")
    ap.add_argument("--stride", type=int, default=1, help="keep every Nth window (default all)")
    ap.add_argument("--csv", help="also write the features as CSV (large)")
    args = ap.parse_args(argv)

    folder = Path(args.data)
    source = FileSource(folder, parse_time(args.t_from), parse_time(args.t_to))
    timing: dict = {}
    started = time.perf_counter()
    t0s, rows = [], []
    next_report = None
    for t0, x in feature_stream(source, args.window, args.hop, args.stride, timing):
        t0s.append(t0)
        rows.append(x.astype(np.float32))
        if next_report is None:
            next_report = (t0 // 3600 + 1) * 3600
        if t0 >= next_report:
            print(f"  {int(t0 // 3600):02d}:00  {len(rows):,} windows, {time.perf_counter() - started:.0f} s",
                  flush=True)
            next_report += 3600
    if not rows:
        print("no windows found", file=sys.stderr)
        return 1

    X = np.vstack(rows)
    t0 = np.array(t0s)
    labels = np.array([source.label_at(t + 0.5 * args.window) for t in t0])
    extra = {}
    truth = read_truth(folder)
    if truth:
        rec = [truth.get(int(t + 0.5 * args.window), ("", np.nan, "")) for t in t0]  # truth at window middle
        extra = {"truth_state": np.array([r[0] for r in rec]), "truth_load": np.array([r[1] for r in rec]),
                 "truth_events": np.array([r[2] for r in rec])}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        out, X=X, t0=t0, label=labels,
        feature_names=np.array(F.FEATURE_NAMES), feature_sensor=np.array([F.sensor_of(n) for n in F.FEATURE_NAMES]),
        feature_text=np.array([F.FEATURE_TEXT[n] for n in F.FEATURE_NAMES]),
        feature_kind=np.array([F.FEATURE_KIND[n] for n in F.FEATURE_NAMES]),
        window_s=args.window, hop_s=args.hop, features_version=features_version(), source=str(folder),
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"), **extra,
    )
    if args.csv:
        with open(args.csv, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["t0", "label"] + F.FEATURE_NAMES)
            for t, lab, x in zip(t0, labels, X):
                w.writerow([f"{t:.3f}", lab] + [f"{v:.5g}" for v in x])

    n = timing.get("windows", 0)
    print(f"wrote {out}: {X.shape[0]:,} windows x {X.shape[1]} features, "
          f"{out.stat().st_size / 1e6:.1f} MB, features version {features_version()}")
    print(f"features: {1000 * timing.get('seconds', 0) / max(1, n):.2f} ms/window; "
          f"total {time.perf_counter() - started:.0f} s")
    for sensor, sl in F.SLICES.items():
        missing = int(np.isnan(X[:, sl]).all(1).sum())
        print(f"  {sensor:9s} missing in {missing:,} windows")
    if truth:
        states, counts = np.unique(extra["truth_state"], return_counts=True)
        print("  truth states: " + ", ".join(f"{s} {c:,}" for s, c in zip(states, counts)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
