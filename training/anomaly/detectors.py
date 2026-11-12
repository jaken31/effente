"""Candidate "is this machine still normal?" models, all with one interface, for the bench.

Each learns from prepared rows (effente.model_input.Prep) of one machine's normal running:

    det.fit(Z)                 # Z: (rows, inputs), no NaN, within +-8
    score, resid = det.score(Z)
    # score: (rows,) higher = less normal. resid: (rows, inputs) how far each input is from what
    # the model expected given the others (signed), or None if the model cannot say

`resid` is what the fault-type classifier works from. Models that only give a score fall back
to the plain distance from the training median (Z itself), which ignores operating context.
"""

from __future__ import annotations

import numpy as np
from sklearn.covariance import LedoitWolf
from sklearn.decomposition import PCA
from sklearn.ensemble import IsolationForest
from sklearn.mixture import GaussianMixture
from sklearn.neighbors import NearestNeighbors
from sklearn.neural_network import MLPRegressor


class MaxZ:
    """Simplest rule: the reading furthest from its own normal. No relationships between readings."""
    name = "max |z| (no model)"

    def fit(self, Z):
        return self

    def score(self, Z):
        return np.abs(Z).max(1), None


class PCARecon:
    """Normal = a few straight-line patterns; score = what those patterns cannot explain."""
    name = "PCA"

    def __init__(self, var: float = 0.95, max_k: int = 40):
        self.var, self.max_k = var, max_k

    def fit(self, Z):
        full = PCA().fit(Z)
        k = int(np.searchsorted(np.cumsum(full.explained_variance_ratio_), self.var) + 1)
        self.pca = PCA(min(k, self.max_k, Z.shape[1])).fit(Z)
        return self

    def score(self, Z):
        rec = self.pca.inverse_transform(self.pca.transform(Z))
        r = Z - rec
        return (r ** 2).mean(1), r


class Gaussian:
    """Mahalanobis distance from the normal average, with a shrunk covariance (Ledoit-Wolf)."""
    name = "Gaussian (Mahalanobis)"

    def fit(self, Z):
        self.lw = LedoitWolf().fit(Z)
        return self

    def score(self, Z):
        return self.lw.mahalanobis(Z), None


class Mixture:
    """Several normal states (shifts, load levels), each a Gaussian."""
    name = "Gaussian mixture"

    def __init__(self, k: int = 4, dims: int = 30):
        self.k, self.dims = k, dims

    def fit(self, Z):
        self.pca = PCA(min(self.dims, Z.shape[1])).fit(Z)
        self.gm = GaussianMixture(self.k, covariance_type="full", reg_covar=1e-3, random_state=0).fit(
            self.pca.transform(Z))
        return self

    def score(self, Z):
        return -self.gm.score_samples(self.pca.transform(Z)), None


class Autoencoder:
    """A small network that learns to reproduce normal rows; score = how badly it reproduces these."""
    name = "autoencoder"

    def __init__(self, hidden=None, noise: float = 0.1, seed: int = 0):
        self.hidden, self.noise, self.seed = hidden, noise, seed

    def fit(self, Z):
        if self.hidden is None:  # the middle must be much narrower than the input, or it just copies it
            mid = int(np.clip(Z.shape[1] // 6, 3, 16))
            self.hidden = (int(np.clip(4 * mid, 8, 64)), mid, int(np.clip(4 * mid, 8, 64)))
        rng = np.random.default_rng(self.seed)
        Xn = np.vstack([Z + rng.normal(0, self.noise, Z.shape) for _ in range(3)])  # denoising: harder to copy faults
        Y = np.vstack([Z] * 3)
        self.net = MLPRegressor(hidden_layer_sizes=self.hidden, activation="relu", alpha=1e-4, learning_rate_init=2e-3, max_iter=300,
                                early_stopping=True, validation_fraction=0.15, n_iter_no_change=15,
                                random_state=self.seed).fit(Xn, Y)
        return self

    def score(self, Z):
        r = Z - self.net.predict(Z)
        return (r ** 2).mean(1), r


class Similarity:
    """Autoassociative kernel regression (the idea behind MSET and similarity-based monitoring):
    the expected reading is a blend of the stored normal minutes most like this one."""
    name = "similarity (AAKR/MSET-style)"

    def __init__(self, memory: int = 2000, k: int = 20, seed: int = 0):
        self.memory, self.k, self.seed = memory, k, seed

    def fit(self, Z):
        rng = np.random.default_rng(self.seed)
        idx = rng.choice(len(Z), min(self.memory, len(Z)), replace=False)
        self.M = Z[idx]
        self.nn = NearestNeighbors(n_neighbors=min(self.k + 1, len(self.M))).fit(self.M)
        d, _ = self.nn.kneighbors(self.M)
        self.h = float(np.median(d[:, 1:].mean(1))) + 1e-9  # bandwidth: typical distance between normal minutes
        return self

    def score(self, Z):
        d, i = self.nn.kneighbors(Z, n_neighbors=min(self.k, len(self.M)))
        w = np.exp(-(d / self.h) ** 2 / 2) + 1e-12
        est = (self.M[i] * w[..., None]).sum(1) / w.sum(1, keepdims=True)
        r = Z - est
        return (r ** 2).mean(1), r


class Forest:
    name = "isolation forest"

    def fit(self, Z):
        self.f = IsolationForest(n_estimators=200, random_state=0).fit(Z)
        return self

    def score(self, Z):
        return -self.f.score_samples(Z), None


class KNN:
    """Distance to the nearest normal minutes."""
    name = "nearest neighbours"

    def __init__(self, k: int = 5, memory: int = 5000, seed: int = 0):
        self.k, self.memory, self.seed = k, memory, seed

    def fit(self, Z):
        rng = np.random.default_rng(self.seed)
        idx = rng.choice(len(Z), min(self.memory, len(Z)), replace=False)
        self.nn = NearestNeighbors(n_neighbors=self.k).fit(Z[idx])
        return self

    def score(self, Z):
        d, _ = self.nn.kneighbors(Z)
        return d.mean(1), None


ALL = [MaxZ, PCARecon, Gaussian, Mixture, Autoencoder, Similarity, Forest, KNN]


def threshold(calib_scores, rows_per_week: float, alarms_per_week: float = 1.0) -> float:
    """Alarm threshold for a target false-alarm rate, from scores on held-out normal data.

    The highest calibration score is too low a bar when watching far longer than calibrating
    (some normal rows beat it by chance). Instead fit a gamma distribution to the calibration
    scores (as the DCASE machine-sound baselines do) and take the quantile that one normal row in
    `rows_per_week / alarms_per_week` exceeds. Never below the highest calibration score."""
    from scipy import stats
    s = np.asarray(calib_scores, float)
    s = s[np.isfinite(s)]
    lo = float(s.min())
    shift = lo - 1e-9 * (abs(lo) + 1) if lo <= 0 else 0.0  # gamma needs positive values
    a, loc, scale = stats.gamma.fit(s - shift, floc=0)
    q = 1.0 - alarms_per_week / max(rows_per_week, 1.0)
    return float(max(stats.gamma.ppf(q, a, loc=loc, scale=scale) + shift, s.max()))

