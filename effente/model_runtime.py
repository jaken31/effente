"""Run a trained effente model on the device. NumPy only.

A model file (JSON, written by training/model/train.py) holds, in the TRD's model-file spirit:
whole-number (int8) weights and biases with their scale factors, the normalisation statistics
(effente.model_input.Prep), the unknown-alarm threshold, the fault types and the feature table.
Two parts:

  normal model     an autoencoder learned on THIS machine's normal minutes. Its score is how badly
                   it reproduces the current minute; its residuals say how far each reading is from
                   what it expected given the others
  fault types      effente.fault_types signatures over those residuals, blended with a small
                   learned classifier when the file has one; trained once, the same on any machine

`Watcher` is what the device runs: feed it every row with its time; it summarises each period
(the model's period_s: a minute for long recordings, down to a second for a short demo one)
(effente.model_input.MinuteSummary, the same summaries the recorder stores), scores the minute,
needs the score over the threshold for the median of the last few minutes (persistence), and
returns a result in the TRD log-record shape.

A spike (added 13 Nov): the median outvotes one abnormal period whatever its score, so something that
happens once (a part falls off, a hard knock) never showed. When the model's baseline carries a spike
line (effente.trainer: "model_spike", from the UNSMOOTHED held-out scores, well above the threshold,
which is fitted to smoothed ones), a single period scoring over that line x the margin alarms at
once, named from that period's own deviations, and is held SPIKE_HOLD_S so it can be seen. Its margin
is spike_margin (the device's own dial, "spike_sensitivity"), or the main margin when none is given. The
median rule is unchanged. With 1-minute periods (long recordings) the model is fed minute medians,
so a 1-second spike cannot reach it at all: there the per-second impact checks (effente.safety) are
what catch it, and the trainer stores no spike line. Simulated data only: unverified on a real machine.
"""

from __future__ import annotations

import json
from collections import Counter, deque
from pathlib import Path

import numpy as np

from effente import fault_types as FT
from effente import features as F
from effente.model_input import MinuteSummary, Prep

SPIKE_HOLD_S = 30.0  # how long a spike alarm stays, so it can be seen
FORMAT = "effente-model-1"
FAR_SPREADS = 6.0  # this far from usual counts as evidence whatever the context


class Net:
    """A small fully connected network with int8 weights: y = act(x @ (q * scale) + b), one scale
    per output unit (per-channel), so small weights keep their precision."""

    def __init__(self, layers: list[dict]):
        self.layers = []
        for L in layers:
            W = np.asarray(L["w_q"], np.int8).astype(np.float32) * np.asarray(L["w_scale"], np.float32)[None, :]
            b = np.asarray(L["b_q"], np.int32).astype(np.float32) * np.float32(L["b_scale"])
            self.layers.append((W, b, L["act"]))

    def __call__(self, X: np.ndarray, upto: int | None = None) -> np.ndarray:
        h = np.atleast_2d(np.asarray(X, np.float32))
        for i, (W, b, act) in enumerate(self.layers[:upto]):
            h = h @ W + b
            if act == "relu":
                h = np.maximum(h, 0)
            elif act == "sigmoid":
                h = 1 / (1 + np.exp(-np.clip(h, -30, 30)))
        return h

    def hidden(self, X, layer: int = 1) -> np.ndarray:
        """Activity of a hidden layer (for the unknown alarm and mech interp)."""
        return self(X, upto=layer + 1)


def quantize(W: np.ndarray, b: np.ndarray, act: str) -> dict:
    """int8 weights with a scale per output unit, int16 biases with one scale."""
    ws = np.abs(W).max(0) / 127
    ws = np.where(ws > 0, ws, 1.0)
    bs = float(np.abs(b).max() / 32767) or 1.0
    return {"w_q": np.round(W / ws[None, :]).astype(int).tolist(), "w_scale": [float(v) for v in ws],
            "b_q": np.round(b / bs).astype(int).tolist(), "b_scale": bs, "act": act,
            "shape": list(W.shape)}


