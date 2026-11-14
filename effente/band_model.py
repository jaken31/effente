"""A second normal model for effente: band features per sensor group, one small PCA per group, fused.
NumPy only. New file: nothing else in effente/ uses it yet, and nothing existing changes.

Where it comes from: the SSCC chain-conveyor benchmark, second round (training/record/datasets/SSCC_RESULTS.md).
Chosen on SSCC data only (heavy and light loads); the med loads were kept back to confirm it. It learns from normal
running only: no fault examples, no pretrained weights.

One row a second (window_row):
  vibration, per axis of the 1 kHz accelerometer: 20 log10 band powers on geomspace(5, 500, 21) Hz, log10 rms,
  kurtosis, crest factor (69 inputs);
  sound at 16 kHz: 159 log10 band powers of 50 Hz each from 50 to 8000 Hz, log10 rms, kurtosis, crest factor
  (162 inputs).

BandModel.learn(rows, rec=None): rows in time order, one a second, normal running only (rec: recording number per
row when the learning joins several recordings; blocks are then taken inside each one).
  0. the vibration group is used only if the accelerometer hears the machine: at least 25 % of its band inputs
     6 dB or more over the MPU-6500's own noise floor (MPU_FLOOR, a datasheet-based figure); otherwise sound only;
  1. robust scaling: (x - median) / (IQR / 1.349), clipped at +-8; inputs that never move are dropped;
  2. the learning rows are cut into 5 blocks in time; for each block, a model is learned on the other four:
     per group a PCA (95 % of variance, at most 40 patterns) scored as the device's PCA (mean squared residual over
     its learning mean, plus mean Hotelling T2);
  3. each group's out-of-sample scores are divided by their median (each group's normal level = 1), and a second's
     score is the MEAN over the groups;
  4. the line: the 99.5th percentile of the median of 3 consecutive out-of-sample scores;
  5. judging uses the same 5 block models (the mean of their group scores), so the line and the judging come from
     models learned on as many rows.
judge(row) once a second: alarm when the median of the last 3 scores is over line x margin (a spike rule exists,
off by default: on SSCC it added 5 % first-second catches and many alarms in the first minutes after a start).

BandModes: one BandModel per operating point (for instance one learning recording per belt speed); a second is
normal when it is normal for any of them. On SSCC, needed when one model must cover several speeds.

UNVERIFIED: nothing here has run on the board; costs in SSCC_RESULTS.md are PC timings scaled by a rough ratio.
MPU_FLOOR is computed from the datasheet noise, not measured on our board.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

VIB_EDGES = np.geomspace(5.0, 500.0, 21)
SND_EDGES = np.arange(50.0, 8001.0, 50.0)  # 159 linear bands of 50 Hz (round 2: better than 64 log bands on SSCC)
N_VIB = 3 * (len(VIB_EDGES) - 1 + 3)
N_SND = len(SND_EDGES) - 1 + 3
FORMAT = "effente-band-model-1"


# ----------------------------------------------------------------- features

def band_features(x: np.ndarray, rate: float, edges: np.ndarray) -> np.ndarray:
    """One window, (n,) or (n, channels) -> per channel: log10 band powers, log10 rms, kurtosis, crest factor
    (channel-major, flattened)."""
    x = np.asarray(x, float)
    x = x[:, None] if x.ndim == 1 else x
    x = x - x.mean(0)
    w = np.hanning(len(x))
    p = np.abs(np.fft.rfft(x * w[:, None], axis=0)) ** 2
    f = np.fft.rfftfreq(len(x), 1 / rate)
    cs = np.vstack([np.zeros((1, p.shape[1])), np.cumsum(p, 0)])
    i = np.searchsorted(f, edges)
    bands = np.log10(np.maximum(cs[i[1:]] - cs[i[:-1]], 1e-30) / (2 * w.sum() ** 2) + 1e-20)
    rms = x.std(0) + 1e-12
    return np.vstack([bands, np.log10(rms), (x ** 4).mean(0) / rms ** 4 - 3, np.abs(x).max(0) / rms]).T.ravel()


SND_EDGES_LOG64 = np.geomspace(50.0, 8000.0, 65)  # the first round's sound bands (64 log bands)


def window_row(accel_g: np.ndarray, audio: np.ndarray, accel_rate: float = 1000.0,
               audio_rate: float = 16000.0, audio_full_scale: float = 32767.0,
               snd_edges: np.ndarray = SND_EDGES) -> np.ndarray:
    """One second: (n, 3) acceleration in g and (m,) sound -> N_VIB + len(snd_edges) + 2 inputs (vibration first).
    The sound's scale only shifts its log levels, which the robust scaling removes; keep it fixed per device."""
    return np.r_[band_features(accel_g, accel_rate, VIB_EDGES),
                 band_features(np.asarray(audio, float) / audio_full_scale, audio_rate, snd_edges)]


