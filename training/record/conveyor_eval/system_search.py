#!/usr/bin/env python3
"""The best whole detection system on the conveyor evaluation data: model(s) + persistence + per-second checks.

Builds on model_search.py (imported from the main checkout by file: its rotation, crossfit line, Prepared and
judge_flags, and its cached scores in data/fake/model-search/scores, read only). Nothing in effente/ changes; the
per-second safety checks are this branch's (claude/sound-check-fix-8eb204: outside-normal judged on 60 s medians).

JUDGING: model_search's agreed rules, unchanged.
  rotation   learn on two of {normal-w1, normal-w2, heldout}, false alerts on the third plus the fault runs'
             pre-onset minutes, all three ways; the line is the crossfit (cross-week out-of-sample) one
  rank       the worst rotation's false alerts per running week (no slack), then mean missed of 30, then mean
             lateness; plateaus preferred (the setting's neighbours are reported)
  counting   catches continue over stops; per-episode windows (start to end + 30 min); the onset minute counts as
             after onset; a new false-alert stretch after every stop

Steps (each writes data/fake/system-search/<step>.json in the main checkout):
  seconds    the per-second columns the checks read, one compact file per run (seconds/<run>.npz)
  persist    exp 1: long persistence on the model's own score: the median of each block of B running minutes
             over frac x the line, held for `hold` blocks (early_warning's "line"), OR'ed with the model's alarm
  safety     exp 2: the per-second path: the fixed safety checks per rotation (baseline from the rotation's
             training weeks), and a per-second spike detector, alone and OR'ed with the best model system
  relheat    exp 3: camera temperatures relative to the camera's own background, refitted in the rotation
  unbalance  exp 4: why unbalance-obvious is missed in two rotations
  combine    exp 5/6: OR of models (lines raised together), max of normalised scores, and the final system

    PYTHONPATH=<this worktree> nice -n 10 python3 -m training.record.conveyor_eval.system_search seconds
"""

from __future__ import annotations

import os

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ.setdefault(_v, "1")

import argparse  # noqa: E402
import importlib.util  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
import warnings  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402

from effente import features as F  # noqa: E402
from effente import safety as SF  # noqa: E402

MAIN = Path("/Users/finnfreas/Library/Application Support/Claude/scratch-workspaces/1206cbf9-1f94-4639-898f-e56527c39385/"
            "02812373-138d-4a58-874b-33e46fa50871/scratch-2026-11-12-b20dcd/effente")
DATA = MAIN / "data/fake/conveyor-eval"
OUT = MAIN / "data/fake/system-search"


def _load_model_search():
    spec = importlib.util.spec_from_file_location("model_search", MAIN / "training/record/conveyor_eval/model_search.py")
    ms = importlib.util.module_from_spec(spec)
    sys.modules["model_search"] = ms
    spec.loader.exec_module(ms)
    ms.OUT = MAIN / "data/fake/model-search"  # its cache and scores, read only
    ms.DATA = DATA
    return ms


MS = _load_model_search()
ROTS = MS.ROT_KEYS
SENS = MS.SENS
SMOOTHINGS = MS.SMOOTHINGS
FP_LEVELS = (0.0, 0.25, 0.5, 1.0, 2.0)

MODELS = {  # name -> (approach, input, params) as model_search keys its scores
    "PCA group-mean": ("PCA (device, float)", "median", {"max_k": 200, "t2": False, "var": 0.95, "group": "mean"}),
    "Gaussian group-mean": ("Gaussian (Mahalanobis)", "median", {"group": "mean"}),
}


def write(name, obj):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(obj, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o)))
    print(f"wrote {OUT / name}", flush=True)


_PS = None


def prepared():
    global _PS
    if _PS is None:
        _PS = {rot: MS.Prepared(watch=MS.WATCHED[rot]) for rot in ROTS}
    return _PS


def scores(model, rot):
    """(calibration raw scores, raw scores per run) from model_search's cache, for a rotation."""
    a, k, p = MODELS[model]
    j = MS.job(a, k, p, fold=MS._rot_fold(rot), calib="crossfit")
    _, cal, raw = MS.load_scores(j["key"])
    return cal, raw


# ----------------------------------------------------------------- judging many flag columns in the rotation

def judge_rot(flags_by_rot, details=False):
    """flags_by_rot[rot][run] -> (minutes, k) bool. Returns k rows with per-rotation results and the ranking keys."""
    Ps = prepared()
    per = {rot: MS.judge_flags(Ps[rot], flags_by_rot[rot], details) for rot in ROTS}
    k = len(per[ROTS[0]])
    rows = []
    for c in range(k):
        rr = {rot: per[rot][c] for rot in ROTS}
        row = {"fp_by_rotation": {r: v["fp"] for r, v in rr.items()},
               "fp_week_by_rotation": {r: v["fp_week"] for r, v in rr.items()},
               "worst_fp_week": max(v["fp_week"] for v in rr.values()),
               "missed_by_rotation": {r: v["missed"] for r, v in rr.items()},
               "mean_missed": round(float(np.mean([v["missed"] for v in rr.values()])), 2),
               "lateness_by_rotation": {r: v["lateness"] for r, v in rr.items()},
               "mean_lateness": round(float(np.mean([v["lateness"] for v in rr.values()])), 4),
               "min_caught": min(v["caught"] for v in rr.values())}
        if details:
            row["detail"] = {r: {x: v[x] for x in ("missed_runs", "slow_wear_caught_median", "sudden_median_min",
                                                   "median_min_caught", "subtle", "obvious", "runs")} for r, v in rr.items()}
        rows.append(row)
    return rows


def rank_key(r):
    return (r["worst_fp_week"], r["mean_missed"], r["mean_lateness"])


def frontier(rows, describe):
    out = []
    for L in FP_LEVELS:
        pool = [r for r in rows if r["worst_fp_week"] <= L + 1e-9 and r["min_caught"] > 0]
        if pool:
            b = min(pool, key=lambda r: (r["mean_missed"], r["mean_lateness"]))
            out.append({"level": L, "setting": describe(b), "fp_by_rotation": b["fp_by_rotation"],
                        "missed_by_rotation": b["missed_by_rotation"], "mean_missed": b["mean_missed"],
                        "mean_lateness": b["mean_lateness"]})
    return out


# ----------------------------------------------------------------- model alarm and persistence flags

def model_flag_sets(P, cal, raw):
    """{n: {run: (minutes, len(SENS)) bool}}: the model's own alarm, as model_search judges it."""
    out = {}
    for n in SMOOTHINGS:
        _, ratio = MS.ratios(P, cal, raw, n)
        out[n] = MS.model_flags(P, ratio, SENS)
    return out


def block_on(ratio, dev_run, B, fracs, holds):
    """early_warning's "line" on one run: medians of consecutive blocks of B running minutes (the buffer carries
    over stops, as EarlyWarning.observe), on once `hold` blocks in a row are over frac (x the line), from the
    minute the block closes until a block falls back. Returns (minutes, len(fracs) * len(holds)) bool."""
    ri = np.nonzero(dev_run)[0]
    nb = len(ri) // B
    out = np.zeros((len(ratio), len(fracs) * len(holds)), bool)
    if nb == 0:
        return out
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        med = np.nanmedian(np.where(np.isfinite(ratio[ri[:nb * B]]), ratio[ri[:nb * B]], np.nan).reshape(nb, B), 1)
    med = np.nan_to_num(med, nan=0.0)
    close = ri[np.arange(1, nb + 1) * B - 1]
    ends = np.r_[close[1:], len(ratio)]
    c = 0
    for f in fracs:
        over = med > f
        runlen = np.zeros(nb, int)
        k = 0
        for i in range(nb):
            k = k + 1 if over[i] else 0
            runlen[i] = k
        for h in holds:
            on = runlen >= h
            for i in np.nonzero(on)[0]:
                out[close[i]:ends[i], c] = True
            c += 1
    return out


PERSIST_B = (30, 60, 120, 360)
PERSIST_FRAC = (0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0)
PERSIST_HOLD = (1, 2, 3, 4, 6)


def persist_flags(P, cal, raw, B):
    """{run: (minutes, len(FRAC) * len(HOLD))} for one block size; the score is raw / the 1-minute line."""
    line = MS.lines_for(cal, 1)
    return {name: block_on(np.asarray(raw[name], float) / line, P.runs[name]["reset"], B, PERSIST_FRAC, PERSIST_HOLD)
            for name in P.runs}


def persist(models=None):
    """Exp 1: model alarm (smoothing x sensitivity, or off) OR persistence (B x frac x hold), in the rotation."""
    Ps = prepared()
    models = models or list(MODELS)
    result = {}
    for model in models:
        t0 = time.time()
        base = {rot: scores(model, rot) for rot in ROTS}
        mflags = {rot: model_flag_sets(Ps[rot], *base[rot]) for rot in ROTS}
        cols = [("off", None)] + [(n, s) for n in SMOOTHINGS for s in SENS]
        rows = []
        for B in PERSIST_B:
            pf = {rot: persist_flags(Ps[rot], *base[rot], B) for rot in ROTS}
            for pi, (f, h) in enumerate((f, h) for f in PERSIST_FRAC for h in PERSIST_HOLD):
                fl = {}
                for rot in ROTS:
                    fl[rot] = {}
                    for name in Ps[rot].runs:
                        p = pf[rot][name][:, pi]
                        m = np.hstack([mflags[rot][n][name] for n in SMOOTHINGS])
                        fl[rot][name] = np.hstack([p[:, None], m | p[:, None]])
                for (n, s), r in zip(cols, judge_rot(fl)):
                    rows.append({"model": model, "smooth": n, "sensitivity": s, "block": B, "frac": f, "hold": h, **r})
            # the model alone (no persistence), once
        fl = {rot: {name: np.hstack([mflags[rot][n][name] for n in SMOOTHINGS]) for name in Ps[rot].runs} for rot in ROTS}
        for (n, s), r in zip(cols[1:], judge_rot(fl)):
            rows.append({"model": model, "smooth": n, "sensitivity": s, "block": None, "frac": None, "hold": None, **r})
        result[model] = rows
        print(f"{model}: {len(rows)} settings judged in {time.time() - t0:.0f} s", flush=True)
    return result


def describe_p(r):
    m = "model off" if r["smooth"] == "off" else f"n{r['smooth']} s{r['sensitivity']}"
    p = "no persistence" if r["block"] is None else f"block {r['block']} min > {r['frac']}x line held {r['hold']}"
    return f"{r['model']} | {m} | {p}"


