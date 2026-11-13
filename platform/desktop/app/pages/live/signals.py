"""The Live page's signals table: one row per signal, with a value, context,
a 5-minute sparkline and a link to the page that explains it.

Rows hold live widgets (FlagValue, Sparkline), so this is a column of row
widgets styled like DataTable rather than a QTableView.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from ...data import logic
from ...theme import fonts
from ...theme import tokens as t
from ...widgets import FlagValue, Sparkline
from .common import code_html, evidence_count, label, short, signed, top_feature

NAME_W = 230
VALUE_W = 133  # the mockup's 120px cell, as wide as the browser lays it out
SPARK_W = 240
CHEV_W = 36
ROW_H = 47  # the mockup: a 46px cell and its 1px rule
VIBRATION_LIMIT_MM_S = 11  # contract "Problems": vibration_over_limit, the limit is 11


@dataclass
class SignalSpec:
    key: str
    name: str
    value: str
    abnormal: bool
    context: str  # rich text
    values: list
    lo: float
    hi: float
    threshold: float | None
    go: str


class _HeaderRow(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedHeight(32)  # th: 8 + 8 padding and an 11px label, as the browser lays it out
        lay = QHBoxLayout(self)
        lay.setContentsMargins(t.CELL_PAD_H, 0, t.CELL_PAD_H, 1)
        lay.setSpacing(2 * t.CELL_PAD_H)
        f = fonts.table_header()
        cols = [("Signal", NAME_W, Qt.AlignmentFlag.AlignLeft), ("Now", VALUE_W, Qt.AlignmentFlag.AlignRight),
                ("Context", None, Qt.AlignmentFlag.AlignLeft), ("Last 5 min", SPARK_W, Qt.AlignmentFlag.AlignLeft),
                ("", CHEV_W - 2 * t.CELL_PAD_H, Qt.AlignmentFlag.AlignLeft)]
        for text, w, al in cols:
            lab = label(text, "muted", self, f)
            lab.setAlignment(al | Qt.AlignmentFlag.AlignVCenter)
            if w is None:
                lab.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
                lay.addWidget(lab, 1)
            else:
                lab.setFixedWidth(w - 2 * t.CELL_PAD_H if w > CHEV_W else max(0, w))
                lay.addWidget(lab)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setPen(QPen(t.qcolor(t.BORDER), 1))
        y = self.height() - 0.5
        p.drawLine(QPointF(0, y), QPointF(self.width(), y))


class SignalRow(QWidget):
    clicked = Signal(str)

    def __init__(self, key: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.key = key
        self.go = ""
        self.last = False
        self.setFixedHeight(ROW_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(t.CELL_PAD_H, 0, t.CELL_PAD_H, 0)
        lay.setSpacing(2 * t.CELL_PAD_H)
        self.name = label("", "strong", self, fonts.font(t.FONT_TABLE))
        self.name.setFixedWidth(NAME_W - 2 * t.CELL_PAD_H)
        self.value = FlagValue("", False, self, font=fonts.big_value(21), alignment=Qt.AlignmentFlag.AlignRight)
        self.value.setFixedWidth(VALUE_W - 2 * t.CELL_PAD_H)
        self.value.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        self.context = label("", "muted", self, fonts.font(t.FONT_TABLE))
        self.context.setTextFormat(Qt.TextFormat.RichText)
        self.context.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.spark = Sparkline(parent=self)
        self.spark.setFixedWidth(SPARK_W - 2 * t.CELL_PAD_H)
        self.chev = QLabel("→", self)
        self.chev.setProperty("role", "muted")
        self.chev.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.chev.setFixedWidth(CHEV_W - 2 * t.CELL_PAD_H)
        lay.addWidget(self.name)
        lay.addWidget(self.value)
        lay.addWidget(self.context, 1)
        lay.addWidget(self.spark)
        lay.addWidget(self.chev)

    def set_spec(self, s: SignalSpec) -> None:
        self.go = s.go
        self.name.setText(s.name)
        self.value.set_value(s.value, s.abnormal)
        self.context.setText(s.context)
        self.spark.set_values(s.values, s.lo, s.hi, s.threshold)

    def event(self, e):
        if e.type() in (e.Type.HoverEnter, e.Type.HoverLeave):
            self.chev.setProperty("role", "strong" if e.type() == e.Type.HoverEnter else "muted")
            self.chev.style().unpolish(self.chev)
            self.chev.style().polish(self.chev)
            self.update()
        return super().event(e)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        if self.underMouse():
            p.fillRect(self.rect(), t.qcolor(t.HOVER))
        if not self.last:
            p.setPen(QPen(t.qcolor(t.GRID), 1))
            y = self.height() - 0.5
            p.drawLine(QPointF(0, y), QPointF(self.width(), y))

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit(self.go)
        super().mouseReleaseEvent(e)


class SignalsTable(QWidget):
    navigate = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(0)
        self._lay.addWidget(_HeaderRow(self))
        self._rows: dict[str, SignalRow] = {}

    def rows(self) -> list[SignalRow]:
        return [r for r in self._rows.values() if not r.isHidden()]

    def row(self, key: str) -> SignalRow | None:
        r = self._rows.get(key)
        return r if r is not None and not r.isHidden() else None

    def set_specs(self, specs: list[SignalSpec]) -> None:
        wanted = {s.key for s in specs}
        for s in specs:
            r = self._rows.get(s.key)
            if r is None:
                r = SignalRow(s.key, self)
                r.clicked.connect(self.navigate.emit)
                self._rows[s.key] = r
                self._lay.addWidget(r)
            r.set_spec(s)
            r.show()
        for k, r in self._rows.items():
            if k not in wanted:
                r.hide()
        shown = self.rows()
        for i, r in enumerate(shown):
            r.last = i == len(shown) - 1
            r.update()


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def build_specs(state, records: list) -> list[SignalSpec]:
    """The rows for the newest record and the last 5 minutes of records."""
    rec = state.latest() or {}
    score = rec.get("unknown_score")
    line = rec.get("alarm_line") if _num(rec.get("alarm_line")) else logic.alarm_line(rec.get("sensitivity"))
    sens = state.sensitivity()
    wanted_line = logic.alarm_line(sens["wanted"]) if sens["wanted"] is not None else None
    specs: list[SignalSpec] = []

    if line is None:
        ctx = "Sensitivity not reported, so no alarm line"
    else:
        ctx = f"Device alarms above {short(line)}"
        if wanted_line is not None and abs(wanted_line - line) > 1e-6:
            ctx += f" · your line {wanted_line:.2f}, on its way"
    specs.append(SignalSpec(
        "score", "Alarm score", f"{score:.2f}×" if _num(score) else "–",
        bool(_num(score) and line is not None and score > line), ctx,
        [r.get("unknown_score") if _num(r.get("unknown_score")) else None for r in records],
        0.0, 4.0, line, "history"))

    conf = rec.get("confidence")
    judged = _num(score)
    specs.append(SignalSpec(
        "confidence", "Confidence", f"{round(conf * 100)}%" if judged and _num(conf) else "–", False,
        "How sure the device is of its label",
        [r.get("confidence") if _num(r.get("unknown_score")) and _num(r.get("confidence")) else None for r in records],
        0.3, 1.0, None, "history"))

    n_ev = evidence_count(rec)
    top = top_feature(rec)
    if top:
        texts = state.feature_texts()
        ctx = f"{logic.capitalise(texts.get(top[0], top[0]))} {code_html(top[0])} {signed(top[1])} spreads"
    else:
        ctx = "None yet"
    specs.append(SignalSpec(
        "moved", "Readings that moved", str(n_ev) if rec.get("features") is not None and rec else "–", n_ev > 0, ctx,
        [evidence_count(r) if r.get("features") else None for r in records], 0.0, 5.0, None, "evidence"))

    specs.append(_velocity_spec(state, rec, records))

    heat = logic.heat_view(rec) if rec else {"kind": "none"}
    if heat["kind"] == "camera" and heat.get("max_c") is not None:
        bg = heat.get("background_c")
        rise = f", +{heat['max_c'] - bg:.1f} °C over room" if bg is not None else ""
        vals = [(r.get("heat") or {}).get("max_c") for r in records]
        vals = [v if _num(v) else None for v in vals]
        hi = max([40.0] + [v for v in vals if v is not None])
        specs.append(SignalSpec(
            "heat", "Hottest point", f"{heat['max_c']:.1f} °C", _hot_problem(state),
            f"Hottest pixel{rise} · optional sensor", vals, 0.0, hi, None, "thermal"))
    elif heat["kind"] == "imu" and heat.get("imu_temp_c") is not None:
        vals = [(r.get("heat") or {}).get("imu_temp_c") for r in records]
        vals = [v if _num(v) else None for v in vals]
        hi = max([40.0] + [v for v in vals if v is not None])
        specs.append(SignalSpec(
            "heat", "Hottest point", f"{heat['imu_temp_c']:.1f} °C", _hot_problem(state),
            "IMU temperature, no camera · optional sensor", vals, 0.0, hi, None, "thermal"))
    return specs


def _hot_problem(state) -> bool:
    return any(p.get("name") in ("too_hot", "hotter_than_normal", "heating_fast") for p in state.problems())


def _velocity_spec(state, rec: dict, records: list) -> SignalSpec:
    """Records carry no velocity in mm/s (contract 3.3). The value shown is the
    one a vibration problem sends; the context gives the spreads the model saw."""
    probs = {p.get("name"): p for p in state.problems()}
    shown = probs.get("vibration_over_limit") or probs.get("vibration_above_normal")
    value = logic.problem_value_text(shown) if shown else ""
    feats = rec.get("features") or {}
    spreads = feats.get("vib_velocity_mm_s")
    margin = logic.margins(state.sensitivity()["reported"])["vibration"]
    bits = []
    if _num(spreads):
        bits.append(f"{signed(spreads)} spreads {code_html('vib_velocity_mm_s')}")
    bits.append(f"wrong above {short(margin)}\u00d7 normal")
    bits.append(f"limit {VIBRATION_LIMIT_MM_S} mm/s for any machine")
    vals = [(r.get("features") or {}).get("vib_velocity_mm_s") for r in records]
    return SignalSpec("velocity", "Vibration velocity", value or "–", bool(shown), " · ".join(bits),
                      [v if _num(v) else None for v in vals], -2.0, 10.0, None, "spectrum")
