#!/usr/bin/env python3
"""Real bearing faults (University of Ottawa, UODS-VAFDC) -> does the device catch them and name them?

The dataset: 20 ball bearings at constant load and speed (about 1,700-1,820 rpm). Each was recorded
healthy (H_k_0), then with a developing fault (X_k_1) and a full fault (X_k_2): inner race (bearings
1-5), outer race (6-10), ball (11-15) and cage (16-20). Each file is 10 s at 42 kHz: an accelerometer,
a microphone, and the speed and load.

What this does, for each bearing, the way the device would:
  1. The accelerometer as OUR device would see it (as in ims.py): resampled to 1 kHz, the MPU's
     low-pass, its noise and gravity added, clipped at its +-8 g range. Only vibration: the device has
     no microphone yet. The accelerometer's unit is taken to be m/s^2 (unverified: the files don't
     say). Any steady offset is removed first (a piezo sensor has none; some files have one).
  2. Learn this bearing's normal from the first 6 s of its healthy file (one 1 s window every 20 ms:
     301 heavily overlapping rows, the trainer's minimum), with effente.trainer, the device's trainer.
  3. Judge, with effente.model_runtime.Watcher as the device does (one row a "second", the median of
     the last 5 scores): the last 2 s of the healthy file (kept apart: no window overlaps the
     learning), and the developing and full fault files.
  4. Score: caught (at least half the judged rows say something is wrong), what was named (the
     right name is bearing_defect_like: our fault types don't split inner race, outer race, ball and
     cage; scored on the name it gave most often), false alarms on the held-out healthy rows. At three sensitivities (margins on the model's
     line). Also how often the outside-normal and universal checks (effente.safety) were over their line.

    python3 -m training.record.datasets.ottawa "data/real/ottawa-uods/unpacked/University of Ottawa ..." \\
        --out data/real/ottawa-uods/results.json

Limits: 10 s a bearing is very little to learn a normal from (the device learns for 5 minutes or
more), and the held-out healthy part is only 2 s, so false-alarm rates here are rough.
Source: University of Ottawa, "UODS-VAFDC" (Mendeley Data, 2023). Cite the source.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np
from scipy import io as sio
from scipy import signal

from effente import fault_types as FT
from effente import features as F
from effente import model_runtime as RT
from effente import safety as SF
from effente.trainer import train_model
from training.record.datasets.ims import MPU_NOISE_G, RATE_OUT

RATE_IN = 42000.0
KINDS = {"I": "inner race", "O": "outer race", "B": "ball", "C": "cage"}
RIGHT = "bearing_defect_like"
# What the device now says (effente.fault_types.SYMPTOM_TEXT): for a bearing fault, any of these describes
# what is going on; "outside normal" is vague but not wrong; anything else would mislead.
ACCURATE = {"unusual_vibration", "vibration_too_high", "knocking_or_jolts", "vibration_unsteady"}
VAGUE = {"outside_normal"}
RELATED = {"impacts", "repeating_knock", "grinding_or_hiss", "vibration_too_high", "unsteady_vibration_or_sound"}
MARGINS = {"0.25 (less sensitive)": 2.0, "0.5 (default)": 1.0, "0.75 (more sensitive)": 0.5}
LEARN_S, HELD_FROM_S = 6.0, 7.0  # learn from windows starting at 0-6 s; held-out healthy windows start at 7-9 s


def kind_of(bearing: int) -> str:
    return "IOBC"[(bearing - 1) // 5]


def as_device(x: np.ndarray, rng) -> np.ndarray:
    """One 42 kHz channel in m/s^2 -> 3-axis 1 kHz samples in g as our MPU would record them."""
    y = (x - x.mean()) / 9.81
    y = signal.resample_poly(y, 1, 42)  # 42 kHz -> 1 kHz, with an anti-aliasing low-pass
    y = signal.sosfilt(signal.butter(4, 218, fs=RATE_OUT, output="sos"), y)  # the MPU-6500's low-pass
    y = signal.sosfilt(signal.butter(2, 2.0, "highpass", fs=RATE_OUT, output="sos"), y)  # no drift
    a = rng.normal(0, MPU_NOISE_G, (len(y), 3))
    a[:, 0] += y
    a[:, 2] += 1.0
    return np.clip(a, -8.0, 8.0)  # the MPU's range: a bigger jolt reads as 8 g


def windows(a: np.ndarray, start_s: float, end_s: float, hop_s: float) -> np.ndarray:
    """Feature rows of the 1 s windows starting at start_s, start_s + hop_s, ... up to end_s."""
    n = int(RATE_OUT)
    rows = []
    for s in np.arange(start_s, end_s + 1e-9, hop_s):
        i = int(round(s * RATE_OUT))
        if i + n > len(a):
            break
        w = F.Window(t0=0.0, accel_g=a[i:i + n], accel_t=np.arange(n) / RATE_OUT, accel_rate_hz=RATE_OUT,
                     accel_range_g=8.0)
        rows.append(F.extract(w))
    return np.array(rows)


def load(folder: Path, name: str, rng) -> np.ndarray:
    hits = list(folder.glob(f"*/{name}.mat"))
    if not hits:
        raise FileNotFoundError(name)
    m = sio.loadmat(hits[0])
    return as_device(np.asarray(m[name], float)[:, 0], rng)


def judge(model: RT.Model, X: np.ndarray, margin: float) -> list[dict]:
    """The Watcher's state after each row, one row a "second" as the device judges a short recording."""
    w = RT.Watcher(model, margin=margin)
    states = []
    for k, x in enumerate(X):
        w.predict_at(float(k), x)
        states.append(dict(w.state))
    w.predict_at(float(len(X)), X[-1])  # closes the last period
    states.append(dict(w.state))
    return [s for s in states[model.smooth:] if s.get("diagnosis") != "warming_up"]  # after the warm-up


