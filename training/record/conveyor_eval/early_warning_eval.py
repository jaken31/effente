#!/usr/bin/env python3
"""Early warning on the realistic conveyor (generate.py's runs): which approach, and which settings of the
whole device, catch faults EARLIEST (at the smallest size) within a false-alert budget?

The model is the device's own (effente.trainer, PCA, 1-minute periods) learned from the per-minute medians
of normal-w1 and normal-w2, with the trend features of a week or more left out, as evaluate.py does.
The early warning's normal (effente.early_warning.calibrate) comes from the same rows, as on the device.

Per run, minute by minute:
  - the model's alarm: the device's Watcher level (the median of the last 5 scores over the line) against
    margins(s)["model"] for a model sensitivity s, as evaluate.py's sweep does
  - the safety checks (effente.safety: universal limits and outside-normal against the learned baseline,
    held 10 s), on the one-a-second rows, at the same sensitivity (a vectorised copy of Safety.update,
    checked against it with --check-safety)
  - each early warning (effente.early_warning.METHODS) at its own sensitivity
The combined device alerts while any of them is on.

Two reports:
  1. Early warning alone, next to the alarm at the default sensitivity: false warnings, lead before the
     alarm, extra catches, wording. Each method also at its "tuned" setting: the most sensitive one with
     at most TUNE_FALSE false warnings over the two training weeks (learn on one, run on the other).
  2. The combined device over a grid of (model sensitivity, early-warning method and sensitivity):
     false alerts a week (training folds, held-out week, before the onset in fault runs) against how early:
       creeping faults (SLOW): the wear at the first alert, as a % of the obvious fault's full size
                                (evaluate.wear_level); lower is earlier
       sudden faults (the rest): subtle ones caught at all; minutes to the alert as a second measure
     Recommendations per budget (BUDGETS false alerts a week) are chosen by the TRAINING folds' false-alert
     rate, never the test runs; the held-out week's rate is shown as the check.
Running minutes only; the first 6 h of each run are the history warming up and not judged.

    python3 -m training.record.conveyor_eval.early_warning_eval --data data/fake/conveyor-eval \\
        --out data/fake/early-warning-results.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np

from effente import early_warning as EW
from effente import fault_types as FT
from effente import features as F
from effente import model_runtime as RT
from effente import safety as SF
from effente.model_input import load_recording
from effente.trainer import train_model
from training.record.conveyor_eval.evaluate import (EXPECTED, WARMUP_S, episodes, full_nuisances, minutes_of,
                                                   running_mask, wear_level)

SLOW = ("idler_bearing", "gear_wear", "motor_bearing", "lubrication_loss")
TUNE_FALSE = 2  # early warning alone: false warnings allowed over the two training weeks (about one a week)
TUNE_GRID = tuple(round(x, 2) for x in np.linspace(0.0, 1.0, 21))
SWEEP = (0.0, 0.25, 0.5, 0.6, 0.75, 0.9, 1.0)
ALARM_SWEEP = (0.6, 0.75, 0.9, 1.0)  # the "sensitivity" method: the alarm's own test at these
MODEL_SWEEP = SWEEP  # the model's (and safety checks') sensitivity in the combined device
COMBINED_METHODS = tuple(m for m in EW.METHODS if m != "sensitivity")  # that one IS the model sweep there
BUDGETS = (0.5, 1.0, 2.0)  # false alerts a week
WEEK = 7 * 86400.0
NOT_WRONG = ("normal", "warming_up", "stopped", None)
SAFETY_COLS = ("vib_rms_x", "vib_rms_y", "vib_rms_z", "vib_velocity_mm_s", "vib_peak_g", "vib_kurtosis", "imu_temp_c",
               "th_max_c", "snd_level_db", "trend_shocks_10m", "trend_heat_rise_per_min",
               "vib_gravity_x", "vib_gravity_y", "vib_gravity_z")


# ----------------------------------------------------------------- the safety checks, vectorised

def _hold(t, over, hold_s=SF.HOLD_S):
    """Safety.update's on/off for one check over time-ordered rows: on once it held for ON_SHARE of the last
    hold_s (with the window nearly covered), off at OFF_SHARE or less."""
    over = np.asarray(over, bool)
    lo = np.searchsorted(t, t - hold_s, "left")
    c = np.r_[0, np.cumsum(over)]
    n = np.arange(len(t)) + 1 - lo
    share = (c[1:] - c[lo]) / n
    covered = (t - t[lo]) >= 0.9 * hold_s
    up = covered & (share >= SF.ON_SHARE)
    down = share <= SF.OFF_SHARE
    on = np.zeros(len(t), bool)
    state = False
    cand = np.nonzero(up | down)[0]
    last = 0
    for i in cand:  # only rows where the state can change
        on[last:i] = state
        if state and down[i]:
            state = False
        elif not state and up[i]:
            state = True
        last = i
    on[last:] = state
    if len(cand):
        on[cand[-1]] = state
    return on


def safety_active(t, C, baseline, sensitivities, gravity_check=True):
    """Per sensitivity: {check name: on per row}, the same checks Safety.update runs (in CHECKS order)."""
    t = np.asarray(t, float)
    rms = np.vstack([C[f"vib_rms_{a}"] for a in "xyz"])
    lin = 10.0 ** rms
    total = np.sqrt((10.0 ** (2 * rms)).sum(0))
    vel, peak = C["vib_velocity_mm_s"], 10.0 ** C["vib_peak_g"]
    kurt = C["vib_kurtosis"] + 3.0
    imu, thm, snd = C["imu_temp_c"], C["th_max_c"], C["snd_level_db"]
    grav = np.sqrt(sum(C[f"vib_gravity_{a}"] ** 2 for a in "xyz"))
    with np.errstate(invalid="ignore"):
        rms_max = np.where(np.isfinite(lin).any(0), np.nanmax(np.where(np.isfinite(lin), lin, -np.inf), 0), np.nan)
        j = np.searchsorted(t, t - 60.0, "right") - 1
        jj = np.maximum(j, 0)
        dt = t - t[jj]
        imu_rise = np.where(dt >= 50.0, (imu - imu[jj]) * 60.0 / np.maximum(dt, 1e-9), np.nan)
        rise = np.fmax(imu_rise, C["trend_heat_rise_per_min"])
        hot = np.fmax(imu, thm)
        bad_g = gravity_check & np.isfinite(grav) & ~((grav >= SF.GRAVITY_G[0]) & (grav <= SF.GRAVITY_G[1]))
        frozen = np.isfinite(rms_max) & (rms_max < SF.FROZEN_G)
        bad_t = np.isfinite(imu) & ~((imu >= SF.IMU_TEMP_OK_C[0]) & (imu <= SF.IMU_TEMP_OK_C[1]))
        sensor = _hold(t, bad_g | frozen | bad_t)
        dropped = {SF.RENAMED.get(n, n) for n in (baseline or {}).get("dropped", [])}
        universal = {"vibration_over_limit": vel > SF.VELOCITY_MM_S,
                     "knocking": (kurt > SF.KURTOSIS) & (peak >= SF.KNOCK_PEAK_G),
                     "too_hot": hot > SF.HOT_C, "heating_fast": rise > SF.HEATING_C_PER_MIN,
                     "repeated_jolts": C["trend_shocks_10m"] >= SF.JOLTS_10M}
        uni_on = {}
        for n, over in universal.items():
            if n in dropped:
                continue
            if n in SF.FROM_VIBRATION:
                over = over & ~sensor
            uni_on[n] = _hold(t, over)
        levels = dict((baseline or {}).get("levels", {}))
        out = {}
        for s in sensitivities:
            m = SF.margins(s)
            act = {"sensor_problem": sensor, **uni_on}
            if levels:
                k, over_v, have_v = m["vibration"], np.zeros(len(t), bool), False
                for name, v in (("vib_velocity_mm_s", vel), ("vib_total_g", total)):
                    lvl = levels.get(name)
                    if lvl and lvl > 0:
                        have_v = True
                        over_v |= np.isfinite(v) & (v > max(k * lvl, SF.VIB_FLOOR[name]))
                if have_v:
                    act["vibration_above_normal"] = _hold(t, over_v & ~sensor)
                above = [v - levels[n] for n, v in (("imu_temp_c", imu), ("th_max_c", thm)) if n in levels]
                if above:
                    act["hotter_than_normal"] = _hold(t, np.fmax.reduce(above) > m["temperature"])
                if "snd_level_db" in levels:
                    act["louder_than_normal"] = _hold(t, (snd - levels["snd_level_db"]) > m["sound"])
            out[s] = {n: act[n] for n in SF.CHECKS if n in act}
    return out


def per_minute(t_sec, minute_t0, active):
    """{check: on per second} -> (any check on during each minute, the first check on in that minute or None)."""
    idx = np.searchsorted(minute_t0, t_sec, "right") - 1
    ok = (idx >= 0) & (idx < len(minute_t0))
    anyon = np.zeros(len(minute_t0), bool)
    first = [None] * len(minute_t0)
    for name, on in active.items():
        hit = np.zeros(len(minute_t0), bool)
        np.logical_or.at(hit, idx[ok & on], True)
        for k in np.nonzero(hit & ~anyon)[0]:
            first[k] = SF.SYMPTOM[name]
        anyon |= hit
    return anyon, first


def load_seconds(folder):
    """The one-a-second rows' times and the columns the safety checks read (not the whole matrix)."""
    X, t0, names = load_recording(folder)
    idx = {n: i for i, n in enumerate(names)}
    C = {c: (np.asarray(X[:, idx[c]], float) if c in idx else np.full(len(t0), np.nan)) for c in SAFETY_COLS}
    order = np.argsort(t0, kind="stable")
    return np.asarray(t0, float)[order], {c: v[order] for c, v in C.items()}


