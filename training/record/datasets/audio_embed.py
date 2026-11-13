#!/usr/bin/env python3
"""Pretrained audio embeddings (BEATs, EfficientAT mn10_as) against our device's own sound features.

Question: do embeddings from a network pretrained on general audio detect real machine faults better
than effente's hand-built sound features (effente/features.py), with the SAME detectors, and could a
small model run on the UNO Q?

Three steps, each a sub-command:

  embed     one clip -> one vector per layer (time-mean pooled), for every clip of a source:
              dcase  a DCASE Task 2 dev_<machine>.zip (10 s, 16 kHz clips, normal train, normal+anomaly test)
              sscc   an unpacked SSCC tree (the Zoom H5 audio_part_*.wav clips; read as sscc.py reads them)
            Resumable: progress is saved every 50 clips next to --out.
  evaluate  DCASE: kNN (k = 5/10/20, as the SSCC benchmark's fd_knn.py) and PCA residual on each
            representation; AUROC, pAUC (FPR <= 0.1), per domain, and catches at zero false alarms with the
            threshold set from training normals only (max of 5-fold out-of-sample training scores).
  cost      time per 10 s clip on this machine's CPU with ONE thread, parameters, file size, multiply-adds,
            peak memory; float32 and dynamic int8.

    nice -n 10 python3 -m training.record.datasets.audio_embed embed dcase data/real/dcase2024-fan/dev_fan.zip \\
        --model beats --out data/real/dcase2024-fan/embed/beats.npz
    nice -n 10 python3 -m training.record.datasets.audio_embed embed dcase data/real/dcase2024-fan/dev_fan.zip \\
        --model mn10 --out data/real/dcase2024-fan/embed/mn10.npz
    python3 -m training.record.datasets.audio_embed evaluate data/real/dcase2024-fan/embed \\
        --features data/real/dcase2024-fan/features.npz
    python3 -m training.record.datasets.audio_embed cost --model beats

For SSCC (once its data is unpacked): `embed sscc <root> --model beats --out ...`, then call
`compare(reps, train, y, groups)` with SSCC's own condition-level split (sscc.py's note: split by
condition, not by clip). The paper's BEATs audio AUROC there is 0.868 (FISHER 0.954).

Weights are not in git. Downloaded (13 Nov 2026) from the official sources only:
  data/models/beats/BEATs_iter3_plus_AS2M.pt   Microsoft unilm BEATs README link (OneDrive), 361,499,833 bytes,
                                               sha256 d43cbfad4d7b56381c061d7a24774f908d4d94c72961f6eb1d9090ff18cd8d34
  data/models/beats/code/{BEATs,backbone,modules}.py   github.com/microsoft/unilm, beats/, master
  data/models/efficientat/mn10_as_mAP_471.pt   github.com/fschmid56/EfficientAT releases v0.0.1, 19,708,753 bytes,
                                               sha256 0bd7dc2443af498c289a2e739f02ebb515d6aa3fd3ab9db539c86123ae368a4e
  data/models/efficientat/code/                git clone --depth 1 (commit a425fdce), MIT licence
BEATs: MIT licence. DCASE 2024 Task 2 data: CC BY-NC-SA 4.0.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import resource
import sys
import time
import wave
import zipfile
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[3]
BEATS_CKPT = REPO / "data/models/beats/BEATs_iter3_plus_AS2M.pt"
BEATS_CODE = REPO / "data/models/beats/code"
MN_CKPT = REPO / "data/models/efficientat/mn10_as_mAP_471.pt"
MN_CODE = REPO / "data/models/efficientat/code"
RATE = 16000


# ----------------------------------------------------------------------------------------- clips

def _wav16(data: bytes) -> tuple[np.ndarray, int]:
    with wave.open(io.BytesIO(data)) as w:
        rate, width, ch = w.getframerate(), w.getsampwidth(), w.getnchannels()
        raw = w.readframes(w.getnframes())
    if width != 2:
        raise ValueError(f"expected 16-bit samples, got {8 * width}-bit")
    return np.frombuffer(raw, "<i2").reshape(-1, ch)[:, 0].astype(np.float32) / 32768.0, rate


def dcase_clips(zip_path: str):
    """(name, meta, waveform in [-1, 1] at 16 kHz) for every clip of a DCASE Task 2 dev zip."""
    pat = re.compile(r"section_(\d+)_(source|target)_(train|test)_(normal|anomaly)")
    with zipfile.ZipFile(zip_path) as zf:
        for name in sorted(n for n in zf.namelist() if n.lower().endswith(".wav")):
            m = pat.search(name)
            if not m:
                continue
            x, rate = _wav16(zf.read(name))
            if rate != RATE:
                raise ValueError(f"{name}: {rate} Hz, expected {RATE}")
            yield Path(name).name, {"section": m.group(1), "domain": m.group(2), "split": m.group(3),
                                    "label": m.group(4)}, x


def sscc_clips(root: str):
    """(clip_id, meta, waveform) for every SSCC Zoom clip, read exactly as sscc.py reads it (first channel,
    resampled to 16 kHz). clip_id matches sscc.py's ("<condition folder>/<index>"). UNVERIFIED on real
    SSCC files: written before the download arrived."""
    from training.record.datasets import sscc
    root = Path(root)
    for cond in sscc.find_conditions(root):
        info = sscc.parse_condition(cond)
        rel = cond.relative_to(root)
        for wav in sorted((cond / "audio").glob("audio_part_*.wav")):
            idx = wav.stem.replace("audio_part_", "")
            x = (sscc.read_audio(wav) / 32768.0).astype(np.float32)
            yield f"{rel}/{idx}", {k: str(v) for k, v in info.items()}, x


# ----------------------------------------------------------------------------------------- models

class Beats:
    """BEATs iter3+ AS2M (self-supervised, not fine-tuned). Every transformer layer's output, time-mean
    pooled, as the SSCC benchmark's extractor does for the last one (DC removed, <= 10 s windows)."""
    name = "beats"
    max_samples = 10 * RATE

    def __init__(self, ckpt=BEATS_CKPT, code=BEATS_CODE):
        import torch
        sys.path.insert(0, str(code))
        from BEATs import BEATs, BEATsConfig  # noqa: E402  (official code, see the module docstring)
        cp = torch.load(ckpt, map_location="cpu", weights_only=True)  # never unpickle arbitrary objects
        self.model = BEATs(BEATsConfig(cp["cfg"]))
        self.model.load_state_dict(cp["model"])
        self.model.eval()
        self.torch = torch

    def forward(self, x):
        """x: (1, samples) float tensor -> list of (1, tokens, 768) per layer, input layer first."""
        return self.forward_fbank(self.model.preprocess(x))

    def forward_fbank(self, fbank):
        """fbank: (1, frames, 128), already normalised as BEATs.preprocess does -> per-layer outputs."""
        m = self.model
        f = m.patch_embedding(fbank.unsqueeze(1))
        f = f.reshape(f.shape[0], f.shape[1], -1).transpose(1, 2)
        f = m.layer_norm(f)
        if m.post_extract_proj is not None:
            f = m.post_extract_proj(f)
        _, layers = m.encoder.extract_features(f, padding_mask=None, tgt_layer=len(m.encoder.layers) - 1)
        return [h.transpose(0, 1) for h, _ in layers]  # each (T, B, C) -> (B, T, C)

    def embed(self, wav: np.ndarray) -> dict[str, np.ndarray]:
        torch = self.torch
        x = torch.from_numpy(wav - wav.mean()).float()
        segs = [x[i:i + self.max_samples] for i in range(0, max(len(x) - self.max_samples, 0) + 1, self.max_samples)]
        if len(x) > self.max_samples and len(x) % self.max_samples:
            segs.append(x[-self.max_samples:])
        outs = []
        with torch.inference_mode():
            for s in segs:
                outs.append([h.mean(1)[0].numpy() for h in self.forward(s[None])])
        return {f"L{i:02d}": np.mean([o[i] for o in outs], 0) for i in range(len(outs[0]))}


