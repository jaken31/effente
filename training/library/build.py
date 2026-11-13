#!/usr/bin/env python3
"""Build model library entries: entries/<entry>/<version>/model.json and manifest.json.

    python3 -m training.library.build                     # every entry, from the simulators
    python3 -m training.library.build conveyor --recording data/real/conveyor/session-01 --status demo

An entry is a pretrained normal model for one machine type. The device later calibrates it to the
machine in front of it in minutes: it keeps the entry's network and refits each reading's median
and spread. So the network only ever sees how far each reading is from THAT machine's normal, in
that machine's spreads, and it is trained on the same thing: every source is standardised with its
own median and spread first, and only then pooled (fit_entry).

Everything built from the simulators is fake data. Nothing here has been checked on a real
machine (unverified). The builder writes no accuracy number it did not compute.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from effente import fault_types as FT
from effente import features as F
from effente import model_runtime as RT
from effente import trainer as TR
from effente.model_input import Prep, _transform_raw, load_recording
from training.library import simulators as SIM
from training.library.catalog import MANIFEST

ENTRIES_DIR = Path(__file__).resolve().parent / "entries"
PIPELINE = "rotating-v1"
KINDS = ("simulated", "device")
STATUSES = ("demo", "unverified", "verified")
CALIB_SHARE = 0.15  # the trainer's held-out share: the last part of each source, by time
SMOOTH = 5  # the trainer's persistence
MIN_S = 300  # calibration length the entries are built for, seconds
HOURS = 3.0  # simulated running time per source

# An entry may use only readings a 5-minute calibration can supply. Two parts to that:
#   - It is built from one-second rows (PERIOD_S = 1), the rows a 5-minute session has. A model
#     built on per-minute medians would be fed 5 rows.
#   - Some readings cannot be refitted from 5 minutes: the time of day barely moves, the trends
#     need minutes to hours of history, and the IMU's temperature is still warming up with the
#     device. They are NOT cut out of the entry. They stay in the column list wherever Prep keeps
#     them, so an entry has exactly the layout the trainer gives (a one-source entry equals
#     train_model, which the tests check) and nothing about the model file is special. They are
#     named in calibration.hold instead, and calibration decides what to do with a held reading.
PERIOD_S = 1
HOLD = ("time_", "trend_", "imu_temp_c")  # name prefixes, or a whole name
WIDEN_WINDOW = 300  # rows: one calibration's worth
WIDEN_RANGE = (1.0, 5.0)


def is_held(column: str) -> bool:
    return column.startswith(HOLD)


# ----------------------------------------------------------------- rows

def prepare(t, X):
    """A source's rows as the trainer takes them: running rows only, one row a second. (t, X)."""
    X = np.asarray(X, float)
    if X.ndim != 2 or X.shape[1] != F.N_FEATURES:
        raise ValueError(f"rows must have the current {F.N_FEATURES} features (effente.trainer.to_current maps older ones)")
    t = np.asarray(t, float)
    run = TR.running_rows(X, list(F.FEATURE_NAMES))
    return TR.summarise(t[run], X[run], PERIOD_S)


def fit_rows(t, calib_share: float = CALIB_SHARE) -> np.ndarray:
    """The trainer's split by time: True for the rows learned from, False for the held-out end."""
    return t < t[0] + (t[-1] - t[0]) * (1 - calib_share)


def _spread(T: np.ndarray) -> np.ndarray:
    """The spread of each column as Prep.fit takes it: IQR / 1.349, else the standard deviation, else 1."""
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        q1, q3 = np.nanpercentile(T, [25, 75], 0)
        std = np.nanstd(T, 0)
    spread = (q3 - q1) / 1.349
    return np.where(spread > 1e-9, spread, np.where(std > 1e-9, std, 1.0))


def _narrowed(p: Prep, columns) -> Prep:
    """The same preparation for fewer columns (the statistics are Prep.fit's own)."""
    at = [p.columns.index(c) for c in columns]
    return Prep(columns, p.centre[at], p.scale[at], p.axis_col, p.missing_cols, p.names_in, p.features_version)


# ----------------------------------------------------------------- the entry

