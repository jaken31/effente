"""Run round-3 configurations on the SELECTION loads (heavy, light) only.
    python run.py "CONFIG1;CONFIG2;..." [--tag name]
CONFIG = "<part>+<part>...|key=val,..."
parts (each its own sensor group unless '@name' puts several in one group):
  lin50            159 linear 50 Hz bands 50-8000 + log rms, kurtosis, crest (= the lin50 sound row)
  log64            64 log bands 50-8000 + 3 stats (= the log64 sound row, original features)
  st               3 stats only
  lin:W:lo:hi      linear bands of W Hz from lo to hi (from round-3 spec cache)
  log:N:lo:hi      N log bands lo..hi
  hyb:N:f          N log bands from 50 Hz to f, then 50 Hz linear bands f..8000
  frmstd / frmpk / frmflux   32 ms frame stats, 32 bands of 250 Hz
  env:lo:hi:w      envelope spectrum (>1 kHz) log power, bands of w Hz from lo to hi
  modifiers after '~': rel (subtract row mean of the part), con:k (minus running median over k neighbour bands),
                       sub:n (split the part into n contiguous sub-groups)
keys: det=pca|maha|knn|if|gmm|ocsvm|ae ; var ; maxk ; fuse=mean|max|p2.. ; q ; k (knn) ; ae=64-16-64 ; ep ; noise ;
      nu ; pk (pca dims for knn/if/ocsvm, e.g. pk=20)
Results: logs/run.log (one line per config), res/<stem>.json (summary) and res/<stem>.npz (per-clip stats).
"""
from __future__ import annotations
import os
for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")
import json, sys, time, re
from multiprocessing import Pool
import numpy as np
import r3lib as L
import dets as D

SEL_LOADS = ("heavy", "light")


def part_feat(p, r):
    base, _, mods = p.partition("~")
    a = base.split(":")
    if base == "lin50":
        X = np.concatenate([r["alin"], r["stats"]], -1); nb = 159
    elif base == "log64":
        X = r["gen_a"]; nb = 64
    elif base == "lin25":
        X = np.concatenate([r["alin25"], r["stats"]], -1); nb = 318
    elif base == "a128":
        X = r["a128"]; nb = 128
    elif base == "st":
        X = r["stats"]; nb = 0
    elif a[0] == "lin":
        w, lo, hi = map(float, a[1:])
        X = L.spec_bands(r["spec"], np.arange(lo, hi + 1e-6, w)); nb = X.shape[-1]
    elif a[0] == "log":
        n, lo, hi = int(a[1]), float(a[2]), float(a[3])
        X = L.spec_bands(r["spec"], np.geomspace(lo, hi, n + 1)); nb = n
    elif a[0] == "hyb":
        n, f = int(a[1]), float(a[2])
        e = np.r_[np.geomspace(50, f, n + 1), np.arange(f + 50, 8000.1, 50)]
        X = L.spec_bands(r["spec"], e); nb = X.shape[-1]
    elif base in ("wfmean", "wfmed", "wfp20", "wfstd"):
        X = r["wf"][:, :, ("wfmean", "wfmed", "wfp20", "wfstd").index(base)]; nb = 159
    elif base in ("wfmed50", "wfp2050"):
        X = np.concatenate([r["wf"][:, :, 1 if base == "wfmed50" else 2], r["stats"]], -1); nb = 159
    elif base == "frmstd":
        X = r["frm"][:, :, 0]; nb = 32
    elif base == "frmpk":
        X = r["frm"][:, :, 1]; nb = 32
    elif base == "frmflux":
        X = r["frm"][:, :, 2]; nb = 32
    elif a[0] == "env":
        lo, hi, w = map(int, a[1:])
        P = 10 ** r["env"]
        X = np.stack([np.log10(P[..., s - 1:s - 1 + w].sum(-1)) for s in range(lo, hi, w)], -1); nb = X.shape[-1]
    else:
        raise ValueError(p)
    nsub = 1
    for m in filter(None, mods.split("~")):
        if m == "rel":
            X = X.copy(); X[..., :nb] -= X[..., :nb].mean(-1, keepdims=True)
        elif m.startswith("con:"):
            k = int(m[4:]); X = X.copy(); B = X[..., :nb]
            pad = np.concatenate([np.repeat(B[..., :1], k, -1), B, np.repeat(B[..., -1:], k, -1)], -1)
            win = np.lib.stride_tricks.sliding_window_view(pad, 2 * k + 1, axis=-1)
            X[..., :nb] = B - np.median(win, -1)
        elif m.startswith("sub:"):
            nsub = int(m[4:])
        else:
            raise ValueError(m)
    return X, nb, nsub