# ----------------------------------------------------------------- one run's inputs, computed once

class Run:
    def __init__(self, name, folder, t, X, lab, model, series, sensitivities=MODEL_SWEEP):
        self.name, self.t, self.lab = name, t, lab
        self.run = running_mask(t, lab) if lab is not None and "running" in lab else ~(X[:, F.INDEX["trend_running"]] < 0.5)
        s, r, Z = model.score(X)
        self.values = series.from_model(s, r, Z)
        self.dev = model.deviation_vector(r, Z)
        self.dev_names = model._dev_names
        w = RT.Watcher(model)
        level, dev_run = [], []
        for tk, x in zip(t, X):
            w._judge(float(tk), x)
            st = w.state
            level.append(st.get("unknown_score") or 0.0)
            dev_run.append(st.get("diagnosis") != "stopped")
        self.level = np.array(level)
        self.dev_running = np.array(dev_run)
        self.keep = self.run & (t >= t[0] + WARMUP_S)
        self.alarm0 = self.alarm_at(0.5)
        ts, C = load_seconds(folder)
        act = safety_active(ts, C, model.d.get("baseline"), sensitivities)
        self.safety = {s: per_minute(ts, t, a) for s, a in act.items()}
        self.span_s = float(t[-1] - t[0]) + 60.0
        sc = (lab or {}).get("scenario", {})
        self.onset = sc.get("onset")
        self.fault = sc.get("fault_kind")
        self.severity = sc.get("severity")
        self.fault_end = (lab or {}).get("faults", [{}])[0].get("end_s") if (lab or {}).get("faults") else None
        self.nuis = [[] for _ in t]
        if lab is not None and "minute_t0" in lab:  # every disturbance overlapping each minute (evaluate.full_nuisances)
            at = {float(v): i for i, v in enumerate(lab["minute_t0"])}
            full = full_nuisances(Path(folder), lab)
            self.nuis = [list(full[at[float(v)]] or []) if float(v) in at else [] for v in t]

    def causes(self, i):
        """What was going on when an alert began: disturbances in that minute and the one before."""
        got = sorted(set(self.nuis[i]) | (set(self.nuis[i - 1]) if i > 0 else set()))
        return got or ["nothing in particular"]

    def alarm_at(self, s):
        return (self.level > SF.margins(s)["model"]) & self.run

    def warn(self, model, calib, method, s):
        """Per minute: (warning on, warning shown, {minute: (symptom, rising, speed)} where one started)."""
        if method == "sensitivity":
            ew = EW.EarlyWarning(model, calib, method=method, alarm_sensitivity=s)
        else:
            ew = EW.EarlyWarning(model, calib, method=method, sensitivity=s)
        on, shown, said, last = np.zeros(len(self.t), bool), np.zeros(len(self.t), bool), {}, None
        for k in range(len(self.t)):
            wrong = bool(self.alarm0[k] or self.safety[0.5][0][k])
            ew.observe(float(self.t[k]), bool(self.dev_running[k]), values=self.values[k], alarm=wrong)
            on[k], shown[k] = ew.on, ew.shown
            now = (ew.symptom, ew.rising, ew.speed) if ew.on else None
            if now is not None and now != last:
                said[k] = now
            last = now
        return on & self.run, shown & self.run, said

    def symptom_at(self, k):
        dev = {n: float(v) for n, v in zip(self.dev_names, self.dev[k])}
        return FT.describe(dev)[0][0]


