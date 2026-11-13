#!/usr/bin/env python3
"""Which "is it still normal?" method is best on the realistic conveyor (generate.py's runs)?

Every method learns this machine's normal from the per-minute medians of normal-w1 and normal-w2 (two
weeks), running minutes only, the way the device does with a long recording:
  - the two the device trains and runs itself (effente.trainer: PCA, and the autoencoder; int8 weights,
    the device's own Watcher: the median of the last 5 minutes against the line)
  - the bench's others (training.anomaly.detectors), with the same inputs, split and line: learn on the
    first 85 % of the running minutes, the line from the last 15 % for about one false alarm a week
Trend features over a week or more are left out for every method: the test runs are 2 to 7 days long and
start with a fresh history, so those features would be empty in the tests (but full in training).

Judged minute by minute on running minutes:
  - false alarms: alarm stretches per running day on the held-out week (the first 6 h are the device's
    history warming up, not judged), with what was going on when each began (site_noise nuisances)
  - each fault run: caught (an alarm after the fault began), minutes from the start to the first alarm,
    and false alarms before it began
  - AUC: fault minutes against held-out minutes, no line at all
  - description (the device's two): what the screen would say (effente.fault_types.describe) for the
    first alarm stretch, and whether it fits the fault (EXPECTED below)
  - a sensitivity sweep (SWEEP): sensitivity only moves each method's line (effente.safety.margins "model":
    alarm when the score over the line passes it), so every setting is judged from the same scores:
    false alarms a running day, faults caught, and minutes from onset to the first alarm, on all faults
    and on the slow ones. Does turning the sensitivity up give earlier warning at a tolerable false-alarm rate?
  - how EARLY, not just how fast (the user's goal: catch faults as early as possible, with the fewest false
    alarms): for creeping (slow) faults, how far the wear had grown at the first alarm, as a % of the obvious
    fault's full size (lower is earlier); for sudden faults, whether the subtle size is caught at all

    python3 -m training.record.conveyor_eval.evaluate --data data/fake/conveyor-eval --out data/fake/conveyor-eval/results.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
import warnings
from collections import Counter
from pathlib import Path

import numpy as np

from effente import features as F
from effente import model_runtime as RT
from effente.model_input import Prep, load_minutes
from effente import safety as SF
from effente.trainer import _causal_median, gamma_threshold, to_current, train_model

SMOOTH = 5
SWEEP = (0.0, 0.25, 0.5, 0.6, 0.75, 0.9, 1.0)
SLOW_FAULTS = ("idler_bearing", "gear_wear", "motor_bearing", "lubrication_loss")
WARMUP_S = 6 * 3600
LONG = ("_7d", "_4w", "per_day", "per_week", "24h_vs_7d", "7d_vs_4w")
# What the screen may sensibly say for each fault (effente.fault_types.SYMPTOM_TEXT).
VIB = {"vibration_too_high", "unusual_vibration"}
EXPECTED = {
    "idler_bearing": VIB | {"knocking_or_jolts", "grinding_noise", "hot_spot", "too_hot", "heating_up", "louder_than_normal", "new_tone"},
    "idler_seized": {"new_tone", "hot_spot", "too_hot", "louder_than_normal", "unusual_vibration", "grinding_noise"},
    "belt_rub": {"new_tone", "hot_spot", "louder_than_normal", "too_hot", "grinding_noise"},
    "belt_slip": {"new_tone", "too_hot", "hot_spot", "heating_up", "louder_than_normal", "unusual_vibration", "speed_changed"},
    "unbalance": VIB,
    "looseness": VIB | {"knocking_or_jolts", "vibration_unsteady"},
    "misalignment": VIB | {"too_hot"},
    "gear_wear": VIB | {"new_tone", "louder_than_normal", "too_hot", "heating_up"},
    "motor_bearing": VIB | {"grinding_noise", "knocking_or_jolts", "louder_than_normal", "too_hot"},
    "splice_damage": {"knocking_or_jolts", "unusual_vibration", "louder_than_normal"},
    "jam": {"knocking_or_jolts", "speed_changed", "too_hot", "vibration_unsteady"},
    "overload": {"too_hot", "heating_up", "louder_than_normal", "vibration_too_high", "unusual_vibration", "hot_spot"},
    "lubrication_loss": {"too_hot", "heating_up", "grinding_noise", "unusual_vibration", "louder_than_normal", "hot_spot"},
    "cooling_blocked": {"too_hot", "heating_up", "hot_spot"},
    "electrical": VIB | {"new_tone", "louder_than_normal", "too_hot", "beeping"},
}


def minutes_of(folder: Path):
    """(minute_t0, median rows on the current feature list, labels.json)."""
    t, stats, names = load_minutes(folder, stats=("median",))
    X = to_current(stats["median"], names)
    for j, n in enumerate(F.FEATURE_NAMES):
        if any(k in n for k in LONG):
            X[:, j] = np.nan
    lab = json.loads((folder / "labels.json").read_text())
    return t.astype(float), X, lab


def full_nuisances(folder: Path, lab: dict) -> list[list[str]]:
    """Every site disturbance overlapping each minute. Runs made before 13 Nov's fix recorded only the
    instant at each minute's 30th second (missing most bangs and jolts), so rebuild it from the run's
    seeds: the same schedule, nothing re-simulated. Falls back to labels.json's own if that fails."""
    from training.record.conveyor_eval.generate import minute_nuisances, scenario_site
    try:
        plan = {s["name"]: s for s in json.loads((folder.parent / "scenarios.json").read_text())}
        site = scenario_site(plan[folder.name])
        return minute_nuisances(site.events, np.asarray(lab["minute_t0"], float))
    except Exception:  # noqa: BLE001
        return lab["nuisance"]


