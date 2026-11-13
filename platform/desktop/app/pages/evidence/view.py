"""What the Evidence page shows, worked out from the record and the model.

No Qt here, so it can be tested without a display. Labels come from
`app.data.logic`; this module only picks and orders what the record holds.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.data import logic

# The centred bar fills at this many spreads either way.
MOVED_BAR_FULL = 10.0
# A fault type's evidence bar fills at this score.
FAULT_BAR_FULL = 12.0


def _number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


@dataclass(frozen=True)
class MovedRow:
    name: str
    text: str
    value: float
    evidence: bool

    @property
    def value_text(self) -> str:
        return f"{self.value:+.1f} spreads"

    @property
    def fraction(self) -> float:
        """Signed share of one half of the bar, -1 to 1."""
        return max(-1.0, min(1.0, self.value / MOVED_BAR_FULL))


def moved_rows(record: dict | None, model: dict | None) -> list[MovedRow]:
    """The record's `features`, largest move first, with the model's text for each."""
    feats = (record or {}).get("features")
    if not isinstance(feats, dict):
        return []
    texts = feature_texts(model)
    rows = [MovedRow(n, texts.get(n, ""), float(v), logic.is_evidence(v)) for n, v in feats.items() if _number(v)]
    rows.sort(key=lambda r: -abs(r.value))
    return rows


def feature_texts(model: dict | None) -> dict[str, str]:
    out = {}
    for f in (model or {}).get("features") or []:
        if isinstance(f, dict) and f.get("name"):
            out[f["name"]] = f.get("text") or ""
    return out


def fault_types(model: dict | None) -> list[dict]:
    return [t for t in (model or {}).get("fault_types") or [] if isinstance(t, dict) and t.get("name")]


def is_abnormal(record: dict | None) -> bool:
    return bool(record) and logic.is_abnormal(record.get("diagnosis"))


@dataclass(frozen=True)
class FaultEvidence:
    name: str
    label: str
    group: str
    text: str
    score: float
    named: bool  # the type the device names for this stretch
    status: str

    @property
    def fraction(self) -> float:
        return max(0.0, min(1.0, self.score / FAULT_BAR_FULL))


def fault_evidence(record: dict | None, model: dict | None) -> list[FaultEvidence]:
    """The record's `fault_types`, strongest first. Empty unless abnormal."""
    if not is_abnormal(record):
        return []
    scores = record.get("fault_types")
    if not isinstance(scores, dict):
        return []
    known = {t["name"]: t for t in fault_types(model)}
    rows = []
    for name, score in scores.items():
        if not _number(score):
            continue
        t = known.get(name, {})
        rows.append(FaultEvidence(
            name=name,
            label=logic.display_name(name),
            group=_cap(t.get("group", "")),
            text=_cap(t.get("text", "")),
            score=float(score),
            named=name == record.get("diagnosis"),
            status=logic.fault_type_tag(t or None),
        ))
    rows.sort(key=lambda r: -r.score)
    return rows


def fault_evidence_hint(record: dict | None) -> str:
    if not is_abnormal(record):
        return "only sent while the machine is abnormal"
    name = logic.display_name(record.get("diagnosis")).lower()
    return f"strongest first · device names {name} (most often this stretch)"


def fault_evidence_empty(record: dict | None) -> str:
    if not record:
        return "No readings yet."
    if not is_abnormal(record):
        return "Sent only while the machine is abnormal. The model reads " + logic.display_name(record.get("diagnosis")) + "."
    return "Abnormal, but no known fault type fits: " + logic.display_name(record.get("diagnosis")) + "."


@dataclass(frozen=True)
class FaultTypeRow:
    group: str
    name: str
    label: str
    text: str
    status: str
    evidence: float | None


def fault_type_rows(record: dict | None, model: dict | None) -> list[FaultTypeRow]:
    """Every type the model can name, in the model's order, with its evidence now."""
    now = (record or {}).get("fault_types") if is_abnormal(record) else None
    now = now if isinstance(now, dict) else {}
    rows = []
    for t in fault_types(model):
        v = now.get(t["name"])
        rows.append(FaultTypeRow(
            group=_cap(t.get("group", "")),
            name=t["name"],
            label=logic.display_name(t["name"]),
            text=t.get("text", ""),
            status=logic.fault_type_tag(t),
            evidence=float(v) if _number(v) else None,
        ))
    return rows


def fault_types_hint(model: dict | None) -> str:
    types = fault_types(model)
    if not types:
        return "no model announced yet"
    validated = sum(1 for t in types if t.get("validated") is True)
    if validated == 0:
        return f"{len(types)} types from the model · none validated yet, so all show as \"possible\""
    return f"{len(types)} types from the model · {validated} validated, the rest show as \"possible\""


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:] if text else ""
