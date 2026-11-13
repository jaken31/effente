#!/usr/bin/env python3
"""Which "is this machine still normal?" method is most accurate on real bearing faults (Ottawa)?

The same per-bearing setup as ottawa.py (each bearing's normal learned from 6 s of its healthy file,
as our 1 kHz MPU would see it), for every method in training.anomaly.detectors plus the two normal
models the device can train and run itself (effente.trainer: PCA and the autoencoder, int8 weights). Every method gets the same treatment:
  - prepared inputs (effente.model_input.Prep) fitted on the learning rows
  - learn on the first 85 % of the learning rows; the line from the last 15 % (held out), for about
    one false alarm a week (trainer.gamma_threshold on the median of the last 5 scores), as the device does
  - judged on the held-out healthy rows and the developing and full fault files, the median of the
    last 5 scores against the line
Scores:
  - caught: a fault file with at least half its judged rows over the line
  - flagged: a healthy held-out part with at least half its rows over the line (a false alarm)
  - accuracy: (faults caught + healthy parts not flagged) / all files, and balanced (the mean of the
    catch rate and the healthy rate, so 40 fault files don't outweigh 20 healthy ones)
  - AUC: how well the score separates fault rows from healthy rows with no line at all (1.0 perfect,
    0.5 chance), per bearing, averaged

    python3 -m training.record.datasets.compare_ottawa "data/real/ottawa-uods/unpacked/University of Ottawa ..." \\
        --out data/real/ottawa-uods/compare.json

Limits as in ottawa.py: 10 s a bearing and 2 s of held-out healthy data per bearing; bearing faults only.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import warnings
from pathlib import Path

import numpy as np

from effente import features as F
from effente import model_runtime as RT
from effente.model_input import Prep
from effente.trainer import _causal_median, gamma_threshold, to_current, train_model
from training.anomaly import detectors as D
from training.record.datasets import ottawa as O

SMOOTH = 5
ROWS_PER_WEEK = 7 * 86400.0  # one row a "second", as the device judges a short recording


def methods():
    return {"PCA (on the device)": "pca", "autoencoder (on the device)": "autoencoder", "max |z| (no model)": D.MaxZ, "PCA": D.PCARecon,
            "Gaussian (Mahalanobis)": D.Gaussian, "Gaussian mixture": D.Mixture, "autoencoder (sklearn)": D.Autoencoder,
            "similarity (AAKR/MSET-style)": D.Similarity, "isolation forest": D.Forest, "nearest neighbours": D.KNN}


def auc(neg, pos) -> float:
    neg, pos = np.asarray(neg, float), np.asarray(pos, float)
    if not len(neg) or not len(pos):
        return float("nan")
    allv = np.concatenate([neg, pos])
    ranks = allv.argsort().argsort() + 1.0
    return float((ranks[len(neg):].sum() - len(pos) * (len(pos) + 1) / 2) / (len(neg) * len(pos)))


def over_share(scores, line) -> float:
    s = _causal_median(np.asarray(scores, float), SMOOTH)[SMOOTH - 1:]
    return float((s > line).mean()) if len(s) else 0.0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder")
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)
    folder = Path(args.folder)
    names = list(F.FEATURE_NAMES)
    per = {m: {"caught": 0, "faults": 0, "flagged": 0, "healthy": 0, "auc": [], "fit_s": 0.0} for m in methods()}
    rows = []
    for b in range(1, 21):
        rng = np.random.default_rng(args.seed + b)
        k = O.kind_of(b)
        healthy = O.load(folder, f"H_{b}_0", rng)
        X_learn = to_current(O.windows(healthy, 0.0, O.LEARN_S, 0.02), names)
        tests = {"healthy": to_current(O.windows(healthy, O.HELD_FROM_S, 9.0, 0.1), names)}
        for level, label in ((1, "developing"), (2, "full")):
            tests[label] = to_current(O.windows(O.load(folder, f"{k}_{b}_{level}", rng), 0.0, 9.0, 0.25), names)
        cut = int(0.85 * len(X_learn))
        prep = Prep.fit(X_learn[:cut], names, running_only=False)
        Z = {lab: np.clip(np.nan_to_num(prep.transform(X)), -8, 8) for lab, X in tests.items()}
        Z_fit = np.clip(np.nan_to_num(prep.transform(X_learn[:cut])), -8, 8)
        Z_cal = np.clip(np.nan_to_num(prep.transform(X_learn[cut:])), -8, 8)
        row = {"bearing": b, "fault": O.KINDS[k]}
        for name, cls in methods().items():
            t0 = time.perf_counter()
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                if isinstance(cls, str):  # the device's own trainer and runtime (int8): it sets the line itself
                    d, _ = train_model(np.arange(len(X_learn), dtype=float), X_learn, names, period=1, seed=args.seed,
                                       normal_model=cls)
                    model = RT.Model(d)
                    score = {lab: model.score(X)[0] for lab, X in tests.items()}
                    line = float(d["threshold"])
                else:
                    det = cls().fit(Z_fit)
                    score = {lab: det.score(z)[0] for lab, z in Z.items()}
                    line = gamma_threshold(_causal_median(det.score(Z_cal)[0], SMOOTH), ROWS_PER_WEEK)
            p = per[name]
            p["fit_s"] += time.perf_counter() - t0
            share = {lab: over_share(s, line) for lab, s in score.items()}
            for lab in ("developing", "full"):
                p["faults"] += 1
                p["caught"] += share[lab] >= 0.5
            p["healthy"] += 1
            p["flagged"] += share["healthy"] >= 0.5
            p["auc"].append(auc(score["healthy"], np.concatenate([score["developing"], score["full"]])))
            row[name] = {k2: round(v, 3) for k2, v in share.items()}
        rows.append(row)
        print(f"bearing {b:2d} done", flush=True)
    table = []
    for name, p in per.items():
        catch, ok = p["caught"] / p["faults"], 1 - p["flagged"] / p["healthy"]
        table.append({"method": name, "faults_caught": f"{p['caught']}/{p['faults']}",
                      "healthy_flagged": f"{p['flagged']}/{p['healthy']}",
                      "accuracy": round((p["caught"] + p["healthy"] - p["flagged"]) / (p["faults"] + p["healthy"]), 3),
                      "balanced_accuracy": round((catch + ok) / 2, 3), "auc": round(float(np.nanmean(p["auc"])), 3),
                      "seconds": round(p["fit_s"], 1)})
    table.sort(key=lambda r: (-r["balanced_accuracy"], -r["auc"]))
    Path(args.out).write_text(json.dumps({"table": table, "per_bearing": rows}, indent=1))
    print(f"\n{'method':32s} {'caught':>8s} {'flagged':>8s} {'accuracy':>9s} {'balanced':>9s} {'AUC':>6s} {'time':>6s}")
    for r in table:
        print(f"{r['method']:32s} {r['faults_caught']:>8s} {r['healthy_flagged']:>8s} {r['accuracy']:9.3f} "
              f"{r['balanced_accuracy']:9.3f} {r['auc']:6.3f} {r['seconds']:5.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
