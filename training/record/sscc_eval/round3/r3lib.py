"""Round 3 harness: data, a generic 'band model' framework with pluggable group detectors, and the Protocol A
evaluation (same split, same median-of-3 alarm rule, line from learning data only).

Model framework (mirrors effente.band_model.BandModel with norm='blocks', judge_with='ensemble'):
  robust scaling (median / IQR, clip +-8) fitted on each block's learning rows; per group a detector; 5 blocks in
  time, each block scored by models fitted on the other 4 (out-of-sample); each group's normal level = median of its
  out-of-sample scores; fused (mean or max); line = q-quantile of the running median of 3 out-of-sample fused scores;
  judging = mean over the 5 block models of the group scores / level, fused.
"""
from __future__ import annotations
import glob, json, os, time
from pathlib import Path
import numpy as np

BASE = Path(r"C:\Users\finnf\effente-sscc")
R3 = BASE / "round3"
FAULTS = ("dry", "lean", "loose", "screwdrop")
SPEC_LO = np.r_[np.arange(1000), 1000 + 10 * np.arange(700)].astype(float)

# ------------------------------------------------------------------ data

def cond_keys(load):
    ks = []
    for nf in sorted(glob.glob(str(BASE / "features" / f"normal__{load}_vel*_*.npz"))):
        key = os.path.basename(nf)[len("normal__"):-4]
        if glob.glob(str(BASE / "features" / f"abnormal__*__{key}_*.npz")):
            ks.append(key)
    return ks


def fault_files(key):
    return [(f, os.path.basename(p)) for f in FAULTS
            for p in sorted(glob.glob(str(BASE / "features" / f"abnormal__{f}__{key}_{f}*.npz")))]


_CACHE = {}


def raw(fname):
    """All stored per-second arrays of one folder, clips in time order."""
    if fname in _CACHE:
        return _CACHE[fname]
    d = np.load(BASE / "features" / fname)
    o = np.argsort(d["t_start"].astype(float), kind="stable")
    e3 = np.load(BASE / "features3" / fname)
    e4 = np.load(BASE / "features4" / fname)
    sp = np.load(R3 / "spec" / fname)
    assert np.array_equal(sp["clip_id"], d["clip_id"]) and np.array_equal(e3["clip_id"], d["clip_id"])
    out = {"gen_a": d["gen_a"][o].astype(float), "gen_v1": d["gen_v1"][o].astype(float),
           "alin": e3["alin"][o].astype(float), "a128": np.load(BASE / "features2" / fname)["a128"][o].astype(float),
           "a256": e3["a256"][o].astype(float), "alin25": e4["alin25"][o].astype(float),
           "spec": sp["spec"][o].astype(float), "stats": sp["stats"][o].astype(float),
           "frm": sp["frm"][o].astype(float), "env": sp["env"][o].astype(float)}
    fp = R3 / "frames" / fname
    if fp.exists():
        fw = np.load(fp)
        assert np.array_equal(fw["clip_id"], d["clip_id"])
        out["wf"] = fw["wf"][o].astype(float)
    _CACHE[fname] = out
    return out


def spec_bands(spec, edges):
    """(..., 1700) power -> (..., bands) log10 band powers (edges in Hz; above 1 kHz rounded to 10 Hz)."""
    cs = np.concatenate([np.zeros(spec.shape[:-1] + (1,)), np.cumsum(spec, -1)], -1)
    i = np.searchsorted(SPEC_LO, edges)
    return np.log10(np.maximum(cs[..., i[1:]] - cs[..., i[:-1]], 1e-30) + 1e-20)


# ------------------------------------------------------------------ framework

def running_median(s, k):
    s = np.asarray(s, float)
    if k <= 1:
        return s
    full = np.r_[np.full(k - 1, s[0]), s] if False else s
    out = np.empty(len(s))
    for i in range(len(s)):
        out[i] = np.median(s[max(0, i - k + 1):i + 1])
    return out


