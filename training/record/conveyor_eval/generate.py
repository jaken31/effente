#!/usr/bin/env python3
"""Make the realistic conveyor data for comparing "is it still normal?" methods.

The machine is effente.simulate_conveyor's industrial belt conveyor in realistic mode (the device on the
drive housing; the thermal camera and microphone pointed at the drive), with real-site disturbances
(effente.site_noise, factory profile). Levels and their sources: REALISM.md in this folder.

Runs, each written like a device's learn-mode folder (hour files with one feature row a second and
per-minute summaries, a 10 s raw clip every hour, labels.json with what was going on each minute):

  normal-w1, normal-w2   the two weeks to learn from: one continuous 14-day machine, each week run as its
                         own chain (the device's trend history starts fresh on each Monday)
  heldout                another week, never learned from: warmer (+4 C), about 10 % busier, and a planned
                         2-hour maintenance stop on Wednesday. Counts false alarms
  <fault>-<subtle|obvious>
                         2 days each, one fault, starting on different weekdays and at different hours of a
                         shift: slow faults (wear) grow over the rest of the run, the others switch on and stay

    python3 -m training.record.conveyor_eval.generate --out data/fake/conveyor-eval --workers 8
    python3 -m training.record.conveyor_eval.generate --out /tmp/x --smoke      # a 3-minute check

Runs that already have labels.json are skipped, so an interrupted generation can simply be restarted.
"""

from __future__ import annotations

import os

# One maths thread per process: with 8 processes each starting several, they fought over the cores and
# every run went about 5 times slower (12 Nov).
for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

import argparse
import json
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np

DAY = 86400


def scenarios(smoke: bool = False) -> list[dict]:
    from effente.simulate_conveyor import FAULT_KINDS, SLOW, SUBTLE, OBVIOUS
    if smoke:
        return [dict(name="smoke", seed=21, days=1, start=10 * 3600, end=10 * 3600 + 180, site_seed=31, faults=[])]
    out = [dict(name="normal-w1", seed=21, days=14, start=0, end=7 * DAY, site_seed=31, faults=[]),
           dict(name="normal-w2", seed=21, days=14, start=7 * DAY, end=14 * DAY, site_seed=31, faults=[]),
           dict(name="heldout", seed=22, week=5, days=7, start=0, end=7 * DAY, site_seed=32, faults=[],
                ambient_offset_c=4.0, load_scale=1.1, stops=[(2 * DAY + 10 * 3600, 2 * DAY + 12 * 3600)])]
    rng = np.random.default_rng(7)
    k = 0
    for kind in FAULT_KINDS:
        for label, sev in (("subtle", SUBTLE), ("obvious", OBVIOUS)):
            d0 = k % 5  # starts Monday to Friday
            if kind in SLOW and d0 == 4:  # wear must peak while the belt runs, not over a stopped weekend
                d0 = 3
            onset = d0 * DAY + float(rng.uniform(8, 18)) * 3600
            end = (d0 + 2) * DAY
            if kind == "jam":  # an obvious jam stops the belt once; a subtle one is a few stalls (bog-downs)
                times = [onset] if sev >= 0.5 else [onset + h * 3600 for h in (0, 2.5, 5.0, 24.0, 26.5)]
                faults = [(kind, t, t + 180, sev) for t in times]
            elif kind in SLOW:  # wear grows over the rest of the run and reaches its full severity at the end
                faults = [(kind, onset, end, sev)]
            else:  # switches on at the onset and stays on past the run's end
                faults = [(kind, onset, end + DAY, sev)]
            out.append(dict(name=f"{kind}-{label}", seed=100 + k, days=d0 + 3, start=d0 * DAY, end=end,
                            site_seed=200 + k, faults=faults, fault_kind=kind, severity=sev, onset=onset))
            k += 1
    return out


def minute_nuisances(events, minutes) -> list[list[str]]:
    """Per minute, every site disturbance overlapping it (not only the instant at its 30th second: most
    bangs and jolts last under a second and would be missed)."""
    ev = sorted(events, key=lambda e: e.start)
    starts = np.array([e.start for e in ev])
    longest = max((e.end - e.start for e in ev), default=0.0)
    out = []
    for m in minutes:
        lo = np.searchsorted(starts, m - longest)  # earlier ones have all ended
        out.append(sorted({e.kind for e in ev[lo:] if e.start < m + 60 and e.end > m}))
    return out


