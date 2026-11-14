#!/usr/bin/env python3
"""Check 3. A fault no type covers: unrecognised_pattern, and not a wrong name.

    python3 -m training.validation.check_unknown_fault --seeds 1,2,3,4,5

A fault type is a signature over named readings (effente.fault_types). Readings that appear in no
signature are found from the code, and three patterns are made from them, each three neighbouring
bands moved INJECT_SPREADS of this machine's spread from the onset on, on top of a normal session
laid out as in check 2 (5 minutes calibration, 5 normal, 10 with the pattern):

  vib_band    three neighbouring vibration bands (a new resonance)
  snd_band    three neighbouring sound bands below the "grinding or hiss" range
  slow_band   three neighbouring slow-vibration bands

Which three is picked from the seed. Per pattern: alarmed or not, next to whether the same session
without the pattern (its twin, as in check 2) alarms in that window; the diagnosis on the screen
during the alarm (the Watcher shows the type named most often in the stretch, and
unrecognised_pattern only if no period was given a name); and, period by period, the share of
alarm periods that were themselves unrecognised_pattern.

Simulated rows, injected patterns. No real unknown fault has been recorded.
"""

from __future__ import annotations

import sys
from collections import Counter
from functools import partial

import numpy as np

from effente import fault_types as FT
from effente import model_runtime as RT
from training.validation import common as C
from training.validation.check_faults import INJECT_SPREADS, layout, outcome, shift, usable

FAMILIES = ("vib_band_", "snd_band_", "slow_band_")
WIDTH = 3
COVERED = frozenset(n for ft in FT.TYPES for m in ft.members for n in m.names)


def uncovered(prep, family: str) -> list[str]:
    """Readings of one family that this machine's model reads and that no fault type's signature names."""
    return [n for n in usable(prep, prep.columns) if n.startswith(family) and n not in COVERED]


def pick(names: list[str], rng) -> list[str]:
    """WIDTH neighbouring readings ([] if the family has fewer)."""
    if len(names) < WIDTH:
        return []
    k = int(rng.integers(len(names) - WIDTH + 1))
    return names[k:k + WIDTH]


def unknown_outcome(w: dict, onset: float, end: float) -> dict:
    """outcome() plus, of the alarm periods after the onset, the share that were unrecognised_pattern
    themselves and every wrong name given."""
    got = outcome(w, onset, end)
    during = w["alarm"] & (w["t"] >= onset) & (w["t"] < end)
    each = [d for d, a in zip(w["diagnosis_this_period"], during) if a]
    got["unrecognised_on_screen"] = got["named"] == "unrecognised_pattern" if got["alarmed"] else None
    got["alarm_periods"] = len(each)
    got["unrecognised_periods"] = sum(d == "unrecognised_pattern" for d in each)
    got["names_given"] = dict(Counter(d for d in each if d != "unrecognised_pattern"))
    return got


def one(task, lib) -> list[dict]:
    entry, seed = task
    machine = C.ENTRY_MACHINE[entry]
    s0, onset, end = layout(machine, seed)
    t, X = C.session(machine, seed, s0, end - s0)
    base = {"entry": entry, "seed": seed}
    model, report = C.calibrate(lib[entry], t, X, seed=seed)
    if model is None:
        return [dict(base, refused=report)]
    judge = lambda rows: C.watch(model, *C.cut(t, rows, s0 + C.CALIB_S, end))  # noqa: E731
    twin = outcome(judge(X), onset, end)["alarmed"]
    prep, X, out = RT.Model(model).prep, X.astype(float), []
    for k, family in enumerate(FAMILIES):
        names = pick(uncovered(prep, family), np.random.default_rng([seed, k]))
        if not names:
            out.append(dict(base, pattern=family, skipped="fewer than 3 such readings in this machine's model"))
            continue
        Xf = X.copy()
        rows = Xf[t >= onset]  # a copy: written back below
        for n in names:
            shift(rows, prep, n, INJECT_SPREADS)
        Xf[t >= onset] = rows
        out.append(dict(base, pattern=family, moved=names, twin_alarmed=twin, **unknown_outcome(judge(Xf), onset, end)))
    return out


def rows_of(detail) -> list[dict]:
    rows = []
    done = [d for d in detail if "alarmed" in d]
    for entry in dict.fromkeys(d["entry"] for d in done):
        got = [d for d in done if d["entry"] == entry]
        alarmed = [d for d in got if d["alarmed"]]
        periods = sum(d["alarm_periods"] for d in got)
        rows += [
            {"entry": entry, "metric": "uncovered patterns: share with an alarm after the onset",
             "value": C.share(d["alarmed"] for d in got), "n": f"{len(got)} patterns"},
            {"entry": entry, "metric": "uncovered patterns: share whose twin without the pattern alarms in the same window",
             "value": C.share(d["twin_alarmed"] for d in got), "n": f"{len(got)} patterns"},
            {"entry": entry, "metric": "uncovered patterns: share of alarmed ones shown as unrecognised_pattern on the screen",
             "value": C.share(d["unrecognised_on_screen"] for d in alarmed), "n": f"{len(alarmed)} alarmed"},
            {"entry": entry, "metric": "uncovered patterns: share of alarm periods that are unrecognised_pattern",
             "value": round(sum(d["unrecognised_periods"] for d in got) / periods, 4) if periods else None,
             "n": f"{periods} alarm periods"}]
    return rows


def main(argv=None) -> int:
    args = C.args_for(__doc__, argv)
    lib = C.library()
    detail = [d for part in C.pmap(partial(one, lib=lib), [(e, s) for e in lib for s in args.seeds], args.workers)
              for d in part]
    C.write_result("unknown_fault", args.out, rows_of(detail), command=C.command("check_unknown_fault", args),
                   seeds=args.seeds, data_source=C.sources("; injected: readings in no fault type signature"),
                   label="simulated, unverified", uses_library=True, detail=detail)
    return 0


if __name__ == "__main__":
    sys.exit(main())
