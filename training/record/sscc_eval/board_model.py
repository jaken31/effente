"""A NumPy-only reference of the recommended normal model, written so it can be moved onto the board as is.

GroupGauss: one Gaussian (Ledoit-Wolf shrunk covariance) per sensor group, each group's score put in units of its
own out-of-sample learning level, fused by the maximum (so one group that changes is not diluted by the others).
Line: the 99th percentile of out-of-sample learning scores after the median-of-3-seconds rule, from 5 contiguous
time blocks of the learning recording (each block scored by a model learned on the other four).
Alarm: the median of the last 3 one-second scores over line x margin (margin 1 = default sensitivity).

Inputs are already prepared rows (the device's effente.model_input.Prep output, or robust-z generic features).
Nothing here needs scipy or scikit-learn. Checked against sklearn.covariance.LedoitWolf by check() below.

Also the recommended detector itself (Final: band features, GroupPCA, learned line, median-of-3 and spike rules),
NumPy only, checked against the benchmark's scores by check_final().

    python -m training.record.sscc_eval.board_model   # self-checks + timings
"""

from __future__ import annotations

import time

import numpy as np


def ledoit_wolf(X: np.ndarray):
    """(mean, precision) of rows X with the Ledoit-Wolf shrinkage of sklearn (assume_centered=False)."""
    X = np.asarray(X, float)
    mu = X.mean(0)
    Xc = X - mu
    n, p = Xc.shape
    S = Xc.T @ Xc / n
    m = np.trace(S) / p
    X2 = Xc ** 2
    beta_ = float((X2.T @ X2).sum()) / n - float((S ** 2).sum())
    d2 = float(((S - m * np.eye(p)) ** 2).sum())
    beta = min(beta_ / n, d2) if d2 > 0 else 0.0
    shrink = beta / d2 if d2 > 0 else 0.0
    C = (1 - shrink) * S + shrink * m * np.eye(p)
    return mu, np.linalg.inv(C)


class Gauss:
    def fit(self, X):
        self.mu, self.P = ledoit_wolf(X)
        self.p = X.shape[1]
        return self

    def score(self, X):
        D = np.asarray(X, float) - self.mu
        return np.einsum("ij,jk,ik->i", D, self.P, D) / self.p


class GroupGauss:
    """Per-group Gaussian, fused by the max of score / its out-of-sample learning median."""

    def __init__(self, groups):
        self.groups = [np.asarray(g) for g in groups]

    def fit(self, X):
        X = np.asarray(X, float)
        n = len(X)
        half = np.arange(n) < n // 2
        self.models, self.ref = [], []
        for g in self.groups:
            oof = np.r_[Gauss().fit(X[~half][:, g]).score(X[half][:, g]),
                        Gauss().fit(X[half][:, g]).score(X[~half][:, g])]
            self.ref.append(max(float(np.median(oof)), 1e-12))
            self.models.append(Gauss().fit(X[:, g]))
        return self

    def score(self, X):
        X = np.asarray(X, float)
        return np.max([m.score(X[:, g]) / r for m, g, r in zip(self.models, self.groups, self.ref)], axis=0)


def median3(s):
    s = np.asarray(s, float)
    return np.array([np.median(s[max(0, i - 2):i + 1]) for i in range(len(s))])


def learn(X, groups, blocks: int = 5, q: float = 0.99):
    """X: learning rows in time order (one a second). Returns (model, line)."""
    n = len(X)
    b = np.arange(n) * blocks // n
    cal = []
    for k in range(blocks):
        m = GroupGauss(groups).fit(X[b != k])
        cal.append(median3(m.score(X[b == k])))
    line = float(np.quantile(np.concatenate(cal), q))
    return GroupGauss(groups).fit(X), line


# ----------------------------------------------------------------- the recommended model (SSCC_RESULTS.md, B1)

VIB_EDGES = np.geomspace(5.0, 500.0, 21)
SND_EDGES = np.geomspace(50.0, 8000.0, 65)


def band_features(x: np.ndarray, rate: float, edges: np.ndarray) -> np.ndarray:
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


def window_row(accel_g: np.ndarray, audio: np.ndarray, accel_rate=1000.0, audio_rate=16000.0) -> np.ndarray:
    """One second: (n, 3) acceleration in g and (m,) sound (any fixed scale) -> 136 inputs (69 vibration, 67 sound)."""
    return np.r_[band_features(accel_g, accel_rate, VIB_EDGES), band_features(np.asarray(audio, float) / 32767.0,
                                                                              audio_rate, SND_EDGES)]


N_VIB = 69  # the first 69 inputs are vibration, the other 67 sound