def fit_entry(sources, entry: str = "entry", version: str = "0.0.0", smooth: int = SMOOTH,
              calib_share: float = CALIB_SHARE):
    """Fit one library entry from several machines. Returns (entry model dict, info).

    sources: [(name, kind, t, X)], kind "simulated" or "device", X one-a-second feature rows on the
    current feature list. Each source is split by time (the last calib_share held out). Columns are
    those every source keeps under Prep.fit. Each source is standardised with its own median and
    spread, the standardised fit rows are stacked, and ONE PCA is fitted to them. The threshold comes
    from every source's held-out rows. With one source this is train_model(..., normal_model="pca").

    info: columns, patterns_kept, heldout_score_median and heldout_score_max (raw scores of every
    held-out row), and per source its name, kind, row counts, Prep and held-out scores."""
    names = list(F.FEATURE_NAMES)
    rows = []
    for name, kind, t, X in sources:
        if kind not in KINDS:
            raise ValueError(f"{name}: kind must be one of {KINDS}")
        t, X = prepare(t, X)
        if len(t) < TR.MIN_ROWS:
            raise ValueError(f"{name}: too little recorded, {len(t)} running rows; an entry needs at least "
                             f"{TR.MIN_ROWS} from every source")
        fit = fit_rows(t, calib_share)
        rows.append((name, kind, t, X, fit, Prep.fit(X[fit], names, t[fit], running_only=False)))
    if not rows:
        raise ValueError("an entry needs at least one source")
    columns = [c for c in rows[0][5].columns if all(c in r[5].columns for r in rows)]
    preps = [_narrowed(r[5], columns) for r in rows]
    Z_fit = np.vstack([p.transform(X[fit]) for p, (_, _, _, X, fit, _) in zip(preps, rows)])

    ae, kept = TR.fit_pca(np.nan_to_num(Z_fit))
    acts = ["linear", "linear"]
    layers = [RT.quantize(W, b, a) for (W, b), a in zip(ae, acts)]
    net = RT.Net(layers)
    hs = np.std(net.hidden(Z_fit, layer=0), 0)
    r_fit = Z_fit - net(Z_fit)
    p0 = preps[0]
    neutral = Prep(columns, np.zeros(len(columns)), np.ones(len(columns)), p0.axis_col, p0.missing_cols, names,
                   p0.features_version)  # the device refits these: an entry carries no machine's levels
    d = {"format": RT.FORMAT, "features_version": RT._version(),
         "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
         "trained_on": f"library entry {entry} {version}: {len(rows)} source{'s' if len(rows) != 1 else ''}",
         "prep": neutral.to_dict(), "period_s": PERIOD_S, "smooth_minutes": smooth, "threshold": 1.0,
         "normal_model": "pca", "autoencoder": layers,
         "pca_score": {"pattern_scale": [float(v) for v in np.where(hs > 1e-6, hs, 1e-6)],
                       "residual_ref": float(max((r_fit ** 2).mean(), 1e-9))}}

    # Every source through the network with its own preparation, as train_model scores its one.
    scored = [RT.Model(dict(d, prep=p.to_dict())).score(X) for p, (_, _, _, X, _, _) in zip(preps, rows)]
    scale = np.std(np.vstack([r[fit] for (_, r, _), (_, _, _, _, fit, _) in zip(scored, rows)]), 0)
    d["residual_scale"] = [float(v) for v in np.where(scale > 1e-4, scale, 1e-4)]
    held = [s[~fit] for (s, _, _), (_, _, _, _, fit, _) in zip(scored, rows)]
    d["threshold"] = TR.gamma_threshold(np.concatenate([TR._causal_median(s, smooth) for s in held]),
                                        7 * 86400 / PERIOD_S)
    d["fault_types"] = [{"name": ft.name, "text": ft.text, "sensing": ft.sensing} for ft in FT.TYPES]
    d["feature_table"] = [{"name": c, "text": F.FEATURE_TEXT.get(c, ""), "unit": F.FEATURE_KIND.get(c, "")}
                          for c in columns]
    all_held = np.concatenate(held)
    d["training_report"] = {"period_s": PERIOD_S, "sources": len(rows),
                            "rows_learned": int(sum(int(r[4].sum()) for r in rows)),
                            "rows_calibration": int(len(all_held)), "inputs": neutral.n_out,
                            "features_kept": len(columns), "normal_model": "pca", "patterns_kept": int(kept),
                            "threshold": round(d["threshold"], 4),
                            "heldout_score_median": round(float(np.median(all_held)), 4),
                            "heldout_score_max": round(float(all_held.max()), 4)}
    d.update(library_entry=entry, entry_version=version, pipeline=PIPELINE, source="entry")
    d["calibration"] = {"widen": [], "hold": list(HOLD), "min_s": MIN_S}
    d["calibration"]["widen"] = [float(v) for v in widen_factors(d, sources)]
    info = {"columns": columns, "patterns_kept": int(kept),
            "heldout_score_median": float(np.median(all_held)), "heldout_score_max": float(all_held.max()),
            "sources": [{"name": name, "kind": kind, "rows_fit": int(fit.sum()), "rows_heldout": int((~fit).sum()),
                         "prep": p, "heldout_scores": s}
                        for (name, kind, _, _, fit, _), p, s in zip(rows, preps, held)]}
    return d, info