def neighbours_p(rows, r):
    """Settings one step away in each knob (sensitivity, frac, hold, block)."""
    def key(x):
        return (x["model"], x["smooth"], x["sensitivity"], x["block"], x["frac"], x["hold"])
    idx = {key(x): x for x in rows}
    out = []
    steps = []
    if r["smooth"] != "off":
        steps += [("sensitivity", round(r["sensitivity"] + d, 3)) for d in (-0.05, -0.025, 0.025, 0.05)]
    if r["block"] is not None:
        i = PERSIST_FRAC.index(r["frac"])
        steps += [("frac", PERSIST_FRAC[j]) for j in (i - 1, i + 1) if 0 <= j < len(PERSIST_FRAC)]
        steps += [("hold", h) for h in PERSIST_HOLD if abs(PERSIST_HOLD.index(h) - PERSIST_HOLD.index(r["hold"])) == 1]
        steps += [("block", b) for b in PERSIST_B if abs(PERSIST_B.index(b) - PERSIST_B.index(r["block"])) == 1]
    for k, v in steps:
        x = idx.get(key({**r, k: v}))
        if x:
            out.append({"changed": f"{k}={v}", "fp_by_rotation": x["fp_by_rotation"], "worst_fp_week": x["worst_fp_week"],
                        "mean_missed": x["mean_missed"], "mean_lateness": x["mean_lateness"]})
    return out


def add_plateau(rows):
    """Each setting's plateau: the worst false alerts and misses over it and its one-step neighbours (sensitivity
    +-0.025, frac, hold and block one step). The plateau ranking prefers settings that stay good when nudged."""
    def key(x):
        return (x["model"], x["smooth"], x["sensitivity"], x["block"], x["frac"], x["hold"])
    idx = {key(x): x for x in rows}
    for r in rows:
        nb = [r]
        if r["smooth"] != "off":
            nb += [idx.get(key({**r, "sensitivity": round(r["sensitivity"] + d, 3)})) for d in (-0.025, 0.025)]
        if r["block"] is not None:
            for knob, grid in (("frac", PERSIST_FRAC), ("hold", PERSIST_HOLD), ("block", PERSIST_B)):
                i = grid.index(r[knob])
                nb += [idx.get(key({**r, knob: grid[j]})) for j in (i - 1, i + 1) if 0 <= j < len(grid)]
        nb = [x for x in nb if x]
        r["plateau_worst_fp_week"] = max(x["worst_fp_week"] for x in nb)
        r["plateau_mean_missed"] = max(x["mean_missed"] for x in nb)


def plateau_key(r):
    return (r["plateau_worst_fp_week"], r["plateau_mean_missed"], r["mean_missed"], r["mean_lateness"])


def run_persist():
    res = persist()
    summary = {}
    for model, rows in res.items():
        add_plateau(rows)
        ok = [r for r in rows if r["min_caught"] > 0]
        best = sorted(ok, key=rank_key)
        plat = sorted(ok, key=plateau_key)
        alone = sorted([r for r in ok if r["block"] is None], key=rank_key)
        p_alone = sorted([r for r in ok if r["smooth"] == "off"], key=rank_key)
        print(f"\n{model}: plateau best {describe_p(plat[0])} fp {plat[0]['fp_by_rotation']} missed "
              f"{plat[0]['missed_by_rotation']} mean {plat[0]['mean_missed']} (worst neighbour: fp "
              f"{plat[0]['plateau_worst_fp_week']}, missed {plat[0]['plateau_mean_missed']}) late {plat[0]['mean_lateness']}")
        summary[model] = {"best": best[0], "best_neighbours": neighbours_p(rows, best[0]),
                          "plateau_best": plat[0], "plateau_neighbours": neighbours_p(rows, plat[0]), "plateau_top10": plat[:10],
                          "top10": best[:10], "model_alone": alone[0], "persistence_alone": p_alone[0] if p_alone else None,
                          "frontier": frontier(ok, describe_p),
                          "best_per_block": {B: sorted([r for r in ok if r["block"] == B], key=rank_key)[0] for B in PERSIST_B}}
        print(f"\n{model}")
        for lab, r in (("model alone", alone[0]), ("persistence alone", p_alone[0] if p_alone else None), ("best", best[0])):
            if r:
                print(f"  {lab:18s} {describe_p(r):75s} fp {r['fp_by_rotation']} missed {r['missed_by_rotation']} "
                      f"mean {r['mean_missed']} late {r['mean_lateness']}")
        for B in PERSIST_B:
            r = summary[model]["best_per_block"][B]
            print(f"  block {B:3d}          {describe_p(r):75s} fp {r['fp_by_rotation']} missed {r['missed_by_rotation']} "
                  f"mean {r['mean_missed']} late {r['mean_lateness']}")
        print("  frontier:", [(f["level"], f["mean_missed"], f["setting"]) for f in summary[model]["frontier"]])
        print("  neighbours of best:", [(x["changed"], x["worst_fp_week"], x["mean_missed"]) for x in summary[model]["best_neighbours"]])
    write("persist.json", {"summary": summary, "all": {m: [{k: v for k, v in r.items()} for r in rows] for m, rows in res.items()}})
    return summary


# ----------------------------------------------------------------- per-second cache

SECOND_COLS = ("vib_rms_x", "vib_rms_y", "vib_rms_z", "vib_velocity_mm_s", "vib_peak_g", "vib_kurtosis", "imu_temp_c",
               "th_max_c", "snd_level_db", "trend_shocks_10m", "trend_heat_rise_per_min",
               "vib_gravity_x", "vib_gravity_y", "vib_gravity_z",
               "snd_peak_db", "snd_kurt_hf", "vib_jerk", "gyr_peak", "trend_running", "vib_speed_hz", "trend_run_minutes")
_HOUR = re.compile(r"features_(\d+)\.npz")


def _seconds_one(name):
    dest = OUT / "seconds" / f"{name}.npz"
    if dest.exists():
        return name, 0.0
    t0 = time.time()
    files = sorted((int(m.group(1)), p) for p in (DATA / name / "features").glob("features_*.npz") if (m := _HOUR.fullmatch(p.name)))
    ts, cols = [], {c: [] for c in SECOND_COLS}
    for _, p in files:
        with np.load(p) as d:
            names = [str(n) for n in d["feature_names"]]
            idx = {n: i for i, n in enumerate(names)}
            X = d["X"]
            ts.append(np.asarray(d["t0"], float))
            for c in SECOND_COLS:
                cols[c].append(np.asarray(X[:, idx[c]], np.float32) if c in idx else np.full(len(X), np.nan, np.float32))
    t = np.concatenate(ts)
    order = np.argsort(t, kind="stable")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp.npz")
    np.savez(tmp, t=t[order], **{c: np.concatenate(v)[order] for c, v in cols.items()})
    tmp.rename(dest)
    return name, time.time() - t0


def run_seconds(workers=1):
    names = sorted(p.parent.name for p in DATA.glob("*/labels.json"))
    assert len(names) == 33, len(names)
    if workers > 1:
        from concurrent.futures import ProcessPoolExecutor
        with ProcessPoolExecutor(workers) as ex:
            for name, s in ex.map(_seconds_one, names):
                print(f"{name}: {s:.0f} s", flush=True)
    else:
        for name in names:
            print(f"{name}: {_seconds_one(name)[1]:.0f} s", flush=True)


# ----------------------------------------------------------------- one model system's flags (model alarm OR persistence)

def model_system_flags(model, smooth, sens, block=None, frac=None, hold=None):
    """{rot: {run: (minutes,) bool}} for one setting of a model + persistence (smooth "off": persistence only)."""
    Ps = prepared()
    out = {}
    for rot in ROTS:
        P = Ps[rot]
        cal, raw = scores(model, rot)
        f = {name: np.zeros(len(P.runs[name]["t"]), bool) for name in P.runs}
        if smooth != "off":
            _, ratio = MS.ratios(P, cal, raw, smooth)
            m = SF.margins(sens)["model"]
            for name in P.runs:
                f[name] |= np.nan_to_num(ratio[name], nan=-1.0) > m
        if block is not None:
            line = MS.lines_for(cal, 1)
            for name in P.runs:
                f[name] |= block_on(np.asarray(raw[name], float) / line, P.runs[name]["reset"], block, (frac,), (hold,))[:, 0]
        out[rot] = f
    return out


# ----------------------------------------------------------------- exp 2: per-second safety checks and spikes

SAFETY_SENS = (0.25, 0.5, 0.75)
SPIKE_FEATS = ("vib_kurtosis", "snd_kurt_hf", "vib_peak_g", "snd_peak_db", "gyr_peak", "vib_jerk")
SPIKE_Q = (99.9, 99.99)
SPIKE_W = (10, 30, 60)  # minutes
SPIKE_M = (1.0, 1.5, 2.0)  # times the most exceedances any training window had
EXTREME_D = (0.0, 0.5, 1.0)  # over the training maximum by this share of (max - p99.99)
_SEC = {}


def seconds(name):
    if name not in _SEC:
        with np.load(OUT / "seconds" / f"{name}.npz") as d:
            _SEC[name] = {k: (np.asarray(d[k], float) if k == "t" else np.asarray(d[k])) for k in d.files}  # float32 kept
    return _SEC[name]


def rot_baseline(rot):
    """The safety baseline as train_model makes it on the rotation's two training weeks (1-minute medians)."""
    R = MS.runs()
    X = np.vstack([R[w]["median"] for w in MS.ROTATIONS[rot]])
    run = MS._running(X)
    return SF.learn_baseline(X[run], F.FEATURE_NAMES, running=run, period_s=60)


def to_minutes(t_sec, on, minute_t):
    """Per-second on -> per minute: on at any second of the minute."""
    idx = np.searchsorted(minute_t, t_sec, "right") - 1
    ok = (idx >= 0) & (idx < len(minute_t)) & on
    hit = np.zeros(len(minute_t), bool)
    hit[idx[ok]] = True
    return hit


