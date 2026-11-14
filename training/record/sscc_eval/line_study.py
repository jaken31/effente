"""How should the line be set from learning data? For saved explore.py runs (spike rule off), re-express each
condition's clip statistics against other references taken from the learning out-of-sample statistic
(median, q90, q95, q99, max) and report:
  - best@1 %: catches with the best single dial on that reference (which reference transfers best between
    conditions: the higher, the less the dial depends on the machine);
  - with the dial chosen on heavy (largest catches with FA <= 1 % there) and judged on light, and the reverse:
    cross-validated FA and catches of a fixed dial.

    python -m training.record.sscc_eval.line_study NAME [NAME...]   (stems of results/A2/*.select.npz)
"""

from __future__ import annotations

import sys

import numpy as np

from effente import band_model as BM
from training.record.sscc_eval import common as C

A2 = C.RESULTS / "A2"
REFS = ("q50", "q90", "q95", "q99", "max")
NOFS = ("dry", "lean", "loose")


def load(stem: str, which: str = "select"):
    d = np.load(A2 / f"{stem}.{which}.npz")
    conds = sorted({k.split("|")[0] for k in d.files})
    out = {}
    for c in conds:
        oof = d[f"{c}|oof"]
        line = float(d[f"{c}|line"])
        # learning statistic: the smoothed out-of-sample score (smooth = 3 unless the stem says otherwise)
        k = 3
        for tok in stem.split("_"):
            if tok.startswith("smooth"):
                k = int(tok[6:])
        st = BM.running_median(oof, k)
        refs = {"q50": np.quantile(st, .5), "q90": np.quantile(st, .9), "q95": np.quantile(st, .95),
                "q99": np.quantile(st, .99), "max": st.max()}
        out[c] = {"h": d[f"{c}|hold_stat"].max(1) * line, "f": d[f"{c}|fault_stat"].max(1) * line,
                  "lab": d[f"{c}|labels"], "refs": refs}
    return out


def pooled(res, conds, ref, dial):
    h = np.concatenate([res[c]["h"] / (res[c]["refs"][ref] * dial) for c in conds])
    f = np.concatenate([res[c]["f"] / (res[c]["refs"][ref] * dial) for c in conds])
    lab = np.concatenate([res[c]["lab"] for c in conds])
    return h, f, lab


def main(stems):
    for stem in stems:
        res = load(stem)
        conds = list(res)
        heavy = [c for c in conds if c.startswith("heavy")]
        light = [c for c in conds if c.startswith("light")]
        print(stem)
        for ref in REFS:
            h, f, lab = pooled(res, conds, ref, 1.0)
            ns = np.isin(lab, NOFS)
            b1 = C.catch_at_fpr(h, f[ns], 0.01)[0]
            b0 = C.catch_at_fpr(h, f[ns], 0.0)[0]
            cv = []
            for tr, te in ((heavy, light), (light, heavy)):
                ht, ft, lt = pooled(res, tr, ref, 1.0)
                thr = C.catch_at_fpr(ht, ft, 0.01)[1]  # the dial that lets <= 1 % of the training normals alarm
                he, fe, le = pooled(res, te, ref, 1.0)
                cv.append((int((he > thr).sum()), len(he), float(np.mean(fe[np.isin(le, NOFS)] > thr)),
                           float(np.mean(fe[le == "screwdrop"] > thr)), thr))
            print(f"  {ref:4s} best@0 {100 * b0:5.1f} best@1% {100 * b1:5.1f} (no screwdrop) | CV heavy->light: FA "
                  f"{cv[0][0]}/{cv[0][1]} caught {100 * cv[0][2]:.0f} screw {100 * cv[0][3]:.0f} dial {cv[0][4]:.2f} | "
                  f"light->heavy: FA {cv[1][0]}/{cv[1][1]} caught {100 * cv[1][2]:.0f} screw {100 * cv[1][3]:.0f} "
                  f"dial {cv[1][4]:.2f}")


if __name__ == "__main__":
    main(sys.argv[1:])
