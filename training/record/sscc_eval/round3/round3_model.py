"""Round-3 normal model for effente ("hyb60"). NumPy only (learn, judge, save, load).

Chosen on SSCC heavy + light loads (round 3, C:\\Users\\finnf\\effente-sscc\\round3\\ROUND3.md); frozen before its
single med-load run.

One row a second (window_row):
  vibration, per axis of the 1 kHz accelerometer: 20 log10 band powers on geomspace(5, 500, 21) Hz, log10 rms,
  kurtosis, crest factor (69 inputs, as effente/band_model.py);
  sound at 16 kHz, one 1 s Hann FFT: 30 log10 band powers on geomspace(50, 600, 31) Hz (fine at low frequency, like
  log64), then 148 linear 50 Hz bands from 600 to 8000 Hz (like lin50), then log10 rms, kurtosis, crest factor
  (181 inputs; the ESP32 sends these 181 values).

Round3Model.learn(rows, rec=None): rows in time order, normal running only.
  0. the vibration group is used only if the accelerometer hears the machine (lin50's rule: >= 25 % of its bands
     6 dB or more over the MPU-6500 floor); otherwise sound only (always the case on SSCC);
  1. 5 contiguous blocks in time (inside each recording); for each block, on the other four: robust scaling
     ((x - median) / (IQR / 1.349), clipped +-8) and per group a PCA (95 % of variance, at most 60 patterns) scored
     as mean squared residual / its learning mean + mean Hotelling T2;
  2. each group's out-of-sample scores over their median (normal level 1); groups fused by the MAX;
  3. line = 99.5th percentile of the running median of 3 out-of-sample fused scores;
  4. judging = the mean of the 5 block models' group scores (the models that set the line).
judge(row): alarm when the median of the last 3 scores is over line x margin.

Adding a sensor group (thermal camera): give its columns as another entry of `groups`; it gets its own PCA and
normal level and joins by the max. Nothing else changes.
"""
from __future__ import annotations

import io
import json
from pathlib import Path

import numpy as np

VIB_EDGES = np.geomspace(5.0, 500.0, 21)
SND_EDGES = np.r_[np.geomspace(50.0, 600.0, 31), np.arange(650.0, 8000.1, 50.0)]  # 178 bands
N_VIB = 3 * (len(VIB_EDGES) - 1 + 3)
N_SND = len(SND_EDGES) - 1 + 3
FORMAT = "effente-round3-hyb60-1"

MPU_FLOOR = np.array([
    [-7.261, -7.602, -7.277, -7.032, -7.057, -6.965, -6.764, -6.740, -6.589, -6.549, -6.390, -6.287, -6.203, -6.084,
     -5.944, -5.920, -6.162, -6.926, -8.429, -9.415, -2.357, -0.055, 3.330],
    [-7.329, -7.574, -7.279, -7.047, -7.085, -6.901, -6.720, -6.769, -6.657, -6.552, -6.372, -6.289, -6.199, -6.084,
     -5.994, -5.916, -6.123, -6.975, -8.341, -9.412, -2.357, -0.054, 3.390],
    [-7.348, -7.611, -7.141, -7.136, -7.086, -6.966, -6.705, -6.760, -6.642, -6.439, -6.377, -6.235, -6.209, -6.065,
     -5.984, -5.929, -6.148, -6.956, -8.396, -9.430, -2.355, -0.031, 3.412]]).ravel()


# ----------------------------------------------------------------- features

def band_features(x, rate, edges):
    """One window, (n,) or (n, channels) -> per channel: log10 band powers, log10 rms, kurtosis, crest factor."""
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


def sound_row(audio, audio_rate=16000.0, audio_full_scale=32767.0):
    return band_features(np.asarray(audio, float) / audio_full_scale, audio_rate, SND_EDGES)


def window_row(accel_g, audio, accel_rate=1000.0, audio_rate=16000.0, audio_full_scale=32767.0):
    """One second: (n, 3) acceleration in g and (m,) sound -> N_VIB + N_SND inputs (vibration first)."""
    return np.r_[band_features(accel_g, accel_rate, VIB_EDGES), sound_row(audio, audio_rate, audio_full_scale)]


def vib_active(raw_median, floor=MPU_FLOOR, min_snr_db=6.0, min_share=0.25):
    m = np.asarray(raw_median, float)[:N_VIB].reshape(3, -1)
    fl = np.asarray(floor, float).reshape(3, -1)
    nb = len(VIB_EDGES) - 1
    return float(np.mean(10 * (m[:, :nb] - fl[:, :nb]) >= min_snr_db)) >= min_share


# ----------------------------------------------------------------- pieces

def running_median(s, k):
    s = np.asarray(s, float)
    return np.array([np.median(s[max(0, i - k + 1):i + 1]) for i in range(len(s))])


def _blocks_within(rec, n_blocks):
    b = np.zeros(len(rec), int)
    for r in np.unique(rec):
        idx = np.nonzero(rec == r)[0]
        b[idx] = np.arange(len(idx)) * n_blocks // len(idx)
    return b


def _pca_fit(Z, var, max_k):
    mu = Z.mean(0)
    _, sv, vt = np.linalg.svd(Z - mu, full_matrices=False)
    share = np.cumsum(sv ** 2) / max(float((sv ** 2).sum()), 1e-12)
    k = int(min(np.searchsorted(share, var) + 1, max_k, len(sv)))
    V = vt[:k].T
    h = (Z - mu) @ V
    r = (Z - mu) - h @ V.T
    return {"mu": mu, "V": V, "hs": np.maximum(h.std(0), 1e-6), "ref": max(float((r ** 2).mean()), 1e-9)}


