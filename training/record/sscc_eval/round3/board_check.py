"""Run round3_model (hyb60) on a raw SSCC sample, on this computer or on the board, and compare with a reference.
  python hyb_check.py <dir of round3_model.py> <sample.npz> <ref.npz> make     (PC: writes the reference)
  python hyb_check.py <dir of round3_model.py> <sample.npz> <ref.npz> check    (board: compares with it)
Features from the raw audio and acceleration, learning on the first 60 % of the normal clips, judging the last
35 % and the fault clips second by second (judge(), reset per clip), saving and loading the model, timings."""
import json
import os
import sys
import tempfile
import time

import numpy as np

sys.path.insert(0, sys.argv[1])
import round3_model as RM  # noqa: E402

sample, ref_path, mode = sys.argv[2], sys.argv[3], sys.argv[4]
d = np.load(sample)
normal, fault, acc = d["normal"], d["fault"], d["acc"]

t0 = time.perf_counter()
k = 0
rows = []
for clips in (normal, fault):
    for x in clips:
        for s in range(5):
            rows.append(RM.window_row(acc[k], x[s * 16000:(s + 1) * 16000]))
            k += 1
rows = np.array(rows)
feat_ms = 1000 * (time.perf_counter() - t0) / len(rows)
Rn, Rf = rows[:5 * len(normal)].reshape(len(normal), 5, -1), rows[5 * len(normal):].reshape(len(fault), 5, -1)
n_learn, n_gap = int(0.60 * len(Rn)), int(0.05 * len(Rn))
learn, held = Rn[:n_learn].reshape(-1, rows.shape[1]), Rn[n_learn + n_gap:]

t0 = time.perf_counter()
m = RM.Round3Model().learn(learn)
learn_s = time.perf_counter() - t0


def clips_alarmed(model, C):
    n, sc = 0, []
    for clip in C:
        model.reset()
        j = [model.judge(r) for r in clip]
        sc += [x["score"] for x in j]
        n += any(x["alarm"] for x in j)
    return n, np.array(sc)


t0 = time.perf_counter()
fa, s_held = clips_alarmed(m, held)
caught, s_fault = clips_alarmed(m, Rf)
judge_ms = 1000 * (time.perf_counter() - t0) / (5 * (len(held) + len(Rf)))

with tempfile.TemporaryDirectory() as tmp:
    p = os.path.join(tmp, "hyb60.npz")
    m.save(p)
    size_kb = os.path.getsize(p) / 1000
    m2 = RM.Round3Model.load(p)
fa2, s_held2 = clips_alarmed(m2, held)
caught2, s_fault2 = clips_alarmed(m2, Rf)

# learning 30 minutes: the learning rows repeated with a little noise
rng = np.random.default_rng(0)
big = np.tile(learn, (int(np.ceil(1800 / len(learn))), 1))[:1800]
big = big + rng.normal(0, 0.02, big.shape)
t0 = time.perf_counter()
RM.Round3Model().learn(big)
learn30_s = time.perf_counter() - t0

out = {"inputs": int(rows.shape[1]), "groups_used": len(m.groups), "learn_rows": int(len(learn)), "line": float(m.line),
       "false_alarms": f"{fa}/{len(held)}", "fault_clips_caught": f"{caught}/{len(Rf)}",
       "after_save_load": {"false_alarms": f"{fa2}/{len(held)}", "fault_clips_caught": f"{caught2}/{len(Rf)}",
                           "largest_score_change": float(max(np.max(np.abs(s_held2 / s_held - 1)),
                                                             np.max(np.abs(s_fault2 / s_fault - 1))))},
       "features_ms_per_second": round(feat_ms, 2), "learn_s": round(learn_s, 2), "learn_30min_s": round(learn30_s, 2),
       "judge_ms": round(judge_ms, 2), "file_kB": round(size_kb), "numpy": np.__version__}
if mode == "make":
    np.savez_compressed(ref_path, rows=rows.astype(np.float64), line=m.line, s_held=s_held, s_fault=s_fault,
                        fa=fa, caught=caught)
else:
    r = np.load(ref_path)
    out["against_the_PC"] = {
        "features_largest_abs_difference": float(np.max(np.abs(rows - r["rows"]))),
        "line_relative_difference": float(abs(m.line / float(r["line"]) - 1)),
        "scores_largest_relative_difference": float(max(np.max(np.abs(s_held / r["s_held"] - 1)),
                                                        np.max(np.abs(s_fault / r["s_fault"] - 1)))),
        "same_alarms": bool(fa == int(r["fa"]) and caught == int(r["caught"]))}
print(json.dumps(out, indent=1))