def starts(flags, keep):
    f = flags & keep
    return [i for i in np.nonzero(f)[0] if i == 0 or not f[i - 1]]


def first_start(flags, after):
    """The first minute in `after` where an alert begins (one already on at the onset is a false one,
    still going: it is not counted as catching the fault)."""
    f = np.asarray(flags, bool)
    prev = np.r_[False, f[:-1]]
    idx = np.nonzero(f & ~prev & after)[0]
    return int(idx[0]) if len(idx) else None


def fit(t, X):
    d, _ = train_model(t, X, list(F.FEATURE_NAMES), period=60, normal_model="pca")
    m = RT.Model(d)
    return m, EW.calibrate(m, t, X), EW.Series(m)


def per_week(n, run):
    """False episodes a week, by this run's calendar time past its first 6 h."""
    judged = max(run.span_s - WARMUP_S, 1.0)
    return n * WEEK / judged


# ----------------------------------------------------------------- report 1: early warning alone

def tune(folds, methods):
    out = {}
    for method in methods:
        grid = [g for g in TUNE_GRID if g >= 0.5] if method == "sensitivity" else TUNE_GRID
        counts = {}
        for g in grid:
            counts[g] = sum(len(starts(run.warn(m, calib, method, g)[1], run.keep)) for m, calib, run in folds)
        ok = [g for g in grid if counts[g] <= TUNE_FALSE]
        out[method] = {"setting": max(ok) if ok else min(grid), "false_by_setting": {str(k): v for k, v in counts.items()}}
        print(f"tuned {method:12s} -> {out[method]['setting']}  {counts}", flush=True)
    return out