def summary(states: list[dict]) -> dict:
    wrong = [s for s in states if s.get("diagnosis") not in ("normal", "warming_up", "stopped")]
    share = len(wrong) / max(len(states), 1)
    seen = Counter(s["diagnosis"] for s in wrong)
    named = seen.most_common(1)[0][0] if wrong else "normal"  # the name it gave most often
    told = Counter(s.get("symptom") for s in wrong)  # what the screen says (effente.fault_types.describe)
    said = told.most_common(1)[0][0] if wrong else None
    return {"rows": len(states), "wrong_share": round(share, 3), "caught": share >= 0.5, "named": named,
            "said": said, "said_seen": dict(told),
            "named_at_end": states[-1].get("diagnosis") if states else None,
            "names_seen": dict(Counter(s["diagnosis"] for s in wrong))}


def safety_share(X: np.ndarray, baseline: dict) -> dict:
    """Per check, the share of rows over its line (no 10 s hold: the files are too short for it)."""
    out = Counter()
    m = SF.margins(SF.DEFAULT_SENSITIVITY)
    for x in X:
        r = SF.readings(x)
        for name, (over, _) in {**SF._universal(r), **SF._relative(r, baseline.get("levels", {}), m),
                                "sensor_problem": SF._sensor(r)}.items():
            out[name] += bool(over)
    return {k: round(v / len(X), 3) for k, v in sorted(out.items()) if v}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", help="the unpacked UODS-VAFDC folder (holding 1_Healthy, 2_Inner_Race_Faults, ...)")
    ap.add_argument("--out", required=True, help="results JSON")
    ap.add_argument("--bearings", default="1-20")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)
    lo, hi = (int(v) for v in args.bearings.split("-"))
    folder = Path(args.folder)
    results = []
    for b in range(lo, hi + 1):
        t0 = time.perf_counter()
        rng = np.random.default_rng(args.seed + b)
        k = kind_of(b)
        healthy = load(folder, f"H_{b}_0", rng)
        X_learn = windows(healthy, 0.0, LEARN_S, 0.02)
        X_held = windows(healthy, HELD_FROM_S, 9.0, 0.1)
        d, report = train_model(np.arange(len(X_learn), dtype=float), X_learn, list(F.FEATURE_NAMES), period=1,
                                source=f"ottawa H_{b}_0", seed=args.seed)
        model = RT.Model(d)
        res = {"bearing": b, "fault": KINDS[k], "train": {k2: report[k2] for k2 in ("rows_learned", "threshold",
                                                                                     "warnings")}}
        tests = {"healthy (held out)": X_held}
        for level, label in ((1, "developing"), (2, "full")):
            try:
                tests[label] = windows(load(folder, f"{k}_{b}_{level}", rng), 0.0, 9.0, 0.25)
            except FileNotFoundError:
                pass
        for label, X in tests.items():
            res[label] = {"by_sensitivity": {s: summary(judge(model, X, m)) for s, m in MARGINS.items()},
                          "safety_over": safety_share(X, d.get("baseline") or {})}
        res["seconds"] = round(time.perf_counter() - t0, 1)
        results.append(res)
        dflt = "0.5 (default)"
        print(f"bearing {b:2d} ({KINDS[k]:10s}) healthy wrong {res['healthy (held out)']['by_sensitivity'][dflt]['wrong_share']:.2f}"
              + "".join(f" | {lab}: {'caught' if res[lab]['by_sensitivity'][dflt]['caught'] else 'missed'} as "
                        f"{res[lab]['by_sensitivity'][dflt]['named']}" for lab in ("developing", "full") if lab in res),
              flush=True)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps({"source": "UODS-VAFDC", "method": __doc__.split("What this does")[1].split("Limits")[0].strip(),
                                          "results": results}, indent=1))
    print_table(results)
    return 0


