"""Cost on this PC of the device's own steps and of the recommended ones, to scale to the board.

The board's own figures (from the brief, measured there): the device PCA trains in about 25 s on a 10-30 minute
recording; one vibration feature pass takes about 8 ms. The same steps are timed here, so the PC-to-board ratio is
measured rather than guessed, and the new steps are scaled by it.

    python -m training.record.sscc_eval.timing
"""

from __future__ import annotations

import json
import time
import warnings

import numpy as np

from effente import features as F
from effente import trainer as TR
from effente.model_input import Prep
from training.record.sscc_eval import board_model as BM
from training.record.sscc_eval import common as C
from training.record.sscc_eval import make_features as MF


def best_of(f, n=5):
    ts = []
    for _ in range(n):
        t0 = time.perf_counter()
        f()
        ts.append(time.perf_counter() - t0)
    return min(ts)


def main():
    out = {}
    d = np.load(C.BASE / "cache" / "normal__heavy_vel100_clean.npz")
    vib = d["vib"][0].astype(float) * d["vib_scale"][0][None, :]
    aud = d["audio"][0].astype(float)
    acc = MF.device_vib(vib[:, 1:4], 1)[:1000]
    snd = aud[:16000]
    wv = F.Window(t0=0.0, accel_g=acc, accel_t=np.arange(1000) / 1000, accel_rate_hz=1000.0, accel_range_g=8.0)
    wa = F.Window(t0=0.0, accel_g=acc, accel_t=np.arange(1000) / 1000, accel_rate_hz=1000.0, accel_range_g=8.0,
                  audio=snd, audio_rate_hz=16000.0)
    out["device_features_vibration_only_ms"] = 1000 * best_of(lambda: F.extract(wv), 20)
    out["device_features_vib_and_sound_ms"] = 1000 * best_of(lambda: F.extract(wa), 20)
    out["generic_features_(1k vib 3 axes + 16k sound)_ms"] = 1000 * best_of(
        lambda: (MF.generic(acc, 1000, MF.V1_EDGES), MF.generic(snd / 32767, 16000, MF.A_EDGES)), 20)
    # a 30-minute recording: 1,800 one-second rows, made from SSCC rows (5 clips' worth repeated with jitter)
    feats = np.load(C.FEAT / "normal__heavy_vel100_clean.npz")
    X0 = feats["dev"].reshape(-1, F.N_FEATURES).astype(float)
    rng = np.random.default_rng(0)
    X = X0[rng.integers(0, len(X0), 1800)]
    X = X + rng.normal(0, 0.01, X.shape) * np.nan_to_num(np.abs(X))
    t = np.arange(1800, dtype=float)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        t0 = time.perf_counter()
        TR.train_model(t, X, list(F.FEATURE_NAMES))
        out["device_train_model_1800_rows_s"] = time.perf_counter() - t0
        prep = Prep.fit(X, list(F.FEATURE_NAMES), None, running_only=False)
    Z = prep.transform(X).astype(float)
    names = prep.names_out
    groups = [np.array([i for i, n in enumerate(names) if n.startswith(p)]) for p in ("vib", "snd")]
    t0 = time.perf_counter()
    m, line = BM.learn(Z, groups)
    out["groupgauss_learn_1800_rows_device_features_s"] = time.perf_counter() - t0
    out["groupgauss_inputs"] = int(sum(len(g) for g in groups))
    out["groupgauss_judge_one_row_ms"] = 1000 * best_of(lambda: m.score(Z[:1]), 50)
    G = np.hstack([rng.normal(size=(1800, 69)), rng.normal(size=(1800, 67))])
    gg = [np.arange(69), np.arange(69, 136)]
    t0 = time.perf_counter()
    m2, _ = BM.learn(G, gg)
    out["groupgauss_learn_1800_rows_generic_features_s"] = time.perf_counter() - t0
    out["groupgauss_generic_judge_one_row_ms"] = 1000 * best_of(lambda: m2.score(G[:1]), 50)
    from training.record.sscc_eval import methods as M

    def learn_fused_pca(A, grp):
        b = np.arange(len(A)) * 5 // len(A)
        cal = [BM.median3(M.Fused(lambda: M.PCA(0.95, 40, True), grp, "max").fit(A[b != k]).score(A[b == k]))
               for k in range(5)]
        return M.Fused(lambda: M.PCA(0.95, 40, True), grp, "max").fit(A), float(np.quantile(np.concatenate(cal), 0.99))

    t0 = time.perf_counter()
    fp, _ = learn_fused_pca(G, gg)
    out["FINAL_group_pca_learn_1800_rows_136_inputs_s"] = time.perf_counter() - t0
    out["FINAL_group_pca_judge_one_row_ms"] = 1000 * best_of(lambda: fp.score(G[:1]), 50)
    out["FINAL_group_pca_memory_bytes"] = int(sum(m.V.nbytes + m.mu.nbytes + m.hs.nbytes for m in fp.models))
    X3 = np.vstack([G] * 2)
    t0 = time.perf_counter()
    learn_fused_pca(X3, gg)
    out["FINAL_group_pca_learn_3600_rows_s"] = time.perf_counter() - t0
    out["model_memory_bytes_device_features"] = int(sum(mm.P.nbytes + mm.mu.nbytes for mm in m.models))
    out["model_memory_bytes_generic_features"] = int(sum(mm.P.nbytes + mm.mu.nbytes for mm in m2.models))
    r_train = 25.0 / out["device_train_model_1800_rows_s"]
    r_feat = 8.0 / out["device_features_vibration_only_ms"]
    out["board_over_pc_ratio_from_training"] = r_train
    out["board_over_pc_ratio_from_vibration_features"] = r_feat
    out["note"] = ("ratios use the brief's board figures (PCA training about 25 s, vibration features about 8 ms); "
                   "the training ratio assumes the board's 25 s was for a recording of about 1,800 one-second rows")
    print(json.dumps(out, indent=1))
    (C.RESULTS / "timing.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
