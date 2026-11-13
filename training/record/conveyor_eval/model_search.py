#!/usr/bin/env python3
"""Search for the best "is it still normal?" model on the conveyor evaluation data (generate.py's runs).

Builds on evaluate.py (imported, not copied): the same runs, running minutes, warm-up, wear level, AUC and
sensitivity (effente.safety.margins). Nothing in effente/ is changed.

  cache        each run's minute rows (median and max, effente.model_input.load_minutes) on the current feature
               list with week-long trends blanked (as evaluate.minutes_of), in data/fake/model-search/cache/
  calibration  how to set the alarm line so that its false-alert rate holds when the training changes:
               tail15/30/50 (learn on the first 85/70/50 % of the running training minutes, line from the rest)
               against crossfit (learn on one half, score the other, both ways; line from the pooled
               out-of-sample scores; the final model learns on all of it), each on four training folds
               (both weeks, both weeks swapped, w1 only, w2 only)
  stage1       every approach in evaluate.methods() x three inputs (median; median + minute max of every feature;
               median + minute max of the impulsive features), with the chosen calibration
  stage2       tunes the best three tunable approaches
  stage3       robustness of the top few: other training folds (sensitivity fixed, and re-picked), new seeds
  analysis     the winner run by run, the device's own runtime with residual-only PCA, bootstrap, trade-offs
  combined     the model's alarm OR the early warning (data/fake/early-warning-search, from early_warning_search.py)

The device's two (evaluate.DeviceMethod: effente.trainer.train_model and the int8 runtime) are reproduced in float
(DevPCA, DevAE: the same Prep, PCA/autoencoder, score and line); stage 1 checks them against the real device.

JUDGING (agreed with the main session, early-warning and the checker, 13 Nov):
  candidate  approach x input x setting x smoothing (median of the last 1, 3, 5 or 10 scores since the last
             stop the device sees, trend_running) x sensitivity (0 to 1 in 0.025 steps); the alarm is
             smoothed score / line > effente.safety.margins(s)["model"], only while running (labels.json)
  line       effente.trainer.gamma_threshold for one false alarm a week of running minutes, from the
             calibration minutes' smoothed scores, recomputed for each smoothing; healthy weeks only
  false      alert stretches (a new one after every stop) beginning in the held-out week's or the fault runs'
             pre-onset running minutes after each run's 6 h warm-up, / those running minutes x 10080: per
             running week
  caught     a stretch that starts inside the fault window (onset to the last fault episode's end + 30 min,
             capped at the run's end) and was not on at the previous running minute: a stretch continues
             over a stop, so an alarm on at the evening stop and again at the morning restart is no catch
  early      lateness per fault run in [0, 1]: slow faults (evaluate.SLOW_FAULTS) the wear at the first catch
             as a share of the obvious size (severity x wear share; at most 0.3 for a subtle one), sudden
             faults the minutes to it / the window's minutes; a miss is 1. The mean over all 30
  rank       fewest false alerts (no slack; among candidates that catch something), then fewest missed of
             30, then lowest lateness
  Safety checks are not included (the current sound check fires on every forklift; early-warning reports
  the whole device with them).

    python3 -m training.record.conveyor_eval.model_search cache
    python3 -m training.record.conveyor_eval.model_search calibration --workers 4
    python3 -m training.record.conveyor_eval.model_search stage1 --workers 4
    python3 -m training.record.conveyor_eval.model_search stage2 --workers 4
    python3 -m training.record.conveyor_eval.model_search stage3 --workers 4
    python3 -m training.record.conveyor_eval.model_search analysis
    python3 -m training.record.conveyor_eval.model_search combined
"""

from __future__ import annotations

import os

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS",
           "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_v, "1")  # one thread per worker: 4 workers stay on 4 cores

import argparse  # noqa: E402
import json  # noqa: E402
import math  # noqa: E402
import re  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
import warnings  # noqa: E402
from concurrent.futures import ProcessPoolExecutor, as_completed  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402

from effente import features as F  # noqa: E402
from effente import safety as SF  # noqa: E402
from effente.model_input import Prep, load_minutes  # noqa: E402
from effente.trainer import _causal_median, fit_mlp, fit_pca, gamma_threshold, running_rows, to_current  # noqa: E402
from training.record.conveyor_eval import evaluate as E  # noqa: E402

OUT = Path("data/fake/model-search")
DATA = Path("data/fake/conveyor-eval")
EW_DIR = Path("data/fake/early-warning-search")
SMOOTHINGS = (1, 3, 5, 10)
SENS = tuple(round(0.025 * i, 3) for i in range(41))
IMPULSIVE = ("vib_peak_g", "snd_peak_db", "vib_kurtosis", "snd_kurt_hf", "knock_period_s", "knock_repeat")
INPUTS = ("median", "max", "impulsive")
GRACE_S = 30 * 60
TR = F.INDEX["trend_running"]
WEEKS = ("normal-w1", "normal-w2")
ROWS_A_WEEK = 7 * 1440
RUNNING_WEEK = 10080
FOLDS = ("both", "both_swapped", "w1", "w2")
CALIBS = ("tail15", "tail30", "tail50", "crossfit")
FP_LEVELS = (0.0, 0.25, 0.5, 1.0, 2.0)
N_FAULT_RUNS = 30


# ----------------------------------------------------------------- the cache

def _cache_one(folder: str, dest: str) -> str:
    folder, dest = Path(folder), Path(dest)
    t, stats, names = load_minutes(folder, stats=("median", "max"))
    med, mx = to_current(stats["median"], names), to_current(stats["max"], names)
    for j, n in enumerate(F.FEATURE_NAMES):  # as evaluate.minutes_of: week-long trends are empty in the tests
        if any(k in n for k in E.LONG):
            med[:, j] = np.nan
            mx[:, j] = np.nan
    lab = json.loads((folder / "labels.json").read_text())
    run = E.running_mask(t.astype(float), lab)
    meta = {"scenario": lab["scenario"], "faults": lab.get("faults", [])}
    if folder.name == "heldout":
        meta["nuisance"] = E.full_nuisances(folder, lab)
    tmp = dest.with_suffix(".tmp.npz")
    np.savez(tmp, t=t.astype(float), median=med.astype(np.float32), max=mx.astype(np.float32), running=run,
             meta=json.dumps(meta))
    tmp.rename(dest)
    return folder.name


def build_cache(data: Path, workers: int) -> None:
    out = OUT / "cache"
    out.mkdir(parents=True, exist_ok=True)
    folders = sorted(p for p in data.iterdir() if (p / "labels.json").exists())
    todo = [p for p in folders if not (out / f"{p.name}.npz").exists()]
    t0 = time.time()
    with ProcessPoolExecutor(workers) as ex:
        for f in as_completed([ex.submit(_cache_one, str(p), str(out / f"{p.name}.npz")) for p in todo]):
            print(f"  cached {f.result()}  ({time.time() - t0:.0f} s)", flush=True)
    for name in ("normal-w1", "heldout", "jam-subtle"):  # the cache must give exactly what minutes_of gives
        t, X, _ = E.minutes_of(data / name)
        c = np.load(out / f"{name}.npz")
        assert np.array_equal(t, c["t"]) and np.array_equal(np.isnan(X), np.isnan(c["median"])), name
        assert np.array_equal(np.nan_to_num(X), np.nan_to_num(c["median"].astype(float))), name
    print(f"cache ready: {len(folders)} runs, checked against evaluate.minutes_of ({time.time() - t0:.0f} s)")


_RUNS: dict | None = None


def runs() -> dict:
    """Every cached run: name -> dict(t, median, max, running, meta)."""
    global _RUNS
    if _RUNS is None:
        _RUNS = {}
        for p in sorted((OUT / "cache").glob("*.npz")):
            if p.name.endswith(".tmp.npz"):
                continue
            with np.load(p) as d:
                _RUNS[p.stem] = {"t": d["t"], "median": d["median"].astype(float), "max": d["max"].astype(float),
                                 "running": d["running"], "meta": json.loads(str(d["meta"]))}
        assert len(_RUNS) == 33 and "heldout" in _RUNS and all(w in _RUNS for w in WEEKS), sorted(_RUNS)
    return _RUNS


# ----------------------------------------------------------------- inputs

MAX_ALL = [j for j, n in enumerate(F.FEATURE_NAMES)
           if F.FEATURE_KIND.get(n) != "index" and not n.startswith("time_") and n != "trend_running"
           and not any(k in n for k in E.LONG)]
MAX_IMP = [F.INDEX[n] for n in IMPULSIVE]


def make_input(kind: str, r: dict) -> tuple[np.ndarray, list[str]]:
    names = list(F.FEATURE_NAMES)
    if kind == "median":
        return r["median"], names
    cols = MAX_ALL if kind == "max" else MAX_IMP
    return np.hstack([r["median"], r["max"][:, cols]]), names + [f"{F.FEATURE_NAMES[j]}_max" for j in cols]


# ----------------------------------------------------------------- detectors (fit(Z), score(Z) -> 1-D)

GROUPS = ("vibration", "sound", "heat", "other")


def input_group(name: str) -> str:
    """Which sensor group a prepared input belongs to (the group-balanced score; sound-fix's idea, 13 Nov)."""
    base = name[:-4] if name.endswith("_max") and name[:-4] in F.INDEX else name
    if base.startswith("missing_"):
        base = {"vibration": "vibration", "rotation": "vibration", "sound": "sound", "heat": "heat"}.get(base[8:], "other")
        return base
    if base.startswith("vib_shock_axis_"):
        return "vibration"
    sensor = F.FEATURE_SENSOR.get(base)
    return {"vibration": "vibration", "rotation": "vibration", "sound": "sound", "heat": "heat"}.get(sensor, "other")


class _Grouped:
    """Mixin: score = per-group mean squared residual, each over its learning level, then the groups' mean
    ("mean": equal shares, so 116 camera inputs cannot outvote 67 vibration ones) or their maximum ("max")."""
    group = None
    names = None

    def _group_fit(self, R2):
        g = np.array([input_group(n) for n in self.names])
        self._gidx = [np.nonzero(g == k)[0] for k in GROUPS if (g == k).any()]
        self._gref = [max(float(R2[:, i].mean()), 1e-9) for i in self._gidx]

    def _group_score(self, R2):
        G = np.stack([R2[:, i].mean(1) / ref for i, ref in zip(self._gidx, self._gref)], 1)
        return G.mean(1) if self.group == "mean" else G.max(1)


class DevPCA:
    """The device's PCA (effente.trainer.fit_pca + effente.model_runtime.Model.score) in float: residual over
    its training mean, plus (t2=True, as the device) the distance along the kept patterns (Hotelling's T2)."""

    def __init__(self, var: float = 0.95, max_k: int = 40, t2: bool = True, group: str | None = None):
        self.var, self.max_k, self.t2, self.group = var, max_k, t2, group

    def fit(self, Z):
        (W, b), (W2, b2) = fit_pca(np.nan_to_num(Z), self.var, self.max_k)[0]
        self.W, self.b, self.W2, self.b2 = (np.asarray(a, np.float64) for a in (W, b, W2, b2))
        self.k = self.W.shape[1]
        h, r = self._parts(Z)
        self.hs = np.where(h.std(0) > 1e-6, h.std(0), 1e-6)
        self.ref = float(max((r ** 2).mean(), 1e-9))
        if self.group:
            _Grouped._group_fit(self, r ** 2)
        return self

    def _parts(self, Z):
        Z = np.asarray(Z, np.float64)
        h = Z @ self.W + self.b
        return h, Z - (h @ self.W2 + self.b2)

    def score(self, Z):
        h, r = self._parts(Z)
        s = _Grouped._group_score(self, r ** 2) if self.group else (r ** 2).mean(1)
        return s / (1.0 if self.group else self.ref) + ((h / self.hs) ** 2).mean(1) if self.t2 else s


