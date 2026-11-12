#!/usr/bin/env python3
"""Run the candidate models on REAL data turned into effente features.

    python3 -m training.anomaly.real dcase data/real/dcase2024-fan/features.npz
    python3 -m training.anomaly.real wear data/real/nasa-ims/test2_bearing1.npz --learn-h 36 --calib-h 12

dcase: learn the normal training clips; AUC (and pAUC at 10 % false alarms, as DCASE reports it)
       of normal vs anomalous test clips, overall and for the source and target domains (target =
       recorded in changed conditions, the harder case).
wear:  a run to failure, one row every few minutes: learn the first --learn-h hours, set the
       threshold on the next --calib-h hours (for one false alarm a week, from a gamma fit), then report when
       each model first alarms, as hours before the end of the run (the failure).

Same preparation (effente.model_input.Prep) and the same detectors as the simulated bench.
"""

from __future__ import annotations

import argparse
import sys
import time

import numpy as np
from sklearn.metrics import roc_auc_score

from effente.model_input import Prep
from training.anomaly import detectors as D


def pauc(y, s, max_fpr=0.1):
    return float(roc_auc_score(y, s, max_fpr=max_fpr))


def run_dcase(path, stat):
    d = np.load(path, allow_pickle=False)
    X = d[stat].astype(float)
    names = [str(n) for n in d["feature_names"]]
    split, label, domain = d["split"], d["label"], d["domain"]
    train, test = split == "train", split == "test"
    prep = Prep.fit(X[train], names)
    Z = prep.transform(X)
    y = (label[test] == "anomaly").astype(int)
    print(f"{train.sum()} normal training clips, {test.sum()} test clips ({y.sum()} anomalous); "
          f"{len(prep.columns)} sound features used")
    print(f"  {'model':30s} {'AUC':>6s} {'pAUC':>6s} {'AUC source':>11s} {'AUC target':>11s}")
    for cls in D.ALL:
        det = cls().fit(Z[train])
        s, _ = det.score(Z[test])
        row = [roc_auc_score(y, s), pauc(y, s)]
        for dom in ("source", "target"):
            m = domain[test] == dom
            row.append(roc_auc_score(y[m], s[m]) if m.any() and 0 < y[m].sum() < m.sum() else float("nan"))
        print(f"  {det.name:30s} {row[0]:6.3f} {row[1]:6.3f} {row[2]:11.3f} {row[3]:11.3f}")


def causal_median(x, n):
    return np.array([np.median(x[max(0, i - n + 1):i + 1]) for i in range(len(x))])


def run_wear(path, learn_h, calib_h, smooth):
    d = np.load(path, allow_pickle=False)
    t, X = d["t"].astype(float), d["X"].astype(float)
    names = [str(n) for n in d["feature_names"]]
    hours = (t - t[0]) / 3600.0
    end = hours[-1]
    fit, calib = hours < learn_h, (hours >= learn_h) & (hours < learn_h + calib_h)
    watch = hours >= learn_h + calib_h
    prep = Prep.fit(X[fit], names)
    Z = prep.transform(X)
    print(f"{len(t)} rows over {end:.0f} h; learning on the first {learn_h:.0f} h ({fit.sum()} rows), threshold "
          f"from the next {calib_h:.0f} h; {len(prep.columns)} features used")
    print(f"  {'model':30s} {'first alarm':>12s} {'hours before the end':>21s} {'alarms in the first half of watch':>34s}")
    half = watch & (hours < learn_h + calib_h + (end - learn_h - calib_h) / 2)
    for cls in D.ALL:
        t0 = time.perf_counter()
        det = cls().fit(Z[fit])
        s, _ = det.score(Z)
        sm = causal_median(s, smooth)
        rows_per_week = 7 * 24 / max(np.median(np.diff(hours)), 1e-9)
        thr = D.threshold(sm[calib], rows_per_week)
        alarm = watch & (sm > thr)
        first = hours[alarm][0] if alarm.any() else None
        print(f"  {det.name:30s} {('%.1f h' % first) if first is not None else 'none':>12s} "
              f"{('%.1f' % (end - first)) if first is not None else '-':>21s} {int((alarm & half).sum()):>34d}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("dcase")
    a.add_argument("features")
    a.add_argument("--stat", default="median", choices=["median", "max"])
    b = sub.add_parser("wear")
    b.add_argument("features")
    b.add_argument("--learn-h", type=float, default=36.0)
    b.add_argument("--calib-h", type=float, default=12.0)
    b.add_argument("--smooth", type=int, default=3, help="rows of persistence (causal median)")
    args = ap.parse_args(argv)
    if args.cmd == "dcase":
        run_dcase(args.features, args.stat)
    else:
        run_wear(args.features, args.learn_h, args.calib_h, args.smooth)
    return 0


if __name__ == "__main__":
    sys.exit(main())