def scenario_site(sc: dict):
    """The SiteNoise of a scenario, rebuilt from its seeds (the schedule only, no samples)."""
    from effente.simulate_conveyor import ConveyorSim, Fault
    from effente.site_noise import SiteNoise
    faults = [Fault(k, a, b, "", sev) for k, a, b, sev in sc.get("faults", [])]
    sim = ConveyorSim(days=sc["days"], seed=sc["seed"], week=sc.get("week", 0), faults=faults, start_s=sc["start"],
                      end_s=sc["end"], realistic=True, ambient_offset_c=sc.get("ambient_offset_c", 0.0),
                      load_scale=sc.get("load_scale", 1.0), stops=tuple(sc.get("stops", ())))
    return SiteNoise(sim, profile="factory", seed=sc["site_seed"])


def run(sc: dict, out_root: str, clip_every_s: float) -> str:
    from effente.simulate_conveyor import ConveyorSim, Fault
    from effente.site_noise import SiteNoise
    from effente.history import History
    from device.intake.extract import feature_stream
    from device.intake.recorder import Recorder
    folder = Path(out_root) / sc["name"]
    if (folder / "labels.json").exists():
        return f"{sc['name']}: already there"
    t_start = time.time()
    faults = [Fault(k, a, b, f"{k} from {a:.0f} s", sev) for k, a, b, sev in sc["faults"]]
    sim = ConveyorSim(days=sc["days"], seed=sc["seed"], week=sc.get("week", 0), faults=faults, start_s=sc["start"],
                      end_s=sc["end"], realistic=True, ambient_offset_c=sc.get("ambient_offset_c", 0.0),
                      load_scale=sc.get("load_scale", 1.0), stops=tuple(sc.get("stops", ())))
    src = SiteNoise(sim, profile="factory", seed=sc["site_seed"])
    hist = History()
    rec = Recorder(folder, lambda: src.info, src.sensors, label=sc["name"], hop_s=1.0, snippet_every_s=clip_every_s,
                   history=hist, min_free_gb=5.0)
    rows, last = 0, time.time()
    for t0, x in feature_stream(src, hop_s=1.0, tap=rec.on_packet, history=hist):
        rec.on_window(t0, x)
        rows += 1
        if time.time() - last > 60:
            last = time.time()
            (folder / "progress.txt").write_text(f"{(t0 - sc['start']) / (sc['end'] - sc['start']):.3f}\n")
    rec.close()
    minutes = np.arange(sc["start"], sc["end"], 60.0)
    labels = {"scenario": {k: v for k, v in sc.items() if k != "faults"},
              "faults": [{"kind": k, "start_s": a, "end_s": b, "severity": sev} for k, a, b, sev in sc["faults"]],
              "minute_t0": minutes.tolist(),
              "label": [sim.label_at(m + 30) for m in minutes],
              "running": [bool(sim.running[int(m + 30)]) for m in minutes],
              "nuisance": minute_nuisances(src.events, minutes)}
    (folder / "labels.json").write_text(json.dumps(labels))
    (folder / "progress.txt").write_text("1.000\n")
    return f"{sc['name']}: {rows} rows in {(time.time() - t_start) / 60:.1f} min"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--clip-every-s", type=float, default=3600.0)
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--only", help="comma-separated run names")
    args = ap.parse_args(argv)
    todo = plan = scenarios(args.smoke)
    if args.only:
        keep = set(args.only.split(","))
        todo = [s for s in todo if s["name"] in keep]
    todo.sort(key=lambda s: -(s["end"] - s["start"]))  # the long chains first
    Path(args.out).mkdir(parents=True, exist_ok=True)
    (Path(args.out) / "scenarios.json").write_text(json.dumps(plan, indent=1))  # the whole plan, even with --only
    print(f"{len(todo)} runs, {sum(s['end'] - s['start'] for s in todo) / DAY:.0f} simulated days", flush=True)
    failed = 0
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futs = {pool.submit(run, s, args.out, args.clip_every_s): s["name"] for s in todo}
        for f in as_completed(futs):
            try:
                print(f.result(), flush=True)
            except Exception:  # noqa: BLE001
                failed += 1
                print(f"{futs[f]}: FAILED\n{traceback.format_exc()}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
