#!/usr/bin/env python3
"""Does every simulated fault show up in the recorded features? A check of the
intake data, not a model.

Every feature is put on one scale: spreads away from normal, where normal is
learned from the baseline minutes (the first clean stretch) and a spread is how
much the feature moves there. Each minute has a mean (slow changes) and a max
(instant events); whichever is further from normal counts.

Short faults (a jam, a bump, an alarm, a fan stopping) are measured against the
hour just before them, so a fault that began earlier and is still going does
not get credited to them, and the change must also beat the feature's biggest
wobble within that hour. Slow faults (wear and heating over days) overlap other
faults and the daily cycle, so they are measured by running the simulation a
second time without that one fault, with the same noise, and comparing the
two windows by window (every few minutes through the fault). Only the fault
differs, so whatever moves is the fault's doing. This covers the per-window
features; the history features build on them.

A feature only counts as showing a fault if the change is bigger than any
change of the same kind during a separate clean normal period, and at least 5
spreads. That bar keeps the normal ups and downs of 200-odd features from
counting as detections.

  python3 -m device.intake.coverage_check data/fake/conveyor-week --sim conveyor
  python3 -m device.intake.coverage_check data/fake/server-rack --sim server
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from device.intake import extract as E
from effente import features as F

DAY = 86400
SLOW = {"idler_bearing", "fan3_bearing", "connector_heat"}
MIN_SPREADS = 5.0
REF_S = 3600        # short faults: compared with this long before them
REF_GAP_S = 120     # ... ending this long before the fault starts
STEP_WINDOW_S = 1200  # a fault that never ends (a fan stopping) is judged on its first 20 minutes
PAIRS_MAX = 1500    # slow faults: at most this many paired windows, at least 2 minutes apart
END_SHARE = 0.2     # ... and the change is the median over the last 20% of the fault
FLOOR = {"log": 0.02, "db": 0.3, "c": 0.1, "hz": 0.5, "ratio": 0.02, "count": 0.5, "s": 5.0, "min": 1.0,
         "g": 0.002, "mm_s": 0.05, "pct": 0.5, "c_per_min": 0.05, "c_per_h": 0.05, "c_per_day": 0.05}


def load(folder: Path):
    T, MEAN, MAX = [], [], []
    for p in sorted((folder / "features").glob("features_?????.npz")):
        d = np.load(p)
        T.append(d["minute_t0"])
        MEAN.append(d["minute_mean"])
        MAX.append(d["minute_max"])
    return np.concatenate(T), np.vstack(MEAN), np.vstack(MAX)


def hhmm(t):
    d = int(t // DAY)
    return f"{['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'][d % 7]} {int(t % DAY // 3600):02d}:{int(t % 3600 // 60):02d}"


def further(a, b):
    """Elementwise, whichever of a and b is further from zero."""
    return np.where(np.abs(b) > np.abs(a), b, a)


def local_change(ZM, ZX, mid, ok, rows, t_ref):
    """Change of ZM/ZX at `rows` against their median over ok minutes in the hour before t_ref,
    and the biggest such change within that hour itself (how much the feature was wobbling)."""
    ref = ok & (mid >= t_ref - REF_GAP_S - REF_S) & (mid < t_ref - REF_GAP_S)
    if ref.sum() < 20:  # e.g. the machine only just started: look further back
        ref = ok & (mid >= t_ref - REF_GAP_S - 3 * REF_S) & (mid < t_ref - REF_GAP_S)
    if ref.sum() < 5:
        nan = np.full(ZM.shape[1], np.nan)
        return np.full((np.count_nonzero(rows), ZM.shape[1]), np.nan), nan
    mm, mx = np.nanmedian(ZM[ref], 0), np.nanmedian(ZX[ref], 0)
    wobble = np.nanmax(np.abs(further(ZM[ref] - mm, ZX[ref] - mx)), 0)
    return further(ZM[rows] - mm, ZX[rows] - mx), wobble


def fault_window(f, t_end):
    """(start, end) of the minutes a short fault is judged on."""
    dur = f.end_s - f.start_s
    end = f.end_s if dur <= 3 * 3600 else f.start_s + STEP_WINDOW_S
    return f.start_s - 60, min(end, t_end) + 60


def window_rows(sim, seconds):
    """Per-window features of the 1 s window starting at each of `seconds`, each with its own
    fixed noise, so two copies of a simulation that differ in one fault can be compared."""
    rows = np.full((len(seconds), F.N_BASE), np.nan)
    for j, s in enumerate(seconds):
        sim.nrng = np.random.default_rng([sim.seed, 2, int(s)])
        sim.start_s, sim.end_s = float(s), float(s) + 1.0
        for _, x in E.feature_stream(sim, hop_s=1.0):
            rows[j] = x[F.BASE_SLICE]
            break
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path)
    ap.add_argument("--sim", choices=["conveyor", "server"], required=True)
    args = ap.parse_args(argv)
    if args.sim == "conveyor":
        from effente.simulate_conveyor import ConveyorSim as Sim
        sim = Sim(days=7)
        running = lambda t: sim.running[np.clip(t.astype(int), 0, len(sim.running) - 1)].astype(bool)
    else:
        from effente.simulate_server import ServerRackSim as Sim
        sim = Sim(days=3)
        running = lambda t: np.ones(len(t), bool)

    T, MEAN, MAX = load(args.folder)
    mid = T + 30
    run = running(mid)
    labels = np.array([sim.label_at(t) for t in mid])
    faults = [f for f in sim.faults if f.start_s < T.max()]
    near_fault = np.zeros(len(T), bool)
    for f in faults:
        near_fault |= (mid > f.start_s - 600) & (mid < f.start_s + 600)
    clean = (labels == "normal") & ~near_fault & run
    day = (mid // DAY).astype(int)
    if args.sim == "conveyor":
        base, test = clean & (day == 0), clean & (day == 1)
    else:  # one learning day: the morning is the baseline, the afternoon and evening the clean test
        base = clean & (day == 0) & (mid % DAY < 14 * 3600)
        test = clean & (day == 0) & (mid % DAY >= 14 * 3600)
    print(f"{len(T):,} minutes; baseline {base.sum():,} minutes, clean test {test.sum():,} minutes")

    use = [i for i, n in enumerate(F.FEATURE_NAMES)
           if F.FEATURE_KIND[n] != "index" and F.FEATURE_SENSOR[n] != "clock"]
    names = [F.FEATURE_NAMES[i] for i in use]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        med = np.nanmedian(MEAN[base][:, use], 0)
        mad = 1.4826 * np.nanmedian(np.abs(MEAN[base][:, use] - med), 0)
        floor = np.array([FLOOR.get(F.FEATURE_KIND[n], 0.05) for n in names])
        spread = np.maximum(np.nan_to_num(mad, nan=np.inf), np.maximum(floor, 0.02 * np.abs(med)))
        ZM = (MEAN[:, use] - med) / spread
        ZX = (MAX[:, use] - med) / spread
        ZM[:, np.isnan(med)] = np.nan
        ZX[:, np.isnan(med)] = np.nan
        Z = further(ZM, ZX)

        # Bars. Slow faults: the worst level in the clean test. Short faults: the worst change
        # against the previous hour in the clean test.
        bar_level = np.maximum(np.nan_to_num(np.nanmax(np.abs(Z[test]), 0), nan=np.inf), MIN_SPREADS)
        short_windows = np.zeros(len(T), bool)
        for f in faults:
            if f.kind not in SLOW:
                a, b = fault_window(f, T.max())
                short_windows |= (mid >= a) & (mid < b + (3600 if f.end_s - f.start_s <= 3 * 3600 else 0))
        ok_ref = run & ~short_windows
        jumps = np.vstack([local_change(ZM, ZX, mid, ok_ref, np.arange(len(T)) == j, mid[j])[0]
                           for j in np.nonzero(test)[0]])
        bar_jump = np.maximum(np.nan_to_num(np.nanmax(np.abs(jumps), 0), nan=np.inf), MIN_SPREADS)

        # Slow faults: the same simulation without that fault, window by window.
        paired = {}
        use_base = np.array([i < F.N_BASE for i in use])
        for f in faults:
            if f.kind not in SLOW:
                continue
            t_end = min(f.end_s, T.max())
            step = max(120.0, (t_end - f.start_s) / PAIRS_MAX)
            secs = np.arange(f.start_s, t_end, step).round()
            secs = secs[running(secs)]
            without = Sim(days=sim.days, faults=[g for g in sim.faults if g is not f])
            d = window_rows(sim, secs) - window_rows(without, secs)
            Dp = np.full((len(secs), len(use)), np.nan)
            Dp[:, use_base] = d[:, [i for i in use if i < F.N_BASE]] / spread[use_base]
            paired[f.kind] = (secs, Dp)

    events = [json.loads(line) for line in open(args.folder / "events.jsonl")]
    print("\nfault                when          seen?  strongest features: change in spreads (x the clean bar)")
    report = []
    for f in faults:
        if f.kind in SLOW:
            secs, Dp = paired[f.kind]
            last = secs >= secs[0] + (1 - END_SHARE) * (secs[-1] - secs[0])
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                change = np.nanmedian(Dp[last], 0)  # the fault's own effect near the end of the wear
            bar = bar_level
            view = (0, T.max())
        else:
            a, b = fault_window(f, T.max())
            rows = (mid >= a) & (mid < b)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                D, wobble = local_change(ZM, ZX, mid, ok_ref, rows, f.start_s)
                k_at = np.nanargmax(np.nan_to_num(np.abs(D), nan=-1), 0)
            change = D[k_at, np.arange(len(use))]
            bar = np.maximum(bar_jump, np.nan_to_num(wobble, nan=0.0))
            view = (f.start_s - 3 * 3600, b + 3 * 3600)
        margin = np.nan_to_num(np.abs(change) / bar, nan=0.0)
        top = np.argsort(-margin)[:5]
        seen = margin[top[0]] >= 1
        line = "  ".join(f"{names[k]} {change[k]:+.0f} (x{margin[k]:.1f})" for k in top if margin[k] > 0)
        print(f"{f.kind:20s} {hhmm(f.start_s):13s} {'yes' if seen else 'NO':5s}  {line}")
        by_sensor = []
        for sensor in ("vibration", "rotation", "sound", "heat", "room"):
            ks = [k for k in range(len(use)) if F.FEATURE_SENSOR[names[k]] == sensor]
            if ks:
                k = max(ks, key=lambda k: margin[k])
                if margin[k] > 0:
                    by_sensor.append(f"{sensor} x{margin[k]:.1f} ({names[k]})")
        print(f"{'':39s}best per sensor: {', '.join(by_sensor)}")
        if f.kind in SLOW and seen:  # when did the fault's own effect first stay past the bar?
            good = [k for k in top if margin[k] >= 1]
            n_roll = max(3, int(round(1800 / max(1.0, float(np.median(np.diff(secs)))))))  # ~30 minutes
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                roll = np.vstack([np.nanmedian(Dp[max(0, i - n_roll + 1):i + 1][:, good], 0)
                                  for i in range(len(secs))])
            over = ((np.sign(roll) == np.sign(change[good])) & (np.abs(roll) >= bar[good])).any(1)
            # the 30-minute median is past the bar, and stays past for the next 30 minutes
            hit = [i for i in range(len(secs)) if over[i:i + n_roll].all()]
            if hit:
                t_first = secs[hit[0]]
                frac = (t_first - f.start_s) / (f.end_s - f.start_s)
                print(f"{'':39s}first steadily past the clean bar {hhmm(t_first)}, "
                      f"{100 * frac:.0f}% of the way into the wear (paired runs, {len(secs)} windows)")
        near = sorted({e["event"] for e in events if e.get("t") is not None
                       and f.start_s - 120 <= e["t"] <= f.start_s + 1200
                       and e["event"].startswith(("sudden", "device"))})
        if near and f.kind not in SLOW:
            print(f"{'':39s}events logged: {', '.join(near)}")
        report.append((f, top, seen, bar, view))

    kinds_sudden = ("sudden_vibration", "sudden_sound", "sudden_heat", "device_dropped")
    away = {n: sum(1 for e in events if e["event"] == n and e.get("t") is not None
                   and not any(f.start_s - 120 <= e["t"] <= min(f.end_s, f.start_s + 4 * 3600) + 600 for f in faults))
            for n in kinds_sudden}
    print(f"\nsudden events logged away from any fault (normal bumps and noises): {away}")
    kinds = {}
    for e in events:
        kinds[e["event"]] = kinds.get(e["event"], 0) + 1
    size = sum(p.stat().st_size for p in args.folder.rglob("*") if p.is_file())
    print(f"events: {kinds}")
    print(f"folder {size / 1e6:.0f} MB, {len(list((args.folder / 'raw').glob('clip_*.npz')))} raw clips")

    fig, axes = plt.subplots(len(report), 1, figsize=(14, 2.3 * len(report)))
    for ax, (f, top, seen, bar, view) in zip(np.atleast_1d(axes), report):
        k = top[0]
        sel = (mid >= view[0]) & (mid <= view[1])
        ax.plot(mid[sel] / DAY, Z[sel, k], lw=0.5, label="recorded")
        if f.kind in SLOW:
            secs, Dp = paired[f.kind]
            ax.plot(secs / DAY, Dp[:, k], lw=0.7, color="k", label="the fault's own effect (paired runs)")
            ax.axhline(bar[k], color="r", lw=0.6, ls=":")
            ax.axhline(-bar[k], color="r", lw=0.6, ls=":")
            ax.legend(fontsize=7, loc="upper left")
        ax.axvspan(f.start_s / DAY, min(f.end_s, T.max()) / DAY, color="orange", alpha=0.25)
        ax.set_ylabel("spreads", fontsize=8)
        extra = "; red lines: the clean bar" if f.kind in SLOW else ""
        ax.set_title(f"{f.kind}: {names[k]} ({'seen' if seen else 'not seen'}){extra}", fontsize=9)
        ax.grid(alpha=0.3)
    np.atleast_1d(axes)[-1].set_xlabel("day (0 = Monday)")
    fig.tight_layout()
    out = args.folder / "coverage.png"
    fig.savefig(out, dpi=100)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
