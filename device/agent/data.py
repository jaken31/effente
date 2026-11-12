"""Read the device log and the verified feature table."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path


def load_log(path: str | Path, machine_id: str) -> list[dict]:
    """Return one machine's log records, oldest first. The log is one JSON object per line."""
    records = []
    with open(path, encoding="utf-8") as lines:
        for line in lines:
            if line.strip():
                record = json.loads(line)
                if record.get("machine_id") == machine_id:
                    records.append(record)
    return sorted(records, key=lambda r: r.get("at", ""))


def load_feature_table(path: str | Path) -> dict[str, str]:
    """Return verified feature name -> explanation sentence, from the model file."""
    with open(path, encoding="utf-8") as model_file:
        model = json.load(model_file)
    return {feature["name"]: feature["sentence"] for feature in model.get("features", [])}


def load_model_version(path: str | Path) -> str:
    """Return the model file's `model_version`, or "" if it has none."""
    with open(path, encoding="utf-8") as model_file:
        return str(json.load(model_file).get("model_version", ""))


def newest_model_version(records: list[dict]) -> tuple[list[dict], str, list[str]]:
    """Keep only the readings from the newest model version.

    Newest means the version of the latest reading, since a version is any
    string and cannot be ordered by itself. Returns (kept records, that
    version, every version seen in order of first appearance). A reading with
    no `model_version` counts as version "".
    """
    if not records:
        return [], "", []
    versions = list(dict.fromkeys(str(r.get("model_version", "")) for r in records))
    newest = str(records[-1].get("model_version", ""))
    kept = [r for r in records if str(r.get("model_version", "")) == newest]
    return kept, newest, versions


def summarize_readings(records: list[dict]) -> dict:
    """Condense log records into what the agent needs. Spectra are left out."""
    if not records:
        return {"windows": 0}
    activity: dict[str, list[float]] = {}
    masked: Counter = Counter()
    peak_by_diagnosis: dict[str, dict[str, float]] = {}
    for record in records:
        peaks = peak_by_diagnosis.setdefault(record.get("diagnosis"), {})
        for name, value in record.get("features", {}).items():
            activity.setdefault(name, []).append(value)
            peaks[name] = max(peaks.get(name, 0.0), round(value, 3))
        masked.update(record.get("mask", []))
    return {
        "windows": len(records),
        "first_at": records[0].get("at"),
        "last_at": records[-1].get("at"),
        "diagnoses": dict(Counter(r.get("diagnosis") for r in records)),
        "last_diagnosis": records[-1].get("diagnosis"),
        # Feature name -> windows in which it was switched off. Such a feature
        # reads 0 there, so a fault it detects cannot show in those windows.
        "masked_windows": dict(masked),
        # Diagnosis -> feature -> highest activity in windows with that diagnosis.
        "peak_by_diagnosis": peak_by_diagnosis,
        "max_unknown_score": max(r.get("unknown_score", 0.0) for r in records),
        "feature_activity": {
            name: {"mean": round(sum(values) / len(values), 3), "peak": round(max(values), 3)}
            for name, values in activity.items()
        },
    }