class MN10:
    """EfficientAT MobileNetV3 mn10_as (AudioSet-tagged, 4.9 M parameters). Expects 32 kHz: our 16 kHz audio is
    upsampled (nothing above 8 kHz, as on our microphone). Every block's output, mean over frequency and time;
    'last' is the network's own 960-d pooled feature."""
    name = "mn10"

    def __init__(self, ckpt=MN_CKPT, code=MN_CODE):
        import torch
        import types
        sys.path.insert(0, str(code))
        # helpers/utils.py reads a label CSV relative to the cwd on import; model.py needs only NAME_TO_WIDTH
        stub = types.ModuleType("helpers.utils")
        stub.NAME_TO_WIDTH = lambda name: 1.0
        sys.modules.setdefault("helpers", types.ModuleType("helpers"))
        sys.modules["helpers.utils"] = stub
        from models.mn.model import get_model  # noqa: E402
        from models.preprocess import AugmentMelSTFT  # noqa: E402
        import contextlib
        with contextlib.redirect_stdout(io.StringIO()):  # get_model prints the whole network
            self.model = get_model(width_mult=1.0, pretrained_name=None, head_type="mlp")
        self.model.load_state_dict(torch.load(ckpt, map_location="cpu", weights_only=True))
        self.model.eval()
        self.mel = AugmentMelSTFT(n_mels=128, sr=32000, win_length=800, hopsize=320)
        self.mel.eval()
        self.torch = torch

    def forward(self, x16):
        import torchaudio
        x = torchaudio.functional.resample(x16, RATE, 32000)
        return self.forward_spec(self.mel(x))

    def forward_spec(self, spec):
        """spec: (1, 128, frames), normalised like AugmentMelSTFT's output -> every block's output."""
        return self.model._forward_impl(spec.unsqueeze(1), return_fmaps=True)[1]

    def embed(self, wav: np.ndarray) -> dict[str, np.ndarray]:
        torch = self.torch
        with torch.inference_mode():
            fmaps = self.forward(torch.from_numpy(wav).float()[None])
        return {f"B{i:02d}": f.mean((2, 3))[0].numpy() for i, f in enumerate(fmaps)}


class Ours:
    """Our device's sound features (effente.features, via dcase.clip_rows), one row per whole 1 s window, kept
    per window (W00, W01, ...; NaN past the clip's end) so shorter clips are just the first N windows."""
    name = "ours"
    max_windows = 20

    def embed(self, wav: np.ndarray) -> dict[str, np.ndarray]:
        from training.record.datasets.dcase import clip_rows
        R = clip_rows(np.round(np.clip(wav, -1, 1) * 32767).astype(np.int16), RATE)
        out = np.full((self.max_windows, R.shape[1]), np.nan, np.float32)
        out[:min(len(R), self.max_windows)] = R[:self.max_windows]
        return {f"W{k:02d}": out[k] for k in range(self.max_windows)}


class CED:
    """CED-tiny (RicherMans/CED, 5.5 M parameters, AudioSet-tagged; 16 kHz, 64 mel bands, 10 ms hop). Every
    transformer block's output, mean over its tokens (the model's own 'mean' pooling); L12 is after the final norm."""
    name = "ced"

    def __init__(self, ckpt=None, code=None):
        import importlib.util
        import types
        import torch
        ckpt = ckpt or REPO / "data/models/ced/audiotransformer_tiny_mAP_4814.pt"
        code = Path(code or REPO / "data/models/ced/code")
        # CED's code imports `models.layers` / `models.checkpoints`; EfficientAT also has a `models` folder. Load
        # CED's files under those names without letting its package __init__ shadow EfficientAT's.
        if "models" not in sys.modules:
            pkg = types.ModuleType("models")
            pkg.__path__ = [str(MN_CODE / "models")]
            sys.modules["models"] = pkg

        def load(modname, path):
            spec = importlib.util.spec_from_file_location(modname, path)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[modname] = mod
            spec.loader.exec_module(mod)
            return mod
        load("models.layers", code / "models/layers.py")
        load("models.checkpoints", code / "models/checkpoints.py")
        at = load("ced_audiotransformer", code / "models/audiotransformer.py")
        self.model = at.ced_tiny(pretrained=False)
        sd = torch.load(ckpt, map_location="cpu", weights_only=True)
        sd = sd.get("model", sd)
        want = set(self.model.state_dict())
        self.model.load_state_dict(sd, strict=False)  # CED overrides it (resizes position embeddings), returns None
        bad = [k for k in want - set(sd) if "front_end" not in k] + sorted(set(sd) - want)
        if bad:
            raise RuntimeError(f"CED checkpoint does not match the model: {bad[:5]}")
        self.model.eval()
        self.torch = torch

    def forward(self, x):
        return self.forward_spec(self.model.front_end(x))

    def forward_spec(self, spec):
        """spec: (1, 64, frames) dB mel from the model's front end -> per-block token sequences (<= 1012 frames)."""
        from einops import rearrange
        m = self.model
        x = m.init_bn(rearrange(spec, "b f t -> b 1 f t"))[..., :m.target_length]
        x = m.patch_embed(x)
        b, c, f, t = x.shape
        x = x + m.time_pos_embed[:, :, :, :t] + m.freq_pos_embed[:, :, :, :]
        x = rearrange(x, "b c f t -> b (f t) c")
        x = m.pos_drop(x)
        outs = []
        for blk in m.blocks:
            x = blk(x)
            outs.append(x)
        outs[-1] = m.norm(x)
        return outs

    def embed(self, wav: np.ndarray) -> dict[str, np.ndarray]:
        torch = self.torch
        with torch.inference_mode():
            hs = self.forward(torch.from_numpy(wav).float()[None])
        return {f"L{i + 1:02d}": h.mean(1)[0].numpy() for i, h in enumerate(hs)}


