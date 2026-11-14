"""A synthetic "yell" on SSCC normal clips: how far does a loud, short, sound-only event move each model?

On the real board a yell 26 dB over the room only moved the device's model to 0.73 of its line (sound is about 45
of ~100 inputs scored together). Here, for every held-out normal clip of the selection-set conditions, a voice-like
harmonic sound (f0 220 Hz with 3 % vibrato, 10 harmonics at 1/k, 50 ms fades) is added to seconds 2-3 (two whole
windows) at +26 dB over the clip's own sound rms. Vibration is untouched. Reported per model: the share of yelled
clips that alarm, and the median score over the line in the yell seconds (overall and, for the recommended model,
the sound group's own score over its learning median).

    python -m training.record.sscc_eval.yell_test
"""

from __future__ import annotations

import json
import warnings

import numpy as np

from effente import features as F
from effente import model_runtime as RT
from training.record.sscc_eval import common as C
from training.record.sscc_eval import eval_device as ED
from training.record.sscc_eval import eval_methods as EM
from training.record.sscc_eval import make_features as MF

RATE = 16000
YELL_DB = 26.0
SECONDS = (1, 2)  # windows 1 and 2 (the clip's 2nd and 3rd second)


def yell(n: int, rng) -> np.ndarray:
    t = np.arange(n) / RATE
    f0 = 220 * (1 + 0.03 * np.sin(2 * np.pi * 5 * t + rng.uniform(0, 6)))
    ph = 2 * np.pi * np.cumsum(f0) / RATE
    y = sum(np.sin(k * ph) / k for k in range(1, 11))
    env = np.minimum(1, np.minimum(t, t[-1] - t) / 0.05)
    y = y * env
    return y / np.sqrt(np.mean(y ** 2))


def main():
    data = C.load_data()
    cache = {}
    out = {}
    rng = np.random.default_rng(0)
    for cond in [c for c in C.fault_conditions(data) if C.in_set(c, "select")] if hasattr(C, "in_set") else \
            [c for c in C.fault_conditions(data) if not c.startswith(C.CONFIRM_LOAD)]:
        learn, hold = C.split_normal(data, cond)
        # yelled copies of the held-out clips' features
        dev = data.feats["dev"][hold].astype(float).copy()
        gen = data.feats["gen_a"][hold].astype(float).copy()
        folder = data.meta["folder"][hold]
        for i, idx in enumerate(hold):
            f = folder[i]
            if f not in cache:
                cache = {f: np.load(C.BASE / "cache" / (f.replace("/", "__") + ".npz"))}
            d = cache[f]
            j = int(np.nonzero(d["clip_id"] == data.meta["clip_id"][idx])[0][0])
            aud = d["audio"][j].astype(float)
            seg = slice(SECONDS[0] * RATE, (SECONDS[-1] + 1) * RATE)
            level = np.sqrt(np.mean(aud ** 2))
            aud[seg] = np.clip(aud[seg] + yell(seg.stop - seg.start, rng) * level * 10 ** (YELL_DB / 20), -32767, 32767)
            for k in SECONDS:
                snd = aud[k * RATE:(k + 1) * RATE]
                x = F.extract(F.Window(t0=float(k), audio=snd, audio_rate_hz=float(RATE)))
                keep = dev[i, k, F.INDEX["snd_env_order"]]
                dev[i, k, F.SLICES["sound"]] = x[F.SLICES["sound"]]
                dev[i, k, F.INDEX["snd_env_order"]] = keep
                gen[i, k] = MF.generic(snd / 32767.0, RATE, MF.A_EDGES)[0]
        # the device as it is
        dmodel, _ = ED.train(data, learn)
        m = RT.Model(dmodel)
        thr, spike = m.threshold, m.spike_line
        s = m.score(dev.reshape(-1, F.N_FEATURES))[0].reshape(len(hold), 5)
        s0 = m.score(data.feats["dev"][hold].astype(float).reshape(-1, F.N_FEATURES))[0].reshape(len(hold), 5)
        med = np.array([C.running_median(np.r_[[np.median(s0) / thr] * 4, r / thr], 5)[4:] for r in s])
        dev_alarm = (med > 1).any(1) | (s / spike > 1).any(1)
        # the recommended model
        RL = EM.raw_rows("g1+ga", learn)
        flat = lambda a: a.reshape(-1, a.shape[-1])
        fold = np.arange(len(learn)) * 5 // len(learn)
        cal = [C.running_median(EM.fit_score("g1+ga", "fused_pca95_t2_max", flat(RL[fold != q]),
                                             [flat(RL[fold == q])])[0], 3) for q in range(5)]
        line = float(np.quantile(np.concatenate(cal), 0.99))
        prep = EM.Prepared("g1+ga").fit(flat(RL))
        model = EM.make_model("fused_pca95_t2_max", prep).fit(prep.transform(flat(RL)))
        RH = EM.raw_rows("g1+ga", hold)
        RH[..., 69:] = gen  # the yelled sound
        Zh = prep.transform(flat(RH))
        fs = model.score(Zh).reshape(len(hold), 5) / line
        parts = model.parts(Zh)
        snd_part = (parts[1] / np.median(model.oof[1])).reshape(len(hold), 5)
        pre = float(np.median(fs[:, [0, 3, 4]]))  # typical normal history
        fmed = np.array([C.running_median(np.r_[pre, pre, r], 3)[2:] for r in fs])
        fin_alarm = (fmed > 1).any(1)
        out[cond] = {"clips": len(hold),
                     "device_alarm_share": float(dev_alarm.mean()),
                     "device_score_over_line_in_yell_s": float(np.median(s[:, list(SECONDS)] / thr)),
                     "device_score_over_spike_line_in_yell_s": float(np.median(s[:, list(SECONDS)] / spike)),
                     "device_score_over_line_without_yell": float(np.median(s0[:, list(SECONDS)] / thr)),
                     "final_alarm_share": float(fin_alarm.mean()),
                     "final_score_over_line_in_yell_s": float(np.median(fs[:, list(SECONDS)])),
                     "final_sound_group_over_its_median_in_yell_s": float(np.median(snd_part[:, list(SECONDS)]))}
        print(cond, json.dumps({k: round(v, 3) for k, v in out[cond].items()}), flush=True)
    (C.RESULTS / "yell_test.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        main()
