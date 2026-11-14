"""Shared pieces of the SSCC benchmark: data, splits, alarm rules and metrics.

Data: C:/Users/finnf/effente-sscc/features/*.npz (make_features.py), one per condition folder.
A "condition" is (load, speed, clean|noise); the two screwdrop folders of light_vel100_clean are one condition.

Protocol A split (fixed before any result was seen): per condition, the normal clips in time order (clip NNNN
starts at 5*NNNN s: one folder is one contiguous recording cut into 5 s clips): the first 60 % learn, the next 5 %
are dropped (a gap), the last 35 % are held out. Fault clips are all judged.
CONFIRMATION set (fixed before any result was seen): every condition with load "med". No method selection uses it.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

BASE = Path(r"C:\Users\finnf\effente-sscc")
FEAT = Path(os.environ.get("SSCC_FEATURES", str(BASE / "features")))  # what-if feature sets live elsewhere
RESULTS = BASE / "results"
A_DIR = RESULTS / os.environ.get("SSCC_A_SUBDIR", "A")  # where eval_methods writes and report reads
FAULTS = ("dry", "lean", "loose", "screwdrop")
LEARN_SHARE, GAP_SHARE = 0.60, 0.05
CONFIRM_LOAD = "med"


@dataclass
class Data:
    meta: dict  # per clip arrays: label, load, speed, noise, noise_kind, folder, clip_id, t_start, cond
    feats: dict  # name -> (N, 5, ...) arrays
    names: dict = field(default_factory=dict)

    def idx(self, **kw) -> np.ndarray:
        m = np.ones(len(self.meta["label"]), bool)
        for k, v in kw.items():
            vals = v if isinstance(v, (list, tuple, set)) else [v]
            m &= np.isin(self.meta[k], list(vals))
        return np.nonzero(m)[0]


def cond_key(load, speed, noise) -> str:
    return f"{load}_vel{int(speed)}_{noise}"


def load_data(kinds=("dev", "gen_v16", "gen_v1", "gen_v1c", "gen_a")) -> Data:
    metas, feats = {}, {k: [] for k in kinds}
    keys = ("label", "load", "speed", "noise", "noise_kind", "folder")
    for p in sorted(FEAT.glob("*.npz")):
        if p.name.endswith(".part.npz"):
            continue
        d = np.load(p)
        n = len(d["clip_id"])
        for k in keys:
            metas.setdefault(k, []).extend([d[k].item()] * n)
        for k in ("clip_id", "t_start"):
            metas.setdefault(k, []).extend(list(d[k]))
        metas.setdefault("raw_std", []).extend(list(d["raw_std"]))
        for k in kinds:
            if k.startswith("ced"):  # optional pretrained embeddings, made by embed_ced.py
                feats[k].append(np.load(BASE / "features_ced" / p.name)[k])
            else:
                feats[k].append(d[k])
    meta = {k: np.array(v) for k, v in metas.items()}
    meta["cond"] = np.array([cond_key(a, b, c) for a, b, c in zip(meta["load"], meta["speed"], meta["noise"])])
    feats = {k: np.concatenate(v) for k, v in feats.items()}
    return Data(meta, feats)


def time_order(data: Data, ids: np.ndarray) -> np.ndarray:
    """ids sorted by folder then start time (screwdrop _1 then _2)."""
    o = np.lexsort((data.meta["t_start"][ids].astype(float), data.meta["folder"][ids]))
    return ids[o]


def split_normal(data: Data, cond: str):
    """(learn ids, held-out ids) of a condition's normal clips, in time order (Protocol A)."""
    ids = time_order(data, data.idx(cond=cond, label="normal"))
    if os.environ.get("SSCC_SPLIT") == "reverse":  # robustness check: learn on the LAST 60 %, hold out the first 35 %
        ids = ids[::-1]
    n = len(ids)
    a = int(round(n * LEARN_SHARE))
    b = a + int(round(n * GAP_SHARE))
    if os.environ.get("SSCC_SPLIT") == "reverse":  # each part back in time order
        return time_order(data, ids[:a]), time_order(data, ids[b:])
    return ids[:a], ids[b:]


def fault_conditions(data: Data) -> list[str]:
    c = sorted(set(data.meta["cond"][data.meta["label"] != "normal"]))
    return [k for k in c if len(data.idx(cond=k, label="normal"))]


# ----------------------------------------------------------------- alarm rules on per-second scores

def running_median(s: np.ndarray, k: int) -> np.ndarray:
    """Causal median of the last k values (fewer at the start), per position of a 1-D array."""
    return np.array([np.median(s[max(0, i - k + 1):i + 1]) for i in range(len(s))])