class OnnxInt8:
    """The board's version: the model's own front end, then its dynamic-int8 ONNX network from `onnx` (10 s input;
    shorter clips are zero-padded, so use it on 10 s clips). One onnxruntime thread. Last layer only."""
    base = None

    def __init__(self, onnx_dir=REPO / "data/models/onnx"):
        import onnxruntime as ort
        self.m = self.base()
        so = ort.SessionOptions()
        so.intra_op_num_threads = 1
        self.sess = ort.InferenceSession(str(Path(onnx_dir) / f"{self.base.name}_10s_int8.onnx"), so,
                                         providers=["CPUExecutionProvider"])
        self.n = self.sess.get_inputs()[0].shape

    def embed(self, wav: np.ndarray) -> dict[str, np.ndarray]:
        import torch
        x = np.zeros(10 * RATE, np.float32)
        x[:min(len(wav), len(x))] = wav[:len(x)]
        t = torch.from_numpy(x - x.mean() if self.base is Beats else x)[None]
        with torch.inference_mode():
            if self.base is Beats:
                spec = self.m.model.preprocess(t)
            elif self.base is CED:
                spec = self.m.model.front_end(t)
            else:
                spec = self.m.mel(__import__("torchaudio").functional.resample(t, RATE, 32000))
        return {"last": self.sess.run(None, {self.sess.get_inputs()[0].name: spec.numpy()})[0][0]}


class Mn10Int8(OnnxInt8):
    name, base = "mn10_int8", MN10


class BeatsInt8(OnnxInt8):
    name, base = "beats_int8", Beats


class CedInt8(OnnxInt8):
    name, base = "ced_int8", CED


MODELS = {"beats": Beats, "mn10": MN10, "ced": CED, "ours": Ours, "mn10_int8": Mn10Int8, "beats_int8": BeatsInt8,
          "ced_int8": CedInt8}


def embed_source(clips, model, out: str, seconds: float | None = None, every: int = 50) -> None:
    """Embed every clip into out (.npz): one array per layer, the clip metadata, and seconds per clip. Resumable."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    part = out.with_suffix(".partial.npz")
    done: dict[str, dict] = {}
    if part.exists():
        d = np.load(part, allow_pickle=False)
        rows = json.loads(str(d["meta"]))
        layers = [k for k in d.files if k != "meta"]
        for i, r in enumerate(rows):
            done[r["clip"]] = {"meta": r, "emb": {k: d[k][i] for k in layers}}
        print(f"resuming: {len(done)} clips already embedded", flush=True)

    def save(path):
        names = list(done)
        layers = list(done[names[0]]["emb"])
        tmp = path.with_suffix(".tmp.npz")
        np.savez(tmp, meta=json.dumps([done[n]["meta"] for n in names]),
                 **{k: np.stack([done[n]["emb"][k] for n in names]).astype(np.float32) for k in layers})
        tmp.rename(path)

    t_start, n_new = time.time(), 0
    for name, meta, x in clips:
        if name in done:
            continue
        if seconds:
            x = x[: int(seconds * RATE)]
        t0 = time.perf_counter()
        emb = model.embed(x)
        dt = time.perf_counter() - t0
        done[name] = {"meta": {"clip": name, **meta, "seconds": dt, "clip_s": len(x) / RATE}, "emb": emb}
        n_new += 1
        if n_new % every == 0:
            save(part)
            print(f"  {len(done)} clips, {dt:.2f} s for the last one, {time.time() - t_start:.0f} s so far", flush=True)
    save(part)
    # final file: metadata as plain arrays
    d = np.load(part, allow_pickle=False)
    rows = json.loads(str(d["meta"]))
    keys = list(rows[0])
    np.savez_compressed(out, **{k: d[k] for k in d.files if k != "meta"},
                        **{f"meta_{k}": np.array([r[k] for r in rows]) for k in keys})
    part.unlink()
    print(f"wrote {out}: {len(rows)} clips, layers {[k for k in d.files if k != 'meta']}", flush=True)


# ----------------------------------------------------------------------------------------- detectors

RATES = (0.0, 0.01, 0.02, 0.05, 0.10)  # false-alarm rates PER 10 s CLIP (normal test clips flagged / normal test clips)
SINGLE = ("kNN k=1", "kNN k=3", "kNN k=5", "density kNN", "Mahalanobis", "PCA residual")
FUSED_FROM = ("kNN k=5", "density kNN", "Mahalanobis", "PCA residual")


def backend_scores(Xtr: np.ndarray, Xq: np.ndarray, K: int = 16) -> dict[str, np.ndarray]:
    """Every backend, fitted on normal training rows Xtr, scoring the query rows Xq. All work on embeddings
    standardised with the training mean and sd.
      kNN k=1/3/5   mean distance to the k nearest training rows (fd_knn.py: StandardScaler + L2)
      density kNN   distance to the nearest training row divided by that row's own mean distance to its K
                    nearest training neighbours (local-density normalisation, as the survey's Saengthong 2025)
      Mahalanobis   squared distance under a Ledoit-Wolf shrunk covariance
      PCA residual  squared reconstruction residual keeping 95 % of the training variance"""
    from sklearn.covariance import LedoitWolf
    from sklearn.decomposition import PCA
    from sklearn.neighbors import NearestNeighbors
    mu, sd = Xtr.mean(0), Xtr.std(0)
    sd[sd == 0] = 1.0
    Ztr, Zq = (Xtr - mu) / sd, (Xq - mu) / sd
    nn = NearestNeighbors().fit(Ztr)
    d, i = nn.kneighbors(Zq, n_neighbors=5)
    dtr, _ = nn.kneighbors(Ztr, n_neighbors=min(K + 1, len(Ztr)))
    local = dtr[:, 1:].mean(1)  # each training row's own neighbourhood size, itself excluded
    out = {"kNN k=1": d[:, 0], "kNN k=3": d[:, :3].mean(1), "kNN k=5": d[:, :5].mean(1),
           "density kNN": d[:, 0] / np.maximum(local[i[:, 0]], 1e-12),
           "Mahalanobis": LedoitWolf().fit(Ztr).mahalanobis(Zq)}
    pca = PCA(n_components=0.95, svd_solver="full").fit(Ztr)
    out["PCA residual"] = ((Zq - pca.inverse_transform(pca.transform(Zq))) ** 2).sum(1)
    return out


def oof_and_test(Xtr: np.ndarray, Xte: np.ndarray, folds: int = 5, seed: int = 0):
    """Out-of-fold scores of the training rows (each scored by a model that never saw it) and test scores from
    the model fitted on all training rows. Thresholds and z-scaling come from the out-of-fold scores only."""
    idx = np.random.default_rng(seed).permutation(len(Xtr))
    oof: dict[str, np.ndarray] = {}
    for f in range(folds):
        te = idx[f::folds]
        tr = np.setdiff1d(idx, te)
        for k, v in backend_scores(Xtr[tr], Xtr[te]).items():
            oof.setdefault(k, np.empty(len(Xtr)))[te] = v
    test = backend_scores(Xtr, Xte)
    return oof, test


def zfuse(oof: dict, test: dict, names=FUSED_FROM):
    """z-score each backend with its out-of-fold mean and sd, then fuse: mean of z, and min of z (an alert needs
    every backend to agree). Returns (oof, test) dicts with 'fuse z-mean' and 'fuse z-min'."""
    zo, zt = [], []
    for n in names:
        m, s = oof[n].mean(), oof[n].std() or 1.0
        zo.append((oof[n] - m) / s)
        zt.append((test[n] - m) / s)
    zo, zt = np.array(zo), np.array(zt)
    return {"fuse z-mean": zo.mean(0), "fuse z-min": zo.min(0)}, {"fuse z-mean": zt.mean(0), "fuse z-min": zt.min(0)}


def pauc_dcase(y, s, max_fpr=0.1):
    """DCASE's pAUC: the raw area under the ROC for FPR in [0, max_fpr], divided by max_fpr (chance = max_fpr / 2 /
    max_fpr = 0.05). NOT sklearn's McClish-standardised value (chance 0.5)."""
    from sklearn.metrics import roc_curve
    fpr, tpr, _ = roc_curve(y, s)
    stop = np.searchsorted(fpr, max_fpr, side="right")
    x = np.concatenate([fpr[:stop], [max_fpr]])
    t = np.concatenate([tpr[:stop], [np.interp(max_fpr, fpr, tpr)]])
    return float(np.trapezoid(t, x) / max_fpr)


