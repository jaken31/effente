"""One model for several speeds: per load and background (clean / noise), learn on the first 60 % of the normal
recordings at speeds 60, 80 and 100 together (each its own recording: the line's blocks and the group normaliser are
taken inside each recording), then judge each speed's held-out normal clips (continuous stream) and fault clips
(each after typical history), with the same rule as explore.py.

    python -m training.record.sscc_eval.multispeed2 --set select --configs "NAME;NAME"
"""

from __future__ import annotations

import argparse
import json
import sys
from multiprocessing import Pool

import numpy as np

from training.record.sscc_eval import common as C
from training.record.sscc_eval import explore as E

NOFS = ("dry", "lean", "loose")


def run(job):
    name, which = job
    cfg = E.cfg_parse(name)
    d = E.data()
    flat = lambda a: a.reshape(-1, a.shape[-1])
    loads = ("heavy", "light") if which == "select" else ("med",)
    res = {}
    for load in loads:
        for noise in ("clean", "noise"):
            conds = [f"{load}_vel{v}_{noise}" for v in (60, 80, 100)]
            parts = [C.split_normal(d, c) for c in conds]
            RL = [E.rows(cfg["fs"], p[0])[0] for p in parts]
            grp = E.rows(cfg["fs"], parts[0][0])[1]
            rec = np.concatenate([np.full(len(r) * 5, i) for i, r in enumerate(RL)])
            m = E.make(cfg, grp, cfg["fs"]).learn(np.concatenate([flat(r) for r in RL]), rec)
            for c, (learn, hold) in zip(conds, parts):
                fids = C.time_order(d, d.idx(cond=c, label=list(C.FAULTS)))
                RH = E.rows(cfg["fs"], hold)[0]
                RF = E.rows(cfg["fs"], fids)[0]
                sh = m.scores(flat(RH))
                pre = float(np.median(sh))
                hs = E.rule_stat(m, sh, pre).reshape(len(hold), 5)
                fs = np.array([E.rule_stat(m, x, pre) for x in E.fault_clip_scores(m, RF)])
                lab = d.meta["label"][fids]
                res[c] = {"fa": int((hs.max(1) > 1).sum()), "n": len(hold),
                          "noscrew": [int((fs[np.isin(lab, NOFS)].max(1) > 1).sum()), int(np.isin(lab, NOFS).sum())],
                          "screw": [int((fs[lab == "screwdrop"].max(1) > 1).sum()), int((lab == "screwdrop").sum())]}
    tot = {}
    for key in ("clean", "noise"):
        cs = [c for c in res if c.endswith(key)]
        tot[key] = {"fa": [sum(res[c]["fa"] for c in cs), sum(res[c]["n"] for c in cs)],
                    "noscrew": [sum(res[c]["noscrew"][0] for c in cs), sum(res[c]["noscrew"][1] for c in cs)],
                    "screw": [sum(res[c]["screw"][0] for c in cs), sum(res[c]["screw"][1] for c in cs)]}
    stem = name.replace("|", "__").replace("+", "-").replace("=", "").replace(",", "_").replace("@", "AT")
    (E.OUT / f"{stem}.multispeed_{which}.json").write_text(json.dumps({"config": name, "per_cond": res, "total": tot},
                                                                      indent=1))
    pc = lambda a: f"{a[0]}/{a[1]} ({100 * a[0] / max(a[1], 1):.0f} %)"
    lines = [f"{name}"]
    for key, t in tot.items():
        lines.append(f"   {key}: FA {pc(t['fa'])} | caught without screwdrop {pc(t['noscrew'])} | screwdrop {pc(t['screw'])}")
    lines.append("   per condition: " + "; ".join(f"{c} FA {r['fa']}/{r['n']} noScrew {r['noscrew'][0]}/{r['noscrew'][1]}"
                                               for c, r in res.items()))
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default="select", choices=("select", "confirm"))
    ap.add_argument("--configs", required=True)
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args(argv)
    names = [x for x in a.configs.split(";") if x]
    with Pool(min(a.workers, len(names))) as pool:
        for msg in pool.imap_unordered(run, [(n, a.set) for n in names]):
            print(msg, flush=True)


if __name__ == "__main__":
    sys.exit(main())