def judge_alone(runs, model, calib, method, s):
    res = {"method": method, "setting": s, "runs": {}}
    for run in runs.values():
        _, shown, said = run.warn(model, calib, method, s)
        if run.onset is None:
            st = starts(shown, run.keep)
            res["false_per_week"] = round(per_week(len(st), run), 2)
            res["false_causes"] = dict(Counter(c for i in st for c in run.causes(i)))
            res["false_said"] = dict(Counter(said[i][0] for i in st if i in said))
            continue
        after = run.run & (run.t >= run.onset)
        before = run.keep & (run.t < run.onset)
        fa = first_start(run.alarm0, after)
        fw = first_start(shown, after)
        first = fw is not None and (fa is None or fw < fa)
        k = max((i for i in said if i <= fw), default=None) if fw is not None else None
        sym = said[k][0] if k is not None else None
        res["runs"][run.name] = {
            "fault": run.fault, "severity": run.severity,
            "alarm_h": None if fa is None else round((run.t[fa] - run.onset) / 3600, 2),
            "warn_h": None if fw is None else round((run.t[fw] - run.onset) / 3600, 2),
            "warned_first": first,
            "lead_h": round(float(run.t[fa] - run.t[fw]) / 3600, 2) if first and fa is not None else None,
            "wear_at_warn_pct": round(100 * wear_level(run.t[fw], run.onset, run.fault_end, run.severity), 1)
            if fw is not None and run.fault in SLOW else None,
            "extra_catch": fw is not None and fa is None,
            "false_before": len(starts(shown, before)),
            "said": sym, "said_fits": None if sym is None else (sym in EXPECTED[run.fault])}
    return res


