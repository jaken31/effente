#!/usr/bin/env python3
"""Measure calibration of library entries on SIMULATED machines. Laptop only (the simulators need SciPy).

Everything here is simulated: effente.simulate_conveyor and effente.simulate_server, with
effente.site_noise on top. No recording of a real machine exists yet, so every number this prints
is "simulated" and says nothing certain about real machines (unverified).

    python3 -m training.calibration.measure --entry training/library/entries/conveyor \\
        --entry training/library/entries/generic_rotating --out calibration-sim.json

--entry is a library entry folder (model.json and manifest.json); give it once per entry.

What it does:
  machines    simulated sessions of one hour, each starting with a fresh history, as a device just
              snapped on would: the simulator's conveyor with four seeds, loads and room
              temperatures, and its server rack with three seeds. The conveyors are one simulated
              design; if an entry was built from the same simulator they are not a new design to
              it, only new seeds, loads and rooms. With --sensors device (the default) the rows
              keep only what the device has today: the sound, heat and room readings are missing.
  section 1   every entry on every machine, calibrated on the first --calibration-s seconds (300):
              the fit check's ratio and verdict, the threshold with the held-out median and
              highest score behind it, and the share of the 40 minutes of normal running that
              follow in which the device's Watcher alarms (and of the first 5 of them). Beside it,
              a model trained on the same 300 s alone (effente.trainer.train_model).
  section 2   the same alarm share for calibrations of 60 s to 20 minutes.
  section 3   conveyor sessions with an unbalance or a looseness switched on 10 minutes after a
              300 s calibration: alarm or not, seconds to the alarm, what it was called. With
              --classifier, also what it is called when a fault classifier is fitted during
              calibration (calibrate does that only for an entry that carries one).
              --all-faults runs every sudden fault of both simulators on every machine above
              instead (conveyor: 11 kinds, obvious and subtle; server rack: 4 kinds) and counts
              how many alarm. Sections 1 and 3 end with one summary line per entry, which is what
              a sweep of --threshold-median-multiple compares.
  section 4   with --watch-hours only: a 300 s calibration at --start-hour, then a long watch of
              simulated conveyors: alarm seconds by clock hour and the reading that moved most in
              each. --hold-from-manifest adds the variant with the manifest's hold list in force.
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
FAULT_ONSET_S, FAULT_SESSION_S = 900, 1800
JAM_S = 180  # a jam stops the belt; it is cleared after this long, so there are running seconds to judge
SERVER_FAULTS = (("ups_alarm", 300), ("rack_bump", 2), ("hdd_clicking", None), ("fan5_failure", None))  # seconds on


def all_fault_runs(kind):
    """--all-faults: every sudden fault the simulator of this kind of machine has, as (fault,
    severity, seconds it lasts or None for "to the end"). The wear kinds that grow over days do not
    fit a 30-minute session and are left out."""
    if kind == "conveyor":
        from effente.simulate_conveyor import FAULT_KINDS, OBVIOUS, SLOW, SUBTLE
        return [(k, sev, JAM_S if k == "jam" else None) for k in FAULT_KINDS if k not in SLOW for sev in (OBVIOUS, SUBTLE)]
    return [(k, 1.0, lasts) for k, lasts in SERVER_FAULTS]


def session(kind, seed, start, seconds, settings, faults=(), cache=None, sensors="device"):
    """One-a-second feature rows of a simulated session with a fresh history: (t, X).
    faults: (kind, seconds into the session it starts, severity[, seconds it lasts]); without the
    last, the fault stays on to the end."""
    faults = [tuple(f) + (None,) * (4 - len(f)) for f in faults]
    key = f"{kind}-{seed}-{start}-{seconds}-" + "-".join(f"{k}{v}" for k, v in sorted(settings.items())) + \
          "".join(f"-{k}{a}{s}" + (f"for{n}" if n else "") for k, a, s, n in faults)
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
        days = int((start + seconds) // DAY) + 1  # a session may run past midnight
        fl = [Fault(k, start + a, start + a + n if n else days * DAY + DAY, f"{k} (simulated)", s)
              for k, a, s, n in faults]
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


MULTIPLE = None  # --threshold-median-multiple; None leaves calibrate's default


def run_calibrate(model, manifest, t, X, min_running_s=300):
    """Calibrate and fit-check: (calibrated model, {"ratio", "ok", "threshold", ...})."""
    extra = {} if MULTIPLE is None else {"threshold_median_multiple": MULTIPLE}
    cal, rep = calibrate(model, t, X, NAMES, min_running_s=min_running_s, **extra)
    v = fit_check(rep, manifest)
    return cal, {"ratio": round(v["ratio"], 3), "ok": v["ok"], "threshold": rep["threshold"],
                 "held_out_smoothed_median": rep["held_out_smoothed_median"],
                 "held_out_smoothed_max": rep["held_out_smoothed_max"],
                 "threshold_below_highest_held_out": rep["threshold_below_highest_held_out"],
                 "entry_threshold": rep["entry_threshold"]}


def watch(d, t, X):
    """The device's Watcher over rows: (seconds judged, seconds abnormal, alarm stretches, first alarm time,
    names, the reading that moved most in each abnormal second)."""
    w = RT.Watcher(RT.Model(d))
    judged = abnormal = stretches = 0
    first, names, last, was, moved = None, Counter(), None, False, Counter()
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
            if r.get("features"):
                moved[max(r["features"], key=lambda k: abs(r["features"][k]))] += 1
            if first is None:
                first = float(r["minute"])
            if not was:
                stretches += 1
        was = bad
    return judged, abnormal, stretches, first, names, moved


def slow_reading(name: str) -> bool:
    """A reading the library's manifests list under calibration.hold: the clock, trends, the IMU's temperature."""
    return name.startswith(("time_", "trend_")) or name == "imu_temp_c"


def share(d, t, X):
    judged, abnormal, stretches, _, _, moved = watch(d, t, X)
    return {"judged_s": judged, "alarm_s": abnormal, "alarm_share": round(abnormal / max(judged, 1), 4),
            "alarm_stretches": stretches, "moved_most": dict(moved.most_common(3)),
            "alarm_s_led_by_a_slow_reading": sum(n for k, n in moved.items() if slow_reading(k))}


def with_manifest_hold(model: dict, manifest: dict) -> dict:
    """A copy of an entry's model file carrying the manifest's calibration.hold list, so calibrate's
    model-carried hold runs. For measuring only: the library keeps hold in the manifest, and
    calibrate does not read the manifest."""
    hold = list((manifest.get("calibration") or {}).get("hold") or [])
    return dict(model, calibration={"hold": hold})


def day_watch(d, t, X, t_zero):
    """The Watcher over a long stretch: alarm seconds by clock hour, what they were called, and which
    reading moved most in each alarm second. Clock hours count from Monday 00:00 (the simulators' zero)."""
    w = RT.Watcher(RT.Model(d))
    judged = alarm = 0
    by_hour, names, moved, last = Counter(), Counter(), Counter(), None
    for ti, x in zip(t, X):
        r = w.predict_at(float(ti), x)
        if r.get("minute") is None or r.get("minute") == last:
            continue
        last = r["minute"]
        if r["diagnosis"] in ("warming_up", "stopped"):
            continue
        judged += 1
        if r["diagnosis"] not in QUIET:
            alarm += 1
            by_hour[int(float(r["minute"]) // H) % 24] += 1
            names[r["diagnosis"]] += 1
            if r.get("features"):
                moved[max(r["features"], key=lambda k: abs(r["features"][k]))] += 1
    clock = sum(n for k, n in moved.items() if k.startswith("time_") or k == "imu_temp_c")
    trend = sum(n for k, n in moved.items() if k.startswith("trend_"))
    return {"judged_s": judged, "alarm_s": alarm, "alarm_share": round(alarm / max(judged, 1), 4),
            "alarm_s_by_clock_hour": {f"{h:02d}": by_hour[h] for h in sorted(by_hour)},
            "named": dict(names.most_common(4)), "moved_most": dict(moved.most_common(6)),
            "alarm_s_led_by_clock_or_imu_temperature": clock, "alarm_s_led_by_a_trend": trend}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--entry", action="append", required=True, help="library entry folder; repeat for more")
    ap.add_argument("--sensors", choices=("device", "all"), default="device",
                    help="device: accelerometer, gyro and IMU temperature only (default); all: every simulated sensor")
    ap.add_argument("--out", help="write every number as JSON here")
    ap.add_argument("--cache", help="folder to keep the simulated rows in between runs")
    ap.add_argument("--classifier", action="store_true",
                    help="section 3: also name the faults with a fault classifier fitted during calibration")
    ap.add_argument("--threshold-median-multiple", type=float,
                    help="calibrate's threshold_median_multiple (default: calibrate's own default)")
    ap.add_argument("--calibration-s", type=int, default=300,
                    help="sections 1 and 3: seconds of calibration (default 300, the minimum; 600 is what people are asked for)")
    ap.add_argument("--faults-only", action="store_true", help="section 3 only")
    ap.add_argument("--all-faults", action="store_true",
                    help="section 3: every sudden fault of both simulators on every machine, counted per entry")
    ap.add_argument("--watch-hours", type=float,
                    help="section 4 only: calibrate on 300 s from --start-hour, then watch this many hours")
    ap.add_argument("--start-hour", type=float, default=8.0, help="section 4: clock hour (Monday) the session starts")
    ap.add_argument("--watch-machines", type=int, default=2, help="section 4: how many of the simulated conveyors")
    ap.add_argument("--hold-from-manifest", action="store_true",
                    help="section 4: also the variant with the manifest's calibration.hold copied into the model")
    ap.add_argument("--quick", action="store_true", help="one conveyor, one server rack, section 1 only")
    a = ap.parse_args(argv)
    global MULTIPLE
    MULTIPLE = a.threshold_median_multiple
    cs = a.calibration_s
    if not 60 <= cs < FAULT_ONSET_S:
        ap.error(f"--calibration-s must be from 60 to {FAULT_ONSET_S - 1}")
    t_start = time.time()
    machines = {k: MACHINES[k] for k in list(MACHINES)[1:2] + list(MACHINES)[5:6]} if a.quick else MACHINES
    if a.faults_only or a.watch_hours:
        machines = {}
    get = lambda kind, seed, start, seconds, settings, faults=(): session(
        kind, seed, start, seconds, settings, faults, cache=a.cache, sensors=a.sensors)
    entries = {}
    for folder in a.entry:
        folder = Path(folder)
        manifest = json.loads((folder / "manifest.json").read_text())
        name = str(manifest.get("entry_id") or manifest.get("entry") or folder.name)
        if name in entries:  # two entries with one name: tell them apart by folder
            name = f"{name} ({folder})"
        entries[name] = (json.loads((folder / "model.json").read_text()), manifest)
    out = {"label": "simulated (effente.simulate_conveyor, effente.simulate_server, effente.site_noise); "
                    "unverified on real machines", "sensors": a.sensors, "entries": list(entries),
           "threshold_median_multiple": "default" if MULTIPLE is None else MULTIPLE, "calibration_s": cs,
           "calibrated_300s": [], "lengths": [], "faults": []}

    print(f"threshold_median_multiple: {out['threshold_median_multiple']}, calibration: {cs} s")
    if not a.faults_only and not a.watch_hours:
        print(f"SIMULATED, sensors: {a.sensors}. 1. Calibrated on the first {cs} s, then {WATCH_S} s of normal running.")
        print("   ratio = fit check (pass = no gross mismatch found, not proof of machine type); alarm = share of seconds alarming in the first 300 s / all 2400 s after calibration;")
        print(f"   alone = the same for a model trained on the same {cs} s alone; threshold = multiple x median of the")
        print("   held-out smoothed scores; highest = the highest of those scores (the trainer never goes below it)")
        for ename, (d, manifest) in entries.items():
            print(f" entry {ename}")
            for mname, (kind, seed, start, settings) in machines.items():
                t, X = get(kind, seed, start, SESSION_S, settings)
                sel = t < t[0] + cs
                first5 = (t >= t[0] + cs) & (t < t[0] + cs + 300)
                after = (t >= t[0] + cs) & (t < t[0] + cs + WATCH_S)
                cal, v = run_calibrate(d, manifest, t[sel], X[sel], min_running_s=cs - 1)
                alone, _ = train_model(t[sel], X[sel], NAMES)
                row = {"entry": ename, "machine": mname, **v, "first_300s": share(cal, t[first5], X[first5]),
                       "all_2400s": share(cal, t[after], X[after]), "trained_alone_2400s": share(alone, t[after], X[after])}
                out["calibrated_300s"].append(row)
                print(f"   {mname:40s} ratio {v['ratio']:5.2f} {'pass' if v['ok'] else 'FAIL'} threshold "
                      f"{v['threshold']:7.2f} (median {v.get('held_out_smoothed_median', float('nan')):5.2f}, highest {v.get('held_out_smoothed_max', float('nan')):5.2f})  alarm {row['first_300s']['alarm_share']:.3f} / "
                      f"{row['all_2400s']['alarm_share']:.3f}  alone {row['trained_alone_2400s']['alarm_share']:.3f}")
                if row["all_2400s"]["alarm_s"]:
                    a2 = row["all_2400s"]
                    print(f"      of its {a2['alarm_s']} alarm seconds, {a2['alarm_s_led_by_a_slow_reading']} were led by a clock, "
                          f"trend or IMU temperature reading; moved most: {a2['moved_most']}")

        rows = [r for r in out["calibrated_300s"] if "threshold_below_highest_held_out" in r]
        below = sum(bool(r["threshold_below_highest_held_out"]) for r in rows)
        print(f"   the threshold was below the highest held-out smoothed score in {below} of {len(rows)} calibrations")
        out["threshold_below_highest_held_out"] = {"below": below, "of": len(rows)}
        out["normal_summary"] = []
        for ename in entries:
            mine = [r["all_2400s"] for r in out["calibrated_300s"] if r["entry"] == ename]
            row = {"entry": ename, "machines": len(mine), "machines_with_an_alarm": sum(r["alarm_s"] > 0 for r in mine),
                   "alarm_s": sum(r["alarm_s"] for r in mine), "judged_s": sum(r["judged_s"] for r in mine),
                   "alarm_stretches": sum(r["alarm_stretches"] for r in mine),
                   "highest_alarm_share": max(r["alarm_share"] for r in mine)}
            out["normal_summary"].append(row)
            print(f"   summary, entry {ename}: {row['machines_with_an_alarm']} of {row['machines']} machines alarmed in the "
                  f"{WATCH_S} s of normal running; {row['alarm_s']} alarm seconds of {row['judged_s']} judged, in "
                  f"{row['alarm_stretches']} stretches; highest share on one machine {row['highest_alarm_share']:.3f}")

    if not a.quick and not a.faults_only and not a.watch_hours:
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

    if not a.quick and not a.watch_hours:
        if a.all_faults:
            fault_machines = [(mname, m, all_fault_runs(m[0])) for mname, m in MACHINES.items()]
            print(f"\nSIMULATED, sensors: {a.sensors}. 3. Every sudden simulated fault on every machine: calibrated on "
                  f"the first {cs} s, fault on from {FAULT_ONSET_S} s (session {FAULT_SESSION_S} s; a jam lasts {JAM_S} s)")
        else:
            fault_machines = [(f"conveyor, seed {FAULT_MACHINE[1]}", FAULT_MACHINE, [(f, s, None) for f, s in FAULT_RUNS])]
            print(f"\nSIMULATED, sensors: {a.sensors}. 3. Faults on 'conveyor, seed {FAULT_MACHINE[1]}': calibrated on the "
                  f"first {cs} s, fault on from {FAULT_ONSET_S} s to the end ({FAULT_SESSION_S} s)")
        for ename, (d, manifest) in entries.items():
            print(f" entry {ename}")
            for mname, (kind, seed, start, settings), runs in fault_machines:
                if a.all_faults:
                    print(f"  {mname}")
                for fault, sev, lasts in runs:
                    t, X = get(kind, seed, start, FAULT_SESSION_S, settings, faults=((fault, FAULT_ONSET_S, sev, lasts),))
                    sel = t < t[0] + cs
                    cal, cv = run_calibrate(d, manifest, t[sel], X[sel], min_running_s=cs - 1)
                    before = (t >= t[0] + cs) & (t < t[0] + FAULT_ONSET_S)
                    during = t >= t[0] + FAULT_ONSET_S
                    jb, ab = watch(cal, t[before], X[before])[:2]
                    jd, ad, _, first, names, _ = watch(cal, t[during], X[during])
                    row = {"entry": ename, "machine": mname, "machine_kind": kind, "fault": fault, "severity": sev,
                           "threshold": cv["threshold"],
                           "alarm_share_before": round(ab / max(jb, 1), 4), "alarm_share_during": round(ad / max(jd, 1), 4),
                           "seconds_to_alarm": None if first is None else round(first - (t[0] + FAULT_ONSET_S), 1),
                           "named": dict(names.most_common(3))}
                    print(f"   {fault:16s} severity {sev:.1f}: alarm share before {row['alarm_share_before']:.3f}, during "
                          f"{row['alarm_share_during']:.3f}, first alarm after {row['seconds_to_alarm']} s, "
                          f"named {row['named']}")
                    if not d.get("fault_classifier") and a.classifier:
                        # the same entry asking for a classifier (calibrate fits one to an entry that carries the key)
                        cal, _ = run_calibrate(dict(d, fault_classifier=None), manifest, t[sel], X[sel], min_running_s=cs - 1)
                        row["named_with_a_classifier_fitted_here"] = dict(watch(cal, t[during], X[during])[4].most_common(3))
                        print(f"   {'':16s} with a fault classifier fitted during calibration: "
                              f"named {row['named_with_a_classifier_fitted_here']}")
                    out["faults"].append(row)
        # A fault counts as caught only if the machine was quiet before it: an alarm that was already
        # on says nothing about the fault.
        out["fault_summary"] = []
        print(" summary: faults that alarmed, of those with no alarm between the calibration and the fault")
        for ename in entries:
            for kind, sev in sorted({(r["machine_kind"], r["severity"]) for r in out["faults"]}):
                mine = [r for r in out["faults"] if (r["entry"], r["machine_kind"], r["severity"]) == (ename, kind, sev)]
                quiet = [r for r in mine if r["alarm_share_before"] == 0]
                row = {"entry": ename, "machine_kind": kind, "severity": sev, "faults": len(mine),
                       "quiet_before": len(quiet), "caught": sum(r["seconds_to_alarm"] is not None for r in quiet)}
                out["fault_summary"].append(row)
                print(f"   entry {ename}, {kind} faults, severity {sev:.1f}: {row['caught']} alarmed of {row['quiet_before']} "
                      f"({row['faults'] - row['quiet_before']} of {row['faults']} left out: alarm before the fault)")

    if a.watch_hours:
        start, watch_s = a.start_hour * H, a.watch_hours * H
        conveyors = [(k, v) for k, v in MACHINES.items() if v[0] == "conveyor"][1:1 + a.watch_machines]
        out["day_watch"] = []
        print(f"\nSIMULATED, sensors: {a.sensors}. 4. Calibrated on 300 s from {a.start_hour:g}:00 on Monday, then "
              f"watched for {a.watch_hours:g} h. Alarm share is of the seconds the machine ran (06:00 to 22:00).")
        for ename, (d, manifest) in entries.items():
            variants = [("as merged", d)]
            if a.hold_from_manifest:
                variants.append(("with the manifest's hold", with_manifest_hold(d, manifest)))
            for mname, (kind, seed, _, settings) in conveyors:
                t, X = get(kind, seed, start, 300 + watch_s, settings)
                sel = t < t[0] + 300
                for vname, model in variants:
                    cal, v = run_calibrate(model, manifest, t[sel], X[sel])
                    row = {"entry": ename, "machine": mname, "variant": vname, **v, **day_watch(cal, t[~sel], X[~sel], t[0])}
                    out["day_watch"].append(row)
                    print(f" entry {ename}, {mname}, {vname}: threshold {v['threshold']:.2f}")
                    print(f"   alarm share {row['alarm_share']:.4f} ({row['alarm_s']} of {row['judged_s']} s)")
                    print(f"   alarm seconds by clock hour: {row['alarm_s_by_clock_hour']}")
                    print(f"   named: {row['named']}")
                    print(f"   reading that moved most, per alarm second: {row['moved_most']}")
                    print(f"   led by the clock or the IMU temperature: {row['alarm_s_led_by_clock_or_imu_temperature']} s; "
                          f"led by a trend: {row['alarm_s_led_by_a_trend']} s")

    out["seconds"] = round(time.time() - t_start, 1)
    if a.out:
        Path(a.out).write_text(json.dumps(out, indent=1))
        print(f"\nwrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