class DevAE:
    """The device's autoencoder (effente.trainer.fit_mlp, its sizes, noise 0.1) in float."""

    def __init__(self, mid: int | None = None, seed: int = 0, group: str | None = None):
        self.mid, self.seed, self.group = mid, seed, group

    def fit(self, Z):
        d = Z.shape[1]
        mid = self.mid or int(np.clip(d // 6, 3, 16))
        hid = int(np.clip(4 * mid, 8, 64))
        self.sizes = [d, hid, mid, hid, d]
        self.layers, _ = fit_mlp(Z, Z, self.sizes, ["relu", "relu", "relu", "linear"], noise=0.1, seed=self.seed)
        if self.group:
            _Grouped._group_fit(self, self.resid(Z) ** 2)
        return self

    def resid(self, Z):
        h = np.asarray(Z, np.float32)
        for i, (W, b) in enumerate(self.layers):
            h = h @ W + b
            if i < len(self.layers) - 1:
                h = np.maximum(h, 0)
        return np.asarray(Z, np.float32) - h

    def score(self, Z):
        R2 = self.resid(Z).astype(np.float64) ** 2
        return _Grouped._group_score(self, R2) if self.group else R2.mean(1)


class GaussGroups:
    """Mahalanobis made group-balanced: each input's conditional residual given all the others (precision P of
    the Ledoit-Wolf covariance: r = P (z - mu) / sqrt(diag P), in conditional spreads), squared, as _Grouped."""

    def __init__(self, group: str = "mean"):
        self.group = group

    def fit(self, Z):
        from sklearn.covariance import LedoitWolf
        lw = LedoitWolf().fit(Z)
        self.mu, self.P = lw.location_, lw.precision_
        dg = np.diag(self.P)
        self.ok = dg > 1e-9  # a constant input (the always-missing room sensor) has no conditional spread
        self.sd = np.sqrt(np.where(self.ok, dg, 1.0))
        _Grouped._group_fit(self, self.resid(Z) ** 2)
        return self

    def resid(self, Z):
        return np.where(self.ok, ((np.asarray(Z, float) - self.mu) @ self.P) / self.sd, 0.0)

    def score(self, Z):
        return _Grouped._group_score(self, self.resid(Z) ** 2)


class MaxZFloor:
    """The no-model floor, made able to alarm (the checker, 13 Nov). evaluate.py's max |z| scores the clipped
    inputs (+-8), so its score never passes its own line (20.2: at most 0.40 of it) and it only ever alarms at
    sensitivity 0.9 or more, where it alarms always. Here: unclipped robust z (Prep's scaling, missing = 0), and
    only the readings with a real healthy spread (interquartile range above 0.1 robust spreads in learning;
    a count that is nearly always the same value, like th_hot_pixels, otherwise dominates)."""
    raw = True

    def fit(self, Z):
        q1, q3 = np.percentile(Z, [25, 75], 0)
        self.keep = np.nonzero((q3 - q1) > 0.1)[0]
        return self

    def score(self, Z):
        return np.abs(Z[:, self.keep]).max(1)


class Bench:
    """A training.anomaly.detectors class, with its keyword settings; .score keeps only the score."""

    def __init__(self, cls, **kw):
        self.det = cls(**kw)

    def fit(self, Z):
        self.det.fit(Z)
        return self

    def score(self, Z):
        return self.det.score(Z)[0]


DEVICE_EQUIV = {"PCA (on the device)": "PCA (device, float)", "autoencoder (on the device)": "autoencoder (device, float)"}


def detector(approach: str, params: dict):
    from sklearn.ensemble import IsolationForest
    from sklearn.mixture import GaussianMixture
    from training.anomaly import detectors as D
    p = dict(params)
    seed = p.pop("seed", 0)
    approach = DEVICE_EQUIV.get(approach, approach)
    if approach == "PCA (device, float)":
        return DevPCA(**p)
    if approach == "autoencoder (device, float)":
        return DevAE(seed=seed, **p)
    if approach == "PCA":
        return Bench(D.PCARecon, **p)
    if approach == "Gaussian (Mahalanobis)":
        return GaussGroups(**p) if p.get("group") else Bench(D.Gaussian)
    if approach == "Gaussian mixture":
        class M(D.Mixture):  # the bench's, with the seed free (it fixes random_state=0)
            def fit(self, Z):
                from sklearn.decomposition import PCA
                self.pca = PCA(min(self.dims, Z.shape[1])).fit(Z)
                self.gm = GaussianMixture(self.k, covariance_type="full", reg_covar=1e-3, random_state=seed).fit(
                    self.pca.transform(Z))
                return self
        return Bench(M, **p)
    if approach == "autoencoder (sklearn)":
        return Bench(D.Autoencoder, seed=seed, **p)
    if approach == "similarity (AAKR/MSET-style)":
        return Bench(D.Similarity, seed=seed, **p)
    if approach == "isolation forest":
        class Fo(D.Forest):
            def fit(self, Z):
                self.f = IsolationForest(n_estimators=p.get("n", 200), random_state=seed).fit(Z)
                return self
        return Bench(Fo)
    if approach == "nearest neighbours":
        return Bench(D.KNN, seed=seed, **p)
    if approach == "max |z| (no model)":
        return MaxZFloor()
    raise ValueError(approach)


def _feed(prep, det, X):
    if getattr(det, "raw", False):
        with np.errstate(invalid="ignore"):
            return np.nan_to_num(prep._scaled(X), nan=0.0, posinf=0.0, neginf=0.0)
    return prep.transform(X)


# ----------------------------------------------------------------- training folds and calibration

HELD_SHIFT_S = 2 * 604800  # heldout's clock also starts at 0: as a training week it is put after w1 and w2

ROTATIONS = {"rot_heldout": ("normal-w1", "normal-w2"), "rot_w2": ("normal-w1", "heldout"),
             "rot_w1": ("normal-w2", "heldout")}
WATCHED = {"rot_heldout": "heldout", "rot_w2": "normal-w2", "rot_w1": "normal-w1"}


def fold_chunks(fold: str, kind: str):
    """The training data of a fold as time-ordered chunks [(t, X)], and the input names."""
    R = runs()
    order = {"both": WEEKS, "both_swapped": WEEKS[::-1], "w1": WEEKS[:1], "w2": WEEKS[1:],
             "all3": WEEKS + ("heldout",), **ROTATIONS}[fold]
    chunks = []
    for w in order:
        X, names = make_input(kind, R[w])
        chunks.append((R[w]["t"] + (HELD_SHIFT_S if w == "heldout" else 0), X))
    return chunks, names


def _running(X):
    return running_rows(X, list(F.FEATURE_NAMES))


def fit_detector(approach, params, t, X, names):
    prep = Prep.fit(X, names, t, running_only=False)
    det = detector(approach, params)
    det.names = prep.names_out  # for the group-balanced scores
    det.fit(_feed(prep, det, X))
    return prep, det


def calibrated_fit(approach, params, kind, fold, calib):
    """(prep, det, calibration raw scores) for a fold and calibration method. Running training minutes only."""
    chunks, names = fold_chunks(fold, kind)
    t = np.concatenate([c[0] for c in chunks])
    X = np.vstack([c[1] for c in chunks])
    run = _running(X)
    t, X = t[run], X[run]
    if calib.startswith("tail"):
        share = int(calib[4:]) / 100
        cut = int((1 - share) * len(t))
        prep, det = fit_detector(approach, params, t[:cut], X[:cut], names)
        return prep, det, det.score(_feed(prep, det, X[cut:]))
    if calib == "crossfit":
        # Each week scored by a model that never saw it (one week: its two halves), pooled in order.
        if len(chunks) >= 2:
            sizes = [int(_running(c[1]).sum()) for c in chunks]
            part = np.repeat(np.arange(len(chunks)), sizes)
        else:
            part = (np.arange(len(t)) >= len(t) // 2).astype(int)
        cal = []
        for g in range(part.max() + 1):
            prep, det = fit_detector(approach, params, t[part != g], X[part != g], names)
            cal.append(det.score(_feed(prep, det, X[part == g])))
        prep, det = fit_detector(approach, params, t, X, names)  # the final model learns on all of it
        return prep, det, np.concatenate(cal)
    raise ValueError(calib)


# ----------------------------------------------------------------- scoring jobs

def key_of(approach, kind, params, variant) -> str:
    p = "_".join(f"{k}{v}" for k, v in sorted(params.items())) or "default"
    s = f"{approach}__{kind}__{p}__{variant}"
    return re.sub(r"[^A-Za-z0-9_.=-]+", "-", s).strip("-")


def job(approach, kind="median", params=None, fold="both", calib="tail15", **kw) -> dict:
    params = params or {}
    approach = DEVICE_EQUIV.get(approach, approach) if not kw.get("real_device") else approach
    j = {"approach": approach, "input": kind, "params": params, "fold": fold, "calib": calib, **kw}
    j["variant"] = f"{fold}-{calib}"
    j["key"] = "v2__" + key_of(approach + (" REAL" if kw.get("real_device") else ""), kind, params, j["variant"])
    return j


def score_job(j: dict) -> dict:
    dest = OUT / "scores" / f"{j['key']}.npz"
    if dest.exists():
        return {"key": j["key"], "cached": True}
    R = runs()
    t0 = time.time()
    extra = {}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        if j.get("real_device"):  # the device's own trainer and int8 runtime, both weeks, its own split
            chunks, _ = fold_chunks("both", "median")
            t = np.concatenate([c[0] for c in chunks])
            X = np.vstack([c[1] for c in chunks])
            m = E.DeviceMethod(j["real_device"]).fit(t, X)
            run = np.nonzero(_running(X))[0]
            tt = t[run]
            cal_rows = run[tt >= tt[0] + (tt[-1] - tt[0]) * 0.85]  # train_model's split by time
            cal_raw = m.model.score(X[cal_rows])[0]
            out = {n: m.model.score(r["median"])[0] for n, r in R.items() if n not in WEEKS}
            extra = {"device_threshold": float(m.d["threshold"])}
            if j.get("check_watcher"):
                h = R["heldout"]
                extra["watcher_unknown_score"] = [s for _, s, _ in m.judge(h["t"], h["median"], h["running"])]
        else:
            prep, det, cal_raw = calibrated_fit(j["approach"], j["params"], j["input"], j["fold"], j["calib"])
            out = {n: det.score(_feed(prep, det, make_input(j["input"], r)[0])) for n, r in R.items()
                   if n not in WEEKS or j["fold"] in ROTATIONS}
            extra = {"inputs": prep.n_out}
            if isinstance(det, DevPCA):
                extra["patterns_kept"] = det.k
            if isinstance(det, MaxZFloor):
                extra["readings_kept"] = int(len(det.keep))
    fit_s = time.time() - t0
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp.npz")
    np.savez(tmp, cal=np.asarray(cal_raw, float), meta=json.dumps({**j, **extra, "total_s": round(fit_s, 1)}, default=str),
             **{f"run__{k}": np.asarray(v, float) for k, v in out.items()})
    tmp.rename(dest)
    return {"key": j["key"], "total_s": round(fit_s, 1)}


def _init_worker():
    runs()


def run_jobs(jobs: list[dict], workers: int) -> None:
    todo = [j for j in jobs if not (OUT / "scores" / f"{j['key']}.npz").exists()]
    print(f"{len(jobs)} scoring jobs, {len(todo)} to run on {workers} workers", flush=True)
    if not todo:
        return
    t0 = time.time()
    todo.sort(key=lambda j: -(("autoencoder" in j["approach"]) * 10 + (j["calib"] == "crossfit") * 3 +
                              ("similarity" in j["approach"]) * 3 + (j["input"] == "max") * 2))
    failed = []
    with ProcessPoolExecutor(workers, initializer=_init_worker) as ex:
        futs = {ex.submit(score_job, j): j for j in todo}
        for k, f in enumerate(as_completed(futs), 1):
            try:
                r = f.result()
                print(f"  [{k}/{len(todo)}] {r['key']}  {r.get('total_s')} s  ({time.time() - t0:.0f} s in)", flush=True)
            except Exception as e:  # noqa: BLE001
                failed.append(futs[f]["key"])
                print(f"  [{k}/{len(todo)}] FAILED {futs[f]['key']}: {e!r}", flush=True)
    if failed:
        raise SystemExit(f"{len(failed)} scoring jobs failed: {failed}")


def load_scores(key):
    with np.load(OUT / "scores" / f"{key}.npz") as d:
        meta = json.loads(str(d["meta"]))
        raw = {k[5:]: d[k] for k in d.files if k.startswith("run__")}
        cal = d["cal"]
    assert len(raw) >= 31 and "heldout" in raw, (key, sorted(raw))
    bad = {k: int((~np.isfinite(v)).sum()) for k, v in raw.items() if not np.isfinite(v).all()}
    assert not bad and np.isfinite(cal).all(), (key, bad, int((~np.isfinite(cal)).sum()))
    return meta, cal, raw


# ----------------------------------------------------------------- judging

def smoothed(raw, reset_running, n):
    """Median of the last n scores since the last stop (as the device's Watcher), NaN while stopped."""
    import pandas as pd
    raw = np.asarray(raw, float)
    on = np.asarray(reset_running, bool)
    out = np.full(len(raw), np.nan)
    if n <= 1:
        out[on] = raw[on]
        return out
    seg = np.cumsum(np.r_[on[0], on[1:] & ~on[:-1]]) * on
    s = pd.Series(np.where(on, raw, np.nan))
    out = s.groupby(seg).transform(lambda x: x.rolling(n, min_periods=1).median()).to_numpy(copy=True)
    out[~on] = np.nan
    return out


def _episodes(A):
    """Per column: stretches in a (rows, columns) bool array (a False row ends one)."""
    if not len(A):
        return np.zeros(A.shape[1], int)
    return A[0].astype(int) + ((~A[:-1]) & A[1:]).sum(0)


class Prepared:
    """What judging needs of each run, computed once. `watch` is the healthy week the false alerts are counted on
    (with the fault runs' pre-onset minutes): heldout, or in a rotation the week left out of training."""

    def __init__(self, watch: str = "heldout"):
        R = runs()
        self.watch = watch
        self.runs = {}
        for name, r in R.items():
            if name in ("normal-w1", "normal-w2", "heldout") and name != watch:
                continue
            t, run, meta = r["t"], r["running"].astype(bool), r["meta"]
            d = {"t": t, "run": run, "reset": ~(r["median"][:, TR] < 0.5), "ridx": np.nonzero(run)[0]}
            judged = run & (t >= t[0] + E.WARMUP_S)
            if name == watch:
                d["fpmask"] = judged
                d["nuisance"] = meta.get("nuisance")
            else:
                sc, faults = meta["scenario"], meta["faults"]
                onset = float(sc["onset"])
                end = min(max(f["end_s"] for f in faults) + GRACE_S, t[-1] + 60)
                # The minute holding the onset (onsets fall 3-56 s into a minute) counts as after it: in the window,
                # not in the false-alert minutes (the checker, 13 Nov).
                win = np.zeros(len(t), bool)
                for f in faults:
                    win |= (t + 60 > f["start_s"]) & (t < f["end_s"] + GRACE_S)
                win &= run & (t + 60 > onset)
                d.update(onset=onset, kind=sc["fault_kind"], sev=float(sc["severity"]), f_end=float(faults[0]["end_s"]),
                         win=win, fpmask=judged & (t + 60 <= onset), slow=sc["fault_kind"] in E.SLOW_FAULTS,
                         win_len_min=max((end - onset) / 60, 1.0))
            self.runs[name] = d
        self.fault_names = sorted(n for n in self.runs if n != watch)
        assert len(self.fault_names) == N_FAULT_RUNS and watch in self.runs, len(self.fault_names)
        self.held_min = int(self.runs[watch]["fpmask"].sum())
        self.pre_min = int(sum(self.runs[n]["fpmask"].sum() for n in self.fault_names))


def judge_flags(P: Prepared, flags: dict, details=False) -> list[dict]:
    """Every column of per-run (minutes, columns) alert flags, judged by the agreed rules."""
    k = next(iter(flags.values())).shape[1]
    fp_held = fp_pre = 0
    per = {}
    for name, d in P.runs.items():
        A = flags[name] & d["run"][:, None]
        fp = _episodes(A & d["fpmask"][:, None])
        if name == P.watch:
            fp_held = fp
            continue
        fp_pre = fp_pre + fp
        ri = d["ridx"]  # a stretch continues over stops: the previous RUNNING minute
        Ar = A[ri]
        prev = np.vstack([np.zeros((1, k), bool), Ar[:-1]])
        start = Ar & ~prev & d["win"][ri][:, None]
        anyw = Ar & d["win"][ri][:, None]
        has, has_any = start.any(0), anyw.any(0)
        t_first = np.where(has, d["t"][ri][start.argmax(0)], np.nan)
        mins = (t_first - d["onset"]) / 60
        if d["slow"]:
            u = np.clip((t_first - d["onset"]) / max(d["f_end"] - d["onset"], 1.0), 0, 1)
            wear = d["sev"] * (np.exp(3 * u) - 1) / (math.e ** 3 - 1)
            late = np.where(has, wear, 1.0)
        else:
            wear = np.full(k, np.nan)
            late = np.where(has, np.minimum(mins / d["win_len_min"], 1.0), 1.0)
        per[name] = {"caught": has, "any": has_any, "mins": mins, "wear": np.where(has, 100 * wear, np.nan), "late": late}
    names = P.fault_names
    slow = [n for n in names if P.runs[n]["slow"]]
    sudden = [n for n in names if not P.runs[n]["slow"]]
    sub = [n for n in names if P.runs[n]["sev"] < 0.5]
    fp_all = fp_held + fp_pre
    rows = []
    for c in range(k):
        caught = {n: bool(per[n]["caught"][c]) for n in names}
        wear_c = [per[n]["wear"][c] for n in slow if caught[n]]
        mins_c = [per[n]["mins"][c] for n in names if caught[n] and not P.runs[n]["slow"]]
        mins_all = [per[n]["mins"][c] for n in names if caught[n]]
        row = {"fp": int(fp_all[c]), "fp_week": round(float(fp_all[c]) / (P.held_min + P.pre_min) * RUNNING_WEEK, 3),
               "fp_heldout": int(fp_held[c]), "fp_preonset": int(fp_pre[c]),
               "fp_heldout_week": round(float(fp_held[c]) / P.held_min * RUNNING_WEEK, 3),
               "fp_preonset_week": round(float(fp_pre[c]) / P.pre_min * RUNNING_WEEK, 3),
               "caught": sum(caught.values()), "missed": N_FAULT_RUNS - sum(caught.values()),
               "removed_by_new_start_rule": int(sum(per[n]["any"][c] for n in names) - sum(caught.values())),
               "subtle": sum(caught[n] for n in sub), "obvious": sum(caught[n] for n in names if n not in sub),
               "slow_caught": sum(caught[n] for n in slow),
               "slow_wear_caught_median": round(float(np.median(wear_c)), 2) if wear_c else None,
               "slow_wear_all_median_missed100": round(float(np.median([per[n]["wear"][c] if caught[n] else 100.0
                                                                        for n in slow])), 2),
               "sudden_subtle_caught": sum(caught[n] for n in sudden if n in sub),
               "sudden_obvious_caught": sum(caught[n] for n in sudden if n not in sub),
               "sudden_median_min": round(float(np.median(mins_c)), 1) if mins_c else None,
               "median_min_caught": round(float(np.median(mins_all)), 1) if mins_all else None,
               "lateness": round(float(np.mean([per[n]["late"][c] for n in names])), 4),
               "missed_runs": [n for n in names if not caught[n]]}
        if details:
            row["runs"] = {n: {"caught": caught[n], "minutes": None if not caught[n] else round(float(per[n]["mins"][c]), 1),
                               "wear_pct": None if not caught[n] or not P.runs[n]["slow"] else round(float(per[n]["wear"][c]), 2),
                               "lateness": round(float(per[n]["late"][c]), 4)} for n in names}
        rows.append(row)
    return rows


def lines_for(cal, n):
    return gamma_threshold(_causal_median(np.asarray(cal, float), n), ROWS_A_WEEK)


def ratios(P, cal, raw, n):
    line = lines_for(cal, n)
    return line, {name: smoothed(raw[name], P.runs[name]["reset"], n) / line for name in P.runs}


def model_flags(P, ratio, sens):
    m = np.array([SF.margins(s)["model"] for s in sens])
    return {name: np.nan_to_num(r, nan=-1.0)[:, None] > m[None, :] for name, r in ratio.items()}


def judge_scores(P, cal, raw, smooth_ns=SMOOTHINGS, sens=SENS, details=False) -> list[dict]:
    out = []
    for n in smooth_ns:
        line, ratio = ratios(P, cal, raw, n)
        h = P.runs[P.watch]
        held = ratio[P.watch][h["fpmask"] & np.isfinite(ratio[P.watch])]
        aucs = [E.auc(held, ratio[nm][P.runs[nm]["win"] & np.isfinite(ratio[nm])]) for nm in P.fault_names]
        for s, row in zip(sens, judge_flags(P, model_flags(P, ratio, sens), details)):
            out.append({"smooth": n, "sensitivity": s, "line": line, "mean_auc": round(float(np.nanmean(aucs)), 4), **row})
    return out


def judge_jobs(jobs, P=None) -> list[dict]:
    P = P or Prepared()
    out = []
    for j in jobs:
        meta, cal, raw = load_scores(j["key"])
        for row in judge_scores(P, cal, raw):
            out.append({"key": j["key"], "approach": j["approach"], "input": j["input"], "params": j["params"],
                        "fold": j.get("fold"), "calib": j.get("calib"), "real_device": bool(j.get("real_device")), **row})
    return out


def rank_key(r):
    return (r["fp"], r["missed"], r["lateness"], -r["mean_auc"])


def rank(rows) -> list[dict]:
    """Fewest false alerts (no slack), then fewest missed, then lowest lateness; candidates catching nothing out."""
    return sorted((r for r in rows if r["caught"] > 0), key=rank_key)


def best_per(rows, by=("approach", "input")) -> list[dict]:
    seen, out = set(), []
    for r in rank(rows):
        g = tuple(json.dumps(r[b], sort_keys=True) for b in by)
        if g not in seen:
            seen.add(g)
            out.append(r)
    return out


def frontier_levels(rows, levels=FP_LEVELS) -> list[dict]:
    """Per false-alert level (per running week): the fewest misses reachable at or below it, and the earliest there."""
    out = []
    for L in levels:
        ok = [r for r in rows if r["caught"] > 0 and r["fp_week"] <= L + 1e-9]
        if ok:
            b = min(ok, key=lambda r: (r["missed"], r["lateness"], r["fp"]))
            out.append({"level": L, **{k: b.get(k) for k in SHOW}, "candidate": describe(b)})
    return out


SHOW = ("fp", "fp_week", "fp_heldout", "fp_preonset", "caught", "missed", "subtle", "slow_caught",
        "slow_wear_caught_median", "sudden_subtle_caught", "sudden_median_min", "median_min_caught", "lateness",
        "mean_auc", "removed_by_new_start_rule", "smooth", "sensitivity")


def describe(r) -> str:
    p = ",".join(f"{k}={v}" for k, v in sorted(r["params"].items()))
    return (f"{r['approach']}{' [real]' if r.get('real_device') else ''} | {r['input']} | {p or '-'} | "
            f"smooth {r['smooth']} | sens {r['sensitivity']}" + (f" + EW {r['ew']}" if r.get("ew") else ""))


def fmt_row(r) -> str:
    return (f"{describe(r):82s} fp {r['fp']} ({r['fp_week']:.2f}/rw; held {r['fp_heldout']}, pre {r['fp_preonset']}) | "
            f"missed {r['missed']:2d}/{N_FAULT_RUNS} | late {r['lateness']:.3f} | slow {r['slow_caught']}/8 wear "
            f"{r['slow_wear_caught_median']} | sudden subtle {r['sudden_subtle_caught']}/11, med "
            f"{r['sudden_median_min']} min | AUC {r['mean_auc']:.3f} | -{r['removed_by_new_start_rule']} restart")


def write(name, obj):
    p = OUT / name
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=1, default=str))
    tmp.rename(p)
    print(f"wrote {p}")


def chosen_calib() -> str:
    return json.loads((OUT / "calibration.json").read_text())["chosen"]


# ----------------------------------------------------------------- calibration study

CAL_MODELS = [("PCA (device, float)", "median", {"var": 0.95, "t2": False, "max_k": 40}),
              ("PCA (device, float)", "median", {}),
              ("PCA", "impulsive", {}),
              ("Gaussian (Mahalanobis)", "impulsive", {}),
              ("Gaussian (Mahalanobis)", "median", {}),
              ("autoencoder (device, float)", "median", {}),
              ("autoencoder (sklearn)", "median", {})]


def calibration(workers):
    """Which way of setting the line keeps its false-alert rate when the training changes? Per model and method,
    the setting is picked ONCE on the both-weeks fold (the ranking rule, and the next one less sensitive), then
    the same setting is judged on every fold."""
    jobs = {(a, k, json.dumps(p), f, c): job(a, k, p, fold=f, calib=c)
            for a, k, p in CAL_MODELS for f in FOLDS for c in CALIBS}
    run_jobs(list(jobs.values()), workers)
    P = Prepared()
    res = []
    for a, k, p in CAL_MODELS:
        for c in CALIBS:
            rows = {f: judge_jobs([jobs[(a, k, json.dumps(p), f, c)]], P) for f in FOLDS}
            pick = rank(rows["both"])[0]
            down = next((r for r in rows["both"] if r["smooth"] == pick["smooth"]
                         and abs(r["sensitivity"] - (pick["sensitivity"] - 0.025)) < 1e-9), pick)
            entry = {"model": f"{a} | {k} | {p}", "calib": c, "pick": {x: pick[x] for x in ("smooth", "sensitivity")}}
            for tag, sel in (("pick", pick), ("one_step_down", down)):
                at = {f: next(r for r in rows[f] if r["smooth"] == sel["smooth"] and r["sensitivity"] == sel["sensitivity"])
                      for f in FOLDS}
                entry[tag] = {f: {x: at[f][x] for x in ("fp", "fp_week", "missed", "lateness", "line")} for f in FOLDS}
                fps = [at[f]["fp_week"] for f in FOLDS]
                entry[tag + "_fp_max"], entry[tag + "_fp_spread"] = max(fps), max(fps) - min(fps)
            ls = [entry["pick"][f]["line"] for f in FOLDS]
            entry["line_ratio"] = round(max(ls) / min(ls), 3)
            entry["repicked"] = {f: {x: rank(rows[f])[0][x] for x in ("fp", "missed", "lateness", "smooth", "sensitivity")}
                                 for f in FOLDS}
            res.append(entry)
            print(f"  {entry['model'][:58]:58s} {c:9s} pick n{pick['smooth']} s{pick['sensitivity']:<5} "
                  f"fp/rw by fold {[entry['pick'][f]['fp_week'] for f in FOLDS]} max {entry['pick_fp_max']:.2f} | "
                  f"one step down max {entry['one_step_down_fp_max']:.2f} | line ratio {entry['line_ratio']} | "
                  f"missed both {entry['pick']['both']['missed']}", flush=True)
    summary = {}
    for c in CALIBS:
        es = [e for e in res if e["calib"] == c]
        summary[c] = {"mean_fp_max_over_folds": round(float(np.mean([e["pick_fp_max"] for e in es])), 3),
                      "mean_fp_max_one_step_down": round(float(np.mean([e["one_step_down_fp_max"] for e in es])), 3),
                      "mean_fp_spread": round(float(np.mean([e["pick_fp_spread"] for e in es])), 3),
                      "mean_line_ratio": round(float(np.mean([e["line_ratio"] for e in es])), 3),
                      "mean_missed_both": round(float(np.mean([e["pick"]["both"]["missed"] for e in es])), 2)}
    chosen = min(CALIBS, key=lambda c: (summary[c]["mean_fp_max_over_folds"], summary[c]["mean_fp_max_one_step_down"],
                                        summary[c]["mean_line_ratio"]))
    for c, s in summary.items():
        print(f"{c:9s} {s}")
    print("chosen:", chosen)
    write("calibration.json", {"rule": "lowest mean over models of the worst fold's false alerts at the setting "
                                       "picked on both weeks; then the same one step less sensitive; then the line's spread",
                               "summary": summary, "chosen": chosen, "entries": res})


# ----------------------------------------------------------------- stages

APPROACHES = list(E.methods().keys())


def stage1_jobs(calib) -> list[dict]:
    return [job(a, k, calib=calib) for a in APPROACHES for k in INPUTS]


def check_device_equivalents(P) -> dict:
    """The float equivalents against the real device (both weeks, the trainer's split by time)."""
    out = {}
    for real, a in (("pca", "PCA (on the device)"), ("autoencoder", "autoencoder (on the device)")):
        jr = job(a, "median", real_device=real, check_watcher=True)
        run_jobs([jr], 1)
        ma, ca, ra = load_scores(jr["key"])
        line5 = lines_for(ca, 5)
        h = P.runs["heldout"]
        mine = smoothed(ra["heldout"], h["reset"], 5) / line5
        w = np.asarray(ma["watcher_unknown_score"], float)
        both = np.isfinite(mine) & (w > 0)
        out[real] = {"device_threshold": ma["device_threshold"], "line5_recomputed": line5,
                     "watcher_vs_vectorised_max_abs_diff": round(float(np.max(np.abs(np.round(mine[both], 3) - w[both]))), 4),
                     "minutes": int(both.sum()), "key": jr["key"]}
    return out


def stage1(workers):
    calib = chosen_calib()
    jobs = stage1_jobs(calib)
    run_jobs(jobs, workers)
    P = Prepared()
    eq = check_device_equivalents(P)
    rows = judge_jobs(jobs, P)
    # The device's own (int8, train_model's tail-15 line) as a reference row, judged the same way.
    real = judge_jobs([job("PCA (on the device)", "median", real_device="pca", check_watcher=True),
                       job("autoencoder (on the device)", "median", real_device="autoencoder", check_watcher=True)], P)
    top = best_per(rows)
    print("device equivalents:", json.dumps(eq))
    print(f"\njudged running minutes: held-out {P.held_min}, before onsets {P.pre_min}; calibration {calib}")
    print("\nbest per approach x input (ranked):")
    for r in top:
        print("  " + fmt_row(r))
    print("\nthe real device, as trained today:")
    for r in best_per(real):
        print("  " + fmt_row(r))
    write("stage1.json", {"calibration": calib, "judged_running_minutes": {"heldout": P.held_min, "pre_onset": P.pre_min},
                          "device_equivalents": eq, "best_per_approach_input": top, "real_device_best": best_per(real),
                          "frontier": frontier_levels(rows), "candidates": rows, "real_device_candidates": real})


TUNABLE = ("PCA", "PCA (device, float)", "autoencoder (device, float)", "autoencoder (sklearn)", "nearest neighbours",
           "Gaussian mixture", "similarity (AAKR/MSET-style)", "isolation forest")


def stage2_jobs(top3, kinds, calib) -> list[dict]:
    jobs = []
    for a in top3:
        for kind in kinds[a]:
            if a in ("PCA", "PCA (device, float)"):
                for var in (0.90, 0.95, 0.99):
                    for t2 in (False, True):
                        for mk in (40, 200):
                            jobs.append(job("PCA (device, float)", kind, {"var": var, "t2": t2, "max_k": mk}, calib=calib))
            elif a == "autoencoder (device, float)":
                for mid in (4, 8, 16, 32):
                    for seed in (0, 1, 2):
                        jobs.append(job(a, kind, {"mid": mid, "seed": seed}, calib=calib))
            elif a == "autoencoder (sklearn)":
                for mid in (4, 8, 16, 32):
                    h = int(np.clip(4 * mid, 8, 64))
                    for seed in (0, 1, 2):
                        jobs.append(job(a, kind, {"hidden": [h, mid, h], "seed": seed}, calib=calib))
            elif a == "nearest neighbours":
                for k in (1, 3, 5, 10, 20, 50):
                    for mem in (5000, 20000):
                        jobs.append(job(a, kind, {"k": k, "memory": mem}, calib=calib))
            elif a == "Gaussian mixture":
                for k in (1, 2, 4, 8, 12):
                    for dims in (15, 30, 60):
                        jobs.append(job(a, kind, {"k": k, "dims": dims}, calib=calib))
            elif a == "similarity (AAKR/MSET-style)":
                for mem in (1000, 2000, 5000, 20000):
                    for k in (5, 10, 20, 50):
                        jobs.append(job(a, kind, {"memory": mem, "k": k}, calib=calib))
            elif a == "isolation forest":
                for n in (100, 200, 500):
                    for seed in (0, 1, 2):
                        jobs.append(job(a, kind, {"n": n, "seed": seed}, calib=calib))
    return jobs


def stage2(workers):
    s1 = json.loads((OUT / "stage1.json").read_text())
    calib = s1["calibration"]
    order = []
    for r in s1["best_per_approach_input"]:
        if r["approach"] in TUNABLE and r["approach"] not in order:
            order.append(r["approach"])
    top3 = order[:3]
    kinds = {a: list(dict.fromkeys(r["input"] for r in s1["best_per_approach_input"] if r["approach"] == a))[:2]
             for a in top3}
    untunable_ahead = [r["approach"] for r in s1["best_per_approach_input"][:5] if r["approach"] not in TUNABLE]
    print("stage 2 tunes:", kinds, "| ahead of them with nothing to tune:", untunable_ahead, flush=True)
    jobs = stage2_jobs(top3, kinds, calib)
    run_jobs(jobs, workers)
    P = Prepared()
    rows = judge_jobs(jobs, P)
    allrows = s1["candidates"] + rows
    top = best_per(allrows, by=("approach", "input", "params"))
    print("\nbest per approach x input x setting (ranked, stages 1 and 2):")
    for r in top[:25]:
        print("  " + fmt_row(r))
    beyond = []  # smoothing beyond the brief's grid, reported on its own
    for r in top[:4]:
        meta, cal, raw = load_scores(r["key"])
        for b in [rank(judge_scores(P, cal, raw, smooth_ns=(n,)))[0] for n in (10, 15, 20, 30)]:
            beyond.append({"pick": describe(r), **{k: b[k] for k in SHOW}})
            print(f"  longer smoothing {describe(r)[:70]:70s} n={b['smooth']:2d}: fp {b['fp']} missed {b['missed']} "
                  f"late {b['lateness']:.3f}")
    write("stage2.json", {"calibration": calib, "tuned": kinds, "ahead_with_nothing_to_tune": untunable_ahead,
                          "best_per_setting": top, "top10": best_per(allrows)[:10], "frontier": frontier_levels(allrows),
                          "beyond_grid_smoothing": beyond, "candidates": rows})


def stage3(workers, n_top=5):
    """The top few settings (distinct approaches) plus the device's PCA as it is, on the other folds and seeds."""
    s2 = json.loads((OUT / "stage2.json").read_text())
    calib = s2["calibration"]
    picks, seen = [], set()
    for r in s2["best_per_setting"]:
        fam = r["approach"].replace(" (device, float)", "")
        if fam not in seen:
            seen.add(fam)
            picks.append(r)
        if len(picks) == n_top:
            break
    resid = next(r for r in s2["best_per_setting"] if r["approach"] == "PCA (device, float)" and r["input"] == "median"
                 and r["params"].get("t2") is False and r["params"].get("var") == 0.95)
    today = next(r for r in s2["best_per_setting"] if r["approach"] == "PCA (device, float)" and r["input"] == "median"
                 and r["params"] == {})
    for extra in (resid, today):
        if all(describe(extra) != describe(p) for p in picks):
            picks.append(extra)
    seeded = ("autoencoder", "similarity", "nearest", "mixture", "forest")
    plan, jobs = [], []
    for r in picks:
        vs = [(f, job(r["approach"], r["input"], r["params"], fold=f, calib=calib)) for f in FOLDS]
        if any(s in r["approach"].lower() for s in seeded):
            base = {k: v for k, v in r["params"].items() if k != "seed"}
            vs += [(f"seed{s}", job(r["approach"], r["input"], {**base, "seed": s}, calib=calib)) for s in (11, 12, 13)]
        plan.append((r, vs))
        jobs += [j for _, j in vs]
    run_jobs(jobs, workers)
    P = Prepared()
    out = []
    for r, vs in plan:
        e = {"pick": describe(r), "stage2": {k: r[k] for k in SHOW}, "variants": {}}
        for v, j in vs:
            rows = judge_jobs([j], P)
            fixed = next(x for x in rows if x["smooth"] == r["smooth"] and x["sensitivity"] == r["sensitivity"])
            re_ = rank(rows)[0]
            e["variants"][v] = {"fixed": {k: fixed[k] for k in SHOW + ("line",)},
                                "repicked": {k: re_[k] for k in SHOW + ("line",)},
                                "frontier": frontier_levels(rows)}
        folds = [e["variants"][f]["repicked"] for f in FOLDS]
        e["repicked_spread"] = {"fp": [x["fp"] for x in folds], "missed": [x["missed"] for x in folds],
                                "lateness": [x["lateness"] for x in folds],
                                "mean_missed": round(float(np.mean([x["missed"] for x in folds])), 2),
                                "mean_lateness": round(float(np.mean([x["lateness"] for x in folds])), 4)}
        e["fixed_fp_by_fold"] = [e["variants"][f]["fixed"]["fp"] for f in FOLDS]
        out.append(e)
        print(f"\n{e['pick']}")
        for v, d in e["variants"].items():
            print(f"   {v:13s} fixed: fp {d['fixed']['fp']:3d} missed {d['fixed']['missed']:2d} late {d['fixed']['lateness']:.3f} "
                  f"| repicked n{d['repicked']['smooth']} s{d['repicked']['sensitivity']}: fp {d['repicked']['fp']} "
                  f"missed {d['repicked']['missed']} late {d['repicked']['lateness']:.3f}")
    write("stage3.json", {"calibration": calib, "folds": FOLDS, "picks": out})


# ----------------------------------------------------------------- analysis

def device_residual_only(calib="tail15") -> dict:
    """Residual-only PCA on the device's own trainer and int8 runtime, changing nothing in effente/: train as the
    device does, drop the model file's "pca_score" (effente.model_runtime.Model.score then scores the residual
    alone), redo the line from the device's calibration rows, and run its Watcher on the held-out week."""
    from effente import model_runtime as RT
    key = "v2__PCA-on-the-device-REAL-residual-only__median__default__both-tail15"
    chunks, _ = fold_chunks("both", "median")
    t = np.concatenate([c[0] for c in chunks])
    X = np.vstack([c[1] for c in chunks])
    m = E.DeviceMethod("pca").fit(t, X)
    d = dict(m.d)
    d.pop("pca_score")
    model = RT.Model(d)
    run = np.nonzero(_running(X))[0]
    tt = t[run]
    cal_raw = model.score(X[run[tt >= tt[0] + (tt[-1] - tt[0]) * 0.85]])[0]
    d["threshold"] = lines_for(cal_raw, 5)
    model = RT.Model(d)
    R = runs()
    out = {n: model.score(r["median"])[0] for n, r in R.items() if n not in WEEKS}
    w = RT.Watcher(model)
    watcher = []
    for tk, x in zip(R["heldout"]["t"], R["heldout"]["median"]):
        w._judge(float(tk), x)
        watcher.append(w.state.get("unknown_score") or 0.0)
    np.savez(OUT / "scores" / f"{key}.npz", cal=cal_raw,
             meta=json.dumps({"key": key, "approach": "PCA (on the device), residual only", "input": "median",
                              "params": {}, "threshold": d["threshold"]}),
             **{f"run__{k}": v for k, v in out.items()})
    return {"key": key, "watcher": np.asarray(watcher, float)}


def bootstrap(P, a, b, n_boot=10000, seed=0) -> dict:
    """Paired bootstrap over the 30 fault runs of lateness(a) - lateness(b), each at its own fixed setting."""
    L, C = [], []
    for r in (a, b):
        meta, cal, raw = load_scores(r["key"])
        row = judge_scores(P, cal, raw, smooth_ns=(r["smooth"],), sens=(r["sensitivity"],), details=True)[0]
        L.append(np.array([row["runs"][n]["lateness"] for n in P.fault_names]))
        C.append({n for n in P.fault_names if row["runs"][n]["caught"]})
    diff = L[0] - L[1]
    rng = np.random.default_rng(seed)
    boots = diff[rng.integers(0, len(diff), (n_boot, len(diff)))].mean(1)
    return {"a": describe(a), "b": describe(b), "lateness_diff": round(float(diff.mean()), 4),
            "ci95": [round(float(np.percentile(boots, 2.5)), 4), round(float(np.percentile(boots, 97.5)), 4)],
            "only_a_caught": sorted(C[0] - C[1]), "only_b_caught": sorted(C[1] - C[0])}


def analysis():
    P = Prepared()
    s1 = json.loads((OUT / "stage1.json").read_text())
    s2 = json.loads((OUT / "stage2.json").read_text())
    s3 = json.loads((OUT / "stage3.json").read_text())
    allrows = s1["candidates"] + s2["candidates"]
    dres = device_residual_only()
    meta, cal, raw = load_scores(dres["key"])
    rows_dres = [{"key": dres["key"], "approach": "PCA (on the device), residual only", "input": "median", "params": {},
                  "real_device": True, **x} for x in judge_scores(P, cal, raw)]
    h = P.runs["heldout"]
    mine = smoothed(raw["heldout"], h["reset"], 5) / lines_for(cal, 5)
    both = np.isfinite(mine) & (dres["watcher"] > 0)
    dres_out = {"watcher_vs_vectorised_max_abs_diff": round(float(np.max(np.abs(np.round(mine[both], 3) - dres["watcher"][both]))), 4),
                "minutes": int(both.sum()), "best": {k: rank(rows_dres)[0][k] for k in SHOW},
                "frontier": frontier_levels(rows_dres)}
    print("device residual-only (int8 runtime, tail-15 line):", fmt_row(rank(rows_dres)[0]),
          "| watcher diff", dres_out["watcher_vs_vectorised_max_abs_diff"], flush=True)
    winner = rank(allrows)[0]
    down = next(r for r in allrows if r["key"] == winner["key"] and r["smooth"] == winner["smooth"]
                and abs(r["sensitivity"] - (winner["sensitivity"] - 0.025)) < 1e-9)
    print("winner:", fmt_row(winner))
    print("one step less sensitive:", fmt_row(down))
    meta, cal, raw = load_scores(winner["key"])
    wdet = judge_scores(P, cal, raw, smooth_ns=(winner["smooth"],), sens=(winner["sensitivity"],), details=True)[0]
    missed = [{"run": n, "kind": P.runs[n]["kind"], "size": "subtle" if P.runs[n]["sev"] < 0.5 else "obvious",
               "slow": P.runs[n]["slow"]} for n in P.fault_names if not wdet["runs"][n]["caught"]]
    others = [e for e in s3["picks"] if e["pick"] != describe(winner)]
    boots = []
    for e in others:
        r = next(x for x in allrows if describe(x) == e["pick"])
        b = bootstrap(P, winner, r)
        boots.append(b)
        print(f"  winner - {b['b'][:70]:70s}: {b['lateness_diff']:+.3f} {b['ci95']} only winner {b['only_a_caught']} "
              f"only other {b['only_b_caught']}")
    curves = {e["pick"]: frontier_levels([x for x in allrows if x["key"] == next(
        y for y in allrows if describe(y) == e["pick"])["key"]], levels=(0, 0.25, 0.5, 1, 2, 4, 8)) for e in s3["picks"]}
    write("analysis.json", {"winner": {k: winner[k] for k in SHOW + ("key", "approach", "input", "params", "missed_runs")},
                            "one_step_less_sensitive": {k: down[k] for k in SHOW + ("missed_runs",)},
                            "winner_runs": wdet["runs"], "winner_missed": missed, "device_residual_only": dres_out,
                            "bootstrap_vs_winner": boots, "tradeoff_curves": curves,
                            "frontier_all": frontier_levels(allrows)})


# ----------------------------------------------------------------- rotation over the three healthy weeks

ROT_KEYS = ("rot_heldout", "rot_w2", "rot_w1")


def _rot_fold(rot):
    return "both" if rot == "rot_heldout" else rot  # learn w1+w2 = the "both" fold (same scores)


def rotation_configs() -> list[tuple]:
    s1 = json.loads((OUT / "stage1.json").read_text())
    s2 = json.loads((OUT / "stage2.json").read_text())
    cfg = []
    for r in s1["best_per_approach_input"] + s2["best_per_setting"][:15]:
        c = (r["approach"], r["input"], json.dumps(r["params"], sort_keys=True))
        if c not in cfg:
            cfg.append(c)
    for v in (0.90, 0.92, 0.93, 0.94, 0.95, 0.96, 0.97, 0.98, 0.99):  # the variance plateau, residual only
        c = ("PCA (device, float)", "median", json.dumps({"max_k": 200, "t2": False, "var": v}, sort_keys=True))
        if c not in cfg:
            cfg.append(c)
    for g in ("mean", "max"):  # group-balanced scores (needs each input's group and spread in the model file)
        for v in (0.95, 0.99):
            cfg.append(("PCA (device, float)", "median", json.dumps({"max_k": 200, "t2": False, "var": v, "group": g}, sort_keys=True)))
        cfg.append(("PCA (device, float)", "max", json.dumps({"max_k": 200, "t2": False, "var": 0.99, "group": g}, sort_keys=True)))
        cfg.append(("Gaussian (Mahalanobis)", "median", json.dumps({"group": g})))
        cfg.append(("autoencoder (device, float)", "median", json.dumps({"group": g})))
    return cfg


def rotation(workers):
    """The device's cycle: record two healthy weeks, learn, watch. Each candidate learns on two of {w1, w2, heldout}
    (line by cross-week out-of-sample scores of those two only) and is judged on the third week plus the fault runs'
    pre-onset minutes (false alerts) and the 30 fault runs (catches). Ranked on the WORST rotation's false alerts,
    then the mean missed, then the mean lateness; also with a plateau rule (the setting and its two sensitivity
    neighbours all good)."""
    calib = "crossfit"
    cfg = rotation_configs()
    jobs = {(c, rot): job(c[0], c[1], json.loads(c[2]), fold=_rot_fold(rot), calib=calib) for c in cfg for rot in ROT_KEYS}
    run_jobs(list(jobs.values()), workers)
    Ps = {rot: Prepared(watch=WATCHED[rot]) for rot in ROT_KEYS}
    cands = []
    per_cfg = {}
    for c in cfg:
        by_rot = {}
        for rot in ROT_KEYS:
            meta, cal, raw = load_scores(jobs[(c, rot)]["key"])
            by_rot[rot] = judge_scores(Ps[rot], cal, raw)
        rows = []
        for i, base in enumerate(by_rot["rot_heldout"]):
            rr = {rot: by_rot[rot][i] for rot in ROT_KEYS}
            assert all(x["smooth"] == base["smooth"] and x["sensitivity"] == base["sensitivity"] for x in rr.values())
            row = {"approach": c[0], "input": c[1], "params": json.loads(c[2]), "smooth": base["smooth"],
                   "sensitivity": base["sensitivity"],
                   "fp_by_rotation": {k: v["fp"] for k, v in rr.items()},
                   "fp_week_by_rotation": {k: v["fp_week"] for k, v in rr.items()},
                   "fp_watched_by_rotation": {k: v["fp_heldout"] for k, v in rr.items()},
                   "fp_preonset_by_rotation": {k: v["fp_preonset"] for k, v in rr.items()},
                   "worst_fp_week": max(v["fp_week"] for v in rr.values()),
                   "mean_fp_week": round(float(np.mean([v["fp_week"] for v in rr.values()])), 3),
                   "missed_by_rotation": {k: v["missed"] for k, v in rr.items()},
                   "mean_missed": round(float(np.mean([v["missed"] for v in rr.values()])), 2),
                   "lateness_by_rotation": {k: v["lateness"] for k, v in rr.items()},
                   "mean_lateness": round(float(np.mean([v["lateness"] for v in rr.values()])), 4),
                   "min_caught": min(v["caught"] for v in rr.values()),
                   "mean_auc": round(float(np.mean([v["mean_auc"] for v in rr.values()])), 4),
                   "line_by_rotation": {k: v["line"] for k, v in rr.items()},
                   "removed_by_new_start_rule": {k: v["removed_by_new_start_rule"] for k, v in rr.items()},
                   **{f"heldout_rot_{k}": base[k] for k in ("caught", "subtle", "slow_caught", "slow_wear_caught_median",
                                                            "sudden_subtle_caught", "sudden_median_min",
                                                            "median_min_caught", "missed_runs")},
                   "missed_runs_by_rotation": {k: v["missed_runs"] for k, v in rr.items()}}
            rows.append(row)
        per_cfg[c] = rows
        cands += rows
    idx = {(r["approach"], r["input"], json.dumps(r["params"], sort_keys=True), r["smooth"], r["sensitivity"]): r
           for r in cands}
    for r in cands:  # plateau: the setting and its sensitivity neighbours one step either side
        c = (r["approach"], r["input"], json.dumps(r["params"], sort_keys=True), r["smooth"])
        nb = [idx.get(c + (round(r["sensitivity"] + d, 3),)) for d in (-0.025, 0.0, 0.025)]
        nb = [x for x in nb if x]
        r["plateau_worst_fp_week"] = max(x["worst_fp_week"] for x in nb)
        r["plateau_mean_missed"] = max(x["mean_missed"] for x in nb)
    ok = [r for r in cands if r["min_caught"] > 0]
    strict = sorted(ok, key=lambda r: (r["worst_fp_week"], r["mean_missed"], r["mean_lateness"], -r["mean_auc"]))
    plateau = sorted(ok, key=lambda r: (r["plateau_worst_fp_week"], r["plateau_mean_missed"], r["mean_missed"],
                                        r["mean_lateness"], -r["mean_auc"]))

    def best_each(order):
        seen, out = set(), []
        for r in order:
            g = (r["approach"], r["input"])
            if g not in seen:
                seen.add(g)
                out.append(r)
        return out

    def neighbours(r):
        c = (r["approach"], r["input"], json.dumps(r["params"], sort_keys=True))
        out = []
        for n in SMOOTHINGS:
            for d in (-0.05, -0.025, 0.0, 0.025, 0.05):
                x = idx.get(c + (n, round(r["sensitivity"] + d, 3)))
                if x and (n == r["smooth"] or d == 0.0):
                    out.append({k: x[k] for k in ("smooth", "sensitivity", "fp_by_rotation", "worst_fp_week",
                                                  "mean_missed", "mean_lateness")})
        return out

    frontier = []
    for L in FP_LEVELS:
        pool = [r for r in ok if r["worst_fp_week"] <= L + 1e-9]
        if pool:
            b = min(pool, key=lambda r: (r["mean_missed"], r["mean_lateness"]))
            frontier.append({"level_worst_fp_week": L, "candidate": describe(b), **{k: b[k] for k in (
                "fp_by_rotation", "worst_fp_week", "mean_missed", "missed_by_rotation", "mean_lateness",
                "heldout_rot_slow_wear_caught_median", "heldout_rot_sudden_median_min")}})
    var_plateau = []
    for v in (0.90, 0.92, 0.93, 0.94, 0.95, 0.96, 0.97, 0.98, 0.99):
        rs = [r for r in ok if r["approach"] == "PCA (device, float)" and r["input"] == "median"
              and r["params"] == {"max_k": 200, "t2": False, "var": v}]
        b = sorted(rs, key=lambda r: (r["worst_fp_week"], r["mean_missed"], r["mean_lateness"]))[0]
        var_plateau.append({"var": v, "smooth": b["smooth"], "sensitivity": b["sensitivity"], "worst_fp_week": b["worst_fp_week"],
                            "mean_missed": b["mean_missed"], "mean_lateness": b["mean_lateness"],
                            "missed_by_rotation": b["missed_by_rotation"]})
    W, Wp = strict[0], plateau[0]
    down = idx.get((W["approach"], W["input"], json.dumps(W["params"], sort_keys=True), W["smooth"],
                    round(W["sensitivity"] - 0.025, 3)))
    print("strict winner :", describe(W), W["fp_by_rotation"], W["mean_missed"], W["mean_lateness"])
    print("plateau winner:", describe(Wp), Wp["fp_by_rotation"], Wp["mean_missed"], Wp["mean_lateness"])
    print("one step down :", describe(down) if down else None, down and down["fp_by_rotation"], down and down["mean_missed"])
    print("\nbest per approach x input (strict rule):")
    for r in best_each(strict)[:20]:
        print(f"  {describe(r):80s} fp {r['fp_by_rotation']} worst {r['worst_fp_week']:.2f} | missed "
              f"{r['missed_by_rotation']} mean {r['mean_missed']:.1f} | late {r['mean_lateness']:.3f}")
    print("\nfrontier (worst rotation):")
    for f in frontier:
        print(f"  <= {f['level_worst_fp_week']}: {f['candidate'][:80]:80s} missed {f['missed_by_rotation']} late {f['mean_lateness']:.3f}")
    print("\nresidual-only PCA by variance kept:")
    for v in var_plateau:
        print(f"  {v}")
    write("rotation.json", {"rule": "worst rotation's false alerts per running week (no slack), then mean missed of 30, "
                                    "then mean lateness; candidates catching nothing in a rotation are out",
                            "rotations": {k: {"learn": ROTATIONS[k], "watch": WATCHED[k]} for k in ROT_KEYS},
                            "watched_running_minutes": {k: Ps[k].held_min for k in ROT_KEYS},
                            "preonset_running_minutes": {k: Ps[k].pre_min for k in ROT_KEYS},
                            "strict_winner": W, "strict_winner_neighbours": neighbours(W), "one_step_less_sensitive": down,
                            "plateau_winner": Wp, "plateau_winner_neighbours": neighbours(Wp),
                            "best_per_approach_input_strict": best_each(strict),
                            "best_per_approach_input_plateau": best_each(plateau),
                            "top20_strict": strict[:20], "frontier": frontier, "pca_residual_variance": var_plateau,
                            "configs": [list(c) for c in cfg], "candidates": cands})


PERSIST_B = (30, 60, 120, 360)
PERSIST_FRAC = (0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0)
PERSIST_HOLD = (1, 2, 3, 4, 6)
PERSIST_SKIP = {("PCA (device, float)", "median", json.dumps({"group": "mean", "max_k": 200, "t2": False, "var": 0.95}, sort_keys=True)),
                ("Gaussian (Mahalanobis)", "median", json.dumps({"group": "mean"}, sort_keys=True))}  # sound-fix's share


def persist_flags(P, raw, line, B):
    """Long persistence, exactly as effente.early_warning.Line / early_warning_search.blocks: blocks of B consecutive
    device-running minutes per run (spanning stops), each block's median of score / line; a counter of consecutive
    blocks over frac, on when >= hold; a block's verdict holds from its last minute until the next block closes, the
    run's last block until the run's end. Columns: PERSIST_FRAC x PERSIST_HOLD."""
    K = len(PERSIST_FRAC) * len(PERSIST_HOLD)
    out = {}
    for name, d in P.runs.items():
        on = np.zeros((len(d["t"]), K), bool)
        r = np.nonzero(d["reset"])[0]
        nb = len(r) // B
        if nb:
            med = np.median((np.asarray(raw[name], float)[r[:nb * B]] / line).reshape(nb, B), 1)
            ends = r[B - 1::B][:nb]
            nxt = list(ends[1:]) + [len(d["t"])]
            col = 0
            for f in PERSIST_FRAC:
                above = med > f
                runlen = np.zeros(nb, int)
                c = 0
                for i, a_ in enumerate(above):
                    c = c + 1 if a_ else 0
                    runlen[i] = c
                for h in PERSIST_HOLD:
                    for i in np.nonzero(runlen >= h)[0]:
                        on[ends[i]:nxt[i], col] = True
                    col += 1
        out[name] = on
    return out


def persistence():
    """Long persistence as the model's own alarm (no smoothing / sensitivity), in the rotation with the cross-week
    line (lines_for(cal, 10)): does it beat the minute alarm, or is it just more smoothing? Also OR'ed with each
    configuration's best minute alarm. Ranked like the rotation (worst rotation's false alerts, mean missed,
    mean lateness). sound-fix runs the same on PERSIST_SKIP."""
    rot = json.loads((OUT / "rotation.json").read_text())
    cfg = [tuple(c) for c in rot["configs"] if tuple(c) not in PERSIST_SKIP]
    Ps = {r: Prepared(watch=WATCHED[r]) for r in ROT_KEYS}
    best_alone = {}
    for r in rot["candidates"]:
        c = (r["approach"], r["input"], json.dumps(r["params"], sort_keys=True))
        if r["min_caught"] > 0:
            k = (r["worst_fp_week"], r["mean_missed"], r["mean_lateness"])
            if c not in best_alone or k < best_alone[c][0]:
                best_alone[c] = (k, r["smooth"], r["sensitivity"])
    rows, t0 = [], time.time()
    labels = [(f, h) for f in PERSIST_FRAC for h in PERSIST_HOLD]
    for c in cfg:
        per = {}
        for rk in ROT_KEYS:
            meta, cal, raw = load_scores(job(c[0], c[1], json.loads(c[2]), fold=_rot_fold(rk), calib="crossfit")["key"])
            line = lines_for(cal, 10)
            _, n, s = best_alone.get(c, (None, 10, 0.5))
            _, ratio = ratios(Ps[rk], cal, raw, n)
            Mb = model_flags(Ps[rk], ratio, (s,))
            for B in PERSIST_B:
                F_ = persist_flags(Ps[rk], raw, line, B)
                per.setdefault(("alone", B), {})[rk] = judge_flags(Ps[rk], F_)
                per.setdefault(("or_best_minute_alarm", B), {})[rk] = judge_flags(Ps[rk], {nm: F_[nm] | Mb[nm] for nm in F_})
        for (mode, B), d in per.items():
            for i, (f, h) in enumerate(labels):
                rr = {rk: d[rk][i] for rk in ROT_KEYS}
                rows.append({"approach": c[0], "input": c[1], "params": json.loads(c[2]), "mode": mode, "block_min": B,
                             "frac": f, "hold": h, "minute_alarm": None if mode == "alone" else list(best_alone.get(c, (None, 10, 0.5))[1:]),
                             "fp_by_rotation": {k: v["fp"] for k, v in rr.items()},
                             "worst_fp_week": max(v["fp_week"] for v in rr.values()),
                             "missed_by_rotation": {k: v["missed"] for k, v in rr.items()},
                             "mean_missed": round(float(np.mean([v["missed"] for v in rr.values()])), 2),
                             "mean_lateness": round(float(np.mean([v["lateness"] for v in rr.values()])), 4),
                             "min_caught": min(v["caught"] for v in rr.values()),
                             "heldout_rot": _keep(rr["rot_heldout"])})
        print(f"  {c[0][:28]:28s} {c[1]:9s} {c[2][:50]:50s} ({time.time() - t0:.0f} s)", flush=True)
    key3 = lambda r: (r["worst_fp_week"], r["mean_missed"], r["mean_lateness"])
    ok = sorted((r for r in rows if r["min_caught"] > 0), key=key3)
    per_cfg = []
    for c in cfg:
        a_ = best_alone.get(c)
        pa = [r for r in ok if (r["approach"], r["input"], json.dumps(r["params"], sort_keys=True)) == c and r["mode"] == "alone"]
        po = [r for r in ok if (r["approach"], r["input"], json.dumps(r["params"], sort_keys=True)) == c and r["mode"] != "alone"]
        per_cfg.append({"config": list(c), "minute_alarm_best": None if not a_ else {"worst_fp_week": a_[0][0], "mean_missed": a_[0][1],
                                                                                      "mean_lateness": a_[0][2], "smooth": a_[1], "sensitivity": a_[2]},
                        "persistence_alone_best": pa[0] if pa else None, "or_best": po[0] if po else None})
    per_cfg.sort(key=lambda e: key3(e["or_best"]) if e["or_best"] else (9e9,))
    for e in per_cfg[:20]:
        m, pa, po = e["minute_alarm_best"], e["persistence_alone_best"], e["or_best"]
        print(f"  {' | '.join(e['config'])[:70]:70s} minute {m and (m['worst_fp_week'], m['mean_missed'])} | persist "
              f"{pa and (pa['worst_fp_week'], pa['mean_missed'], pa['block_min'], pa['frac'], pa['hold'])} | OR "
              f"{po and (po['worst_fp_week'], po['mean_missed'], po['block_min'], po['frac'], po['hold'])}")
    write("persistence.json", {"note": persistence.__doc__, "grid": {"block_min": PERSIST_B, "frac": PERSIST_FRAC, "hold": PERSIST_HOLD},
                               "skipped_for_sound_fix": [list(c) for c in PERSIST_SKIP], "per_config": per_cfg,
                               "top20": ok[:20]})


def naming():
    """Fault naming, as a tie-breaker: for candidates on the device's own models (PCA, its residual-only scoring,
    the autoencoder), what the screen would say for the first caught stretch (effente.fault_types.describe on the
    device model's deviations, the symptom said most often in the stretch's first 30 minutes), and whether it fits
    the fault (evaluate.EXPECTED). The alarm minutes are the candidate's own (rotation learn w1+w2); the wording comes
    from the real int8 model trained on the same two weeks (effente.trainer.train_model, PCA at 0.95 / autoencoder at
    its default size, so a candidate with another size is named by the default-size model)."""
    from collections import Counter
    from effente import fault_types as FT
    rot = json.loads((OUT / "rotation.json").read_text())
    P = Prepared()
    R = runs()
    chunks, _ = fold_chunks("both", "median")
    t = np.concatenate([c[0] for c in chunks])
    X = np.vstack([c[1] for c in chunks])
    models = {}
    for kind in ("pca", "autoencoder"):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            models[kind] = E.DeviceMethod(kind).fit(t, X).model
    picks = [r for r in rot["best_per_approach_input_strict"] if r["approach"] in ("PCA (device, float)",
                                                                                   "autoencoder (device, float)")]
    picks += [r for r in rot["candidates"] if r["approach"] == "PCA (device, float)" and r["input"] == "median"
              and r["params"] == {"max_k": 200, "t2": False, "var": 0.95}][:0]
    out = []
    for r in picks:
        if r["input"] != "median":
            continue
        m = models["pca" if "PCA" in r["approach"] else "autoencoder"]
        j = job(r["approach"], r["input"], r["params"], fold="both", calib="crossfit")
        meta, cal, raw = load_scores(j["key"])
        line, ratio = ratios(P, cal, raw, r["smooth"])
        flags = model_flags(P, ratio, (r["sensitivity"],))
        said = {}
        for n in P.fault_names:
            d = P.runs[n]
            A = flags[n][:, 0] & d["run"]
            ri = d["ridx"]
            Ar = A[ri]
            prev = np.r_[False, Ar[:-1]]
            st = np.nonzero(Ar & ~prev & d["win"][ri])[0]
            if not len(st):
                continue
            k0 = st[0]
            stretch = [ri[k] for k in range(k0, min(k0 + 30, len(ri))) if Ar[k]]
            s_, r_, Z_ = m.score(R[n]["median"][stretch])
            words = Counter(FT.describe(m.deviations(r_[i:i + 1], Z_[i:i + 1]))[0][0] for i in range(len(stretch)))
            w = words.most_common(1)[0][0]
            said[n] = {"said": w, "fits": w in E.EXPECTED[d["kind"]]}
        fits = sum(v["fits"] for v in said.values())
        out.append({"candidate": describe(r), "caught_rot_heldout": len(said), "said_fits": fits, "runs": said})
        print(f"  {describe(r):80s} said fits {fits}/{len(said)}")
    # The Gaussian: conditional residuals (GaussGroups.resid) through the device's own deviation rule
    # (effente.model_runtime.Model.deviation_vector: a reading counts as far as both its residual, in spreads of its
    # healthy residual, and its distance from usual agree; 6 spreads or more from usual counts as it is) and
    # effente.fault_types.describe, the wording path the device's Watcher uses.
    from effente.model_runtime import FAR_SPREADS
    gpicks = [r for r in rot["best_per_approach_input_strict"] if r["approach"] == "Gaussian (Mahalanobis)" and r["input"] == "median"]
    best_groups = {}
    for r in sorted((r for r in rot["candidates"] if r["approach"] == "Gaussian (Mahalanobis)" and r["input"] == "median"
                     and r["params"].get("group") and r["min_caught"] > 0),
                    key=lambda r: (r["worst_fp_week"], r["mean_missed"], r["mean_lateness"])):
        best_groups.setdefault(r["params"]["group"], r)
    gpicks += list(best_groups.values())
    if gpicks:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            prep, _, _ = calibrated_fit("Gaussian (Mahalanobis)", {}, "median", "both", "crossfit")
            Xtr = np.vstack([c[1] for c in chunks])
            Ztr = prep.transform(Xtr[_running(Xtr)])
            g = GaussGroups("mean")
            g.names = prep.names_out
            g.fit(Ztr)
        ncol = len(prep.columns)
        rs = g.resid(Ztr)[:, :ncol].std(0)
        rs = np.where(rs > 1e-4, rs, 1e-4)
        for r in gpicks:
            j = job(r["approach"], r["input"], r["params"], fold="both", calib="crossfit")
            meta, cal, raw = load_scores(j["key"])
            line, ratio = ratios(P, cal, raw, r["smooth"])
            flags = model_flags(P, ratio, (r["sensitivity"],))
            said = {}
            for n in P.fault_names:
                d = P.runs[n]
                ri = d["ridx"]
                Ar = (flags[n][:, 0] & d["run"])[ri]
                st = np.nonzero(Ar & ~np.r_[False, Ar[:-1]] & d["win"][ri])[0]
                if not len(st):
                    continue
                stretch = [ri[k] for k in range(st[0], min(st[0] + 30, len(ri))) if Ar[k]]
                Z = prep.transform(R[n]["median"][stretch])
                rr = g.resid(Z)[:, :ncol] / rs
                Zc = Z[:, :ncol]
                dev = np.where(np.sign(rr) == np.sign(Zc), np.sign(rr) * np.minimum(np.abs(rr), np.abs(Zc)), 0.0)
                dev = np.where(np.abs(Zc) >= FAR_SPREADS, Zc, dev)
                words = Counter(FT.describe(dict(zip(prep.columns, map(float, v))))[0][0] for v in dev)
                w = words.most_common(1)[0][0]
                said[n] = {"said": w, "fits": w in E.EXPECTED[d["kind"]]}
            fits = sum(v["fits"] for v in said.values())
            out.append({"candidate": describe(r), "caught_rot_heldout": len(said), "said_fits": fits, "runs": said,
                        "how": "Gaussian conditional residuals through the device's deviation rule and describe()"})
            print(f"  {describe(r):80s} said fits {fits}/{len(said)}")
    write("naming.json", {"note": naming.__doc__, "results": out,
                          "bench_models": "Gaussian named via its conditional residuals (above). Not named: kNN, forest, "
                                          "mixture and max |z| (no per-reading residuals); bench PCA and the sklearn "
                                          "autoencoder could be, the same way, but were not"})


def final_model():
    """The model to ship, learned on all three healthy weeks (line by leave-one-week-out scores). For reference only:
    no unseen healthy week is left to judge it on."""
    rot = json.loads((OUT / "rotation.json").read_text())
    picks = [rot["plateau_winner"], rot["strict_winner"]]
    picks += [r for r in rot["best_per_approach_input_strict"] if r["approach"] == "PCA (device, float)" and r["input"] == "median"][:2]
    out = []
    seen = set()
    for r in picks:
        d = describe(r)
        if d in seen:
            continue
        seen.add(d)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            prep, det, cal = calibrated_fit(r["approach"], r["params"], r["input"], "all3", "crossfit")
        line = lines_for(cal, r["smooth"])
        m = SF.margins(r["sensitivity"])["model"]
        out.append({"candidate": d, "inputs": prep.n_out, "line": line, "margin": m, "alarm_above": line * m,
                    "patterns_kept": getattr(det, "k", None),
                    "line_by_rotation": r["line_by_rotation"]})
        print(f"  {d:80s} line {line:.4g} x margin {m:.3f} = alarm above {line * m:.4g} (rotations: {r['line_by_rotation']})")
    write("final_model.json", {"note": final_model.__doc__, "models": out})


# ----------------------------------------------------------------- combined: model alarm OR early warning

def _ew_model_file(model):
    return re.sub(r"[()|]", "", model).replace(" ", "_")


MODEL_FREE = ("CUSUM per reading (no model)",)


def _read_ew(jl: Path, P) -> list:
    """[(key, {run: per-minute early-warning flags})] of one early_warning_search.py file pair, checked to be on our
    minute grid (labels minute_t0) for every run P judges."""
    keys = [json.loads(line)["key"] for line in jl.read_text().splitlines() if line.strip()]
    with np.load(jl.with_suffix(".npz"), allow_pickle=False) as d:
        names = [str(x) for x in d["run_names"]]
        bounds = d["bounds"].astype(int)
        minutes = int(d["minutes"])
        mt0 = d["minute_t0"]
        flags = d["flags"]
    assert len(bounds) == len(names) + 1 and len(keys) == len(flags), (jl.name, len(names), len(keys))
    span = {n: (bounds[j], bounds[j + 1]) for j, n in enumerate(names)}
    for n in P.runs:
        a_, b_ = span[n]
        assert np.array_equal(mt0[a_:b_].astype(float), P.runs[n]["t"]), (jl.name, n)
    out = []
    for i, k in enumerate(keys):
        bits = np.unpackbits(flags[i])[:minutes].astype(bool)
        out.append((k, {n: bits[a_:b_] for n, (a_, b_) in span.items() if n in P.runs}))
    return out


def ew_sets(P) -> dict:
    """The learn-w1+w2 early-warning sets: {evaluate.methods() name: [(key, flags)]}."""
    out = {}
    for jl in sorted(EW_DIR.glob("ew_*.jsonl")):
        if jl.name.startswith("ew_rot__") or not jl.with_suffix(".npz").exists():
            continue
        sets = _read_ew(jl, P)
        if sets:
            out[sets[0][0]["model"]] = sets
    return out


def ew_rot_sets(Ps) -> dict:
    """Early warnings run in the rotations: {(candidate identity, rotation): [(key, flags)]}; candidate identity is
    the approach for the no-model CUSUM, else (approach, input, params, smooth) as early-warning keyed it."""
    out = {}
    for jl in sorted(EW_DIR.glob("ew_rot__*.jsonl")):
        if not jl.with_suffix(".npz").exists():
            continue
        rot = jl.stem.rsplit("__", 1)[1]
        if rot not in Ps:
            continue
        sets = _read_ew(jl, Ps[rot])
        if not sets:
            continue
        c = sets[0][0]["candidate"]
        ident = c["approach"] if c["approach"] in MODEL_FREE else \
            (c["approach"], c.get("input"), json.dumps(c.get("params", {}), sort_keys=True), c.get("smooth"))
        out[(ident, rot)] = sets
    return out


def _ew_desc(k):
    m = k.get("model") or k.get("candidate", {}).get("approach")
    return f"{m} / {k['early']} / block {k['block']} / " + ",".join(f"{a}={b}" for a, b in sorted(k["params"].items()))


def _ew_ident(k):
    return (k.get("early"), k.get("block"), json.dumps(k.get("params", {}), sort_keys=True))


def _keep(row):
    return {k: row[k] for k in SHOW + ("missed_runs",) if k in row}


def combined():
    """The device's alert as agreed: the model's alarm OR the early warning, per minute; false alerts and catches
    counted on the OR'ed flags (overlapping stretches count once), safety checks not included.
      A. Rotations (the headline): every rotation candidate OR each early warning early-warning ran in that rotation
         (the no-model CUSUM for every candidate; its model-dependent ones for the candidates it was given),
         ranked like the model alone: worst rotation's false alerts, then mean missed, then mean lateness.
      B. Learn w1+w2 only, every early-warning setting: the model-dependent early warnings paired with exactly the
         model they were computed on (evaluate.methods(), tail-15 line: the bench's split by rows, the device's own
         trainer); the no-model CUSUM with every candidate."""
    rot = json.loads((OUT / "rotation.json").read_text())
    cfg = [tuple(c) for c in rot["configs"]]
    Ps = {r: Prepared(watch=WATCHED[r]) for r in ROT_KEYS}
    P = Ps["rot_heldout"]
    # ---- A: rotations
    rsets = ew_rot_sets(Ps)
    print("early-warning rotation sets:", {f"{k[0] if isinstance(k[0], str) else k[0][0]} | {k[1]}": len(v)
                                          for k, v in rsets.items()}, flush=True)
    rows_a = {}  # (config, n, ew ident) -> {rot: [row per sens]}
    alone_a = {}
    for c in cfg:
        for rk in ROT_KEYS:
            j = job(c[0], c[1], json.loads(c[2]), fold=_rot_fold(rk), calib="crossfit")
            meta, cal, raw = load_scores(j["key"])
            evs = list(rsets.get((MODEL_FREE[0], rk), []))
            for ident, sets in rsets.items():
                if ident[1] == rk and not isinstance(ident[0], str) and ident[0][:3] == c:
                    evs += [(k, per, ident[0][3]) for k, per in sets]
            for n in SMOOTHINGS:
                line, ratio = ratios(Ps[rk], cal, raw, n)
                M = model_flags(Ps[rk], ratio, SENS)
                alone_a.setdefault((c, n), {})[rk] = judge_flags(Ps[rk], M)
                for e in evs:
                    k, per = e[0], e[1]
                    if len(e) == 3 and e[2] is not None and e[2] != n:
                        continue  # a candidate-specific early warning goes with its candidate's smoothing
                    rows_a.setdefault((c, n, _ew_ident(k), _ew_desc(k)), {})[rk] = judge_flags(
                        Ps[rk], {nm: M[nm] | per[nm][:, None] for nm in M})

    def merge(c, n, by_rot, ew=None):
        out = []
        for i, s in enumerate(SENS):
            rr = {rk: by_rot[rk][i] for rk in ROT_KEYS}
            out.append({"approach": c[0], "input": c[1], "params": json.loads(c[2]), "smooth": n, "sensitivity": s,
                        "ew": ew, "fp_by_rotation": {k: v["fp"] for k, v in rr.items()},
                        "worst_fp_week": max(v["fp_week"] for v in rr.values()),
                        "missed_by_rotation": {k: v["missed"] for k, v in rr.items()},
                        "mean_missed": round(float(np.mean([v["missed"] for v in rr.values()])), 2),
                        "mean_lateness": round(float(np.mean([v["lateness"] for v in rr.values()])), 4),
                        "min_caught": min(v["caught"] for v in rr.values()),
                        "removed_by_new_start_rule": {k: v["removed_by_new_start_rule"] for k, v in rr.items()},
                        "heldout_rot": _keep(rr["rot_heldout"]),
                        "missed_runs_by_rotation": {k: v["missed_runs"] for k, v in rr.items()}})
        return out

    alone = [x for (c, n), d in alone_a.items() for x in merge(c, n, d)]
    comb = [x for (c, n, _, ew), d in rows_a.items() if len(d) == 3 for x in merge(c, n, d, ew)]
    key3 = lambda r: (r["worst_fp_week"], r["mean_missed"], r["mean_lateness"])
    ok = lambda rows: sorted((r for r in rows if r["min_caught"] > 0), key=key3)

    def front(rows):
        out = []
        for L in FP_LEVELS:
            pool = [r for r in rows if r["min_caught"] > 0 and r["worst_fp_week"] <= L + 1e-9]
            if pool:
                b_ = min(pool, key=lambda r: (r["mean_missed"], r["mean_lateness"]))
                out.append({"level_worst_fp_week": L, "candidate": describe(b_), "fp_by_rotation": b_["fp_by_rotation"],
                            "mean_missed": b_["mean_missed"], "missed_by_rotation": b_["missed_by_rotation"],
                            "mean_lateness": b_["mean_lateness"]})
        return out

    idx_alone = {(r["approach"], r["input"], json.dumps(r["params"], sort_keys=True), r["smooth"], r["sensitivity"]): r
                 for r in alone}
    avc = []
    for g in dict.fromkeys((r["approach"], r["input"]) for r in alone):
        a_ = ok([r for r in alone if (r["approach"], r["input"]) == g])
        c_ = ok([r for r in comb if (r["approach"], r["input"]) == g])
        if not a_ or not c_:
            continue
        a0, c0 = a_[0], c_[0]
        same = idx_alone[(c0["approach"], c0["input"], json.dumps(c0["params"], sort_keys=True), c0["smooth"], c0["sensitivity"])]
        only_ew = {rk: sorted(set(same["missed_runs_by_rotation"][rk]) - set(c0["missed_runs_by_rotation"][rk]))
                   for rk in ROT_KEYS}
        avc.append({"model": f"{g[0]} | {g[1]}", "alone": a0, "alone_setting": describe(a0), "combined": c0,
                    "combined_setting": describe(c0), "same_setting_alone": same, "only_early_warning_caught": only_ew})
    avc.sort(key=lambda e: key3(e["combined"]))
    print("\nA. rotations, alone vs combined (worst-rotation false alerts, mean missed, mean lateness):")
    for e in avc:
        a0, c0 = e["alone"], e["combined"]
        print(f"  {e['model'][:44]:44s} alone {a0['worst_fp_week']:.2f}/{a0['mean_missed']:.1f}/{a0['mean_lateness']:.3f} | "
              f"combined {c0['worst_fp_week']:.2f}/{c0['mean_missed']:.1f}/{c0['mean_lateness']:.3f} with {c0['ew']}")
    rank_c, rank_a = ok(comb), ok(alone)
    print("\ncombined winner:", describe(rank_c[0]) if rank_c else None)
    # ---- B: learn w1+w2, every early-warning setting
    sets = ew_sets(P)
    print("\nearly-warning sets (learn w1+w2):", {m: len(v) for m, v in sets.items()}, flush=True)
    real = {"PCA (on the device)": "v2__" + key_of("PCA (on the device) REAL", "median", {}, "both-tail15"),
            "autoencoder (on the device)": "v2__" + key_of("autoencoder (on the device) REAL", "median", {}, "both-tail15")}
    exact = {m: real.get(m) or key_of(m, "median", {}, "default") for m in sets if m not in MODEL_FREE}
    rows_b = []
    t0 = time.time()
    for m, key in exact.items():
        if not (OUT / "scores" / f"{key}.npz").exists():
            print(f"  no tail-15 scores for {m} ({key})")
            continue
        meta, cal, raw = load_scores(key)
        pair = sets[m] + [x for mf in MODEL_FREE for x in sets.get(mf, [])]
        for n in SMOOTHINGS:
            line, ratio = ratios(P, cal, raw, n)
            M = model_flags(P, ratio, SENS)
            base = {"approach": m, "input": "median", "params": {}, "smooth": n, "line": "tail-15 (evaluate.methods())",
                    "mean_auc": 0.0}
            for s_, row in zip(SENS, judge_flags(P, M)):
                rows_b.append({**base, "sensitivity": s_, "ew": None, **_keep(row)})
            for k, per in pair:
                for s_, row in zip(SENS, judge_flags(P, {nm: M[nm] | per[nm][:, None] for nm in M})):
                    if row["fp_week"] <= max(FP_LEVELS) + 1e-9:
                        rows_b.append({**base, "sensitivity": s_, "ew": _ew_desc(k), **_keep(row)})
        print(f"  {m:32s} with {len(pair)} early warnings ({time.time() - t0:.0f} s)", flush=True)
    ew_alone = []
    for m, ss in sets.items():
        for k, per in ss:
            ew_alone.append({"ew": _ew_desc(k), **_keep(judge_flags(P, {n: f[:, None] for n, f in per.items()})[0])})
    avc_b = []
    for m in exact:
        a_ = rank([r for r in rows_b if r["approach"] == m and r["ew"] is None])
        c_ = rank([r for r in rows_b if r["approach"] == m and r["ew"]])
        if a_ and c_:
            same = next(r for r in rows_b if r["approach"] == m and r["ew"] is None and r["smooth"] == c_[0]["smooth"]
                        and r["sensitivity"] == c_[0]["sensitivity"])
            avc_b.append({"model": m, "alone": a_[0], "combined": c_[0],
                          "only_early_warning_caught": sorted(set(same["missed_runs"]) - set(c_[0]["missed_runs"]))})
    for e in avc_b:
        print(f"  B {e['model'][:30]:30s} alone fp {e['alone']['fp']} missed {e['alone']['missed']} late {e['alone']['lateness']:.3f}"
              f" | combined fp {e['combined']['fp']} missed {e['combined']['missed']} late {e['combined']['lateness']:.3f} "
              f"({e['combined']['ew']}) only EW {e['only_early_warning_caught']}")
    write("combined.json", {
        "rule": "OR of model alarm and early warning per minute; false alerts = stretches of the OR (new after each stop) "
                "in the watched week + pre-onset; catches = OR stretches starting in the window, not on at the previous "
                "running minute; ranked by worst rotation's false alerts, mean missed, mean lateness; no safety checks",
        "A_rotations": {"early_warning_sets": {f"{k[0] if isinstance(k[0], str) else '|'.join(map(str, k[0]))} | {k[1]}": len(v)
                                               for k, v in rsets.items()},
                        "winner_combined": rank_c[0] if rank_c else None, "winner_alone": rank_a[0] if rank_a else None,
                        "top10_combined": rank_c[:10], "frontier_combined": front(comb), "frontier_alone": front(alone),
                        "alone_vs_combined": avc},
        "B_learn_w1w2": {"early_warning_sets": {m: len(v) for m, v in sets.items()}, "alone_vs_combined": avc_b,
                         "frontier_combined": frontier_levels([r for r in rows_b if r["ew"]]),
                         "frontier_alone": frontier_levels([r for r in rows_b if not r["ew"]]),
                         "early_warning_alone_best": sorted(ew_alone, key=lambda r: (r["fp"], r["missed"], r["lateness"]))[:10]}})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stage", choices=("cache", "calibration", "stage1", "stage2", "stage3", "analysis", "combined",
                                      "rotation", "naming", "final", "persistence"))
    ap.add_argument("--data", default=str(DATA))
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args(argv)
    {"cache": lambda: build_cache(Path(args.data), min(args.workers, 3)),
     "calibration": lambda: calibration(args.workers), "stage1": lambda: stage1(args.workers),
     "stage2": lambda: stage2(args.workers), "stage3": lambda: stage3(args.workers),
     "analysis": analysis, "combined": combined, "rotation": lambda: rotation(args.workers), "naming": naming,
     "final": final_model, "persistence": persistence}[args.stage]()
    return 0


if __name__ == "__main__":
    sys.exit(main())
