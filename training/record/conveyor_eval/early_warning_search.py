#!/usr/bin/env python3
"""The early-warning half of the conveyor search: every early-warning method, with its own settings and
block length, on every normal model, inside the whole device (model alarm + safety checks + early
warning), at every model sensitivity. Which single setting catches faults earliest with the fewest
false alerts?

Learned from the healthy weeks only (normal-w1 and normal-w2, per-minute medians, trend features of a week
or more left out: evaluate.minutes_of). Judged on running minutes, the first 6 h of each run not judged:
  false alerts   alert stretches that begin in the held-out healthy week, or before the onset in a fault
                 run, per week of calendar time judged (the ranking's first measure)
  creeping       idler_bearing, gear_wear, motor_bearing, lubrication_loss: the wear at the first alert
                 that begins after the onset (evaluate.wear_level), as a share of the run's final size
                 (a miss counts as 1); also as a % of the obvious fault's full size
  sudden         the other 11: subtle ones caught at all, then minutes from the onset
The device alerts while any of these is on:
  the model's alarm  the score over its line (the device's Watcher level, or evaluate.BenchMethod's) above
                     effente.safety.margins(s)["model"]; s = "off" leaves the alarm out
  the safety checks  effente.safety on the one-a-second rows, at the same sensitivity (0.5 with "off"),
                     against the learned baseline (early_warning_eval.safety_active)
  early warning      effente.early_warning's detectors (Cusum, Ewma, Line; the trend test vectorised the same
                     way as early_warning.Trend) on blocks of BLOCKS running minutes, the group series from
                     the model's per-reading deviations (Model.deviation_vector for the device's two; for the
                     bench models the same rule on their residuals, or Z where a model gives none), against
                     the healthy weeks (early_warning.calibrate_values)
The "sensitivity" early-warning method is the model's own alarm at a higher sensitivity: in the whole
device that is the model-sensitivity axis itself (MODEL_GRID), so it is searched there.

An 11th approach has no model: a CUSUM on every prepared reading's z (Prep fit on the healthy weeks), both
directions, per minute or on block medians, alerting when any reading's sum passes the limit; next to it,
max |z| (evaluate's bench "max |z| (no model)").

Ranking (the user's, 13 Nov): the fewest false alerts per running week first, with no slack (among settings
that catch anything), then the fewest missed faults (of 30), then the earliest over all 30 with misses counted
as the latest (Metrics).
Ranked WITHOUT the current safety checks (their sound check fires on every passing forklift); the same
setting with them is shown beside it, and the sound check's fixes are searched separately (--stage sound). One run per fault, and the settings are picked on the same runs they are judged on, so the top
few are checked again: false alerts when learning on one healthy week and watching the other, and how often
they stay on top when the fault kinds are resampled.

    python3 -m training.record.conveyor_eval.early_warning_search --data data/fake/conveyor-eval \\
        --out data/fake/early-warning-search --workers 4
"""

from __future__ import annotations

import os

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_v, "1")  # one core per worker: the main session's search runs alongside

import argparse
import itertools
import json
import math
import sys
import time
import warnings
from collections import Counter
from multiprocessing import get_context
from pathlib import Path

import numpy as np

from effente import early_warning as EW
from effente import fault_types as FT
from effente import features as F
from effente import model_runtime as RT
from effente import safety as SF
from effente.model_input import Prep
from training.record.conveyor_eval import early_warning_eval as EV
from training.record.conveyor_eval import evaluate as E

SLOW = E.SLOW_FAULTS
WEEK = 7 * 86400.0
RUNNING_WEEK = 10080  # minutes: false alerts are counted per running week (episodes / judged running minutes x 10080)
WINDOW_PAD_S = 1800.0  # a fault's window ends 30 min after its last episode
FRONT = (0.0, 0.25, 0.5, 1.0, 2.0)
BLOCKS = (15, 30, 60)
MODEL_GRID = ("off", 0.0, 0.1, 0.2, 0.25, 0.3, 0.4, 0.5, 0.6, 0.7, 0.75, 0.8, 0.9, 1.0)
SAFETY_GRID = tuple(sorted({0.5} | {s for s in MODEL_GRID if s != "off"}))
GRID = {
    "cusum": [{"k": k, "h": h} for k in (0.25, 0.5, 1.0) for h in (2, 3, 4, 5, 6, 8, 10, 14, 20)],
    "cusum_score": [{"k": k, "h": h} for k in (0.25, 0.5, 1.0) for h in (2, 3, 4, 5, 6, 8, 10, 14, 20)],
    "ewma": [{"lam": lam, "L": L} for lam in (0.1, 0.2, 0.3, 0.5) for L in (1.5, 2, 2.5, 3, 3.5, 4, 5, 6)],
    "trend": [{"n": n, "z": z, "rise": r} for n in (4, 6, 8, 12) for z in (1.28, 1.64, 2.0, 2.5) for r in (0.5, 1, 1.5, 2.5)],
    "line": [{"frac": f, "hold": h} for f in (0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9) for h in (1, 2, 3, 4, 6)],
}
ZCUSUM_BLOCKS = (1, 15, 30, 60)
ZCUSUM_GRID = [{"k": k, "h": h} for k in (0.5, 1.0, 2.0, 3.0) for h in (3, 5, 10, 20, 40, 80)]
ZCUSUM = "CUSUM per reading (no model)"
TOP_CHECK = 12  # settings re-checked on the learn-one-week folds and the resampling


# ----------------------------------------------------------------- data shared by every model

class Data:
    """Every test run's minutes, concatenated, with the masks the metrics need (built once per process)."""

    def __init__(self, root: Path, cache: Path):
        self.root = Path(root)
        w = {n: E.minutes_of(self.root / n) for n in ("normal-w1", "normal-w2")}
        self.weeks = w
        self.t_tr = np.concatenate([w["normal-w1"][0], w["normal-w2"][0]])
        self.X_tr = np.vstack([w["normal-w1"][1], w["normal-w2"][1]])
        names = sorted(p.name for p in self.root.iterdir() if (p / "labels.json").exists() and not p.name.startswith("normal-w"))
        self.names = names
        ts, Xs, labs = [], [], []
        for n in names:
            t, X, lab = E.minutes_of(self.root / n)
            ts.append(t), Xs.append(X), labs.append(lab)
        self.labs = dict(zip(names, labs))
        self.Xs = dict(zip(names, Xs))
        self.ts = dict(zip(names, ts))
        self.bounds = np.cumsum([0] + [len(t) for t in ts])
        self.t = np.concatenate(ts)
        N = len(self.t)
        self.run_id = np.concatenate([np.full(len(t), i) for i, t in enumerate(ts)])
        self.first_row = np.zeros(N, bool)
        self.first_row[self.bounds[:-1]] = True
        self.run = np.concatenate([E.running_mask(t, lab) for t, lab in zip(ts, labs)])
        self.dev_running = np.concatenate([~(X[:, F.INDEX["trend_running"]] < 0.5) for X in Xs])
        t0 = np.concatenate([np.full(len(t), t[0]) for t in ts])
        keep = self.run & (self.t >= t0 + E.WARMUP_S)
        onset = np.concatenate([np.full(len(t), lab["scenario"].get("onset", np.nan)) for t, lab in zip(ts, labs)])
        self.onset = onset
        self.is_fault = np.isfinite(onset)
        self.held = keep & ~self.is_fault
        # the fault window: each fault episode from its start to its end + 30 min (a jam's 3-minute stalls are not
        # "caught" by an alert hours later; one window per stall, as model-search has it), from the onset on
        inwin = np.zeros(len(self.t), bool)
        for i, lab in enumerate(labs):
            a, b = self.bounds[i], self.bounds[i + 1]
            for f in lab.get("faults", []):  # a minute is in a window if it overlaps it
                inwin[a:b] |= (ts[i] + 60.0 > f["start_s"]) & (ts[i] <= f["end_s"] + WINDOW_PAD_S)
        # the minute containing the onset counts as after it for catches, and is not judged for false alerts
        # (onsets fall 3-56 s into their minute; the checker, 13 Nov)
        with np.errstate(invalid="ignore"):
            self.pre = keep & self.is_fault & (self.t + 60.0 <= onset)
            self.after = self.run & self.is_fault & (self.t + 60.0 > onset) & inwin
        self.fp_minutes = int((self.held | self.pre).sum())  # judged healthy running minutes
        # for catches, an alert continues over a stop: each running minute is compared with the previous
        # running minute of the same run (an early warning keeps its state overnight; a false alert already on
        # before the onset must not count as a catch at the next morning's restart)
        self.run_idx = np.nonzero(self.run)[0]
        rid = self.run_id[self.run_idx]
        self.run_first = np.r_[True, rid[1:] != rid[:-1]]
        # per fault run: kind, severity, slow or not, the share of final size each minute
        self.fault = {}
        share = np.full(N, np.nan)
        for i, (n, t, lab) in enumerate(zip(names, ts, labs)):
            sc = lab["scenario"]
            if sc.get("onset") is None:
                continue
            end = lab["faults"][0]["end_s"]
            wend = min(max(f["end_s"] for f in lab["faults"]) + WINDOW_PAD_S, float(t[-1]) + 60.0)
            self.fault[i] = (n, sc["fault_kind"], float(sc["severity"]), sc["fault_kind"] in SLOW, float(sc["onset"]), end,
                             (wend - float(sc["onset"])) / 60.0)
            if sc["fault_kind"] in SLOW:
                a, b = self.bounds[i], self.bounds[i + 1]
                share[a:b] = [E.wear_level(tk, sc["onset"], end, 1.0) for tk in t]
        self.share = share
        self.slow_runs = [i for i, f in self.fault.items() if f[3]]
        self.sudden_sub = [i for i, f in self.fault.items() if not f[3] and f[2] < 0.5]
        self.sudden_obv = [i for i, f in self.fault.items() if not f[3] and f[2] >= 0.5]
        self.safety = load_safety(self, cache)

    def runs_of(self, a):
        """Split a concatenated per-minute array back into runs."""
        return [a[self.bounds[i]:self.bounds[i + 1]] for i in range(len(self.names))]


