"""PC timings of effente.band_model (both presets): features of one second, learning on 30 / 60 minutes of rows,
judging one second, model file size. The board estimate uses the first round's measured board/PC ratios
(SSCC_RESULTS.md: about x16-18 for the PCA training step, about x11 for features); rough.

    python -m training.record.sscc_eval.timing2      -> results/timing2.json
"""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

import numpy as np

from effente import band_model as BM
from training.record.sscc_eval import common as C


def main():
    rng = np.random.default_rng(0)
    acc = rng.normal(0, 0.005, (1000, 3))
    acc[:, 2] += 1
    snd = rng.normal(0, 300, 16000)
    out = {}
    for preset, edges in (("lin50", BM.SND_EDGES), ("log64", BM.SND_EDGES_LOG64)):
        t0 = time.perf_counter()
        for _ in range(200):
            r = BM.window_row(acc, snd, snd_edges=edges)
        t_feat = (time.perf_counter() - t0) / 200
        res = {"features_ms": 1000 * t_feat, "n_inputs": int(len(r))}
        for minutes in (30, 60):
            n = minutes * 60
            # rows with a realistic structure: smooth spectra plus noise, and the vibration over the MPU floor so
            # that both groups are learned (the slower case)
            base = np.r_[BM.MPU_FLOOR + 1.0, np.linspace(-6, -9, len(r) - BM.N_VIB)]
            R = base + rng.normal(0, 0.1, (n, len(r))) + rng.normal(0, 0.1, (n, 1)) * np.linspace(0, 1, len(r))
            t0 = time.perf_counter()
            m = BM.make_model(preset).learn(R)
            res[f"learn_{minutes}min_s"] = time.perf_counter() - t0
        t0 = time.perf_counter()
        for i in range(300):
            m.judge(R[i])
        res["judge_ms"] = 1000 * (time.perf_counter() - t0) / 300
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "m.json"
            m.save(p)
            res["model_kB"] = p.stat().st_size / 1e3
        res["vibration_group_used"] = bool(m.active[:BM.N_VIB].any())
        res["board_estimate"] = {"learn_30min_s": res["learn_30min_s"] * 17, "learn_60min_s": res["learn_60min_s"] * 17,
                                 "judge_ms": res["judge_ms"] * 17, "features_ms_if_on_linux": res["features_ms"] * 11}
        out[preset] = res
        print(preset, json.dumps(res, indent=1))
    (C.RESULTS / "timing2.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
