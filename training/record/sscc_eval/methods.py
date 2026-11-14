"""Normal models for the SSCC benchmark: each learns from normal one-second rows only and scores rows
(higher = less normal). All work on an already prepared matrix Z (rows x inputs).

Every class has fit(Z) -> self and score(Z) -> (rows,) array. `groups` (lists of column indices) let a model be
scored per sensor group and fused.
"""

from __future__ import annotations

import numpy as np


class PCA:
    """PCA normal model, scored like the device (effente.model_runtime.Model.score): the mean squared residual over
    its in-sample level, plus (t2=True) Hotelling's T2 as the mean of (pattern / its spread)^2. With groups, the
    residual is taken per group, each over its own in-sample level, then combined by mean or max ("balanced")."""

    def __init__(self, var=0.95, max_k=40, t2=True, groups=None, combine="mean"):
        self.var, self.max_k, self.t2, self.groups, self.combine = var, max_k, t2, groups, combine

    def fit(self, Z):
        Z = np.asarray(Z, float)
        self.mu = Z.mean(0)
        _, sv, vt = np.linalg.svd(Z - self.mu, full_matrices=False)
        share = np.cumsum(sv ** 2) / max(float((sv ** 2).sum()), 1e-12)
        k = int(min(np.searchsorted(share, self.var) + 1, self.max_k, len(sv)))
        self.V = vt[:k].T
        h = (Z - self.mu) @ self.V
        self.hs = np.maximum(h.std(0), 1e-6)
        r = (Z - self.mu) - h @ self.V.T
        if self.groups:
            self.ref = [max(float((r[:, g] ** 2).mean()), 1e-9) for g in self.groups]
        else:
            self.ref = max(float((r ** 2).mean()), 1e-9)
        return self

    def score(self, Z):
        Z = np.asarray(Z, float) - self.mu
        h = Z @ self.V
        r = Z - h @ self.V.T
        if self.groups:
            parts = np.stack([(r[:, g] ** 2).mean(1) / ref for g, ref in zip(self.groups, self.ref)], 1)
            s = parts.max(1) if self.combine == "max" else parts.mean(1)
        else:
            s = (r ** 2).mean(1) / self.ref
        if self.t2:
            s = s + ((h / self.hs) ** 2).mean(1)
        return s


class Gauss:
    """Gaussian (Mahalanobis) with Ledoit-Wolf shrinkage; score = d^2 / inputs."""

    def fit(self, Z):
        from sklearn.covariance import LedoitWolf
        Z = np.asarray(Z, float)
        self.lw = LedoitWolf().fit(Z)
        self.d = Z.shape[1]
        return self

    def score(self, Z):
        return self.lw.mahalanobis(np.asarray(Z, float)) / self.d


class KNN:
    """Mean distance to the k nearest learning rows (Euclidean, or cosine)."""

    def __init__(self, k=1, metric="euclidean"):
        self.k, self.metric = k, metric

    def fit(self, Z):
        from sklearn.neighbors import NearestNeighbors
        self.nn = NearestNeighbors(n_neighbors=self.k, metric=self.metric).fit(np.asarray(Z, float))
        return self

    def score(self, Z):
        d, _ = self.nn.kneighbors(np.asarray(Z, float))
        return d.mean(1)


class IForest:
    def __init__(self, n=200, seed=0):
        self.n, self.seed = n, seed

    def fit(self, Z):
        from sklearn.ensemble import IsolationForest
        self.m = IsolationForest(n_estimators=self.n, random_state=self.seed).fit(np.asarray(Z, float))
        return self

    def score(self, Z):
        return -self.m.score_samples(np.asarray(Z, float))


class AE:
    """A small autoencoder (sklearn MLPRegressor, tanh, bottleneck), scored by mean squared error."""

    def __init__(self, hidden=(32, 8, 32), seed=0):
        self.hidden, self.seed = hidden, seed

    def fit(self, Z):
        import warnings
        from sklearn.neural_network import MLPRegressor
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self.m = MLPRegressor(hidden_layer_sizes=self.hidden, activation="tanh", alpha=1e-3, max_iter=400,
                                  early_stopping=True, random_state=self.seed).fit(Z, Z)
        return self

    def score(self, Z):
        return ((self.m.predict(Z) - Z) ** 2).mean(1)


class MaxZ:
    """The largest |robust z| over the inputs (optionally a subset): "any reading far outside its normal"."""

    def __init__(self, cols=None):
        self.cols = cols

    def fit(self, Z):
        Z = np.asarray(Z, float)[:, self.cols] if self.cols is not None else np.asarray(Z, float)
        self.med = np.median(Z, 0)
        q1, q3 = np.percentile(Z, [25, 75], 0)
        sd = (q3 - q1) / 1.349
        self.sd = np.where(sd > 1e-9, sd, np.maximum(Z.std(0), 1e-9))
        return self

    def score(self, Z):
        Z = np.asarray(Z, float)[:, self.cols] if self.cols is not None else np.asarray(Z, float)
        return np.abs((Z - self.med) / self.sd).max(1)


class Fused:
    """One model per sensor group, each score put on the same footing by its own out-of-fold learning scores
    (2 folds in time order), then fused:
      "max"   the largest score / its learning median
      "maxq"  the largest score / its learning 99th percentile (each group at its own line, then OR)
      "mean"  the mean of score / its learning median
      "z"     the mean of (score - learning mean) / learning spread
      "zmax"  the largest of those z
      "ecdf"  the mean of the score's quantile among the learning scores (the paper's benchmark fusion)."""

    def __init__(self, make, groups, combine="max"):
        self.make, self.groups, self.combine = make, groups, combine

    def fit(self, Z):
        Z = np.asarray(Z, float)
        n = len(Z)
        half = np.arange(n) < n // 2
        self.models, self.oof = [], []
        for g in self.groups:
            oof = np.empty(n)
            oof[~half] = self.make().fit(Z[half][:, g]).score(Z[~half][:, g])
            oof[half] = self.make().fit(Z[~half][:, g]).score(Z[half][:, g])
            self.oof.append(np.sort(oof))
            self.models.append(self.make().fit(Z[:, g]))
        return self

    def parts(self, Z):
        Z = np.asarray(Z, float)
        return [m.score(Z[:, g]) for m, g in zip(self.models, self.groups)]

    def score(self, Z):
        P = self.parts(Z)
        if self.combine == "maxq":  # each group over its own 99th percentile of out-of-fold learning scores
            R = np.stack([p / max(o[int(0.99 * (len(o) - 1))], 1e-12) for p, o in zip(P, self.oof)], 1)
            return R.max(1)
        if self.combine in ("max", "mean"):
            R = np.stack([p / max(np.median(o), 1e-12) for p, o in zip(P, self.oof)], 1)
            return R.max(1) if self.combine == "max" else R.mean(1)
        if self.combine in ("z", "zmax"):
            R = np.stack([(p - o.mean()) / max(o.std(), 1e-12) for p, o in zip(P, self.oof)], 1)
            return R.max(1) if self.combine == "zmax" else R.mean(1)
        if self.combine == "ecdf":
            R = np.stack([(np.searchsorted(o, p, side="right") + 0.5) / (len(o) + 1) for p, o in zip(P, self.oof)], 1)
            return R.mean(1) + 1e-3 * np.stack([p / max(np.median(o), 1e-12) for p, o in zip(P, self.oof)], 1).mean(1)
        raise ValueError(self.combine)