def summary_alone(res):
    runs = list(res["runs"].values())
    first = [r for r in runs if r["warned_first"]]
    leads = [r["lead_h"] for r in first if r["lead_h"] is not None]
    slow = [r for r in runs if r["fault"] in SLOW]
    slow_leads = [r["lead_h"] for r in slow if r["warned_first"] and r["lead_h"] is not None]
    wear = [r["wear_at_warn_pct"] for r in slow if r["warned_first"] and r["wear_at_warn_pct"] is not None]
    extra = [r for r in runs if r["extra_catch"]]
    fits = [r["said_fits"] for r in first if r["said_fits"] is not None]
    return {"method": res["method"], "setting": res["setting"], "heldout_false_per_week": res.get("false_per_week"),
            "before_onset_false": sum(r["false_before"] for r in runs), "warned_first": f"{len(first)}/{len(runs)}",
            "median_lead_h": round(float(np.median(leads)), 1) if leads else None,
            "slow_warned_first": f"{sum(r['warned_first'] for r in slow)}/{len(slow)}",
            "slow_median_lead_h": round(float(np.median(slow_leads)), 1) if slow_leads else None,
            "slow_median_wear_pct": round(float(np.median(wear)), 1) if wear else None,
            "extra_catches": f"{len(extra)} ({sum(r['severity'] < 0.5 for r in extra)} subtle)",
            "extra_names": [n for n, r in res["runs"].items() if r["extra_catch"]],
            "said_fits": f"{sum(fits)}/{len(fits)}" if fits else "-"}


def table_alone(rows):
    head = ("method", "setting", "held-out false/week", "false before onset", "warned before alarm", "median lead h",
            "slow: warned first", "slow: median lead h", "slow: wear at warning %", "extra catches", "wording fits")
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for r in rows:
        out.append("| " + " | ".join(str(v) for v in (
            r["method"], r["setting"], r["heldout_false_per_week"], r["before_onset_false"], r["warned_first"],
            r["median_lead_h"], r["slow_warned_first"], r["slow_median_lead_h"], r["slow_median_wear_pct"],
            r["extra_catches"], r["said_fits"])) + " |")
    return "\n".join(out)


# ----------------------------------------------------------------- report 2: the combined device

