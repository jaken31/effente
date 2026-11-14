"""Protocol B (changed conditions) and the paper's own protocol.

B-shift: learn on ONE condition (load, speed, clean; its Protocol A learning clips), then count false alarms at the
         learned line on held-out normal clips of: the same condition, the same load at other speeds, other loads at
         the same speed, and the same load and speed with factory noise. Faults of the same condition are judged too.
B-variety: learn on several conditions at once (one load, speeds 60/80/100; clean only, or clean + noise), then
         judge every included condition's held-out normal and fault clips (Protocol A held-out parts), to see how much
         normal variety the learning needs and what it costs in catches.
Paper:   learn on all normal clips at speeds 20/40/60/80 (every load, clean and noise), judge the normal clips at
         speed 100 and every fault clip; AUROC per clip, kNN (k=1) on clip-level features (the mean of the five
         one-second rows), per sensor and fused by the mean of per-channel ECDF scores, as fd_knn.py does.

Methods: the device as it is (effente.trainer, Watcher rule), and alternatives from eval_methods (line = the highest
out-of-sample learning score, 5 blocks per condition).

    python -m training.record.sscc_eval.eval_shift [--part shift,variety,paper]
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings

import numpy as np

from effente import features as F
from effente import model_runtime as RT
from effente import trainer as TR
from training.record.sscc_eval import common as C
from training.record.sscc_eval import eval_methods as EM

ALT = ("dev|gauss", "dev|fused_gauss_max", "dev|fused_knn1_max", "g1+ga|gauss", "g1+ga|fused_gauss_max",
       "g16+ga|fused_gauss_max")


def device_scores(data, train_ids, test_sets):
    """Train the device's model on the clips (joined end to end), return {name: (clips, 5) Watcher statistic in
    units of the alarm line, accumulated over the clip} for every test set (normal sets as streams, fault sets as
    clips that each follow the first test set's stream)."""
    X = data.feats["dev"][train_ids].reshape(-1, F.N_FEATURES).astype(float)
    t = np.arange(len(X), dtype=float)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        d, _ = TR.train_model(t, X, list(F.FEATURE_NAMES))
    m = RT.Model(d)
    thr, spike = m.threshold, (m.spike_line or np.inf)
    out = {}
    for name, (ids, kind) in test_sets.items():
        s = m.score(data.feats["dev"][ids].reshape(-1, F.N_FEATURES).astype(float))[0].reshape(len(ids), 5)
        out[name] = (s, kind)
    res = {}
    ref = next(v[0] for v in out.values() if v[1] == "normal")
    for name, (s, kind) in out.items():
        if kind == "normal":
            nm, _ = C.clip_stats(s / thr, s[:0] / thr, "med", 5)
            na, _ = C.clip_stats(s / spike, s[:0], "any")
            res[name] = np.maximum(nm, na)
        else:
            _, fm = C.clip_stats(ref / thr, s / thr, "med", 5)
            _, fa = C.clip_stats(ref / spike, s / spike, "any")
            res[name] = np.maximum(fm, fa)
    return res


def alt_scores(data, cfg, train_ids, test_sets, folds):
    """An alternative: line = the 99th percentile of the out-of-sample learning scores after the median-of-3 rule
    (folds: block index per training clip), alarm when the median of the last 3 seconds is over it (chosen on the
    selection set, Protocol A). Normal sets are run as streams; fault clips follow the first normal set's stream."""
    fs, spec = cfg.split("|")
    RL = EM.raw_rows(fs, train_ids)
    flat = lambda a: a.reshape(-1, a.shape[-1])
    cal = np.zeros((len(train_ids), 5))
    for q in np.unique(folds):
        cal[folds == q] = EM.fit_score(fs, spec, flat(RL[folds != q]), [flat(RL[folds == q])])[0].reshape(-1, 5)
    cs = np.concatenate([C.running_median(cal[folds == q].ravel(), 3) for q in np.unique(folds)])
    line = float(np.quantile(cs, 0.99))
    tests = [flat(EM.raw_rows(fs, ids)) for ids, _ in test_sets.values()]
    scores = EM.fit_score(fs, spec, flat(RL), tests)
    ref = None
    out = {}
    for (name, (ids, kind)), s in zip(test_sets.items(), scores):
        s = s.reshape(-1, 5) / line
        if kind == "normal":
            out[name] = C.clip_stats(s, s[:0], "med", 3)[0]
            ref = s if ref is None else ref
        else:
            out[name] = C.clip_stats(ref, s, "med", 3)[1]
    return out


def block_folds(data, ids, k=5):
    """Per clip: its block (0..k-1) within its own condition, by time."""
    f = np.zeros(len(ids), int)
    conds = data.meta["cond"][ids]
    for c in np.unique(conds):
        sel = np.nonzero(conds == c)[0]
        f[sel] = np.arange(len(sel)) * k // len(sel)
    return f


def shift(data, methods, which):
    rows = []
    for load in ("heavy", "light", "med"):
        if not C.__dict__ and False:
            pass
        for speed in (60, 80, 100):
            if (load == C.CONFIRM_LOAD) != (which == "confirm"):
                continue
            cond = C.cond_key(load, speed, "clean")
            learn, hold = C.split_normal(data, cond)
            tests = {"same": (hold, "normal")}
            for s2 in (20, 40, 60, 80, 100):
                if s2 != speed:
                    tests[f"speed{s2}"] = (C.split_normal(data, C.cond_key(load, s2, "clean"))[1], "normal")
            for l2 in ("heavy", "light", "med"):
                if l2 != load:
                    tests[f"load_{l2}"] = (C.split_normal(data, C.cond_key(l2, speed, "clean"))[1], "normal")
            tests["noise"] = (C.split_normal(data, C.cond_key(load, speed, "noise"))[1], "normal")
            fids = C.time_order(data, data.idx(cond=cond, label=list(C.FAULTS)))
            tests["faults"] = (fids, "fault")
            for meth in methods:
                if meth == "device":
                    res = device_scores(data, learn, tests)
                else:
                    res = alt_scores(data, meth, learn, tests, block_folds(data, learn))
                r = {"method": meth, "cond": cond}
                for name, st in res.items():
                    r[name] = [int((st[:, -1] > 1).sum()), int(len(st))]
                rows.append(r)
                print(json.dumps(r), flush=True)
    return rows


def variety(data, methods, which):
    rows = []
    for load in ("heavy", "light", "med"):
        if (load == C.CONFIRM_LOAD) != (which == "confirm"):
            continue
        for mix in ("clean", "clean+noise"):
            noises = ["clean"] if mix == "clean" else ["clean", "noise"]
            conds = [C.cond_key(load, s, n) for s in (60, 80, 100) for n in noises]
            learn = np.concatenate([C.split_normal(data, c)[0] for c in conds])
            tests = {}
            for s in (60, 80, 100):
                for n in ("clean", "noise"):
                    c = C.cond_key(load, s, n)
                    tests[f"{c}|normal"] = (C.split_normal(data, c)[1], "normal")
                    tests[f"{c}|fault"] = (C.time_order(data, data.idx(cond=c, label=list(C.FAULTS))), "fault")
            for meth in methods:
                if meth == "device":
                    # the faults of each condition follow that condition's own normal stream
                    res = {}
                    for s in (60, 80, 100):
                        for n in ("clean", "noise"):
                            c = C.cond_key(load, s, n)
                            sub = {k: v for k, v in tests.items() if k.startswith(c + "|")}
                            res.update(device_scores(data, learn, sub))
                else:
                    res = alt_scores(data, meth, learn, tests, block_folds(data, learn))
                r = {"method": meth, "load": load, "learn": mix}
                for name, st in res.items():
                    lab = data.meta["label"][tests[name][0]]
                    r[name] = [int((st[:, -1] > 1).sum()), int(len(st))]
                    if tests[name][1] == "fault":
                        for f in C.FAULTS:
                            sel = lab == f
                            r[f"{name}|{f}"] = [int((st[sel, -1] > 1).sum()), int(sel.sum())]
                rows.append(r)
                print(json.dumps({k: v for k, v in r.items() if "|" not in k or k.endswith("normal") or k.endswith("fault")}), flush=True)
    return rows


def paper(data):
    """The paper's fault-detection protocol with kNN on clip-level features, per sensor and fused (ECDF mean)."""
    from sklearn.neighbors import NearestNeighbors
    from sklearn.preprocessing import StandardScaler
    tr = data.idx(label="normal", speed=[20, 40, 60, 80])
    te = np.r_[data.idx(label="normal", speed=100), data.idx(label=list(C.FAULTS))]
    y = (data.meta["label"][te] != "normal").astype(int)
    D = data.feats
    channels = {  # one "channel" per sensor signal, as the benchmark embeds each channel on its own
        "audio": [D["gen_a"]],
        "vibration": [D["gen_v16"][:, :, c] for c in range(4)],
    }
    dev = D["dev"]
    vib_cols = [i for i, n in enumerate(F.FEATURE_NAMES) if n.startswith("vib") and not np.isnan(dev[tr, :, i]).all()]
    snd_cols = [i for i, n in enumerate(F.FEATURE_NAMES) if n.startswith("snd") and not np.isnan(dev[tr, :, i]).all()]
    out = {}
    for k in (1, 11):
        for metric in ("euclidean", "cosine"):
            per = {}
            for name, arrs in channels.items():
                qs = []
                for a in arrs:
                    A = a.reshape(len(a), 5, -1).mean(1)
                    sc = StandardScaler().fit(A[tr])
                    Xtr, Xte = sc.transform(A[tr]), sc.transform(A[te])
                    nn = NearestNeighbors(n_neighbors=k + 1, metric=metric).fit(Xtr)
                    dtr = nn.kneighbors(Xtr)[0][:, 1:].mean(1)  # learning scores without the clip itself
                    dte = nn.kneighbors(Xte, n_neighbors=k)[0].mean(1)
                    ref = np.sort(dtr)
                    qs.append(np.clip((np.searchsorted(ref, dte, side="right") + 0.5) / (len(ref) + 1), 1e-6, 1 - 1e-6))
                    per[f"{name}_ch{len(qs) - 1}_raw"] = C.auroc(dte[y == 0], dte[y == 1])
                per[name] = np.mean(qs, 0)
            fused = np.mean([per["audio"]] + [per["vibration"]] * 1, 0)
            allch = (per["audio"] + 4 * per["vibration"]) / 5  # mean over the 5 channels, as fd_knn fuses all
            r = {"audio": C.auroc(per["audio"][y == 0], per["audio"][y == 1]),
                 "vibration": C.auroc(per["vibration"][y == 0], per["vibration"][y == 1]),
                 "fused_mean_over_channels": C.auroc(allch[y == 0], allch[y == 1]),
                 "fused_audio_vib_equal": C.auroc(fused[y == 0], fused[y == 1])}
            r.update({kk: vv for kk, vv in per.items() if kk.endswith("_raw")})
            # the device's own features, clip means
            for gname, cols in (("dev_vib", vib_cols), ("dev_snd", snd_cols), ("dev_all", vib_cols + snd_cols)):
                A = np.nan_to_num(dev[:, :, cols].astype(float).mean(1))
                sc = StandardScaler().fit(A[tr])
                nn = NearestNeighbors(n_neighbors=k, metric=metric).fit(sc.transform(A[tr]))
                dte = nn.kneighbors(sc.transform(A[te]))[0].mean(1)
                r[gname] = C.auroc(dte[y == 0], dte[y == 1])
            for f in C.FAULTS:
                sel = (data.meta["label"][te] == f) | (y == 0)
                r[f"fused_{f}"] = C.auroc(allch[sel][y[sel] == 0], allch[sel][y[sel] == 1])
            out[f"k{k}_{metric}"] = r
            print(f"paper protocol k={k} {metric}: " + json.dumps({kk: round(v, 3) for kk, v in r.items()}), flush=True)
    out["n_train"], out["n_test_normal"], out["n_test_fault"] = int(len(tr)), int((y == 0).sum()), int(y.sum())
    return out


def paper_ced(data):
    """The paper's protocol with CED-tiny embeddings (embed_ced.py) in place of our band features: clip mean of the
    per-second embeddings, per channel (Zoom sound; 3 vibration axes), kNN, ECDF fusion; as paper()."""
    from sklearn.neighbors import NearestNeighbors
    from sklearn.preprocessing import StandardScaler
    tr = data.idx(label="normal", speed=[20, 40, 60, 80])
    te = np.r_[data.idx(label="normal", speed=100), data.idx(label=list(C.FAULTS))]
    y = (data.meta["label"][te] != "normal").astype(int)
    D = data.feats
    channels = {"audio": [D["ced_a"]], "vibration": [D["ced_v"][:, :, c] for c in range(3)]}
    out = {}
    for k in (1, 11):
        for metric in ("euclidean", "cosine"):
            per = {}
            for name, arrs in channels.items():
                qs = []
                for a in arrs:
                    A = a.reshape(len(a), 5, -1).mean(1)
                    sc = StandardScaler().fit(A[tr])
                    Xtr, Xte = sc.transform(A[tr]), sc.transform(A[te])
                    nn = NearestNeighbors(n_neighbors=k + 1, metric=metric).fit(Xtr)
                    ref = np.sort(nn.kneighbors(Xtr)[0][:, 1:].mean(1))
                    dte = nn.kneighbors(Xte, n_neighbors=k)[0].mean(1)
                    qs.append(np.clip((np.searchsorted(ref, dte, side="right") + 0.5) / (len(ref) + 1), 1e-6, 1 - 1e-6))
                per[name] = np.mean(qs, 0)
            allch = (per["audio"] + 3 * per["vibration"]) / 4
            r = {"audio": C.auroc(per["audio"][y == 0], per["audio"][y == 1]),
                 "vibration": C.auroc(per["vibration"][y == 0], per["vibration"][y == 1]),
                 "fused_mean_over_channels": C.auroc(allch[y == 0], allch[y == 1])}
            out[f"k{k}_{metric}"] = r
            print(f"paper protocol, CED-tiny, k={k} {metric}: " + json.dumps({kk: round(v, 3) for kk, v in r.items()}),
                  flush=True)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--part", default="paper,shift,variety")
    ap.add_argument("--set", default="select")
    ap.add_argument("--methods", default="device," + ",".join(ALT))
    ap.add_argument("--tag", default="", help="added to the output file names")
    args = ap.parse_args(argv)
    res = {}
    data = EM.data()
    methods = args.methods.split(",")
    if "ced" in args.part:
        res["paper_ced"] = paper_ced(data)
        (C.RESULTS / "paper_protocol_ced.json").write_text(json.dumps(res["paper_ced"], indent=1))
    if "paper" in args.part.replace("ced", ""):
        res["paper"] = paper(data)
        (C.RESULTS / "paper_protocol.json").write_text(json.dumps(res["paper"], indent=1))
    if "shift" in args.part:
        res["shift"] = shift(data, methods, args.set)
        (C.RESULTS / f"shift_{args.set}{args.tag}.json").write_text(json.dumps(res["shift"], indent=1))
    if "variety" in args.part:
        res["variety"] = variety(data, methods, args.set)
        (C.RESULTS / f"variety_{args.set}{args.tag}.json").write_text(json.dumps(res["variety"], indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