def metrics(y, s, dom, oof):
    """AUROC; DCASE pAUC (raw, FPR <= 0.1) and the standardised one; DCASE per-domain AUC (that domain's normals vs
    ALL anomalies); then for each false-alarm rate r in RATES: the threshold from the out-of-fold TRAINING scores
    (r = 0: their max; else their (1 - r) quantile), anomalies caught and normals flagged (total and per domain),
    and the test-set oracle (the most anomalies caught with at most floor(r * normals) normals flagged)."""
    from sklearn.metrics import roc_auc_score
    r = {"auc": roc_auc_score(y, s), "pauc": pauc_dcase(y, s), "pauc_std": roc_auc_score(y, s, max_fpr=0.1)}
    for d in np.unique(dom):
        m = (dom == d) | (y == 1)
        r[f"auc_{d}"] = roc_auc_score(y[m], s[m])
    normal = np.sort(s[y == 0])[::-1]
    for rate in RATES:
        tag = f"{rate * 100:g}%"
        thr = float(oof.max()) if rate == 0 else float(np.quantile(oof, 1 - rate))
        r[f"thr@{tag}"] = thr
        r[f"caught@{tag}"] = int(((s > thr) & (y == 1)).sum())
        r[f"flagged@{tag}"] = int(((s > thr) & (y == 0)).sum())
        for d in np.unique(dom):
            m = dom == d
            r[f"caught_{d}@{tag}"] = int(((s > thr) & (y == 1) & m).sum())
            r[f"flagged_{d}@{tag}"] = int(((s > thr) & (y == 0) & m).sum())
        allow = int(np.floor(rate * len(normal)))
        r[f"oracle@{tag}"] = int((s[y == 1] > normal[allow]).sum()) if allow < len(normal) else int((y == 1).sum())
    return r


def compare(reps: dict[str, np.ndarray], train: np.ndarray, y: np.ndarray, groups: np.ndarray,
            pairs: list[tuple[str, str]] = (), quiet: bool = False) -> list[dict]:
    """Every backend and both fusions on every representation. train: mask of normal training rows (fit, z-scaling
    and thresholds); test rows are the rest, with y (1 = fault) and groups (domain) for per-group numbers.
    pairs: (repA, repB) whose 'fuse z-mean' scores are fused once more (z-mean of the two), e.g. ours + BEATs."""
    test = ~train
    rows, keep = [], {}
    for rep, X in reps.items():
        X = np.asarray(X, float)
        oof, sc = oof_and_test(X[train], X[test])
        fo, ft = zfuse(oof, sc)
        oof.update(fo)
        sc.update(ft)
        keep[rep] = (fo["fuse z-mean"], ft["fuse z-mean"])
        for det in (*SINGLE, "fuse z-mean", "fuse z-min"):
            rows.append({"rep": rep, "dim": X.shape[1], "detector": det, **metrics(y, sc[det], groups, oof[det])})
        if not quiet:
            b = max(rows[-8:], key=lambda r: r["auc"])
            print(f"  {rep:30s} best AUROC {b['auc']:.3f} ({b['detector']}); caught at 0/1/5 %: "
                  + "/".join(str(b[f'caught@{t}']) for t in ("0%", "1%", "5%")), flush=True)
    for a, b in pairs:
        if a in keep and b in keep:
            zo = [(keep[k][0] - keep[k][0].mean()) / (keep[k][0].std() or 1) for k in (a, b)]
            zt = [(keep[k][1] - keep[k][0].mean()) / (keep[k][0].std() or 1) for k in (a, b)]
            rows.append({"rep": f"{a} + {b}", "dim": 0, "detector": "fuse z-mean of both",
                         **metrics(y, np.mean(zt, 0), groups, np.mean(zo, 0))})
    return rows