def widen_factors(entry: dict, sources, window: int = WIDEN_WINDOW) -> np.ndarray:
    """Per column of the entry: how much wider a reading's spread is over a whole source than within
    one calibration-length window. The median, over every `window`-row window of every source, of
    (spread over the whole source / spread within the window), kept within 1..5.

    A 5-minute calibration sees too narrow a spread (the load, the oil and the room barely change
    in 5 minutes), so the device multiplies the spread it refits by this."""
    columns = entry["prep"]["columns"]
    at = [F.INDEX[c] for c in columns]
    ratios = []
    for _, _, t, X in sources:
        _, X = prepare(t, X)
        T = _transform_raw(X[:, at], columns)
        whole = _spread(T)
        for a in range(0, len(T) - window + 1, window):
            ratios.append(whole / _spread(T[a:a + window]))
    if not ratios:
        return np.ones(len(columns))
    r = np.nanmedian(np.array(ratios), 0)
    return np.clip(np.where(np.isfinite(r), r, 1.0), *WIDEN_RANGE)


# ----------------------------------------------------------------- what the committed entries are

@dataclass(frozen=True)
class EntrySpec:
    entry: str
    version: str
    title: str
    status: str
    machines: tuple  # training.library.simulators machines
    sensors: dict
    mount: str
    profile: dict
    notes: tuple = ()


_SENSORS = {"required": ["accelerometer", "gyroscope"], "optional": ["imu_temperature"]}
_COMMON_NOTES = (
    "Built from simulated machines only. Nothing here has been checked on a real machine.",
    "Accelerometer, gyro and IMU readings only: the simulators ran with the microphone, thermal camera and room "
    "sensor absent, because that is what the device has today.",
    "No fault_classifier in the model file. The classifier is trained per machine today, and retraining it in "
    "calibration is not designed yet.",
    "No baseline in the model file: the outside-normal levels belong to one machine.",
    "Readings named in the model file's calibration.hold (time of day, trends, IMU temperature) cannot be "
    "refitted from a 5-minute calibration.",
)
SPECS = {s.entry: s for s in (
    EntrySpec("conveyor", "0.1.0", "Belt conveyor drive", "unverified", SIM.CONVEYORS, _SENSORS,
              "rigid, on the drive-end bearing housing (as simulated)",
              {"machine_type": "conveyor", "drive": "gearbox", "speed": "fixed"},
              _COMMON_NOTES + ("To be rebuilt with real recordings of the demo conveyor and relabelled demo.",)),
    EntrySpec("generic_rotating", "0.1.0", "Generic rotating machine", "unverified",
              SIM.CONVEYORS + (SIM.SERVER_RACK,), {"required": ["accelerometer", "gyroscope"], "optional": []},
              "rigid, on the machine's housing or frame (as simulated)",
              {"machine_type": "other", "drive": None, "speed": None},
              _COMMON_NOTES + (
                  "A research pass on the simulators found that a conveyor entry calibrated on the server rack "
                  "raised no false alarms and caught none of five faults. So using an entry on another type of "
                  "machine is unproven.",)),
)}


def manifest_for(spec: EntrySpec, model: dict, info: dict, model_bytes: bytes, described: dict, status: str) -> dict:
    """The entry's manifest. Numbers that were not measured stay null."""
    median, threshold = info["heldout_score_median"], float(model["threshold"])
    seconds = sum(s["rows_fit"] + s["rows_heldout"] for s in info["sources"])
    notes = list(spec.notes) + [
        f"Built from {seconds} seconds of running in all ({seconds / 3600:.1f} hours over {len(info['sources'])} "
        f"sources); the last 15 percent of each source was held out for the threshold.",
        f"The threshold ({threshold:.4g}) is {threshold / median:.1f} times the median held-out score ({median:.4g}). "
        f"It is never below the highest held-out score, which site disturbances push up."]
    return {
        "manifest": MANIFEST, "entry": spec.entry, "version": spec.version, "title": spec.title, "status": status,
        "use": "watch", "pipeline": PIPELINE, "features_version": model["features_version"],
        "sensors": spec.sensors, "mount": spec.mount, "profile": spec.profile,
        "fault_types": [ft["name"] for ft in model["fault_types"]],
        "calibration": {"unit": "seconds", "min": MIN_S},
        "fit_check": {"entry_heldout_score_median": median, "entry_threshold": threshold, "rule": "open"},
        "trained_on": [{"name": s["name"], "kind": s["kind"], "description": described.get(s["name"], ""),
                        "running_seconds": s["rows_fit"] + s["rows_heldout"],
                        "heldout_score_median": float(np.median(s["heldout_scores"]))} for s in info["sources"]],
        "scorecard": {"measured_on_device": False, "false_alarms_per_week": None, "faults": []},
        "model_file": "model.json", "model_sha1": hashlib.sha1(model_bytes).hexdigest(), "notes": notes,
    }


