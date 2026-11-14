"""The device as it is, on SSCC Protocol A: effente.trainer.train_model + the Watcher's alarm rules + effente.safety.

Per condition (load, speed, clean|noise) with fault clips: learn from the first 60 % of its normal clips (as one
recording, one feature row a second, the device's own trainer: 1 s periods, tail line, spike line), then judge
  - the held-out normal clips, run through as the stream they are (consecutive in time), and
  - every fault clip, as if the fault started at its first second, after the last seconds of the normal stream.
The model alarms as the Watcher does: the median of the last 5 one-second scores over the threshold x margin, or
one second over the spike line x margin (margin 1 = the default sensitivity 0.5). The vectorised rule here is
checked against the real Watcher class on one condition (check_watcher).
Safety: effente.safety.Safety with the baseline learn_baseline gives plus the spike levels learn_spike_levels
gives (as device/intake/device_app.py does after training); a check counts if it starts during the clip.

    python -m training.record.sscc_eval.eval_device [--only heavy_vel100_clean]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import warnings

import numpy as np

from effente import features as F
from effente import model_runtime as RT
from effente import safety as SF
from effente import trainer as TR
from training.record.sscc_eval import common as C

PRE_S = 10  # normal seconds fed to Safety before each fault clip


def rows_of(data, ids):
    X = data.feats["dev"][ids].reshape(-1, F.N_FEATURES).astype(float)
    t = (data.meta["t_start"][ids].astype(float)[:, None] + np.arange(5)[None, :]).ravel()
    return t, X


def train(data, learn):
    t, X = rows_of(data, learn)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        t0 = time.perf_counter()
        d, report = TR.train_model(t, X, list(F.FEATURE_NAMES))
        secs = time.perf_counter() - t0
        cols = {c: X[:, F.INDEX[c]] for c in SF.SPIKE_FEATURES_SHORT + SF.MOMENT_COLUMNS + ("trend_running",)}
        spikes = SF.learn_spike_levels(t, cols, ~(cols["trend_running"] < 0.5))
    if spikes:
        d["baseline"]["spikes"] = spikes
    report["train_seconds_pc"] = round(secs, 2)
    return d, report


def safety_started(baseline, t, X, judge_from: float):
    """Names of the checks that start at t >= judge_from, with the first second (relative to judge_from)."""
    s = SF.Safety(baseline, gravity_check=True)
    first = {}
    for ti, x in zip(t, X):
        started, _ = s.update(float(ti), x)
        if ti >= judge_from:
            for p in started:
                first.setdefault(p["name"], int(ti - judge_from))
    return first


def check_watcher(model, X_stream, t_stream, thr, spike) -> dict:
    """Run the real Watcher over a stream and compare its alarm seconds with the vectorised rule."""
    w = RT.Watcher(model, margin=1.0)
    alarms = []
    for ti, x in zip(t_stream, X_stream):
        st = w.predict_at(float(ti), x)
        alarms.append(st.get("diagnosis") not in ("normal", "warming_up", None))
    s = model.score(X_stream)[0]
    med = C.running_median(s / thr, 5)
    vec = (med > 1) | (s / spike > 1)
    # the Watcher judges a second when the next one arrives: its state at row i is the verdict on row i-1
    w_al = np.array(alarms[1:])
    held = np.array(alarms[1:]) & ~vec[:-1]
    return {"rows": len(s), "watcher_alarm_rows": int(w_al.sum()), "vector_alarm_rows": int(vec[:-1].sum()),
            "agree_rows": int((w_al == vec[:-1]).sum()), "watcher_only_rows_(spike_hold)": int(held.sum())}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--out", default=str(C.RESULTS / "device_A.npz"))
    args = ap.parse_args(argv)
    C.RESULTS.mkdir(parents=True, exist_ok=True)
    data = C.load_data(kinds=("dev",))
    conds = [c for c in C.fault_conditions(data) if args.only in c]
    save, info = {}, {}
    for cond in conds:
        learn, hold = C.split_normal(data, cond)
        fids = C.time_order(data, data.idx(cond=cond, label=list(C.FAULTS)))
        d, report = train(data, learn)
        model = RT.Model(d)
        thr = model.threshold
        spike = model.spike_line or np.inf
        th, Xh = rows_of(data, hold)
        sh = model.score(Xh)[0].reshape(len(hold), 5)
        Xf = data.feats["dev"][fids].astype(float)
        sf = model.score(Xf.reshape(-1, F.N_FEATURES))[0].reshape(len(fids), 5)
        nm, fm = C.clip_stats(sh / thr, sf / thr, "med", 5)
        na, fa = C.clip_stats(sh / spike, sf / spike, "any")
        # safety checks
        base = SF.baseline_of(d)
        norm_first = []
        s = SF.Safety(base, gravity_check=True)
        starts = {}
        for i, (ti, x) in enumerate(zip(th, Xh)):
            started, _ = s.update(float(ti), x)
            for p in started:
                starts.setdefault(i // 5, {}).setdefault(p["name"], i % 5)
        norm_first = [starts.get(i, {}) for i in range(len(hold))]
        pre_t, pre_X = th[-PRE_S:], Xh[-PRE_S:]
        fault_first = []
        for j in range(len(fids)):
            tf = pre_t[-1] + 1 + np.arange(5)
            fault_first.append(safety_started(base, np.r_[pre_t, tf], np.vstack([pre_X, Xf[j]]), tf[0]))
        # A threshold-only change to try: the device's own model, but the line from 5 time blocks of the learning
        # clips (each scored by a device model trained on the other four), 99th percentile after a median of 3,
        # alarm on the median of the last 3 seconds (no spike rule). Saved as "cf_*" in units of that line.
        fold = np.arange(len(learn)) * 5 // len(learn)
        oof = []
        for q in range(5):
            dq, _ = train(data, learn[fold != q])
            _, Xq = rows_of(data, learn[fold == q])
            oof.append(C.running_median(RT.Model(dq).score(Xq)[0], 3))
        cf_line = float(np.quantile(np.concatenate(oof), 0.99))
        cn, cfm = C.clip_stats(sh / cf_line, sf / cf_line, "med", 3)
        save[cond] = {"cf_norm": cn, "cf_fault": cfm, "norm_med": nm, "fault_med": fm, "norm_spike": na, "fault_spike": fa,
                      "norm_score": sh / thr, "fault_score": sf / thr, "labels": data.meta["label"][fids],
                      "fault_ids": data.meta["clip_id"][fids], "fault_folder": data.meta["folder"][fids]}
        info[cond] = {"report": {k: report[k] for k in ("period_s", "rows_learned", "rows_calibration", "inputs",
                                                        "patterns_kept", "threshold", "train_seconds_pc")},
                      "spike_line_over_threshold": spike / thr, "n_learn": len(learn), "n_hold": len(hold),
                      "n_fault": len(fids), "safety_norm": norm_first, "safety_fault": fault_first,
                      "baseline_levels": d["baseline"]["levels"], "spikes": d["baseline"].get("spikes")}
        if cond == conds[0]:
            info[cond]["watcher_check"] = check_watcher(model, Xh, th, thr, spike)
        nf = np.mean(np.maximum(nm, na)[:, -1] > 1)
        ff = np.mean(np.maximum(fm, fa)[:, -1] > 1)
        print(f"{cond}: learn {len(learn)} hold {len(hold)} faults {len(fids)}; patterns {report['patterns_kept']}, "
              f"spike/thr {spike / thr:.2f}; model FA {nf:.2f}, caught {ff:.2f}; train {report['train_seconds_pc']} s",
              flush=True)
    flat = {}
    for cond, v in save.items():
        for k, a in v.items():
            flat[f"{cond}|{k}"] = a
    np.savez(args.out, **flat)
    with open(args.out.replace(".npz", ".json"), "w") as f:
        json.dump(info, f, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
    print("saved", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
