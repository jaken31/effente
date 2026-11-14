"""Checks that effente.band_model (the code that would ship) is what explore.py measured:
  1. window_row on the cached raw signals of a few clips gives the feature rows explore.py uses ("v1+alin");
  2. BandModel() with its default settings gives the same per-second scores and line as the frozen explore.py
     configuration on one condition;
  3. save/load as JSON gives the same scores.

    python -m training.record.sscc_eval.check_band_model
"""

from __future__ import annotations

import tempfile
import zlib
from pathlib import Path

import numpy as np

from effente import band_model as BM
from training.record.sscc_eval import common as C
from training.record.sscc_eval import explore as E
from training.record.sscc_eval import make_features as MF

FINAL = "v1+alin|fuse=mean,snr=6,q=0.995"


def check_rows(folder="normal__heavy_vel80_noise", clips=(0, 7)):
    d = np.load(MF.CACHE / f"{folder}.npz")
    data = E.data()
    worst = 0.0
    for i in clips:
        vib = d["vib"][i].astype(np.float64) * d["vib_scale"][i][None, :]
        aud = d["audio"][i].astype(np.float64)
        seed = zlib.crc32(f"{d['folder']}/{d['clip_id'][i]}".encode())
        a1 = MF.device_vib(vib[:, list(MF.TRI)], seed)
        gid = np.nonzero((data.meta["folder"] == str(d["folder"])) & (data.meta["clip_id"] == d["clip_id"][i]))[0]
        ref = E.rows("v1+alin", gid)[0][0]
        for k in range(5):
            r = BM.window_row(a1[k * 1000:(k + 1) * 1000], aud[k * 16000:(k + 1) * 16000])
            worst = max(worst, float(np.max(np.abs(r - ref[k]))))
    print(f"1. window_row vs explore rows: largest absolute difference {worst:.2e} (rows stored as float32)")
    return worst


def check_model(cond="light_vel80_noise"):
    d = E.data()
    learn, hold = C.split_normal(d, cond)
    RL, grp = E.rows("v1+alin", learn)
    RH = E.rows("v1+alin", hold)[0]
    flat = lambda a: a.reshape(-1, a.shape[-1])
    cfg = E.cfg_parse(FINAL)
    ref = E.make(cfg, grp, cfg["fs"]).learn(flat(RL))
    m = BM.BandModel().learn(flat(RL))
    a, b = ref.scores(flat(RH)), m.scores(flat(RH))
    print(f"2. BandModel() vs explore '{FINAL}' on {cond}: line {m.line:.6f} vs {ref.line:.6f}; scores largest relative "
          f"difference {float(np.max(np.abs(a - b) / b)):.2e}; vibration group used: {bool(m.active[:BM.N_VIB].any())}")
    with tempfile.TemporaryDirectory() as t:
        p = Path(t) / "m.json"
        m.save(p)
        m2 = BM.BandModel.load(p)
        c = m2.scores(flat(RH))
        size = p.stat().st_size
    print(f"3. after save/load: largest relative difference {float(np.max(np.abs(c - b) / b)):.2e}; file {size / 1e3:.0f} kB")


if __name__ == "__main__":
    check_rows()
    check_model()
