"""Is a variant better, or only more trigger-happy? For saved explore.py runs (Protocol A select + shift), move one
sensitivity dial (the line x dial, same for every condition) and report, per dial: held-out false alarms, fault
catches (without screwdrop / screwdrop), and alarms on normal clips under changed conditions (other load, next
speed, learn-late/judge-early). Then each variant at the dial where its changed-condition alarms (load + speed) match
the reference's at dial 1: catches compared at equal "alarms on things that are not faults".

    python -m training.record.sscc_eval.tradeoff REF_STEM STEM [STEM...]
"""

from __future__ import annotations

import json
import sys

import numpy as np

from training.record.sscc_eval import common as C

A2 = C.RESULTS / "A2"
NOFS = ("dry", "lean", "loose")


def load(stem):
    d = np.load(A2 / f"{stem}.select.npz")
    conds = sorted({k.split("|")[0] for k in d.files})
    h = np.concatenate([d[f"{c}|hold_stat"].max(1) for c in conds])
    f = np.concatenate([d[f"{c}|fault_stat"].max(1) for c in conds])
    lab = np.concatenate([d[f"{c}|labels"] for c in conds])
    sh = json.loads((A2 / f"{stem}.shift.json").read_text())["clip_max_stat"]
    return h, f, lab, {k: np.array(v) for k, v in sh.items()}


def at(stem, dial):
    h, f, lab, sh = load(stem)
    ns = np.isin(lab, NOFS)
    ls = np.r_[sh["load"], sh["speed"]]
    return {"fa": int((h > dial).sum()), "n": len(h), "noscrew": float(np.mean(f[ns] > dial)),
            "screw": float(np.mean(f[lab == "screwdrop"] > dial)), "shift": float(np.mean(ls > dial)),
            "reverse": float(np.mean(sh["reverse"] > dial))}


def main(ref, stems):
    r0 = at(ref, 1.0)
    print(f"reference {ref} at dial 1: {r0}")
    for stem in [ref] + stems:
        dials = np.exp(np.linspace(np.log(0.5), np.log(4), 141))
        rows = [(dl, at(stem, dl)) for dl in dials]
        # the smallest dial whose load+speed shift alarms are <= the reference's
        ok = [(dl, r) for dl, r in rows if r["shift"] <= r0["shift"] + 1e-9]
        dl, r = ok[0] if ok else rows[-1]
        r1 = at(stem, 1.0)
        print(f"{stem:32s} dial 1: FA {r1['fa']}/{r1['n']} noScrew {100 * r1['noscrew']:.0f} screw {100 * r1['screw']:.0f} "
              f"shift {100 * r1['shift']:.0f} rev {100 * r1['reverse']:.0f} || iso-shift dial {dl:.2f}: FA {r['fa']} "
              f"noScrew {100 * r['noscrew']:.0f} screw {100 * r['screw']:.0f} shift {100 * r['shift']:.0f} "
              f"rev {100 * r['reverse']:.0f}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])