def safety_flags():
    """{rot: {run: {column: (minutes,) bool}}}: each check, universal, outside-normal and all, per sensitivity."""
    from training.record.conveyor_eval import early_warning_eval as EV
    Ps = prepared()
    out = {}
    for rot in ROTS:
        base = rot_baseline(rot)
        out[rot] = {}
        for name, d in Ps[rot].runs.items():
            s = seconds(name)
            C = {c: s[c] for c in EV.SAFETY_COLS}
            act = EV.safety_active(s["t"], C, base, SAFETY_SENS)
            cols = {}
            for sv in SAFETY_SENS:
                per = {n: to_minutes(s["t"], on, d["t"]) for n, on in act[sv].items()}
                for n, v in per.items():
                    if n in SF.RELATIVE or sv == SAFETY_SENS[0]:  # universal checks don't depend on sensitivity
                        cols[n if n not in SF.RELATIVE else f"{n}@{sv}"] = v
                z = np.zeros(len(d["t"]), bool)
                rel = np.logical_or.reduce([per.get(n, z) for n in SF.RELATIVE])
                uni = np.logical_or.reduce([per.get(n, z) for n in SF.UNIVERSAL + SF.SENSOR])
                cols[f"outside_normal@{sv}"] = rel
                cols["universal"] = uni
                cols[f"all_safety@{sv}"] = rel | uni
            out[rot][name] = cols
    return out


def _rolling_count(t, e, w_s):
    cs = np.r_[0, np.cumsum(e)]
    j = np.searchsorted(t, t - w_s, "right")
    return cs[1:] - cs[j]


def spike_flags():
    """{rot: {run: {column: (minutes,) bool}}}: per-second spike rates and extremes, thresholds from the rotation's
    training weeks only (running seconds)."""
    Ps = prepared()
    out, thresholds = {}, {}
    for rot in ROTS:
        train = [seconds(w) for w in MS.ROTATIONS[rot]]
        th = {}
        for f in SPIKE_FEATS:
            v = np.concatenate([d[f][d["trend_running"] > 0.5] for d in train])
            v = v[np.isfinite(v)]
            q = {qq: float(np.percentile(v, qq)) for qq in SPIKE_Q}
            mx = float(v.max())
            kmax = {}
            for qq in SPIKE_Q:
                for W in SPIKE_W:
                    kmax[(qq, W)] = max(float(_rolling_count(d["t"], (d[f] > q[qq]) & (d["trend_running"] > 0.5),
                                                             W * 60).max()) for d in train)
            th[f] = {"q": q, "max": mx, "kmax": kmax}
        thresholds[rot] = {f: {"q": v["q"], "max": v["max"], "kmax": {f"{a}/{b}": c for (a, b), c in v["kmax"].items()}}
                           for f, v in th.items()}
        out[rot] = {}
        for name, d in Ps[rot].runs.items():
            s = seconds(name)
            run = s["trend_running"] > 0.5
            cols = {}
            for f in SPIKE_FEATS:
                v = s[f]
                for qq in SPIKE_Q:
                    e = (v > th[f]["q"][qq]) & run
                    for W in SPIKE_W:
                        cnt = _rolling_count(s["t"], e, W * 60)
                        for m in SPIKE_M:
                            cols[f"rate {f} >p{qq} in {W} min >{m}x train max"] = to_minutes(
                                s["t"], cnt > m * max(th[f]["kmax"][(qq, W)], 1.0), d["t"])
                gap = th[f]["max"] - th[f]["q"][99.99]
                for dd in EXTREME_D:
                    cols[f"extreme {f} > train max +{dd} gap"] = to_minutes(s["t"], (v > th[f]["max"] + dd * gap) & run, d["t"])
            out[rot][name] = cols
    return out, thresholds


def judge_columns(colsets):
    """colsets[rot][run][column] -> (minutes,) bool. Judge every column in the rotation."""
    names = list(next(iter(next(iter(colsets.values())).values())).keys())
    fl = {rot: {run: np.stack([c[n] for n in names], 1) for run, c in runs_.items()} for rot, runs_ in colsets.items()}
    rows = judge_rot(fl, details=True)
    return {n: r for n, r in zip(names, rows)}


IMPULSIVE_RUNS = ("jam-obvious", "jam-subtle", "splice_damage-obvious", "splice_damage-subtle")


def caught_runs(r):
    """Runs caught in every rotation, and in some."""
    sets = [set(n for n, v in r["detail"][rot]["runs"].items() if v["caught"]) for rot in ROTS]
    return sorted(set.intersection(*sets)), sorted(set.union(*sets))


def or_flags(*sets):
    """OR of several {rot: {run: (minutes,) bool}}."""
    return {rot: {name: np.logical_or.reduce([s[rot][name] for s in sets]) for name in sets[0][rot]} for rot in ROTS}


def run_safety():
    t0 = time.time()
    saf = safety_flags()
    print(f"safety flags in {time.time() - t0:.0f} s", flush=True)
    spk, thresholds = spike_flags()
    print(f"spike flags in {time.time() - t0:.0f} s", flush=True)
    res = {}
    for label, cs in (("safety", saf), ("spike", spk)):
        judged = judge_columns(cs)
        for n, r in judged.items():
            allc, somec = caught_runs(r)
            res[n] = {"family": label, "fp_by_rotation": r["fp_by_rotation"], "worst_fp_week": r["worst_fp_week"],
                      "mean_missed": r["mean_missed"], "caught_every_rotation": allc, "caught_some_rotation": somec,
                      "impulsive_caught_every_rotation": [x for x in allc if x in IMPULSIVE_RUNS],
                      "minutes_by_run": {rot: {k: v["minutes"] for k, v in r["detail"][rot]["runs"].items() if v["caught"]}
                                         for rot in ROTS}}
    print("\nper-second columns alone (worst-rotation false alerts / week; runs caught in every rotation):")
    for n, r in sorted(res.items(), key=lambda kv: (kv[1]["family"], kv[1]["worst_fp_week"], kv[1]["mean_missed"])):
        if r["worst_fp_week"] <= 2.0 and r["caught_some_rotation"]:
            print(f"  {r['family']:6s} {n:60s} fp {r['fp_by_rotation']} | every {r['caught_every_rotation']} "
                  f"| some-only {sorted(set(r['caught_some_rotation']) - set(r['caught_every_rotation']))}")
    zero = {n: r for n, r in res.items() if r["worst_fp_week"] == 0.0}
    print("\nimpulsive runs caught at 0 false alerts in every rotation, by any column:",
          sorted(set(x for r in zero.values() for x in r["impulsive_caught_every_rotation"])))
    # OR with the model systems from exp 1, keeping 0 false alerts: add the 0-false-alert per-second columns
    systems = {"PCA + persistence (plateau)": ("PCA group-mean", 10, 0.6, 120, 0.25, 3),
               "PCA + persistence (strict)": ("PCA group-mean", 10, 0.625, 120, 0.15, 6),
               "PCA alone": ("PCA group-mean", 10, 0.625, None, None, None)}
    combos = {}
    zero_cols = [n for n, r in zero.items() if r["caught_some_rotation"]]
    for sname, spec in systems.items():
        base = model_system_flags(*spec)
        cand = {"model system": base}
        for n in zero_cols:
            src = saf if n in next(iter(next(iter(saf.values())).values())) else spk
            cand[f"+ {n}"] = or_flags(base, {rot: {run: src[rot][run][n] for run in src[rot]} for rot in ROTS})
        # all zero-fp per-second columns together
        if zero_cols:
            allz = {rot: {run: np.logical_or.reduce([(saf if n in saf[rot][run] else spk)[rot][run][n] for n in zero_cols])
                          for run in saf[rot]} for rot in ROTS}
            cand["+ every 0-false-alert per-second column"] = or_flags(base, allz)
        names = list(cand)
        fl = {rot: {run: np.stack([cand[k][rot][run] for k in names], 1) for run in base[rot]} for rot in ROTS}
        rows = judge_rot(fl, details=True)
        combos[sname] = {}
        print(f"\n{sname}: {spec}")
        for k, r in zip(names, rows):
            allc, _ = caught_runs(r)
            combos[sname][k] = {kk: r[kk] for kk in ("fp_by_rotation", "worst_fp_week", "missed_by_rotation", "mean_missed",
                                                     "mean_lateness")}
            combos[sname][k]["missed_runs_by_rotation"] = {rot: r["detail"][rot]["missed_runs"] for rot in ROTS}
            if k == "model system" or r["mean_missed"] < rows[0]["mean_missed"] or r["worst_fp_week"] > 0:
                print(f"  {k:70s} fp {r['fp_by_rotation']} missed {r['missed_by_rotation']} mean {r['mean_missed']} "
                      f"late {r['mean_lateness']}")
    write("safety.json", {"columns": res, "thresholds": thresholds, "combos": combos})


# ----------------------------------------------------------------- top-k inside each group (the unbalance dilution)

class TopKPCA:
    """Device-style PCA residual (var kept, no T2). Each input's squared residual over its own training spread; inside
    each sensor group the mean ("mean", = model_search's group-mean up to the per-input scaling), the mean of the k
    largest ("top3"...) or the largest ("max"), over that statistic's training mean; across groups the mean or max."""

    def __init__(self, within="top5", across="mean", var=0.95):
        self.within, self.across, self.var = within, across, var
        self.pca = MS.DevPCA(var=var, max_k=200, t2=False)

    def fit(self, Z):
        self.pca.fit(Z)
        r = self.pca._parts(Z)[1]
        self.sd = np.maximum(r.std(0), 1e-3)
        g = np.array([MS.input_group(n) for n in self.names])
        self.gidx = [np.nonzero(g == k)[0] for k in MS.GROUPS if (g == k).any()]
        self.gref = [1.0] * len(self.gidx)
        self.gref = [max(float(v), 1e-9) for v in self._groups(Z).mean(0)]
        return self

    def _groups(self, Z):
        q = (self.pca._parts(Z)[1] / self.sd) ** 2
        cols = []
        for i, ref in zip(self.gidx, self.gref):
            qi = q[:, i]
            if self.within == "mean":
                v = qi.mean(1)
            elif self.within == "max":
                v = qi.max(1)
            else:
                k = min(int(self.within[3:]), qi.shape[1])
                v = np.partition(qi, qi.shape[1] - k, axis=1)[:, -k:].mean(1)
            cols.append(v / ref)
        return np.stack(cols, 1)

    def score(self, Z):
        G = self._groups(Z)
        return G.mean(1) if self.across == "mean" else G.max(1)


_orig_detector = MS.detector


def _detector(approach, params):
    if approach == "PCA top-k":
        return TopKPCA(**params)
    return _orig_detector(approach, params)


MS.detector = _detector