def _safety_one(args):
    folder, minute_t0, baseline, cache = args
    p = Path(cache) / f"safety_{folder.name}.npz"
    if p.exists():
        return str(p)
    ts, C = EV.load_seconds(folder)
    act = EV.safety_active(ts, C, baseline, SAFETY_GRID)
    out = {}
    for s, a in act.items():
        anyon, first = EV.per_minute(ts, minute_t0, a)
        out[f"on_{s}"] = anyon
        out[f"sym_{s}"] = np.array([x or "" for x in first])
    np.savez_compressed(p, **out)
    return str(p)


def safety_baseline(X_tr):
    names = list(F.FEATURE_NAMES)
    run = ~(X_tr[:, F.INDEX["trend_running"]] < 0.5)
    return SF.learn_baseline(X_tr[run], names, running=run)


def build_safety_cache(root: Path, cache: Path, workers: int):
    """The safety checks per run and sensitivity, once (they don't depend on the model)."""
    cache.mkdir(parents=True, exist_ok=True)
    w = [E.minutes_of(root / n) for n in ("normal-w1", "normal-w2")]
    X_tr = np.vstack([w[0][1], w[1][1]])
    baseline = safety_baseline(X_tr)
    (cache / "baseline.json").write_text(json.dumps(baseline, default=float))
    names = sorted(p.name for p in root.iterdir() if (p / "labels.json").exists() and not p.name.startswith("normal-w"))
    jobs = [(root / n, E.minutes_of(root / n)[0], baseline, str(cache)) for n in names]
    with get_context("spawn").Pool(workers) as pool:
        for k, p in enumerate(pool.imap_unordered(_safety_one, jobs)):
            print(f"  safety {k + 1}/{len(jobs)} {Path(p).name}", flush=True)


def load_safety(data: Data, cache: Path):
    on = {s: [] for s in SAFETY_GRID}
    sym = {s: [] for s in SAFETY_GRID}
    for n in data.names:
        with np.load(Path(cache) / f"safety_{n}.npz") as d:
            for s in SAFETY_GRID:
                on[s].append(d[f"on_{s}"])
                sym[s].append(d[f"sym_{s}"])
    return {s: (np.concatenate(on[s]), np.concatenate(sym[s])) for s in SAFETY_GRID}


# ----------------------------------------------------------------- one model's per-minute output

class _Dev:
    """Lets early_warning.Series turn precomputed deviations and a score over the line into group series."""

    def __init__(self, names):
        self._dev_names, self.threshold = list(names), 1.0

    def deviation_vector(self, r, Z):
        return r


def bench_dev(meth, X):
    """(score over the line per row, deviations per row, their names) for an evaluate.BenchMethod."""
    Z = meth.z(X)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        s, r = meth.det.score(Z)
    cols = meth.prep.columns
    Zc = Z[:, :len(cols)]
    if r is None:
        dev = Zc
    else:
        rr = r[:, :len(cols)] / meth.resid_scale
        same = np.sign(rr) == np.sign(Zc)
        dev = np.where(same, np.sign(rr) * np.minimum(np.abs(rr), np.abs(Zc)), 0.0)
        dev = np.where(np.abs(Zc) >= RT.FAR_SPREADS, Zc, dev)
    return np.asarray(s, float) / meth.line, dev, cols


class ModelOut:
    """A fitted model's per-minute level (for the alarm), group series and deviations on every test run,
    and its group series on the healthy weeks (for the early warning's normal)."""

    def __init__(self, name, data: Data, t_tr=None, X_tr=None, runs=None):
        t_tr = data.t_tr if t_tr is None else t_tr
        X_tr = data.X_tr if X_tr is None else X_tr
        self.name = name
        meth = E.methods()[name]
        started = time.time()
        meth.fit(t_tr, X_tr)
        if isinstance(meth, E.BenchMethod):  # the residuals' own spread on the rows it learned from
            run = ~(X_tr[:, F.INDEX["trend_running"]] < 0.5)
            Zf = meth.z(X_tr[run][: int(0.85 * run.sum())])
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                _, r = meth.det.score(Zf)
            if r is not None:
                sd = np.std(r[:, :len(meth.prep.columns)], 0)
                meth.resid_scale = np.where(sd > 1e-4, sd, 1e-4)
        self.fit_s = time.time() - started
        self.meth = meth
        runs = runs if runs is not None else [(data.ts[n], data.Xs[n]) for n in data.names]
        levels, values, devs = [], [], []
        for t, X in runs:
            lv, v, d = self._minutes(t, X)
            levels.append(lv), values.append(v), devs.append(d)
        self.level = np.concatenate(levels)
        self.nan_level = int((~np.isfinite(self.level)).sum())
        self.level = np.where(np.isfinite(self.level), self.level, 0.0)
        self.values = np.concatenate(values)
        self.dev = np.concatenate(devs)
        self.nan_score = int((~np.isfinite(self.values[:, EW.SCORE])).sum())
        run = ~(X_tr[:, F.INDEX["trend_running"]] < 0.5)
        _, self.train_values, _ = self._series(X_tr[run])
        self.score_s = time.time() - started - self.fit_s

    def _series(self, X):
        if isinstance(self.meth, E.DeviceMethod):
            m = self.meth.model
            s, r, Z = m.score(X)
            dev, names, sl = m.deviation_vector(r, Z), m._dev_names, s / m.threshold
        else:
            sl, dev, names = bench_dev(self.meth, X)
        self.dev_names = list(names)
        return sl, EW.Series(_Dev(names)).from_model(sl, dev, None), dev

    def _minutes(self, t, X):
        run = ~(X[:, F.INDEX["trend_running"]] < 0.5)
        judged = self.meth.judge(t, X, run)
        level = np.array([np.nan if j[1] is None else j[1] for j in judged], float)
        _, values, dev = self._series(X)
        return level, values, dev.astype(np.float32)


# ----------------------------------------------------------------- early warning on blocks

def blocks(data: Data, values, dev_running, B, bounds=None):
    """Per run: (block-end minute indices into the concatenated arrays, block medians)."""
    bounds = data.bounds if bounds is None else bounds
    out = []
    for i in range(len(bounds) - 1):
        a, b = bounds[i], bounds[i + 1]
        r = np.nonzero(dev_running[a:b])[0] + a
        n = len(r) // B
        if n == 0:
            out.append((np.zeros(0, int), np.zeros((0, values.shape[1]))))
            continue
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            med = np.nanmedian(values[r[:n * B]].reshape(n, B, -1), axis=1)
        out.append((r[B - 1::B][:n], med))
    return out


def zscore(med, calib):
    c = np.array([np.nan if v is None else v for v in calib["centre"]], float)
    s = np.array([np.nan if v is None else v for v in calib["spread"]], float)
    with np.errstate(invalid="ignore"):
        return np.clip((med - c) / s, -EW.Z_CLIP, EW.Z_CLIP)


