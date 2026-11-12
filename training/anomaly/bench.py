#!/usr/bin/env python3
"""Compare "is this machine still normal?" models on the same data, the same way.

    python3 -m training.anomaly.bench --run data/fake/conveyor-2wk-faults --clean data/fake/conveyor-2wk-clean \\
        --sim conveyor --sim-days 14 --fault-shift-days 7 --site factory --site-seed 11

What it does, like a device's first fortnight:
  1. Learn: every model learns this machine's normal from the per-minute summaries of the
     first `--learn-days` (running minutes only), prepared by effente.model_input.Prep.
  2. Calibrate: the alarm threshold is set on the last learning day that had running time,
     held out from learning: for one false alarm a week, from a gamma fit to that day's scores.
  3. Watch: the rest is scored minute by minute. An alarm needs the score over the threshold
     for the median of the last `--smooth` minutes (persistence; causal, no peeking ahead).
  4. Judge:
     - false alarms per day: alarm episodes in the clean twin run's watch period (the same
       machine, site and noise with the faults switched off), with what was going on at the
       site when each started (people, forklifts, sun, ...)
     - each fault: caught or not, how many minutes after it began, and for slow faults how far
       into the wear
     - AUC: how well the score separates fault minutes from normal ones, threshold-free
       (1.0 perfect, 0.5 chance)

The fault and nuisance labels come from the same simulator and site settings as the run.
Simulated data only: this says which model suits the data as simulated, not how a model will
do on a real machine (unverified).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
from sklearn.metrics import roc_auc_score

from effente.model_input import Prep, load_minutes
from device.intake import extract as E
from training.anomaly import detectors as D

DAY = 86400.0


def minutes(folder, stat: str):
    t, stats, names = load_minutes(folder)
    o = np.argsort(t)
    return t[o], stats[stat][o], names


def running(X, names):
    if "trend_running" in names:
        return X[:, names.index("trend_running")] >= 0.5
    return np.ones(len(X), bool)


def causal_median(x, n: int):
    out = np.empty_like(x)
    for i in range(len(x)):
        out[i] = np.median(x[max(0, i - n + 1):i + 1])
    return out


def episodes(flags):
    """(start, end) index pairs of runs of True."""
    out, start = [], None
    for i, f in enumerate(flags):
        if f and start is None:
            start = i
        elif not f and start is not None:
            out.append((start, i))
            start = None
    if start is not None:
        out.append((start, len(flags)))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", required=True, help="learn-mode recording folder, with faults")
    ap.add_argument("--clean", help="the same run with no faults (for false alarms)")
    E.add_source_args(ap)
    ap.add_argument("--learn-days", type=float, default=7.0)
    ap.add_argument("--stat", default="median", choices=["median", "mean"], help="per-minute summary to judge")
    ap.add_argument("--smooth", type=int, default=5, help="minutes of persistence (causal median)")
    ap.add_argument("--models", help="comma-separated subset of model names")
    ap.add_argument("--out", help="write the results as JSON")
    args = ap.parse_args(argv)

    src = E.make_source(args)
    faults = list(getattr(src, "faults", []))
    nuisance = getattr(src, "nuisance_at", None)

    t, X, names = minutes(args.run, args.stat)
    run = running(X, names)
    day = (t // DAY).astype(int)
    learn = t < args.learn_days * DAY
    run_days = [d for d in sorted(set(day[learn])) if (run & learn & (day == d)).sum() >= 60]
    if len(run_days) < 2:
        raise SystemExit("need at least 2 learning days with running time")
    calib = learn & run & (day == run_days[-1])
    fit = learn & run & (day < run_days[-1])
    watch = ~learn & run
    print(f"{len(t):,} minutes; learning on {fit.sum():,} running minutes (days {run_days[:-1]}), "
          f"threshold from day {run_days[-1]} ({calib.sum()} minutes), watching {watch.sum():,} running minutes")

    if args.clean:
        tc, Xc, names_c = minutes(args.clean, args.stat)
        assert names_c == names, "the clean run was made with other features"
        run_c = running(Xc, names_c)
        watch_c = (tc >= args.learn_days * DAY) & run_c
    labels = np.array([src.label_at(float(x) + 30) for x in t])

    prep = Prep.fit(X[fit], names, t[fit])
    Z = prep.transform(X)
    Zc = prep.transform(Xc) if args.clean else None
    print(f"model inputs: {prep.n_out} ({len(prep.columns)} features kept of {len(names)})")

    chosen = [c for c in D.ALL if not args.models or c.name in args.models.split(",") or c.__name__ in args.models.split(",")]
    results = []
    for cls in chosen:
        det = cls()
        t0 = time.perf_counter()
        det.fit(Z[fit])
        fit_s = time.perf_counter() - t0
        s, _ = det.score(Z)
        sm = causal_median(s, args.smooth)
        thr = D.threshold(sm[calib], rows_per_week=7 * 1440)  # one false alarm a week of running minutes
        alarm = (sm > thr) & run
        res = {"model": det.name, "fit_s": round(fit_s, 1), "threshold": thr}

        # false alarms
        if args.clean:
            sc, _ = det.score(Zc)
            smc = causal_median(sc, args.smooth)
            alarm_c = (smc > thr) & watch_c
            eps = episodes(alarm_c)
            days_watched = watch_c.sum() / 1440.0 if watch_c.any() else 1.0
            causes = {}
            for a, b in eps:
                kinds = sorted({k for i in range(a, b) for k in (nuisance(float(tc[i]) + 30) if nuisance else [])}) or ["none"]
                for k in kinds:
                    causes[k] = causes.get(k, 0) + 1
            res["false_alarms_per_day"] = round(len(eps) / max(days_watched, 1e-9), 2)
            res["false_alarm_causes"] = causes
            normal_scores = smc[watch_c]
        else:
            normal = watch & (labels == "normal")
            eps = episodes(alarm & normal)
            res["false_alarms_per_day"] = round(len(eps) / max(normal.sum() / 1440.0, 1e-9), 2)
            normal_scores = sm[normal]

        # each fault
        per = []
        for f in faults:
            end = min(f.end_s, float(t.max()) + 60)
            short = f.end_s - f.start_s <= 3 * 3600
            win = (t >= f.start_s) & (t < end + (1800 if short else 0)) & run
            hit = np.nonzero(alarm & win)[0]
            item = {"fault": f.kind, "caught": bool(len(hit))}
            if len(hit):
                first = float(t[hit[0]])
                item["after_min"] = round((first - f.start_s) / 60.0, 1)
                if not short:
                    item["share_of_wear"] = round((first - f.start_s) / max(end - f.start_s, 1.0), 2)
            per.append(item)
        res["faults"] = per
        res["caught"] = sum(p["caught"] for p in per)
        fault_min = watch & (labels != "normal")
        if fault_min.any() and len(normal_scores):
            y = np.r_[np.zeros(len(normal_scores)), np.ones(fault_min.sum())]
            res["auc"] = round(float(roc_auc_score(y, np.r_[normal_scores, sm[fault_min]])), 3)
        results.append(res)
        print(f"  {det.name:30s} caught {res['caught']}/{len(faults)}  false alarms/day {res['false_alarms_per_day']:5.2f}"
              f"  AUC {res.get('auc', float('nan')):.3f}  (fit {fit_s:.0f} s)", flush=True)

    print("\nper fault (minutes after it began; for slow faults, share of the wear):")
    head = "  " + "".join(f"{r['model'][:18]:>20s}" for r in results)
    print(f"  {'fault':18s}" + head[2:])
    for j, f in enumerate(faults):
        row = f"  {f.kind:18s}"
        for r in results:
            p = r["faults"][j]
            cell = "missed" if not p["caught"] else (f"{p['after_min']:.0f} min" + (f" ({p['share_of_wear']:.0%})"
                                                                                  if "share_of_wear" in p else ""))
            row += f"{cell:>20s}"
        print(row)
    if args.clean:
        print("\nfalse alarms by what was going on at the site:")
        for r in results:
            print(f"  {r['model']:30s} {r['false_alarm_causes']}")
    if args.out:
        Path(args.out).write_text(json.dumps({"run": args.run, "clean": args.clean, "results": results}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