def default_groups(n_inputs: int = N_VIB + N_SND) -> list[np.ndarray]:
    return [np.arange(N_VIB), np.arange(N_VIB, n_inputs)]


# The MPU-6500's own noise in the vibration inputs (per axis x, y, z: 20 log10 band powers, log10 rms, kurtosis,
# crest), with the machine still: its datasheet noise (300 ug/sqrt(Hz)) through the chip's 218 Hz low-pass and
# 1/4096 g steps, 1-second windows, median of 60 (training/record/sscc_eval/vib_floor.py). A datasheet figure, not
# measured on our board: better, record the board on the machine with the machine off and use that.
MPU_FLOOR = np.array([
    [-7.261, -7.602, -7.277, -7.032, -7.057, -6.965, -6.764, -6.740, -6.589, -6.549, -6.390, -6.287, -6.203, -6.084,
     -5.944, -5.920, -6.162, -6.926, -8.429, -9.415, -2.357, -0.055, 3.330],
    [-7.329, -7.574, -7.279, -7.047, -7.085, -6.901, -6.720, -6.769, -6.657, -6.552, -6.372, -6.289, -6.199, -6.084,
     -5.994, -5.916, -6.123, -6.975, -8.341, -9.412, -2.357, -0.054, 3.390],
    [-7.348, -7.611, -7.141, -7.136, -7.086, -6.966, -6.705, -6.760, -6.642, -6.439, -6.377, -6.235, -6.209, -6.065,
     -5.984, -5.929, -6.148, -6.956, -8.396, -9.430, -2.355, -0.031, 3.412]]).ravel()


def vib_active(raw_median: np.ndarray, floor: np.ndarray = MPU_FLOOR, min_snr_db: float = 6.0,
               min_share: float = 0.25) -> np.ndarray:
    """Does the accelerometer hear the machine, or only its own noise? The whole vibration group is used when at least
    min_share of its band inputs have a learning median power min_snr_db or more over the sensor's floor; otherwise
    none of it (a group of sensor noise only raises the line that the other group must pass). Returns a mask over
    the N_VIB vibration inputs."""
    m = np.asarray(raw_median, float)[:N_VIB].reshape(3, -1)
    f = np.asarray(floor, float).reshape(3, -1)
    nb = len(VIB_EDGES) - 1
    share = float(np.mean(10 * (m[:, :nb] - f[:, :nb]) >= min_snr_db))
    return np.full(N_VIB, share >= min_share)


# ----------------------------------------------------------------- small helpers

def running_median(s: np.ndarray, k: int) -> np.ndarray:
    s = np.asarray(s, float)
    return np.array([np.median(s[max(0, i - k + 1):i + 1]) for i in range(len(s))])


def pool_rows(Z: np.ndarray, k: int, history: np.ndarray | None = None) -> np.ndarray:
    """Row i -> mean of the last k rows up to i (history rows, if given, come before Z; fewer at the start)."""
    if k <= 1:
        return Z
    H = np.zeros((0, Z.shape[1])) if history is None else np.asarray(history, float)[-(k - 1):]
    A = np.vstack([H, Z])
    cs = np.vstack([np.zeros((1, A.shape[1])), np.cumsum(A, 0)])
    j = np.arange(len(H), len(A)) + 1
    lo = np.maximum(j - k, 0)
    return (cs[j] - cs[lo]) / (j - lo)[:, None]


