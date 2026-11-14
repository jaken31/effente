"""A NumPy-only reference of the recommended normal model, written so it can be moved onto the board as is.

GroupGauss: one Gaussian (Ledoit-Wolf shrunk covariance) per sensor group, each group's score put in units of its
own out-of-sample learning level, fused by the maximum (so one group that changes is not diluted by the others).
Line: the 99th percentile of out-of-sample learning scores after the median-of-3-seconds rule, from 5 contiguous
time blocks of the learning recording (each block scored by a model learned on the other four).
Alarm: the median of the last 3 one-second scores over line x margin (margin 1 = default sensitivity).

Inputs are already prepared rows (the device's effente.model_input.Prep output, or robust-z generic features).
Nothing here needs scipy or scikit-learn. Checked against sklearn.covariance.LedoitWolf by check() below.

    python -m training.record.sscc_eval.board_model   # self-check + timings
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
