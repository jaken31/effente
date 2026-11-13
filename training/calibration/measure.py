#!/usr/bin/env python3
"""Measure calibration of library entries on SIMULATED machines. Laptop only (the simulators need SciPy).

Everything here is simulated: effente.simulate_conveyor and effente.simulate_server, with
effente.site_noise on top. No recording of a real machine exists yet, so every number this prints
is "simulated" and says nothing certain about real machines (unverified).

    python3 -m training.calibration.measure --entry training/library/entries/conveyor/0.1.0 \\
        --entry training/library/entries/generic_rotating/0.1.0 --out calibration-sim.json

--entry is a library entry folder (model.json and manifest.json); give it once per entry.

What it does:
  machines    simulated sessions of one hour, each starting with a fresh history, as a device just
              snapped on would: the simulator's conveyor with four seeds, loads and room
              temperatures, and its server rack with three seeds. The conveyors are one simulated
              design; if an entry was built from the same simulator they are not a new design to
              it, only new seeds, loads and rooms. With --sensors device (the default) the rows
              keep only what the device has today: the sound, heat and room readings are missing.
  section 1   every entry on every machine, calibrated on the first 300 s: the fit check's ratio
              and verdict, the threshold, and the share of the 40 minutes of normal running that
              follow in which the device's Watcher alarms (and of the first 5 of them). Beside it,
              a model trained on the same 300 s alone (effente.trainer.train_model).
  section 2   the same alarm share for calibrations of 60 s to 20 minutes.
  section 3   conveyor sessions with an unbalance or a looseness switched on 10 minutes after a
              300 s calibration: alarm or not, seconds to the alarm, what it was called. With
              --classifier, also what it is called when a fault classifier is fitted during
              calibration (calibrate does that only for an entry that carries one).
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
WATCH_S = 2400  # normal running judged after each calibration
SESSION_S = 3600
LENGTHS = (60, 120, 180, 300, 600, 1200)
NAMES = list(F.FEATURE_NAMES)
QUIET = ("normal", "warming_up", "stopped")
NOT_ON_DEVICE = [i for i, n in enumerate(NAMES) if F.FEATURE_SENSOR.get(n) in ("heat", "sound", "room")]

# name: (kind, seed, start, simulator settings). Conveyors run 06:00-22:00 on weekdays.
MACHINES = {
    "conveyor, seed 1": ("conveyor", 1, DAY + 13 * H, {}),
    "conveyor, seed 2, load x1.2, room +5 C": ("conveyor", 2, 10 * H, dict(load_scale=1.2, ambient_offset_c=5.0)),
    "conveyor, seed 3, load x0.8, room -5 C": ("conveyor", 3, 2 * DAY + 15 * H, dict(load_scale=0.8, ambient_offset_c=-5.0)),
    "conveyor, seed 4, room +10 C": ("conveyor", 4, 3 * DAY + 9 * H, dict(ambient_offset_c=10.0)),
    "server rack, seed 2": ("server", 2, DAY + 14 * H, {}),
    "server rack, seed 5": ("server", 5, 10 * H, {}),
    "server rack, seed 6": ("server", 6, DAY + 16 * H, {}),
}
FAULT_RUNS = (("unbalance", 1.0), ("unbalance", 0.3), ("looseness", 1.0), ("looseness", 0.3))
FAULT_MACHINE = ("conveyor", 2, 10 * H, dict(load_scale=1.2, ambient_offset_c=5.0))
FAULT_CALIB_S, FAULT_ONSET_S, FAULT_SESSION_S = 300, 900, 1800


def session(kind, seed, start, seconds, settings, faults=(), cache=None, sensors="device"):
    """One-a-second feature rows of a simulated session with a fresh history: (t, X)."""
    key = f"{kind}-{seed}-{start}-{seconds}-" + "-".join(f"{k}{v}" for k, v in sorted(settings.items())) + \
          "".join(f"-{k}{a}{s}" for k, a, s in faults)
    path = Path(cache) / f"{key}.npz" if cache else None
    if path and path.exists():
        with np.load(path) as d:
            t, X = d["t"], d["X"]
    else:
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
    if sensors == "device":  # the device today: accelerometer, gyro and the IMU's temperature
        X = np.array(X, float)
        X[:, NOT_ON_DEVICE] = np.nan
    return t, X


def run_calibrate(model, manifest, t, X, min_running_s=300):
    """Calibrate and fit-check: (calibrated model, {"ratio", "ok", "threshold"})."""
    cal, rep = calibrate(model, t, X, NAMES, min_running_s=min_running_s)
    v = fit_check(rep, manifest)
    return cal, {"ratio": round(v["ratio"], 3), "ok": v["ok"], "threshold": rep["threshold"]}


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
    ap.add_argument("--entry", action="append", required=True, help="library entry folder; repeat for more")
    ap.add_argument("--sensors", choices=("device", "all"), default="device",
                    help="device: accelerometer, gyro and IMU temperature only (default); all: every simulated sensor")
    ap.add_argument("--out", help="write every number as JSON here")
    ap.add_argument("--cache", help="folder to keep the simulated rows in between runs")
    ap.add_argument("--classifier", action="store_true",
                    help="section 3: also name the faults with a fault classifier fitted during calibration")
    ap.add_argument("--quick", action="store_true", help="one conveyor, one server rack, section 1 only")
    a = ap.parse_args(argv)
    t_start = time.time()
    machines = {k: MACHINES[k] for k in list(MACHINES)[1:2] + list(MACHINES)[5:6]} if a.quick else MACHINES
    get = lambda kind, seed, start, seconds, settings, faults=(): session(
        kind, seed, start, seconds, settings, faults, cache=a.cache, sensors=a.sensors)
    entries = {}
    for folder in a.entry:
        folder = Path(folder)
        manifest = json.loads((folder / "manifest.json").read_text())
        name = str(manifest.get("entry_id") or manifest.get("entry") or folder.name)
        entries[name] = (json.loads((folder / "model.json").read_text()), manifest)
    out = {"label": "simulated (effente.simulate_conveyor, effente.simulate_server, effente.site_noise); "
                    "unverified on real machines", "sensors": a.sensors, "entries": list(entries),
           "calibrated_300s": [], "lengths": [], "faults": []}

    print(f"SIMULATED, sensors: {a.sensors}. 1. Calibrated on the first 300 s, then {WATCH_S} s of normal running.")
    print("   ratio = fit check; alarm = share of seconds alarming in the first 300 s / all 2400 s after calibration;")
    print("   alone = the same for a model trained on the same 300 s alone")
    for ename, (d, manifest) in entries.items():
        print(f" entry {ename}")
        for mname, (kind, seed, start, settings) in machines.items():
            t, X = get(kind, seed, start, SESSION_S, settings)
            sel = t < t[0] + 300
            first5 = (t >= t[0] + 300) & (t < t[0] + 600)
            after = (t >= t[0] + 300) & (t < t[0] + 300 + WATCH_S)
            cal, v = run_calibrate(d, manifest, t[sel], X[sel])
            alone, _ = train_model(t[sel], X[sel], NAMES)
            row = {"entry": ename, "machine": mname, **v, "first_300s": share(cal, t[first5], X[first5]),
                   "all_2400s": share(cal, t[after], X[after]), "trained_alone_2400s": share(alone, t[after], X[after])}
            out["calibrated_300s"].append(row)
            print(f"   {mname:40s} ratio {v['ratio']:5.2f} {'fits  ' if v['ok'] else 'no fit'} threshold "
                  f"{v['threshold']:7.2f}  alarm {row['first_300s']['alarm_share']:.3f} / "
                  f"{row['all_2400s']['alarm_share']:.3f}  alone {row['trained_alone_2400s']['alarm_share']:.3f}")

    if not a.quick:
        print(f"\nSIMULATED, sensors: {a.sensors}. 2. Share of the {WATCH_S} s after calibration that alarm, "
              f"by calibration length")
        print(f"   {'':40s} " + " ".join(f"{str(n) + ' s':>7s}" for n in LENGTHS))
        for ename, (d, manifest) in entries.items():
            print(f" entry {ename}")
            for mname, (kind, seed, start, settings) in machines.items():
                t, X = get(kind, seed, start, SESSION_S, settings)
                cells = []
                for n in LENGTHS:
                    sel = t < t[0] + n
                    after = (t >= t[0] + n) & (t < t[0] + n + WATCH_S)
                    cal, v = run_calibrate(d, manifest, t[sel], X[sel], min_running_s=n - 1)
                    row = {"entry": ename, "machine": mname, "calibration_s": n, **v, **share(cal, t[after], X[after])}
                    out["lengths"].append(row)
                    cells.append(f"{row['alarm_share']:7.3f}")
                print(f"   {mname:40s} " + " ".join(cells))

        kind, seed, start, settings = FAULT_MACHINE
        print(f"\nSIMULATED, sensors: {a.sensors}. 3. Faults on 'conveyor, seed {seed}': calibrated on the first "
              f"{FAULT_CALIB_S} s, fault on from {FAULT_ONSET_S} s to the end ({FAULT_SESSION_S} s)")
        for ename, (d, manifest) in entries.items():
            print(f" entry {ename}")
            for fault, sev in FAULT_RUNS:
                t, X = get(kind, seed, start, FAULT_SESSION_S, settings, faults=((fault, FAULT_ONSET_S, sev),))
                sel = t < t[0] + FAULT_CALIB_S
                cal, _ = run_calibrate(d, manifest, t[sel], X[sel])
                before = (t >= t[0] + FAULT_CALIB_S) & (t < t[0] + FAULT_ONSET_S)
                during = t >= t[0] + FAULT_ONSET_S
                jb, ab, _, _, _ = watch(cal, t[before], X[before])
                jd, ad, _, first, names = watch(cal, t[during], X[during])
                row = {"entry": ename, "fault": fault, "severity": sev,
                       "alarm_share_before": round(ab / max(jb, 1), 4), "alarm_share_during": round(ad / max(jd, 1), 4),
                       "seconds_to_alarm": None if first is None else round(first - (t[0] + FAULT_ONSET_S), 1),
                       "named": dict(names.most_common(3))}
                print(f"   {fault:10s} severity {sev:.1f}: alarm share before {row['alarm_share_before']:.3f}, during "
                      f"{row['alarm_share_during']:.3f}, first alarm after {row['seconds_to_alarm']} s, "
                      f"named {row['named']}")
                if not d.get("fault_classifier") and a.classifier:
                    # the same entry asking for a classifier (calibrate fits one to an entry that carries the key)
                    cal, _ = run_calibrate(dict(d, fault_classifier=None), manifest, t[sel], X[sel])
                    row["named_with_a_classifier_fitted_here"] = dict(watch(cal, t[during], X[during])[4].most_common(3))
                    print(f"   {'':10s} with a fault classifier fitted during calibration: "
                          f"named {row['named_with_a_classifier_fitted_here']}")
                out["faults"].append(row)

    out["seconds"] = round(time.time() - t_start, 1)
    if a.out:
        Path(a.out).write_text(json.dumps(out, indent=1))
        print(f"\nwrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