def build(name):
    fs, _, rest = name.partition("|")
    c = {"det": "pca", "var": 0.95, "maxk": 40, "fuse": "mean", "q": 0.995, "k": 5, "ae": "64-16-64", "ep": 300,
         "noise": 0.0, "nu": 0.05, "pk": 0, "lr": 3e-3, "wd": 1e-4, "ntree": 200, "blocks": 5, "smooth": 3, "gq": 0.5, "chunk": 0}
    for kv in filter(None, rest.split(",")):
        k, v = kv.split("=")
        c[k] = v if k in ("det", "fuse", "ae") else float(v)
    parts = fs.split("+")

    def featfn(r):
        return np.concatenate([part_feat(p.split("@")[0], r)[0] for p in parts], -1)

    # groups from one dummy row
    dummy = L.raw("normal__heavy_vel100_clean.npz")
    groups, gname, off = [], {}, 0
    for p in parts:
        pp, _, g = p.partition("@")
        X, nb, nsub = part_feat(pp, {k: v[:1] for k, v in dummy.items()})
        d = X.shape[-1]
        idx = np.arange(off, off + d)
        off += d
        subs = np.array_split(idx[:nb], nsub) if nsub > 1 else [idx]
        if nsub > 1 and d > nb:
            subs[-1] = np.r_[subs[-1], idx[nb:]]
        for s in subs:
            if g and nsub == 1:
                if g in gname:
                    groups[gname[g]] = np.r_[groups[gname[g]], s]
                else:
                    gname[g] = len(groups); groups.append(s)
            else:
                groups.append(s)
    pk = (0.99, int(c["pk"])) if c["pk"] > 0 else None
    det = {"pca": lambda: L.PCA(c["var"], int(c["maxk"])),
           "maha": lambda: D.Maha(),
           "pcaw": lambda: D.PCAw(c["var"], int(c["maxk"]), c.get("wres", 1.0), c.get("wt2", 1.0), c.get("cap", 0.0)),
           "pcares": lambda: D.PCAres(c["var"], int(c["maxk"])),
           "knn": lambda: D.KNN(int(c["k"]), pk),
           "if": lambda: D.IForest(int(c["ntree"]), pk),
           "gmm": lambda: D.GMM(int(c["k"]), (0.95, int(c["pk"]) or 20)),
           "ocsvm": lambda: D.OCSVM(c["nu"], "scale", pk),
           "ae": lambda: D.AE(tuple(int(x) for x in c["ae"].split("-")), int(c["ep"]), c["lr"], c["wd"], c["noise"]),
           }[c["det"]]
    modelfn = lambda: L.Framework(det, groups, fuse=c["fuse"], q=c["q"], blocks=int(c["blocks"]), smooth=int(c["smooth"]), gq=c["gq"], chunk=int(c["chunk"]))
    return featfn, modelfn, groups


def _job(args):
    name, key = args
    featfn, modelfn, _ = build(name)
    return L.run_cond(key, featfn, modelfn, keep_scores=True)


def baseline_job(args):
    preset, key = args
    import sys as _s
    _s.path.insert(0, r"C:\Users\finnf\effente-sscc-work")
    from effente import band_model as BM

    def featfn(r):
        v = r["gen_v1"].reshape(r["gen_v1"].shape[0], 5, -1)
        snd = np.concatenate([r["alin"], r["gen_a"][..., 64:]], -1) if preset == "lin50" else r["gen_a"]
        return np.concatenate([v, snd], -1)
    return L.run_cond(key, featfn, lambda: BM.make_model(preset), keep_scores=True)


def stem(name):
    return re.sub(r"[^A-Za-z0-9.=_-]+", "_", name)[:150]


def main():
    names = [s for s in sys.argv[1].split(";") if s.strip()]
    loads = SEL_LOADS
    keys = [k for l in loads for k in L.cond_keys(l)]
    assert not any(k.startswith("med") for k in keys)
    (L.R3 / "res").mkdir(exist_ok=True)
    (L.R3 / "logs").mkdir(exist_ok=True)
    with Pool(12) as pool:
        for name in names:
            t = time.time()
            if name.startswith("BASE:"):
                res = pool.map(baseline_job, [(name[5:], k) for k in keys])
            else:
                res = pool.map(_job, [(name, k) for k in keys])
            s = L.summarize(res)
            line = f"{name:60s} | {L.fmt(s)} | ALL {s['ALL']['fa']}/{s['ALL']['nh']} ns {s['ALL']['ns']:.1f} c {s['ALL/clean']['ns']:.1f} n {s['ALL/noise']['ns']:.1f} [{time.time()-t:.0f}s]"
            print(line, flush=True)
            with open(L.R3 / "logs" / "run.log", "a") as f:
                f.write(time.strftime("%H:%M:%S ") + line + "\n")
            st = stem(name)
            json.dump({"name": name, "summary": s}, open(L.R3 / "res" / f"{st}.json", "w"), indent=1)
            np.savez_compressed(L.R3 / "res" / f"{st}.npz", **{f"{r['key']}__{k}": v for r in res for k, v in r.items() if k != "key"})


if __name__ == "__main__":
    main()