def running_mask(t, lab):
    run = dict(zip((float(v) for v in lab["minute_t0"]), lab["running"]))
    return np.array([bool(run.get(float(v), False)) for v in t])


def episodes(flags) -> int:
    f = np.asarray(flags, bool)
    return int(f[0]) + int(((~f[:-1]) & f[1:]).sum()) if len(f) else 0


class DeviceMethod:
    def __init__(self, normal_model):
        self.normal_model = normal_model

    def fit(self, t, X):
        self.d, self.report = train_model(t, X, list(F.FEATURE_NAMES), period=60, normal_model=self.normal_model)
        self.model = RT.Model(self.d)
        return self

    def judge(self, t, X, run):
        """Per minute: (alarm, score over the line, symptom or None), as the device's Watcher does."""
        w = RT.Watcher(self.model, margin=1.0)  # the default sensitivity; the sweep re-reads the score
        out = []
        for k, (tk, x) in enumerate(zip(t, X)):
            w._judge(float(tk), x)  # each row is already a minute's median: judge it as the Watcher does
            st = w.state
            alarm = st.get("diagnosis") not in ("normal", "warming_up", "stopped", None)
            out.append((alarm and run[k], st.get("unknown_score") or 0.0, st.get("symptom") if alarm else None))
        return out


class BenchMethod:
    def __init__(self, cls):
        self.cls = cls

    def fit(self, t, X):
        names = list(F.FEATURE_NAMES)
        run = ~(X[:, F.INDEX["trend_running"]] < 0.5)
        t, X = t[run], X[run]
        cut = int(0.85 * len(t))
        self.prep = Prep.fit(X[:cut], names, t[:cut], running_only=False)
        z = lambda A: np.clip(np.nan_to_num(self.prep.transform(A)), -8, 8)
        self.z = z
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self.det = self.cls().fit(z(X[:cut]))
            cal = self.det.score(z(X[cut:]))[0]
        self.line = gamma_threshold(_causal_median(cal, SMOOTH), 7 * 1440)
        return self

    def judge(self, t, X, run):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            s = self.det.score(self.z(X))[0]
        out, hist = [], []
        for k in range(len(t)):
            if not run[k]:
                hist = []
                out.append((False, 0.0, None))
                continue
            hist = (hist + [s[k]])[-SMOOTH:]
            m = float(np.median(hist))
            out.append((len(hist) >= 2 and m > self.line, m / self.line if len(hist) >= 2 else 0.0, None))
        return out


def methods():
    from training.anomaly import detectors as D
    return {"PCA (on the device)": DeviceMethod("pca"), "autoencoder (on the device)": DeviceMethod("autoencoder"),
            "PCA": BenchMethod(D.PCARecon), "Gaussian (Mahalanobis)": BenchMethod(D.Gaussian),
            "Gaussian mixture": BenchMethod(D.Mixture), "autoencoder (sklearn)": BenchMethod(D.Autoencoder),
            "similarity (AAKR/MSET-style)": BenchMethod(D.Similarity), "isolation forest": BenchMethod(D.Forest),
            "nearest neighbours": BenchMethod(D.KNN), "max |z| (no model)": BenchMethod(D.MaxZ)}