def combined(runs, folds, warn_cache, fold_cache, methods=COMBINED_METHODS):
    """Every (model sensitivity, early method, early sensitivity) setting of the whole device."""
    configs = [(sm, None, None) for sm in MODEL_SWEEP] + \
              [(sm, meth, se) for sm in MODEL_SWEEP for meth in methods for se in SWEEP]
    rows = []
    for sm, meth, se in configs:
        row = {"model_sensitivity": sm, "early": meth or "none", "early_sensitivity": se}
        n = 0
        for i, (_, _, run) in enumerate(folds):
            a = run.alarm_at(sm) | run.safety[sm][0] | (fold_cache[i][(meth, se)][0] if meth else False)
            n += per_week(len(starts(a, run.keep)), run)
        row["train_false_per_week"] = round(n / len(folds), 2)
        creep, sudden, before_n, before_s, causes = [], [], 0, 0.0, Counter()
        for name, run in runs.items():
            ew_on, said = (warn_cache[name][(meth, se)][0], warn_cache[name][(meth, se)][2]) if meth else (False, {})
            alarm, (saf, saf_sym) = run.alarm_at(sm), run.safety[sm]
            alert = alarm | saf | ew_on
            if run.onset is None:
                st = starts(alert, run.keep)
                row["heldout_false_per_week"] = round(per_week(len(st), run), 2)
                causes.update(c for i in st for c in run.causes(i))
                row["heldout_false_by"] = dict(Counter("alarm" if alarm[i] else "safety" if saf[i] else "early" for i in st))
                continue
            before = run.keep & (run.t < run.onset)
            before_n += len(starts(alert, before))
            before_s += before.sum() * 60.0
            k = first_start(alert, run.run & (run.t >= run.onset))
            src, sym = None, None
            if k is not None:
                if alarm[k]:
                    src, sym = "alarm", run.symptom_at(k)
                elif saf[k]:
                    src, sym = "safety", saf_sym[k]
                else:
                    j = max((i for i in said if i <= k), default=None)
                    src, sym = "early", said[j][0] if j is not None else None
            r = {"run": name, "fault": run.fault, "severity": run.severity, "caught": k is not None,
                 "minutes": None if k is None else round((run.t[k] - run.onset) / 60, 1), "by": src, "said": sym,
                 "said_fits": None if sym is None else sym in EXPECTED[run.fault]}
            if run.fault in SLOW:
                wear = wear_level(run.t[k], run.onset, run.fault_end, run.severity) if k is not None else None
                r["wear_pct"] = None if wear is None else round(100 * wear, 1)
                r["share_of_final"] = 1.0 if wear is None else wear / run.severity  # missed counts as its full size
                creep.append(r)
            else:
                sudden.append(r)
        row["before_onset_false"] = before_n
        row["before_onset_false_per_week"] = round(before_n * WEEK / max(before_s, 1.0), 2)
        row["heldout_false_causes"] = dict(causes)
        sub_c = [r for r in creep if r["severity"] < 0.5]
        obv_c = [r for r in creep if r["severity"] >= 0.5]
        sub_s = [r for r in sudden if r["severity"] < 0.5]
        obv_s = [r for r in sudden if r["severity"] >= 0.5]
        med = lambda v: round(float(np.median(v)), 1) if v else None
        row.update({
            "creep_subtle_caught": f"{sum(r['caught'] for r in sub_c)}/{len(sub_c)}",
            "creep_obvious_caught": f"{sum(r['caught'] for r in obv_c)}/{len(obv_c)}",
            "creep_subtle_wear_pct": med([r["wear_pct"] for r in sub_c if r["caught"]]),
            "creep_obvious_wear_pct": med([r["wear_pct"] for r in obv_c if r["caught"]]),
            "creep_mean_share": round(float(np.mean([r["share_of_final"] for r in creep])), 3) if creep else None,
            "creep_minutes": med([r["minutes"] for r in creep if r["caught"]]),
            "sudden_subtle_caught": f"{sum(r['caught'] for r in sub_s)}/{len(sub_s)}",
            "sudden_obvious_caught": f"{sum(r['caught'] for r in obv_s)}/{len(obv_s)}",
            "sudden_minutes": med([r["minutes"] for r in sudden if r["caught"]]),
            "said_fits": f"{sum(bool(r['said_fits']) for r in creep + sudden if r['caught'])}/"
                         f"{sum(r['caught'] for r in creep + sudden)}",
            "first_by": dict(Counter(r["by"] for r in creep + sudden if r["caught"])),
            "runs": creep + sudden})
        rows.append(row)
    return rows


def _n(frac):
    return int(str(frac).split("/")[0])


def rank_key(row):
    """Earliest first: most faults caught (creeping and subtle sudden), then the smallest wear at the alert,
    then the fewest false alerts in training, then the quickest on sudden faults."""
    caught = _n(row["creep_subtle_caught"]) + _n(row["creep_obvious_caught"]) + _n(row["sudden_subtle_caught"])
    return (-caught, row["creep_mean_share"], row["train_false_per_week"], row["sudden_minutes"] or 1e9)


