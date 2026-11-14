"""Markdown tables for Protocol B (results/shift_*.json, results/variety_*.json) and the paper protocol.

    python -m training.record.sscc_eval.tables_b shift_select_run1.json shift_select_final.json ...
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict

from training.record.sscc_eval import common as C


def pct(k, n):
    return f"{100 * k / n:.0f} % ({k}/{n})" if n else "-"


def shift_table(rows):
    agg = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for r in rows:
        m = r["method"]
        for k, (a, n) in ((k, v) for k, v in r.items() if isinstance(v, list)):
            cat = ("same condition" if k == "same" else "other speed (20-100)" if k.startswith("speed") else
                   "other load, same speed" if k.startswith("load_") else "same, with factory noise" if k == "noise"
                   else "faults (same condition)")
            agg[m][cat][0] += a
            agg[m][cat][1] += n
    cats = ["same condition", "other load, same speed", "other speed (20-100)", "same, with factory noise",
            "faults (same condition)"]
    out = ["| method | " + " | ".join(cats) + " |", "|---" * (len(cats) + 1) + "|"]
    for m, d in agg.items():
        out.append(f"| {m} | " + " | ".join(pct(*d[c]) for c in cats) + " |")
    return "\n".join(out)


def variety_table(rows):
    out = ["| method | learned on | FA clean normals | FA noise normals | caught clean faults | caught noise faults | "
           "clean: dry / lean / loose / screwdrop | noise: dry / lean / loose / screwdrop |", "|---" * 8 + "|"]
    agg = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for r in rows:
        key = (r["method"], r["learn"])
        for k, v in r.items():
            if not isinstance(v, list):
                continue
            parts = k.split("|")
            noise = "clean" if parts[0].endswith("clean") else "noise"
            if len(parts) == 2:
                agg[key][f"{parts[1]}|{noise}"][0] += v[0]
                agg[key][f"{parts[1]}|{noise}"][1] += v[1]
            else:
                agg[key][f"{parts[2]}|{noise}"][0] += v[0]
                agg[key][f"{parts[2]}|{noise}"][1] += v[1]
    for (m, learn), d in agg.items():
        per = lambda nz: " / ".join(f"{100 * d[f'{f}|{nz}'][0] / max(d[f'{f}|{nz}'][1], 1):.0f}" for f in C.FAULTS)
        out.append(f"| {m} | {learn} | {pct(*d['normal|clean'])} | {pct(*d['normal|noise'])} | {pct(*d['fault|clean'])} | "
                   f"{pct(*d['fault|noise'])} | {per('clean')} | {per('noise')} |")
    return "\n".join(out)


def main(argv=None):
    for name in (argv or sys.argv[1:]):
        rows = json.loads((C.RESULTS / name).read_text())
        print(f"\n### {name}\n")
        print(shift_table(rows) if name.startswith("shift") else variety_table(rows))


if __name__ == "__main__":
    main()
