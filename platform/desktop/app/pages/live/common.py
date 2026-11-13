"""Small helpers the Live and History pages share.

No labelling or judging rules here: names, verdicts and sensitivity words come
from app.data.logic. This only picks colours for kinds and formats times.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import math

from PySide6.QtWidgets import QLabel, QWidget

from ...data import logic
from ...theme import tokens as t

# Colour of a diagnosis kind (logic.diagnosis_kind). Shapes only.
KIND_COLORS = {
    "normal": t.NORMAL,
    "warming_up": t.MUTED,
    "stopped": t.MUTED,
    "none": t.MUTED,
    "unrecognised": t.ALERT,
    "fault": t.WARN,
}


def diagnosis_color(diagnosis) -> str:
    return KIND_COLORS.get(logic.diagnosis_kind(diagnosis), t.MUTED)


def diagnosis_pill(model, diagnosis, model_version=None) -> tuple[tuple[str, str], str | None]:
    """((text, colour), tooltip) for a diagnosis pill. A fault the model has not
    validated reads "possible" in a muted colour, with the tag as its tooltip."""
    tag = logic.diagnosis_tag(model, diagnosis, model_version)
    color = t.MUTED if tag else diagnosis_color(diagnosis)
    return (logic.diagnosis_text(model, diagnosis, model_version), color), tag or None


def verdict_color(verdict) -> str:
    return t.WARN if verdict == "real" else t.MUTED


def ms_of(iso) -> float | None:
    v = logic.parse_time(iso)
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else v


def clock(iso, utc: bool = False) -> str:
    """HH:MM:SS of an ISO time, local unless `utc`. "–" when it does not parse."""
    ms = ms_of(iso)
    if ms is None:
        return "–"
    when = dt.datetime.fromtimestamp(ms / 1000.0, dt.UTC)
    return (when if utc else when.astimezone()).strftime("%H:%M:%S")


def duration(seconds: float) -> str:
    s = round(seconds)
    return f"{s // 60}m {s % 60}s" if s >= 60 else f"{s}s"


def periods(n: int) -> str:
    return f"{n} period{'' if n == 1 else 's'}"


def signed(v: float) -> str:
    return ("+" if v >= 0 else "−") + f"{abs(v):.1f}"


def short(v: float) -> str:
    """A number with up to two decimals and at least one: 1.0, 0.25, 2.5."""
    text = f"{v:.2f}".rstrip("0")
    return text + "0" if text.endswith(".") else text


def top_feature(record) -> tuple[str, float] | None:
    """The reading that moved most in a record, by absolute spreads."""
    feats = (record or {}).get("features") or {}
    items = [(k, v) for k, v in feats.items() if isinstance(v, (int, float)) and not isinstance(v, bool)]
    return max(items, key=lambda kv: abs(kv[1])) if items else None


def evidence_count(record) -> int:
    feats = (record or {}).get("features") or {}
    return sum(1 for v in feats.values() if logic.is_evidence(v))


def fault_group(model, diagnosis) -> str:
    """The group the model announced for a fault type, capitalised; "" when none."""
    for ft in (model or {}).get("fault_types") or []:
        if isinstance(ft, dict) and ft.get("name") == diagnosis and ft.get("group"):
            return logic.capitalise(ft["group"])
    return ""


def code_html(name: str) -> str:
    """A reading's code name, set small and mono, as the mockup's .code."""
    from ...theme.tokens import Fonts

    return f'<span style="font-family:\'{Fonts.mono}\'; font-size:{t.FONT_MONO}px; color:{t.MUTED}">{name}</span>'


def label(text: str = "", role: str = "soft", parent: QWidget | None = None, font=None) -> QLabel:
    w = QLabel(text, parent)
    w.setProperty("role", role)
    if font is not None:
        w.setFont(font)
    return w


CSV_COLUMNS = ["seq", "at", "device_id", "machine_id", "model_version", "diagnosis", "diagnosis_this_minute",
               "confidence", "unknown_score", "sensitivity", "alarm_line", "abnormal_minutes", "out_of_range",
               "wrong", "reason", "note", "features", "problems", "simulated"]


def write_records_csv(path: str, records: list) -> int:
    """Write records, as stored, to a CSV file (nested fields as JSON). Returns the row count."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(CSV_COLUMNS)
        for r in records:
            w.writerow([json.dumps(r.get(c)) if isinstance(r.get(c), (dict, list)) else r.get(c, "")
                        for c in CSV_COLUMNS])
    return len(records)