def _pca_score(m, Z):
    D = Z - m["mu"]
    h = D @ m["V"]
    r = D - h @ m["V"].T
    return (r ** 2).mean(1) / m["ref"] + ((h / m["hs"]) ** 2).mean(1)


# ----------------------------------------------------------------- the model

class Round3Model:
    def __init__(self, var=0.95, max_k=60, blocks=5, q=0.995, smooth=3, clip=8.0, fuse="max", groups=None,
                 vib_floor=MPU_FLOOR, min_snr_db=6.0):
        self.var, self.max_k, self.blocks, self.q, self.smooth, self.clip = var, max_k, blocks, q, smooth, clip
        self.fuse_how, self.groups_in, self.vib_floor, self.min_snr_db = fuse, groups, vib_floor, min_snr_db
        self.recent = []

    def _groups(self, n_in):
        if self.groups_in is not None:
            return [np.asarray(g, int) for g in self.groups_in]
        return [np.arange(N_VIB), np.arange(N_VIB, n_in)]

    def _fuse(self, G):
        return G.max(1) if self.fuse_how == "max" else G.mean(1)

    def _fit_core(self, R):
        med = np.median(R, 0)
        q1, q3 = np.percentile(R, [25, 75], 0)
        sd = (q3 - q1) / 1.349
        sd = np.where(sd > 1e-9, sd, R.std(0))
        sd = np.where(sd > 1e-9, sd, 1.0)
        Z = np.clip((R - med) / sd, -self.clip, self.clip)
        return {"med": med, "sd": sd, "pca": [_pca_fit(Z[:, g], self.var, self.max_k) for g in self.groups]}

    def _gs(self, core, R):
        Z = np.clip((R - core["med"]) / core["sd"], -self.clip, self.clip)
        return np.stack([_pca_score(m, Z[:, g]) for m, g in zip(core["pca"], self.groups)], 1)

    def learn(self, rows, rec=None):
        R = np.asarray(rows, float)
        rec = np.zeros(len(R), int) if rec is None else np.asarray(rec)
        self.n_in = R.shape[1]
        groups = self._groups(self.n_in)
        if self.groups_in is None and self.vib_floor is not None and not vib_active(np.median(R[:, :N_VIB], 0),
                                                                                    self.vib_floor, self.min_snr_db):
            groups = groups[1:]
        self.groups = groups
        b = _blocks_within(rec, self.blocks)
        G = np.zeros((len(R), len(groups)))
        self.cores = []
        for k in range(self.blocks):
            core = self._fit_core(R[b != k])
            self.cores.append(core)
            G[b == k] = self._gs(core, R[b == k])
        self.gmed = np.maximum(np.median(G, 0), 1e-12)
        self.oof = self._fuse(G / self.gmed)
        stat = np.concatenate([running_median(self.oof[rec == r], self.smooth) for r in np.unique(rec)])
        self.line = float(np.quantile(stat, self.q))
        self.reset()
        return self

    def group_matrix(self, rows):
        R = np.atleast_2d(np.asarray(rows, float))
        return sum(self._gs(c, R) for c in self.cores) / len(self.cores) / self.gmed

    def scores(self, rows):
        return self._fuse(self.group_matrix(rows))

    def reset(self):
        self.recent = []

    def judge(self, row, margin=1.0):
        g = self.group_matrix(row)
        s = float(self._fuse(g)[0])
        self.recent = (self.recent + [s])[-self.smooth:]
        level = float(np.median(self.recent)) / self.line
        return {"alarm": bool(level > margin), "level": level, "score": s, "groups": [float(x) for x in g[0]]}

    # --- saving: one .npz (float32 matrices + a JSON header)
    def save(self, path):
        meta = {"format": FORMAT, "n_in": int(self.n_in), "line": self.line, "gmed": self.gmed.tolist(),
                "settings": {"var": self.var, "max_k": self.max_k, "blocks": self.blocks, "q": self.q,
                             "smooth": self.smooth, "clip": self.clip, "fuse": self.fuse_how},
                "groups": [g.tolist() for g in self.groups],
                "pca_ref": [[m["ref"] for m in c["pca"]] for c in self.cores]}
        arr = {"meta": np.frombuffer(json.dumps(meta).encode(), np.uint8)}
        for i, c in enumerate(self.cores):
            arr[f"c{i}_med"], arr[f"c{i}_sd"] = c["med"].astype(np.float32), c["sd"].astype(np.float32)
            for j, m in enumerate(c["pca"]):
                for k in ("mu", "V", "hs"):
                    arr[f"c{i}_g{j}_{k}"] = m[k].astype(np.float32)
        with open(path, "wb") as f:
            np.savez_compressed(f, **arr)

    @classmethod
    def load(cls, path):
        z = np.load(path)
        meta = json.loads(bytes(z["meta"]).decode())
        if meta["format"] != FORMAT:
            raise ValueError(meta["format"])
        m = cls(**meta["settings"])
        m.n_in, m.line, m.gmed = meta["n_in"], meta["line"], np.array(meta["gmed"])
        m.groups = [np.array(g, int) for g in meta["groups"]]
        m.cores = []
        for i in range(m.blocks):
            m.cores.append({"med": z[f"c{i}_med"].astype(float), "sd": z[f"c{i}_sd"].astype(float),
                            "pca": [{"mu": z[f"c{i}_g{j}_mu"].astype(float), "V": z[f"c{i}_g{j}_V"].astype(float),
                                     "hs": z[f"c{i}_g{j}_hs"].astype(float), "ref": meta["pca_ref"][i][j]}
                                    for j in range(len(m.groups))]})
        m.reset()
        return m
