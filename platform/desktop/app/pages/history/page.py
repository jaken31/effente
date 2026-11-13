"""The History page: trends, alarms with their answers, and every record.

Built from the mockup's renderHistory. Labels come from app.data.logic. The
diagnosis band shows what the device said: the laptop never relabels records.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QPainter, QPen, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ...data import logic
from ...theme import fonts
from ...theme import tokens as t
from ...widgets import Button, DataTable, Panel
from ..live.charts import DiagnosisBand, LineChart, LineChartSpec, RefLine, Series
from ..live.common import (
    clock,
    diagnosis_color,
    fault_group,
    label,
    ms_of,
    short,
    signed,
    top_feature,
    verdict_color,
    write_records_csv,
)

RANGES = [("300", "Last 5 min"), ("3600", "Last 1 h"), ("all", "All")]
FILTERS = [("", "All"), ("abn", "Abnormal only"), ("normal", "Normal only")]
ALARMS_MAX_H = 300
RECORDS_MAX_H = 360
RECORDS_SHOWN = 400


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


class _Swatch(QWidget):
    """A 10px legend swatch: solid, or a dashed outline."""

    def __init__(self, color: str, dashed: bool, parent: QWidget | None = None):
        super().__init__(parent)
        self._color, self._dashed = color, dashed
        self.setFixedSize(10, 10)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(0.5, 0.5, 9, 9)
        if self._dashed:
            pen = QPen(t.qcolor(self._color), 1)
            pen.setDashPattern([2, 2])
            p.setPen(pen)
            p.drawRoundedRect(r, 2, 2)
        else:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(t.qcolor(self._color))
            p.drawRoundedRect(r, 2, 2)


class Legend(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(8, 0, 0, 0)
        self._lay.setSpacing(16)
        self._names: list[str] = []

    def names(self) -> list[str]:
        return list(self._names)

    def set_items(self, items: list[tuple[str, str, bool]]) -> None:
        if [n for n, _, _ in items] == self._names:
            return
        while self._lay.count():
            w = self._lay.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        self._names = [n for n, _, _ in items]
        for name, color, dashed in items:
            cell = QWidget(self)
            cl = QHBoxLayout(cell)
            cl.setContentsMargins(0, 0, 0, 0)
            cl.setSpacing(6)
            cl.addWidget(_Swatch(color, dashed, cell))
            cl.addWidget(label(name, "soft", cell, fonts.font(t.FONT_SMALL)))
            self._lay.addWidget(cell)


class _AnswerCell(QWidget):
    """Real Problem and False Alarm, inside an alarms table row."""

    answered = Signal(str, str)

    def __init__(self, alarm_id: str, parent: QWidget | None = None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(t.CELL_PAD_H, 0, 0, 0)
        lay.setSpacing(6)
        self.alarm_id = alarm_id
        self.btn_real = Button("Real Problem", "secondary", self, small=True)
        self.btn_false = Button("False Alarm", "secondary", self, small=True)
        f = fonts.font(t.FONT_SMALL - 1, 500)
        for b in (self.btn_real, self.btn_false):
            b.setFont(f)
            lay.addWidget(b)
        lay.addStretch(1)
        self.btn_real.clicked.connect(lambda: self.answered.emit(alarm_id, "real"))
        self.btn_false.clicked.connect(lambda: self.answered.emit(alarm_id, "false"))


class _FitTable(DataTable):
    """A DataTable as tall as its rows, up to a cap; it scrolls past that."""

    def __init__(self, cap: int, parent: QWidget | None = None):
        super().__init__(parent)
        self._cap = cap
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def fit(self) -> None:
        rows = self.model().rowCount() if self.model() else 0
        h = self.horizontalHeader().height() + rows * t.ROW_H + 2
        self.setFixedHeight(min(self._cap, h))

    def sizeHint(self) -> QSize:
        return QSize(super().sizeHint().width(), self.height())


def _item(text, *, right=False, strong=False, mono=False, flag=False, pill=None, tip=None) -> QStandardItem:
    it = QStandardItem("" if text is None else str(text))
    if right:
        it.setData(int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter), Qt.ItemDataRole.TextAlignmentRole)
    if strong:
        it.setData(True, DataTable.StrongRole)
    if mono:
        it.setData(True, DataTable.MonoRole)
    if flag:
        it.setData(True, DataTable.FlagRole)
    if pill:
        it.setData(pill, DataTable.PillRole)
    if tip:
        it.setToolTip(tip)
    return it


def _header(model: QStandardItemModel, names: list[str], right: set[int]) -> None:
    model.setHorizontalHeaderLabels(names)
    for i in right:
        model.setHeaderData(i, Qt.Orientation.Horizontal,
                            int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                            Qt.ItemDataRole.TextAlignmentRole)


class HistoryPage(QWidget):
    key = "history"
    title = "History"
    subtitle = "every period and alarm for this machine"

    navigate = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.state = None
        # The shell puts each page in a scroll area with the content padding.
        content = self
        col = QVBoxLayout(content)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(t.PAGE_GAP)

        # ---- trends
        self.trends = Panel("Trends", "", content)
        self.legend = Legend(self.trends.head)
        self.trends.head.layout().insertWidget(1, self.legend)
        self.range = QComboBox(self.trends)
        for k, text in RANGES:
            self.range.addItem(text, k)
        self.range.currentIndexChanged.connect(lambda _i: self.refresh())
        self.btn_export = Button("Export CSV", "secondary", self.trends, small=True)
        self.btn_export.clicked.connect(self._export_dialog)
        self.trends.header_end.addWidget(self.range)
        self.trends.header_end.addWidget(self.btn_export)
        charts = QWidget(self.trends)
        cl = QVBoxLayout(charts)
        cl.setContentsMargins(14, 12, 14, 8)
        cl.setSpacing(0)
        self.band = DiagnosisBand("Diagnosis", 124, charts)
        self.score_chart = LineChart(84, parent=charts)
        self.conf_chart = LineChart(70, parent=charts)
        self.spread_chart = LineChart(96, parent=charts)
        cl.addWidget(self.band)
        cl.addSpacing(8)
        for c in (self.score_chart, self.conf_chart, self.spread_chart):
            cl.addWidget(c)
            cl.addSpacing(14)
        self.trends.add(charts)
        col.addWidget(self.trends)

        # ---- alarms
        self.alarms_panel = Panel("Alarms", "", content)
        self.alarms_table = _FitTable(ALARMS_MAX_H, self.alarms_panel)
        self.alarms_model = QStandardItemModel(0, 7, self.alarms_table)
        _header(self.alarms_model, ["Started", "Ended", "Diagnosis", "Group", "Periods", "Model", "Answer"], {4})
        self.alarms_table.setModel(self.alarms_model)
        self._alarm_cols([117, 117, 296, 233, 76, 221])
        self.alarms_panel.add(self.alarms_table)
        col.addWidget(self.alarms_panel)

        # ---- records
        self.records_panel = Panel("Records", "", content)
        self.filter = QComboBox(self.records_panel)
        for k, text in FILTERS:
            self.filter.addItem(text, k)
        self.filter.setMinimumWidth(124)
        self.filter.currentIndexChanged.connect(lambda _i: self._show_records())
        self.records_panel.header_end.addWidget(self.filter)
        self.records_table = _FitTable(RECORDS_MAX_H, self.records_panel)
        self.records_model = QStandardItemModel(0, 8, self.records_table)
        _header(self.records_model, ["Seq", "Time (UTC)", "Diagnosis", "This period", "Confidence", "Score",
                                     "Periods", "Top reading"], {4, 5, 6})
        self.records_table.setModel(self.records_model)
        hh = self.records_table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for i, w in enumerate([72, 129, 294, 211, 105, 90, 108]):
            self.records_table.setColumnWidth(i, w)
        self.records_panel.add(self.records_table)
        col.addWidget(self.records_panel)
        col.addStretch(1)

    def minimumSizeHint(self) -> QSize:
        # The shell's scroll area squeezes a page to its minimum height; this page
        # scrolls instead, so its minimum height is its natural height.
        hint = super().minimumSizeHint()
        return QSize(hint.width(), max(hint.height(), self.sizeHint().height()))

    def _alarm_cols(self, widths: list[int]) -> None:
        hh = self.alarms_table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for i, w in enumerate(widths):
            self.alarms_table.setColumnWidth(i, w)

    # ---- binding ---------------------------------------------------------------

    def bind(self, state) -> None:
        self.state = state
        for sig in (state.snapshot_changed, state.alarms_changed, state.settings_changed):
            sig.connect(self.refresh)
        state.record_added.connect(lambda _r: self.refresh())
        self.refresh()

    def refresh(self) -> None:
        if self.state is None:
            return
        self._show_trends()
        self._show_alarms()
        self._show_records()

    def trend_records(self) -> list:
        records = self.state.records(logic.HISTORY_CAP)
        k = self.range.currentData()
        if k == "all" or not records:
            return records
        end = ms_of(records[-1].get("at"))
        if end is None:
            return records
        span = float(k) * 1000
        return [r for r in records if (ms_of(r.get("at")) or end) >= end - span]

    # ---- trends --------------------------------------------------------------------

    def _show_trends(self) -> None:
        rs = self.trend_records()
        self.band.set_items([(logic.display_name(r.get("diagnosis")) or "–", diagnosis_color(r.get("diagnosis")))
                             for r in rs])
        newest = rs[-1] if rs else {}
        line = newest.get("alarm_line") if _num(newest.get("alarm_line")) else logic.alarm_line(newest.get("sensitivity"))
        lines = []
        if line is not None:
            lines.append(RefLine(line, t.ALERT, f"device line {short(line)}"))
        wanted = self.state.sensitivity()["wanted"]
        wanted_line = logic.alarm_line(wanted) if wanted is not None else None
        if wanted_line is not None and (line is None or abs(wanted_line - line) > 1e-6):
            lines.append(RefLine(wanted_line, t.DOT, f"your line {wanted_line:.2f}", (2, 3)))
        self.score_chart.set_data(
            [Series([r.get("unknown_score") if _num(r.get("unknown_score")) else None for r in rs], t.DOT)],
            LineChartSpec("Alarm score", 0, 4, [0, 2, 4], 1, lines))
        self.conf_chart.set_data(
            [Series([r.get("confidence") if _num(r.get("unknown_score")) and _num(r.get("confidence")) else None
                     for r in rs], t.DOT)],
            LineChartSpec("Confidence", 0, 1, [0, 1], 1))
        names = self._spread_names(rs)
        series = [Series([(r.get("features") or {}).get(n) if _num((r.get("features") or {}).get(n)) else None
                          for r in rs], t.DOT if i == 0 else t.SOFT, dashed=i == 1) for i, n in enumerate(names)]
        self.legend.set_items([(n, t.DOT if i == 0 else t.SOFT, i == 1) for i, n in enumerate(names)])
        self.spread_chart.set_data(
            series,
            LineChartSpec("Spreads", -2, 10, [0, logic.EVIDENCE_SPREADS, 10], 0,
                          [RefLine(logic.EVIDENCE_SPREADS, t.MUTED, f"evidence at {logic.EVIDENCE_SPREADS:g}", (2, 4))],
                          [clock(r.get("at")) for r in rs]))

    @staticmethod
    def _spread_names(rs: list) -> list[str]:
        """The two readings that moved most over the range, for the spreads chart."""
        peak: dict[str, float] = {}
        for r in rs:
            for k, v in (r.get("features") or {}).items():
                if _num(v):
                    peak[k] = max(peak.get(k, 0.0), abs(v))
        return [k for k, _ in sorted(peak.items(), key=lambda kv: -kv[1])[:2]]

    # ---- alarms ----------------------------------------------------------------------

    def _show_alarms(self) -> None:
        alarms = self.state.alarms()
        unanswered = sum(1 for a in alarms if logic.is_unanswered(a))
        self.alarms_panel.set_hint(f"{unanswered} unanswered · each alarm asks one question: "
                                   "real problem or false alarm?")
        m = self.alarms_model
        m.removeRows(0, m.rowCount())
        model = self.state.model() or {}
        for a in alarms:
            diag = a.get("diagnosis")
            ans = a.get("answer")
            source = model.get("source") if model.get("model_version") == a.get("model_version") else None
            model_text = " · ".join(str(x) for x in (a.get("library_entry") or a.get("model_version"), source) if x)
            answer_item = _item("")
            if ans:
                answer_item = _item("", pill=(logic.verdict_text(ans.get("verdict")), verdict_color(ans.get("verdict"))),
                                    tip=f"by {ans.get('by')}" if ans.get("by") else None)
            m.appendRow([
                _item(clock(a.get("start"))),
                _item("ongoing" if a.get("open") else clock(a.get("end")), strong=bool(a.get("open"))),
                _item("", pill=(logic.display_name(diag), diagnosis_color(diag))),
                _item(fault_group(model, diag) or "–"),
                _item(a.get("periods", ""), right=True),
                _item(model_text or "–"),
                answer_item,
            ])
            if not ans:
                cell = _AnswerCell(a["alarm_id"], self.alarms_table)
                cell.answered.connect(self._answer)
                self.alarms_table.setIndexWidget(m.index(m.rowCount() - 1, 6), cell)
        self.alarms_table.fit()

    def answer_cells(self) -> list[_AnswerCell]:
        out = []
        for row in range(self.alarms_model.rowCount()):
            w = self.alarms_table.indexWidget(self.alarms_model.index(row, 6))
            if isinstance(w, _AnswerCell):
                out.append(w)
        return out

    def _answer(self, alarm_id: str, verdict: str) -> None:
        self.state.answer_alarm(alarm_id, verdict)

    # ---- records ---------------------------------------------------------------------

    def filtered_records(self) -> list:
        k = self.filter.currentData()
        rs = self.state.records(logic.HISTORY_CAP)
        if k == "abn":
            rs = [r for r in rs if logic.is_abnormal(r.get("diagnosis"))]
        elif k == "normal":
            rs = [r for r in rs if not logic.is_abnormal(r.get("diagnosis"))]
        return list(reversed(rs))

    def _show_records(self) -> None:
        if self.state is None:
            return
        rows = self.filtered_records()
        self.records_panel.set_hint(f"{len(rows)} period{'' if len(rows) == 1 else 's'}")
        m = self.records_model
        m.removeRows(0, m.rowCount())
        for r in rows[:RECORDS_SHOWN]:
            judged = _num(r.get("unknown_score"))
            score = r.get("unknown_score")
            line = r.get("alarm_line") if _num(r.get("alarm_line")) else logic.alarm_line(r.get("sensitivity"))
            top = top_feature(r)
            this = r.get("diagnosis_this_minute")
            m.appendRow([
                _item(r.get("seq", ""), mono=True),
                _item(clock(r.get("at"), utc=True)),
                _item("", pill=(logic.display_name(r.get("diagnosis")), diagnosis_color(r.get("diagnosis")))),
                _item(logic.display_name(this) if this else "–"),
                _item(f"{r['confidence']:.2f}" if judged and _num(r.get("confidence")) else "–", right=True),
                _item(f"{score:.2f}" if judged else "–", right=True,
                      flag=bool(judged and line is not None and score > line)),
                _item(r.get("abnormal_minutes") or "", right=True),
                _item(f"{top[0]} {signed(top[1])}" if top else "–", mono=bool(top)),
            ])
        self.records_table.fit()

    # ---- export ----------------------------------------------------------------------

    def _export_dialog(self) -> None:
        machine = (self.state.current_machine() if self.state else None) or "history"
        path, _ = QFileDialog.getSaveFileName(self, "Export CSV", f"{machine}-records.csv", "CSV (*.csv)")
        if path:
            write_records_csv(path, self.trend_records())
