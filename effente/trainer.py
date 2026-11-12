"""Train a machine's model from its recording. NumPy only, so the device can train itself.

Used both on the UNO Q (the button's "train" step) and on a laptop (training/model/train.py),
so a model is trained the same way wherever it is trained:

  1. Rows: one feature row a second (learn mode keeps these), running rows only, summarised
     into per-period medians. The period follows the recording's length, so a 20-minute demo
     recording still has enough rows and a week has per-minute ones (choose_period).
  2. Split by time: the last part (calib_share) is held out for the threshold.
  3. Prepare (effente.model_input.Prep) and learn the normal model: a small denoising
     autoencoder, trained with Adam and early stopping.
  4. Whole-number weights (int8, a scale per neuron), and a check that the device runtime
     (effente.model_runtime) gives the same scores.
  5. Threshold: one false alarm a week of the period's rows, from a gamma fit to the held-out
     scores (method of moments, Wilson-Hilferty quantile), never below the highest held-out score.
  6. Fault-type classifier from fault patterns injected into this machine's own normal
     deviations (effente.fault_types signatures); no real labelled faults yet.
Starting values throughout (unverified on real machines).
"""

from __future__ import annotations

import math
import time
from datetime import datetime, timezone

import numpy as np

from effente import fault_types as FT
from effente import features as F
from effente import model_runtime as RT
from effente.model_input import Prep

PERIODS = (1, 2, 5, 10, 15, 30, 60)
ROWS_WANTED = 1500
MIN_ROWS = 300


def choose_period(duration_s: float) -> int:
    """The longest summary period that still gives about ROWS_WANTED rows (1 s for short recordings)."""
    ok = [p for p in PERIODS if duration_s / p >= ROWS_WANTED]
    return max(ok) if ok else 1


def summarise(t, X, period: int):
    """Per-period medians of whole-second rows: (period start times, rows)."""
    t = np.asarray(t, float)
    X = np.asarray(X, float)
    if len(t) > 1 and np.median(np.diff(np.sort(t))) < 0.9:  # several rows a second: one each second
        whole = np.abs(t - np.round(t)) < 1e-6
        t, X = t[whole], X[whole]
    if period <= 1:
        return np.floor(t + 1e-6), X
    k = np.floor(t / period).astype(np.int64)
    order = np.argsort(k, kind="stable")
    k, X = k[order], X[order]
    cut = np.r_[0, np.nonzero(np.diff(k))[0] + 1, len(k)]
    with np.errstate(all="ignore"):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            rows = np.array([np.nanmedian(X[a:b], 0) for a, b in zip(cut[:-1], cut[1:])])
    return k[cut[:-1]].astype(float) * period, rows


def to_current(X, names) -> np.ndarray:
    """Rows recorded with an older feature list, mapped by name onto the current one (NaN where missing)."""
    names = [str(n) for n in names]
    X = np.asarray(X, float)
    if names == list(F.FEATURE_NAMES):
        return X
    out = np.full((len(X), F.N_FEATURES), np.nan)
    for j, n in enumerate(names):
        if n in F.INDEX:
            out[:, F.INDEX[n]] = X[:, j]
    return out


def running_rows(X, names):
    if "trend_running" in names:
        r = X[:, names.index("trend_running")]
        return ~(r < 0.5)  # NaN (not known yet) counts as running
    return np.ones(len(X), bool)


# ----------------------------------------------------------------- a small network, trained with Adam

def _init(sizes, rng):
    return [(rng.normal(0, math.sqrt(2.0 / a), (a, b)).astype(np.float32), np.zeros(b, np.float32))
            for a, b in zip(sizes[:-1], sizes[1:])]


def _forward(layers, acts, X):
    hs = [X]
    h = X
    for (W, b), act in zip(layers, acts):
        z = h @ W + b
        h = np.maximum(z, 0) if act == "relu" else (1 / (1 + np.exp(-np.clip(z, -30, 30))) if act == "sigmoid" else z)
        hs.append(h)
    return hs