def blocks_of(n, nb):
    return np.arange(n) * nb // n


class PCA:
    def __init__(self, var=0.95, max_k=40):
        self.var, self.max_k = var, max_k

    def fit(self, Z):
        mu = Z.mean(0)
        _, sv, vt = np.linalg.svd(Z - mu, full_matrices=False)
        share = np.cumsum(sv ** 2) / max(float((sv ** 2).sum()), 1e-12)
        k = int(min(np.searchsorted(share, self.var) + 1, self.max_k, len(sv)))
        V = vt[:k].T
        h = (Z - mu) @ V
        r = (Z - mu) - h @ V.T
        self.mu, self.V, self.hs, self.ref = mu, V, np.maximum(h.std(0), 1e-6), max(float((r ** 2).mean()), 1e-9)
        return self

    def score(self, Z):
        D = Z - self.mu
        h = D @ self.V
        r = D - h @ self.V.T
        return (r ** 2).mean(1) / self.ref + ((h / self.hs) ** 2).mean(1)


class Framework:
    def __init__(self, det, groups, fuse="mean", q=0.995, blocks=5, smooth=3, clip=8.0, transform=None, gq=0.5, chunk=0):
        self.det, self.groups_in, self.fuse_how, self.q = det, groups, fuse, q
        self.blocks, self.smooth, self.clip, self.gq, self.chunk = blocks, smooth, clip, gq, chunk
        self.transform = transform  # optional fn(Z scaled) -> Z (row-wise, no fitting)

    def _scaler(self, R):
        med = np.median(R, 0)
        q1, q3 = np.percentile(R, [25, 75], 0)
        sd = (q3 - q1) / 1.349
        sd = np.where(sd > 1e-9, sd, R.std(0))
        sd = np.where(sd > 1e-9, sd, 1.0)
        return med, sd

    def _scale(self, sc, R):
        return np.clip((R - sc[0]) / sc[1], -self.clip, self.clip)

    def _fit_core(self, R):
        sc = self._scaler(R)
        Z = self._scale(sc, R)
        return {"sc": sc, "models": [self.det().fit(Z[:, g]) for g in self.groups_in]}

    def _gs(self, core, R):
        Z = self._scale(core["sc"], R)
        return np.stack([m.score(Z[:, g]) for m, g in zip(core["models"], self.groups_in)], 1)

    def _fuse(self, G):
        if self.fuse_how == "mean":
            return G.mean(1)
        if self.fuse_how == "max":
            return G.max(1)
        if self.fuse_how.startswith("p"):  # power mean
            p = float(self.fuse_how[1:])
            return (np.maximum(G, 0) ** p).mean(1) ** (1 / p)
        raise ValueError(self.fuse_how)

    def learn(self, R, rec=None):
        R = np.asarray(R, float)
        rec = np.zeros(len(R), int) if rec is None else np.asarray(rec)
        if self.chunk <= 0:
            b = np.zeros(len(R), int)
            for r in np.unique(rec):
                idx = np.nonzero(rec == r)[0]
                b[idx] = blocks_of(len(idx), self.blocks)
        else:
            b = (np.arange(len(R)) // self.chunk) % self.blocks
        G = np.zeros((len(R), len(self.groups_in)))
        self.cores = []
        for k in range(self.blocks):
            core = self._fit_core(R[b != k])
            self.cores.append(core)
            G[b == k] = self._gs(core, R[b == k])
        self.gmed = np.maximum(np.quantile(G, self.gq, axis=0), 1e-12)
        self.Goof = G / self.gmed
        self.oof = self._fuse(self.Goof)
        self.line = float(np.quantile(np.concatenate([running_median(self.oof[rec == r], self.smooth)
                                                       for r in np.unique(rec)]), self.q))
        return self

    def group_scores(self, R):
        R = np.atleast_2d(np.asarray(R, float))
        return sum(self._gs(c, R) for c in self.cores) / len(self.cores) / self.gmed

    def scores(self, R):
        return self._fuse(self.group_scores(R))


# ------------------------------------------------------------------ evaluation

def split(n):
    a = int(np.floor(0.6 * n + 0.5))
    b = a + int(np.floor(0.05 * n + 0.5))
    return slice(0, a), slice(b, n)


def med3_stat(s, pre, k=3):
    full = np.r_[np.full(k - 1, pre), s]
    return np.array([np.median(full[i:i + k]) for i in range(len(s))])


def run_cond(key, featfn, modelfn, keep_scores=False):
    """featfn(raw dict) -> (clips, 5, d) ; modelfn() -> object with learn(rows)->self, .line, .oof, .scores(rows)
    (and optionally .group_scores)."""
    N = featfn(raw(f"normal__{key}.npz"))
    tr, ho = split(len(N))
    d = N.shape[-1]
    m = modelfn().learn(N[tr].reshape(-1, d))
    H = N[ho]
    sh = m.scores(H.reshape(-1, d))
    pre = float(np.median(m.oof))
    hstat = (med3_stat(sh, pre) / m.line).reshape(len(H), 5)
    fstat, labs, fscore, folders = [], [], [], []
    for lab, fn in fault_files(key):
        F = featfn(raw(fn))
        sf = m.scores(F.reshape(-1, d)).reshape(len(F), 5)
        fstat.append(np.array([med3_stat(x, pre) for x in sf]) / m.line)
        fscore.append(sf)
        labs += [lab] * len(F)
        folders += [fn] * len(F)
    out = {"key": key, "hold_max": hstat.max(1), "fault_max": np.concatenate(fstat).max(1), "labels": np.array(labs),
           "line": m.line}
    if keep_scores:
        out.update({"oof": m.oof, "hold_score": sh.reshape(len(H), 5), "fault_score": np.concatenate(fscore),
                    "pre": pre})
    return out


def summarize(res):
    """res: list of run_cond outputs -> dict of counts/percent."""
    def agg(rs):
        fa = sum(int((r["hold_max"] > 1).sum()) for r in rs)
        near = sum(int((r["hold_max"] > 0.9).sum()) for r in rs)
        nh = sum(len(r["hold_max"]) for r in rs)
        lab = np.concatenate([r["labels"] for r in rs]) if rs else np.array([])
        hit = np.concatenate([r["fault_max"] > 1 for r in rs]) if rs else np.array([])
        pc = lambda fs: 100.0 * hit[np.isin(lab, fs)].mean() if np.isin(lab, fs).any() else float("nan")
        return {"fa": fa, "near": near, "nh": nh, "all": pc(FAULTS), "ns": pc(FAULTS[:3]), "screw": pc(["screwdrop"]),
                **{f: pc([f]) for f in FAULTS[:3]}}
    out = {}
    loads = sorted(set(r["key"].split("_")[0] for r in res))
    for L in loads:
        rs = [r for r in res if r["key"].startswith(L + "_")]
        out[L] = agg(rs)
        out[L + "/clean"] = agg([r for r in rs if r["key"].endswith("clean")])
        out[L + "/noise"] = agg([r for r in rs if r["key"].endswith("noise")])
    out["ALL"] = agg(res)
    out["ALL/clean"] = agg([r for r in res if r["key"].endswith("clean")])
    out["ALL/noise"] = agg([r for r in res if r["key"].endswith("noise")])
    return out


def fmt(s, loads=("heavy", "light")):
    parts = []
    for L in loads:
        a, c, n = s[L], s[L + "/clean"], s[L + "/noise"]
        parts.append(f"{L[:2]} {a['fa']}({a['near']})/{a['nh']} ns {a['ns']:5.1f} (c {c['ns']:5.1f} n {n['ns']:5.1f}) "
                     f"d/l/o {a['dry']:.0f}/{a['lean']:.0f}/{a['loose']:.0f} sc {a['screw']:.0f}")
    return " | ".join(parts)