def _blocks_within(rec: np.ndarray, n_blocks: int) -> np.ndarray:
    """Block number of every row: n_blocks contiguous blocks inside each recording."""
    b = np.zeros(len(rec), int)
    for r in np.unique(rec):
        idx = np.nonzero(rec == r)[0]
        b[idx] = np.arange(len(idx)) * n_blocks // len(idx)
    return b


# ----------------------------------------------------------------- one group's model

def pca_fit(Z: np.ndarray, var: float = 0.95, max_k: int = 40) -> dict:
    mu = Z.mean(0)
    _, sv, vt = np.linalg.svd(Z - mu, full_matrices=False)
    share = np.cumsum(sv ** 2) / max(float((sv ** 2).sum()), 1e-12)
    k = int(min(np.searchsorted(share, var) + 1, max_k, len(sv)))
    V = vt[:k].T
    h = (Z - mu) @ V
    r = (Z - mu) - h @ V.T
    return {"mu": mu, "V": V, "hs": np.maximum(h.std(0), 1e-6), "ref": max(float((r ** 2).mean()), 1e-9)}


def pca_score(m: dict, Z: np.ndarray) -> np.ndarray:
    D = Z - m["mu"]
    h = D @ m["V"]
    r = D - h @ m["V"].T
    return (r ** 2).mean(1) / m["ref"] + ((h / m["hs"]) ** 2).mean(1)


# ----------------------------------------------------------------- the model

