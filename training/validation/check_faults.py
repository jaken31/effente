#!/usr/bin/env python3
"""Check 2. Injected and simulated faults: alarm or not, time to alarm, fault type named.

    python3 -m training.validation.check_faults --seeds 1,2,3,4,5

Every session: 5 minutes of calibration, 5 minutes of normal running, then 10 minutes with the
fault. The device's runtime judges everything after the calibration. Each seed is one machine,
calibrated once, on its session without a fault (the twin); its fault sessions are the same machine,
seed and site with one fault switched on at the onset, judged by that one calibrated model.

  simulated  the simulator makes the fault in the sensor signals. Conveyor: its 11 kinds that switch
             on within a minute, each at severity 1.0 and 0.3 (the simulator's "obvious" and
             "subtle"). Server rack: its 4 sudden kinds. Faults that grow over days (the simulators'
             wear kinds) do not fit a 20-minute session and are left out; check 7 covers slow wear.
  injected   no simulator fault: from the onset, the readings of one fault type's signature
             (effente.fault_types) are moved INJECT_SPREADS of this machine's own spread, scaled by
             the signature's weights, on top of a normal session. This is the pattern the fault
             classifier is trained on, so it checks the naming path, not real faults.

Per fault: alarmed after the onset or not, seconds from the onset to the first alarm, the fault type
on the screen (the one named most over the alarm periods) and its symptom. An alarm only says
something about the fault if the machine would not have alarmed anyway. So the share of twins
that alarm in the fault's window is reported next to the faults' own, and the time to alarm is
taken only over faults whose twin stays quiet.
For the conveyor the symptom is compared with what training/record/conveyor_eval/evaluate.py lists
as sensible for that fault (EXPECTED).

Simulated only; the fault type signatures are unverified on real faults.
"""

from __future__ import annotations

import sys
from collections import Counter
from functools import partial

import numpy as np

from effente import fault_types as FT
from effente import features as F
from effente import model_runtime as RT
from training.validation import common as C

NORMAL_S = 300  # normal running between the calibration and the fault
FAULT_S = 600
INJECT_SPREADS = 8.0  # the size of an injected pattern: the preparation's clip (effente.model_input.CLIP)
SERVER_FAULTS = (("ups_alarm", 300), ("rack_bump", 2), ("hdd_clicking", None), ("fan5_failure", None))


def cases(machine: str) -> list[tuple]:
    """(kind, seconds it lasts or None for "stays on", severity) for every simulated fault."""
    if machine == "conveyor":
        from effente.simulate_conveyor import FAULT_KINDS, OBVIOUS, SLOW, SUBTLE
        return [(k, 180 if k == "jam" else None, sev) for k in FAULT_KINDS if k not in SLOW for sev in (OBVIOUS, SUBTLE)]
    return [(k, dur, 1.0) for k, dur in SERVER_FAULTS]


def layout(machine: str, seed: int):
    """(session start, fault onset, session end) on the simulator's clock."""
    s0 = C.start_of(machine, seed)
    return s0, s0 + C.CALIB_S + NORMAL_S, s0 + C.CALIB_S + NORMAL_S + FAULT_S


def outcome(w: dict, onset: float, end: float) -> dict:
    """What the device did about one fault, from its judged periods (common.watch)."""
    got = C.fault_outcome(w["t"], w["alarm"], onset, end)
    during = w["alarm"] & (w["t"] >= onset) & (w["t"] < end)
    named = Counter(d for d, a in zip(w["diagnosis"], during) if a)
    said = Counter(s for s, a in zip(w["symptom"], during) if a and s)
    return {"alarmed": got["caught"], "seconds_to_alarm": got["after_s"],
            "named": named.most_common(1)[0][0] if named else None,
            "symptom": said.most_common(1)[0][0] if said else None}


# ----------------------------------------------------------------- injected patterns

