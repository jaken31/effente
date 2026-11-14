"""Writes the small SSCC sample the band model's unit test replays: one selection-set condition's learning rows,
held-out normal rows and 40 fault clips (10 per fault, every 10th of each in time order), with the benchmark's own
results for them (line, per-second scores, clip alarms) taken from the explore.py run of the frozen final model.

    python -m training.record.sscc_eval.make_test_sample
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from training.record.sscc_eval import common as C
from training.record.sscc_eval import explore as E

COND = "light_vel100_clean"
STEM = "v1-alin__fusemean_snr6_q0.995"
OUT = Path(__file__).resolve().parent / "tests" / "data" / f"sscc_{COND}_sample.npz"


def main():
    d = E.data()
    learn, hold = C.split_normal(d, COND)
    fids = C.time_order(d, d.idx(cond=COND, label=list(C.FAULTS)))
    lab = d.meta["label"][fids]
    pick = np.concatenate([np.nonzero(lab == f)[0][::10][:10] for f in C.FAULTS])
    saved = np.load(E.OUT / f"{STEM}.select.npz", allow_pickle=True)
    rows = lambda ids: E.rows("v1+alin", ids)[0].astype(np.float32)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        OUT, learn=rows(learn).reshape(-1, 231), hold=rows(hold).reshape(-1, 231), fault=rows(fids[pick]),
        fault_label=lab[pick].astype(str), line=np.array(float(saved[f"{COND}|line"])),
        hold_score=saved[f"{COND}|hold_score"].ravel(), fault_score=saved[f"{COND}|fault_score"][pick],
        hold_alarm=saved[f"{COND}|hold_stat"].max(1) > 1, fault_alarm=saved[f"{COND}|fault_stat"][pick].max(1) > 1)
    print(OUT, OUT.stat().st_size // 1000, "kB; fault clips alarmed", int((saved[f"{COND}|fault_stat"][pick].max(1) > 1).sum()),
          "of", len(pick), "; held-out clips alarmed", int((saved[f"{COND}|hold_stat"].max(1) > 1).sum()))


if __name__ == "__main__":
    main()
