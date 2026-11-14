#!/usr/bin/env python3
"""System C with the device's REAL classes, not the eval's copies: learn on normal-w1 + normal-w2, watch heldout and
the 30 fault runs (model_search's rot_heldout), judged by model_search's rules.

  model         effente.trainer.train_model on the two weeks' 1-minute medians (what load_for_training gives a
                long recording), then effente.model_runtime.Watcher minute by minute at sensitivity 0.5
  early warning effente.early_warning.EarlyWarning(model, calibrate(...)) with its defaults
  per second    effente.safety.Safety over every one-a-second row (the outside-normal checks, the universal limits and
                more_impacts, its levels from learn_spike_levels on the two weeks' one-a-second rows via
                effente.model_input.load_columns), at sensitivity 0.5
An alert minute is any of: the Watcher's diagnosis is a fault (as the device's verdict), the early warning is on, or a
safety problem is on at any second of the minute. No jam rules (they are not in the device).

    PYTHONPATH=<worktree> nice -n 10 python3 -m training.record.conveyor_eval.verify_system_c
"""

from __future__ import annotations

import json
import sys
import time

import numpy as np

from effente import early_warning as EW
from effente import features as F
from effente import model_runtime as RT
from effente import safety as SF
from effente.model_input import load_columns, load_recording
from effente.trainer import to_current, train_model
from training.record.conveyor_eval import system_search as S

MS = S.MS
NOT_WRONG = ("normal", "warming_up", "stopped", None)


def train():
    chunks, names = MS.fold_chunks("both", "median")
    t = np.concatenate([c[0] for c in chunks])
    X = np.vstack([c[1] for c in chunks])
    d, rep = train_model(t, X, list(F.FEATURE_NAMES), period=60, source="normal-w1 + normal-w2")
    ts, cols, offset = [], {c: [] for c in SF.SPIKE_FEATURES + ("trend_running",)}, 0.0
    for w in MS.WEEKS:
        tt, cc = load_columns(S.DATA / w, list(cols))
        ts.append(tt + offset)
        offset = float(ts[-1].max()) + 1.0
        for c in cols:
            cols[c].append(cc[c])
    cols = {c: np.concatenate(v) for c, v in cols.items()}
    spikes = SF.learn_spike_levels(np.concatenate(ts), cols, ~(cols["trend_running"] < 0.5))
    if spikes:
        d["baseline"]["spikes"] = spikes
    model = RT.Model(d)
    calib = EW.calibrate(model, t, X, list(F.FEATURE_NAMES))
    return d, rep, model, calib


def watch(name, P, model, calib, d):
    """Per minute of the run: (model alarm, early warning on, safety problem on, which safety checks)."""
    r = MS.runs()[name]
    mt, Xm = r["t"], r["median"]
    w = RT.Watcher(model, margin=SF.margins(0.5)["model"])
    ew = EW.EarlyWarning(model, calib, sensitivity=0.5)
    alarm, early = np.zeros(len(mt), bool), np.zeros(len(mt), bool)
    for k, (tk, x) in enumerate(zip(mt, Xm)):
        w._judge(float(tk), x)
        st = w.state
        alarm[k] = st.get("diagnosis") not in NOT_WRONG
        ew.observe(float(tk), st.get("diagnosis") != "stopped", row=x, alarm=bool(alarm[k]))
        early[k] = ew.on
    X, t0, names = load_recording(S.DATA / name)
    X = to_current(X, names)
    order = np.argsort(t0, kind="stable")
    X, t0 = X[order], np.asarray(t0, float)[order]
    sf = SF.Safety(SF.baseline_of(d), sensitivity=0.5)
    safety = np.zeros(len(mt), bool)
    which = {}
    idx = np.searchsorted(mt, t0, "right") - 1
    for i, (tk, x) in enumerate(zip(t0, X)):
        sf.update(float(tk), x)
        if sf.active and 0 <= idx[i] < len(mt):
            safety[idx[i]] = True
            for n in sf.active:
                which.setdefault(n, set()).add(int(idx[i]))
    return alarm, early, safety, {n: sorted(v) for n, v in which.items()}


def main() -> int:
    t0 = time.time()
    d, rep, model, calib = train()
    print(f"trained: {rep['line']}; patterns {rep['patterns_kept']}; threshold {d['threshold']:.3f}; spikes "
          f"{d['baseline'].get('spikes', {}).get('features')} [{time.time() - t0:.0f} s]", flush=True)
    P = MS.Prepared(watch="heldout")
    parts = {}
    for name in P.runs:
        t1 = time.time()
        parts[name] = watch(name, P, model, calib, d)
        print(f"  {name}: {time.time() - t1:.0f} s", flush=True)
    res = {}
    for label, pick in (("model alarm", (0,)), ("model + early warning", (0, 1)), ("per-second (safety + more_impacts)", (2,)),
                        ("whole device", (0, 1, 2))):
        fl = {name: np.logical_or.reduce([parts[name][i] for i in pick])[:, None] for name in P.runs}
        r = MS.judge_flags(P, fl, details=True)[0]
        res[label] = {k: r[k] for k in ("fp", "fp_week", "fp_heldout", "fp_preonset", "caught", "missed", "lateness",
                                        "missed_runs", "slow_wear_caught_median", "sudden_median_min")}
        print(f"{label:38s} false alerts {r['fp']} ({r['fp_week']}/week; heldout {r['fp_heldout']}, pre-onset "
              f"{r['fp_preonset']}) | missed {r['missed']} {r['missed_runs']} | late {r['lateness']}", flush=True)
    res["safety_checks_on"] = {name: {n: len(v) for n, v in parts[name][3].items()} for name in P.runs}
    res["training_report"] = rep
    S.write("verify_system_c.json", res)
    return 0


if __name__ == "__main__":
    sys.exit(main())