def print_said(results: list[dict]) -> None:
    """Scored on what the screen says (the symptom), not the cause: described straight from what moved
    (now), and through the fault type's symptom (before), side by side."""
    for s in MARGINS:
        for how, key in (("described from what moved", "said"), ("through the fault type", None)):
            said = Counter((r[lab]["by_sensitivity"][s]["said"] if key else FT.symptom_of(r[lab]["by_sensitivity"][s]["named"]))
                           for r in results for lab in ("developing", "full")
                           if lab in r and r[lab]["by_sensitivity"][s]["caught"])
            n = sum(said.values())
            acc, vague = sum(said[k] for k in ACCURATE), sum(said[k] for k in VAGUE)
            print(f"said ({how}), sensitivity {s}: of {n} caught, accurate {acc}, vague {vague}, "
                  f"misleading {n - acc - vague}: {dict(said)}")


def print_table(results: list[dict]) -> None:
    print_said(results)
    for lab in ("developing", "full"):
        rows = [r[lab]["safety_over"] for r in results if lab in r]
        hit = sum(any(v >= 0.8 for v in x.values()) for x in rows)
        print(f"safety checks ({lab}): over a line on 80 %+ of rows in {hit}/{len(rows)} files: "
              f"{dict(Counter(k for x in rows for k, v in x.items() if v >= 0.8))}")
    held = [r["healthy (held out)"]["safety_over"] for r in results]
    print(f"safety checks (healthy held out): over a line on 80 %+ of rows in "
          f"{sum(any(v >= 0.8 for v in x.values()) for x in held)}/{len(held)} bearings")
    for s in MARGINS:
        print(f"\nsensitivity {s}")
        for lab in ("developing", "full"):
            rows = [r[lab]["by_sensitivity"][s] for r in results if lab in r]
            caught = [x for x in rows if x["caught"]]
            right = sum(x["named"] == RIGHT for x in caught)
            related = sum(x["named"] in RELATED for x in caught)
            print(f"  {lab:10s}: caught {len(caught)}/{len(rows)}; of those named {RIGHT} {right}, "
                  f"a related name {related}, other {len(caught) - right - related}: "
                  f"{dict(Counter(x['named'] for x in caught if x['named'] != RIGHT and x['named'] not in RELATED))}")
        held = [r["healthy (held out)"]["by_sensitivity"][s] for r in results]
        any_right = sum(RIGHT in x["names_seen"] for r in results for lab in ("developing", "full") if lab in r
                        for x in [r[lab]["by_sensitivity"][s]])
        print(f"  files where {RIGHT} came up at all: {any_right}/{sum(lab in r for r in results for lab in ('developing', 'full'))}")
        print(f"  healthy held out: wrong on {np.mean([x['wrong_share'] for x in held]):.0%} of rows on average; "
              f"a bearing flagged (half its rows) {sum(x['caught'] for x in held)}/{len(held)}")


if __name__ == "__main__":
    sys.exit(main())
