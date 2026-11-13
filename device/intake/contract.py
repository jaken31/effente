"""What the device's results lines must carry for platform/CONTRACT.md (version 1, track 5's).

The contract's log record (message 2) needs `device_id`, `machine_id`, `model_version`, `at` and a
`diagnosis` that is one of normal, imbalance, loose_mount or unknown. Our model names 21 fault types
(effente.fault_types) and says warming_up, stopped or unrecognised_pattern when it is not naming one,
so the results line carries both:

  diagnosis          the contract's value, mapped by DIAGNOSIS below (anything without a contract name
                     is "unknown")
  diagnosis_detail   our own name, unchanged: unbalance_like, looseness_or_rattle, warming_up, ...

The contract's `features` are 0-to-1 activities of switch-off-verified features after the mask. Our
model gives signed deviations of effente features in spreads (e.g. vib_1x +6.2), and none has a
switch-off test, so those go in `deviations` and `features` is empty. A model that reports a `mask`
(it applies masks itself, like platform/sim) keeps its own `features`.

What the contract cannot express (the fault types, the safety layer, sensitivity, the spectrum's
shape) is in device/intake/CONTRACT_NOTES.md, not worked around here.
"""

from __future__ import annotations

import hashlib
import math
from datetime import datetime, timezone
from pathlib import Path

from effente import fault_types as FT

CONTRACT_VERSION = 1
DIAGNOSES = ("normal", "imbalance", "loose_mount", "unknown")
REQUIRED = ("device_id", "machine_id", "model_version", "at", "diagnosis")
NO_MODEL = "none"  # model_version when no model is loaded

# Our names with a contract name. Every other name (the other 19 fault types, unrecognised_pattern,
# warming_up, stopped, no model, an error) is "unknown": the contract has no "not judged yet".
DIAGNOSIS = {
    "normal": "normal",
    "unbalance_like": "imbalance",
    "looseness_or_rattle": "loose_mount",
}


def iso_utc(when: datetime | None = None) -> str:
    """The contract's time format: ISO 8601 UTC with a Z, e.g. 2026-11-14T16:02:11Z."""
    when = when or datetime.now(timezone.utc)
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def contract_diagnosis(detail) -> str:
    if detail in DIAGNOSES:
        return detail
    return DIAGNOSIS.get(detail, "unknown")


def _reason(diagnosis: str, detail) -> str:
    """The contract's one line: empty for normal and unknown; otherwise the fault type's sentence
    (effente.fault_types; its signatures are starting values, not verified by a switch-off test)."""
    if diagnosis in ("normal", "unknown"):
        return ""
    return FT.TYPES[FT.NAMES.index(detail)].text if detail in FT.NAMES else ""


def _finite(v) -> bool:
    try:
        return v is not None and not isinstance(v, bool) and math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


def record_fields(result, status: str | None = None) -> dict:
    """The contract fields of one results line, from the model's result (a dict, or None)."""
    r = result if isinstance(result, dict) else {}
    if r.get("diagnosis") is not None:
        detail = r["diagnosis"]
    elif "error" in r:
        detail = "error"
    else:
        detail = "no_model" if result is None else (status or "unknown")
    diagnosis = contract_diagnosis(detail)
    out = {"diagnosis": diagnosis, "diagnosis_detail": str(detail),
           "reason": r["reason"] if isinstance(r.get("reason"), str) else _reason(diagnosis, detail)}
    if _finite(r.get("confidence")):
        out["confidence"] = min(max(float(r["confidence"]), 0.0), 1.0)
    if _finite(r.get("unknown_score")):
        out["unknown_score"] = max(float(r["unknown_score"]), 0.0)
    if "mask" in r:  # the model applies masks itself: its features are the contract's
        out["mask"] = list(r["mask"])
        if isinstance(r.get("features"), dict):
            out["features"] = r["features"]
    else:  # our model cannot switch features off: nothing was masked, no verified features
        out["mask"], out["features"] = [], {}
        if isinstance(r.get("features"), dict):
            out["deviations"] = r["features"]
    for k in ("wrong", "what", "details", "problems"):  # the device's own answer (effente.safety): what is
        # going on in plain words ("what"), and the specific fault type and checks behind it ("details")
        if k in r:
            out[k] = r[k]
    return out


def model_version_of(*paths) -> str:
    """A model_version that changes when any of the model's files changes: the first file's name and
    a hash of the files' bytes, e.g. rec-20261112-101500-3f2a9c1e. "none" without a file."""
    files = [Path(p) for p in paths if p]
    if not files:
        return NO_MODEL
    h = hashlib.sha1()
    for p in files:
        try:
            h.update(p.read_bytes())
        except OSError:
            h.update(b"missing")
    return f"{files[-1].stem}-{h.hexdigest()[:8]}"


def is_simulated(source) -> bool:
    """True when readings come from a simulator (effente.simulate_*, or site noise around one), or a
    replay of a folder with "fake" in its path. A replay of a real recording counts as real."""
    seen = 0
    while source is not None and seen < 5:
        mod = type(source).__module__ or ""
        if mod.startswith("effente.simulate"):
            return True
        folder = getattr(source, "folder", None)
        if folder is not None and any("fake" in part.lower() for part in Path(folder).parts):
            return True
        source = getattr(source, "src", None)  # effente.site_noise.SiteNoise wraps a source
        seen += 1
    return False


def check_record(rec: dict) -> list[str]:
    """What in one results line breaks the contract's log record (empty list: nothing). The spectrum's
    shape is not checked: ours is known to differ (CONTRACT_NOTES.md)."""
    bad = []
    for k in REQUIRED:
        if not isinstance(rec.get(k), str) or not rec[k]:
            bad.append(f"{k} missing or not a non-empty string")
    if rec.get("diagnosis") not in DIAGNOSES:
        bad.append(f"diagnosis {rec.get('diagnosis')!r} is not one of {DIAGNOSES}")
    at = rec.get("at")
    if isinstance(at, str):
        try:
            datetime.strptime(at, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            bad.append(f"at {at!r} is not ISO 8601 UTC with a Z")
    if "confidence" in rec and not (_finite(rec["confidence"]) and 0 <= rec["confidence"] <= 1):
        bad.append("confidence not from 0 to 1")
    if "unknown_score" in rec and not (_finite(rec["unknown_score"]) and rec["unknown_score"] >= 0):
        bad.append("unknown_score not 0 or more")
    if not isinstance(rec.get("reason", ""), str):
        bad.append("reason not a string")
    elif rec.get("diagnosis") in ("normal", "unknown") and rec.get("reason"):
        bad.append("reason must be empty for normal and unknown")
    feats = rec.get("features", {})
    if not isinstance(feats, dict) or not all(_finite(v) and 0 <= v <= 1 for v in feats.values()):
        bad.append("features must map names to 0..1")
    mask = rec.get("mask", [])
    if not isinstance(mask, list) or not all(isinstance(n, str) for n in mask):
        bad.append("mask must be a list of names")
    if "simulated" in rec and not isinstance(rec["simulated"], bool):
        bad.append("simulated not true or false")
    return bad
