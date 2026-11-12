"""Simulated device model: spectra, feature activity, mask, diagnosis.

Everything here is MADE UP. The spectra are illustrative shapes chosen to look
plausible on the dashboard, not measurements of any fan, and the "model" is a
hand-written stand-in, not a trained network. This is not the signal pipeline
from the TRD and must never be presented as one. Every record and model
announcement built here carries "simulated": true.

The mask is applied mechanically, the way the real device must do it:

    spectrum -> feature activity -> zero masked features -> output layer -> diagnosis

so switching a feature off removes the diagnosis that depends on it and leaves
the others alone, without any lookup of "what the answer should be".
"""

from __future__ import annotations

import math
import random
import statistics
from datetime import datetime, timezone

MODEL_VERSION = "sim-1"
DEVICE_ID = "effente-sim-01"
MACHINE_ID = "demo-fan-1"

N_BINS = 64
HZ_MAX = 500.0
BIN_HZ = HZ_MAX / (N_BINS - 1)  # bin i sits at i * BIN_HZ, 0 Hz to 500 Hz
TURN_HZ = 30.0  # demo fan taken as about 1,800 RPM (assumed, not measured)
UNKNOWN_HZ = 300.0  # the made-up "never seen" tone

CASES = ["normal", "imbalance", "loose_mount", "unknown"]
CLASS_NAMES = ["normal", "imbalance", "loose_mount"]
UNKNOWN_THRESHOLD = 0.5

# Feature table, matching the PRD demo script. Unknown has no feature.
FEATURES = [
    {"name": "once_per_turn", "sentence": "once-per-turn vibration"},
    {"name": "rattle", "sentence": "rattling pattern"},
]
FEATURE_NAMES = [f["name"] for f in FEATURES]
SENTENCES = {f["name"]: f["sentence"] for f in FEATURES}

# Output layer: one weight per feature, plus a bias, for each class.
# Stand-in for the trained network's last layer.
WEIGHTS = {
    "normal": {"once_per_turn": -3.0, "rattle": -3.0},
    "imbalance": {"once_per_turn": 6.0, "rattle": 0.0},
    "loose_mount": {"once_per_turn": 0.0, "rattle": 6.0},
}
BIAS = {"normal": 1.5, "imbalance": 0.0, "loose_mount": 0.0}


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def bin_hz(i: int) -> float:
    return i * BIN_HZ


def _peak(f0: float, amp: float, width_hz: float = 4.0) -> list[float]:
    return [amp * math.exp(-0.5 * ((bin_hz(i) - f0) / width_hz) ** 2) for i in range(N_BINS)]


def _add(a: list[float], b: list[float]) -> list[float]:
    return [x + y for x, y in zip(a, b)]


def _floor(rng: random.Random, level: float, jitter: float) -> list[float]:
    return [level + rng.uniform(-jitter, jitter) for _ in range(N_BINS)]


def make_spectrum(case: str, rng: random.Random) -> list[float]:
    """One window's 64-bin spectrum for a demo case. Illustrative shapes only."""
    if case == "normal":
        s = _floor(rng, 0.03, 0.012)
        s = _add(s, _peak(TURN_HZ, rng.uniform(0.08, 0.11)))
    elif case == "imbalance":
        s = _floor(rng, 0.03, 0.012)
        s = _add(s, _peak(TURN_HZ, rng.uniform(0.55, 0.68)))
        s = _add(s, _peak(2 * TURN_HZ, rng.uniform(0.05, 0.1)))
    elif case == "loose_mount":
        # Raised broadband energy plus harmonics that change every window.
        s = _floor(rng, rng.uniform(0.17, 0.26), 0.05)
        s = _add(s, _peak(TURN_HZ, 0.05))
        for k in range(2, 7):
            if rng.random() < 0.7:
                s = _add(s, _peak(k * TURN_HZ, rng.uniform(0.1, 0.5)))
    elif case == "unknown":
        # A strong tone far from the once-per-turn line and its harmonics.
        s = _floor(rng, 0.03, 0.012)
        s = _add(s, _peak(TURN_HZ, rng.uniform(0.08, 0.11)))
        s = _add(s, _peak(UNKNOWN_HZ, rng.uniform(0.7, 0.85), 5.0))
        s = _add(s, _peak(UNKNOWN_HZ - 20, rng.uniform(0.1, 0.2)))
        s = _add(s, _peak(UNKNOWN_HZ + 20, rng.uniform(0.1, 0.2)))
    else:
        raise ValueError(f"unknown case {case!r}")
    return [round(clamp(x), 4) for x in s]


def feature_activity(spectrum: list[float]) -> dict[str, float]:
    """Raw (unmasked) activity of each feature, computed from the spectrum."""
    floor = statistics.median(spectrum)
    near_turn = [x for i, x in enumerate(spectrum) if abs(bin_hz(i) - TURN_HZ) <= BIN_HZ]
    line = max(near_turn) - floor
    band = [x for i, x in enumerate(spectrum) if 45.0 <= bin_hz(i) <= 400.0]
    return {
        "once_per_turn": round(clamp((line - 0.15) / 0.45), 3),
        "rattle": round(clamp((statistics.median(band) - 0.07) / 0.2), 3),
    }


