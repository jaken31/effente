"""The headline table of the second round: the device as it is, the previous recommendation (as reported, and as
board_model.Final ships it with its spike rule) and the new final model, on one set, three ways (all faults,
without screwdrop, screwdrop alone).

    python -m training.record.sscc_eval.final_tables --set select --final "v1+alin|fuse=mean,snr=6,q=0.995"
Needs results/A2/<final stem>.<set>.npz from explore.py. Writes results/A2/final_tables.<set>.json.
"""

from __future__ import annotations

import argparse
import json

import numpy as np

from training.record.sscc_eval import common as C
from training.record.sscc_eval import explore as E
from training.record.sscc_eval import report as R


def stem_of(name: str) -> str:
    return name.replace("|", "__").replace("+", "-").replace("=", "").replace(",", "_").replace("@", "AT")


def as_res(ns, fs, lab):
    return {c: {"hold_stat": ns[c], "fault_stat": fs[c], "labels": lab[c]} for c in ns}


def from_explore(name: str, which: str):
    d = np.load(E.OUT / f"{stem_of(name)}.{which}.npz", allow_pickle=True)
    conds = sorted({k.split("|")[0] for k in d.files})
    return {c: {"hold_stat": d[f"{c}|hold_stat"], "fault_stat": d[f"{c}|fault_stat"], "labels": d[f"{c}|labels"],
                "stream_stat": d[f"{c}|stream_stat"]} for c in conds}


def row(label: str, s: dict) -> str:
    a = s["all"]
    p = lambda x: f"{100 * x:.0f}"
    ci = a["fa_ci"]
    st = a["all"].get("stream_clips_caught")
    return (f"| {label} | {a['fa_clips']}/{a['n_normal']} [{100 * ci[0]:.1f}-{100 * ci[1]:.1f} %] | "
            f"{a['fa_episodes_per_hour']:.1f} | **{p(a['all']['caught'])}** | {p(a['no_screw']['caught'])} | "
            f"{p(a['screw']['caught'])} | {p(a['dry']['caught'])} | {p(a['lean']['caught'])} | {p(a['loose']['caught'])} | "
            f"{s['clean']['fa_clips']}/{s['clean']['n_normal']}, {p(s['clean']['all']['caught'])} / "
            f"{p(s['clean']['no_screw']['caught'])} | {s['noise']['fa_clips']}/{s['noise']['n_normal']}, "
            f"{p(s['noise']['all']['caught'])} / {p(s['noise']['no_screw']['caught'])} | "
            f"{p(a['all']['best_at_1pct'])} / {p(a['no_screw']['best_at_1pct'])} | "
            f"{'/'.join(p(x) for x in a['all']['by_s'][:3])}/{p(a['all']['by_s'][4])} | "
            f"{'-' if st is None else p(st)} |")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default="select", choices=("select", "confirm"))
    ap.add_argument("--final", required=True)
    ap.add_argument("--extra", default="", help="more explore.py configs to add as rows, ';'-separated")
    a = ap.parse_args(argv)
    which = a.set
    rows = {}
    ns, fs, lab = R.device_stats(C.RESULTS / "device_A.npz", which, "both")
    rows["Device as it is"] = E.summarise(as_res(ns, fs, lab), sorted(ns))
    old = C.A_DIR / "g1-ga__fused_pca95_t2_max.npz"
    ns, fs, lab = R.alt_stats(old, which, "med3", "q99")
    rows["Previous recommendation, as reported (no spike rule)"] = E.summarise(as_res(ns, fs, lab), sorted(ns))
    ns, fs, lab = R.alt_stats(old, which, "med3spike1.0", "q99")
    rows["Previous recommendation as shipped in board_model.Final (spike rule on)"] = E.summarise(as_res(ns, fs, lab),
                                                                                                 sorted(ns))
    for name in [a.final] + [x for x in a.extra.split(";") if x]:
        res = from_explore(name, which)
        rows[("NEW FINAL " if name == a.final else "") + f"`{name}`"] = E.summarise(res, sorted(res))
    print(f"set: {which}")
    print("| | normal clips alarmed [95 % CI] | false-alarm episodes per hour | caught, all faults | without "
          "screwdrop | screwdrop | dry | lean | loose | clean: FA, caught all / w/o screwdrop | factory noise: FA, "
          "caught all / w/o screwdrop | best single line at 1 % FA: all / w/o screwdrop | caught by 1/2/3/5 s | "
          "each fault recording as one stream |")
    print("|" + "---|" * 15)
    for k, s in rows.items():
        print(row(k, s))
    (E.OUT / f"final_tables.{which}.json").write_text(json.dumps(rows, indent=1, default=float))


if __name__ == "__main__":
    main()
