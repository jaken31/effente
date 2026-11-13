#!/usr/bin/env python3
"""Check 5. Calibration length: false alarms and missed faults, from 5 minutes down.

    python3 -m training.validation.check_calibration_length --seeds 1,2,3,4,5

The recordings are those of checks 1 and 2, each one long simulated session per seed. Its 5-minute
calibration is cut shorter by keeping only the last N seconds before the watch starts, so what is
watched afterwards is the same rows at every length (LENGTHS_S).

  false alarms   check 1's normal session: share of the 30 minutes after calibration that alarm
  missed faults  check 2's simulated fault sessions (every kind and severity), judged by the model
                 calibrated on their no-fault twin at that length: share with no alarm after the
                 onset. A machine that alarms without a fault misses nothing, so the share of the
                 twins that alarm in the fault window is given for the same length.

A calibration the calibration code refuses is counted as refused and left out of the shares.

Simulated only.
"""

from __future__ import annotations

import sys
from functools import partial

from training.validation import common as C
from training.validation.check_faults import machine_one, warm, warm_tasks
from training.validation.check_heldout_normal import sessions

LENGTHS_S = (300, 240, 180, 120, 60, 30)


def normal_one(task, lib) -> dict:
    entry, seed, length = task
    (t, X), _ = sessions(C.ENTRY_MACHINE[entry], seed)
    out = {"entry": entry, "seed": seed, "what": "normal", "calibration_s": length}
    model, report = C.calibrate(lib[entry], t, X, seed=seed, length_s=length)
    if model is None:
        return dict(out, refused=report)
    watched = C.watch(model, *C.cut(t, X, t[0] + C.CALIB_S, t[-1] + 1))
    return dict(out, period_s=model["period_s"], **C.alarm_share(watched["alarm"]))


def fault_one(task, lib) -> list[dict]:
    entry, seed, length = task
    return [dict(d, what="twin" if d["how"] == "twin" else "fault")
            for d in machine_one((entry, seed), lib, length_s=length, injected=False)]


def rows_of(detail) -> list[dict]:
    rows = []
    for entry in dict.fromkeys(d["entry"] for d in detail):
        for length in sorted({d["calibration_s"] for d in detail}, reverse=True):
            got = [d for d in detail if d["entry"] == entry and d["calibration_s"] == length]
            ok = [d for d in got if "refused" not in d]
            normal = [d for d in ok if d["what"] == "normal"]
            faults = [d for d in ok if d["what"] == "fault"]
            periods = sum(d["rows"] for d in normal)
            if normal:
                rows.append({"entry": entry, "metric": f"{length} s calibration: share of held-out normal periods that alarm",
                             "value": round(sum(d["alarm_rows"] for d in normal) / periods, 4) if periods else None,
                             "n": f"{periods} periods of {normal[0]['period_s']} s, {len(normal)} machines",
                             "range": C.spread([d["share"] for d in normal])})
            if faults:
                rows.append({"entry": entry,
                             "metric": f"{length} s calibration: share of simulated faults with no alarm after the onset",
                             "value": C.share(not d["alarmed"] for d in faults), "n": f"{len(faults)} faults"})
            twins = [d for d in ok if d["what"] == "twin"]
            if twins:
                rows.append({"entry": entry,
                             "metric": f"{length} s calibration: share of the faults' no-fault twins that alarm in the fault window",
                             "value": C.share(d["alarmed"] for d in twins), "n": f"{len(twins)} machines"})
            if len(ok) < len(got):
                rows.append({"entry": entry, "metric": f"{length} s calibration: calibrations the calibration code refused",
                             "value": len(got) - len(ok), "n": f"{len({d['seed'] for d in got})} machines"})
    return rows


def main(argv=None) -> int:
    args = C.args_for(__doc__, argv)
    lib = C.library()
    detail = C.pmap(partial(normal_one, lib=lib), [(e, s, n) for e in lib for s in args.seeds for n in LENGTHS_S], args.workers)
    machines = [(e, s) for e in lib for s in args.seeds]
    C.pmap(warm, warm_tasks(machines), args.workers)
    for part in C.pmap(partial(fault_one, lib=lib), [(e, s, n) for e, s in machines for n in LENGTHS_S], args.workers):
        detail += part
    C.write_result("calibration_length", args.out, rows_of(detail), command=C.command("check_calibration_length", args),
                   seeds=args.seeds, data_source=C.sources(), label="simulated", uses_library=True, detail=detail)
    return 0


if __name__ == "__main__":
    sys.exit(main())
