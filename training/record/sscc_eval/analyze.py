"""Extra Protocol A analyses for the report: the device's sensitivity dial, how early faults are flagged, the
per-condition picture, the safety checks, and the "one group changes" (dilution) question.

    python -m training.record.sscc_eval.analyze --set select --out results/analysis_select.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

from effente import safety as SF
from training.record.sscc_eval import common as C
from training.record.sscc_eval import report as R

FINALISTS = ("dev__gauss", "dev__fused_gauss_max", "dev__fused_knn1_max", "dev__knn1", "dev__pca95_t2",
             "g1-ga__gauss", "g1-ga__fused_gauss_max", "ga__gauss", "dev_snd__knn1", "g1c__gauss",
             "g16-ga__fused_gauss_max", "g16-ga__gauss")


def pooled(ns, fs, lab):
    neg = np.concatenate([ns[c] for c in ns])
    pos = np.concatenate([fs[c] for c in fs])
    lb = np.concatenate([lab[c] for c in lab])
    return neg, pos, lb


def margin_sweep(ns, fs, lab, margins=(0.5, 0.7, 0.85, 1.0, 1.25, 1.5, 2.0, 3.0)):
    neg, pos, lb = pooled(ns, fs, lab)
    out = []
    for m in margins:
        r = {"margin": m, "sensitivity": round(float((1 - np.log(m) / np.log(4)) / 2), 3),
             "fa": f"{int((neg[:, -1] > m).sum())}/{len(neg)}", "caught_%": round(100 * float((pos[:, -1] > m).mean()), 1)}
        for f in C.FAULTS:
            r[f] = round(100 * float((pos[lb == f, -1] > m).mean()), 1)
        out.append(r)
    return out


def early(ns, fs, lab, thr=1.0):
    neg, pos, lb = pooled(ns, fs, lab)
    r = {"fa_clips": f"{int((neg[:, -1] > thr).sum())}/{len(neg)}"}
    for f in ("all",) + C.FAULTS:
        p = pos if f == "all" else pos[lb == f]
        r[f] = [round(100 * float((p[:, j] > thr).mean()), 1) for j in range(5)]
    return r


def at_fpr_threshold(ns, fs, lab, fpr=0.01):
    neg, pos, lb = pooled(ns, fs, lab)
    _, thr = C.catch_at_fpr(neg[:, -1], pos[:, -1], fpr)
    return thr


def per_condition(ns, fs, lab):
    out = {}
    for c in ns:
        out[c] = {"fa": f"{int((ns[c][:, -1] > 1).sum())}/{len(ns[c])}",
                  "caught_%": round(100 * float((fs[c][:, -1] > 1).mean()), 1),
                  "auroc": round(C.auroc(ns[c][:, -1], fs[c][:, -1]), 3)}
        for f in C.FAULTS:
            sel = lab[c] == f
            if sel.any():
                out[c][f] = round(100 * float((fs[c][sel, -1] > 1).mean()), 1)
    return out


def safety_summary(which):
    res = R.device_with_safety(C.RESULTS / "device_A.npz", which)
    out = {}
    for c, v in res.items():
        for kind in ("norm", "fault"):
            names = v[f"safety_{kind}_names"]
            for i, s in enumerate(names):
                for k in s:
                    key = (kind, k, "all" if kind == "norm" else str(v["labels"][i]), R.noise_of(c))
                    out[key] = out.get(key, 0) + 1
    model_only = {"n": 0, "model": 0, "model_or_safety": 0}
    per = {}
    for c, v in res.items():
        for f in C.FAULTS:
            sel = v["labels"] == f
            d = per.setdefault(f, [0, 0, 0])
            d[0] += int(sel.sum())
            d[1] += int(v["fault_model"][sel].sum())
            d[2] += int((v["fault_model"] | v["fault_safety"])[sel].sum())
    fa = [0, 0, 0]
    for c, v in res.items():
        fa[0] += len(v["norm_model"])
        fa[1] += int(v["norm_model"].sum())
        fa[2] += int((v["norm_model"] | v["norm_safety"]).sum())
    return {"started_counts": {"|".join(k): n for k, n in sorted(out.items())},
            "per_fault_[n, model, model_or_safety]": per, "normal_[n, model, model_or_safety]": fa}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default="select")
    ap.add_argument("--out", default="")
    ap.add_argument("--finalists", default=",".join(FINALISTS))
    args = ap.parse_args(argv)
    res = {}
    dev = R.device_stats(C.RESULTS / "device_A.npz", args.set)
    res["device_margin_sweep"] = margin_sweep(*dev)
    res["device_early_at_line"] = early(*dev)
    res["device_early_at_1pct"] = early(*dev, thr=at_fpr_threshold(*dev))
    res["device_per_condition"] = per_condition(*dev)
    res["device_safety"] = safety_summary(args.set)
    for name in args.finalists.split(","):
        p = C.A_DIR / f"{name}.npz"
        if not p.exists():
            continue
        st = R.alt_stats(p, args.set, "med3", "q99")
        res[f"{name}|margin_sweep"] = margin_sweep(*st)
        res[f"{name}|early_at_line"] = early(*st)
        res[f"{name}|early_at_1pct"] = early(*st, thr=at_fpr_threshold(*st))
        res[f"{name}|per_condition"] = per_condition(*st)
        st1 = R.alt_stats(p, args.set, "any", "q99")
        res[f"{name}|any_rule_early_at_line"] = early(*st1)
    out = json.dumps(res, indent=1, default=str)
    if args.out:
        Path(args.out).write_text(out)
    print(out[:200])
    return 0


if __name__ == "__main__":
    sys.exit(main())