def _simulate(job):
    machine, hours, seed = job
    return SIM.simulated_rows(machine, hours, seed)


def build(spec: EntrySpec, out_dir=ENTRIES_DIR, hours: float = HOURS, seed: int = 0, recordings=(),
          status: str | None = None, jobs: int | None = None) -> dict:
    """Build one entry into out_dir/<entry>/<version>/. recordings: learn-mode folders recorded with
    the device, added as extra sources. Returns the model, manifest, info and the two paths."""
    status = status or spec.status
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    if status == "verified":
        raise ValueError("verified needs a scorecard measured on device data; this builder measures none")
    work = [(m, hours, seed) for m in spec.machines]
    if len(work) > 1 and (jobs is None or jobs > 1):
        with ProcessPoolExecutor(max_workers=jobs) as ex:  # each machine has its own seed: order-free
            simulated = list(ex.map(_simulate, work))
    else:
        simulated = [_simulate(w) for w in work]
    sources = [(m.name, "simulated", t, X) for m, (t, X) in zip(spec.machines, simulated)]
    described = {m.name: m.description for m in spec.machines}
    for folder in recordings:
        X, t, names = load_recording(folder)
        name = Path(folder).resolve().name
        if name in described:
            raise ValueError(f"two sources are called {name}")
        sources.append((name, "device", t, TR.to_current(X, names)))
        described[name] = f"device recording, learn-mode folder {name}"
    model, info = fit_entry(sources, spec.entry, spec.version)
    folder = Path(out_dir) / spec.entry / spec.version
    folder.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(model, separators=(",", ":")).encode()
    manifest = manifest_for(spec, model, info, raw, described, status)
    (folder / "model.json").write_bytes(raw)
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return {"model": model, "manifest": manifest, "info": info, "model_path": folder / "model.json",
            "manifest_path": folder / "manifest.json"}


def describe(built: dict) -> str:
    m, info, d = built["manifest"], built["info"], built["model"]
    median, threshold = info["heldout_score_median"], d["threshold"]
    widen = np.array(d["calibration"]["widen"])
    lines = [f"{m['entry']} {m['version']} ({m['status']}): {built['model_path']}",
             f"  model.json {built['model_path'].stat().st_size} bytes; {len(info['columns'])} columns "
             f"({sum(is_held(c) for c in info['columns'])} held), {d['training_report']['inputs']} inputs, "
             f"{info['patterns_kept']} patterns",
             f"  threshold {threshold:.4f}; held-out scores: median {median:.4f}, highest "
             f"{info['heldout_score_max']:.4f}; threshold / median {threshold / median:.1f}",
             f"  widen factors: median {np.median(widen):.2f}, highest {widen.max():.2f}"]
    for s in info["sources"]:
        lines.append(f"  {s['name']} ({s['kind']}): {s['rows_fit']} rows learned, {s['rows_heldout']} held out, "
                     f"held-out median {np.median(s['heldout_scores']):.4f}, highest {s['heldout_scores'].max():.4f}")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("entries", nargs="*", help=f"entries to build (default all: {', '.join(SPECS)})")
    ap.add_argument("--hours", type=float, default=HOURS, help="simulated running time per simulated source")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default=str(ENTRIES_DIR), help="the library folder")
    ap.add_argument("--recording", action="append", default=[], metavar="FOLDER",
                    help="a learn-mode folder recorded with the device, added as a source (repeat for more)")
    ap.add_argument("--status", choices=STATUSES[:2], help="the status label to write (default: the entry's own)")
    ap.add_argument("--jobs", type=int, help="processes for simulating (default: one per core)")
    args = ap.parse_args(argv)
    for name in args.entries:
        if name not in SPECS:
            ap.error(f"no entry {name!r}; there are {', '.join(SPECS)}")
    if args.recording and len(args.entries) != 1:
        ap.error("--recording needs exactly one entry named")
    for name in args.entries or list(SPECS):
        print(describe(build(SPECS[name], args.out, args.hours, args.seed, args.recording, args.status, args.jobs)),
              flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
