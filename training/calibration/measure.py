#!/usr/bin/env python3
"""Measure calibration on SIMULATED machines. Laptop only (the simulators need SciPy).

Everything here is simulated: effente.simulate_conveyor and effente.simulate_server, with
effente.site_noise on top. No recording of a real machine exists yet, so every number this
prints is "simulated" and says nothing certain about real machines (unverified).

What it does:
  entries     two stand-in library entries, each trained the normal way (effente.trainer.train_model,
              PCA) on the first 40 minutes of one simulated machine: a conveyor and a server rack.
              These are NOT the real library (training/library/); they only follow its shape.
  machines    other simulated sessions, each one hour, starting with a fresh history as a device
              just snapped on would: the entry's own conveyor on another day, the same conveyor
              design with other seeds, loads and room temperatures, and server racks. "Another
              conveyor" is the same simulated design with another seed, load and room: the simulator
              has no second conveyor design.
  per length  calibrate on the first L seconds, then run the device's Watcher on the 40 minutes
              that follow, all normal running: how many of those seconds alarm. Next to it, the
              entry as shipped (no calibration) and a model trained on the same L seconds alone.
  fit check   the ratio effente.calibration.fit_check works out, for every entry on every machine.
  faults      conveyor sessions with an unbalance or a looseness switched on 10 minutes after a
              5-minute calibration: alarm or not, seconds to the alarm, what it was called.

    python3 -m training.calibration.measure --out calibration-sim.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np

from effente import features as F
from effente import model_runtime as RT
from effente.calibration import calibrate, fit_check
from effente.trainer import train_model

H = 3600
DAY = 86400
ENTRY_S = 2400  # each stand-in entry is trained on this much
WATCH_S = 2400  # normal running judged after each calibration
SESSION_S = 3600
LENGTHS = (60, 120, 180, 300, 600, 1200)
NAMES = list(F.FEATURE_NAMES)
QUIET = ("normal", "warming_up", "stopped")

# name: (kind, seed, start, simulator settings). Conveyors run 06:00-22:00 on weekdays.
ENTRIES = {
    "conveyor": ("conveyor", 1, 8 * H, {}),
    "server": ("server", 2, 9 * H, {}),
}
MACHINES = {
    "conveyor, the entry's own, next day": ("conveyor", 1, DAY + 13 * H, {}),
    "conveyor, seed 2, load x1.2, room +5 C": ("conveyor", 2, 10 * H, dict(load_scale=1.2, ambient_offset_c=5.0)),
    "conveyor, seed 3, load x0.8, room -5 C": ("conveyor", 3, 2 * DAY + 15 * H, dict(load_scale=0.8, ambient_offset_c=-5.0)),
    "conveyor, seed 4, load x1.0, room +10 C": ("conveyor", 4, 3 * DAY + 9 * H, dict(ambient_offset_c=10.0)),
    "server, the entry's own, next day": ("server", 2, DAY + 14 * H, {}),
    "server, seed 5": ("server", 5, 10 * H, {}),
    "server, seed 6": ("server", 6, DAY + 16 * H, {}),
}
FAULT_RUNS = (("unbalance", 1.0), ("unbalance", 0.3), ("looseness", 1.0), ("looseness", 0.3))
FAULT_MACHINE = ("conveyor", 2, 10 * H, dict(load_scale=1.2, ambient_offset_c=5.0))
FAULT_CALIB_S, FAULT_ONSET_S, FAULT_SESSION_S = 300, 900, 1800


def session(kind, seed, start, seconds, settings, faults=(), cache=None):
    """One-a-second feature rows of a simulated session with a fresh history: (t, X)."""
    key = f"{kind}-{seed}-{start}-{seconds}-" + "-".join(f"{k}{v}" for k, v in sorted(settings.items())) + \
          "".join(f"-{k}{a}{s}" for k, a, s in faults)
    path = Path(cache) / f"{key}.npz" if cache else None
    if path and path.exists():
        with np.load(path) as d:
            return d["t"], d["X"]
    from device.intake.extract import feature_stream
    from effente.history import History
    from effente.simulate_conveyor import ConveyorSim, Fault
    from effente.simulate_server import ServerRackSim
    from effente.site_noise import SiteNoise
    days = int(start // DAY) + 1
    fl = [Fault(k, start + a, days * DAY + DAY, f"{k} (simulated)", s) for k, a, s in faults]
    if kind == "conveyor":
        sim = ConveyorSim(days=days, seed=seed, faults=fl, start_s=start, end_s=start + seconds, realistic=True,
                          **settings)
        src = SiteNoise(sim, profile="factory", seed=1000 + seed)
    else:
        sim = ServerRackSim(days=days, seed=seed, faults=fl, start_s=start, end_s=start + seconds)
        src = SiteNoise(sim, profile="datacenter", seed=1000 + seed)
    rows = list(feature_stream(src, hop_s=1.0, history=History()))
    t, X = np.array([r[0] for r in rows], float), np.array([r[1] for r in rows], float)
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(path, t=t, X=X)
    return t, X


def quantiles(s):
    return {k: round(float(np.percentile(s, q)), 6) for k, q in (("p50", 50), ("p90", 90), ("p99", 99))}


def make_entry(name, cache):
    """A stand-in library entry: (model dict, manifest dict), shaped like training/library/entries/<id>/."""
    kind, seed, start, settings = ENTRIES[name]
    t, X = session(kind, seed, start, ENTRY_S, settings, cache=cache)
    d, rep = train_model(t, X, NAMES, source=f"simulated {kind}, seed {seed} (stand-in entry, not the library)")
    m = RT.Model(d)
    tt = t[np.isfinite(t)]
    cut = tt[0] + (tt[-1] - tt[0]) * 0.85  # the trainer's held-out part
    s = m.score(X[t >= cut])[0]
    manifest = {"entry_id": f"sim_{name}", "version": "0", "normal_model": d["normal_model"],
                "features_version": d["features_version"], "sources": [d["trained_on"]],
                "held_out_score_quantiles": quantiles(s), "scorecard": {}, "status": "unverified"}
    return d, manifest


def watch(d, t, X):
    """The device's Watcher over rows: (seconds judged, seconds abnormal, alarm stretches, first alarm time, names)."""
    w = RT.Watcher(RT.Model(d))
    judged = abnormal = stretches = 0
    first, names, last, was = None, Counter(), None, False
    for ti, x in zip(t, X):
        r = w.predict_at(float(ti), x)
        if r.get("minute") is None or r.get("minute") == last:
            continue
        last = r["minute"]
        if r["diagnosis"] in ("warming_up", "stopped"):
            was = False
            continue
        judged += 1
        bad = r["diagnosis"] not in QUIET
        if bad:
            abnormal += 1
            names[r["diagnosis"]] += 1
            if first is None:
                first = float(r["minute"])
            if not was:
                stretches += 1
        was = bad
    return judged, abnormal, stretches, first, names