def fit_scores(approach, params, kind="median"):
    """{rot: (cal, raw)} by model_search's crossfit for any detector (fits in memory, nothing cached)."""
    R = MS.runs()
    out = {}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for rot in ROTS:
            fold = MS._rot_fold(rot)
            prep, det, cal = MS.calibrated_fit(approach, params, kind, fold, "crossfit")
            raw = {n: det.score(MS._feed(prep, det, MS.make_input(kind, r)[0])) for n, r in R.items()
                   if n not in MS.WEEKS or fold in MS.ROTATIONS}
            out[rot] = (np.asarray(cal, float), raw)
    return out


def judge_model_rotation(sc, label):
    """model_search's judge_scores in each rotation, joined per (smooth, sensitivity), with a plateau over sens +-0.025."""
    Ps = prepared()
    by = {rot: MS.judge_scores(Ps[rot], *sc[rot], details=True) for rot in ROTS}
    rows = []
    for i, base in enumerate(by[ROTS[0]]):
        rr = {rot: by[rot][i] for rot in ROTS}
        rows.append({"model": label, "smooth": base["smooth"], "sensitivity": base["sensitivity"],
                     "fp_by_rotation": {r: v["fp"] for r, v in rr.items()},
                     "worst_fp_week": max(v["fp_week"] for v in rr.values()),
                     "missed_by_rotation": {r: v["missed"] for r, v in rr.items()},
                     "mean_missed": round(float(np.mean([v["missed"] for v in rr.values()])), 2),
                     "mean_lateness": round(float(np.mean([v["lateness"] for v in rr.values()])), 4),
                     "min_caught": min(v["caught"] for v in rr.values()),
                     "line_by_rotation": {r: round(float(v["line"]), 3) for r, v in rr.items()},
                     "missed_runs_by_rotation": {r: v["missed_runs"] for r, v in rr.items()}})
    idx = {(r["smooth"], r["sensitivity"]): r for r in rows}
    for r in rows:
        nb = [x for x in (idx.get((r["smooth"], round(r["sensitivity"] + d, 3))) for d in (-0.025, 0.0, 0.025)) if x]
        r["plateau_worst_fp_week"] = max(x["worst_fp_week"] for x in nb)
        r["plateau_mean_missed"] = max(x["mean_missed"] for x in nb)
    return rows


TOPK_VARIANTS = [(w, a) for w in ("mean", "top3", "top5", "top10", "max") for a in ("mean", "max")]


def run_topk():
    res = {}
    for within, across in TOPK_VARIANTS:
        t0 = time.time()
        label = f"PCA top-k within={within} across={across}"
        sc = fit_scores("PCA top-k", {"within": within, "across": across})
        rows = [r for r in judge_model_rotation(sc, label) if r["min_caught"] > 0]
        s = min(rows, key=rank_key)
        p = min(rows, key=lambda r: (r["plateau_worst_fp_week"], r["plateau_mean_missed"], r["mean_missed"], r["mean_lateness"]))
        res[label] = {"strict": s, "plateau": p, "frontier": frontier(rows, lambda r: f"n{r['smooth']} s{r['sensitivity']}"),
                      "scores": {rot: {"cal": sc[rot][0].tolist()} for rot in ROTS}}
        print(f"{label:42s} strict {s['mean_missed']:5} {s['missed_by_rotation']} fp {s['fp_by_rotation']} n{s['smooth']} "
              f"s{s['sensitivity']} | plateau {p['mean_missed']:5} (nb {p['plateau_mean_missed']}) | lines {s['line_by_rotation']} "
              f"[{time.time() - t0:.0f} s]", flush=True)
        um = {rot: "unbalance-obvious" in s["missed_runs_by_rotation"][rot] for rot in ROTS}
        print(f"{'':42s} unbalance-obvious missed: {um}; frontier {[(f['level'], f['mean_missed']) for f in res[label]['frontier']]}", flush=True)
        # keep raw scores for later combinations
        np.savez_compressed(OUT / f"scores_topk_{within}_{across}.npz",
                            **{f"{rot}__cal": sc[rot][0] for rot in ROTS},
                            **{f"{rot}__run__{n}": v for rot in ROTS for n, v in sc[rot][1].items()})
    for v in res.values():
        v.pop("scores")
    write("topk.json", res)


# ----------------------------------------------------------------- exp 3: heat relative to the camera's background

_orig_make_input = MS.make_input
_BG = F.INDEX["th_background_c"]
_CAM_ABS = [F.INDEX[n] for n in ("th_max_c", "th_mean_c")]  # cells, spots and hotspot are already above background
_IMU_ABS = [F.INDEX[n] for n in ("imu_temp_c", "trend_heat_1m", "trend_heat_10m", "trend_heat_1h", "trend_heat_6h",
                                 "trend_heat_24h")]  # the heat history follows the IMU when there is no room sensor


def _make_input(kind, r):
    if kind in ("relheat", "relheat_imu"):
        X = np.array(r["median"], float, copy=True)
        bg = X[:, _BG].copy()
        for j in _CAM_ABS + (_IMU_ABS if kind == "relheat_imu" else []):
            X[:, j] = X[:, j] - bg
        X[:, _BG] = 0.0  # constant: Prep drops it, so no absolute camera temperature is left
        return X, list(F.FEATURE_NAMES)
    return _orig_make_input(kind, r)


MS.make_input = _make_input
RELHEAT_MODELS = {"PCA group-mean": ("PCA (device, float)", {"max_k": 200, "t2": False, "var": 0.95, "group": "mean"}),
                  "Gaussian group-mean": ("Gaussian (Mahalanobis)", {"group": "mean"})}


def run_relheat():
    res = {}
    for model, (approach, params) in RELHEAT_MODELS.items():
        for kind in ("median", "relheat", "relheat_imu"):
            t0 = time.time()
            label = f"{model} | {kind}"
            sc = fit_scores(approach, params, kind)
            rows = [r for r in judge_model_rotation(sc, label) if r["min_caught"] > 0]
            s = min(rows, key=rank_key)
            p = min(rows, key=lambda r: (r["plateau_worst_fp_week"], r["plateau_mean_missed"], r["mean_missed"], r["mean_lateness"]))
            line10 = {rot: round(float(MS.lines_for(sc[rot][0], 10)), 2) for rot in ROTS}
            res[label] = {"strict": s, "plateau": p, "line_n10_by_rotation": line10,
                          "frontier": frontier(rows, lambda r: f"n{r['smooth']} s{r['sensitivity']}")}
            print(f"{label:40s} strict {s['mean_missed']:5} {s['missed_by_rotation']} fp {s['fp_by_rotation']} n{s['smooth']} "
                  f"s{s['sensitivity']} | plateau {p['mean_missed']} (nb {p['plateau_mean_missed']}) | line n10 {line10} "
                  f"[{time.time() - t0:.0f} s]", flush=True)
            np.savez_compressed(OUT / f"scores_{model.split()[0]}_{kind}.npz",
                                **{f"{rot}__cal": sc[rot][0] for rot in ROTS},
                                **{f"{rot}__run__{n}": v for rot in ROTS for n, v in sc[rot][1].items()})
    write("relheat.json", res)


# ----------------------------------------------------------------- exp 5 and 6: combinations and the final system

MODELS["AE group-mean"] = ("autoencoder (device, float)", "median", {"group": "mean"})


def load_npz_scores(fname):
    with np.load(OUT / fname) as d:
        out = {}
        for rot in ROTS:
            raw = {k.split("__run__", 1)[1]: d[k] for k in d.files if k.startswith(f"{rot}__run__")}
            out[rot] = (d[f"{rot}__cal"], raw)
    return out


def source(name):
    """{rot: (cal, raw)} for a model name in MODELS, or 'npz:<file>' saved by topk/relheat."""
    if name.startswith("npz:"):
        return load_npz_scores(name[4:])
    return {rot: scores(name, rot) for rot in ROTS}


def max_normalised(srcs):
    """One score: the max over models of raw / that model's 1-minute line, with calibration rows the same max
    (crossfit rows are in the same order for every model)."""
    out = {}
    for rot in ROTS:
        cals = [np.asarray(s[rot][0], float) for s in srcs]
        n = min(len(c) for c in cals)
        assert all(len(c) == n for c in cals), [len(c) for c in cals]
        lines = [MS.lines_for(c, 1) for c in cals]
        cal = np.max([c / l for c, l in zip(cals, lines)], 0)
        raw = {name: np.max([np.asarray(s[rot][1][name], float) / l for s, l in zip(srcs, lines)], 0) for name in srcs[0][rot][1]}
        out[rot] = (cal, raw)
    return out


def combo_rows(label, srcs, mode):
    """Model alarm (smoothing x a SHARED sensitivity, so the lines move together) OR persistence, judged in the rotation.
    mode "or": each model over its own line; "maxcal": the max of normalised scores with its own line."""
    Ps = prepared()
    comb = max_normalised(srcs)
    cols = [("off", None)] + [(n, s) for n in SMOOTHINGS for s in SENS]
    mflags = {}
    for rot in ROTS:
        P = Ps[rot]
        per_n = {}
        for n in SMOOTHINGS:
            if mode == "or":
                fl = None
                for s_ in srcs:
                    f = MS.model_flags(P, MS.ratios(P, *s_[rot], n)[1], SENS)
                    fl = f if fl is None else {k: fl[k] | f[k] for k in fl}
            else:
                fl = MS.model_flags(P, MS.ratios(P, *comb[rot], n)[1], SENS)
            per_n[n] = fl
        mflags[rot] = per_n
    rows = []
    for B in PERSIST_B:
        pf = {rot: persist_flags(Ps[rot], *comb[rot], B) for rot in ROTS}
        for pi, (f, h) in enumerate((f, h) for f in PERSIST_FRAC for h in PERSIST_HOLD):
            fl = {rot: {name: np.hstack([pf[rot][name][:, pi:pi + 1],
                                         np.hstack([mflags[rot][n][name] for n in SMOOTHINGS]) | pf[rot][name][:, pi:pi + 1]])
                        for name in Ps[rot].runs} for rot in ROTS}
            for (n, s), r in zip(cols, judge_rot(fl)):
                rows.append({"model": label, "smooth": n, "sensitivity": s, "block": B, "frac": f, "hold": h, **r})
    fl = {rot: {name: np.hstack([mflags[rot][n][name] for n in SMOOTHINGS]) for name in Ps[rot].runs} for rot in ROTS}
    for (n, s), r in zip(cols[1:], judge_rot(fl)):
        rows.append({"model": label, "smooth": n, "sensitivity": s, "block": None, "frac": None, "hold": None, **r})
    add_plateau(rows)
    return rows, comb, mflags