class BandModel:
    def __init__(self, var: float = 0.95, max_k: int = 40, blocks: int = 5, q: float = 0.995, smooth: int = 3,
                 spike: float = 0.0, pool: int = 1, groups: list | None = None, clip: float = 8.0,
                 norm: str = "blocks", norm_q: float = 0.5, judge_with: str = "ensemble",
                 vib_floor: np.ndarray | None = MPU_FLOOR, vib_cols: np.ndarray | None = None,
                 min_snr_db: float = 6.0, fuse: str = "mean"):
        self.var, self.max_k, self.blocks, self.q, self.smooth = var, max_k, blocks, q, smooth
        self.norm, self.norm_q, self.judge_with, self.fuse_how = norm, norm_q, judge_with, fuse
        # the vibration inputs (N_VIB of them, in band_features order) that are checked against the sensor's floor;
        # default: the first N_VIB inputs when the default groups are used. vib_floor None = no check.
        self.vib_floor, self.min_snr_db = vib_floor, min_snr_db
        self.vib_cols = (np.arange(N_VIB) if groups is None else None) if vib_cols is None else np.asarray(vib_cols)
        self.active = None
        self.spike_times, self.pool, self.clip = spike, pool, clip
        self.groups_in = None if groups is None else [np.asarray(g, int) for g in groups]
        self.recent: list[float] = []
        self.hist = None

    # --- pieces that both learning and judging use
    def _scaler(self, R: np.ndarray):
        med = np.median(R, 0)
        q1, q3 = np.percentile(R, [25, 75], 0)
        sd = (q3 - q1) / 1.349
        sd = np.where(sd > 1e-9, sd, R.std(0))
        keep = np.nonzero(sd > 1e-9)[0]
        return keep, med[keep], sd[keep]

    def _scale(self, sc, R: np.ndarray) -> np.ndarray:
        keep, med, sd = sc
        return np.clip((np.atleast_2d(R)[:, keep] - med) / sd, -self.clip, self.clip)

    def _groups_after(self, keep: np.ndarray, n_in: int) -> list[np.ndarray]:
        gin = self.groups_in if self.groups_in is not None else default_groups(n_in)
        pos = {c: i for i, c in enumerate(keep) if self.active is None or self.active[c]}
        out = [np.array([pos[c] for c in g if c in pos], int) for g in gin]
        return [g for g in out if len(g)]

    def _fit_core(self, R: np.ndarray, rec: np.ndarray, seg: np.ndarray | None = None) -> dict:
        """Scaling, pooling, per-group PCA and per-group normaliser from rows R (time order, recordings rec;
        seg: contiguous stretches, for pooling, default = rec)."""
        sc = self._scaler(R)
        Z = self._pooled(self._scale(sc, R), rec if seg is None else seg)
        groups = self._groups_after(sc[0], R.shape[1])
        half = _blocks_within(rec, 2) == 0
        models, med = [], []
        for g in groups:
            if self.norm == "halves":  # the first benchmark's normaliser (two halves in time, inside each recording)
                oof = np.r_[self._gscore(self._gfit(Z[half][:, g]), Z[~half][:, g]),
                            self._gscore(self._gfit(Z[~half][:, g]), Z[half][:, g])]
                med.append(max(float(np.median(oof)), 1e-12))
            else:  # set by learn() from the out-of-sample block scores
                med.append(1.0)
            models.append(self._gfit(Z[:, g]))
        return {"scaler": sc, "groups": groups, "models": models, "med": med}

    def _pooled(self, Z: np.ndarray, rec: np.ndarray) -> np.ndarray:
        if self.pool <= 1:
            return Z
        out = np.empty_like(Z)
        for r in np.unique(rec):
            idx = np.nonzero(rec == r)[0]
            out[idx] = pool_rows(Z[idx], self.pool)
        return out

    def _gfit(self, Z: np.ndarray):
        return pca_fit(Z, self.var, self.max_k)

    def _gscore(self, m, Z: np.ndarray) -> np.ndarray:
        return pca_score(m, Z)

    def _fuse(self, G: np.ndarray) -> np.ndarray:
        return G.mean(1) if self.fuse_how == "mean" else G.max(1)

    def _group_scores(self, core: dict, Z: np.ndarray) -> np.ndarray:
        return np.stack([self._gscore(m, Z[:, g]) / md for m, g, md in zip(core["models"], core["groups"], core["med"])], 1)

    # --- learning
    def learn(self, rows, rec=None) -> "BandModel":
        R = np.asarray(rows, float)
        rec = np.zeros(len(R), int) if rec is None else np.asarray(rec)
        self.active = np.ones(R.shape[1], bool)
        if self.vib_floor is not None and self.vib_cols is not None:
            self.active[self.vib_cols] = vib_active(np.median(R[:, self.vib_cols], 0), self.vib_floor, self.min_snr_db)
        b = _blocks_within(rec, self.blocks)
        G, cores = None, []
        for k in range(self.blocks):
            seg = rec * 2 + (b > k)
            core = self._fit_core(R[b != k], rec[b != k], seg[b != k])
            cores.append(core)
            Z = self._scale(core["scaler"], R)
            Z = self._pooled(Z, rec)[b == k]
            gk = self._group_scores(core, Z)
            G = np.zeros((len(R), gk.shape[1])) if G is None else G
            G[b == k] = gk
        # each group's out-of-sample learning scores over their norm_q quantile (median): every group's normal level is 1
        if self.norm == "halves":
            self.gmed = np.ones(G.shape[1])
        else:
            self.gmed = np.maximum(np.quantile(G, self.norm_q, axis=0), 1e-12)
        oof = self._fuse(G / self.gmed)
        stat = np.concatenate([running_median(oof[rec == r], self.smooth) for r in np.unique(rec)])
        self.line = float(np.quantile(stat, self.q))
        self.spike = float(oof.max()) * self.spike_times if self.spike_times > 0 else float("inf")
        self.oof = oof
        # judging: "ensemble" = the same block models that set the line (mean of their group scores), so the line and
        # the judging come from models learned on as many rows; "full" = one model on all rows (the first benchmark's)
        self.cores = cores if self.judge_with == "ensemble" else [self._fit_core(R, rec)]
        self.raw_median = np.median(R, 0)
        self.n_in = R.shape[1]
        self.reset()
        return self

    # --- judging
    def group_matrix(self, rows, history=None) -> np.ndarray:
        """(n, groups) normalised group scores of a contiguous run of rows (history: raw rows just before it,
        used by pooling only)."""
        R = np.atleast_2d(np.asarray(rows, float))
        G = 0.0
        for core in self.cores:
            Z = self._scale(core["scaler"], R)
            H = None if history is None or self.pool <= 1 else self._scale(core["scaler"], np.asarray(history, float))
            G = G + self._group_scores(core, pool_rows(Z, self.pool, H))
        return G / len(self.cores) / self.gmed

    def scores(self, rows, history=None, group_scores: bool = False):
        """Per-second fused scores of a contiguous run of rows."""
        G = self.group_matrix(rows, history)
        return (self._fuse(G), G) if group_scores else self._fuse(G)

    def typical_row(self) -> np.ndarray:
        """A raw row at the learning median of every input."""
        return self.raw_median.copy()

    def reset(self):
        self.recent, self.hist = [], None

    def judge(self, row, margin: float = 1.0, spike_margin: float = 1.0) -> dict:
        """One second. Returns alarm, level (median of the last `smooth` scores over the line), the score, whether
        the spike rule fired, and each group's score over its normal level (which group moved)."""
        row = np.atleast_2d(np.asarray(row, float))
        if self.pool > 1:
            self.hist = row if self.hist is None else np.vstack([self.hist, row])[-self.pool:]
            g = self.group_matrix(self.hist[-1:], self.hist[:-1] if len(self.hist) > 1 else None)
        else:
            g = self.group_matrix(row)
        s = float(self._fuse(g)[0])
        self.recent = (self.recent + [s])[-self.smooth:]
        level = float(np.median(self.recent)) / self.line
        spike = s > self.spike * spike_margin
        return {"alarm": bool(level > margin or spike), "level": level, "score": s, "spike": bool(spike),
                "groups": [float(x) for x in g[0]]}

    # --- saving
    def to_dict(self) -> dict:
        def core_d(c):
            keep, med, sd = c["scaler"]
            return {"keep": keep.tolist(), "median": med.tolist(), "spread": sd.tolist(),
                    "groups": [g.tolist() for g in c["groups"]], "group_median": [float(x) for x in c["med"]],
                    "pca": [{"mu": m["mu"].tolist(), "V": m["V"].tolist(), "hs": m["hs"].tolist(), "ref": m["ref"]}
                            for m in c["models"]]}
        return {"format": FORMAT, "n_in": int(self.n_in),
                "settings": {"var": self.var, "max_k": self.max_k, "blocks": self.blocks, "q": self.q,
                             "smooth": self.smooth, "spike": self.spike_times, "pool": self.pool, "clip": self.clip,
                             "norm": self.norm, "norm_q": self.norm_q, "judge_with": self.judge_with,
                             "fuse": self.fuse_how, "min_snr_db": self.min_snr_db},
                "active": self.active.astype(int).tolist(),
                "line": self.line, "spike_line": self.spike if np.isfinite(self.spike) else None,
                "group_level": [float(x) for x in self.gmed], "raw_median": self.raw_median.tolist(),
                "cores": [core_d(c) for c in self.cores]}

    @classmethod
    def from_dict(cls, d: dict) -> "BandModel":
        if d.get("format") != FORMAT:
            raise ValueError(f"not a band model: {d.get('format')}")
        s = d["settings"]
        m = cls(var=s["var"], max_k=s["max_k"], blocks=s["blocks"], q=s["q"], smooth=s["smooth"], spike=s["spike"],
                pool=s["pool"], clip=s["clip"], norm=s["norm"], norm_q=s["norm_q"], judge_with=s["judge_with"],
                fuse=s["fuse"], min_snr_db=s["min_snr_db"])
        m.active = np.array(d["active"], bool)
        m.n_in, m.line = d["n_in"], d["line"]
        m.spike = float("inf") if d["spike_line"] is None else d["spike_line"]
        m.gmed = np.array(d["group_level"])
        m.raw_median = np.array(d["raw_median"])
        m.cores = [{"scaler": (np.array(c["keep"], int), np.array(c["median"]), np.array(c["spread"])),
                    "groups": [np.array(g, int) for g in c["groups"]], "med": list(c["group_median"]),
                    "models": [{"mu": np.array(p["mu"]), "V": np.array(p["V"]).reshape(len(p["mu"]), -1),
                                "hs": np.array(p["hs"]), "ref": p["ref"]} for p in c["pca"]]}
                   for c in d["cores"]]
        m.reset()
        return m

    def save(self, path):
        Path(path).write_text(json.dumps(self.to_dict()))

    @classmethod
    def load(cls, path) -> "BandModel":
        return cls.from_dict(json.loads(Path(path).read_text()))