def share(d, t, X):
    judged, abnormal, stretches, _, _ = watch(d, t, X)
    return {"judged_s": judged, "alarm_s": abnormal, "alarm_share": round(abnormal / max(judged, 1), 4),
            "alarm_stretches": stretches}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", help="write every number as JSON here")
    ap.add_argument("--cache", help="folder to keep the simulated rows in between runs")
    ap.add_argument("--quick", action="store_true", help="one conveyor, one server, 300 s only (a smoke run)")
    a = ap.parse_args(argv)
    t_start = time.time()
    machines = dict(list(MACHINES.items())[1:2] + list(MACHINES.items())[5:6]) if a.quick else MACHINES
    lengths = (300,) if a.quick else LENGTHS
    out = {"label": "simulated (effente.simulate_conveyor, effente.simulate_server, effente.site_noise); "
                    "unverified on real machines",
           "entries": {}, "fit": [], "lengths": [], "faults": []}

    entries = {}
    for name in ENTRIES:
        d, manifest = make_entry(name, a.cache)
        entries[name] = (d, manifest)
        out["entries"][name] = {"period_s": d["period_s"], "readings": len(d["prep"]["columns"]),
                                "patterns": d["training_report"]["patterns_kept"], "threshold": d["threshold"],
                                "held_out_score_quantiles": manifest["held_out_score_quantiles"]}
        print(f"entry {name}: {out['entries'][name]}")

    print("\nSIMULATED. fit check, 300 s calibration: ratio of this machine's held-out median score to the entry's")
    print(f"{'machine':42s} " + " ".join(f"{'on ' + e:>22s}" for e in entries))
    for mname, (kind, seed, start, settings) in machines.items():
        t, X = session(kind, seed, start, SESSION_S, settings, cache=a.cache)
        cells = []
        for ename, (d, manifest) in entries.items():
            sel = t < t[0] + 300
            _, rep = calibrate(d, t[sel], X[sel], NAMES)
            v = fit_check(rep, manifest)
            out["fit"].append({"machine": mname, "entry": ename, "right_entry": kind == ename,
                               "ratio": round(v["ratio"], 3), "ok": v["ok"],
                               "readings_missing": len(rep["readings_missing"]),
                               "readings_constant": len(rep["readings_constant"])})
            cells.append(f"{v['ratio']:14.2f} {'fits' if v['ok'] else 'no fit':>7s}")
        print(f"{mname:42s} " + " ".join(cells))

    print(f"\nSIMULATED. alarm share of the {WATCH_S} s of normal running after calibrating on the first L seconds,")
    print("right entry only. calibrated / entry as shipped / trained on the same L seconds alone (needs 300 s)")
    print(f"{'machine':42s} " + " ".join(f"{'L=' + str(n) + ' s':>20s}" for n in lengths))
    for mname, (kind, seed, start, settings) in machines.items():
        t, X = session(kind, seed, start, SESSION_S, settings, cache=a.cache)
        d, manifest = entries[kind]
        cells = []
        for n in lengths:
            sel = t < t[0] + n
            after = (t >= t[0] + n) & (t < t[0] + n + WATCH_S)
            cal, rep = calibrate(d, t[sel], X[sel], NAMES, min_running_s=n - 1)
            row = {"machine": mname, "entry": kind, "calibration_s": n, "threshold": rep["threshold"],
                   "fit_ratio": round(fit_check(rep, manifest)["ratio"], 3),
                   "calibrated": share(cal, t[after], X[after]), "entry_as_shipped": share(d, t[after], X[after])}
            own = "-"
            if n >= 300:
                alone, _ = train_model(t[sel], X[sel], NAMES)
                row["trained_alone"] = share(alone, t[after], X[after])
                own = f"{row['trained_alone']['alarm_share']:.3f}"
            out["lengths"].append(row)
            cells.append(f"{row['calibrated']['alarm_share']:.3f}/{row['entry_as_shipped']['alarm_share']:.3f}/{own:>5s}")
        print(f"{mname:42s} " + " ".join(f"{c:>20s}" for c in cells))

    if not a.quick:
        kind, seed, start, settings = FAULT_MACHINE
        d, _ = entries[kind]
        print(f"\nSIMULATED. faults on 'conveyor, seed {seed}': calibrated on the first {FAULT_CALIB_S} s, fault on "
              f"from {FAULT_ONSET_S} s to the end ({FAULT_SESSION_S} s)")
        for fault, sev in FAULT_RUNS:
            t, X = session(kind, seed, start, FAULT_SESSION_S, settings, faults=((fault, FAULT_ONSET_S, sev),),
                           cache=a.cache)
            sel = t < t[0] + FAULT_CALIB_S
            cal, _ = calibrate(d, t[sel], X[sel], NAMES)
            before = (t >= t[0] + FAULT_CALIB_S) & (t < t[0] + FAULT_ONSET_S)
            during = t >= t[0] + FAULT_ONSET_S
            jb, ab, _, _, _ = watch(cal, t[before], X[before])
            jd, ad, _, first, names = watch(cal, t[during], X[during])
            row = {"fault": fault, "severity": sev, "alarm_share_before": round(ab / max(jb, 1), 4),
                   "alarm_share_during": round(ad / max(jd, 1), 4),
                   "seconds_to_alarm": None if first is None else round(first - (t[0] + FAULT_ONSET_S), 1),
                   "named": dict(names.most_common(3))}
            out["faults"].append(row)
            print(f"  {fault:10s} severity {sev:.1f}: alarm share before {row['alarm_share_before']:.3f}, during "
                  f"{row['alarm_share_during']:.3f}, first alarm after {row['seconds_to_alarm']} s, named {row['named']}")

    out["seconds"] = round(time.time() - t_start, 1)
    if a.out:
        Path(a.out).write_text(json.dumps(out, indent=1))
        print(f"\nwrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