COMBOS = [("PCA", ["PCA group-mean"], "or"),
          ("PCA | Gauss", ["PCA group-mean", "Gaussian group-mean"], "or"),
          ("PCA | AE", ["PCA group-mean", "AE group-mean"], "or"),
          ("PCA | Gauss | AE", ["PCA group-mean", "Gaussian group-mean", "AE group-mean"], "or"),
          ("max(PCA, Gauss)", ["PCA group-mean", "Gaussian group-mean"], "maxcal"),
          ("max(PCA, AE)", ["PCA group-mean", "AE group-mean"], "maxcal"),
          ("max(PCA, Gauss, AE)", ["PCA group-mean", "Gaussian group-mean", "AE group-mean"], "maxcal")]


def flags_for(row, srcs, mode):
    """{rot: {run: (minutes,) bool}} for one combination row (rebuilt, so the final system can be OR'ed)."""
    Ps = prepared()
    comb = max_normalised(srcs)
    out = {}
    for rot in ROTS:
        P = Ps[rot]
        f = {name: np.zeros(len(P.runs[name]["t"]), bool) for name in P.runs}
        if row["smooth"] != "off":
            m = SF.margins(row["sensitivity"])["model"]
            for s_ in (srcs if mode == "or" else [comb]):
                _, ratio = MS.ratios(P, *s_[rot], row["smooth"])
                for name in P.runs:
                    f[name] |= np.nan_to_num(ratio[name], nan=-1.0) > m
        if row["block"] is not None:
            line = MS.lines_for(comb[rot][0], row.get("line_n", 1))
            for name in P.runs:
                f[name] |= block_on(np.asarray(comb[rot][1][name], float) / line, P.runs[name]["reset"], row["block"],
                                    (row["frac"],), (row["hold"],))[:, 0]
        out[rot] = f
    return out


# The per-second set, fixed BEFORE judging (not picked by 0 false alerts on the judged data): the device's own
# safety checks at the default sensitivity, and two impulsiveness rates at a middle setting of exp 2's plateau.
PER_SECOND_SET = ("all_safety@0.5", "rate snd_kurt_hf >p99.9 in 30 min >2.0x train max",
                  "rate vib_kurtosis >p99.9 in 30 min >2.0x train max")


def per_second_flags(cols=PER_SECOND_SET):
    saf = safety_flags()
    spk, _ = spike_flags()
    out = {}
    for rot in ROTS:
        out[rot] = {}
        for run in saf[rot]:
            out[rot][run] = np.logical_or.reduce([(saf if c in saf[rot][run] else spk)[rot][run][c] for c in cols])
    return out


def describe_c(r):
    return describe_p(r)


def summarise_row(r, flags=None):
    """The report for one system: per rotation fp, missed runs, median minutes, slow wear."""
    out = {k: r[k] for k in ("fp_by_rotation", "worst_fp_week", "missed_by_rotation", "mean_missed", "mean_lateness")}
    if flags is not None:
        d = judge_rot({rot: {run: f[:, None] for run, f in flags[rot].items()} for rot in ROTS}, details=True)[0]
        out["missed_runs_by_rotation"] = {rot: d["detail"][rot]["missed_runs"] for rot in ROTS}
        out["sudden_median_min_by_rotation"] = {rot: d["detail"][rot]["sudden_median_min"] for rot in ROTS}
        out["slow_wear_pct_median_by_rotation"] = {rot: d["detail"][rot]["slow_wear_caught_median"] for rot in ROTS}
        out["minutes_by_run"] = {rot: {k: v["minutes"] for k, v in d["detail"][rot]["runs"].items() if v["caught"]} for rot in ROTS}
        out["wear_pct_by_run"] = {rot: {k: v["wear_pct"] for k, v in d["detail"][rot]["runs"].items() if v["wear_pct"] is not None}
                                  for rot in ROTS}
    return out


COMBOS_RELHEAT = [("PCA rel", ["npz:scores_PCA_relheat_imu.npz"], "or"),
                  ("PCA rel | Gauss rel", ["npz:scores_PCA_relheat_imu.npz", "npz:scores_Gaussian_relheat_imu.npz"], "or"),
                  ("max(PCA rel, Gauss rel)", ["npz:scores_PCA_relheat_imu.npz", "npz:scores_Gaussian_relheat_imu.npz"], "maxcal")]


def system_frontier(rows, srcs, mode, ps, top=25):
    """The final system's frontier: for each false-alert level, the best of the `top` model rows (by mean missed at
    that level) once OR'ed with the per-second set (which adds no false alerts of its own, but can add catches)."""
    out = []
    for L in FP_LEVELS:
        pool = sorted([r for r in rows if r["worst_fp_week"] <= L + 1e-9 and r["min_caught"] > 0],
                      key=lambda r: (r["mean_missed"], r["mean_lateness"]))[:top]
        if not pool:
            continue
        fl = {rot: {} for rot in ROTS}
        flags = [or_flags(flags_for(r, srcs, mode), ps) for r in pool]
        for rot in ROTS:
            for run in flags[0][rot]:
                fl[rot][run] = np.stack([f[rot][run] for f in flags], 1)
        judged = judge_rot(fl)
        ok = [(j, r) for j, r in zip(judged, pool) if j["worst_fp_week"] <= L + 1e-9]
        j, r = min(ok, key=lambda x: (x[0]["mean_missed"], x[0]["mean_lateness"]))
        out.append({"level": L, "setting": describe_c(r), "model_only_missed": r["mean_missed"], "fp_by_rotation": j["fp_by_rotation"],
                    "missed_by_rotation": j["missed_by_rotation"], "mean_missed": j["mean_missed"], "mean_lateness": j["mean_lateness"]})
        print(f"  frontier <= {L}: {j['mean_missed']} {j['missed_by_rotation']} late {j['mean_lateness']} ({describe_c(r)})", flush=True)
    return out


def run_combine(combos=None, tag=""):
    combos = combos or COMBOS
    res, best_rows = {}, {}
    for label, names, mode in combos:
        t0 = time.time()
        srcs = [source(n) for n in names]
        rows, _, _ = combo_rows(label, srcs, mode)
        ok = [r for r in rows if r["min_caught"] > 0]
        s = min(ok, key=rank_key)
        p = min(ok, key=plateau_key)
        alone = min([r for r in ok if r["block"] is None], key=rank_key)
        res[label] = {"strict": s, "strict_neighbours": neighbours_p(rows, s), "plateau": p,
                      "plateau_neighbours": neighbours_p(rows, p), "no_persistence": alone,
                      "frontier": frontier(ok, describe_c)}
        print(f"{label:22s} alone {alone['mean_missed']:5} | +persist strict {s['mean_missed']:5} {s['missed_by_rotation']} "
              f"({describe_c(s)}) | plateau {p['mean_missed']:5} {p['missed_by_rotation']} nb {p['plateau_mean_missed']} "
              f"({describe_c(p)}) [{time.time() - t0:.0f} s]", flush=True)
        best_rows[label] = (s, p, srcs, mode, rows)
    write(f"combine{tag}.json", res)
    # exp 6: the final systems = model combination OR the fixed per-second set
    ps = per_second_flags()
    final = {}
    for label, (s, p, srcs, mode, rows) in best_rows.items():
        if label in ("PCA", "PCA | Gauss", "PCA rel", "PCA rel | Gauss rel"):
            print(f"FRONTIER {label} + per-second:", flush=True)
            final[f"{label} frontier + per-second"] = system_frontier([r for r in rows if r["min_caught"] > 0], srcs, mode, ps)
        for kind, r in (("strict", s), ("plateau", p)):
            f = flags_for(r, srcs, mode)
            joined = or_flags(f, ps)
            jr = judge_rot({rot: {run: joined[rot][run][:, None] for run in joined[rot]} for rot in ROTS})[0]
            final[f"{label} [{kind}] + per-second"] = {"setting": describe_c(r), "model_only": summarise_row(r),
                                                       "with_per_second": summarise_row(jr, joined)}
            print(f"FINAL {label:22s} [{kind:7s}] model {r['mean_missed']:5} -> + per-second {jr['mean_missed']:5} "
                  f"{jr['missed_by_rotation']} fp {jr['fp_by_rotation']} late {jr['mean_lateness']}", flush=True)
    write(f"final{tag}.json", final)


# ----------------------------------------------------------------- jams: short stops and stalls

MODELS["PCA device 0.99"] = ("PCA (device, float)", "median", {"max_k": 200, "t2": True, "var": 0.99})


def jam_events(name, stall_frac=0.9, stall_s=3, stop_max_min=30, confirm_s=600):
    """Alert times (s) of two jam signs, causal:
    stall  belt speed under stall_frac x its 5-minute running median for >= stall_s s while running, and the belt still
           running confirm_s later (so a normal stop's coast-down is not a stall); the alert waits for that confirmation
    stop   the belt stops and restarts within stop_max_min minutes; the alert is at the restart."""
    import pandas as pd
    d = seconds(name)
    t, sp, run = d["t"], d["vib_speed_hz"].astype(float), d["trend_running"] > 0.5
    med = pd.Series(np.where(run, sp, np.nan)).rolling(300, min_periods=60).median().to_numpy()
    low = run & (sp < stall_frac * med) & (sp > 1)
    stalls, i, n = [], 0, len(t)
    while i < n:
        if low[i]:
            j = i
            while j < n and low[j]:
                j += 1
            end = np.searchsorted(t, t[j - 1] + confirm_s)
            if j - i >= stall_s and end < n and run[j:end + 1].all():
                stalls.append(float(t[j - 1] + confirm_s))
            i = j
        else:
            i += 1
    stops = []
    ch = np.nonzero(np.diff(run.astype(np.int8)))[0]
    for k in ch:
        if run[k] and not run[k + 1]:
            r = np.nonzero(run[k + 1:])[0]
            if len(r) and t[k + 1 + r[0]] - t[k + 1] <= stop_max_min * 60:
                stops.append(float(t[k + 1 + r[0]]))
    return stalls, stops