def fit_mlp(X, Y, sizes, acts, loss="mse", noise=0.0, epochs=300, batch=128, lr=2e-3, l2=1e-4, val_share=0.15,
            patience=15, seed=0, progress=None, time_limit_s=None):
    """Train a small fully connected network. Returns (layers as (W, b) pairs, best validation loss)."""
    rng = np.random.default_rng(seed)
    X, Y = np.asarray(X, np.float32), np.asarray(Y, np.float32)
    idx = rng.permutation(len(X))
    nv = max(1, int(len(X) * val_share))
    vi, ti = idx[:nv], idx[nv:]
    layers = _init(sizes, rng)
    m = [(np.zeros_like(W), np.zeros_like(b)) for W, b in layers]
    v = [(np.zeros_like(W), np.zeros_like(b)) for W, b in layers]
    b1, b2, eps, step = 0.9, 0.999, 1e-8, 0
    best, best_layers, bad = float("inf"), [(W.copy(), b.copy()) for W, b in layers], 0
    started = time.perf_counter()

    def loss_of(out, y):
        if loss == "bce":
            o = np.clip(out, 1e-6, 1 - 1e-6)
            return float(-(y * np.log(o) + (1 - y) * np.log(1 - o)).mean())
        return float(((out - y) ** 2).mean())

    for epoch in range(epochs):
        rng.shuffle(ti)
        for s in range(0, len(ti), batch):
            bi = ti[s:s + batch]
            xb, yb = X[bi], Y[bi]
            if noise:
                xb = xb + rng.normal(0, noise, xb.shape).astype(np.float32)
            hs = _forward(layers, acts, xb)
            out = hs[-1]
            # d loss / d pre-activation of the output (mse with a linear output, bce with a sigmoid)
            g = (2.0 / out.size) * (out - yb) if loss == "mse" else (out - yb) / out.size
            step += 1
            for li in range(len(layers) - 1, -1, -1):
                W, b = layers[li]
                gW = hs[li].T @ g + l2 * W
                gb = g.sum(0)
                if li:
                    g = (g @ W.T) * (hs[li] > 0)  # hidden layers are relu
                for k, (p, gp) in enumerate(((W, gW), (b, gb))):
                    mk, vk = m[li][k], v[li][k]
                    mk *= b1
                    mk += (1 - b1) * gp
                    vk *= b2
                    vk += (1 - b2) * gp * gp
                    p -= lr * (mk / (1 - b1 ** step)) / (np.sqrt(vk / (1 - b2 ** step)) + eps)
        val = loss_of(_forward(layers, acts, X[vi])[-1], Y[vi])
        if progress:
            progress(epoch, val)
        if val < best - 1e-6:
            best, best_layers, bad = val, [(W.copy(), b.copy()) for W, b in layers], 0
        else:
            bad += 1
            if bad >= patience:
                break
        if time_limit_s and time.perf_counter() - started > time_limit_s:
            break
    return best_layers, best


# ----------------------------------------------------------------- threshold without scipy

