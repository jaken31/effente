"""Optional: frozen pretrained audio embeddings (CED-tiny, mispeech/ced-tiny, 5.4 M parameters) per 1-second window.

For every clip in the cache: the Zoom sound at 16 kHz, and the tri-axis vibration channels (CSV ch1-3) at 16 kHz
(times a fixed 10, not clipped, so their level sits in the model's range; one constant for every clip, so level differences
stay), each one second through CED-tiny's encoder, mean over its 24 tokens -> 192 numbers.
Writes C:/Users/finnf/effente-sscc/features_ced/<folder>.npz with ced_a (n, 5, 192) and ced_v (n, 5, 3, 192).

Needs torch, torchaudio and transformers in the venv (CPU wheels), and downloads the model's official weights and
code from Hugging Face (trust_remote_code: the authors' own repository).

    HF_HOME=C:/Users/finnf/effente-sscc/hf python -m training.record.sscc_eval.embed_ced
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

BASE = Path(r"C:\Users\finnf\effente-sscc")
OUT = BASE / "features_ced"
VIB_GAIN = 10.0


def main() -> int:
    import torch
    import transformers
    from transformers import AutoFeatureExtractor, AutoModelForAudioClassification
    transformers.logging.set_verbosity_error()
    torch.set_num_threads(14)
    name = "mispeech/ced-tiny"
    fe = AutoFeatureExtractor.from_pretrained(name, trust_remote_code=True)
    model = AutoModelForAudioClassification.from_pretrained(name, trust_remote_code=True).eval()
    OUT.mkdir(exist_ok=True)

    def embed(wins: np.ndarray) -> np.ndarray:
        out = []
        for i in range(0, len(wins), 256):
            inp = fe(list(wins[i:i + 256].astype(np.float32)), sampling_rate=16000, return_tensors="pt")
            with torch.no_grad():
                out.append(model.encoder(inp["input_values"]).logits.mean(1).numpy())
        return np.concatenate(out)

    for p in sorted((BASE / "cache").glob("*.npz")):
        out = OUT / p.name
        if out.exists():
            continue
        d = np.load(p)
        n = len(d["clip_id"])
        a = d["audio"].astype(np.float32) / 32767.0
        ea = embed(a.reshape(n * 5, 16000)).reshape(n, 5, -1)
        v = d["vib"][:, :, 1:4].astype(np.float32) * d["vib_scale"][:, None, 1:4].astype(np.float32) * VIB_GAIN
        v = v.reshape(n, 5, 16000, 3).transpose(0, 1, 3, 2).reshape(n * 5 * 3, 16000)
        ev = embed(v).reshape(n, 5, 3, -1)
        np.savez(out, ced_a=ea.astype(np.float32), ced_v=ev.astype(np.float32), clip_id=d["clip_id"])
        print(p.name, n, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