def auc(neg, pos) -> float:
    neg, pos = np.asarray(neg, float), np.asarray(pos, float)
    if not len(neg) or not len(pos):
        return float("nan")
    ranks = np.concatenate([neg, pos]).argsort().argsort() + 1.0
    return float((ranks[len(neg):].sum() - len(pos) * (len(pos) + 1) / 2) / (len(neg) * len(pos)))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--methods", help="comma-separated subset")
    args = ap.parse_args(argv)
    root = Path(args.data)
    t1, X1, _ = minutes_of(root / "normal-w1")
    t2, X2, _ = minutes_of(root / "normal-w2")
    t_tr, X_tr = np.concatenate([t1, t2]), np.vstack([X1, X2])
    tests = {}
    for folder in sorted(p for p in root.iterdir() if (p / "labels.json").exists() and not p.name.startswith("normal-w")):
        t, X, lab = minutes_of(folder)
        lab["nuisance"] = full_nuisances(folder, lab)
        tests[folder.name] = (t, X, lab, running_mask(t, lab))
    meths = methods()
    if args.methods:
        meths = {k: v for k, v in meths.items() if k in args.methods.split(",")}
    results = {}
    for name, m in meths.items():
        t0 = time.time()
        m.fit(t_tr, X_tr)
        res = {"fit_s": round(time.time() - t0, 1), "runs": {}}
        held = None
        sweep_in = []  # (run name, minute times, running, over-the-line score, onset or None)
        for run_name, (t, X, lab, run) in tests.items():
            judged = m.judge(t, X, run)
            alarm = np.array([a for a, _, _ in judged])
            score = np.array([s for _, s, _ in judged])
            sc = lab["scenario"]
            f_end = lab["faults"][0]["end_s"] if lab.get("faults") else None
            sweep_in.append((run_name, t, run, score, None if run_name == "heldout" else sc["onset"],
                             sc.get("fault_kind"), sc.get("severity"), f_end))
            if run_name == "heldout":
                keep = run & (t >= t[0] + WARMUP_S)
                eps = episodes(alarm[keep])
                days = keep.sum() / 1440
                starts = [i for i in np.nonzero(keep)[0] if alarm[i] and (i == 0 or not alarm[i - 1])]
                causes = Counter(c for i in starts for c in (lab["nuisance"][i] or ["nothing in particular"]))
                held = score[keep]
                res["false_alarms_per_running_day"] = round(eps / max(days, 1e-9), 3)
                res["false_alarm_causes"] = dict(causes)
                continue
            onset = sc["onset"]
            after = run & (t >= onset)
            before = run & (t >= t[0] + WARMUP_S) & (t < onset)
            first = next((i for i in np.nonzero(after)[0] if alarm[i]), None)
            said = None
            if first is not None:
                stretch = [judged[i][2] for i in range(first, min(first + 30, len(judged))) if judged[i][0]]
                said = Counter(s for s in stretch if s).most_common(1)[0][0] if any(stretch) else None
            res["runs"][run_name] = {
                "fault": sc["fault_kind"], "severity": sc["severity"], "caught": first is not None,
                "minutes_to_alarm": None if first is None else round((t[first] - onset) / 60, 1),
                "false_alarms_before": episodes(alarm[before]), "said": said,
                "said_fits": None if said is None else said in EXPECTED[sc["fault_kind"]],
                "_scores": score[after].tolist()}
        for r in res["runs"].values():
            r["auc"] = round(auc(held, r.pop("_scores")), 3) if held is not None else None
        res["sweep"] = sweep(sweep_in)
        results[name] = res
        sub = [r for r in res["runs"].values() if r["severity"] < 0.5]
        obv = [r for r in res["runs"].values() if r["severity"] >= 0.5]
        print(f"{name:30s} subtle {sum(r['caught'] for r in sub)}/{len(sub)}  obvious {sum(r['caught'] for r in obv)}/{len(obv)}"
              f"  false alarms/day {res.get('false_alarms_per_running_day')}  ({res['fit_s']} s)", flush=True)
    Path(args.out).write_text(json.dumps(results, indent=1, default=str))
    print_table(results)
    print_sweep(results)
    return 0


def wear_level(t_alarm, onset, end, severity) -> float:
    """A slow fault's size at time t_alarm, as a share of the obvious size (simulate_conveyor's wear shape)."""
    u = min(max((t_alarm - onset) / max(end - onset, 1.0), 0.0), 1.0)
    return severity * (math.exp(3 * u) - 1) / (math.e ** 3 - 1)