# Named settings. "lin50" is the round-2 final (frozen before its confirmation run). "log64" is the first round's
# recommendation (64 log sound bands, groups fused by the maximum, group levels from two halves in time taken inside
# each recording, judged by one model on all rows, 99th percentile line), with that normaliser fix: use it with
# window_row(..., snd_edges=SND_EDGES_LOG64). On SSCC's held-back set the two catch about as many real faults
# (see SSCC_RESULTS.md, round 2); lin50 is stronger under loud background noise and on the screwdrop, log64 in quiet.
PRESETS = {
    "lin50": {},
    "log64": {"q": 0.99, "fuse": "max", "norm": "halves", "judge_with": "full", "vib_floor": None},
}


def make_model(preset: str = "lin50", **kw) -> "BandModel":
    return BandModel(**{**PRESETS[preset], **kw})


class BandModes:
    """Several operating points (belt speeds, loads, backgrounds), each learned as its own BandModel; a second is
    normal when it is normal for any of them: score = the smallest of (each mode's score / its line), line = 1.
    learn(rows, rec): rec numbers the operating point of every row (for instance, one learning recording per speed).
    Judging needs no speed reading: the closest mode wins."""

    def __init__(self, **kw):
        self.kw = kw
        self.line, self.spike = 1.0, float("inf")
        self.smooth = kw.get("smooth", 3)
        self.pool = kw.get("pool", 1)
        self.recent: list[float] = []

    def learn(self, rows, rec=None) -> "BandModes":
        R = np.asarray(rows, float)
        rec = np.zeros(len(R), int) if rec is None else np.asarray(rec)
        self.modes = [BandModel(**self.kw).learn(R[rec == r]) for r in np.unique(rec)]
        self.oof = np.concatenate([m.oof / m.line for m in self.modes])
        return self

    def scores(self, rows, history=None):
        return np.min([m.scores(rows, history) / m.line for m in self.modes], axis=0)

    def typical_row(self) -> np.ndarray:
        return self.modes[0].typical_row()

    def reset(self):
        self.recent = []
        for m in self.modes:
            m.reset()

    def judge(self, row, margin: float = 1.0) -> dict:
        outs = [m.judge(row) for m in self.modes]
        s = min(o["score"] / m.line for o, m in zip(outs, self.modes))
        self.recent = (self.recent + [s])[-self.smooth:]
        level = float(np.median(self.recent))
        best = int(np.argmin([o["score"] / m.line for o, m in zip(outs, self.modes)]))
        return {"alarm": bool(level > margin), "level": level, "score": s, "mode": best, "groups": outs[best]["groups"]}

    def to_dict(self) -> dict:
        return {"format": FORMAT + "-modes", "modes": [m.to_dict() for m in self.modes]}

    @classmethod
    def from_dict(cls, d: dict) -> "BandModes":
        o = cls()
        o.modes = [BandModel.from_dict(x) for x in d["modes"]]
        o.smooth, o.pool = o.modes[0].smooth, o.modes[0].pool
        return o
