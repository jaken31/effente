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


def summarize_readings(records: list[dict]) -> dict:
    """Condense log records into what the agent needs. Spectra are left out."""
    if not records:
        return {"windows": 0}
    activity: dict[str, list[float]] = {}
    for record in records:
        for name, value in record.get("features", {}).items():
            activity.setdefault(name, []).append(value)
    return {
        "windows": len(records),
        "first_at": records[0].get("at"),
        "last_at": records[-1].get("at"),
        "diagnoses": dict(Counter(r.get("diagnosis") for r in records)),
        "max_unknown_score": max(r.get("unknown_score", 0.0) for r in records),
        "feature_activity": {
            name: {"mean": round(sum(values) / len(values), 3), "peak": round(max(values), 3)}
            for name, values in activity.items()
        },
    }
