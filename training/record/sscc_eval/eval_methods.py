"""Alternatives to the device's model on SSCC Protocol A (same split as eval_device.py).

Per condition and per configuration (features x model):
  - cal: out-of-sample scores of the learning clips (two halves in time; each half scored by a model learned
         on the other), from which a line is set using learning data only, as the device must;
  - hold / fault: scores of the held-out normal clips and the fault clips by the model learned on all
         learning clips.
Saved raw to results/A/<config>.npz; report.py turns them into tables.

    python -m training.record.sscc_eval.eval_methods [--only <config substring>] [--workers 12]
"""

from __future__ import annotations

import argparse
import sys
import time
from multiprocessing import Pool

import numpy as np

from training.record.sscc_eval import common as C
from training.record.sscc_eval import methods as M

_DATA = None
FOLDS = 5  # the learning clips' out-of-sample scores: 5 blocks in time, each scored by a model learned on the rest


def data():
    global _DATA
    if _DATA is None:
        _DATA = C.load_data()
    return _DATA


# ----------------------------------------------------------------- feature sets

def dev_names():
    from effente import features as F
    return list(F.FEATURE_NAMES)


def raw_rows(fs: str, ids: np.ndarray) -> np.ndarray:
    """(clips, 5, d) raw features of a feature set."""
    D = data().feats
    if fs.startswith("dev"):
        return D["dev"][ids].astype(float)
    parts = []
    for p in fs.split("+"):
        if p == "g16":
            parts.append(D["gen_v16"][ids][:, :, 1:4].reshape(len(ids), 5, -1))
        elif p == "g16m":  # the motor's single-axis channel (column 0), full bandwidth
            parts.append(D["gen_v16"][ids][:, :, 0].reshape(len(ids), 5, -1))
        elif p == "g16all":
            parts.append(D["gen_v16"][ids].reshape(len(ids), 5, -1))
        elif p == "g1":
            parts.append(D["gen_v1"][ids].reshape(len(ids), 5, -1))
        elif p == "g1c":
            parts.append(D["gen_v1c"][ids].reshape(len(ids), 5, -1))
        elif p == "ga":
            parts.append(D["gen_a"][ids].reshape(len(ids), 5, -1))
        elif p in ("ga32", "ga32b"):  # the same sound with the device's 32 bands (pairs of the 64 summed)
            a = D["gen_a"][ids].astype(float)
            b32 = np.log10(10 ** a[..., 0:64:2] + 10 ** a[..., 1:64:2])
            parts.append(b32 if p == "ga32b" else np.concatenate([b32, a[..., 64:]], -1))
        elif p == "gab":  # 64 bands only, without log rms, kurtosis and crest
            parts.append(D["gen_a"][ids][..., :64].astype(float))
        elif p == "g1tot":  # the device's vibration bands: power summed over the 3 axes, plus each axis' log rms
            v = D["gen_v1"][ids].astype(float)
            tot = np.log10((10 ** v[..., :20]).sum(2))
            parts.append(np.concatenate([tot, v[..., :, 20]], -1))
        else:
            raise ValueError(p)
    return np.concatenate(parts, 2).astype(float)


def group_of_generic(fs: str) -> list[str]:
    sizes = {"g16": 105, "g16m": 35, "g16all": 140, "g1": 69, "g1c": 69, "ga": 67, "ga32": 35, "ga32b": 32, "gab": 64,
             "g1tot": 23}
    out = []
    for p in fs.split("+"):
        out += ["snd" if p.startswith("ga") else "vib"] * sizes[p]
    return out