def detect(method, p, z, raw_score):
    """Per block: on, and which series is strongest (for naming), with early_warning's own detectors."""
    n = z.shape[1]
    on = np.zeros(len(z), bool)
    lead = np.full(len(z), -1)
    if method in ("cusum", "cusum_score", "ewma"):
        det = EW.Cusum(n, k=p["k"], h=p["h"]) if method != "ewma" else EW.Ewma(n, lam=p["lam"], L=p["L"])
        for j in range(len(z)):
            zz = z[j].copy()
            if method == "cusum_score":
                zz[:EW.SCORE] = 0.0
            o = det.step(zz)
            if o.any():
                on[j] = True
                lead[j] = int(np.argmax(np.where(o, det.strength(), -np.inf)))
    elif method == "line":
        det = EW.Line(n, frac=p["frac"], hold=p["hold"])
        for j in range(len(z)):
            if det.step_raw(float(raw_score[j]))[EW.SCORE]:
                on[j], lead[j] = True, EW.SCORE
    elif method == "trend":
        N = p["n"]
        need = max(4, N // 2)
        for j in range(need - 1, len(z)):
            H = z[max(0, j - N + 1):j + 1]
            mk = np.array([EW.mann_kendall_z(H[:, c]) for c in range(n)])
            ts = np.array([EW.theil_sen(H[:, c]) for c in range(n)])
            ok = np.isfinite(mk) & np.isfinite(ts) & (mk >= p["z"]) & (ts * (len(H) - 1) >= p["rise"]) & (H[-1] >= 1.0)
            if ok.any():
                on[j] = True
                lead[j] = int(np.argmax(np.where(ok, mk, -np.inf)))
    return on, lead


class TrendCache:
    """Mann-Kendall z and Theil-Sen total rise per block and series, per window: the trend grid only
    thresholds them (the same test as early_warning.Trend, computed once per window)."""

    def __init__(self, z):
        self.z = z
        self.c = {}

    def get(self, N):
        if N not in self.c:
            z, n = self.z, self.z.shape[1]
            mk = np.full(z.shape, np.nan)
            rise = np.full(z.shape, np.nan)
            for j in range(max(4, N // 2) - 1, len(z)):
                H = z[max(0, j - N + 1):j + 1]
                for c in range(n):
                    mk[j, c] = EW.mann_kendall_z(H[:, c])
                    ts = EW.theil_sen(H[:, c])
                    rise[j, c] = ts * (len(H) - 1) if math.isfinite(ts) else np.nan
            self.c[N] = (mk, rise)
        return self.c[N]


def detect_trend(tc: TrendCache, p):
    mk, rise = tc.get(p["n"])
    with np.errstate(invalid="ignore"):
        ok = (mk >= p["z"]) & (rise >= p["rise"]) & (tc.z >= 1.0)
    on = ok.any(1)
    lead = np.where(on, np.argmax(np.where(ok, mk, -np.inf), 1), -1)
    return on, lead


def to_minutes(N, ends, on_blocks, bounds):
    """A block's verdict holds from its last minute until the next block closes, and the run's last block
    until the run's end (never into the next run: 13 Nov fix, it leaked into every later run before)."""
    on = np.zeros(N, bool)
    if not len(ends):
        return on
    bounds = np.asarray(bounds)
    stop = int(bounds[np.searchsorted(bounds, ends[0], "right")])
    for e, nxt, o in zip(ends, list(ends[1:]) + [stop], on_blocks):
        if o:
            on[e:nxt] = True
    return on


# ----------------------------------------------------------------- metrics, over every run at once

class Metrics:
    """The shared definitions (agreed with model-search, 13 Nov): alerts only while running, judged over running
    minutes; false alerts = stretches beginning in judged healthy minutes (held-out after its 6 h,
    and before the onset in fault runs after theirs) per running week; caught = a stretch beginning after the
    onset, while running, inside a fault window (each episode's start to its end + 30 min; the minute holding the onset or a
    start counts as inside, and is not judged for false alerts), and not already on at
    the previous RUNNING minute: for catches a stretch continues over a stop; for false alerts a new stretch
    starts after every stop (the final rule, 13 Nov); earliness over all 30 faults, misses counted: a slow fault
    by its wear at the first alert as a % of the obvious size (missed: 100), a sudden one by its minutes from the
    onset as a share of the window (missed: 1), averaged as shares (wear / 100, minutes / window)."""

    def __init__(self, data: Data):
        self.d = data
        self.N = len(data.t)

    def __call__(self, alert):
        d = self.d
        alert = np.asarray(alert, bool) & d.run
        f = alert & (d.held | d.pre)  # false alerts: a new stretch after every stop (the final rule, 13 Nov)
        st = f & ~(np.r_[False, f[:-1]] & ~d.first_row)
        fp_restarts = int((st & ~run_starts(d, alert)).sum())  # of them, restarts of a stretch on before the stop
        fp_held = int((st & d.held).sum())
        fp_pre = int((st & d.pre).sum())
        firsts = catches(d, alert)
        # the old way (a stop ends a stretch for catches too): the fault runs it would credit only through a
        # restart of an alert that was already on before the stop
        prev_any = np.r_[False, alert[:-1]] & ~d.first_row
        old = np.nonzero(alert & ~prev_any & d.after)[0]
        restart_only = len(set(d.run_id[old].tolist()) - set(firsts))
        late, wear_all, wear_caught, mins_all, mins_caught = [], [], [], [], []
        for i, f_ in d.fault.items():
            k = firsts.get(i)
            sev, slow, onset, win = f_[2], f_[3], f_[4], f_[6]
            if slow:
                w = 100.0 if k is None else 100 * sev * float(d.share[k])
                wear_all.append(w)
                late.append(w / 100.0)
                if k is not None:
                    wear_caught.append(w)
            else:
                m = win if k is None else (d.t[k] - onset) / 60.0
                mins_all.append(m)
                late.append(min(m / max(win, 1.0), 1.0))
                if k is not None:
                    mins_caught.append(m)
        cnt = lambda runs: f"{sum(firsts.get(i) is not None for i in runs)}/{len(runs)}"
        slow_sub = [i for i in d.slow_runs if d.fault[i][2] < 0.5]
        slow_obv = [i for i in d.slow_runs if d.fault[i][2] >= 0.5]
        med = lambda v: round(float(np.median(v)), 1) if v else None
        return {"fp_per_week": round((fp_held + fp_pre) / max(d.fp_minutes, 1) * RUNNING_WEEK, 3),
                "fp_heldout": fp_held, "fp_pre_onset": fp_pre,
                "heldout_per_running_week": round(fp_held / max(int(d.held.sum()), 1) * RUNNING_WEEK, 3),
                "earliness": round(float(np.mean(late)), 4), "caught": len(firsts), "faults": len(d.fault),
                "slow_subtle_caught": cnt(slow_sub), "slow_obvious_caught": cnt(slow_obv),
                "slow_mean_wear_pct": round(float(np.mean(wear_all)), 1), "slow_median_wear_pct_caught": med(wear_caught),
                "sudden_subtle_caught": cnt(d.sudden_sub), "sudden_obvious_caught": cnt(d.sudden_obv),
                "sudden_mean_min": round(float(np.mean(mins_all)), 1), "sudden_median_min_caught": med(mins_caught),
                "restart_only_catches_excluded": restart_only, "false_alerts_that_are_restarts": fp_restarts,
                "missed": sorted(f_[0] for i, f_ in d.fault.items() if i not in firsts),
                "_firsts": firsts}


def run_starts(d, alert):
    """Minutes where an alert stretch begins, comparing each running minute with the previous running minute of
    the same run: a stretch that was on before a stop and is on after it is the same stretch (13 Nov, agreed)."""
    a = alert[d.run_idx]
    prev = np.r_[False, a[:-1]] & ~d.run_first
    st = np.zeros(len(alert), bool)
    st[d.run_idx[a & ~prev]] = True
    return st


def catches(d, alert):
    """Per fault run: the first minute where an alert stretch begins after the onset, inside the fault window,
    while running; a stretch that was on at the previous running minute (across a stop) is not a new one."""
    cand = np.nonzero(run_starts(d, alert) & d.after)[0]
    if not len(cand):
        return {}
    u, idx = np.unique(d.run_id[cand], return_index=True)
    return dict(zip(u.tolist(), cand[idx].tolist()))


def rank_key(r):
    """After the false alerts (the user, 13 Nov): the fewest missed faults, then the earliest over all 30
    (misses counted as the latest)."""
    return (-r["caught"], r["earliness"])


def winner(rows):
    """The fewest false alerts (no slack) among candidates that catch something, then rank_key."""
    rows = [r for r in rows if r and r["caught"] > 0]
    if not rows:
        return None
    lo = min(r["fp_per_week"] for r in rows)
    return min([r for r in rows if r["fp_per_week"] == lo], key=rank_key)


def front(rows):
    """The trade-off: for each false-alert level (per running week), the fewest misses and then the earliest
    setting within it."""
    out = {}
    for b in FRONT:
        ok = [r for r in rows if r["caught"] > 0 and r["fp_per_week"] <= b]
        out[str(b)] = min(ok, key=rank_key) if ok else None
    return out


# ----------------------------------------------------------------- one model's whole grid

def model_rows(name, data: Data, mo: ModelOut, store: EWStore | None = None):
    """Every (model sensitivity, early method, its settings, block) for one model, as summary rows, each with
    its first alert per fault run: [minute index, which part raised it, what the device would say]."""
    M = Metrics(data)
    N = len(data.t)
    alarms = {s: (np.zeros(N, bool) if s == "off" else (mo.level > SF.margins(s)["model"]) & data.run) for s in MODEL_GRID}
    safety = {s: data.safety[0.5 if s == "off" else s] for s in MODEL_GRID}
    none = (np.zeros(N, bool), np.array([""] * N))
    base = {(s, w): alarms[s] | (safety[s][0] if w else False) for s in MODEL_GRID for w in (True, False)}
    rows = []
    said_memo = {}

    def said_at(k):  # what moved most at that minute (effente.fault_types.describe), as the alarm would say it
        if k not in said_memo:
            said_memo[k] = FT.describe({n: float(v) for n, v in zip(mo.dev_names, mo.dev[k])})[0][0]
        return said_memo[k]

    def add(early, block, params, ew_on):
        for s, w in base:
            if s == "off" and ew_on is None and not w:
                continue  # nothing at all
            alert = base[s, w] | (ew_on & data.run) if ew_on is not None else base[s, w]
            r = M(alert)
            firsts = r.pop("_firsts")
            r.update({"model": name, "model_sensitivity": s, "safety_checks": w, "early": early, "block": block,
                      "params": params})
            saf = safety[s] if w else none
            info = {}
            for i, k in firsts.items():
                if alarms[s][k]:
                    info[str(i)] = [k, "alarm", said_at(k)]
                elif saf[0][k]:
                    info[str(i)] = [k, "safety", str(saf[1][k]) or None]
                else:
                    info[str(i)] = [k, "early", said_at(k)]
            r["firsts"] = info
            rows.append(r)

    add("none", None, None, None)
    for B in BLOCKS:
        calib = EW.calibrate_values(mo.train_values, 60, block=B)
        bl = blocks(data, mo.values, data.dev_running, B)
        zs = [(ends, zscore(med, calib), med[:, EW.SCORE]) for ends, med in bl]
        tcs = [TrendCache(z) for _, z, _ in zs]
        for method, grid in GRID.items():
            for p in grid:
                on = np.zeros(N, bool)
                for (ends, z, raw), tc in zip(zs, tcs):
                    o, _ = detect_trend(tc, p) if method == "trend" else detect(method, p, z, raw)
                    on |= to_minutes(N, ends, o, data.bounds)
                on &= data.run
                if store is not None:
                    store.add(method, B, p, on)
                add(method, B, p, on)
    return rows


def _zprep(data: Data):
    names = list(F.FEATURE_NAMES)
    run_tr = ~(data.X_tr[:, F.INDEX["trend_running"]] < 0.5)
    prep = Prep.fit(data.X_tr[run_tr], names, data.t_tr[run_tr], running_only=False)
    nc = len(prep.columns)
    zf = lambda A: np.clip(np.nan_to_num(prep.transform(A)[:, :nc]), -8, 8)
    Ztr = zf(data.X_tr[run_tr])
    Z = np.vstack([zf(data.Xs[n]) for n in data.names])
    return np.concatenate([Ztr, -Ztr], 1), np.concatenate([Z, -Z], 1)  # both directions, each one-sided


def _zper_run(data: Data, Ztr, Z, B):
    """Per run: (minute indices where a value is judged, standardised values): every running minute's z,
    or block medians against the healthy weeks' blocks."""
    if B == 1:
        out = []
        for i in range(len(data.names)):
            a, b = data.bounds[i], data.bounds[i + 1]
            r = np.nonzero(data.dev_running[a:b])[0] + a
            out.append((r, Z[r]))
        return out
    calib = EW.calibrate_values(Ztr, 60, block=B)
    c = np.array([0.0 if v is None else v for v in calib["centre"]])
    sd = np.array([1.0 if v is None else v for v in calib["spread"]])
    return [(ends, np.clip((med - c) / sd, -EW.Z_CLIP, EW.Z_CLIP)) for ends, med in blocks(data, Z, data.dev_running, B)]


def _zon(data: Data, per_run, p):
    N = len(data.t)
    on = np.zeros(N, bool)
    for ends, z in per_run:
        det = EW.Cusum(z.shape[1], k=p["k"], h=p["h"])
        ob = np.zeros(len(z), bool)
        for j in range(len(z)):
            ob[j] = det.step(z[j]).any()
        on |= to_minutes(N, ends, ob, data.bounds)
    return on & data.run


def zcusum_rows(data: Data, store: EWStore | None = None):
    """The 11th approach: CUSUM on every prepared reading's z, no model, with and without the safety checks."""
    Ztr, Z = _zprep(data)
    M = Metrics(data)
    rows = []
    for B in ZCUSUM_BLOCKS:
        per_run = _zper_run(data, Ztr, Z, B)
        for p in ZCUSUM_GRID:
            on = _zon(data, per_run, p)
            if store is not None:
                store.add("zcusum", B, p, on)
            for s in list(MODEL_GRID) + ["no safety checks"]:
                saf = False if s == "no safety checks" else data.safety[0.5 if s == "off" else s][0]
                r = M(on | saf)
                firsts = r.pop("_firsts")
                r.update({"model": ZCUSUM, "model_sensitivity": "-" if s == "no safety checks" else s,
                          "safety_checks": s != "no safety checks", "early": "zcusum", "block": B, "params": p})
                sym = None if s == "no safety checks" else data.safety[0.5 if s == "off" else s][1]
                r["firsts"] = {str(i): [k, "safety" if sym is not None and saf[k] else "cusum",
                                        (str(sym[k]) or None) if sym is not None and saf[k] else None]
                               for i, k in firsts.items()}
                rows.append(r)
    return rows


def _zcusum_alert(data: Data, r):
    """Rebuild the no-model CUSUM's alert for one setting."""
    Ztr, Z = _zprep(data)
    on = _zon(data, _zper_run(data, Ztr, Z, int(r["block"])), r["params"])
    s = r["model_sensitivity"]
    return on | (data.safety[0.5 if s in ("off", "-") else s][0] if r.get("safety_checks", True) else False)


class EWStore:
    """The early warning's own output per setting (before it is combined with the alarm and the checks, and
    the same at every model sensitivity), for joining with the model search:
      ew_<model>.jsonl   one line per setting: its key, and per run the first warning that begins after the
                         onset and every false warning (beginning in judged healthy time), as minute_t0 values
      ew_<model>.npz     the per-minute flags of every setting, bit-packed (np.unpackbits(flags[i])[:minutes]),
                         rows in the .jsonl's order, with each run's name and minute_t0"""

    def __init__(self, data: Data, model: str, out: Path):
        self.d, self.model, self.out = data, model, Path(out)
        self.lines, self.flags = [], []

    def add(self, method, block, params, on):
        d = self.d
        on = np.asarray(on, bool)
        f = on & d.run & (d.held | d.pre)
        st_false = f & ~(np.r_[False, f[:-1]] & ~d.first_row)
        first = np.zeros(len(on), bool)
        first[list(catches(d, on & d.run).values())] = True
        runs = {}
        for i, n in enumerate(d.names):
            a, b = d.bounds[i], d.bounds[i + 1]
            fs = np.nonzero(st_false[a:b])[0]
            fa = np.nonzero(first[a:b])[0]
            runs[n] = {"first_after_onset_t": float(d.t[a + fa[0]]) if len(fa) else None,
                       "false_starts_t": [float(d.t[a + k]) for k in fs],
                       "false_where": ["heldout" if d.held[a + k] else "pre_onset" for k in fs]}
        key = {"model": self.model, "inputs": "minute_median (evaluate.minutes_of)", "early": method,
               "block": block, "params": params}
        self.lines.append(json.dumps({"key": key, "runs": runs}, default=float))
        self.flags.append(np.packbits(on))

    def save(self):
        safe = _safe(self.model)
        (self.out / f"ew_{safe}.jsonl").write_text("\n".join(self.lines) + "\n")
        np.savez_compressed(self.out / f"ew_{safe}.npz", flags=np.array(self.flags), minutes=len(self.d.t),
                            run_names=np.array(self.d.names), bounds=self.d.bounds, minute_t0=self.d.t)


def _safe(name):
    return name.replace(" ", "_").replace("/", "-").replace("|", "").replace("(", "").replace(")", "")


def _worker(args):
    name, root, cache, out = args
    started = time.time()
    data = Data(Path(root), Path(cache))
    load_s = time.time() - started
    store = EWStore(data, name, Path(out))
    if name == ZCUSUM:
        rows = zcusum_rows(data, store)
        fit_s = score_s = 0.0
    else:
        mo = ModelOut(name, data)
        fit_s, score_s = mo.fit_s, mo.score_s
        nans = {"level_nan_minutes": mo.nan_level, "score_nan_minutes": mo.nan_score}
        rows = model_rows(name, data, mo, store)
        # the model's own per-minute level, so the alarm at any sensitivity can be rebuilt by whoever joins
        np.savez_compressed(Path(out) / f"level_{_safe(name)}.npz", level=mo.level, minute_t0=data.t,
                            run_names=np.array(data.names), bounds=data.bounds)
    store.save()
    took = {"load_s": round(load_s, 1), "fit_s": round(fit_s, 1), "score_s": round(score_s, 1),
            "grid_s": round(time.time() - started - load_s - fit_s - score_s, 1), "rows": len(rows),
            **(nans if name != ZCUSUM else {})}
    (Path(out) / f"rows_{_safe(name)}.json").write_text(json.dumps({"model": name, "took": took, "rows": rows}, default=str))
    return name, took


# ----------------------------------------------------------------- checking the winners

def first_alerts(data: Data, r):
    """Per fault run: the first alert's minutes from the onset, wear, which part raised it, what it would say."""
    out = {}
    for i, (k, by, said) in r.get("firsts", {}).items():
        n, kind, sev, slow, onset = data.fault[int(i)][:5]
        out[n] = {"by": by, "said": said, "fits": None if said is None else said in E.EXPECTED[kind],
                  "minutes": round((data.t[k] - onset) / 60, 1),
                  "wear_pct": round(100 * sev * float(data.share[k]), 1) if slow else None}
    for f in data.fault.values():
        out.setdefault(f[0], {"by": None, "missed": True})
    return out


def alert_for(data: Data, mo: ModelOut | None, r, bounds=None, values=None, level=None, run=None, dev_running=None,
              safety=None, train_values=None):
    """Rebuild one setting's per-minute alert (on the test runs, or on a fold's week with the arrays given)."""
    values = mo.values if values is None else values
    level = mo.level if level is None else level
    run = data.run if run is None else run
    dev_running = data.dev_running if dev_running is None else dev_running
    safety = data.safety if safety is None else safety
    train_values = mo.train_values if train_values is None else train_values
    N = len(run)
    s = r["model_sensitivity"]
    alert = (np.zeros(N, bool) if s == "off" else (level > SF.margins(s)["model"]) & run)
    if r.get("safety_checks", True):
        alert = alert | safety[0.5 if s == "off" else s][0]
    if r["early"] != "none":
        B = int(r["block"])
        calib = EW.calibrate_values(train_values, 60, block=B)
        on = np.zeros(N, bool)
        for ends, med in blocks(data, values, dev_running, B, bounds=bounds):
            z = zscore(med, calib)
            o, _ = detect_trend(TrendCache(z), r["params"]) if r["early"] == "trend" else detect(r["early"], r["params"], z, med[:, EW.SCORE])
            on |= to_minutes(N, ends, o, data.bounds if bounds is None else bounds)
        alert |= on & run
    return alert


def fold_check(root: Path, cache: Path, data: Data, picks):
    """False alerts per running week when learning on one healthy week and watching the other (both ways)."""
    out = {}
    weeks = data.weeks
    by_model = {}
    for key, r in picks.items():
        by_model.setdefault(r["model"], []).append((key, r))
    need_safety = any(r.get("safety_checks") for r in picks.values())
    sec = {wn: EV.load_seconds(root / wn) for wn in ("normal-w1", "normal-w2")} if need_safety else {}
    for model, items in by_model.items():
        if model == ZCUSUM:
            continue
        for a, b in (("normal-w1", "normal-w2"), ("normal-w2", "normal-w1")):
            ta, Xa, _ = weeks[a]
            tb, Xb, labb = weeks[b]
            mo = ModelOut(model, data, t_tr=ta, X_tr=Xa, runs=[(tb, Xb)])
            saf = {s: (np.zeros(len(tb), bool), None) for s in SAFETY_GRID}
            if need_safety:
                ts, C = sec[b]
                act = EV.safety_active(ts, C, safety_baseline(Xa), SAFETY_GRID)
                saf = {s: (EV.per_minute(ts, tb, act[s])[0], None) for s in SAFETY_GRID}
            run = E.running_mask(tb, labb)
            dev_run = ~(Xb[:, F.INDEX["trend_running"]] < 0.5)
            keep = run & (tb >= tb[0] + E.WARMUP_S)
            for key, r in items:
                alert = alert_for(data, mo, r, bounds=[0, len(tb)], run=run, dev_running=dev_run, safety=saf) & run
                f = alert & keep  # a new stretch after every stop, as in Metrics
                n = int((f & ~np.r_[False, f[:-1]]).sum())
                out.setdefault(key, []).append(round(n / max(int(keep.sum()), 1) * RUNNING_WEEK, 2))
    return out


def lateness(data: Data, i, k):
    sev, slow, onset, win = data.fault[i][2], data.fault[i][3], data.fault[i][4], data.fault[i][6]
    if k is None:
        return 1.0
    return float(sev * data.share[k]) if slow else min((data.t[k] - onset) / 60.0 / max(win, 1.0), 1.0)


def resample(data: Data, cands, n=1000, seed=0):
    """How often each candidate is still the best (fewest false alerts, then earliest over the faults drawn,
    then most caught) when the 15 fault kinds are drawn again with replacement. The false alerts are not
    resampled (they come from healthy time)."""
    rng = np.random.default_rng(seed)
    kinds = sorted({f[1] for f in data.fault.values()})
    runs_of = {k: [i for i, f in data.fault.items() if f[1] == k] for k in kinds}
    late = {key: {i: lateness(data, i, r["_firsts"].get(i)) for i in data.fault} for key, r in cands.items()}
    wins = Counter()
    for _ in range(n):
        pick = [i for k in rng.choice(kinds, len(kinds)) for i in runs_of[k]]
        best_key, best_val = None, None
        for key, r in cands.items():
            v = (r["fp_per_week"], round(float(np.mean([late[key][i] for i in pick])), 6),
                 -sum(r["_firsts"].get(i) is not None for i in pick))
            if best_val is None or v < best_val:
                best_key, best_val = key, v
        wins[best_key] += 1
    return {k: round(v / n, 3) for k, v in wins.items()}


def label(r):
    p = "" if not r.get("params") else ", ".join(f"{k} {v}" for k, v in r["params"].items())
    early = r["early"] if r["early"] == "none" else f"{r['early']} ({p}; block {r['block']})"
    saf = " + safety checks" if r.get("safety_checks") else ""
    return f"{r['model']} | sens {r['model_sensitivity']}{saf} | {early}"


HEAD = ("model", "model sens.", "early warning", "settings", "block", "false/running week", "held-out", "pre-onset",
        "earliness", "caught", "slow subtle", "slow obvious", "slow mean wear %", "sudden subtle", "sudden obvious",
        "sudden mean min", "+ current safety: false/wk", "+ safety: earliness")


def line(r, with_safety=None):
    p = "" if not r.get("params") else ", ".join(f"{k} {v}" for k, v in r["params"].items())
    ws = with_safety or {}
    return "| " + " | ".join(str(v) for v in (
        r["model"], r["model_sensitivity"], r["early"], p or "-", r["block"] or "-", r["fp_per_week"], r["fp_heldout"],
        r["fp_pre_onset"], r["earliness"], f"{r['caught']}/{r['faults']}", r["slow_subtle_caught"],
        r["slow_obvious_caught"], r["slow_mean_wear_pct"], r["sudden_subtle_caught"], r["sudden_obvious_caught"],
        r["sudden_mean_min"], ws.get("fp_per_week", "-"), ws.get("earliness", "-"))) + " |"


def table(rows, pair=None, first=None):
    head = ((first,) if first else ()) + HEAD
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for item in rows:
        lab, r = item if first else (None, item)
        if not r:
            continue
        ln = line(r, (pair or {}).get(key_of(r)))
        out.append(f"| {lab} " + ln if first else ln)
    return "\n".join(out)


def key_of(r, safety=None):
    return (r["model"], r["model_sensitivity"], r["early"], r["block"], json.dumps(r["params"], sort_keys=True),
            r.get("safety_checks") if safety is None else safety)


# the threshold that makes each early-warning method less sensitive when raised
LESS = {"cusum": "h", "cusum_score": "h", "ewma": "L", "trend": "z", "line": "frac", "zcusum": "h"}


def neighbours(r, index):
    """The winner's next-less-sensitive settings: one model-sensitivity step down, and the early warning's
    limit one step up."""
    out = {}
    grid = [g for g in MODEL_GRID if g != "off"]
    s = r["model_sensitivity"]
    if s in grid and grid.index(s) > 0:
        k = (r["model"], grid[grid.index(s) - 1]) + key_of(r)[2:]
        out["model sensitivity one step down"] = index.get(k)
    if r["early"] in LESS and r["params"]:
        name = LESS[r["early"]]
        vals = sorted({p[name] for p in (GRID.get(r["early"]) or ZCUSUM_GRID)})
        v = r["params"][name]
        if v in vals and vals.index(v) + 1 < len(vals):
            p = dict(r["params"], **{name: vals[vals.index(v) + 1]})
            k = key_of(r)[:4] + (json.dumps(p, sort_keys=True),) + key_of(r)[5:]
            out[f"early warning {name} one step up"] = index.get(k)
    return out


# ----------------------------------------------------------------- main

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--models", help="comma-separated subset of evaluate.methods() names (and the no-model CUSUM)")
    ap.add_argument("--stage", choices=("all", "safety", "sound", "grid", "report", "rotations"), default="all")
    ap.add_argument("--candidates", help="rotations: a JSON file, a list of {approach, input, params, smooth, key}")
    args = ap.parse_args(argv)
    root, out = Path(args.data), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    cache = out / "cache"
    started = time.time()
    if args.stage in ("all", "safety"):
        build_safety_cache(root, cache, args.workers)
        print(f"safety checks cached, {time.time() - started:.0f} s", flush=True)
    if args.stage in ("all", "sound"):
        build_sound_cache(root, cache, args.workers)
        print(f"sound-check variants cached, {time.time() - started:.0f} s", flush=True)
    names = list(E.methods()) + [ZCUSUM]
    if args.models:
        names = [n for n in names if n in args.models.split(",")]
    if args.stage in ("all", "grid"):
        jobs = [(n, str(root), str(cache), str(out)) for n in names]
        with get_context("spawn").Pool(min(args.workers, len(jobs))) as pool:
            for name, took in pool.imap_unordered(_worker, jobs):
                print(f"{name}: {took}  ({time.time() - started:.0f} s)", flush=True)
    if args.stage in ("all", "report"):
        report(root, cache, out, names)
    if args.stage == "rotations":
        run_rotations(out, json.loads(Path(args.candidates).read_text()), args.workers)
    print(f"done, {time.time() - started:.0f} s", flush=True)
    return 0


def report(root, cache, out, names):
    OUT_DIR[:] = [Path(out)]
    rows = []
    took = {}
    for p in sorted(out.glob("rows_*.json")):
        d = json.loads(p.read_text())
        if d["model"] in names:
            rows += d["rows"]
            took[d["model"]] = d["took"]
    print(f"{len(rows)} settings", flush=True)
    index = {key_of(r): r for r in rows}
    pair = {key_of(r, False): r for r in rows if r.get("safety_checks")}  # the same setting + current safety checks
    pair = {k: {"fp_per_week": v["fp_per_week"], "earliness": v["earliness"]} for k, v in pair.items()}
    ranked = [r for r in rows if not r.get("safety_checks")]  # ranked without the current (forklift-prone) checks
    by_model = {}
    for r in ranked:
        by_model.setdefault(r["model"], []).append(r)
    overall = winner(ranked)
    alone = {m: winner([r for r in rs if r["early"] == "none"]) for m, rs in by_model.items()}
    combined = {m: winner([r for r in rs if r["early"] != "none"]) for m, rs in by_model.items()}
    best_method = {f"{m} | {meth}": winner([r for r in rs if r["early"] == meth])
                   for m, rs in by_model.items() for meth in sorted({r["early"] for r in rs})}
    safety_alone = next((r for r in rows if r["model"] == "PCA (on the device)" and r["model_sensitivity"] == "off"
                         and r.get("safety_checks") and r["early"] == "none"), None)
    fr = front(ranked)
    # the top candidates: fewest false alerts, then earliest; checked on the folds and by resampling
    pool = sorted([r for r in ranked if r["caught"] > 0], key=lambda r: (r["fp_per_week"],) + rank_key(r))
    top, seen = [], set()
    for r in pool:
        if label(r) not in seen:
            top.append(r)
            seen.add(label(r))
        if len(top) >= TOP_CHECK:
            break
    data = Data(root, cache)
    near = neighbours(overall, index) if overall else {}
    show = top + [overall] + list(alone.values()) + list(combined.values()) + [v for v in fr.values()] + list(near.values())
    details = {label(r): first_alerts(data, r) for r in show if r}
    cands = {label(r): {**r, "_firsts": {int(i): v[0] for i, v in r["firsts"].items()}} for r in top}
    folds = fold_check(root, cache, data, {label(r): r for r in top + [overall] if r})
    stay = resample(data, cands)
    sound = sound_report(data, cache, overall)
    keep = {id(r) for r in show if r} | {id(v) for v in best_method.values() if v}
    for r in rows:
        if id(r) not in keep:
            r.pop("firsts", None)
    result = {
        "definitions": Metrics.__doc__, "settings": len(rows), "took": took,
        "winner": overall, "winner_first_alerts": details.get(label(overall)) if overall else None,
        "winner_fold_fp_per_running_week": folds.get(label(overall)) if overall else None,
        "winner_neighbours": near, "front": fr, "model_alone": alone, "model_plus_early_warning": combined,
        "best_per_model_and_method": best_method, "safety_checks_alone": safety_alone,
        "front_per_model": {m: front(rs) for m, rs in by_model.items()},
        "top": [{**r, "label": label(r), "fold_fp_per_running_week": folds.get(label(r)), "stays_best": stay.get(label(r), 0.0),
                 "first_alerts": details.get(label(r))} for r in top],
        "first_alerts": details, "with_current_safety": {label(r): pair.get(key_of(r)) for r in show if r},
        "sound_check": sound}
    (out / "search.json").write_text(json.dumps(result, indent=1, default=str))
    print("\nWinner (fewest false alerts per running week, then fewest missed faults, then earliest over all 30):")
    print("  " + (label(overall) if overall else "none"))
    print(table([overall], pair))
    print("  missed:", overall and overall.get("missed"))
    print("\nNext less sensitive:")
    print(table([(k, v) for k, v in near.items()], pair, first="neighbour"))
    print("\nFrontier (per running week): the fewest misses reachable at each false-alert level, and the earliness there:")
    print(table([(b, v) for b, v in fr.items()], pair, first="false alerts at most"))
    for b, v in fr.items():
        print(f"  {b}: missed {v and v.get('missed')}")
    print("\nModel alone (no early warning) vs model + early warning, best per model:")
    print(table([(m, alone.get(m)) for m in by_model] + [(m + " + EW", combined.get(m)) for m in by_model], pair, first="what"))
    print("\nSafety checks alone (current):", safety_alone and {k: safety_alone[k] for k in ("fp_per_week", "caught", "earliness")})
    print("\nTop candidates, with the checks:")
    for r in top:
        print(f"  {label(r)}: fp {r['fp_per_week']}/running week, earliness {r['earliness']}, caught {r['caught']}, "
              f"folds {folds.get(label(r))}, stays best {stay.get(label(r), 0.0)}")
    print("\nSound check:", json.dumps(sound.get("summary"), default=str)[:3000])


# ----------------------------------------------------------------- the sound check's false alerts

SOUND_LEVELS = ("minute p99 (today)", "second p99", "second p99.9")  # what "the learned loudest" is taken as
SOUND_KINDS = tuple(f"per-second, hold {h} s" for h in (10, 30, 60, 120)) + \
              tuple(f"minute median, {k} min in a row" for k in (1, 2, 3))
SOUND_MARGINS = (6.0, 9.0, 12.0)  # dB over that level at sensitivity 0.5 (6 is today's)
SOUND_SENS = tuple(x for x in MODEL_GRID if x != "off")  # the margin scales with the dial, as margins()["sound"]
TODAY = ("minute p99 (today)", "per-second, hold 10 s", 6.0)


def sound_variants():
    """(name, (level, kind, margin at 0.5)), least change first: how many of the three differ from today's,
    then the smaller margin, then the shorter hold."""
    out = [(f"{lv} | {kd} | +{m:g} dB", (lv, kd, m)) for lv in SOUND_LEVELS for kd in SOUND_KINDS for m in SOUND_MARGINS]
    change = lambda v: (sum(a != b for a, b in zip(v[1], TODAY)), v[1][2], SOUND_KINDS.index(v[1][1]), SOUND_LEVELS.index(v[1][0]))
    return sorted(out, key=change)


def _skey(name, sens):
    return f"snd|{name}|{sens}"


def _sound_one(args):
    folder, minute_t0, snd_min, baseline, levels, cache = args
    p = Path(cache) / f"sound_{folder.name}.npz"
    if p.exists():
        return str(p)
    ts, C = EV.load_seconds(folder)
    out = {}
    act = EV.safety_active(ts, C, baseline, [0.5])[0.5]  # every check at the default, one by one
    for name, on in act.items():
        out[f"chk_{name}"] = EV.per_minute(ts, minute_t0, {name: on})[0]
    for sens in SOUND_SENS:  # every other check, at each sensitivity
        a = EV.safety_active(ts, C, baseline, [sens])[sens]
        out[f"others_{sens}"] = EV.per_minute(ts, minute_t0, {n: v for n, v in a.items() if n != "louder_than_normal"})[0]
    snd = C["snd_level_db"]
    k05 = SF.margins(0.5)["sound"]
    for vname, (lv, kd, m) in sound_variants():
        level = levels[lv]
        for sens in SOUND_SENS:
            margin = m * SF.margins(sens)["sound"] / k05
            if kd.startswith("per-second"):
                h = float(kd.split("hold ")[1].split(" s")[0])
                on = EV._hold(ts, np.isfinite(snd) & (snd - level > margin), hold_s=h)
                out[_skey(vname, sens)] = EV.per_minute(ts, minute_t0, {"louder_than_normal": on})[0]
            else:
                k = int(kd.split(", ")[1].split(" min")[0])
                over = np.isfinite(snd_min) & (snd_min - level > margin)
                c = np.convolve(over.astype(int), np.ones(k, int), mode="full")[:len(over)]
                out[_skey(vname, sens)] = c >= k
    np.savez_compressed(p, **out)
    return str(p)


def sound_levels(root: Path, baseline):
    """The learned loudest three ways: today's (p99 of the training minutes' medians, effente.safety.learn_baseline),
    and the root-cause fix: p99 and p99.9 of the one-a-second running rows the check actually judges."""
    from effente.model_input import load_recording
    v = []
    for wn in ("normal-w1", "normal-w2"):
        X, t0, names = load_recording(root / wn)
        idx = {n: i for i, n in enumerate(names)}
        snd, run = X[:, idx["snd_level_db"]].astype(float), ~(X[:, idx["trend_running"]] < 0.5)
        v.append(snd[run & np.isfinite(snd)])
        del X
    v = np.concatenate(v)
    return {"minute p99 (today)": float(baseline["levels"]["snd_level_db"]),
            "second p99": float(np.percentile(v, 99)), "second p99.9": float(np.percentile(v, 99.9))}


def build_sound_cache(root: Path, cache: Path, workers: int):
    baseline = json.loads((cache / "baseline.json").read_text())
    levels = sound_levels(root, baseline)
    (cache / "sound_levels.json").write_text(json.dumps(levels))
    print(f"  sound levels: {levels}", flush=True)
    names = sorted(p.name for p in root.iterdir() if (p / "labels.json").exists() and not p.name.startswith("normal-w"))
    jobs = []
    for n in names:
        t, X, _ = E.minutes_of(root / n)
        jobs.append((root / n, t, X[:, F.INDEX["snd_level_db"]], baseline, levels, str(cache)))
    with get_context("spawn").Pool(workers) as pool:
        for k, p in enumerate(pool.imap_unordered(_sound_one, jobs)):
            print(f"  sound {k + 1}/{len(jobs)} {Path(p).name}", flush=True)


_SOUND: dict = {}


def _flags_of(data: Data, cache, key):
    """One sound-cache key over every run (each run's file read once, then kept)."""
    if not _SOUND:
        for n in data.names:
            with np.load(Path(cache) / f"sound_{n}.npz") as d:
                _SOUND[n] = {k: d[k] for k in d.files}
    return np.concatenate([_SOUND[n][key] for n in data.names])


_NUIS: dict = {}


def nuisances(data: Data, n):
    if n not in _NUIS:
        _NUIS[n] = E.full_nuisances(data.root / n, data.labs[n])
    return _NUIS[n]


def _causes(data: Data, flags):
    """What was going on when each false alert began (evaluate.full_nuisances, that minute and the one before)."""
    f = flags & data.run & (data.held | data.pre)
    st = np.nonzero(f & ~(np.r_[False, f[:-1]] & ~data.first_row))[0]
    c = Counter()
    for k in st:
        i = int(data.run_id[k])
        nu = nuisances(data, data.names[i])
        j = k - data.bounds[i]
        got = set(nu[j] or []) | (set(nu[j - 1] or []) if j > 0 else set())
        c.update(got or {"nothing in particular"})
    return dict(c.most_common())


def _during(data: Data, k):
    i = int(data.run_id[k])
    nu = nuisances(data, data.names[i])
    j = k - data.bounds[i]
    return sorted(set(nu[j] or []) | (set(nu[j - 1] or []) if j > 0 else set()))


def _judge_flags(data: Data, M, fl, causes=True):
    """A check's false alerts and catches; a catch made while a forklift passed is counted apart as likely
    coincidence (the fault began, then a forklift went by), not the fault being heard."""
    m = M(fl)
    firsts = m.pop("_firsts")
    caught = {data.fault[i][0]: round((data.t[k] - data.fault[i][4]) / 60, 1) for i, k in firsts.items()}
    fork = sorted(data.fault[i][0] for i, k in firsts.items() if "forklift_passing" in _during(data, k))
    return {"fp_per_week": m["fp_per_week"], "fp_heldout": m["fp_heldout"], "fp_pre_onset": m["fp_pre_onset"],
            "caught": len(caught), "catches_minutes": caught, "catches_during_forklift": fork,
            "caught_without_forklift": len(caught) - len(fork),
            "causes": _causes(data, fl) if causes and m["fp_per_week"] else {}}


def sound_report(data: Data, cache, overall):
    """The sound check's fixes: false alerts and catches of each variant (alone, and with every other check),
    every other check's false alerts at the default, the chosen fix across the sensitivity dial, and the whole
    device with it (the winner + its early warning + the fixed checks)."""
    M = Metrics(data)
    if not (Path(cache) / f"sound_{data.names[0]}.npz").exists():
        return {"summary": "not run (--stage sound)"}
    with np.load(Path(cache) / f"sound_{data.names[0]}.npz") as d:
        checks = [k[4:] for k in d.files if k.startswith("chk_")]
    levels = json.loads((Path(cache) / "sound_levels.json").read_text())
    out = {"levels_db": levels, "checks_at_default": {}, "variants_at_default": {}}
    for c in checks:
        out["checks_at_default"][c] = _judge_flags(data, M, _flags_of(data, cache, f"chk_{c}"))
    others05 = _flags_of(data, cache, "others_0.5")
    order = [v for v, _ in sound_variants()]
    for v in order:
        fl = _flags_of(data, cache, _skey(v, 0.5))
        j = _judge_flags(data, M, fl)
        allc = M(fl | others05)
        allc.pop("_firsts")
        j.update(all_checks_fp_per_week=allc["fp_per_week"], all_checks_caught=allc["caught"])
        out["variants_at_default"][v] = j
    today_name = order[0]
    today = set(out["variants_at_default"][today_name]["catches_minutes"])
    zero = [v for v in order if out["variants_at_default"][v]["fp_per_week"] == 0]
    # no false alerts, then the most catches, then the least change
    # today's check gives about 40 false alerts a running week, so its "catches" are mostly chance (80-1400 min
    # after the onset, many while a forklift passed): they are noise, not a baseline to keep. A variant with no
    # false alerts is judged by its own catches.
    zero.sort(key=lambda v: (-out["variants_at_default"][v]["caught"], order.index(v)))
    pick = zero[0] if zero else None
    out["today"] = today_name
    out["pick"] = {"variant": pick, "catches": out["variants_at_default"][pick]["catches_minutes"] if pick else None,
                   "todays_catches_noise": sorted(today),
                   "rule": "0 false alerts, then the most catches, then the least change (fewest of level / judging / "
                           "margin changed, then the smaller margin and shorter hold). Today's catches are not a "
                           "baseline: at about 40 false alerts a running week they are mostly chance"}
    root_fix = [v for v in order if v.startswith("second p99") and "hold 10 s" in v and "+6 dB" in v]
    sweep = {}
    for v in ([pick] if pick else []) + root_fix + [today_name]:
        sweep[v] = {}
        for sens in SOUND_SENS:
            fl = _flags_of(data, cache, _skey(v, sens))
            j = _judge_flags(data, M, fl, causes=False)
            allc = M(fl | _flags_of(data, cache, f"others_{sens}"))
            allc.pop("_firsts")
            sweep[v][str(sens)] = {"fp_per_week": j["fp_per_week"], "caught": j["caught"],
                                   "caught_without_forklift": j["caught_without_forklift"],
                                   "all_checks_fp_per_week": allc["fp_per_week"], "all_checks_caught": allc["caught"]}
    out["sensitivity_sweep"] = sweep
    if pick and overall:
        s_ = overall["model_sensitivity"]
        sens = s_ if s_ in SOUND_SENS else 0.5
        fixed = _flags_of(data, cache, f"others_{sens}") | _flags_of(data, cache, _skey(pick, sens))
        win = overall_alert(data, overall)
        if win is not None:
            m1, m2 = M(win), M(win | fixed)
            for m in (m1, m2):
                m.pop("_firsts")
            out["whole_device"] = {"setting": label(overall), "checks_at_sensitivity": sens, "winner": m1,
                                   "winner_plus_fixed_checks": m2}
    out["summary"] = {"levels_db": levels, "pick": out["pick"], "whole_device": out.get("whole_device"),
                      "checks_at_default_fp": {c: v["fp_per_week"] for c, v in out["checks_at_default"].items()},
                      "variants_fp_caught": {v: (x["fp_per_week"], x["caught"]) for v, x in out["variants_at_default"].items()}}
    return out


def overall_alert(data: Data, r):
    """A ranked setting's alert rebuilt from the saved files (the model's level and the early warning's flags)."""
    safe = _safe(r["model"])
    alert = np.zeros(len(data.t), bool)
    out = OUT_DIR[0] if OUT_DIR else None
    if out is None:
        return None
    if r["model"] != ZCUSUM and r["model_sensitivity"] not in ("off", "-"):
        lv = np.load(out / f"level_{safe}.npz")["level"]
        alert |= (lv > SF.margins(r["model_sensitivity"])["model"]) & data.run
    if r["early"] != "none":
        keyw = {"early": r["early"], "block": r["block"], "params": r["params"]}
        lines = (out / f"ew_{safe}.jsonl").read_text().splitlines()
        flags = np.load(out / f"ew_{safe}.npz")["flags"]
        for k, ln in enumerate(lines):
            key = json.loads(ln)["key"]
            if all(key.get(a) == b for a, b in keyw.items()):
                alert |= np.unpackbits(flags[k])[:len(data.t)].astype(bool)
                break
        else:
            return None
    return alert


OUT_DIR: list = []


# ----------------------------------------------------------------- rotations (model-search's top candidates)

ROTATIONS = {"rot_heldout": (("normal-w1", "normal-w2"), "heldout"), "rot_w2": (("normal-w1", "heldout"), "normal-w2"),
             "rot_w1": (("normal-w2", "heldout"), "normal-w1")}
HELDOUT_SHIFT_S = 1_209_600.0  # heldout's minutes, when it is learned from, moved 2 weeks on (model-search's rule)
EVAL_NAME = {"PCA (device, float)": "PCA (on the device)", "autoencoder (device, float)": "autoencoder (on the device)"}


def _rot_chunks(MS, rotation, kind):
    learn, watch = ROTATIONS[rotation]
    R = MS.runs()
    chunks = []
    for w in learn:
        X, names = MS.make_input(kind, R[w])
        t = R[w]["t"] + (HELDOUT_SHIFT_S if w == "heldout" else 0.0)
        chunks.append((t, X))
    return chunks, names


def _rot_fit(MS, approach, params, kind, rotation):
    """model_search.calibrated_fit with the cross-week line; the rotation's weeks built the same way if
    model_search has no such fold."""
    fold = {"rot_heldout": "both"}.get(rotation, rotation)
    try:
        return MS.calibrated_fit(approach, params, kind, fold, "crossfit"), "model_search.calibrated_fit"
    except (KeyError, ValueError):
        pass
    chunks, names = _rot_chunks(MS, rotation, kind)
    t = np.concatenate([c[0] for c in chunks])
    X = np.vstack([c[1] for c in chunks])
    run = MS._running(X)
    n1 = int(MS._running(chunks[0][1]).sum())
    t, X = t[run], X[run]
    halves = np.arange(len(t)) < n1
    cal = []
    for a in (halves, ~halves):
        prep, det = MS.fit_detector(approach, params, t[a], X[a], names)
        cal.append(det.score(MS._feed(prep, det, X[~a])))
    prep, det = MS.fit_detector(approach, params, t, X, names)
    return (prep, det, np.concatenate(cal[::-1])), "the same cross-fit, rebuilt here"


def _score_resid(MS, det, Z):
    """(score, residuals or None) from a model_search detector: its own residuals where it has them
    (DevPCA._parts, DevAE.resid, GaussGroups.resid in conditional spreads); for the plain Gaussian, a
    GaussGroups fitted on the same training inputs (model-search's suggestion)."""
    if isinstance(det, MS.Bench):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            s, r = det.det.score(Z)
        if r is None and getattr(det, "_gg", None) is not None:
            r = det._gg.resid(Z)
        return s, r
    if hasattr(MS, "GaussGroups") and isinstance(det, MS.GaussGroups):
        return det.score(Z), det.resid(Z)
    if isinstance(det, MS.DevPCA):
        _, r = det._parts(Z)
        return det.score(Z), r
    if isinstance(det, MS.DevAE) and hasattr(det, "resid"):
        return det.score(Z), det.resid(Z)
    if isinstance(det, MS.DevAE):
        h = np.asarray(Z, np.float32)
        for k, (W, b) in enumerate(det.layers):
            h = h @ W + b
            if k < len(det.layers) - 1:
                h = np.maximum(h, 0)
        return det.score(Z), np.asarray(Z, np.float32) - h
    return det.score(Z), None


def _rot_series(MS, prep, det, line, X, resid_scale=None):
    """Group series (as early_warning.Series) and the score over the line, per minute, from a fitted candidate."""
    Z = MS._feed(prep, det, X)
    s, r = _score_resid(MS, det, Z)
    nc = len(prep.columns)
    Zc = np.clip(np.nan_to_num(np.asarray(Z, float)[:, :nc]), -8, 8)
    if r is None:
        dev = Zc
    else:
        r = np.asarray(r, float)[:, :nc]
        if resid_scale is None:
            sd = np.std(r, 0)
            resid_scale = np.where(sd > 1e-4, sd, 1e-4)
        rr = r / resid_scale
        dev = np.where(np.sign(rr) == np.sign(Zc), np.sign(rr) * np.minimum(np.abs(rr), np.abs(Zc)), 0.0)
        dev = np.where(np.abs(Zc) >= RT.FAR_SPREADS, Zc, dev)
    return EW.Series(_Dev(prep.columns)).from_model(np.asarray(s, float) / line, dev, None), resid_scale, np.asarray(s, float)


def _best_ew(out: Path, model_name, k=3):
    """The early-warning settings to carry into the rotations, judged by the early warning ALONE (model alarm
    off, no safety checks) in the main grid, so a setting is not picked for catches the alarm made: the k best
    overall, the best of each method, and the device's default (cusum k 0.5 h 5, block 60) as a reference."""
    p = out / f"rows_{_safe(model_name)}.json"
    picked = []
    seen = set()

    def take(r):
        key = (r["early"], r["block"], json.dumps(r["params"], sort_keys=True))
        if key not in seen:
            seen.add(key)
            picked.append({"early": r["early"], "block": r["block"], "params": r["params"]})

    if p.exists():
        alone = [r for r in json.loads(p.read_text())["rows"] if r["early"] != "none" and not r.get("safety_checks")
                 and r["model_sensitivity"] in ("off", "-") and r["caught"] > 0]
        order = sorted(alone, key=lambda r: (r["fp_per_week"],) + rank_key(r))
        for r in order[:k]:
            take(r)
        for meth in sorted({r["early"] for r in alone}):
            take(next(r for r in order if r["early"] == meth))
    take({"early": "cusum", "block": 60, "params": {"k": 0.5, "h": 5}})
    return picked


def _rot_save(out: Path, name, keys, flags, names, bounds, t):
    (out / f"ew_rot__{name}.jsonl").write_text("\n".join(json.dumps({"key": k}, default=float) for k in keys) + "\n")
    np.savez_compressed(out / f"ew_rot__{name}.npz", flags=np.array(flags), minutes=len(t), run_names=np.array(names),
                        bounds=np.asarray(bounds), minute_t0=t)


def _rot_runs(MS, rotation):
    """The watched runs of a rotation: its watched healthy week and the 30 fault runs, sorted by name."""
    R = MS.runs()
    watch = ROTATIONS[rotation][1]
    return [n for n in sorted(R) if n == watch or n not in ("normal-w1", "normal-w2", "heldout")]


def _rot_job(args):
    """One (candidate, rotation): early-warning flags for the chosen settings on the watched runs."""
    cand, rotation, out = args
    from training.record.conveyor_eval import model_search as MS
    out = Path(out)
    approach, kind, params, n = cand["approach"], cand.get("input", "median"), cand.get("params") or {}, int(cand.get("smooth", 5))
    (prep, det, cal), how = _rot_fit(MS, approach, params, kind, rotation)
    line = MS.lines_for(cal, n)
    chunks, _ = _rot_chunks(MS, rotation, kind)
    Xtr = np.vstack([c[1] for c in chunks])
    Xtr = Xtr[MS._running(Xtr)]
    if isinstance(det, MS.Bench) and type(det.det).__name__ == "Gaussian" and hasattr(MS, "GaussGroups"):
        gg = MS.GaussGroups()
        gg.names = prep.names_out
        det._gg = gg.fit(MS._feed(prep, det, Xtr))
    train_values, rscale, _ = _rot_series(MS, prep, det, line, Xtr)
    R = MS.runs()
    names = _rot_runs(MS, rotation)
    vals, runm, devrun, ts, raw = [], [], [], [], {}
    for nm in names:
        X, _ = MS.make_input(kind, R[nm])
        v, _, sc = _rot_series(MS, prep, det, line, X, rscale)
        raw[nm] = sc
        vals.append(v)
        runm.append(np.asarray(R[nm]["running"], bool))
        devrun.append(~(R[nm]["median"][:, F.INDEX["trend_running"]] < 0.5))
        ts.append(R[nm]["t"])
    values, run, dev_running, t = np.vstack(vals), np.concatenate(runm), np.concatenate(devrun), np.concatenate(ts)
    bounds = np.cumsum([0] + [len(x) for x in ts])
    N = len(t)
    # the same fit as model-search's? its saved raw scores for this candidate and fold, if any
    same = {"checked": False}
    try:
        fold = {"rot_heldout": "both"}.get(rotation, rotation)
        mkey = MS.job(approach, kind, params, fold, "crossfit")["key"]
        with np.load(MS.OUT / "scores" / f"{mkey}.npz") as d:
            theirs = {k[5:]: d[k] for k in d.files if k.startswith("run__")}
            cal_t = d["cal"]
        diffs = [float(np.nanmax(np.abs(raw[n] - theirs[n]))) for n in names if n in theirs and len(theirs[n]) == len(raw[n])]
        same = {"checked": True, "scores_file": mkey, "runs_compared": len(diffs),
                "max_abs_score_diff": max(diffs) if diffs else None,
                "max_abs_cal_diff": float(np.nanmax(np.abs(np.asarray(cal, float) - cal_t))) if len(cal_t) == len(cal) else None}
    except Exception as e:  # noqa: BLE001  no saved scores for this one
        same["note"] = f"{type(e).__name__}: {e}"[:200]
    keys, flags = [], []
    for ew in _best_ew(out, EVAL_NAME.get(approach, approach)):
        B = int(ew["block"])
        calib = EW.calibrate_values(train_values, 60, block=B)
        on = np.zeros(N, bool)
        for ends, med in blocks(None, values, dev_running, B, bounds=bounds):
            z = zscore(med, calib)
            o, _ = detect_trend(TrendCache(z), ew["params"]) if ew["early"] == "trend" else detect(ew["early"], ew["params"], z, med[:, EW.SCORE])
            on |= to_minutes(N, ends, o, bounds)
        keys.append({"candidate": cand, "rotation": rotation, "early": ew["early"], "block": B, "params": ew["params"],
                     "line": f"crossfit (model_search.lines_for, smooth {n})", "line_value": float(line), "fit": how,
                     "same_fit_as_model_search": same, "early_warning_normal": "this rotation's two training weeks"})
        flags.append(np.packbits(on & run))
    name = f"{_safe(cand.get('key') or approach)}__{rotation}"
    _rot_save(out, name, keys, flags, names, bounds, t)
    return name, len(keys)


def _rot_zcusum(args):
    """The no-model CUSUM in a rotation: Prep learned on the rotation's two weeks."""
    rotation, out = args
    from training.record.conveyor_eval import model_search as MS
    out = Path(out)
    chunks, names_in = _rot_chunks(MS, rotation, "median")
    t_tr = np.concatenate([c[0] for c in chunks])
    X_tr = np.vstack([c[1] for c in chunks])
    run_tr = MS._running(X_tr)
    prep = Prep.fit(X_tr[run_tr], names_in, t_tr[run_tr], running_only=False)
    nc = len(prep.columns)
    zf = lambda A: np.clip(np.nan_to_num(prep.transform(A)[:, :nc]), -8, 8)
    Ztr = zf(X_tr[run_tr])
    Ztr = np.concatenate([Ztr, -Ztr], 1)
    R = MS.runs()
    names = _rot_runs(MS, rotation)
    Z = np.vstack([zf(R[n]["median"]) for n in names])
    Z = np.concatenate([Z, -Z], 1)
    run = np.concatenate([np.asarray(R[n]["running"], bool) for n in names])
    dev_running = np.concatenate([~(R[n]["median"][:, F.INDEX["trend_running"]] < 0.5) for n in names])
    t = np.concatenate([R[n]["t"] for n in names])
    bounds = np.cumsum([0] + [len(R[n]["t"]) for n in names])
    N = len(t)
    keys, flags = [], []
    settings = _best_ew(out, ZCUSUM)
    settings = [x for x in settings if x["early"] == "zcusum"]
    for ew in settings:
        B = int(ew["block"])
        if B == 1:
            per_run = [(np.nonzero(dev_running[a:b])[0] + a, Z[np.nonzero(dev_running[a:b])[0] + a])
                       for a, b in zip(bounds[:-1], bounds[1:])]
        else:
            calib = EW.calibrate_values(Ztr, 60, block=B)
            c = np.array([0.0 if v is None else v for v in calib["centre"]])
            sd = np.array([1.0 if v is None else v for v in calib["spread"]])
            per_run = [(ends, np.clip((med - c) / sd, -EW.Z_CLIP, EW.Z_CLIP))
                       for ends, med in blocks(None, Z, dev_running, B, bounds=bounds)]
        on = np.zeros(N, bool)
        for ends, z in per_run:
            det = EW.Cusum(z.shape[1], k=ew["params"]["k"], h=ew["params"]["h"])
            ob = np.array([det.step(z[j]).any() for j in range(len(z))], bool)
            on |= to_minutes(N, ends, ob, bounds)
        keys.append({"candidate": {"approach": ZCUSUM}, "rotation": rotation, "early": "zcusum", "block": B,
                     "params": ew["params"]})
        flags.append(np.packbits(on & run))
    name = f"{_safe(ZCUSUM)}__{rotation}"
    _rot_save(out, name, keys, flags, names, bounds, t)
    return name, len(keys)


def run_rotations(out: Path, candidates, workers):
    jobs = [(c, r, str(out)) for c in candidates for r in ROTATIONS]
    zjobs = [(r, str(out)) for r in ROTATIONS]
    with get_context("spawn").Pool(workers) as pool:
        for name, n in pool.imap_unordered(_rot_job, jobs):
            print(f"  rotation {name}: {n} settings", flush=True)
        for name, n in pool.imap_unordered(_rot_zcusum, zjobs):
            print(f"  rotation {name}: {n} settings", flush=True)


if __name__ == "__main__":
    sys.exit(main())