def shift(X, prep, name: str, spreads: float) -> None:
    """Move one reading by `spreads` of this machine's spread (as its preparation measures it), in place."""
    j, s = F.INDEX[name], float(prep.scale[prep.columns.index(name)])
    if "kurt" in name:  # prepared as log10(kurtosis + 3): move it there
        X[:, j] = 10 ** (np.log10(np.maximum(X[:, j] + 3.0, 1e-3)) + spreads * s) - 3.0
    else:
        X[:, j] += spreads * s


OFF_SCALE = 1e9  # a spread at or above this is a reading the calibration switched off (effente.calibration)


def usable(prep, names) -> list[str]:
    """The readings of `names` this machine's model reads (kept, and not switched off by the
    calibration) and that can be moved in raw units."""
    at = {c: i for i, c in enumerate(prep.columns)}
    return [n for n in names if n in at and prep.scale[at[n]] < OFF_SCALE and "_since_" not in n]


def inject_type(X, prep, ft, spreads: float = INJECT_SPREADS) -> list[str]:
    """Add one fault type's signature to rows X (in place): for each member, its first usable reading,
    moved in the member's direction, scaled by its weight. Returns the readings moved."""
    members = [(m, usable(prep, m.names)) for m in ft.members]
    members = [(m, names) for m, names in members if names]
    top = max((m.weight for m, _ in members), default=1.0)
    for m, names in members:
        shift(X, prep, names[0], (m.direction or 1) * spreads * m.weight / top)
    return [names[0] for _, names in members]


# ----------------------------------------------------------------- one machine

def fault_session(machine: str, seed: int, case):
    """Rows of the session with one simulated fault switched on at the onset."""
    kind, dur, sev = case
    s0, onset, end = layout(machine, seed)
    return C.session(machine, seed, s0, end - s0, faults=((kind, onset, onset + (dur if dur else C.DAY), sev),))


def warm(task) -> None:
    """Simulate one session into the cache (so the per-machine work below only reads)."""
    machine, seed, case = task
    if case is None:
        s0, _, end = layout(machine, seed)
        C.session(machine, seed, s0, end - s0)
    else:
        fault_session(machine, seed, case)


def warm_tasks(machines) -> list[tuple]:
    """Every session the (entry, seed) machines need: the twin (None) and one per simulated fault."""
    return [(C.ENTRY_MACHINE[e], s, c) for e, s in machines for c in [None, *cases(C.ENTRY_MACHINE[e])]]


def machine_one(task, lib, length_s: int = C.CALIB_S, injected: bool = True) -> list[dict]:
    """One machine: calibrate on its no-fault twin, then judge the twin, each simulated fault and
    (if `injected`) each injected fault type with that model."""
    entry, seed = task
    machine = C.ENTRY_MACHINE[entry]
    s0, onset, end = layout(machine, seed)
    t, X = C.session(machine, seed, s0, end - s0)
    base = {"entry": entry, "seed": seed, "calibration_s": length_s}
    model, report = C.calibrate(lib[entry], t, X, seed=seed, length_s=length_s)
    if model is None:
        return [dict(base, how="twin", refused=report)]
    judge = lambda rows: outcome(C.watch(model, *C.cut(*rows, s0 + C.CALIB_S, end)), onset, end)  # noqa: E731
    twin = judge((t, X))
    out = [dict(base, how="twin", **twin)]
    for kind, dur, sev in cases(machine):
        got = dict(base, how="simulated", fault=kind, severity=sev, twin_alarmed=twin["alarmed"],
                   **judge(fault_session(machine, seed, (kind, dur, sev))))
        if machine == "conveyor" and got["symptom"]:
            from training.record.conveyor_eval.evaluate import EXPECTED
            got["symptom_expected"] = got["symptom"] in EXPECTED[kind]
        out.append(got)
    if not injected:
        return out
    prep, X = RT.Model(model).prep, X.astype(float)
    for ft in FT.TYPES:
        Xf = X.copy()
        rows = Xf[t >= onset]  # a copy: written back below
        moved = inject_type(rows, prep, ft)
        if not moved:
            out.append(dict(base, how="injected", fault=ft.name,
                            skipped="this machine's model reads none of its readings"))
            continue
        Xf[t >= onset] = rows
        got = judge((t, Xf))
        out.append(dict(base, how="injected", fault=ft.name, moved=moved, twin_alarmed=twin["alarmed"],
                        named_as_injected=got["named"] == ft.name, **got))
    return out