class GroupPCA:
    """One PCA per group (95 % of variance, at most 40 patterns), scored as the device's PCA (residual over its
    learning mean, plus mean T2); each group over the median of its out-of-fold learning scores; max over groups."""

    def __init__(self, groups, var=0.95, max_k=40):
        self.groups, self.var, self.max_k = [np.asarray(g) for g in groups], var, max_k

    def _fit1(self, Z):
        mu = Z.mean(0)
        _, sv, vt = np.linalg.svd(Z - mu, full_matrices=False)
        share = np.cumsum(sv ** 2) / max(float((sv ** 2).sum()), 1e-12)
        k = int(min(np.searchsorted(share, self.var) + 1, self.max_k, len(sv)))
        V = vt[:k].T
        h = (Z - mu) @ V
        r = (Z - mu) - h @ V.T
        return mu, V, np.maximum(h.std(0), 1e-6), max(float((r ** 2).mean()), 1e-9)

    @staticmethod
    def _score1(m, Z):
        mu, V, hs, ref = m
        D = Z - mu
        h = D @ V
        r = D - h @ V.T
        return (r ** 2).mean(1) / ref + ((h / hs) ** 2).mean(1)

    def fit(self, Z):
        Z = np.asarray(Z, float)
        n = len(Z)
        half = np.arange(n) < n // 2
        self.models, self.med = [], []
        for g in self.groups:
            oof = np.r_[self._score1(self._fit1(Z[half][:, g]), Z[~half][:, g]),
                        self._score1(self._fit1(Z[~half][:, g]), Z[half][:, g])]
            self.med.append(max(float(np.median(oof)), 1e-12))
            self.models.append(self._fit1(Z[:, g]))
        return self

    def score(self, Z):
        Z = np.atleast_2d(np.asarray(Z, float))
        return np.max([self._score1(m, Z[:, g]) / md for m, g, md in zip(self.models, self.groups, self.med)], axis=0)


class Final:
    """The whole recommended detector: robust scaling, GroupPCA, a learned line and the alarm rules.

    learn(rows): rows = one window_row() a second, in time order, normal running only.
    judge(row) -> (alarm, level): call once a second; level is the median of the last 3 scores over the line."""

    def __init__(self, blocks=5, q=0.99, spike_times=1.0):
        self.blocks, self.q, self.spike_times = blocks, q, spike_times

    def _prep_fit(self, R):
        med = np.median(R, 0)
        q1, q3 = np.percentile(R, [25, 75], 0)
        sd = (q3 - q1) / 1.349
        sd = np.where(sd > 1e-9, sd, R.std(0))
        keep = np.nonzero(sd > 1e-9)[0]
        return keep, med[keep], sd[keep]

    @staticmethod
    def _prep(p, R):
        keep, med, sd = p
        return np.clip((np.atleast_2d(R)[:, keep] - med) / sd, -8, 8)

    def _model(self, R):
        p = self._prep_fit(R)
        groups = [np.nonzero(p[0] < N_VIB)[0], np.nonzero(p[0] >= N_VIB)[0]]
        return p, GroupPCA([g for g in groups if len(g)]).fit(self._prep(p, R))

    def learn(self, R):
        R = np.asarray(R, float)
        b = np.arange(len(R)) * self.blocks // len(R)
        cal, single = [], []
        for k in range(self.blocks):
            p, m = self._model(R[b != k])
            s = m.score(self._prep(p, R[b == k]))
            single.append(s)
            cal.append(median3(s))
        self.line = float(np.quantile(np.concatenate(cal), self.q))
        self.spike = float(np.concatenate(single).max()) * self.spike_times
        self.p, self.m = self._model(R)
        self.recent = []
        return self

    def judge(self, row):
        s = float(self.m.score(self._prep(self.p, row))[0])
        self.recent = (self.recent + [s])[-3:]
        level = float(np.median(self.recent)) / self.line
        return (level > 1.0 or s > self.spike), level


def check_final():
    """The NumPy Final against the benchmark's own scores for the recommended configuration (one condition)."""
    from training.record.sscc_eval import common as C
    from training.record.sscc_eval import eval_methods as EM
    data = C.load_data()
    cond = "heavy_vel80_noise"
    learn, hold = C.split_normal(data, cond)
    RL, RH = EM.raw_rows("g1+ga", learn), EM.raw_rows("g1+ga", hold)
    flat = lambda a: a.reshape(-1, a.shape[-1])
    t0 = time.perf_counter()
    fm = Final().learn(flat(RL))
    t_learn = time.perf_counter() - t0
    ours = fm.m.score(fm._prep(fm.p, flat(RH)))
    ref = EM.fit_score("g1+ga", "fused_pca95_t2_max", flat(RL), [flat(RH)])[0]
    saved = np.load(C.A_DIR / "g1-ga__fused_pca95_t2_max.npz")[f"{cond}|hold"].ravel()
    print(f"Final vs benchmark scores ({cond}): max relative difference {float(np.max(np.abs(ours - ref) / ref)):.2e}, "
          f"vs saved results {float(np.max(np.abs(ours - saved) / saved)):.2e}; learn {t_learn:.2f} s for {len(RL) * 5} rows")


def check():
    from sklearn.covariance import LedoitWolf
    rng = np.random.default_rng(0)
    X = rng.normal(size=(300, 40)) @ rng.normal(size=(40, 40))
    mu, P = ledoit_wolf(X)
    lw = LedoitWolf().fit(X)
    Y = rng.normal(size=(50, 40))
    a = Gauss().fit(X).score(Y) * 40
    b = lw.mahalanobis(Y)
    print("numpy Ledoit-Wolf vs sklearn: max relative difference", float(np.max(np.abs(a - b) / b)))
    for n, p in ((1800, 100), (1800, 160), (300, 160)):
        X = rng.normal(size=(n, p))
        groups = [np.arange(p // 2), np.arange(p // 2, p)]
        t0 = time.perf_counter()
        m, line = learn(X, groups)
        t1 = time.perf_counter()
        for _ in range(200):
            m.score(X[:1])
        t2 = time.perf_counter()
        print(f"rows {n} inputs {p}: learn (5 blocks + final) {t1 - t0:.3f} s, judge one row {1000 * (t2 - t1) / 200:.3f} ms")


if __name__ == "__main__":
    check()
    check_final()