JAM_VARIANTS = [("stall", dict(stall_frac=f, stall_s=n)) for f in (0.85, 0.9) for n in (2, 3, 4)] + \
               [("stop", dict(stop_max_min=m)) for m in (10, 30, 60)]
JAM_DEFAULT = ("stall frac=0.9 s=3", "stop max=30 min")


def jam_flags(hold_min=10):
    """{rot: {run: {column: (minutes,) bool}}}: each sign on for hold_min minutes from its alert; and the training
    weeks' event counts per rotation (the rate a healthy belt has)."""
    Ps = prepared()
    cols_of = {}
    for name in sorted({n for P in Ps.values() for n in P.runs} | set(MS.WEEKS) | {"heldout"}):
        cols = {}
        for kind, kw in JAM_VARIANTS:
            st, sp = jam_events(name, **({"stall_frac": kw.get("stall_frac", 0.9), "stall_s": kw.get("stall_s", 3),
                                          "stop_max_min": kw.get("stop_max_min", 30)}))
            label = (f"stall frac={kw['stall_frac']} s={kw['stall_s']}" if kind == "stall" else f"stop max={kw['stop_max_min']} min")
            cols[label] = st if kind == "stall" else sp
        cols_of[name] = cols
    training = {rot: {c: sum(len(cols_of[w][c]) for w in MS.ROTATIONS[rot]) for c in cols_of["heldout"]} for rot in ROTS}
    out = {}
    for rot in ROTS:
        out[rot] = {}
        for name, d in Ps[rot].runs.items():
            mt = d["t"]
            out[rot][name] = {}
            for c, times in cols_of[name].items():
                f = np.zeros(len(mt), bool)
                for a in times:
                    f |= (mt + 60 > a) & (mt <= a + hold_min * 60)
                out[rot][name][c] = f
    return out, training, {n: {c: [round(x) for x in v] for c, v in cols.items()} for n, cols in cols_of.items()
                           if n.startswith(("jam", "normal", "heldout"))}


SYSTEMS = {  # name -> (sources, mode, row)
    "headline: PCA rel | Gauss rel + persistence (plateau)": (
        ["npz:scores_PCA_relheat_imu.npz", "npz:scores_Gaussian_relheat_imu.npz"], "or",
        {"smooth": 10, "sensitivity": 0.625, "block": 60, "frac": 0.3, "hold": 4, "line_n": 1}),
    "model-search verified: group PCA + line EW": (
        ["PCA group-mean"], "or", {"smooth": 10, "sensitivity": 0.625, "block": 30, "frac": 0.3, "hold": 6, "line_n": 10}),
    "device-ready: device PCA 0.99 no cap + line EW": (
        ["PCA device 0.99"], "or", {"smooth": 10, "sensitivity": 0.65, "block": 30, "frac": 0.3, "hold": 6, "line_n": 10}),
}


def _neighbour_rows(row):
    out = [("setting", row)]
    for d in (-0.025, 0.025):
        out.append((f"sensitivity {round(row['sensitivity'] + d, 3)}", {**row, "sensitivity": round(row["sensitivity"] + d, 3)}))
    for d in (-0.1, 0.1):
        out.append((f"frac {round(row['frac'] + d, 2)}", {**row, "frac": round(row["frac"] + d, 2)}))
    for h in (row["hold"] - 1, row["hold"] + 1):
        out.append((f"hold {h}", {**row, "hold": h}))
    for b in {30: (60,), 60: (30, 120), 120: (60, 360)}[row["block"]]:
        out.append((f"block {b}", {**row, "block": b}))
    return out


def run_final2():
    t0 = time.time()
    ps = per_second_flags()
    jf, training, events = jam_flags()
    print(f"per-second and jam flags in {time.time() - t0:.0f} s; jam events in training weeks per rotation: {training}", flush=True)
    jcols = list(next(iter(next(iter(jf.values())).values())).keys())
    jres = judge_columns(jf)
    jam_alone = {}
    for c in jcols:
        r = jres[c]
        allc, somec = caught_runs(r)
        jam_alone[c] = {"fp_by_rotation": r["fp_by_rotation"], "worst_fp_week": r["worst_fp_week"], "caught_every_rotation": allc,
                        "caught_some_rotation": somec}
        print(f"  jam sign {c:24s} fp {r['fp_by_rotation']} | every {allc} | some-only {sorted(set(somec) - set(allc))}", flush=True)
    jam_set = {rot: {run: jf[rot][run][JAM_DEFAULT[0]] | jf[rot][run][JAM_DEFAULT[1]] for run in jf[rot]} for rot in ROTS}
    res = {"jam_alone": jam_alone, "jam_training_events": training, "jam_event_times": events, "systems": {}}
    for sname, (names, mode, row) in SYSTEMS.items():
        srcs = [source(n) for n in names]
        variants = {}
        for nlabel, nrow in _neighbour_rows(row):
            base = flags_for(nrow, srcs, mode)
            variants[nlabel] = {"alone": base, "+ per-second": or_flags(base, ps), "+ per-second + jam": or_flags(base, ps, jam_set)}
        keys = [(n, v) for n in variants for v in variants[n]]
        fl = {rot: {run: np.stack([variants[n][v][rot][run] for n, v in keys], 1) for run in jam_set[rot]} for rot in ROTS}
        rows = judge_rot(fl, details=True)
        out = {}
        for (n, v), r in zip(keys, rows):
            out.setdefault(n, {})[v] = {k: r[k] for k in ("fp_by_rotation", "worst_fp_week", "missed_by_rotation", "mean_missed",
                                                          "mean_lateness")}
            if n == "setting":
                out[n][v].update({"missed_runs_by_rotation": {rot: r["detail"][rot]["missed_runs"] for rot in ROTS},
                                  "sudden_median_min_by_rotation": {rot: r["detail"][rot]["sudden_median_min"] for rot in ROTS},
                                  "slow_wear_pct_median_by_rotation": {rot: r["detail"][rot]["slow_wear_caught_median"] for rot in ROTS}})
        nb = [x for k, x in out.items() if k != "setting"]
        for v in ("alone", "+ per-second", "+ per-second + jam"):
            out.setdefault("plateau", {})[v] = {"worst_fp_week": max(x[v]["worst_fp_week"] for x in nb + [out["setting"]]),
                                                "worst_mean_missed": max(x[v]["mean_missed"] for x in nb + [out["setting"]])}
        res["systems"][sname] = {"row": row, "models": names, "mode": mode, "results": out}
        print(f"\n{sname}  {row}", flush=True)
        for v in ("alone", "+ per-second", "+ per-second + jam"):
            r = out["setting"][v]
            print(f"  {v:20s} missed {r['mean_missed']:5} {r['missed_by_rotation']} fp {r['fp_by_rotation']} late {r['mean_lateness']} "
                  f"| worst neighbour: fp {out['plateau'][v]['worst_fp_week']} missed {out['plateau'][v]['worst_mean_missed']}", flush=True)
        print("  neighbours (+ per-second + jam):", [(k, x["+ per-second + jam"]["worst_fp_week"], x["+ per-second + jam"]["mean_missed"])
                                                    for k, x in out.items() if k not in ("setting", "plateau")], flush=True)
    write("final2.json", res)


# ----------------------------------------------------------------- exp 8 and 9: faint slow faults, load conditioning

def _sub(n):
    """Physics sub-groups of the prepared inputs (by frequency and kind, not by fault): None = not used."""
    b = n[:-4] if n.endswith("_max") else n
    m = re.match(r"(vib|snd)_band_(\d+)$", b)
    if m:
        k = int(m.group(2))
        if m.group(1) == "vib":
            return "vib_high" if k >= 10 else "vib_low"
        return "snd_high" if k >= 16 else "snd_low"
    if b in ("vib_env_peak", "vib_env_order", "vib_kurtosis", "vib_harm_3_10", "vib_crest", "vib_peak2"):
        return "vib_high"
    if b in ("snd_kurt_hf", "snd_centroid_hz", "snd_env_peak_db", "snd_click", "snd_flatness"):
        return "snd_high"
    if b in ("snd_level_db", "snd_tone_db", "snd_mains_db", "vib_speed_hz", "vib_velocity_mm_s", "vib_1x"):
        return "snd_low" if b.startswith("snd") else "vib_low"
    if b in ("imu_temp_c", "trend_heat_1m", "trend_heat_10m", "trend_heat_1h", "trend_heat_6h", "trend_heat_rise_per_h",
             "trend_heat_rise_per_min"):
        return "heat_imu"  # not the camera: each fault run is effectively another camera (N2)
    return None


SUBGROUPS = ("vib_high", "vib_low", "snd_high", "snd_low", "heat_imu")


def _subparts(det, Z, sd, gidx):
    r = det._parts(Z)[1] / sd
    return np.stack([np.log((r[:, i] ** 2).mean(1) + 1e-6) for i in gidx], 1)


def subgroup_crossfit(rot):
    """Per-minute log mean-square standardised PCA residual per physics sub-group: out-of-sample for each training week
    (crossfit; list of per-week arrays) and for every judged run (final model)."""
    a, kind, p = MODELS["PCA group-mean"]
    chunks, names = MS.fold_chunks(MS._rot_fold(rot), kind)
    weeks = []
    for g in range(len(chunks)):
        tr = [c for i, c in enumerate(chunks) if i != g]
        t = np.concatenate([c[0] for c in tr]); X = np.vstack([c[1] for c in tr])
        run = MS._running(X)
        prep, det = MS.fit_detector(a, p, t[run], X[run], names)
        Z = MS._feed(prep, det, X[run])
        sd = np.maximum(det._parts(Z)[1].std(0), 1e-3)
        gidx = [np.nonzero(np.array([_sub(n) == k for n in det.names]))[0] for k in SUBGROUPS]
        Xg = chunks[g][1]
        weeks.append((_subparts(det, MS._feed(prep, det, Xg), sd, gidx), MS._running(Xg)))
    t = np.concatenate([c[0] for c in chunks]); X = np.vstack([c[1] for c in chunks])
    run = MS._running(X)
    prep, det = MS.fit_detector(a, p, t[run], X[run], names)
    Z = MS._feed(prep, det, X[run])
    sd = np.maximum(det._parts(Z)[1].std(0), 1e-3)
    gidx = [np.nonzero(np.array([_sub(n) == k for n in det.names]))[0] for k in SUBGROUPS]
    R = MS.runs()
    runs_ = {n: _subparts(det, MS._feed(prep, det, MS.make_input(kind, R[n])[0]), sd, gidx) for n in prepared()[rot].runs}
    return weeks, runs_, [len(i) for i in gidx]