# ----------------------------------------------------------------- the report's rows

def summary(got: list[dict], what: str) -> list[dict]:
    """Rows for one group of faults (all with the same entry)."""
    entry = got[0]["entry"]
    n = f"{len(got)} faults"
    alarmed = [d for d in got if d["alarmed"]]
    quiet = [d for d in got if d["twin_alarmed"] is False]
    rows = [{"entry": entry, "metric": f"{what}: share with an alarm after the onset",
             "value": C.share(d["alarmed"] for d in got), "n": n},
            {"entry": entry, "metric": f"{what}: share whose twin without the fault alarms in the same window",
             "value": C.share(d["twin_alarmed"] for d in got), "n": n},
            {"entry": entry, "metric": f"{what}: share with an alarm after the onset, faults whose twin does not alarm",
             "value": C.share(d["alarmed"] for d in quiet), "n": f"{len(quiet)} faults"},
            {"entry": entry, "metric": f"{what}: median seconds from onset to first alarm, faults whose twin does not alarm",
             "value": C.median(d["seconds_to_alarm"] for d in quiet),
             "n": f"{sum(d['alarmed'] for d in quiet)} alarmed of {len(quiet)}"},
            {"entry": entry, "metric": f"{what}: share of alarmed faults given a fault type name (not unrecognised_pattern)",
             "value": C.share(d["named"] != "unrecognised_pattern" for d in alarmed), "n": f"{len(alarmed)} alarmed"}]
    fits = [d["symptom_expected"] for d in alarmed if "symptom_expected" in d]
    if fits:
        rows.append({"entry": entry,
                     "metric": f"{what}: share of alarmed faults whose screen symptom is one expected for the fault",
                     "value": C.share(fits), "n": f"{len(fits)} alarmed"})
    if any("named_as_injected" in d for d in got):
        rows.append({"entry": entry, "metric": f"{what}: share of alarmed faults named as the injected type",
                     "value": C.share(d["named_as_injected"] for d in alarmed), "n": f"{len(alarmed)} alarmed"})
    return rows


def rows_of(detail) -> list[dict]:
    rows = []
    done = [d for d in detail if "alarmed" in d and d["how"] != "twin"]
    for entry in dict.fromkeys(d["entry"] for d in done):
        sim = [d for d in done if d["entry"] == entry and d["how"] == "simulated"]
        for sev in sorted({d["severity"] for d in sim}, reverse=True):
            rows += summary([d for d in sim if d["severity"] == sev], f"simulated faults, severity {sev:g}")
        inj = [d for d in done if d["entry"] == entry and d["how"] == "injected"]
        if inj:
            rows += summary(inj, f"injected fault types, {INJECT_SPREADS:g} spreads")
    return rows


def main(argv=None) -> int:
    args = C.args_for(__doc__, argv)
    lib = C.library()
    machines = [(e, s) for e in lib for s in args.seeds]
    C.pmap(warm, warm_tasks(machines), args.workers)
    detail = [d for part in C.pmap(partial(machine_one, lib=lib), machines, args.workers) for d in part]
    C.write_result("faults", args.out, rows_of(detail), command=C.command("check_faults", args), seeds=args.seeds,
                   data_source=C.sources("; injected: effente.fault_types signatures on those rows"),
                   label="simulated, unverified", uses_library=True, detail=detail)
    return 0


if __name__ == "__main__":
    sys.exit(main())
