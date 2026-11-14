"""The MPU-6500's own noise floor in the band model's vibration inputs, and how far SSCC's device-style vibration
sits above it.

The floor: zero motion through make_features.device_vib (300 ug/sqrt(Hz) datasheet noise, 218 Hz low-pass, 1 g on z,
1/4096 g counts), 1-second windows, effente.band_model.band_features; the median over 60 windows.

    python -m training.record.sscc_eval.vib_floor      -> results/mpu_floor.json + a per-condition SNR table
"""

from __future__ import annotations

import json

import numpy as np

from effente import band_model as BM
from training.record.sscc_eval import common as C
from training.record.sscc_eval import make_features as MF


def mpu_floor(n_clips: int = 12) -> np.ndarray:
    rows = []
    for s in range(n_clips):
        a = MF.device_vib(np.zeros((80000, 3)), seed=1000 + s)
        for k in range(5):
            rows.append(BM.band_features(a[k * 1000:(k + 1) * 1000], 1000.0, BM.VIB_EDGES))
    return np.median(np.array(rows), 0)


def main():
    floor = mpu_floor()
    (C.RESULTS / "mpu_floor.json").write_text(json.dumps({"floor": floor.tolist()}))
    d = C.load_data(("gen_v1",))
    band_cols = np.r_[0:20, 23:43, 46:66]
    print("per condition (normal clips): share of vibration bands at least 3 / 6 / 10 dB over the MPU floor; median SNR dB")
    for cond in sorted(set(d.meta["cond"])):
        ids = d.idx(cond=cond, label="normal")
        X = d.feats["gen_v1"][ids].reshape(-1, 69)
        snr = 10 * (np.median(X, 0) - floor)[band_cols]
        print(f"  {cond:22s} {np.mean(snr > 3):.2f} {np.mean(snr > 6):.2f} {np.mean(snr > 10):.2f}  median {np.median(snr):5.1f} "
              f"max {snr.max():5.1f}")
    X = d.feats["gen_v1"][d.idx(label="normal")].reshape(-1, 69)
    print("all normal: per band SNR dB (x, y, z):")
    print(np.round(10 * (np.median(X, 0) - floor)[band_cols].reshape(3, 20), 1))


if __name__ == "__main__":
    main()