def sweep(runs) -> dict:
    """Per sensitivity: false alarms a running day on heldout, faults caught, minutes onset -> first alarm,
    and how early: slow faults' size at the first alarm, subtle sudden faults caught."""
    out = {}
    for s in SWEEP:
        line = SF.margins(s)["model"]
        fa, days, caught, n, sub, obv, mins, slow_mins, slow_caught, slow_n = 0, 0.0, 0, 0, [0, 0], [0, 0], [], [], 0, 0
        levels, sudden_sub = [], [0, 0]
        for name, t, run, score, onset, kind, sev, f_end in runs:
            alarm = (np.asarray(score) > line) & run
            if onset is None:
                keep = run & (t >= t[0] + WARMUP_S)
                fa += episodes(alarm[keep])
                days += keep.sum() / 1440
                continue
            after = np.nonzero(run & (t >= onset) & alarm)[0]
            hit = len(after) > 0
            n += 1
            caught += hit
            (sub if sev < 0.5 else obv)[0] += hit
            (sub if sev < 0.5 else obv)[1] += 1
            if hit:
                mins.append((t[after[0]] - onset) / 60)
            if kind in SLOW_FAULTS:
                slow_n += 1
                slow_caught += hit
                if hit:
                    slow_mins.append((t[after[0]] - onset) / 60)
                    levels.append(100 * wear_level(t[after[0]], onset, f_end, sev))
            elif sev < 0.5:
                sudden_sub[0] += hit
                sudden_sub[1] += 1
        out[str(s)] = {"false_alarms_per_running_day": round(fa / max(days, 1e-9), 3), "caught": f"{caught}/{n}",
                       "subtle": f"{sub[0]}/{sub[1]}", "obvious": f"{obv[0]}/{obv[1]}",
                       "median_minutes_to_alarm": round(float(np.median(mins)), 1) if mins else None,
                       "slow_caught": f"{slow_caught}/{slow_n}",
                       "slow_median_minutes_to_alarm": round(float(np.median(slow_mins)), 1) if slow_mins else None,
                       "slow_wear_at_alarm_pct": round(float(np.median(levels)), 1) if levels else None,
                       "subtle_sudden_caught": f"{sudden_sub[0]}/{sudden_sub[1]}"}
    return out


def print_sweep(results):
    print("\nsensitivity sweep: false alarms a running day | subtle caught | obvious caught | median minutes to alarm | slow faults caught, median minutes")
    for name, r in results.items():
        print(f"  {name}")
        for s, v in r.get("sweep", {}).items():
            print(f"    {s:>4s}: {v['false_alarms_per_running_day']:6.2f}/day | {v['subtle']:>5s} | {v['obvious']:>5s} | "
                  f"{v['median_minutes_to_alarm']!s:>7s} min | slow {v['slow_caught']} at {v['slow_median_minutes_to_alarm']} min, "
                  f"wear {v['slow_wear_at_alarm_pct']}% | subtle sudden {v['subtle_sudden_caught']}")


def print_table(results):
    print(f"\n{'method':30s} {'subtle':>7s} {'obvious':>8s} {'false/day':>9s} {'median min':>10s} {'mean AUC':>9s} {'said fits':>9s}")
    rows = []
    for name, r in results.items():
        runs = list(r["runs"].values())
        sub = [x for x in runs if x["severity"] < 0.5]
        obv = [x for x in runs if x["severity"] >= 0.5]
        mins = [x["minutes_to_alarm"] for x in runs if x["caught"]]
        fits = [x["said_fits"] for x in runs if x["said_fits"] is not None]
        rows.append((name, f"{sum(x['caught'] for x in sub)}/{len(sub)}", f"{sum(x['caught'] for x in obv)}/{len(obv)}",
                     r.get("false_alarms_per_running_day"), np.median(mins) if mins else float("nan"),
                     np.nanmean([x["auc"] for x in runs if x["auc"] is not None]),
                     f"{sum(fits)}/{len(fits)}" if fits else "-"))
    for row in rows:
        print(f"{row[0]:30s} {row[1]:>7s} {row[2]:>8s} {row[3]!s:>9s} {row[4]:10.0f} {row[5]:9.3f} {row[6]:>9s}")


if __name__ == "__main__":
    sys.exit(main())
