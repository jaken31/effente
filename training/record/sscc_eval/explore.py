"""Round 2 of the SSCC benchmark (14 Nov, morning): variants of the band model (effente/band_model.py), scored
with the SAME code that would ship, on Protocol A (common.split_normal).

Per condition: learn on the first 60 % of the normal recording (BandModel.learn sets its own line from learning
data only), then judge
  - the held-out normal clips as one continuous stream (false alarms: clips with an alarm; episodes per hour),
  - every fault clip as if the fault started at its first second, after typical normal history (pooling history
    = the learning median row; rule history = the median held-out normal score, as common.clip_stats),
  - every fault recording as one continuous stream (all its clips in time order).
Results are pooled over conditions and reported three ways: all faults, faults without screwdrop, screwdrop alone.

    python -m training.record.sscc_eval.explore --set select --configs NAME[,NAME...] [--workers 12]
Raw per-condition scores: results/A2/<config>.npz ; summary: results/A2/<config>.<set>.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np

from effente import band_model as BM
from training.record.sscc_eval import common as C

OUT = C.RESULTS / "A2"
FEAT2 = C.BASE / "features2"
_D = None
NOFS = ("dry", "lean", "loose")


def data():
    global _D
    if _D is None:
        d = C.load_data(("gen_v1", "gen_a"))
        extra = {}
        cids = []
        for p in sorted(C.FEAT.glob("*.npz")):
            if p.name.endswith(".part.npz"):
                continue
            e = np.load(FEAT2 / p.name)
            for k in ("a_fr", "a128", "v_env", "v_fr", "v_coh", "v40"):
                extra.setdefault(k, []).append(e[k])
            e3 = np.load(C.BASE / "features3" / p.name)
            for k in ("a256", "alin", "a128l"):
                extra.setdefault(k, []).append(e3[k])
            ex = np.load(C.BASE / "features_x10" / p.name)
            extra.setdefault("v1x10", []).append(ex["gen_v1"])
            extra.setdefault("v1c", []).append(np.load(p)["gen_v1c"])
            e4 = np.load(C.BASE / "features4" / p.name)
            for k in ("alin25", "alin100", "alin200"):
                extra.setdefault(k, []).append(e4[k])
            cids.extend(list(e["clip_id"]))
        assert np.array_equal(np.array(cids), d.meta["clip_id"]), "features2 out of order"
        for k, v in extra.items():
            d.feats[k] = np.concatenate(v)
        _D = d
    return _D


# ----------------------------------------------------------------- feature sets

def part_rows(p: str, ids: np.ndarray):
    F = data().feats
    n = len(ids)
    if p == "v1":
        return F["gen_v1"][ids].reshape(n, 5, -1), "vib"
    if p == "v1x10":  # what-if: the vibration 10x stronger (as if the CSV were volts of a 100 mV/g sensor)
        return F["v1x10"][ids].reshape(n, 5, -1), "vib"
    if p == "v1c":  # what-if: the same 1 kHz vibration without the MPU-6500's noise
        return F["v1c"][ids].reshape(n, 5, -1), "vib"
    if p == "a64":
        return F["gen_a"][ids], "snd"
    if p == "a32":
        a = F["gen_a"][ids].astype(float)
        return np.concatenate([np.log10(10 ** a[..., 0:64:2] + 10 ** a[..., 1:64:2]), a[..., 64:]], -1), "snd"
    if p == "a128":
        return np.concatenate([F["a128"][ids], F["gen_a"][ids][..., 64:]], -1), "snd"
    if p in ("a256", "alin", "a128l", "alin25", "alin100", "alin200"):
        return np.concatenate([F[p][ids], F["gen_a"][ids][..., 64:]], -1), "snd"
    if p == "afr":
        return F["a_fr"][ids], "sfr"
    if p == "afrs":  # frame std / peak / flux only (no modulation)
        return F["a_fr"][ids][..., :24], "sfr"
    if p == "amod":
        return F["a_fr"][ids][..., 24:], "sfr"
    if p == "venv":
        return F["v_env"][ids].reshape(n, 5, -1), "venv"
    if p == "vfr":
        return F["v_fr"][ids], "vib"
    if p == "vcoh":
        return F["v_coh"][ids], "vcoh"
    if p == "v40":
        v1 = F["gen_v1"][ids]
        return np.concatenate([F["v40"][ids], v1[..., 20:]], -1).reshape(n, 5, -1), "vib"
    raise ValueError(p)


def rows(fs: str, ids: np.ndarray):
    """(clips, 5, d) and the group name of every column. A part may be put in another group with @group."""
    arrs, grp = [], []
    for p in fs.split("+"):
        name, _, g2 = p.partition("@")
        a, g = part_rows(name, ids)
        arrs.append(a.astype(float))
        grp += [g2 or g] * a.shape[-1]
    return np.concatenate(arrs, -1), np.array(grp)


# ----------------------------------------------------------------- model variants (research only)

def ledoit_wolf(X):
    mu = X.mean(0)
    Xc = X - mu
    n, p = Xc.shape
    S = Xc.T @ Xc / n
    m = np.trace(S) / p
    X2 = Xc ** 2
    beta_ = float((X2.T @ X2).sum()) / n - float((S ** 2).sum())
    d2 = float(((S - m * np.eye(p)) ** 2).sum())
    beta = min(beta_ / n, d2) if d2 > 0 else 0.0
    sh = beta / d2 if d2 > 0 else 0.0
    return mu, np.linalg.inv((1 - sh) * S + sh * m * np.eye(p))


class Variant(BM.BandModel):
    """BandModel with another per-group model or fusion (exploration only)."""

    def __init__(self, gmodel="pca", fuse="max", **kw):
        super().__init__(**kw)
        self.gmodel, self.fuse = gmodel, fuse

    def _gfit(self, Z):
        if self.gmodel == "pca":
            return BM.pca_fit(Z, self.var, self.max_k)
        if self.gmodel == "gauss":
            return ledoit_wolf(Z)
        if self.gmodel.startswith("knn"):
            return Z.copy()
        raise ValueError(self.gmodel)

    def _gscore(self, m, Z):
        if self.gmodel == "pca":
            return BM.pca_score(m, Z)
        if self.gmodel == "gauss":
            mu, P = m
            D = Z - mu
            return np.einsum("ij,jk,ik->i", D, P, D) / Z.shape[1]
        if self.gmodel.startswith("knn"):
            k = int(self.gmodel[3:])
            d2 = (Z ** 2).sum(1)[:, None] + (m ** 2).sum(1)[None, :] - 2 * Z @ m.T
            d = np.sqrt(np.maximum(np.partition(d2, k - 1, axis=1)[:, :k], 0))
            return d.mean(1) ** 2
        raise ValueError(self.gmodel)

    def _fuse(self, G):
        if self.fuse == "max":
            return G.max(1)
        if self.fuse == "mean":
            return G.mean(1)
        if self.fuse == "rms":
            return np.sqrt((G ** 2).mean(1))
        if self.fuse == "top2":  # mean of the two largest
            return np.sort(G, 1)[:, -2:].mean(1) if G.shape[1] > 1 else G[:, 0]
        raise ValueError(self.fuse)


# ----------------------------------------------------------------- configurations

def cfg_parse(name: str) -> dict:
    """name = '<featureset>|key=value,...' e.g. 'v1+a64|pool=1,spike=1'."""
    fs, _, rest = name.partition("|")
    c = {"fs": fs, "gmodel": "pca", "fuse": "max", "var": 0.95, "max_k": 40, "pool": 1, "spike": 0.0, "q": 0.99,
         "smooth": 3, "blocks": 5, "norm": "blocks", "norm_q": 0.5, "judge": "ensemble", "snr": -1.0, "modes": 0}
    for kv in filter(None, rest.split(",")):
        k, v = kv.split("=")
        c[k] = v if k in ("gmodel", "fuse", "norm", "judge") else (int(v) if k in ("max_k", "pool", "smooth", "blocks", "modes") else float(v))
    return c


def make(c: dict, grp: np.ndarray, fs: str = ""):
    names = list(dict.fromkeys(grp))
    groups = [np.nonzero(grp == g)[0] for g in names]
    # snr >= 0: drop vibration inputs within snr dB of the MPU's own noise floor (only for the v1/v1x10/v1c layout)
    vib_cols = None
    if c["snr"] >= 0 and fs.split("+")[0].split("@")[0] in ("v1", "v1x10", "v1c"):
        vib_cols = np.arange(BM.N_VIB)
    if c["modes"]:  # one BandModel per operating point (rec), score = min over them (plain BandModel settings)
        return BM.BandModes(var=c["var"], max_k=c["max_k"], blocks=c["blocks"], q=c["q"], smooth=c["smooth"],
                            spike=c["spike"], pool=c["pool"], groups=groups, fuse=c["fuse"],
                            vib_floor=BM.MPU_FLOOR if vib_cols is not None else None, vib_cols=vib_cols,
                            min_snr_db=max(c["snr"], 0.0))
    return Variant(gmodel=c["gmodel"], fuse=c["fuse"], var=c["var"], max_k=c["max_k"], blocks=c["blocks"], q=c["q"],
                   smooth=c["smooth"], spike=c["spike"], pool=c["pool"], groups=groups, norm=c["norm"],
                   norm_q=c["norm_q"], judge_with=c["judge"], vib_floor=BM.MPU_FLOOR if vib_cols is not None else None,
                   vib_cols=vib_cols, min_snr_db=max(c["snr"], 0.0))


# ----------------------------------------------------------------- per condition

def fault_clip_scores(m: BM.BandModel, RF: np.ndarray) -> np.ndarray:
    """(n, 5, d) fault clips -> (n, 5) scores, each clip after typical history (pooling history = learning median)."""
    n, w, d = RF.shape
    if m.pool <= 1:
        return m.scores(RF.reshape(-1, d)).reshape(n, w)
    hist = np.tile(m.typical_row(), (m.pool - 1, 1))
    return np.array([m.scores(x, history=hist) for x in RF])


def rule_stat(m: BM.BandModel, s: np.ndarray, pre: float) -> np.ndarray:
    """Per-second alarm statistic of a score run (alarm when > 1): max(median of last `smooth` / line,
    score / spike line); `pre` = rule history value before the run."""
    k = m.smooth
    full = np.r_[np.full(k - 1, pre), s]
    med = BM.running_median(full, k)[k - 1:]
    return np.maximum(med / m.line, s / m.spike)


def run_condition(cfg: dict, cond: str) -> dict:
    d = data()
    learn, hold = C.split_normal(d, cond)
    fids = C.time_order(d, d.idx(cond=cond, label=list(C.FAULTS)))
    RL, grp = rows(cfg["fs"], learn)
    RH, _ = rows(cfg["fs"], hold)
    RF, _ = rows(cfg["fs"], fids)
    flat = lambda a: a.reshape(-1, a.shape[-1])
    m = make(cfg, grp, cfg["fs"]).learn(flat(RL))
    sh = m.scores(flat(RH))
    pre = float(np.median(sh))
    hstat = rule_stat(m, sh, pre).reshape(len(hold), 5)
    sf = fault_clip_scores(m, RF)
    fstat = np.array([rule_stat(m, x, pre) for x in sf])
    # each fault recording as one stream
    folders = d.meta["folder"][fids]
    sstat = np.zeros_like(fstat)
    for f in np.unique(folders):
        idx = np.nonzero(folders == f)[0]
        s = m.scores(flat(RF[idx]), history=np.tile(m.typical_row(), (max(m.pool - 1, 1), 1)))
        sstat[idx] = rule_stat(m, s, pre).reshape(len(idx), 5)
    return {"hold_score": sh.reshape(len(hold), 5), "fault_score": sf, "hold_stat": hstat, "fault_stat": fstat,
            "stream_stat": sstat, "labels": d.meta["label"][fids], "line": m.line, "spike": m.spike,
            "oof": m.oof, "n_learn": len(RL) * 5}


# ----------------------------------------------------------------- summaries

def conds_of(which: str) -> list[str]:
    out = []
    for c in C.fault_conditions(data()):
        load = c.split("_")[0]
        if which == "all" or (which == "confirm") == (load == C.CONFIRM_LOAD):
            out.append(c)
    return out


def summarise(res: dict, conds: list[str]) -> dict:
    """res[cond] = run_condition output (or anything with hold_stat (n,5), fault_stat (m,5), labels, optional
    stream_stat). Clip alarmed = its statistic over 1 at any second."""
    out = {}
    subsets = {"all": conds, "clean": [c for c in conds if c.endswith("clean")],
               "noise": [c for c in conds if not c.endswith("clean")]}
    for load in sorted({c.split("_")[0] for c in conds}):
        subsets[load] = [c for c in conds if c.startswith(load + "_")]
    for name, cs in subsets.items():
        if not cs:
            continue
        H = np.concatenate([res[c]["hold_stat"] for c in cs])
        Fs = np.concatenate([res[c]["fault_stat"] for c in cs])
        L = np.concatenate([res[c]["labels"] for c in cs])
        hmax = np.maximum.accumulate(H, 1)
        fmax = np.maximum.accumulate(Fs, 1)
        k = int((hmax[:, -1] > 1).sum())
        # false alarm episodes: rising edges of the per-second alarm in each held-out stream
        eps = sum(int(np.sum(np.diff(np.r_[0, (res[c]["hold_stat"].ravel() > 1).astype(int)]) == 1)) for c in cs)
        hours = H.size / 3600
        g = {"n_normal": int(len(H)), "fa_clips": k, "fa_ci": C.wilson(k, len(H)), "fa_episodes": eps,
             "fa_episodes_per_hour": eps / hours, "normal_hours": hours}
        for fname, sel in (("all", np.ones(len(L), bool)), ("no_screw", np.isin(L, NOFS)), ("screw", L == "screwdrop"),
                           ("dry", L == "dry"), ("lean", L == "lean"), ("loose", L == "loose")):
            if not sel.any():
                continue
            p = fmax[sel]
            r = {"n": int(sel.sum()), "caught": float(np.mean(p[:, -1] > 1)),
                 "caught_k": int(np.sum(p[:, -1] > 1)), "ci": C.wilson(int(np.sum(p[:, -1] > 1)), int(sel.sum())),
                 "by_s": [float(np.mean(p[:, j] > 1)) for j in range(5)]}
            share, thr = C.catch_at_fpr(hmax[:, -1], p[:, -1], 0.01)
            r["best_at_1pct"] = share
            r["auroc"] = C.auroc(hmax[:, -1], p[:, -1])
            per_c = []
            for c in cs:
                hc = res[c]["hold_stat"].max(1)
                fc = res[c]["fault_stat"].max(1)
                lc = res[c]["labels"]
                selc = np.isin(lc, NOFS) if fname == "no_screw" else (np.ones(len(lc), bool) if fname == "all"
                                                                       else lc == ("screwdrop" if fname == "screw" else fname))
                if selc.any():
                    per_c.append(C.auroc(hc, fc[selc]))
            r["auroc_per_cond"] = float(np.mean(per_c))
            if all("stream_stat" in res[c] for c in cs):
                S = np.concatenate([res[c]["stream_stat"] for c in cs])[sel]
                r["stream_clips_caught"] = float(np.mean(S.max(1) > 1))
                r["stream_seconds_over"] = float(np.mean(S > 1))
            g[fname] = r
        out[name] = g
    return out


def line_of(s: dict) -> str:
    a = s["all"]
    f = lambda x: f"{100 * x:.0f}"
    return (f"FA {a['fa_clips']}/{a['n_normal']} ep/h {a['fa_episodes_per_hour']:.1f} | all {f(a['all']['caught'])} "
            f"noScrew {f(a['no_screw']['caught'])} screw {f(a['screw']['caught'])} | dry {f(a['dry']['caught'])} "
            f"lean {f(a['lean']['caught'])} loose {f(a['loose']['caught'])} | clean {f(s['clean']['no_screw']['caught'])} "
            f"noise {f(s['noise']['no_screw']['caught'])} (noScrew) | best1% {f(a['all']['best_at_1pct'])}/"
            f"{f(a['no_screw']['best_at_1pct'])} | by1-3s {f(a['all']['by_s'][0])}/{f(a['all']['by_s'][1])}/"
            f"{f(a['all']['by_s'][2])} | stream {f(a['all'].get('stream_clips_caught', math.nan))} | aucC "
            f"{a['no_screw']['auroc_per_cond']:.3f}/{a['screw']['auroc_per_cond']:.3f}")


def run(job) -> str:
    name, which = job
    t0 = time.perf_counter()
    cfg = cfg_parse(name)
    conds = conds_of(which)
    res = {c: run_condition(cfg, c) for c in conds}
    s = summarise(res, conds)
    OUT.mkdir(parents=True, exist_ok=True)
    stem = name.replace("|", "__").replace("+", "-").replace("=", "").replace(",", "_").replace("@", "AT")
    np.savez(OUT / f"{stem}.{which}.npz", **{f"{c}|{k}": v for c in res for k, v in res[c].items()})
    (OUT / f"{stem}.{which}.json").write_text(json.dumps({"config": name, "set": which, "summary": s}, indent=1))
    per = " ".join(f"{ld}:{s[ld]['fa_clips']}/{100 * s[ld]['no_screw']['caught']:.0f}"
                   for ld in ("heavy", "light", "med") if ld in s)
    return f"{name:55s} {line_of(s)} || {per} [{time.perf_counter() - t0:.0f}s]"


def shift(job) -> str:
    """Normal clips under changed conditions (selection set only, heavy <-> light, no med): a model learned on one
    condition judges the held-out normal clips of (a) the other load at the same speed and noise, (b) the
    neighbouring speed (60<->80, 80<->100) at the same load and noise; and (c) the reverse split (learn on the last
    60 % of each recording, judge its first 35 %). Share of 5 s clips with an alarm. Lower = fewer alarms on things
    that are not faults (a load or speed change is not a fault, but should be learned before judging)."""
    import os
    name = job[0]
    cfg = cfg_parse(name)
    d = data()
    flat = lambda a: a.reshape(-1, a.shape[-1])
    conds = [c for c in conds_of("select")]
    models, holds = {}, {}
    for c in conds:
        learn, hold = C.split_normal(d, c)
        RL, grp = rows(cfg["fs"], learn)
        models[c] = make(cfg, grp, cfg["fs"]).learn(flat(RL))
        holds[c] = rows(cfg["fs"], hold)[0]
    raw = {"load": [], "speed": [], "reverse": []}
    kind = ["load"]
    def alarms(m, RH):
        sh = m.scores(flat(RH))
        st = rule_stat(m, sh, float(np.median(sh))).reshape(len(RH), 5)
        raw[kind[0]].extend(st.max(1).tolist())
        return int((st.max(1) > 1).sum()), len(RH)
    tot = {"load": [0, 0], "speed": [0, 0], "reverse": [0, 0]}
    for c in conds:
        load, vel, noise = c.split("_")
        other = ("light" if load == "heavy" else "heavy") + f"_{vel}_{noise}"
        if other in holds:
            a, n = alarms(models[c], holds[other]); tot["load"][0] += a; tot["load"][1] += n
        v = int(vel[3:])
        kind[0] = "speed"
        for v2 in (v - 20, v + 20):
            c2 = f"{load}_vel{v2}_{noise}"
            if c2 in holds:
                a, n = alarms(models[c], holds[c2]); tot["speed"][0] += a; tot["speed"][1] += n
        kind[0] = "load"
    os.environ["SSCC_SPLIT"] = "reverse"
    kind[0] = "reverse"
    try:
        for c in conds:
            learn, hold = C.split_normal(d, c)
            RL, grp = rows(cfg["fs"], learn)
            m = make(cfg, grp, cfg["fs"]).learn(flat(RL))
            a, n = alarms(m, rows(cfg["fs"], hold)[0]); tot["reverse"][0] += a; tot["reverse"][1] += n
    finally:
        os.environ.pop("SSCC_SPLIT", None)
    stem = name.replace("|", "__").replace("+", "-").replace("=", "").replace(",", "_").replace("@", "AT")
    (OUT / f"{stem}.shift.json").write_text(json.dumps({"config": name, "alarms": tot, "clip_max_stat": raw}))
    return f"{name:45s} other load {tot['load'][0]}/{tot['load'][1]} ({100 * tot['load'][0] / tot['load'][1]:.0f} %) | "            f"next speed {tot['speed'][0]}/{tot['speed'][1]} ({100 * tot['speed'][0] / tot['speed'][1]:.0f} %) | "            f"reverse split {tot['reverse'][0]}/{tot['reverse'][1]} ({100 * tot['reverse'][0] / tot['reverse'][1]:.0f} %)"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default="select", choices=("select", "confirm", "all"))
    ap.add_argument("--configs", required=True, help="semicolon-separated config names")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--part", default="protocolA", choices=("protocolA", "shift"))
    args = ap.parse_args(argv)
    if args.part == "shift":
        OUT.mkdir(parents=True, exist_ok=True)
        names = [x for x in args.configs.split(";") if x]
        with Pool(min(args.workers, len(names))) as pool:
            for msg in pool.imap_unordered(shift, [(n,) for n in names]):
                print(msg, flush=True)
        return 0
    names = [x for x in args.configs.split(";") if x]
    if args.set == "confirm":
        print("CONFIRMATION SET", flush=True)
    with Pool(min(args.workers, len(names))) as pool:
        for msg in pool.imap_unordered(run, [(n, args.set) for n in names]):
            print(msg, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
