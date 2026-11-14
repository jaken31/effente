"""Tables from the Protocol A results (results/device_A.npz and results/A/*.npz).

Every score is first put in units of its condition's line, set from learning data only:
  - the device: its own threshold (median of 5 seconds) and spike line (one second), as the Watcher alarms;
  - the alternatives: the highest out-of-sample learning score under the alarm rule ("any": one second over the
    line alarms; "med3": the median of the last 3 seconds over the line).
Then the conditions are pooled. "best@x" uses the best single threshold on those normalised scores for all
conditions at once (one sensitivity setting for every machine) that lets at most x of the held-out normal
clips alarm. Sets: "select" = loads heavy and light (method selection), "confirm" = load med (confirmation).

    python -m training.record.sscc_eval.report --set select [--rules any,med3] [--only dev]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

from training.record.sscc_eval import common as C


def in_set(cond: str, which: str) -> bool:
    load = cond.split("_")[0]
    return which == "all" or (which == "confirm") == (load == C.CONFIRM_LOAD)


def noise_of(cond: str) -> str:
    return "clean" if cond.endswith("clean") else "noise"


def alt_stats(path: Path, which: str, rule: str, line_mode: str = "max"):
    d = np.load(path)
    conds = sorted({k.split("|")[0] for k in d.files if "|" in k})
    ns, fs, lab = {}, {}, {}
    k = 3 if rule.startswith("med3") else 1
    for c in conds:
        if not in_set(c, which):
            continue
        cal, hold, fault = d[f"{c}|cal"], d[f"{c}|hold"], d[f"{c}|fault"]
        cs = C.running_median(cal.ravel(), k) if k > 1 else cal.ravel()
        if line_mode == "max":  # the highest out-of-sample learning score
            line = float(cs.max())
        elif line_mode == "tail":  # the highest score of the last fifth (learned on the first 4/5), like the device
            last = (np.arange(len(cal)) * 5 // len(cal)) == 4
            line = float(cs.reshape(cal.shape)[last].max())
        else:  # "q99" etc: that quantile of the out-of-sample learning scores
            line = float(np.quantile(cs, float(line_mode[1:]) / 100))
        line = line if line > 0 else 1e-12
        r = "any" if k == 1 else "med"
        ns[c], fs[c] = C.clip_stats(hold / line, fault / line, r, k)
        if "spike" in rule:  # also one second over <x> times the highest single out-of-sample learning score
            sl = float(cal.max()) * float(rule.split("spike")[1])
            na, fa = C.clip_stats(hold / sl, fault / sl, "any")
            ns[c], fs[c] = np.maximum(ns[c], na), np.maximum(fs[c], fa)
        lab[c] = d[f"{c}|labels"]
    return ns, fs, lab


def device_stats(path: Path, which: str, part: str = "both"):
    d = np.load(path)
    conds = sorted({k.split("|")[0] for k in d.files})
    ns, fs, lab = {}, {}, {}
    for c in conds:
        if not in_set(c, which):
            continue
        if part == "both":
            ns[c] = np.maximum(d[f"{c}|norm_med"], d[f"{c}|norm_spike"])
            fs[c] = np.maximum(d[f"{c}|fault_med"], d[f"{c}|fault_spike"])
        elif part == "med":
            ns[c], fs[c] = d[f"{c}|norm_med"], d[f"{c}|fault_med"]
        else:
            ns[c], fs[c] = d[f"{c}|norm_spike"], d[f"{c}|fault_spike"]
        lab[c] = d[f"{c}|labels"]
    return ns, fs, lab


def device_with_safety(path: Path, which: str, checks=None):
    """Clip-level alarm counts of the device's model OR its safety checks (only the named checks if given)."""
    d = np.load(path)
    info = json.loads(Path(str(path).replace(".npz", ".json")).read_text())
    res = {}
    for c, v in info.items():
        if not in_set(c, which):
            continue
        nm = np.maximum(d[f"{c}|norm_med"], d[f"{c}|norm_spike"])[:, -1] > 1
        fm = np.maximum(d[f"{c}|fault_med"], d[f"{c}|fault_spike"])[:, -1] > 1
        sn = np.array([bool([k for k in s if checks is None or k in checks]) for s in v["safety_norm"]])
        sf = np.array([bool([k for k in s if checks is None or k in checks]) for s in v["safety_fault"]])
        res[c] = {"norm_model": nm, "fault_model": fm, "norm_safety": sn, "fault_safety": sf,
                  "labels": d[f"{c}|labels"], "safety_fault_names": v["safety_fault"], "safety_norm_names": v["safety_norm"]}
    return res


def summarise(ns, fs, lab):
    return C.summarise(ns, fs, lab, {c: noise_of(c) for c in ns})


def fmt_row(name: str, s: dict, group: str = "all") -> str:
    g = s.get(group)
    if not g:
        return f"| {name} | - |"
    fa = g["faults"]
    cells = [name, f"{g['fa_at_line_k']}/{g['n_normal']}", f"{100 * fa['all']['caught_at_line']:.0f}"]
    cells += [f"{100 * fa[f]['caught_at_line']:.0f}" if f in fa else "-" for f in C.FAULTS]
    cells += [f"{g['auroc_pooled']:.3f}", f"{g['auroc_mean_per_cond']:.3f}", f"{g['pauc10_pooled']:.3f}"]
    cells += [f"{100 * fa['all'][f'best_at_fpr{x:g}']:.0f}" for x in (0.0, 0.01, 0.05)]
    return "| " + " | ".join(cells) + " |"


HEADER = ("| method | false alarms at line (clips) | caught at line % | dry | lean | loose | screwdrop | AUROC pooled | "
          "AUROC mean per cond | pAUC 0.1 | best@0 % | best@1 % | best@5 % |\n|" + "---|" * 13)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default="select")
    ap.add_argument("--rules", default="any,med3")
    ap.add_argument("--only", default="")
    ap.add_argument("--group", default="all")
    ap.add_argument("--line", default="max", help="max | tail | q99 | q95: how an alternative's line is set")
    ap.add_argument("--json", default="")
    args = ap.parse_args(argv)
    out, rows = {}, []
    dev = C.RESULTS / "device_A.npz"
    if dev.exists() and "device" in ("device" + args.only):
        for part in ("both", "med", "spike"):
            s = summarise(*device_stats(dev, args.set, part))
            out[f"device_as_is[{part}]"] = s
            rows.append(fmt_row(f"device as is ({part})", s, args.group))
    for p in sorted(C.A_DIR.glob("*.npz")):
        if args.only and args.only not in p.stem:
            continue
        for rule in args.rules.split(","):
            s = summarise(*alt_stats(p, args.set, rule, args.line))
            out[f"{p.stem}[{rule}]"] = s
            rows.append(fmt_row(f"{p.stem} [{rule}]", s, args.group))
    print(f"set={args.set} group={args.group} line={args.line}")
    print(HEADER)
    print("\n".join(rows))
    if args.json:
        Path(args.json).write_text(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
