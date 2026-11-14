"""Score the frozen candidate (round3_model.Round3Model, features recomputed from the cached 16 kHz audio) and the
two shipped presets with the Protocol A harness.
  python eval_final.py select   -> heavy + light (module check against the harness)
  python eval_final.py med      -> med load: the ONE confirmation run (refuses to run twice)
"""
import os
for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")
import json, sys, time
from multiprocessing import Pool
import numpy as np
import r3lib as L
import round3_model as RM

_SND = {}


def snd_rows(fname):
    """(clips, 5, 181) sound rows from the cached audio, clips in time order."""
    if fname not in _SND:
        d = np.load(L.BASE / "cache" / fname)
        o = np.argsort(d["t_start"].astype(float), kind="stable")
        a = d["audio"][o]
        _SND[fname] = np.array([[RM.sound_row(x[k * 16000:(k + 1) * 16000]) for k in range(5)] for x in a])
    return _SND[fname]


def job(args):
    which, key = args
    if which == "hyb60":
        def featfn(r, _k=None):
            raise RuntimeError
        # featfn needs the file name: wrap run_cond's raw() access
        orig_raw = L.raw

        def featfn(r):
            return np.concatenate([r["gen_v1"].reshape(len(r["gen_v1"]), 5, -1), r["_snd"]], -1)

        def raw2(fname):
            r = dict(orig_raw(fname))
            r["_snd"] = snd_rows(fname)
            return r
        L.raw = raw2
        try:
            return L.run_cond(key, featfn, lambda: RM.Round3Model(), keep_scores=True)
        finally:
            L.raw = orig_raw
    sys.path.insert(0, r"C:\Users\finnf\effente-sscc-work")
    from effente import band_model as BM

    def featfn(r):
        v = r["gen_v1"].reshape(r["gen_v1"].shape[0], 5, -1)
        snd = np.concatenate([r["alin"], r["gen_a"][..., 64:]], -1) if which == "lin50" else r["gen_a"]
        return np.concatenate([v, snd], -1)
    return L.run_cond(key, featfn, lambda: BM.make_model(which), keep_scores=True)


def main():
    which = sys.argv[1]
    loads = ("heavy", "light") if which == "select" else ("med",)
    if which == "med":
        flag = L.R3 / "logs" / "final_med.done"
        if flag.exists():
            sys.exit("med run already done once: " + flag.read_text())
        flag.write_text(time.strftime("%F %T started"))
    keys = [k for l in loads for k in L.cond_keys(l)]
    out = {}
    with Pool(12) as pool:
        for model in ("log64", "lin50", "hyb60"):
            res = pool.map(job, [(model, k) for k in keys])
            s = L.summarize(res)
            out[model] = {"summary": s, "per_cond": {r["key"]: {"fa": int((r["hold_max"] > 1).sum()), "n_hold": len(r["hold_max"]),
                          **{f: [int((r["fault_max"][r["labels"] == f] > 1).sum()), int((r["labels"] == f).sum())]
                             for f in L.FAULTS}} for r in res}}
            line = f"[{which}] {model:6s} | {L.fmt(s, loads)} | ALL {s['ALL']['fa']}/{s['ALL']['nh']} all {s['ALL']['all']:.1f} ns {s['ALL']['ns']:.1f} c {s['ALL/clean']['ns']:.1f} n {s['ALL/noise']['ns']:.1f}"
            print(line, flush=True)
            with open(L.R3 / "logs" / f"final_{which}.log", "a") as f:
                f.write(time.strftime("%F %T ") + line + "\n")
            np.savez_compressed(L.R3 / "res" / f"final_{which}_{model}.npz",
                                **{f"{r['key']}__{k}": v for r in res for k, v in r.items() if k != "key"})
    json.dump(out, open(L.R3 / "res" / f"final_{which}.json", "w"), indent=1)
    if which == "med":
        (L.R3 / "logs" / "final_med.done").write_text(time.strftime("%F %T finished"))


if __name__ == "__main__":
    main()