def _blocks(v, dev_run, B):
    ri = np.nonzero(dev_run)[0]
    nb = len(ri) // B
    if nb == 0:
        return np.zeros((0, v.shape[1])), np.zeros(0, int)
    med = np.median(v[ri[:nb * B]].reshape(nb, B, -1), 1)
    return med, ri[np.arange(1, nb + 1) * B - 1]


def _cusum(z, k):
    S = np.zeros_like(z)
    s = np.zeros(z.shape[1])
    for i in range(len(z)):
        s = np.maximum(0.0, s + z[i] - k)
        S[i] = s
    return S


FAINT_B, FAINT_K, FAINT_F = (60, 120), (0.5, 1.0), (1.0, 1.5, 2.0)


def faint_flags():
    """{rot: {run: {column: (minutes,) bool}}}: a slow CUSUM per sub-group on block medians, standardised and given its
    limit by the training weeks scored out of sample (limit = factor x the highest CUSUM any training week reached)."""
    Ps = prepared()
    out, limits = {}, {}
    for rot in ROTS:
        P = Ps[rot]
        weeks, runs_, sizes = subgroup_crossfit(rot)
        out[rot] = {name: {} for name in P.runs}
        limits[rot] = {"inputs_per_subgroup": dict(zip(SUBGROUPS, sizes))}
        for B in FAINT_B:
            wb = [_blocks(v, r, B)[0] for v, r in weeks]
            allb = np.vstack(wb)
            med = np.median(allb, 0)
            sd = np.maximum(1.4826 * np.median(np.abs(allb - med), 0), 1e-3)
            for k in FAINT_K:
                hmax = np.max([_cusum((b - med) / sd, k).max(0) for b in wb], 0)  # per sub-group, out of sample
                limits[rot][f"B{B} k{k}"] = dict(zip(SUBGROUPS, np.round(hmax, 2).tolist()))
                for name, v in runs_.items():
                    b, close = _blocks(v, P.runs[name]["reset"], B)
                    S = _cusum((b - med) / sd, k) if len(b) else np.zeros((0, len(SUBGROUPS)))
                    ends = np.r_[close[1:], len(v)] if len(close) else np.zeros(0, int)
                    for f in FAINT_F:
                        on = S > f * np.maximum(hmax, 1.0)
                        for gi, gname in enumerate(SUBGROUPS + ("any",)):
                            col = on.any(1) if gname == "any" else on[:, gi]
                            fl = np.zeros(len(v), bool)
                            for i in np.nonzero(col)[0]:
                                fl[close[i]:ends[i]] = True
                            out[rot][name][f"cusum {gname} B{B} k{k} x{f}"] = fl
    return out, limits


def stratified_ratio(P, cal, raw, rot, n, strata=3):
    """The model's smoothed score over a line per load stratum: strata by belt speed (it falls under load through belt
    slip), cut at the training minutes' quantiles; each stratum's line from its own crossfit calibration minutes, at
    1 false alarm a week in all."""
    R = MS.runs()
    chunks, _ = MS.fold_chunks(MS._rot_fold(rot), "median")
    X = np.vstack([c[1] for c in chunks])
    spd_cal = X[MS._running(X), F.INDEX["vib_speed_hz"]]
    assert len(spd_cal) == len(cal), (len(spd_cal), len(cal))
    cuts = np.nanquantile(spd_cal, np.linspace(0, 1, strata + 1)[1:-1])
    sc = np.digitize(np.nan_to_num(spd_cal, nan=np.nanmedian(spd_cal)), cuts)
    sm_cal = MS._causal_median(np.asarray(cal, float), n)
    lines = [MS.gamma_threshold(sm_cal[sc == i], MS.ROWS_A_WEEK / strata) for i in range(strata)]
    out = {}
    for name in P.runs:
        spd = R[name]["median"][:, F.INDEX["vib_speed_hz"]]
        si = np.digitize(np.nan_to_num(spd, nan=np.nanmedian(spd_cal)), cuts)
        out[name] = MS.smoothed(raw[name], P.runs[name]["reset"], n) / np.asarray(lines)[si]
    return out, lines


def run_faint():
    Ps = prepared()
    names, mode, row = SYSTEMS["model-search verified: group PCA + line EW"]
    row = {**row, "sensitivity": 0.6}
    srcs = [source(n) for n in names]
    t0 = time.time()
    ps = per_second_flags()
    jf, _, _ = jam_flags()
    jam_set = {rot: {run: jf[rot][run][JAM_DEFAULT[0]] | jf[rot][run][JAM_DEFAULT[1]] for run in jf[rot]} for rot in ROTS}
    base = or_flags(flags_for(row, srcs, mode), ps, jam_set)
    b = judge_rot({rot: {run: base[rot][run][:, None] for run in base[rot]} for rot in ROTS}, details=True)[0]
    print(f"base A s0.6 + per-second + jam: {b['mean_missed']} {b['missed_by_rotation']} fp {b['fp_by_rotation']} [{time.time() - t0:.0f} s]", flush=True)
    res = {"base": {k: b[k] for k in ("fp_by_rotation", "mean_missed", "missed_by_rotation", "mean_lateness")}}
    # exp 8
    ff, limits = faint_flags()
    cols = list(next(iter(next(iter(ff.values())).values())).keys())
    alone = judge_columns(ff)
    fl = {rot: {run: np.stack([base[rot][run] | ff[rot][run][c] for c in cols], 1) for run in base[rot]} for rot in ROTS}
    joined = dict(zip(cols, judge_rot(fl, details=True)))
    res["exp8"] = {"limits": limits, "columns": {}}
    print("\nexp 8: slow CUSUM per sub-group (alone fp | with A: fp, missed, gained runs)", flush=True)
    for c in cols:
        a_, j = alone[c], joined[c]
        gained = {rot: sorted(set(b["detail"][rot]["missed_runs"]) - set(j["detail"][rot]["missed_runs"])) for rot in ROTS}
        res["exp8"]["columns"][c] = {"alone_fp": a_["fp_by_rotation"], "alone_caught_every": caught_runs(a_)[0],
                                     "with_A": {k: j[k] for k in ("fp_by_rotation", "worst_fp_week", "mean_missed", "mean_lateness")},
                                     "gained": gained}
        if j["worst_fp_week"] == 0 or c.startswith("cusum any"):
            print(f"  {c:32s} alone fp {a_['fp_by_rotation']} | with A fp {j['fp_by_rotation']} missed {j['mean_missed']} gained {gained}", flush=True)
    # exp 9
    res["exp9"] = {}
    print("\nexp 9: model alarm on a line per load stratum (belt speed), else A unchanged", flush=True)
    for strata in (2, 3):
        cols9, flags9 = [], {rot: {run: [] for run in base[rot]} for rot in ROTS}
        lines9 = {}
        for rot in ROTS:
            P = Ps[rot]
            cal, raw = srcs[0][rot]
            ratio, lines9[rot] = stratified_ratio(P, cal, raw, rot, row["smooth"], strata)
            pf = flags_for({**row, "smooth": "off"}, srcs, mode)[rot]  # the persistence part of A alone
            for sv in (0.55, 0.575, 0.6, 0.625, 0.65):
                m = SF.margins(sv)["model"]
                for run in base[rot]:
                    flags9[rot][run].append((np.nan_to_num(ratio[run], nan=-1.0) > m) | pf[run] | ps[rot][run] | jam_set[rot][run])
        fl = {rot: {run: np.stack(v, 1) for run, v in flags9[rot].items()} for rot in ROTS}
        for sv, j in zip((0.55, 0.575, 0.6, 0.625, 0.65), judge_rot(fl, details=True)):
            gained = {rot: sorted(set(b["detail"][rot]["missed_runs"]) - set(j["detail"][rot]["missed_runs"])) for rot in ROTS}
            lost = {rot: sorted(set(j["detail"][rot]["missed_runs"]) - set(b["detail"][rot]["missed_runs"])) for rot in ROTS}
            res["exp9"][f"strata {strata} s {sv}"] = {k: j[k] for k in ("fp_by_rotation", "worst_fp_week", "mean_missed", "mean_lateness")}
            res["exp9"][f"strata {strata} s {sv}"].update(gained=gained, lost=lost)
            print(f"  strata {strata} s {sv}: fp {j['fp_by_rotation']} missed {j['mean_missed']} {j['missed_by_rotation']} gained {gained} lost {lost}", flush=True)
        res["exp9"][f"strata {strata} lines"] = {rot: [round(float(x), 2) for x in l] for rot, l in lines9.items()}
        print(f"  lines per stratum: {res['exp9'][f'strata {strata} lines']}", flush=True)
    write("faint.json", res)


# ----------------------------------------------------------------- exp 10: per-group kNN, quantile-normalised

KNN_K = (5, 10, 20)
KNN_MEM = 5000


def _group_knn_fit(Z, names, seed=0):
    g = np.array([MS.input_group(n) for n in names])
    rng = np.random.default_rng(seed)
    mem = {}
    for k in MS.GROUPS:
        idx = np.nonzero(g == k)[0]
        if not len(idx):
            continue
        M = Z[:, idx]
        if len(M) > KNN_MEM:
            M = M[rng.choice(len(M), KNN_MEM, replace=False)]
        mem[k] = (idx, np.asarray(M, np.float32))
    return mem


def _group_knn_score(mem, Z):
    """{group: (rows, len(KNN_K))}: the mean distance to the k nearest memory rows, per k."""
    out = {}
    kmax = max(KNN_K)
    for k, (idx, M) in mem.items():
        Q = np.asarray(Z[:, idx], np.float32)
        m2 = (M ** 2).sum(1)
        res = np.empty((len(Q), len(KNN_K)), np.float32)
        for a in range(0, len(Q), 2000):
            q = Q[a:a + 2000]
            d2 = np.maximum((q ** 2).sum(1)[:, None] + m2[None, :] - 2 * q @ M.T, 0)
            part = np.sqrt(np.partition(d2, kmax, axis=1)[:, :kmax])
            part.sort(1)
            res[a:a + 2000] = np.stack([part[:, :kk].mean(1) for kk in KNN_K], 1)
        out[k] = res
    return out


