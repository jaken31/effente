"""Shared by the validation checks: simulated sessions, the device's judgement of them, result files.

Everything here is simulated (effente.simulate_conveyor in realistic mode, effente.simulate_server,
both with effente.site_noise). No device recording is read. One feature row a second, as the device
keeps while recording, through the one signal pipeline (device.intake.extract.feature_stream).
The simulators run every sensor; track 3's entries read the accelerometer, the gyro and the IMU's
own temperature only, so a fault that shows only in sound or heat is one today's entries cannot see.

A session is one stay of the device on one machine: it starts with a fresh history, like a device
just snapped on. The first CALIB_S seconds are the calibration; the rest is watched.
"""

from __future__ import annotations

import os

# One maths thread per process, as training/record/conveyor_eval/generate.py does: the checks run
# several processes and each would otherwise start a thread per core.
for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

import argparse
import hashlib
import json
import multiprocessing
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from effente import features as F
from effente import model_runtime as RT
from training.anomaly.bench import alarm_share, fault_outcome  # noqa: F401  (re-exported for the checks)

from training.validation import track3  # the library and calibration code under test

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
CACHE = HERE / ".cache"
DAY = 86400.0

CALIB_S = 300  # the TRD's starting value: 5 minutes of running
SEEDS = (1, 2, 3, 4, 5)  # one simulated machine per seed
# Which simulated machine stands for each library entry. `server` (fans and a drive in a rack) has no
# entry of its own, so it is what generic_rotating would be picked for.
ENTRY_MACHINE = {"conveyor": "conveyor", "generic_rotating": "server"}
SITE = {"conveyor": "factory", "server": "datacenter"}
SOURCE = {"conveyor": "effente.simulate_conveyor (realistic mode) + effente.site_noise (factory)",
          "server": "effente.simulate_server + effente.site_noise (datacenter)"}
NOT_ALARM = ("normal", "warming_up", "stopped", None)


def sources(more: str = "") -> dict:
    """{entry: the data source of its rows}, for write_result: each entry is tested on one simulator."""
    return {entry: SOURCE[machine] + more for entry, machine in ENTRY_MACHINE.items()}


# ----------------------------------------------------------------- simulated sessions

def start_of(machine: str, seed: int, later: bool = False) -> int:
    """When a session starts, on the simulator's clock: a whole second on Monday between 07:00 and
    20:00 (the conveyor runs 06:00-22:00), picked from the seed. `later` picks another time for a
    second session on the same machine."""
    rng = np.random.default_rng([seed, 77, int(later), sorted(SITE).index(machine)])
    return int(rng.uniform(7 * 3600, 20 * 3600))