class Prepared:
    """Fits the preparation on learning rows: effente's Prep for device features (as the device), a robust z
    (median / IQR, clipped at +-8) for the generic ones. Also gives the sensor group of every prepared column."""

    def __init__(self, fs: str):
        self.fs = fs

    def fit(self, R):
        if self.fs.startswith("dev"):
            from effente.model_input import Prep
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                self.prep = Prep.fit(R, dev_names(), None, running_only=False)
            names = self.prep.names_out
            grp = np.array(["vib" if n.startswith("vib") else "snd" if n.startswith("snd") else "other" for n in names])
            keep = np.ones(len(names), bool)
            if self.fs == "dev_vib":
                keep = grp == "vib"
            elif self.fs == "dev_snd":
                keep = grp == "snd"
            elif self.fs == "dev_bands":  # only the band levels (and per-axis rms / overall sound level)
                keep = np.array([("_band_" in n) or n in ("vib_rms_x", "vib_rms_y", "vib_rms_z", "snd_level_db")
                                 for n in names])
            elif self.fs == "dev_nobands":
                keep = np.array([("_band_" not in n) and g != "other" for n, g in zip(names, grp)])
            self.keep = np.nonzero(keep)[0]
            self.groups_ = grp[self.keep]
            self.names_ = [names[i] for i in self.keep]
        else:
            self.med = np.median(R, 0)
            q1, q3 = np.percentile(R, [25, 75], 0)
            sd = (q3 - q1) / 1.349
            sd = np.where(sd > 1e-9, sd, R.std(0))
            self.keep = np.nonzero(sd > 1e-9)[0]
            self.med, self.sd = self.med[self.keep], sd[self.keep]
            self.groups_ = np.array(group_of_generic(self.fs))[self.keep]
            self.names_ = [f"{g}{i}" for g, i in zip(self.groups_, self.keep)]
        return self

    def transform(self, R):
        if self.fs.startswith("dev"):
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                return self.prep.transform(R)[:, self.keep].astype(float)
        return np.clip((R[:, self.keep] - self.med) / self.sd, -8, 8)

    def groups(self):
        return [np.nonzero(self.groups_ == g)[0] for g in ("vib", "snd") if (self.groups_ == g).any()]


HAND = ("vib_rms_x", "vib_rms_y", "vib_rms_z", "vib_velocity_mm_s", "vib_peak_g", "vib_kurtosis", "vib_crest",
        "snd_level_db", "snd_kurt_hf", "snd_peak_db", "snd_centroid_hz", "snd_flatness", "snd_tone_db")


def make_model(spec: str, prep: Prepared):
    """Model from a spec string."""
    g = prep.groups()
    if spec == "pca95_t2":
        return M.PCA(0.95, 40, True)
    if spec == "pca95_res":
        return M.PCA(0.95, 40, False)
    if spec == "pca99_t2":
        return M.PCA(0.99, 200, True)
    if spec == "pca99_res":
        return M.PCA(0.99, 200, False)
    if spec == "pca90_res":
        return M.PCA(0.90, 200, False)
    if spec.startswith("pcabal"):  # pcabal_<var>_<t2|res>_<mean|max>: one PCA, per-group residual levels
        _, v, t, c = spec.split("_")
        return M.PCA(int(v) / 100, 200 if v != "95" else 40, t == "t2", groups=g, combine=c)
    if spec == "gauss":
        return M.Gauss()
    if spec.startswith("knn"):  # knn<k>[c]
        k = int(spec[3:].rstrip("c"))
        return M.KNN(k, "cosine" if spec.endswith("c") else "euclidean")
    if spec == "iforest":
        return M.IForest()
    if spec == "ae":
        return M.AE()
    if spec == "maxz":
        return M.MaxZ()
    if spec == "hand":
        cols = [i for i, n in enumerate(prep.names_) if n in HAND]
        return M.MaxZ(cols)
    if spec.startswith("fused_"):  # fused_<base>_<combine>
        base, comb = spec[len("fused_"):].rsplit("_", 1)
        return M.Fused(lambda: make_model(base, prep), g, comb)
    raise ValueError(spec)