def knn_crossfit(rot):
    """Per-group kNN scores: out of sample for each training week (crossfit) and for every judged run (final memory)."""
    chunks, names = MS.fold_chunks(MS._rot_fold(rot), "median")
    R = MS.runs()
    cal = []
    for g in range(len(chunks)):
        tr = [c for i, c in enumerate(chunks) if i != g]
        t = np.concatenate([c[0] for c in tr]); X = np.vstack([c[1] for c in tr])
        run = MS._running(X)
        prep = MS.Prep.fit(X[run], names, t[run], running_only=False)
        mem = _group_knn_fit(prep.transform(X[run]), prep.names_out)
        Xg = chunks[g][1]
        cal.append(_group_knn_score(mem, prep.transform(Xg[MS._running(Xg)])))
    cal = {k: np.vstack([c[k] for c in cal]) for k in cal[0]}
    t = np.concatenate([c[0] for c in chunks]); X = np.vstack([c[1] for c in chunks])
    run = MS._running(X)
    prep = MS.Prep.fit(X[run], names, t[run], running_only=False)
    mem = _group_knn_fit(prep.transform(X[run]), prep.names_out)
    raw = {n: _group_knn_score(mem, prep.transform(MS.make_input("median", R[n])[0])) for n in prepared()[rot].runs}
    return cal, raw


def _surprisal(ref, v):
    """-log of the exceedance share of v against the reference (out-of-sample training) scores: an ECDF quantile."""
    r = np.sort(ref)
    q = np.searchsorted(r, v, "right") / (len(r) + 1.0)
    return -np.log(np.maximum(1.0 - q, 1.0 / (len(r) + 1.0)))


def knn_scores():
    """{(k, combine): {rot: (cal, raw)}}: per-group quantiles (surprisal) against the crossfit training scores, combined
    by mean or max."""
    out = {}
    for rot in ROTS:
        t0 = time.time()
        cal, raw = knn_crossfit(rot)
        for ki, kk in enumerate(KNN_K):
            calq = np.stack([_surprisal(cal[g][:, ki], cal[g][:, ki]) for g in cal], 1)
            rawq = {n: np.stack([_surprisal(cal[g][:, ki], v[g][:, ki]) for g in cal], 1) for n, v in raw.items()}
            for comb in ("mean", "max"):
                f = (lambda A: A.mean(1)) if comb == "mean" else (lambda A: A.max(1))
                out.setdefault((kk, comb), {})[rot] = (f(calq), {n: f(v) for n, v in rawq.items()})
        print(f"kNN crossfit {rot}: {time.time() - t0:.0f} s", flush=True)
    return out


def run_knn():
    sc = knn_scores()
    for (kk, comb), v in sc.items():
        np.savez_compressed(OUT / f"scores_knn_k{kk}_{comb}.npz", **{f"{rot}__cal": v[rot][0] for rot in ROTS},
                            **{f"{rot}__run__{n}": x for rot in ROTS for n, x in v[rot][1].items()})
    ps = per_second_flags()
    jf, _, _ = jam_flags()
    jam = {rot: {r: jf[rot][r][JAM_DEFAULT[0]] | jf[rot][r][JAM_DEFAULT[1]] for r in jf[rot]} for rot in ROTS}
    namesA, modeA, rowA = SYSTEMS["model-search verified: group PCA + line EW"]
    A = or_flags(flags_for({**rowA, "sensitivity": 0.6}, [source(n) for n in namesA], modeA), ps, jam)
    res = {}
    for (kk, comb), v in sc.items():
        label = f"group kNN k{kk} {comb}"
        rows = [r for r in judge_model_rotation(v, label) if r["min_caught"] > 0]
        st = min(rows, key=rank_key)
        pl = min(rows, key=lambda r: (r["plateau_worst_fp_week"], r["plateau_mean_missed"], r["mean_missed"], r["mean_lateness"]))
        # the kNN alarm at its plateau pick, plus the line early warning like A, then the per-second set and jam rules; and OR system A
        keys, cols = [], {rot: {r: [] for r in A[rot]} for rot in ROTS}
        for sv in sorted({round(pl["sensitivity"] + d, 3) for d in (-0.025, 0.0, 0.025)}):
            for lab, row in ((f"alarm s{sv}", {"smooth": pl["smooth"], "sensitivity": sv, "block": None}),
                             (f"alarm s{sv} + line EW", {"smooth": pl["smooth"], "sensitivity": sv, "block": 30, "frac": 0.3,
                                                         "hold": 6, "line_n": 10})):
                f = flags_for(row, [v], "or")
                for extra, ff in (("", f), (" + per-second + jam", or_flags(f, ps, jam)), (" OR system A", or_flags(f, A))):
                    keys.append(lab + extra)
                    for rot in ROTS:
                        for r in A[rot]:
                            cols[rot][r].append(ff[rot][r])
        fl = {rot: {r: np.stack(x, 1) for r, x in cols[rot].items()} for rot in ROTS}
        judged = dict(zip(keys, judge_rot(fl)))
        res[label] = {"strict": st, "plateau": pl, "systems": {k: {x: j[x] for x in ("fp_by_rotation", "worst_fp_week", "mean_missed",
                                                                                     "missed_by_rotation", "mean_lateness")}
                                                               for k, j in judged.items()}}
        print(f"\n{label}: alone strict {st['mean_missed']} {st['missed_by_rotation']} (n{st['smooth']} s{st['sensitivity']}) | "
              f"plateau {pl['mean_missed']} (nb {pl['plateau_mean_missed']}, n{pl['smooth']} s{pl['sensitivity']})", flush=True)
        for k, j in judged.items():
            print(f"   {k:44s} fp {j['fp_by_rotation']} missed {j['mean_missed']} {j['missed_by_rotation']}", flush=True)
    write("knn.json", res)


# ----------------------------------------------------------------- exp 4: why unbalance-obvious is missed

def _group_parts(det, Z):
    r2 = det._parts(Z)[1] ** 2
    return np.stack([r2[:, i].mean(1) / ref for i, ref in zip(det._gidx, det._gref)], 1)


def group_crossfit(model_params, kind, rot):
    """model_search's crossfit for the group-mean PCA, keeping each group's share: (final prep and det,
    calibration group parts (out of sample), group names)."""
    chunks, names = MS.fold_chunks(MS._rot_fold(rot), kind)
    t = np.concatenate([c[0] for c in chunks])
    X = np.vstack([c[1] for c in chunks])
    run = MS._running(X)
    t, X = t[run], X[run]
    sizes = [int(MS._running(c[1]).sum()) for c in chunks]
    part = np.repeat(np.arange(len(chunks)), sizes)
    cal = []
    for g in range(part.max() + 1):
        prep, det = MS.fit_detector("PCA (device, float)", model_params, t[part != g], X[part != g], names)
        cal.append(_group_parts(det, MS._feed(prep, det, X[part == g])))
    prep, det = MS.fit_detector("PCA (device, float)", model_params, t, X, names)
    gnames = [k for k in MS.GROUPS if any(MS.input_group(n) == k for n in det.names)]
    return prep, det, np.vstack(cal), gnames, part


def run_unbalance(run_name="unbalance-obvious", smooth=10, sens=0.625):
    a, kind, p = MODELS["PCA group-mean"]
    R = MS.runs()
    Ps = prepared()
    out = {}
    for rot in ROTS:
        prep, det, calG, gnames, part = group_crossfit(p, kind, rot)
        cal = calG.mean(1)  # = the group-mean score, out of sample
        line = MS.lines_for(cal, smooth)
        thr = line * SF.margins(sens)["model"]
        d = Ps[rot].runs[run_name]
        G = _group_parts(det, MS._feed(prep, det, MS.make_input(kind, R[run_name])[0]))
        win = d["win"] & d["run"]
        pre = d["fpmask"]
        sm = MS.smoothed(G.mean(1), d["reset"], smooth)
        weeks = MS.ROTATIONS[rot]
        cal_by_week = {w: {g: round(float(np.percentile(calG[part == i][:, j], 99)), 2) for j, g in enumerate(gnames)}
                       for i, w in enumerate(weeks)}
        out[rot] = {"line": round(float(line), 3), "alarm_level": round(float(thr), 3),
                    "smoothed_score_in_window": {"median": round(float(np.nanmedian(sm[win])), 3),
                                                 "p90": round(float(np.nanpercentile(sm[win], 90)), 3),
                                                 "max": round(float(np.nanmax(sm[win])), 3)},
                    "group_parts_in_window_median": {g: round(float(np.median(G[win, j])), 2) for j, g in enumerate(gnames)},
                    "group_parts_before_onset_median": {g: round(float(np.median(G[pre, j])), 2) for j, g in enumerate(gnames)},
                    "calibration_p99_by_group_and_scored_week": cal_by_week,
                    "calibration_score_p99": round(float(np.percentile(cal, 99)), 3),
                    "vib_velocity_mm_s_window_median": round(float(np.nanmedian(R[run_name]["median"][win, F.INDEX["vib_velocity_mm_s"]])), 2)}
        o = out[rot]
        print(f"\n{rot} (learn {weeks}): line {o['line']}, alarm at {o['alarm_level']} | smoothed score in window: "
              f"{o['smoothed_score_in_window']} | velocity {o['vib_velocity_mm_s_window_median']} mm/s")
        print(f"  group parts in window (median): {o['group_parts_in_window_median']}  before onset: "
              f"{o['group_parts_before_onset_median']}")
        print(f"  calibration p99 by group, per out-of-sample week: {o['calibration_p99_by_group_and_scored_week']}")
    write("unbalance.json", out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=("seconds", "persist", "safety", "topk", "relheat", "unbalance", "combine", "combine_rel", "final2", "faint", "knn"))
    ap.add_argument("--workers", type=int, default=1)
    a = ap.parse_args(argv)
    t0 = time.time()
    if a.step == "seconds":
        run_seconds(a.workers)
    elif a.step == "persist":
        run_persist()
    elif a.step == "safety":
        run_safety()
    elif a.step == "unbalance":
        run_unbalance()
    elif a.step == "topk":
        run_topk()
    elif a.step == "relheat":
        run_relheat()
    elif a.step == "combine":
        run_combine()
    elif a.step == "knn":
        run_knn()
    elif a.step == "faint":
        run_faint()
    elif a.step == "final2":
        run_final2()
    elif a.step == "combine_rel":
        run_combine(COMBOS_RELHEAT, "_relheat")
    else:
        raise SystemExit(f"{a.step}: not written yet")
    print(f"done in {time.time() - t0:.0f} s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
