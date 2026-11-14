"""What does a screwdrop recording look like over time? For each fault recording (selection set only), the share
of seconds over the recommended model's line, by fifth of the recording, and how bursty the over-line seconds are.

A one-off event (a screw falling in and being carried out) would show a short run of high seconds; a screw that
stays in the chain path would show high seconds spread over the whole recording.

    python -m training.record.sscc_eval.screw_nature
"""

from __future__ import annotations

import json

import numpy as np

from training.record.sscc_eval import common as C
from training.record.sscc_eval import report as R


def main():
    d = np.load(C.A_DIR / "g1-ga__fused_pca95_t2_max.npz")
    out = {}
    for c in sorted({k.split("|")[0] for k in d.files if "|" in k}):
        if not R.in_set(c, "select"):
            continue
        cal = d[f"{c}|cal"].ravel()
        line = float(np.quantile(C.running_median(cal, 3), 0.99))
        f, lab = d[f"{c}|fault"], d[f"{c}|labels"]
        for fl in C.FAULTS:
            s = f[lab == fl].ravel() / line
            if not len(s):
                continue
            over = s > 1
            fifth = np.array_split(over, 5)
            runs = np.diff(np.r_[0, over.astype(int), 0])
            starts, ends = np.nonzero(runs == 1)[0], np.nonzero(runs == -1)[0]
            lens = ends - starts
            out.setdefault(fl, []).append({
                "cond": c, "seconds": int(len(s)), "share_over": round(float(over.mean()), 3),
                "share_over_by_fifth": [round(float(x.mean()), 2) for x in fifth],
                "median_level": round(float(np.median(s)), 2), "p10_level": round(float(np.quantile(s, 0.1)), 2),
                "n_runs": int(len(lens)), "median_run_s": float(np.median(lens)) if len(lens) else 0.0})
    for fl, rows in out.items():
        print(fl)
        for r in rows:
            print("  ", r)
    (C.RESULTS / "screw_nature.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