def make_source(machine: str, seed: int, start_s: float, end_s: float, faults=(), week: int = 0):
    """The simulated machine with its site's disturbances. faults: (kind, start_s, end_s, severity)."""
    from effente.simulate_conveyor import ConveyorSim, Fault
    from effente.simulate_server import ServerRackSim
    from effente.site_noise import SiteNoise
    fl = [Fault(k, float(a), float(b), f"{k} from {a:.0f} s", float(sev)) for k, a, b, sev in faults]
    days = int(end_s // DAY) + 1
    if machine == "conveyor":
        sim = ConveyorSim(days=days, seed=seed, faults=fl, start_s=start_s, end_s=end_s, week=week, realistic=True)
    elif machine == "server":
        sim = ServerRackSim(days=days, seed=seed, faults=fl, start_s=start_s, end_s=end_s, week=week)
    else:
        raise ValueError(f"no simulator for {machine!r}")
    return SiteNoise(sim, profile=SITE[machine], seed=seed + 1000 * week)


def session(machine: str, seed: int, start_s: int, length_s: int, faults=(), week: int = 0, cache=CACHE):
    """Feature rows of one session, one a second: (t, X). Kept in `cache` (None: not kept), keyed by
    every argument and the feature list, so a rerun with the same seeds reads the same rows."""
    from device.intake.extract import feature_stream
    key = json.dumps([machine, seed, start_s, length_s, [list(f) for f in faults], week, RT._version()])
    path = None if cache is None else Path(cache) / f"{machine}-{hashlib.sha1(key.encode()).hexdigest()[:16]}.npz"
    if path is not None and path.exists():
        with np.load(path) as z:
            return z["t"], z["X"]
    src = make_source(machine, seed, start_s, start_s + length_s, faults, week)
    rows = list(feature_stream(src, hop_s=1.0))
    t, X = np.array([r[0] for r in rows], float), np.array([r[1] for r in rows], np.float32)
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp.npz")
        np.savez_compressed(tmp, t=t, X=X)
        tmp.replace(path)
    return t, X


def cut(t, X, a: float, b: float):
    """Rows with a <= t < b."""
    keep = (t >= a) & (t < b)
    return t[keep], X[keep]


def library() -> dict:
    """The library entries, from the library code under test: {entry name: model file dict}."""
    return track3.library()


# ----------------------------------------------------------------- the device's judgement

def watch(model: dict, t, X) -> dict:
    """Run the device runtime (effente.model_runtime.Watcher) over rows. One item per judged period:
    its time, whether it alarms, what the screen and the log would say, and the reading that moved most."""
    w = RT.Watcher(RT.Model(model))
    ts, alarm, diagnosis, this, symptom, moved = [], [], [], [], [], []
    last = None
    for ti, x in zip(t, X):
        st = w.predict_at(float(ti), x)
        if st.get("minute") is None or st["minute"] == last:
            continue
        last = st["minute"]
        ts.append(float(last))
        alarm.append(st["diagnosis"] not in NOT_ALARM)
        diagnosis.append(st["diagnosis"])
        this.append(st.get("diagnosis_this_minute"))
        symptom.append(st.get("symptom"))
        moved.append(next(iter(st.get("features") or {}), None))
    return {"t": np.array(ts), "alarm": np.array(alarm, bool), "diagnosis": diagnosis,
            "diagnosis_this_period": this, "symptom": symptom, "moved_most": moved}


def calibrate(entry: dict | None, t, X, seed: int = 0, length_s: int = CALIB_S):
    """Calibrate on the last `length_s` seconds of the session's first CALIB_S: (model, report), or
    (None, reason) if the calibration code refuses the data."""
    ct, cX = cut(t, X, t[0] + CALIB_S - length_s, t[0] + CALIB_S)
    try:
        return track3.calibrate(entry, ct, cX, list(F.FEATURE_NAMES), seed=seed)
    except ValueError as e:
        return None, str(e)


# ----------------------------------------------------------------- running and reporting

def pmap(fn, items, workers: int):
    items = list(items)
    if workers <= 1 or len(items) <= 1:
        return [fn(i) for i in items]
    # spawn, not fork: a forked worker would keep the parent's maths threads, one per core each
    with ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn")) as pool:
        return list(pool.map(fn, items))


def share(flags) -> float | None:
    flags = list(flags)
    return round(sum(bool(f) for f in flags) / len(flags), 4) if flags else None


def median(values) -> float | None:
    values = [v for v in values if v is not None]
    return round(float(np.median(values)), 4) if values else None


def spread(values) -> str:
    """Lowest and highest of per-seed values, for the report's range column."""
    values = [v for v in values if v is not None]
    return f"{min(values):.4g} to {max(values):.4g}" if values else ""


def args_for(doc: str, argv=None, extra=None):
    ap = argparse.ArgumentParser(description=doc, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seeds", default=",".join(map(str, SEEDS)), help="comma-separated machine seeds")
    ap.add_argument("--workers", type=int, default=max(1, min(8, (os.cpu_count() or 2) // 2)))
    ap.add_argument("--out", help="result file (default: results/<check>.json in this folder)")
    if extra:
        extra(ap)
    args = ap.parse_args(argv)
    args.seeds = [int(s) for s in str(args.seeds).split(",") if s.strip()]
    clash = sorted(set(args.seeds) & {s for seeds in track3.TRAINING_SEEDS.values() for s in seeds})
    if clash:
        ap.error(f"seeds {clash} are simulated machines the library entries were built from")
    return args


def command(module: str, args, more: str = "") -> str:
    return f"python3 -m training.validation.{module} --seeds {','.join(map(str, args.seeds))}{more}"


def write_result(check: str, out, rows: list[dict], *, command: str, seeds, data_source, label: str,
                 uses_library: bool, detail=None, status: str = "ok", reason: str = "", notes=()) -> dict:
    """Write one check's result file and print its one summary line. Each row is one line of
    REPORT.md: {"entry", "metric", "value", "n", "range", "standin"} (the last three optional; a
    row's own "standin" text marks a number that rests on a placeholder). `uses_library`: the check
    runs the library and calibration code, so its rows are stand-in rows if that code is a stand-in.
    `data_source`: one text for every row, or {entry: text}. `notes`: what the check could not
    measure, for the report's "Not run" list."""
    if isinstance(data_source, dict):
        rows = [dict(r, data_source=data_source[r["entry"]]) for r in rows]
        data_source = "; ".join(dict.fromkeys(data_source.values()))
    result = {"check": check, "status": status, "reason": reason, "command": command, "seeds": list(seeds),
              "data_source": data_source, "label": label, "notes": list(notes),
              "standin": track3.NAME if uses_library and track3.IS_STANDIN else None,
              "library_code": track3.NAME if uses_library else None, "features_version": RT._version(),
              "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "rows": rows, "detail": detail or []}
    path = Path(out) if out else RESULTS / f"{check}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=1, default=_plain) + "\n")
    if status != "ok":
        print(f"{check}: {status}: {reason}", flush=True)
    else:
        print(f"{check}: " + "; ".join(f"{r['entry']} {r['metric']} = {r['value']}" for r in rows[:4])
              + (f"; ... ({len(rows)} rows in {path.name})" if len(rows) > 4 else ""), flush=True)
    return result


def _plain(v):
    if isinstance(v, np.generic):
        return v.item()
    if isinstance(v, np.ndarray):
        return v.tolist()
    raise TypeError(type(v).__name__)