class Model:
    def __init__(self, d: dict):
        if d.get("format") != FORMAT:
            raise ValueError(f"not an {FORMAT} model file")
        here = _version()
        if d["features_version"] != here:
            raise ValueError(f"model was trained on features {d['features_version']}, this device computes {here}")
        self.d = d
        self.prep = Prep.from_dict(d["prep"])
        self.ae = Net(d["autoencoder"])
        self.threshold = float(d["threshold"])
        self.smooth = int(d.get("smooth_minutes", 5))  # summaries of persistence before an alarm
        self.period = int(d.get("period_s", 60))  # seconds per summary: 60 for long recordings, 1 for a demo
        self.classifier = Net(d["fault_classifier"]["layers"]) if d.get("fault_classifier") else None
        self.class_names = list(d["fault_classifier"]["classes"]) if self.classifier else []
        out = self.prep.names_out
        self._dev_idx = [i for i, n in enumerate(out) if n in self.prep.columns]
        self._dev_names = [out[i] for i in self._dev_idx]
        self.resid_scale = np.asarray(d.get("residual_scale", np.ones(len(out))), float)
        # A PCA model (effente.trainer, normal_model "pca") is scored two ways, as in process monitoring:
        # what its patterns can't explain (the residual) and how far along its patterns the row has gone
        # (Hotelling's T2), each about 1 for normal rows. The residual alone misses a change that moves
        # along a pattern of normal (12 Nov: a swinging temperature, 8 spreads off, scored as normal).
        ps = d.get("pca_score")
        self.pattern_scale = np.asarray(ps["pattern_scale"], float) if ps else None
        self.residual_ref = float(ps["residual_ref"]) if ps else 1.0
        # the line one period's own score must pass to alarm at once (None: no spike rule, as before). Kept in
        # the baseline, which already carries added keys, so the model file gets no new key
        sp = ((d.get("baseline") or {}).get("model_spike") or {}).get("line")
        self.spike_line = float(sp) if sp else None

    @classmethod
    def load(cls, path) -> "Model":
        return cls(json.loads(Path(path).read_text()))

    def score(self, X) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Per row: (score, residuals, prepared inputs)."""
        Z = self.prep.transform(X)
        r = Z - self.ae(Z)
        s = (r ** 2).mean(1)
        if self.pattern_scale is not None:
            t2 = ((self.ae.hidden(Z, layer=0) / self.pattern_scale) ** 2).mean(1)
            s = s / self.residual_ref + t2
        return s, r, Z

    def deviation_vector(self, r: np.ndarray, Z: np.ndarray) -> np.ndarray:
        """How far each reading is off, for naming the fault (rows x kept features).

        A reading counts only as far as BOTH say so: its residual, in spreads of its own normal
        residual (the model expected something else, given the other readings), and its distance
        from its usual value, in the same direction. The first stops a reading that is high for a
        normal reason (high load) from counting; the second stops the autoencoder's spread-out
        error from implicating readings that never moved. A reading FAR_SPREADS or more from its
        usual value counts as it is: nothing normal puts it there, and an autoencoder can copy an
        extreme value through, which would hide it in the residual."""
        r, Z = np.atleast_2d(r)[:, self._dev_idx], np.atleast_2d(Z)[:, self._dev_idx]
        rr = r / self.resid_scale[self._dev_idx]
        same = np.sign(rr) == np.sign(Z)
        dev = np.where(same, np.sign(rr) * np.minimum(np.abs(rr), np.abs(Z)), 0.0)
        return np.where(np.abs(Z) >= FAR_SPREADS, Z, dev)

    def deviations(self, r: np.ndarray, Z: np.ndarray) -> dict[str, float]:
        v = self.deviation_vector(r, Z)[0]
        return {n: float(x) for n, x in zip(self._dev_names, v)}

    def learned_types(self, r: np.ndarray, Z: np.ndarray) -> dict[str, float] | None:
        if self.classifier is None:
            return None
        p = self.classifier(np.clip(self.deviation_vector(r, Z), -8, 8))[0]
        return dict(zip(self.class_names, map(float, p)))


def _version() -> str:
    import hashlib
    return hashlib.sha1("|".join(F.FEATURE_NAMES).encode()).hexdigest()[:12]


class Watcher:
    """One machine's live judgement. predict_at(t0, x) for every row; the result changes once a minute."""

    def __init__(self, model: Model, margin: float = 1.0, spike_margin: float | None = None):
        self.m = model
        self.margin = margin  # how far over the learned line counts as wrong (effente.safety.margins)
        # how far over the spike line one period must go: the spike sensitivity's margin; None: the same as margin
        self.spike_margin = spike_margin
        self.summary = MinuteSummary()
        self.minute: int | None = None
        self.scores: deque = deque(maxlen=model.smooth)
        self.abnormal_minutes = 0
        self.episode: Counter = Counter()  # fault types named during the current abnormal stretch
        self.said: Counter = Counter()  # what was going on (symptoms), the same stretch
        self.rows = 0
        self.spike_until, self.spike_state = -float("inf"), None  # a spike alarm being held, and what it said
        self.state = {"diagnosis": "warming_up", "confidence": 0.0, "unknown_score": None,
                      "note": "the first minute is still being summarised"}

    def predict(self, x) -> dict:
        """Without times, rows are taken to come 4 a second (watch mode's default)."""
        self.rows += 1
        return self.predict_at((self.rows - 1) * 0.25, x)

    def predict_at(self, t0: float, x) -> dict:
        m = int(t0 // self.m.period)
        if self.minute is not None and m != self.minute:
            stats = self.summary.take()
            if stats is not None:
                self._judge(self.minute * float(self.m.period), stats["median"])
        self.minute = m
        self.summary.add(t0, x)
        return dict(self.state)

    def _judge(self, minute_t: float, row: np.ndarray):
        names = self.m.prep.names_in
        if "trend_running" in names and row[names.index("trend_running")] < 0.5:
            self.scores.clear()
            self.abnormal_minutes = 0
            self.episode.clear()
            self.said.clear()
            self.spike_until = -float("inf")
            self.state = {"diagnosis": "stopped", "confidence": 1.0, "unknown_score": None, "minute": minute_t,
                          "note": "the machine is not running; nothing is judged while it is stopped"}
            return
        s, r, Z = self.m.score(row[None])
        self.scores.append(float(s[0]))
        level = float(np.median(self.scores)) / self.m.threshold
        oor = int(self.m.prep.out_of_range(row[None])[0])
        dev = self.m.deviations(r, Z)
        state = {"unknown_score": round(level, 3), "minute": minute_t, "out_of_range": oor}
        # A spike: this one period's own score over the spike line (x the margin, so the sensitivity sizes it)
        line = self.m.spike_line
        spike = line is not None and float(s[0]) > line * (self.margin if self.spike_margin is None else self.spike_margin)
        if line is not None:
            state["spike_score"] = round(float(s[0]) / line, 3)  # this period alone, in units of the spike line
        if level > self.margin or spike:
            self.abnormal_minutes += 1
            d = FT.diagnose(dev, self.abnormal_minutes, self.m.learned_types(r, Z))
            self.episode[d.diagnosis] += 1
            named = Counter({k: v for k, v in self.episode.items() if k != "unrecognised_pattern"})
            # The stretch's diagnosis: the type named most often so far, so it does not flip minute to minute.
            lead, votes = (named.most_common(1)[0] if named else (d.diagnosis, self.episode[d.diagnosis]))
            state.update(diagnosis=lead, diagnosis_this_minute=d.diagnosis,
                         confidence=round(d.confidence * votes / sum(self.episode.values()) ** 0.5 / votes ** 0.5, 2),
                         features=d.evidence, fault_types=d.types, abnormal_minutes=self.abnormal_minutes, note=d.note)
            # What people are told: straight from which readings moved (effente.fault_types.describe), the
            # symptom said most often this stretch first, so it doesn't flip; the cause above stays in the data.
            now = FT.describe(dev)
            self.said[now[0][0]] += 1
            first = self.said.most_common(1)[0][0]
            state["symptoms"] = [first] + [s for s, _ in now if s != first][:FT.SAY_AT_MOST - 1]
            state["symptom"] = first
            if spike:  # said at once, and kept for SPIKE_HOLD_S below
                state["spike"] = True
                self.spike_until, self.spike_state = minute_t + SPIKE_HOLD_S, dict(state)
        elif minute_t < self.spike_until:  # nothing wrong this period, but a spike is still being shown
            state = {**self.spike_state, **state, "spike": False, "spike_held": True}
        else:
            self.abnormal_minutes = 0
            self.episode.clear()
            self.said.clear()
            top = dict(sorted(dev.items(), key=lambda kv: -abs(kv[1]))[:3])
            state.update(diagnosis="normal", confidence=round(float(np.clip(1.0 - level / self.margin, 0.0, 1.0)), 2),
                         features={k: round(v, 2) for k, v in top.items()}, note="")
        if oor:
            state["note"] = (state["note"] + "; " if state["note"] else "") + \
                f"{oor} reading{'s' if oor != 1 else ''} far outside anything seen while learning"
        self.state = state