def unknown_score(spectrum: list[float]) -> float:
    """How far the window is from what the stand-in model "knows".

    Stand-in for the TRD's hidden-layer distance: a peak above 220 Hz, where
    none of the three known cases put energy.
    """
    high = [x for i, x in enumerate(spectrum) if bin_hz(i) > 220.0]
    return round(max(0.0, (max(high) - statistics.median(high)) / 0.6), 3)


def apply_mask(activity: dict[str, float], mask: list[str]) -> dict[str, float]:
    """Zero every masked feature. Names not in the table are ignored."""
    return {name: (0.0 if name in mask else value) for name, value in activity.items()}


def diagnose(features: dict[str, float], u_score: float) -> tuple[str, float, str]:
    """Output layer over the (already masked) feature activity.

    Returns (diagnosis, confidence, reason).
    """
    if u_score > UNKNOWN_THRESHOLD:
        return "unknown", round(u_score / (u_score + UNKNOWN_THRESHOLD), 3), ""
    logits = {
        c: BIAS[c] + sum(w * features.get(f, 0.0) for f, w in WEIGHTS[c].items())
        for c in CLASS_NAMES
    }
    top = max(logits.values())
    exps = {c: math.exp(v - top) for c, v in logits.items()}
    best = max(CLASS_NAMES, key=lambda c: logits[c])
    confidence = round(exps[best] / sum(exps.values()), 3)
    if best == "normal":
        return best, confidence, ""
    feature = max(WEIGHTS[best], key=lambda f: WEIGHTS[best][f])
    return best, confidence, SENTENCES[feature]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def model_announcement(device_id: str = DEVICE_ID) -> dict:
    """Contract message 1."""
    return {
        "device_id": device_id,
        "model_version": MODEL_VERSION,
        "class_names": list(CLASS_NAMES),
        "unknown_threshold": UNKNOWN_THRESHOLD,
        "spectrum_hz_max": HZ_MAX,
        "features": [dict(f) for f in FEATURES],
        "simulated": True,
    }


def model_file() -> dict:
    """Small model file for the agent: TRD feature table shape, no weights."""
    return {
        "model_version": MODEL_VERSION,
        "class_names": list(CLASS_NAMES),
        "unknown_threshold": UNKNOWN_THRESHOLD,
        "features": [{"name": f["name"], "unit_indices": [], "sentence": f["sentence"]} for f in FEATURES],
        "simulated": True,
    }


def diagnose_window(
    spectrum: list[float],
    raw_activity: dict[str, float],
    u_score: float,
    mask: list[str],
    device_id: str,
    machine_id: str,
    at: str | None = None,
    simulated: bool = True,
) -> dict:
    """Mask, diagnose and build one contract message 2 record."""
    features = apply_mask(raw_activity, mask)
    diagnosis, confidence, reason = diagnose(features, u_score)
    return {
        "device_id": device_id,
        "machine_id": machine_id,
        "model_version": MODEL_VERSION,
        "at": at or now_iso(),
        "diagnosis": diagnosis,
        "confidence": confidence,
        "unknown_score": u_score,
        "reason": reason,
        "features": features,
        "mask": list(mask),
        "spectrum": list(spectrum),
        "simulated": simulated,
    }


def make_record(
    case: str,
    rng: random.Random,
    mask: list[str],
    device_id: str = DEVICE_ID,
    machine_id: str = MACHINE_ID,
    at: str | None = None,
) -> dict:
    """Generate one simulated window for a case and diagnose it under a mask."""
    spectrum = make_spectrum(case, rng)
    return diagnose_window(
        spectrum, feature_activity(spectrum), unknown_score(spectrum), mask, device_id, machine_id, at
    )


def replay_record(src: dict, mask: list[str], device_id: str, machine_id: str) -> dict:
    """Re-send a saved record now, re-diagnosing under the mask when possible.

    device_id and model_version are always the simulator's own, so the mask and
    model announcement line up. machine_id is kept from the file if present.
    "simulated" stays true unless the file says false explicitly.
    """
    simulated = src.get("simulated") is not False
    machine = src.get("machine_id") or machine_id
    if isinstance(src.get("features"), dict):
        raw = {name: float(src["features"].get(name, 0.0)) for name in FEATURE_NAMES}
        spectrum = src.get("spectrum") or [0.0] * N_BINS
        u = float(src.get("unknown_score", 0.0))
        return diagnose_window(spectrum, raw, u, mask, device_id, machine, simulated=simulated)
    out = dict(src)
    out.update(device_id=device_id, machine_id=machine, model_version=MODEL_VERSION, at=now_iso(), simulated=simulated)
    return out