def _norm_quantile(p: float) -> float:
    """Inverse of the standard normal distribution (Acklam's rational approximation)."""
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02, 1.383577518672690e+02,
         -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02, 6.680131188771972e+01,
         -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00, -2.549732539343734e+00,
         4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00, 3.754408661907416e+00]
    if p < 0.02425:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > 1 - 0.02425:
        return -_norm_quantile(1 - p)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
           (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


def gamma_threshold(scores, rows_per_week: float, alarms_per_week: float = 1.0) -> float:
    s = np.asarray(scores, float)
    s = s[np.isfinite(s)]
    lo = float(s.min())
    shift = lo - 1e-9 * (abs(lo) + 1) if lo <= 0 else 0.0
    x = s - shift
    mean, var = float(x.mean()), float(x.var()) + 1e-24
    k, theta = mean * mean / var, var / mean  # method of moments
    q = 1.0 - alarms_per_week / max(rows_per_week, 1.0)
    z = _norm_quantile(q)
    wh = k * theta * max(1 - 1 / (9 * k) + z * math.sqrt(1 / (9 * k)), 0.0) ** 3  # Wilson-Hilferty
    return float(max(wh + shift, s.max()))


# ----------------------------------------------------------------- injected faults for the classifier

def inject(base: np.ndarray, cols: list[str], rng, n_per_type: int = 400):
    """Normal deviations with one fault type's signature added at a random strength (sometimes two
    types at once), plus plain normal ones labelled with no type."""
    idx = {c: i for i, c in enumerate(cols)}
    usable = []
    for ft in FT.TYPES:
        groups = [([idx[n] for n in m.names if n in idx], m) for m in ft.members]
        groups = [(g, m) for g, m in groups if g]
        if groups:
            usable.append((ft.name, groups))
    names = [u[0] for u in usable]
    X, Y = [], []

    def add_type(row, k):
        _, groups = usable[k]
        s = rng.uniform(4, 15)
        chosen = [gm for gm in groups if rng.random() < 0.8] or [groups[int(rng.integers(len(groups)))]]
        top = max(mm.weight for _, mm in groups)
        for g, m in chosen:
            picks = rng.choice(g, size=min(len(g), int(rng.integers(1, 4))), replace=False)
            sign = m.direction if m.direction else rng.choice([-1, 1])
            row[picks] += sign * s * rng.uniform(0.6, 1.2) * m.weight / top

    for k in range(len(usable)):
        for _ in range(n_per_type):
            row = base[rng.integers(len(base))].copy()
            y = np.zeros(len(usable))
            add_type(row, k)
            y[k] = 1
            if rng.random() < 0.2:
                j = int(rng.integers(len(usable)))
                add_type(row, j)
                y[j] = 1
            X.append(row)
            Y.append(y)
    for _ in range(n_per_type * 3):
        X.append(base[rng.integers(len(base))].copy())
        Y.append(np.zeros(len(usable)))
    return np.clip(np.array(X), -8, 8), np.array(Y), names


# ----------------------------------------------------------------- the whole training

def _causal_median(x, n):
    return np.array([np.median(x[max(0, i - n + 1):i + 1]) for i in range(len(x))])


def train_model(t, X, names, period: int | None = None, smooth: int = 5, calib_share: float = 0.15,
                seed: int = 0, source: str = "", progress=None, time_limit_s: float | None = None):
    """Train from one-a-second feature rows. Returns (model file dict, report). Raises ValueError if
    the recording is too short."""
    X = to_current(X, names)  # the model is always built on the feature list this code computes
    names = list(F.FEATURE_NAMES)
    t = np.asarray(t, float)
    run = running_rows(X, names)
    t, X = t[run], X[run]
    if len(t) < 2:
        raise ValueError("no running data recorded")
    if period is None:
        period = choose_period(float(t.max() - t.min()))
    t, X = summarise(t, X, period)
    if len(t) < MIN_ROWS:
        raise ValueError(f"too little recorded: {len(t)} rows of {period} s; record at least "
                         f"{MIN_ROWS * period // 60} minutes of the machine running")
    cut = t[0] + (t[-1] - t[0]) * (1 - calib_share)
    fit, calib = t < cut, t >= cut
    say = progress or (lambda stage, share: None)
    say("preparing", 0.05)
    prep = Prep.fit(X[fit], names, t[fit], running_only=False)
    Z = prep.transform(X)
    d_in = Z.shape[1]
    mid = int(np.clip(d_in // 6, 3, 16))
    hid = int(np.clip(4 * mid, 8, 64))
    t0 = time.perf_counter()
    say("learning normal", 0.1)
    ae, _ = fit_mlp(Z[fit], Z[fit], [d_in, hid, mid, hid, d_in], ["relu", "relu", "relu", "linear"], noise=0.1,
                    seed=seed, progress=lambda e, v: say("learning normal", 0.1 + 0.6 * min(e, 100) / 100),
                    time_limit_s=time_limit_s)
    fit_s = time.perf_counter() - t0
    acts = ["relu", "relu", "relu", "linear"]
    d = {"format": RT.FORMAT, "features_version": RT._version(),
         "created": datetime.now(timezone.utc).isoformat(timespec="seconds"), "trained_on": source,
         "prep": prep.to_dict(), "period_s": int(period), "smooth_minutes": smooth, "threshold": 1.0,
         "autoencoder": [RT.quantize(W, b, a) for (W, b), a in zip(ae, acts)]}
    model = RT.Model(d)
    h = Z.astype(np.float32)
    for (W, b), a in zip(ae, acts):
        h = h @ W + b
        h = np.maximum(h, 0) if a == "relu" else h
    s_float = ((Z - h) ** 2).mean(1)
    s_int, r_int, Z_int = model.score(X)
    scale = np.std(r_int[fit], 0)
    d["residual_scale"] = [float(v) for v in np.where(scale > 1e-4, scale, 1e-4)]
    model = RT.Model(d)
    d["threshold"] = gamma_threshold(_causal_median(s_int[calib], smooth), 7 * 86400 / period)
    say("learning fault types", 0.75)
    rng = np.random.default_rng(seed)
    dev_cols = [c for c in prep.names_out if c in prep.columns]
    base = model.deviation_vector(r_int[fit], Z_int[fit])
    Xc, Yc, classes = inject(base, dev_cols, rng)
    clf, _ = fit_mlp(Xc, Yc, [Xc.shape[1], 32, Yc.shape[1]], ["relu", "sigmoid"], loss="bce", seed=seed,
                     epochs=150, time_limit_s=time_limit_s)
    d["fault_classifier"] = {"classes": classes, "inputs": dev_cols,
                             "layers": [RT.quantize(W, b, a) for (W, b), a in zip(clf, ["relu", "sigmoid"])],
                             "note": "learned from injected signature patterns only; no real labelled faults yet"}
    p = 1 / (1 + np.exp(-np.clip(np.maximum(Xc @ clf[0][0] + clf[0][1], 0) @ clf[1][0] + clf[1][1], -30, 30)))
    acc = float(((p > 0.5) == (Yc > 0.5)).all(1).mean())
    d["fault_types"] = [{"name": ft.name, "text": ft.text, "sensing": ft.sensing} for ft in FT.TYPES]
    d["feature_table"] = [{"name": c, "text": F.FEATURE_TEXT.get(c, ""), "unit": F.FEATURE_KIND.get(c, "")}
                          for c in prep.columns]
    report = {"period_s": int(period), "rows_learned": int(fit.sum()), "rows_calibration": int(calib.sum()),
              "inputs": prep.n_out, "features_kept": len(prep.columns), "fit_seconds": round(fit_s, 1),
              "int8_vs_float_score_correlation": round(float(np.corrcoef(s_float, s_int)[0, 1]), 5),
              "threshold": round(d["threshold"], 4), "fault_classes": len(classes),
              "classifier_exact_match": round(acc, 3)}
    d["training_report"] = report
    say("done", 1.0)
    return d, report