def recommend(rows, methods=COMBINED_METHODS):
    out = {}
    for b in BUDGETS:
        ok = [r for r in rows if r["train_false_per_week"] <= b]
        best = min(ok, key=rank_key) if ok else None
        alarm_only = [r for r in ok if r["early"] == "none"]
        out[str(b)] = {"best": best, "best_without_early_warning": min(alarm_only, key=rank_key) if alarm_only else None,
                       "best_per_method": {m: min([r for r in ok if r["early"] == m], key=rank_key)
                                           for m in methods if any(r["early"] == m for r in ok)}}
    return out


COMBINED_HEAD = ("model sens.", "early warning", "early sens.", "false/week: training", "held-out", "before onset",
                 "creeping subtle caught", "subtle wear at alert %", "creeping obvious caught",
                 "obvious wear at alert %", "sudden subtle caught", "sudden obvious caught", "sudden median min",
                 "wording fits")


def combined_line(r):
    return "| " + " | ".join(str(v) for v in (
        r["model_sensitivity"], r["early"], r["early_sensitivity"] if r["early"] != "none" else "-",
        r["train_false_per_week"], r["heldout_false_per_week"], r["before_onset_false_per_week"],
        r["creep_subtle_caught"], r["creep_subtle_wear_pct"], r["creep_obvious_caught"], r["creep_obvious_wear_pct"],
        r["sudden_subtle_caught"], r["sudden_obvious_caught"], r["sudden_minutes"], r["said_fits"])) + " |"


def combined_table(rows, label=None):
    head = (("budget",) if label else ()) + COMBINED_HEAD
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for lab, r in rows:
        line = combined_line(r)
        out.append(f"| {lab} " + line if label else line)
    return "\n".join(out)


def strip(row):
    return {k: v for k, v in row.items() if k != "runs"} if row else None


# ----------------------------------------------------------------- checks and main