# ----------------------------------------------------------------------------------------- DCASE evaluation

def load_embed(path):
    d = np.load(path, allow_pickle=False)
    meta = {k[5:]: d[k] for k in d.files if k.startswith("meta_")}
    layers = {k: d[k] for k in d.files if not k.startswith("meta_")}
    return meta, layers


def _ours(layers: dict, seconds: float | None, train: np.ndarray):
    """Our features over the first `seconds` 1 s windows: per-clip median, and median + max (as dcase.py builds
    them). Columns are chosen on the TRAINING clips only (finite and not constant there); a test value that is
    NaN is replaced by that column's training median."""
    import warnings
    W = np.stack([layers[k] for k in sorted(layers)], 1)  # (clips, windows, features)
    n = W.shape[1] if seconds is None else int(seconds)
    W = W[:, :n]
    with np.errstate(all="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore")
        med, mx = np.nanmedian(W, 1), np.nanmax(W, 1)
    out = {}
    for name, X in (("median", med), ("median+max", np.hstack([med, mx]))):
        ok = np.isfinite(X[train]).all(0) & (X[train].std(0) > 0)
        X = X[:, ok].copy()
        fill = np.median(X[train], 0)
        bad = ~np.isfinite(X)
        X[bad] = np.take(fill, np.where(bad)[1])
        out[f"ours {name} ({ok.sum()})"] = X
    return out


EMBED_MODELS = ("beats", "mn10", "ced", "beats_int8", "mn10_int8", "ced_int8")