def clip_stats(norm_stream: np.ndarray, fault: np.ndarray, rule: str = "any", k: int = 5):
    """Clip statistics for an alarm rule on per-second scores already in units of the line (alarm when > 1).

    norm_stream: (n_norm, 5) held-out normal clips, consecutive in time (the stream is run through as one).
    fault: (n_fault, 5) fault clips, each judged as if the fault started at its first second, preceded by
           k-1 seconds at the normal stream's median score (typical normal history for a median rule; taking the
           stream's actual last seconds made one condition's pre-roll, itself over the line, count as catches).
    Returns (normal stats (n_norm, 5), fault stats (n_fault, 5)): stat[:, j] = the highest value of the rule's
    statistic over the clip's first j+1 seconds (so the clip alarms by second j+1 iff stat[:, j] > 1)."""
    if rule == "any":
        ns, fs = norm_stream, fault
    elif rule.startswith("med"):
        flat = running_median(norm_stream.ravel(), k)
        ns = flat.reshape(norm_stream.shape)
        pre = np.full(k - 1, float(np.median(norm_stream))) if k > 1 else np.zeros(0)  # typical normal history
        fs = np.array([running_median(np.r_[pre, f], k)[len(pre):] for f in fault]) if len(fault) else fault
    else:
        raise ValueError(rule)
    return np.maximum.accumulate(ns, 1), np.maximum.accumulate(fs, 1)


# ----------------------------------------------------------------- metrics

def wilson(k: int, n: int, z: float = 1.96):
    if n == 0:
        return (math.nan, math.nan)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def auroc(neg: np.ndarray, pos: np.ndarray) -> float:
    neg, pos = np.asarray(neg, float), np.asarray(pos, float)
    if not len(neg) or not len(pos):
        return math.nan
    allv = np.r_[neg, pos]
    from scipy.stats import rankdata
    r = rankdata(allv)
    rp = r[len(neg):].sum()
    return float((rp - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def pauc(neg, pos, max_fpr: float = 0.1) -> float:
    """Standardised partial AUROC up to max_fpr (McClish, as sklearn's roc_auc_score(max_fpr=))."""
    from sklearn.metrics import roc_auc_score
    y = np.r_[np.zeros(len(neg)), np.ones(len(pos))]
    s = np.r_[neg, pos]
    if len(neg) == 0 or len(pos) == 0:
        return math.nan
    return float(roc_auc_score(y, s, max_fpr=max_fpr))


def catch_at_fpr(neg, pos, fpr: float) -> tuple[float, float]:
    """Best possible threshold: the share of pos over the threshold that lets at most fpr of neg over.
    Returns (catch share, threshold)."""
    neg = np.sort(np.asarray(neg, float))
    n = len(neg)
    allowed = int(math.floor(fpr * n + 1e-9))
    thr = neg[n - 1 - allowed] if allowed < n else -np.inf
    return float(np.mean(np.asarray(pos) > thr)), float(thr)


def summarise(norm_stats: dict, fault_stats: dict, fault_labels: dict, cond_noise: dict, line: float = 1.0) -> dict:
    """Pool per-condition clip statistics (already in units of each condition's learned line) and compute the
    report's numbers. norm_stats[cond]: (n, 5); fault_stats[cond]: (m, 5); fault_labels[cond]: (m,)."""
    out = {}
    groups = {"all": list(norm_stats), "clean": [c for c in norm_stats if cond_noise[c] == "clean"],
              "noise": [c for c in norm_stats if cond_noise[c] != "clean"]}
    for gname, conds in groups.items():
        if not conds:
            continue
        neg = np.concatenate([norm_stats[c] for c in conds])
        pos = np.concatenate([fault_stats[c] for c in conds])
        lab = np.concatenate([fault_labels[c] for c in conds])
        g = {"n_normal": int(len(neg)), "n_fault": int(len(pos)),
             "fa_at_line": float(np.mean(neg[:, -1] > line)), "fa_at_line_k": int(np.sum(neg[:, -1] > line)),
             "fa_at_line_ci": wilson(int(np.sum(neg[:, -1] > line)), len(neg)),
             "auroc_pooled": auroc(neg[:, -1], pos[:, -1]), "pauc10_pooled": pauc(neg[:, -1], pos[:, -1], 0.1)}
        aucs = [auroc(norm_stats[c][:, -1], fault_stats[c][:, -1]) for c in conds]
        g["auroc_mean_per_cond"] = float(np.nanmean(aucs))
        per = {}
        for f in ("all",) + FAULTS:
            sel = np.ones(len(lab), bool) if f == "all" else lab == f
            if not sel.any():
                continue
            p = pos[sel]
            r = {"n": int(sel.sum()), "auroc": auroc(neg[:, -1], p[:, -1]), "pauc10": pauc(neg[:, -1], p[:, -1], 0.1),
                 "caught_at_line": float(np.mean(p[:, -1] > line)),
                 "caught_at_line_k": int(np.sum(p[:, -1] > line)),
                 "caught_at_line_ci": wilson(int(np.sum(p[:, -1] > line)), int(sel.sum())),
                 "caught_at_line_by_s": [float(np.mean(p[:, j] > line)) for j in range(p.shape[1])]}
            for fpr in (0.0, 0.01, 0.05):
                share, thr = catch_at_fpr(neg[:, -1], p[:, -1], fpr)
                r[f"best_at_fpr{fpr:g}"] = share
                r[f"best_at_fpr{fpr:g}_by_s"] = [float(np.mean(p[:, j] > thr)) for j in range(p.shape[1])]
            per[f] = r
        g["faults"] = per
        out[gname] = g
    return out
