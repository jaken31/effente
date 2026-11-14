#!/usr/bin/env python3
"""The check of the projection on run-to-failure bearing data (contract 9.5). It decides `show_days`.

    cd <repo root>
    <python with numpy> platform/partlog/tests/check_projection_ims.py --data <folder of rows files> [--out result.json]

Not a test: pytest does not collect it, and it needs numpy and data the repo does not hold. It was
run with the track 4 validation checkout's .venv. The data checks the METHOD (a straight line
through the model's score), not our device: the bearings, the sensors and the machine are not ours.

The data. NASA IMS bearing data set (University of Cincinnati; phm-datasets.s3.amazonaws.com/NASA/
4.+Bearings.zip), one 1 s snapshot every 10 minutes per bearing until a bearing failed. Each failed
bearing's channel is converted by track 2's training/record/datasets/ims.py (MPU6050-like, --seed 0)
into the rows files below; --bearing there is the column of the snapshot file:

    test2_bearing1.npz  test 2, bearing 1, outer race   column 1 of 4   (2nd_test)
    test1_bearing3.npz  test 1, bearing 3, inner race   column 5 of 8   (1st_test, bearing 3 x)
    test1_bearing4.npz  test 1, bearing 4, roller       column 7 of 8   (1st_test, bearing 4 x)
    test3_bearing3.npz  test 3, bearing 3, outer race   column 3 of 4   (3rd_test, its folder is 4th_test/txt)

What it does, per run. All of it was fixed before the first run:

1. Learn: the trainer's default model (PCA) on the first 72 hours, smooth 3, as
   training/validation/check_ims.py does (`training.model.train --rows --learn-hours 72 --smooth 3`).
2. Watch: the device runtime (`effente.model_runtime.Watcher`, margin 1.0, the default
   sensitivity's line) scores every snapshot after learning. Its `unknown_score` is what a record carries.
3. The crossing: the first snapshot after learning whose `unknown_score` is above 1.0, the alarm
   line. This is what the projection predicts. How long before the failure (the last snapshot) it
   came is reported too.
4. Visits, as a handheld would make them: one a day, the snapshots of a 2 hour block starting 72 h,
   96 h, 120 h ... into the run. A visit with at least 10 scored snapshots makes a reading: the
   median score, at the visit's first snapshot (contract 9.3). 4 visits a day (every 6 h) is run too,
   for information only: the bar is judged on one a day.
5. At each visit before the crossing, `project` is run on the readings so far, with `now` at the
   visit's last snapshot and `show_days` on. A `rising` point is "inside" when ceil(days from the
   newest reading to the crossing) lies within `reaches.days` (an end past a year counts as no
   limit), and a "late miss" when it is below the first number: the projection said there was more
   time than there was.

The bar for "poor", agreed with Jake before the first run. Over the `rising` points of every run
together, at one visit a day:

    (a) inside at 70 % or more of them,
    (b) late misses at 10 % or fewer of them, and
    (c) at least 10 of them.

All three hold: good, and `show_days` may be true. Any one fails: poor, and `show_days` stays false.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import math
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

TESTS = Path(__file__).resolve().parent
PARTLOG = TESTS.parent
REPO = TESTS.parents[2]
for path in (str(REPO), str(PARTLOG)):
    if path not in sys.path:
        sys.path.insert(0, path)

import projection as P  # noqa: E402
from effente import model_runtime as RT  # noqa: E402
from effente import trainer as TR  # noqa: E402
from training.model import train as train_cli  # noqa: E402

RUNS = (
    ("test2_bearing1", "test 2, bearing 1, outer race"),
    ("test1_bearing3", "test 1, bearing 3, inner race"),
    ("test1_bearing4", "test 1, bearing 4, roller element"),
    ("test3_bearing3", "test 3, bearing 3, outer race"),
)
LEARN_HOURS = 72.0
SMOOTH = 3
LINE = 1.0  # effente.safety.margins()["model"] at the default sensitivity
VISIT_HOURS = 2.0
MIN_RECORDS = 10  # contract 9.3
CADENCES = {"one a day": 24.0, "four a day": 6.0}
JUDGED = "one a day"
BAR = {"inside_at_least": 0.70, "late_at_most": 0.10, "rising_points_at_least": 10}


def iso(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat().replace("+00:00", "Z")


def scores_of(rows_path: Path) -> tuple[np.ndarray, np.ndarray, float, float]:
    """Steps 1 and 2: (snapshot times, unknown_score after learning or nan, learning end, failure time)."""
    with np.load(rows_path) as z:
        t, X, names = z["t"].astype(float), z["X"], [str(n) for n in z["feature_names"]]
    with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
        model_path = Path(tmp) / "model.json"
        train_cli.main(["--rows", str(rows_path), "--learn-hours", str(LEARN_HOURS), "--smooth", str(SMOOTH),
                        "--out", str(model_path)])
        model = json.loads(model_path.read_text())
    X = TR.to_current(X, names)
    learned = t.min() + LEARN_HOURS * 3600
    watcher = RT.Watcher(RT.Model(model), margin=LINE)
    period = float(model["period_s"])
    score_at: dict[float, float] = {}
    for ti, x in zip(t[t >= learned], X[t >= learned]):
        state = watcher.predict_at(float(ti), x)
        if state.get("unknown_score") is not None and state.get("minute") is not None:
            score_at[float(state["minute"])] = float(state["unknown_score"])
    # A period is judged when the next one starts: map each period back to its snapshot's time.
    scores = np.full(len(t), np.nan)
    for k, ti in enumerate(t):
        start = math.floor(ti / period) * period
        if ti >= learned and start in score_at:
            scores[k] = score_at[start]
    return t, scores, learned, float(t.max())


def visits(t: np.ndarray, scores: np.ndarray, learned: float, every_hours: float) -> list[dict]:
    """Step 4: the readings a handheld visiting every `every_hours` would have made."""
    readings, start = [], learned
    while start < t.max():
        inside = (t >= start) & (t < start + VISIT_HOURS * 3600) & ~np.isnan(scores)
        if inside.sum() >= MIN_RECORDS:
            readings.append({"start": iso(float(t[inside].min())), "end_s": float(t[inside].max()),
                             "start_s": float(t[inside].min()), "unknown_score": round(float(np.median(scores[inside])), 2),
                             "alarm_line": LINE, "model_version": "ims", "simulated": False})
        start += every_hours * 3600
    return readings


def judge_run(name: str, rows_path: Path) -> dict:
    t, scores, learned, failure = scores_of(rows_path)
    over = np.flatnonzero((scores > LINE) & ~np.isnan(scores))
    crossing = float(t[over[0]]) if len(over) else None
    out = {"run": name, "hours": round((failure - t.min()) / 3600, 1), "learn_hours": LEARN_HOURS,
           "crossing_hours_after_learning": None if crossing is None else round((crossing - learned) / 3600, 1),
           "crossing_hours_before_failure": None if crossing is None else round((failure - crossing) / 3600, 1),
           "cadences": {}}
    for cadence, every in CADENCES.items():
        readings = visits(t, scores, learned, every)
        points = []
        for k, reading in enumerate(readings):
            if crossing is not None and reading["end_s"] >= crossing:
                break
            proj = P.project(readings[: k + 1], None, now=iso(reading["end_s"]), show_days=True)
            point = {"now": iso(reading["end_s"]), "status": proj["status"], "readings": proj["window"]["readings"],
                     "now_score": proj["now_score"], "slope_range": proj["slope_range"],
                     "reaches_days": (proj["reaches"] or {}).get("days"), "actual_days": None, "verdict": ""}
            if crossing is not None:
                point["actual_days"] = round((crossing - reading["start_s"]) / 86400, 2)
            if proj["status"] == P.RISING and crossing is not None:
                first, last = proj["reaches"]["days"]
                actual = math.ceil((crossing - reading["start_s"]) / 86400)
                if first is None or actual < first:
                    point["verdict"] = "late miss"
                elif last is None or actual <= last:
                    point["verdict"] = "inside"
                else:
                    point["verdict"] = "early"  # said less time than there was
            points.append(point)
        out["cadences"][cadence] = {"readings": len(readings), "points": points,
                                    "statuses": _count(p["status"] for p in points),
                                    "verdicts": _count(p["verdict"] for p in points if p["verdict"])}
    return out


def _count(items) -> dict:
    counts: dict[str, int] = {}
    for item in items:
        counts[item] = counts.get(item, 0) + 1
    return counts


def verdict(results: list[dict]) -> dict:
    rising = [p for r in results for p in r["cadences"][JUDGED]["points"] if p["status"] == P.RISING
              and p["verdict"]]
    n = len(rising)
    inside = sum(p["verdict"] == "inside" for p in rising)
    late = sum(p["verdict"] == "late miss" for p in rising)
    checks = {
        "a_inside": n > 0 and inside / n >= BAR["inside_at_least"],
        "b_late": n > 0 and late / n <= BAR["late_at_most"],
        "c_enough": n >= BAR["rising_points_at_least"],
    }
    return {"cadence": JUDGED, "rising_points": n, "inside": inside, "late_misses": late,
            "early": n - inside - late, "checks": checks, "good": all(checks.values()),
            "show_days": all(checks.values())}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", required=True, help="folder holding the four rows files named in RUNS")
    ap.add_argument("--out", default="", help="write the full result here as JSON")
    args = ap.parse_args(argv)
    results = []
    for name, text in RUNS:
        path = Path(args.data) / f"{name}.npz"
        if not path.exists():
            print(f"{name}: {path} is not there, not run", file=sys.stderr)
            continue
        result = judge_run(name, path)
        result["what"] = text
        results.append(result)
        _print_run(result)
    if not results:
        print("No run: no rows files.", file=sys.stderr)
        return 1
    final = verdict(results)
    print(json.dumps(final, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps({"bar": BAR, "verdict": final, "runs": results}, indent=2))
    return 0


def _print_run(r: dict) -> None:
    print(f"\n{r['run']} ({r['what']}): {r['hours']} h, crossing {r['crossing_hours_after_learning']} h after "
          f"learning, {r['crossing_hours_before_failure']} h before failure")
    for cadence, c in r["cadences"].items():
        print(f"  {cadence}: {c['readings']} readings, statuses before crossing {c['statuses']}, {c['verdicts']}")
        if cadence == JUDGED:
            for p in c["points"]:
                print(f"    {p['now']}  {p['status']:<18} score {p['now_score']}  range {p['slope_range']}  "
                      f"reaches {p['reaches_days']}  actual {p['actual_days']} d  {p['verdict']}")


if __name__ == "__main__":
    sys.exit(main())
