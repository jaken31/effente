#!/usr/bin/env python3
"""Check 1. Held-out normal data after calibration: the share of periods that alarm, per entry.

    python3 -m training.validation.check_heldout_normal --seeds 1,2,3,4,5

Per library entry and per seed (one simulated machine each, no fault):
  - calibrate on the first 5 minutes of a session
  - same session: the device's runtime judges the next 30 minutes
  - later session: the same machine in another simulated week at another time of day, with the
    feature history restarted (the device snapped on again), 30 minutes, judged by the same
    calibrated model
An alarm is any judged period whose diagnosis is not normal, warming_up or stopped. The result file
also keeps, per machine, the readings that most often moved most in the alarm periods.

Simulated only. Re-attaching the sensor (a shifted mount) is not simulated: see RERUN.md.
"""

from __future__ import annotations

import sys
from collections import Counter
from functools import partial

from training.validation import common as C

WATCH_S = 1800


def sessions(machine: str, seed: int):
    """(the calibration session with its 30 minutes after, the later session)."""
    s0 = C.start_of(machine, seed)
    s1 = C.start_of(machine, seed, later=True)
    return C.session(machine, seed, s0, C.CALIB_S + WATCH_S), C.session(machine, seed, s1, WATCH_S, week=1)


def judge(model: dict, first, later) -> dict:
    """Alarm shares of the rows after calibration in the first session, and of the later session."""
    t, X = first
    same = C.watch(model, *C.cut(t, X, t[0] + C.CALIB_S, t[-1] + 1))
    again = C.watch(model, *later)
    lead = Counter(m for w in (same, again) for m, a in zip(w["moved_most"], w["alarm"]) if a and m)
    return {"same_session": C.alarm_share(same["alarm"]), "later_session": C.alarm_share(again["alarm"]),
            "moved_most_in_alarm_periods": dict(lead.most_common(5))}


def one(task, lib) -> dict:
    entry, seed = task
    first, later = sessions(C.ENTRY_MACHINE[entry], seed)
    model, report = C.calibrate(lib[entry], *first, seed=seed)
    out = {"entry": entry, "seed": seed, "machine": C.ENTRY_MACHINE[entry]}
    if model is None:
        return dict(out, refused=report)
    return dict(out, period_s=model["period_s"], **judge(model, first, later))


def rows_of(detail) -> list[dict]:
    rows = []
    for entry in dict.fromkeys(d["entry"] for d in detail):
        got = [d for d in detail if d["entry"] == entry and "refused" not in d]
        for key, text in (("same_session", "the 30 min after calibration, same session"),
                          ("later_session", "a later 30 min session, history restarted")):
            n = sum(d[key]["rows"] for d in got)
            rows.append({"entry": entry, "metric": f"share of periods that alarm, {text}",
                         "value": round(sum(d[key]["alarm_rows"] for d in got) / n, 4) if n else None,
                         "n": f"{n} periods of {got[0]['period_s']} s, {len(got)} machines" if got else "0 periods",
                         "range": C.spread([d[key]["share"] for d in got])})
    return rows


def main(argv=None) -> int:
    args = C.args_for(__doc__, argv)
    lib = C.library()
    detail = C.pmap(partial(one, lib=lib), [(e, s) for e in lib for s in args.seeds], args.workers)
    C.write_result("heldout_normal", args.out, rows_of(detail), command=C.command("check_heldout_normal", args),
                   seeds=args.seeds, data_source=C.sources(), label="simulated", uses_library=True,
                   detail=detail)
    return 0


if __name__ == "__main__":
    sys.exit(main())