def dcase_reps(folder: Path, seconds: float | None = None, layers_sel: str = "all"):
    """Representations for one clip length, aligned by clip name to ours.npz's order. Files (from `embed`):
    ours.npz (per window; cropped here), <model>.npz (whole clip), <model>_<S>s.npz.
    layers_sel='all': every BEATs and CED layer (the layer sweep); 'main': a few fixed choices only."""
    meta, layers = load_embed(folder / "ours.npz")
    order = list(meta["clip"])
    train = meta["split"] == "train"
    reps = _ours(layers, seconds, train)
    suffix = "" if seconds is None else f"_{int(seconds)}s"
    for model in EMBED_MODELS:
        f = folder / f"{model}{suffix}.npz"
        if not f.exists():
            continue
        m, L = load_embed(f)
        pos = {c: i for i, c in enumerate(m["clip"])}
        ix = np.array([pos[c] for c in order])
        names = sorted(L)
        if "int8" in model:
            picks = {f"{model} ONNX last": L["last"]}
        elif model in ("beats", "ced"):
            picks = {f"{model} {k}": L[k] for k in names} if layers_sel == "all" else \
                {f"{model} {k}": L[k] for k in (layers_sel.split(",") if layers_sel != "main" else names[-1:]) if k in L}
            picks[f"{model} mean L01-12"] = np.mean([L[k] for k in names[1:]], 0)
        else:  # mn10: the network's own pooled 960-d feature, and two earlier blocks
            n = len(names)
            picks = {"mn10 last (960)": L[names[-1]], f"mn10 block {n - 5}": L[names[n - 5]],
                     f"mn10 block {n // 2}": L[names[n // 2]]}
        reps.update({k: v[ix] for k, v in picks.items()})
    return reps, meta


def evaluate(folder: str, seconds: float | None = None, out: str | None = None, domain: str | None = None,
             layers_sel: str = "all", pairs: list[tuple[str, str]] = ()) -> list[dict]:
    """domain='source': learn on source normals only and test source clips only (one machine in one condition,
    like the device learning the machine it watches). Default: every clip; the 10 target-domain normal training
    clips are in the bank, as DCASE scores it."""
    folder = Path(folder)
    reps, meta = dcase_reps(folder, seconds, layers_sel)
    if domain:
        keep = meta["domain"] == domain
        reps = {k: v[keep] for k, v in reps.items()}
        meta = {k: v[keep] for k, v in meta.items()}
    train = meta["split"] == "train"
    y = (meta["label"][~train] == "anomaly").astype(int)
    dom = meta["domain"][~train]
    print(f"{folder}: clip {'whole' if seconds is None else f'{seconds:g} s'}; {train.sum()} normal training clips; "
          f"test {len(y)} clips ({y.sum()} anomalous, {(y == 0).sum()} normal): "
          + ", ".join(f"{d} {(dom == d).sum()}" for d in np.unique(dom)), flush=True)
    rows = compare(reps, train, y, dom, pairs=pairs)
    machine = folder.parent.name.replace("dcase2024-", "")
    for r in rows:
        r.update(machine=machine, seconds=seconds or 10, domain=domain or "all",
                 n_test_anomaly=int(y.sum()), n_test_normal=int((y == 0).sum()), n_train=int(train.sum()))
    out = Path(out or folder / f"results{'_' + domain if domain else ''}{'' if seconds is None else f'_{int(seconds)}s'}.json")
    out.write_text(json.dumps(rows, indent=1))
    print(f"wrote {out} ({len(rows)} rows)")
    return rows


CLIPS_PER_WEEK = 7 * 24 * 60  # ASSUMPTION for the weekly figures: the device judges one 10 s clip a minute


def summary(paths: list[str], detectors=None, reps=None, rates=("0%", "1%", "5%")) -> str:
    """Across machine types, per representation + detector: mean and worst AUROC / DCASE pAUC, and at each
    false-alarm rate (threshold from training) the anomalies caught (mean, worst) and the normals flagged in total,
    with that realised rate per clip and per running week (one clip a minute). paths: results*.json files of ONE
    clip length and domain."""
    per = {}
    for p in paths:
        for r in json.loads(Path(p).read_text()):
            rep = r["rep"].split(" (")[0]
            if (detectors and r["detector"] not in detectors) or (reps and rep not in reps):
                continue
            per.setdefault((rep, r["detector"]), []).append(r)
    head = "| representation | detector | n | AUROC mean | AUROC worst | pAUC mean | " + " | ".join(
        f"caught @{t} mean / worst | normals flagged @{t} (rate/clip, per week)" for t in rates) + " |"
    lines = [head, "|" + "|".join(["---"] * (head.count("|") - 1)) + "|"]
    for (rep, det), rs in per.items():
        a = np.array([r["auc"] for r in rs])
        cells = [rep, det, str(len(rs)), f"{a.mean():.3f}", f"{a.min():.3f} ({rs[int(a.argmin())]['machine']})",
                 f"{np.mean([r['pauc'] for r in rs]):.3f}"]
        for t in rates:
            c = np.array([r[f"caught@{t}"] / r["n_test_anomaly"] * 100 for r in rs])
            fl = sum(r[f"flagged@{t}"] for r in rs)
            nn_ = sum(r["n_test_normal"] for r in rs)
            cells += [f"{c.mean():.0f}% / {c.min():.0f}% ({rs[int(c.argmin())]['machine']})",
                      f"{fl}/{nn_} ({fl / nn_ * 100:.1f}%, ~{fl / nn_ * CLIPS_PER_WEEK:.0f}/wk)"]
        lines.append("| " + " | ".join(cells) + " |")
    text = "\n".join(lines)
    print(text)
    return text


# ----------------------------------------------------------------------------------------- vibration

def vib_as_audio(acc_g: np.ndarray, rate: float, gain: float) -> list[np.ndarray]:
    """Path (a): each accelerometer axis as our MPU records it (g, ~1 kHz), offset removed, upsampled to 16 kHz
    and scaled by `gain` (set once from training clips so they sit at a typical audio level) -> one waveform per axis.
    Nothing above the MPU's ~500 Hz Nyquist: the network sees a low-passed 'sound'."""
    from scipy.signal import resample_poly
    r = int(round(rate))
    g = np.gcd(r, RATE)
    return [(resample_poly(a - a.mean(), RATE // g, r // g) * gain).astype(np.float32) for a in acc_g.T]


def vib_logspec(acc_g: np.ndarray, rate: float, frames_per_s: int = 100) -> list[np.ndarray]:
    """Path (b): a spectrogram at the native rate. Per axis: 254-point STFT (128 bins over 0..rate/2, ~4 Hz apart at
    1 kHz), hop rate/100 so a 10 s clip gives ~1000 frames like the networks' own 10 ms frames; log power.
    Returns (128, frames) per axis, NOT yet normalised (see vib_norm)."""
    from scipy.signal import stft
    hop = max(int(round(rate / frames_per_s)), 1)
    out = []
    for a in acc_g.T:
        _, _, Z = stft(a - a.mean(), fs=rate, nperseg=254, noverlap=254 - hop, boundary="even", padded=False)
        out.append(np.log(np.abs(Z[:128]) ** 2 + 1e-12).astype(np.float32))
    return out


def vib_embed(model, acc_g, rate, gain, spec_mu, spec_sd) -> dict[str, np.ndarray]:
    """Both vibration paths through one model; each layer averaged over the three axes (the SSCC benchmark fuses
    channels by averaging too). Keys: a_<layer> (audio-rate path), b_<layer> (native spectrogram path)."""
    torch = model.torch
    a = [model.embed(w) for w in vib_as_audio(acc_g, rate, gain)]
    out = {f"a_{k}": np.mean([e[k] for e in a], 0) for k in a[0]}
    b = []
    with torch.inference_mode():
        for S in vib_logspec(acc_g, rate):
            z = torch.from_numpy((S - spec_mu) / spec_sd)  # (128, frames), ~zero mean, unit sd
            if isinstance(model, Beats):
                hs = model.forward_fbank((z.T / 2)[None])  # BEATs' own input is (fbank - mean) / (2 sd)
                b.append({f"L{i:02d}": h.mean(1)[0].numpy() for i, h in enumerate(hs)})
            elif isinstance(model, CED):
                z64 = z.reshape(64, 2, -1).mean(1) if z.shape[0] == 128 else z  # 128 bins -> 64 by pairs
                hs = model.forward_spec((z64 * 20.0 - 40.0)[None])  # roughly the dB scale CED's init_bn expects (unverified)
                b.append({f"L{i + 1:02d}": h.mean(1)[0].numpy() for i, h in enumerate(hs)})
            else:
                fm = model.forward_spec(z[None])
                b.append({f"B{i:02d}": f.mean((2, 3))[0].numpy() for i, f in enumerate(fm)})
    out.update({f"b_{k}": np.mean([e[k] for e in b], 0) for k in b[0]})
    return out


VIB_SCENARIOS = ("unbalance", "misalignment", "looseness", "motor_bearing", "idler_bearing", "gear_wear")


def fake_vib_clips(root: str, scenarios=VIB_SCENARIOS, severity="obvious"):
    """SYNTHETIC conveyor-eval raw clips -> (scenario, t_s, fault?, acc in g (n, 3), rate). Fault = the clip starts
    after the scenario's fault onset."""
    root = Path(root)
    for sc in scenarios:
        d = root / f"{sc}-{severity}"
        lab = json.loads((d / "labels.json").read_text())
        onset = min((f["start_s"] for f in lab["faults"]), default=np.inf)
        for f in sorted((d / "raw").glob("clip_*.npz")):
            z = np.load(f, allow_pickle=False)
            t = float(f.stem.split("_")[1])
            yield sc, t, bool(t >= onset), z["samples"].astype(np.float64) / float(z["accel_lsb_per_g"]), \
                float(z["sample_rate_hz"])


def vibcheck(root: str, out: str) -> None:
    """SYNTHETIC sanity check of the vibration paths. Per scenario: train on the first 2/3 of the pre-onset clips,
    test on the last 1/3 (normal) and every post-onset clip (fault). Our own vibration features (effente, 1 s
    windows, median + max) vs BEATs / mn10 through paths (a) and (b). Detectors and metrics as for DCASE."""
    from effente import features as F
    import torch
    torch.set_num_threads(1)
    clips = list(fake_vib_clips(root))
    sc = np.array([c[0] for c in clips]); fault = np.array([c[2] for c in clips])
    train = np.zeros(len(clips), bool)
    for s_ in np.unique(sc):
        pre = np.where((sc == s_) & ~fault)[0]
        train[pre[: 2 * len(pre) // 3]] = True
    rms = np.median([np.sqrt(np.mean((c[3] - c[3].mean(0)) ** 2)) for c, t in zip(clips, train) if t])
    gain = 0.05 / rms  # training clips at ~-26 dBFS rms, a typical recording level
    S = [vib_logspec(c[3], c[4]) for c, t in zip(clips, train) if t]
    allS = np.concatenate([np.concatenate(x, 1) for x in S], 1)
    mu, sd = float(allS.mean()), float(allS.std())
    print(f"{len(clips)} synthetic clips, {train.sum()} for training; gain {gain:.1f}; log-spec mean {mu:.2f} sd {sd:.2f}",
          flush=True)
    reps: dict[str, list] = {}
    models = {"beats": Beats(), "mn10": MN10(), "ced": CED()}
    t0 = time.time()
    for i, (s_, t, flt, acc, rate) in enumerate(clips):
        n = int(rate)
        R = np.array([F.extract(F.Window(t0=float(k), accel_g=acc[k * n:(k + 1) * n], accel_rate_hz=rate,
                                         accel_range_g=8.0)) for k in range(len(acc) // n)])
        with np.errstate(all="ignore"), __import__("warnings").catch_warnings():
            __import__("warnings").simplefilter("ignore")
            reps.setdefault("ours vib median+max", []).append(np.concatenate([np.nanmedian(R, 0), np.nanmax(R, 0)]))
        for name, m in models.items():
            e = vib_embed(m, acc, rate, gain, mu, sd)
            L = sorted(k for k in e if k.startswith("a_"))
            for path in ("a", "b"):
                keys = sorted(k for k in e if k.startswith(path + "_"))
                reps.setdefault(f"{name} ({path}) last", []).append(e[keys[-1]])
                if name in ("beats", "ced"):
                    reps.setdefault(f"{name} ({path}) mean of layers", []).append(np.mean([e[k] for k in keys[1:]], 0))
        if i % 100 == 0:
            print(f"  {i}/{len(clips)} clips, {time.time() - t0:.0f} s", flush=True)
    X = {k: np.array(v) for k, v in reps.items()}
    ok = np.isfinite(X["ours vib median+max"]).all(0) & (X["ours vib median+max"][train].std(0) > 0)
    X["ours vib median+max"] = X["ours vib median+max"][:, ok]
    np.savez_compressed(out, scenario=sc, fault=fault, train=train, **{k.replace(" ", "_"): v for k, v in X.items()})
    rows = []
    for s_ in np.unique(sc):
        m = sc == s_
        print(f"\n{s_} (SYNTHETIC): {(train & m).sum()} train, {(~train & m & ~fault).sum()} normal test, "
              f"{(m & fault).sum()} fault test")
        for r in compare({k: v[m] for k, v in X.items()}, train[m], fault[m & ~train].astype(int),
                         np.array(["all"] * (m & ~train).sum())):
            rows.append({"scenario": str(s_), **r})
    Path(out).with_suffix(".json").write_text(json.dumps(rows, indent=1))
    print(f"wrote {out} and {Path(out).with_suffix('.json')}")


# ----------------------------------------------------------------------------------------- ONNX (for the board)

def onnx_export(model_name: str, clip_s: float, out_dir: str) -> dict:
    """Export the network from its spectrogram input (fixed length) to ONNX float32, then dynamic int8
    (onnxruntime.quantization, weights int8). Output: the time-mean pooled last layer. Returns the paths and
    the front end's own time (waveform -> spectrogram, torch, one thread), which the ONNX file does not include."""
    import torch
    torch.set_num_threads(1)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    m = MODELS[model_name]()
    wav = torch.from_numpy((np.random.default_rng(0).standard_normal(int(clip_s * RATE)) * 0.05).astype(np.float32))[None]
    if model_name == "beats":
        front = lambda: m.model.preprocess(wav)  # noqa: E731
        net = lambda x: m.forward_fbank(x)[-1].mean(1)  # noqa: E731
    elif model_name == "ced":
        front = lambda: m.model.front_end(wav)  # noqa: E731
        net = lambda x: m.forward_spec(x)[-1].mean(1)  # noqa: E731
    else:
        import torchaudio
        front = lambda: m.mel(torchaudio.functional.resample(wav, RATE, 32000))  # noqa: E731
        net = lambda x: m.forward_spec(x)[-1].mean((2, 3))  # noqa: E731

    class Net(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.inner = m.model

        def forward(self, x):
            return net(x)

    with torch.inference_mode():
        spec = front()
        front()
        t0 = time.perf_counter()
        for _ in range(5):
            front()
        front_s = (time.perf_counter() - t0) / 5
    tag = f"{model_name}_{clip_s:g}s"
    f32 = out_dir / f"{tag}_f32.onnx"
    i8 = out_dir / f"{tag}_int8.onnx"
    with torch.no_grad():
        try:
            torch.onnx.export(Net().eval(), (spec,), str(f32), input_names=["spec"], output_names=["emb"],
                              dynamo=False, opset_version=17)
        except Exception as e:  # noqa: BLE001
            print(f"  TorchScript export failed ({e}); trying dynamo", flush=True)
            torch.onnx.export(Net().eval(), (spec,), str(f32), input_names=["spec"], output_names=["emb"], dynamo=True)
    from onnxruntime.quantization import QuantType, quantize_dynamic
    quantize_dynamic(str(f32), str(i8), weight_type=QuantType.QInt8)
    np.save(out_dir / f"{tag}_input.npy", spec.numpy())
    with torch.inference_mode():
        ref = net(spec).numpy()
    np.save(out_dir / f"{tag}_ref.npy", ref)
    r = {"model": model_name, "clip_s": clip_s, "input_shape": list(spec.shape), "front_end_s_1thread": front_s,
         "f32": str(f32), "f32_mb": f32.stat().st_size / 2**20, "int8": str(i8), "int8_mb": i8.stat().st_size / 2**20}
    print(json.dumps(r), flush=True)
    return r


def ortime(onnx_path: str, threads: int, reps: int = 10, input_npy: str | None = None, ref_npy: str | None = None) -> dict:
    """Time one ONNX model with onnxruntime alone (numpy + onnxruntime: runs on the board too). Median seconds per
    call after 2 warm-ups, peak RSS of this process, and the max difference from the PyTorch output if given."""
    import onnxruntime as ort
    rss0 = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    so = ort.SessionOptions()
    so.intra_op_num_threads = threads
    so.inter_op_num_threads = 1
    so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    sess = ort.InferenceSession(onnx_path, so, providers=["CPUExecutionProvider"])
    inp = sess.get_inputs()[0]
    x = np.load(input_npy) if input_npy else np.random.default_rng(0).standard_normal(
        [d if isinstance(d, int) else 1 for d in inp.shape]).astype(np.float32)
    for _ in range(2):
        y = sess.run(None, {inp.name: x})[0]
    ts = []
    for _ in range(reps):
        t0 = time.perf_counter()
        y = sess.run(None, {inp.name: x})[0]
        ts.append(time.perf_counter() - t0)
    scale = 1 if sys.platform == "darwin" else 1024
    r = {"onnx": Path(onnx_path).name, "threads": threads, "sec_median": float(np.median(ts)), "sec_min": float(min(ts)),
         "peak_rss_mb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * scale / 2**20,
         "rss_before_mb": rss0 * scale / 2**20, "machine": os.uname().machine, "ort": ort.__version__}
    if ref_npy:
        ref = np.load(ref_npy)
        r["max_abs_diff_vs_torch"] = float(np.abs(y - ref).max())
        r["cosine_vs_torch"] = float((y.ravel() @ ref.ravel()) / (np.linalg.norm(y) * np.linalg.norm(ref) + 1e-12))
    print(json.dumps(r), flush=True)
    return r


# ----------------------------------------------------------------------------------------- cost

def cost(model_name: str, quant: bool, reps: int, clip_s: float) -> dict:
    """One thread. Time per clip (median of reps after one warm-up), params, multiply-adds, peak RSS."""
    import torch
    torch.set_num_threads(1)
    rss0 = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    m = MODELS[model_name]()
    net = m.model
    if quant:
        torch.backends.quantized.engine = "qnnpack"
        net = torch.ao.quantization.quantize_dynamic(net, {torch.nn.Linear}, dtype=torch.qint8)
        m.model = net
    params = sum(p.numel() for p in m.model.parameters()) if not quant else \
        sum(p.numel() for p in MODELS[model_name]().model.parameters())
    wav = (np.random.default_rng(0).standard_normal(int(clip_s * RATE)) * 0.05).astype(np.float32)
    from torch.utils.flop_counter import FlopCounterMode
    flops = None
    if not quant:
        with FlopCounterMode(display=False) as fc, torch.inference_mode():
            m.forward(torch.from_numpy(wav)[None])
        flops = fc.get_total_flops()
    m.embed(wav)
    times = []
    for _ in range(reps):
        t0 = time.perf_counter()
        m.embed(wav)
        times.append(time.perf_counter() - t0)
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    scale = 1 if sys.platform == "darwin" else 1024  # macOS reports bytes, Linux KiB
    r = {"model": model_name, "int8_dynamic": quant, "clip_s": clip_s, "threads": 1,
         "sec_per_clip_median": float(np.median(times)), "sec_per_clip_min": float(np.min(times)),
         "params": int(params), "gflops": None if flops is None else flops / 1e9,
         "peak_rss_mb": rss * scale / 2**20, "rss_before_model_mb": rss0 * scale / 2**20,
         "machine": os.uname().machine, "torch": torch.__version__}
    print(json.dumps(r))
    return r


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("embed")
    e.add_argument("source", choices=["dcase", "sscc"])
    e.add_argument("path")
    e.add_argument("--model", choices=list(MODELS), required=True)
    e.add_argument("--out", required=True)
    e.add_argument("--seconds", type=float, default=None, help="keep only the first N seconds of each clip")
    e.add_argument("--threads", type=int, default=1)
    v = sub.add_parser("evaluate")
    v.add_argument("folder", help="an embed folder holding ours.npz, beats*.npz, mn10*.npz, ced*.npz")
    v.add_argument("--seconds", type=float, default=None, help="clip length: 2 or 5 (default: whole clip)")
    v.add_argument("--out", default=None)
    v.add_argument("--domain", default=None, help="'source': learn and test on the source domain only")
    v.add_argument("--layers", default="all", help="'all' (layer sweep), 'main', or e.g. L05,L12")
    v.add_argument("--pair", action="append", default=[], help="'repA|repB': also fuse these two (z-mean)")
    u = sub.add_parser("summary")
    u.add_argument("results", nargs="+", help="results*.json files of one clip length and domain")
    u.add_argument("--detectors", default=None, help="comma-separated")
    u.add_argument("--reps", default=None, help="comma-separated, names without the '(dim)' part")
    vc = sub.add_parser("vibcheck", help="SYNTHETIC sanity check of the vibration paths")
    vc.add_argument("root", help="data/fake/conveyor-eval")
    vc.add_argument("--out", required=True)
    ox = sub.add_parser("onnx", help="export to ONNX float32 + dynamic int8")
    ox.add_argument("--model", choices=["beats", "mn10", "ced"], required=True)
    ox.add_argument("--clip-s", type=float, default=10.0)
    ox.add_argument("--out-dir", required=True)
    ot = sub.add_parser("ortime", help="time an ONNX file with onnxruntime (needs only numpy + onnxruntime)")
    ot.add_argument("onnx")
    ot.add_argument("--threads", type=int, default=1)
    ot.add_argument("--reps", type=int, default=10)
    ot.add_argument("--input", default=None)
    ot.add_argument("--ref", default=None)
    c = sub.add_parser("cost")
    c.add_argument("--model", choices=list(MODELS), required=True)
    c.add_argument("--int8", action="store_true", help="dynamic int8 quantisation of the Linear layers")
    c.add_argument("--reps", type=int, default=5)
    c.add_argument("--clip-s", type=float, default=10.0)
    args = ap.parse_args(argv)
    if args.cmd == "embed":
        import torch
        torch.set_num_threads(args.threads)
        clips = dcase_clips(args.path) if args.source == "dcase" else sscc_clips(args.path)
        embed_source(clips, MODELS[args.model](), args.out, args.seconds)
    elif args.cmd == "evaluate":
        evaluate(args.folder, args.seconds, args.out, args.domain, args.layers, [tuple(p.split('|')) for p in args.pair])
    elif args.cmd == "onnx":
        onnx_export(args.model, args.clip_s, args.out_dir)
    elif args.cmd == "ortime":
        ortime(args.onnx, args.threads, args.reps, args.input, args.ref)
    elif args.cmd == "vibcheck":
        vibcheck(args.root, args.out)
    elif args.cmd == "summary":
        summary(args.results, args.detectors.split(',') if args.detectors else None,
                args.reps.split(',') if args.reps else None)
    else:
        cost(args.model, args.int8, args.reps, args.clip_s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
