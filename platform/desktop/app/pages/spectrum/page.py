"""Spectrum page: band chart, waterfall and bands table (the mockup's renderSpectrum)."""

from __future__ import annotations

from datetime import datetime

from app.pages.spectrum import maths
from app.pages.spectrum.band_chart import BandChart
from app.pages.spectrum.hoverline import HoverLine
from app.pages.spectrum.waterfall import Waterfall
from app.theme import tokens as T
from app.widgets import Button, DataTable, Panel
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QGridLayout, QHeaderView, QWidget

HISTORY = 300
HOVER_IDLE = "Hover a band to see its range and level."
COLUMNS = ("Band", "Range Hz", "Level", "Baseline", "Difference", "× baseline")


class SpectrumPage(QWidget):
    key = "spectrum"
    title = "Spectrum"
    subtitle = "what the sensor hears"
    navigate = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._state = None
        self._machine = None
        self._records: list[dict] = []
        self._custom: tuple[list[float], str] | None = None  # baseline set by hand, and when

        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(T.PAGE_GAP)

        self.chart_panel = Panel("Spectrum", "20 bands · log10 level · hover a band")
        self.chart = BandChart(hover=True)
        chart_wrap = QWidget()
        lay = QGridLayout(chart_wrap)
        lay.setContentsMargins(14, 12, 14, 8)
        lay.addWidget(self.chart)
        self.chart_panel.add(chart_wrap, 1)
        self.hover_line = HoverLine(HOVER_IDLE)
        self.chart_panel.add(self.hover_line)
        self.chart.hovered.connect(self._on_hover)

        self.wf_panel = Panel("Waterfall", f"last {maths.WATERFALL_PERIODS} periods · newest on top")
        self.waterfall = Waterfall()
        wf_wrap = QWidget()
        lay = QGridLayout(wf_wrap)
        lay.setContentsMargins(14, 12, 14, 8)
        lay.addWidget(self.waterfall)
        self.wf_panel.add(wf_wrap, 1)

        self.table_panel = Panel("Bands", "")
        self.base_button = Button("Use Current as Baseline", kind="secondary", small=True)
        self.base_button.clicked.connect(self.use_current_as_baseline)
        self.table_panel.header_end.addWidget(self.base_button)
        self.model = QStandardItemModel(0, len(COLUMNS), self)
        self.model.setHorizontalHeaderLabels(list(COLUMNS))
        for c in range(len(COLUMNS)):
            self.model.horizontalHeaderItem(c).setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.table = DataTable()
        self.table.setModel(self.model)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table_panel.add(self.table, 1)

        grid.addWidget(self.chart_panel, 0, 0)
        grid.addWidget(self.wf_panel, 0, 1)
        grid.addWidget(self.table_panel, 1, 0, 1, 2)
        grid.setColumnStretch(0, 3)
        grid.setColumnStretch(1, 2)
        grid.setRowStretch(0, 13)
        grid.setRowStretch(1, 10)
        self._show_baseline_hint(None)

    # ---- state ------------------------------------------------------------

    def bind(self, state) -> None:
        self._state = state
        state.snapshot_changed.connect(self.refresh)
        state.record_added.connect(self._on_record)
        self.refresh()

    def refresh(self) -> None:
        s = self._state
        if s is None:
            return
        machine = s.current_machine()
        if machine != self._machine:
            self._machine, self._custom = machine, None
        self._records = list(s.records(limit=HISTORY, spectrum=True))
        latest = s.live_record()
        newer = latest is not None and (not self._records or self._records[-1].get("seq") != latest.get("seq"))
        if newer and maths.spectrum_of(latest) and latest.get("machine_id") == machine:
            self._records.append(latest)
        self._draw(latest, s.spectrum_model())

    def _on_record(self, rec: dict) -> None:
        if self._state is not None and rec.get("machine_id") == self._state.current_machine():
            self.refresh()

    # ---- baseline ---------------------------------------------------------

    def baseline(self) -> list[float] | None:
        if self._custom is not None:
            return self._custom[0]
        return maths.baseline_from(self._records)[0]

    def use_current_as_baseline(self) -> None:
        cur = self.chart.spectrum()
        if cur is None:
            return
        self._custom = (list(cur), datetime.now().strftime("%H:%M:%S"))
        self._draw_baseline_parts()

    def _show_baseline_hint(self, n: int | None) -> None:
        if self._custom is not None:
            hint = f"strongest change from the baseline set at {self._custom[1]}"
        elif n:
            hint = f"strongest change from the average of the last {n} normal periods"
        else:
            hint = "strongest change from the baseline · no baseline yet"
        self.table_panel.set_hint(hint)

    # ---- drawing ----------------------------------------------------------

    def _draw(self, latest: dict | None, model: dict | None) -> None:
        self.chart.set_record(latest, model)
        self.waterfall.set_records(self._records)
        self._draw_baseline_parts()

    def _draw_baseline_parts(self) -> None:
        base = self.baseline()
        self.chart.set_baseline(base)
        n = None
        if self._custom is None:
            pool = [r for r in self._records if r.get("diagnosis") == "normal" and maths.spectrum_of(r)]
            n = len(pool) or None
        self._show_baseline_hint(n)
        self._fill_table()
        self._on_hover(self.chart.hover_index())

    def _fill_table(self) -> None:
        self.model.removeRows(0, self.model.rowCount())
        spec = self.chart.spectrum()
        if spec is None:
            return
        right = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        for row in maths.band_rows(spec, self.chart.baseline(), self.chart.edges()):
            cells = [
                str(row["band"]),
                row["range"],
                f"{row['level']:.2f}",
                "–" if row["baseline"] is None else f"{row['baseline']:.2f}",
                "–" if row["diff"] is None else maths.signed(row["diff"]),
                "–" if row["times"] is None else f"{row['times']:.1f}×",
            ]
            items = []
            for c, text in enumerate(cells):
                it = QStandardItem(text)
                it.setEditable(False)
                it.setTextAlignment(right)
                # Range and a big difference read in the strong text colour; never red.
                strong = c == 1 or (c == 4 and maths.is_red(row["diff"]))
                it.setForeground(T.qcolor(T.TEXT if strong else T.SOFT))
                items.append(it)
            self.model.appendRow(items)

    def _on_hover(self, i: int) -> None:
        text = self.chart.hover_text(i) if i >= 0 else ""
        self.hover_line.set_text(text)

    def hover_text(self) -> str:
        return self.hover_line.text()
