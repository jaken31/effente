#!/usr/bin/env python3
"""Check 6. Fit check: the share that pass on the right entry and fail on the wrong one.

    python3 -m training.validation.check_fit --seeds 1,2,3,4,5

Per seed, the 5-minute calibration rows of one simulated conveyor and one simulated server rack
(the calibration sessions of check 1) are given to the fit check of every library entry.

  conveyor          right for the conveyors, wrong for the server racks
  generic_rotating  meant for any machine without its own entry: right for the server racks; the
                    conveyors are reported too. No simulated machine is wrong for it.

The pass rule is track 3's (effente.calibration.fit_check): the median score of the held-out
calibration rows on the calibrated model, over the entry's own held-out median, must not be above
effente.calibration.FIT_MAX_RATIO. That limit is a starting value set on the simulators and the TRD
leaves the rule open, so the pass and fail shares say so (training/validation/track3.py, FIT_RULE).
Two numbers are reported as they are and kept per machine in the result file: that ratio, and the
calibration rows' median score on the entry as shipped over the entry's threshold.

Simulated only: two machine types, both from simulators.
"""

from __future__ import annotations

import sys
from functools import partial

from effente import features as F
from training.validation import common as C
from training.validation.check_heldout_normal import sessions

RIGHT = {("conveyor", "conveyor"): True, ("conveyor", "server"): False,
         ("generic_rotating", "server"): True, ("generic_rotating", "conveyor"): None}  # (entry, machine)


def one(task, lib) -> list[dict]:
    machine, seed = task
    (t, X), _ = sessions(machine, seed)
    rows = C.cut(t, X, t[0], t[0] + C.CALIB_S)
    return [dict(entry=entry, machine=machine, seed=seed, right_entry=RIGHT[(entry, machine)],
                 **C.track3.fit_check(lib[entry], C.track3.manifest(entry), *rows, list(F.FEATURE_NAMES))) for entry in lib]


def rows_of(detail) -> list[dict]:
    rows = []
    for entry in dict.fromkeys(d["entry"] for d in detail):
        for machine in dict.fromkeys(d["machine"] for d in detail):
            got = [d for d in detail if d["entry"] == entry and d["machine"] == machine]
            right = got[0]["right_entry"]
            kind = {True: "right entry", False: "wrong entry", None: "neither right nor wrong"}[right]
            verb = "fail" if right is False else "pass"
            rows.append({"entry": entry, "metric": f"fit check, {machine} machines ({kind}): share that {verb}",
                         "value": C.share(d["passes"] == (verb == "pass") for d in got), "n": f"{len(got)} machines",
                         "standin": getattr(C.track3, "FIT_RULE", None)})
            for key, text in (("score_over_entry_heldout_median",
                               "median held-out score after calibration over the entry's own held-out median score"),
                              ("score_over_entry_line",
                               "median calibration score on the entry as shipped over the entry's threshold")):
                over = [d[key] for d in got if key in d]
                if over:
                    rows.append({"entry": entry, "metric": f"fit check, {machine} machines ({kind}): {text}, median over machines",
                                 "value": C.median(over), "n": f"{len(over)} machines", "range": C.spread(over)})
    return rows


def main(argv=None) -> int:
    args = C.args_for(__doc__, argv)
    lib = C.library()
    detail = [d for part in C.pmap(partial(one, lib=lib), [(m, s) for m in C.SITE for s in args.seeds], args.workers)
              for d in part]
    C.write_result("fit", args.out, rows_of(detail), command=C.command("check_fit", args), seeds=args.seeds,
                   data_source="; ".join(C.SOURCE.values()), label="simulated", uses_library=True, detail=detail)
    return 0


if __name__ == "__main__":
    sys.exit(main())