def check_safety(folder, model, hours=6.0, start_h=0.0, sensitivity=0.5):
    """The vectorised safety checks against Safety.update itself, row by row, on some hours of a run."""
    X, t0, names = load_recording(folder)
    from effente.trainer import to_current
    X = to_current(X, names)
    sel = (t0 >= t0.min() + start_h * 3600) & (t0 < t0.min() + (start_h + hours) * 3600)
    X, t0 = X[sel], np.asarray(t0[sel], float)
    safety = SF.Safety(model.d.get("baseline"), sensitivity=sensitivity)
    ref = {n: np.zeros(len(t0), bool) for n in SF.CHECKS}
    for i, (tk, x) in enumerate(zip(t0, X)):
        safety.update(float(tk), x)
        for p in safety.active:
            ref[p][i] = True
    C = {c: X[:, F.INDEX[c]].astype(float) for c in SAFETY_COLS}
    mine = safety_active(t0, C, model.d.get("baseline"), [sensitivity])[sensitivity]
    diff = {n: int((ref[n] != mine.get(n, np.zeros(len(t0), bool))).sum()) for n in SF.CHECKS}
    print(f"safety check on {folder.name}, {hours} h from {start_h} h at sensitivity {sensitivity}: rows differing per check {diff}; "
          f"on in Safety: { {n: int(v.sum()) for n, v in ref.items() if v.any()} }", flush=True)
    return diff


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--methods", help="comma-separated subset of " + ",".join(EW.METHODS))
    ap.add_argument("--check-safety", action="store_true", help="also compare the vectorised safety checks with Safety")
    args = ap.parse_args(argv)
    root = Path(args.data)
    started = time.time()
    methods = args.methods.split(",") if args.methods else list(EW.METHODS)
    comb_methods = [m for m in methods if m in COMBINED_METHODS]
    weeks = {n: minutes_of(root / n) for n in ("normal-w1", "normal-w2")}
    folds = []
    for a, b in (("normal-w1", "normal-w2"), ("normal-w2", "normal-w1")):
        m, calib, ser = fit(weeks[a][0], weeks[a][1])
        tb, Xb, labb = weeks[b]
        folds.append((m, calib, Run(f"{a}->{b}", root / b, tb, Xb, labb, m, ser)))
        print(f"fold {a} -> {b} ready, {time.time() - started:.0f} s", flush=True)
    tuned = tune(folds, methods)
    t_tr = np.concatenate([weeks["normal-w1"][0], weeks["normal-w2"][0]])
    X_tr = np.vstack([weeks["normal-w1"][1], weeks["normal-w2"][1]])
    model, calib, ser = fit(t_tr, X_tr)
    if args.check_safety:
        check_safety(root / "heldout", model, hours=24, sensitivity=1.0)
        for name in ("idler_seized-obvious", "jam-obvious", "unbalance-obvious"):
            if (root / name / "labels.json").exists():
                check_safety(root / name, model, hours=12, start_h=12, sensitivity=0.75)
    runs = {}
    for folder in sorted(p for p in root.iterdir() if (p / "labels.json").exists() and not p.name.startswith("normal-w")):
        t, X, lab = minutes_of(folder)
        runs[folder.name] = Run(folder.name, folder, t, X, lab, model, ser)
    print(f"{len(runs)} test runs ready, {time.time() - started:.0f} s", flush=True)

    # 1. early warning alone
    alone = {"at_tuned": [], "sweep": []}
    for method in methods:
        res = judge_alone(runs, model, calib, method, tuned[method]["setting"])
        alone["at_tuned"].append({"summary": summary_alone(res), **res})
        for s in (ALARM_SWEEP if method == "sensitivity" else SWEEP):
            res = judge_alone(runs, model, calib, method, s)
            alone["sweep"].append({"summary": summary_alone(res), **res})
    print(f"early warning alone done, {time.time() - started:.0f} s", flush=True)

    # 2. the combined device
    warn_cache = {n: {(m, s): r.warn(model, calib, m, s) for m in comb_methods for s in SWEEP} for n, r in runs.items()}
    fold_cache = [{(mm, s): run.warn(m, c, mm, s) for mm in comb_methods for s in SWEEP} for m, c, run in folds]
    rows = combined(runs, folds, warn_cache, fold_cache, comb_methods)
    rec = recommend(rows, comb_methods)
    alarm_ref = {n: {"alarm_h_default": None if (i := first_start(r.alarm0, r.run & (r.t >= r.onset))) is None
                     else round((r.t[i] - r.onset) / 3600, 2)} for n, r in runs.items() if r.onset is not None}
    out = {"tuned_alone": tuned, "calibration": calib, "alarm_default": alarm_ref, "alone": alone,
           "combined": rows, "recommended": {b: {k: (strip(v) if k != "best_per_method" else {m: strip(x) for m, x in v.items()})
                                                 for k, v in d.items()} for b, d in rec.items()},
           "recommended_runs": {b: (d["best"] or {}).get("runs") for b, d in rec.items()}}
    Path(args.out).write_text(json.dumps(out, indent=1, default=str))

    print("\n1. Early warning alone, at each method's tuned setting (at most about one false warning a week in training):")
    print(table_alone([r["summary"] for r in alone["at_tuned"]]))
    print("\n   ... and its sensitivity sweep:")
    print(table_alone([r["summary"] for r in alone["sweep"]]))
    print("\n2. The whole device: the device as it is (model sensitivity 0.5, no early warning) and the alarm alone:")
    print(combined_table([(None, r) for r in rows if r["early"] == "none"]))
    print("\n   Recommended per budget (chosen by the training weeks' false-alert rate):")
    print(combined_table([(b, d["best"]) for b, d in rec.items() if d["best"]], label=True))
    print("\n   Best without early warning, per budget:")
    print(combined_table([(b, d["best_without_early_warning"]) for b, d in rec.items() if d["best_without_early_warning"]], label=True))
    for b, d in rec.items():
        print(f"\n   Best per early-warning method at {b} false alerts a week:")
        print(combined_table([(m, r) for m, r in d["best_per_method"].items()], label=True))
    print(f"\n{time.time() - started:.0f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