def fit_score(fs: str, spec: str, Rtr: np.ndarray, Rte_list: list[np.ndarray]):
    """Prepare and fit on learning rows (rows x d), score each test array (rows x d)."""
    prep = Prepared(fs).fit(Rtr)
    m = make_model(spec, prep).fit(prep.transform(Rtr))
    return [m.score(prep.transform(R)) for R in Rte_list]


def run_config(cfg: str) -> str:
    fs, spec = cfg.split("|")
    t0 = time.perf_counter()
    d = data()
    out = {}
    fit_times = []
    for cond in C.fault_conditions(d):
        learn, hold = C.split_normal(d, cond)
        fids = C.time_order(d, d.idx(cond=cond, label=list(C.FAULTS)))
        RL, RH, RF = raw_rows(fs, learn), raw_rows(fs, hold), raw_rows(fs, fids)
        flat = lambda a: a.reshape(-1, a.shape[-1])
        fold = np.arange(len(learn)) * FOLDS // len(learn)  # FOLDS contiguous blocks of clips, in time order
        cal = np.zeros((len(learn), 5))
        for q in range(FOLDS):
            cal[fold == q] = fit_score(fs, spec, flat(RL[fold != q]), [flat(RL[fold == q])])[0].reshape(-1, 5)
        ft = time.perf_counter()
        sh, sf = fit_score(fs, spec, flat(RL), [flat(RH), flat(RF)])
        fit_times.append(time.perf_counter() - ft)
        out[f"{cond}|cal"] = cal
        out[f"{cond}|hold"] = sh.reshape(len(hold), 5)
        out[f"{cond}|fault"] = sf.reshape(len(fids), 5)
        out[f"{cond}|labels"] = d.meta["label"][fids]
    out["fit_and_score_seconds_mean"] = np.array(np.mean(fit_times))
    path = C.A_DIR / (cfg.replace("|", "__").replace("+", "-") + ".npz")
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(path, **out)
    return f"{cfg}: {time.perf_counter() - t0:.0f} s"


CONFIGS = [
    # the device's features, alternative models
    "dev|pca95_t2", "dev|pca95_res", "dev|pca99_t2", "dev|pca99_res", "dev|pca90_res",
    "dev|pcabal_95_t2_mean", "dev|pcabal_95_res_mean", "dev|pcabal_95_res_max", "dev|pcabal_99_res_mean",
    "dev|pcabal_99_res_max", "dev|pcabal_99_t2_max",
    "dev|gauss", "dev|knn1", "dev|knn5", "dev|knn1c", "dev|iforest", "dev|ae", "dev|maxz", "dev|hand",
    "dev|fused_knn1_max", "dev|fused_knn1_z", "dev|fused_knn1_ecdf", "dev|fused_pca95_res_max",
    "dev|fused_pca99_res_max", "dev|fused_gauss_max", "dev|fused_gauss_z", "dev|fused_maxz_max",
    # one sensor at a time (device features)
    "dev_vib|pca95_t2", "dev_vib|knn1", "dev_vib|gauss", "dev_snd|pca95_t2", "dev_snd|knn1", "dev_snd|gauss",
    # generic features: bandwidth and sensor what-ifs
    "g16|knn1", "g16|gauss", "g16|pca99_res", "g16all|knn1", "g16m|knn1",
    "g1|knn1", "g1|gauss", "g1|pca99_res", "g1c|knn1", "g1c|gauss",
    "ga|knn1", "ga|gauss", "ga|pca99_res",
    "g16+ga|knn1", "g16+ga|fused_knn1_max", "g16+ga|fused_gauss_max", "g16+ga|gauss",
    "g1+ga|knn1", "g1+ga|fused_knn1_max", "g1+ga|fused_gauss_max", "g1+ga|gauss",
]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--configs", default="")
    ap.add_argument("--workers", type=int, default=12)
    args = ap.parse_args(argv)
    cfgs = args.configs.split(",") if args.configs else [c for c in CONFIGS if args.only in c]
    with Pool(args.workers) as pool:
        for msg in pool.imap_unordered(run_config, cfgs):
            print(msg, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
