#!/usr/bin/env python3
"""Check 4. int8 runtime vs trained network: score correlation.

    python3 -m training.validation.check_int8 --seeds 1,2,3,4,5

The trainer (effente.trainer.train_model) scores its rows twice: with the network as trained (float
weights) and with the device runtime on the int8 weights it wrote (effente.model_runtime), and puts
the correlation of the two in the training report (int8_vs_float_score_correlation). This check
collects that number; it does not compute the correlation a second way.

  library entries   the number in each entry's training report, if the library builder wrote one.
                    If it did not, that is said under "Not run" and no number is given.
  own models        a model trained on one machine alone, the TRD's long-stay model: train_model
                    with its default normal model (PCA) on each seed's 35-minute normal session
                    of check 1. A calibrated model keeps its entry's network, so it has no number
                    of its own.

Simulated rows only.
"""

from __future__ import annotations

import sys

from effente import features as F
from effente import trainer as TR
from training.validation import common as C
from training.validation.check_heldout_normal import sessions

KEY = "int8_vs_float_score_correlation"


def own_one(task) -> dict:
    machine, seed = task
    (t, X), _ = sessions(machine, seed)
    _, report = TR.train_model(t, X, list(F.FEATURE_NAMES), seed=seed, source=f"simulated {machine}, seed {seed}")
    return {"entry": f"own model, {machine}", "machine": machine, "seed": seed, "correlation": report[KEY],
            "normal_model": report["normal_model"], "rows_learned": report["rows_learned"]}


def entry_items(lib: dict) -> tuple[list[dict], list[str]]:
    """(one item per entry that reports the number, a note per entry that does not)."""
    items, notes = [], []
    for name, d in lib.items():
        if KEY in d.get("training_report", {}):
            items.append({"entry": name, "correlation": d["training_report"][KEY]})
        else:
            notes.append(f"library entry {name}: not measured. Its training report has no {KEY}: the library "
                         f"builder writes the int8 weights without keeping the float network to compare with")
    return items, notes


def rows_of(detail) -> list[dict]:
    rows = []
    for entry in dict.fromkeys(d["entry"] for d in detail):
        got = [d["correlation"] for d in detail if d["entry"] == entry]
        what = "lowest over machines" if len(got) > 1 or entry.startswith("own model") else "the entry's network"
        rows.append({"entry": entry, "metric": f"int8 vs float score correlation, {what}", "value": min(got),
                     "n": f"{len(got)} model{'s' if len(got) != 1 else ''}", "range": C.spread(got) if len(got) > 1 else ""})
    return rows


def main(argv=None) -> int:
    args = C.args_for(__doc__, argv)
    detail, notes = entry_items(C.library())
    detail += C.pmap(own_one, [(m, s) for m in C.SITE for s in args.seeds], args.workers)
    C.write_result("int8", args.out, rows_of(detail), command=C.command("check_int8", args), seeds=args.seeds,
                   data_source={d["entry"]: C.SOURCE.get(d.get("machine"), "training/library") for d in detail},
                   label="simulated", uses_library=False, detail=detail,
                   notes=notes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
