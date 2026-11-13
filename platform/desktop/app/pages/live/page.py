"""The Live page: the answer first, then the signals, then the detail.

Built from the mockup's renderLive, renderStatus, renderSignals and
renderSensitivity. Every label and judgement comes from app.data.logic; where
the mockup and CONTRACT.md differ, the contract wins (see status_card.py).
"""

from __future__ import annotations

import datetime as dt

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ...data import logic
from ...theme import fonts
from ...theme import tokens as t
from ...widgets import DataTable, Panel, Pill
from ..spectrum.band_chart import BandChart, baseline_from
from ..thermal.heat_image import ColourBar, HeatImage
from .charts import DiagnosisBand
from .common import (
    clock,
    diagnosis_color,
    duration,
    fault_group,
    label,
    ms_of,
    periods,
    write_records_csv,
)
from .controls import ControlsBody
from .signals import SignalsTable, build_specs
from .status_card import StatusCard

WINDOW_S = 300  # "last 5 minutes"
EVENTS_MAX = 60


def window(records: list, seconds: float = WINDOW_S) -> list:
    """The records within `seconds` of the newest one."""
    if not records:
        return []
    end = ms_of(records[-1].get("at"))
    if end is None:
        return records[-seconds:]
    return [r for r in records if (ms_of(r.get("at")) or end) >= end - seconds * 1000]


class LivePage(QWidget):
    key = "live"
    title = "Live"
    subtitle = "current reading"

    navigate = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.state = None
        self._snoozed: set[str] = set()
        self._bookmarks: list[dict] = []

        # The shell puts each page in a scroll area with the content padding.
        content = self
        grid = QGridLayout(content)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(t.PAGE_GAP)
        grid.setVerticalSpacing(t.PAGE_GAP)
        for c in range(12):
            grid.setColumnStretch(c, 1)

        self.status = StatusCard(content)
        self.status.navigate.connect(self.navigate.emit)
        self.status.focus_controls.connect(self.focus_controls)
        self.status.answer.connect(self._answer)
        self.status.later.connect(self._later)
        grid.addWidget(self.status, 0, 0, 1, 12)

        self.signals_panel = Panel("Signals", "now, and the last 5 minutes · click a row for detail", content)
        self.signals = SignalsTable(self.signals_panel)
        self.signals.navigate.connect(self.navigate.emit)
        self.signals_panel.add(self.signals)
        grid.addWidget(self.signals_panel, 1, 0, 1, 12)

        self.spectrum_panel = Panel("Spectrum", "", content, link=True)
        self.spectrum_panel.setFixedHeight(290)
        self.spectrum = BandChart(self.spectrum_panel, hover=False)
        self.spectrum.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        box = QWidget(self.spectrum_panel)
        bl = QVBoxLayout(box)
        bl.setContentsMargins(14, 12, 14, 8)
        bl.addWidget(self.spectrum)
        self.spectrum_panel.add(box, 1)
        self.spectrum_panel.clicked.connect(lambda: self.navigate.emit("spectrum"))
        grid.addWidget(self.spectrum_panel, 2, 0, 1, 7)

        self.thermal_panel = Panel("Thermal", "", content, link=True)
        self.thermal_panel.setFixedHeight(290)
        tbox = QWidget(self.thermal_panel)
        tl = QHBoxLayout(tbox)
        tl.setContentsMargins(16, 12, 16, 12)
        tl.setSpacing(14)
        self.heat_image = HeatImage(tbox, full=False)
        self.heat_image.set_grid(False)
        self.heat_image.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.heat_bar = ColourBar(tbox)
        self.heat_empty = label("", "muted", tbox, fonts.body())
        self.heat_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tl.addWidget(self.heat_image, 1)
        tl.addWidget(self.heat_empty, 1)
        tl.addWidget(self.heat_bar)
        self.thermal_panel.add(tbox, 1)
        self.thermal_panel.clicked.connect(lambda: self.navigate.emit("thermal"))
        grid.addWidget(self.thermal_panel, 2, 7, 1, 5)

        self.timeline_panel = Panel("Timeline", "last 5 minutes, as the device labels them", content,
                                    padded=True, link=True)
        self.band = DiagnosisBand("Diagnosis", 76, self.timeline_panel)
        self.timeline_panel.add(self.band)
        self.band_sum = QWidget(self.timeline_panel)
        self.band_sum_lay = QHBoxLayout(self.band_sum)
        self.band_sum_lay.setContentsMargins(0, 12, 0, 0)
        self.band_sum_lay.setSpacing(18)
        self.timeline_panel.add(self.band_sum)
        self.timeline_panel.clicked.connect(lambda: self.navigate.emit("history"))
        grid.addWidget(self.timeline_panel, 3, 0, 1, 12)

        self.events_panel = Panel("Events", "alarms, answers, problems, agent runs", content)
        self.events = DataTable(self.events_panel)
        self.events_model = QStandardItemModel(0, 3, self.events)
        self.events_model.setHorizontalHeaderLabels(["Time", "Type", "Event"])
        self.events.setModel(self.events_model)
        hh = self.events.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.events.setColumnWidth(0, 104)
        self.events.setColumnWidth(1, 150)
        self.events_panel.add(self.events, 1)
        grid.addWidget(self.events_panel, 4, 0, 1, 7)

        self.controls_panel = Panel("Controls", "", content, padded=True)
        # Panel centres a title that has no hint; a stretch keeps it on the left.
        self.controls_panel.header_end.addStretch(1)
        self.controls = ControlsBody(self.controls_panel)
        self.controls.sensitivity_wanted.connect(self._set_sensitivity)
        self.controls.run_agent.connect(self._run_agent)
        self.controls.bookmark.connect(self.add_bookmark)
        self.controls.export_session.connect(self._export_dialog)
        self.controls.sim_key.connect(self._sim_key)
        self.controls_panel.add(self.controls, 1)
        grid.addWidget(self.controls_panel, 4, 7, 1, 5)
        grid.setRowMinimumHeight(4, 330)
        # Columns share the width by stretch alone, as the mockup's 12-column grid.
        for panel in (self.spectrum_panel, self.thermal_panel, self.events_panel, self.controls_panel):
            pol = panel.sizePolicy()
            pol.setHorizontalPolicy(QSizePolicy.Policy.Ignored)
            panel.setSizePolicy(pol)
        grid.setRowStretch(5, 1)

    # ---- binding --------------------------------------------------------------

    def bind(self, state) -> None:
        self.state = state
        for sig in (state.snapshot_changed, state.alarms_changed, state.runs_changed, state.problems_changed,
                    state.settings_changed):
            sig.connect(self.refresh)
        state.record_added.connect(lambda _r: self.refresh())
        state.connection_changed.connect(lambda _up: self.refresh())
        if hasattr(state, "agent_message"):
            state.agent_message.connect(self.controls.say)
        self.refresh()

    def refresh(self) -> None:
        s = self.state
        if s is None:
            return
        records = s.records(logic.HISTORY_CAP)
        recent = window(records)
        self.status.show_state(s, recent, self._snoozed)
        self.signals.set_specs(build_specs(s, recent))
        self._show_spectrum(s, records)
        self._show_thermal(s)
        self._show_timeline(s, recent)
        self._show_events(s)
        sens = s.sensitivity()
        self.controls.show_sensitivity(sens["reported"], sens["wanted"])
        self.controls.set_agent_running(s.agent_running())
        self.controls.show_sim(s.sim())

    def _show_spectrum(self, s, records: list) -> None:
        rec = s.latest()
        model = s.model()
        self.spectrum.set_record(rec, model)
        self.spectrum.set_baseline(baseline_from(s.records(logic.HISTORY_CAP, spectrum=True)))
        edges = (model or {}).get("spectrum_edges_hz")
        n = len(self.spectrum.spectrum() or [])
        if isinstance(edges, list) and len(edges) >= 2 and n:
            hint = f"{n} vibration bands, {edges[0]:g} to {edges[-1]:g} Hz · dashed line is the baseline"
        else:
            hint = f"{n} vibration bands · dashed line is the baseline" if n else "no spectrum yet"
        self.spectrum_panel.set_hint(hint)

    def _show_thermal(self, s) -> None:
        view = logic.heat_view(s.latest() or {})
        rng = logic.heat_range(view) if view["kind"] == "camera" else None
        frames = view.get("frames") or []
        if view["kind"] == "camera" and frames and rng:
            hot = None
            if view["hot_row"] is not None and view["hot_col"] is not None and view["max_c"] is not None:
                hot = (view["hot_row"], view["hot_col"], view["max_c"])
            self.heat_image.set_image(list(frames[-1]["pixels"]), logic.HEAT_W, logic.HEAT_H, rng["lo"], rng["hi"], hot)
            self.heat_bar.set_range(rng["lo"], rng["hi"])
            self.heat_image.show()
            self.heat_bar.show()
            self.heat_empty.hide()
        else:
            self.heat_image.clear()
            self.heat_image.hide()
            self.heat_bar.hide()
            self.heat_empty.setText({"imu": "No thermal camera: IMU temperature only",
                                     "none": "No heat reading in the newest record"}.get(
                view["kind"], "No image in the newest record"))
            self.heat_empty.show()
        bits = ["optional sensor"]
        if view["kind"] == "camera":
            if view["max_c"] is not None:
                bits.append(f"hottest {view['max_c']:.1f} °C")
            if view["background_c"] is not None:
                bits.append(f"room {view['background_c']:.1f} °C")
        elif view["kind"] == "imu" and view.get("imu_temp_c") is not None:
            bits.append(f"IMU {view['imu_temp_c']:.1f} °C")
        self.thermal_panel.set_hint(" · ".join(bits))

    def _show_timeline(self, s, recent: list) -> None:
        items = [(logic.display_name(r.get("diagnosis")) or "–", diagnosis_color(r.get("diagnosis"))) for r in recent]
        self.band.set_items(items)
        lay = self.band_sum_lay
        while lay.count():
            w = lay.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        counts: dict[tuple[str, str], int] = {}
        for it in items:
            counts[it] = counts.get(it, 0) + 1
        for (text, color), n in sorted(counts.items(), key=lambda kv: -kv[1]):
            cell = QWidget(self.band_sum)
            cl = QHBoxLayout(cell)
            cl.setContentsMargins(0, 0, 0, 0)
            cl.setSpacing(6)
            cl.addWidget(Pill(text, color, cell))
            cl.addWidget(label(f"{duration(n)} · {round(n / len(items) * 100)}%", "soft", cell,
                               fonts.font(t.FONT_BASE)))
            lay.addWidget(cell)
        lay.addStretch(1)
        start = ms_of(recent[0].get("at")) if recent else None
        n_alarms = sum(1 for a in s.alarms() if start is None or (ms_of(a.get("end")) or 0) >= start)
        lay.addWidget(label(f"{n_alarms} alarm{'' if n_alarms == 1 else 's'} · {periods(len(recent))}",
                            "muted", self.band_sum, fonts.font(t.FONT_SMALL)))

    # ---- events -----------------------------------------------------------------

    def events_list(self) -> list[dict]:
        s = self.state
        out: list[dict] = []
        model = s.model()
        for a in s.alarms():
            diag = a.get("diagnosis")
            group = fault_group(model, diag)
            out.append({"at": a.get("start"), "kind": "Alarm", "color": diagnosis_color(diag),
                        "text": " · ".join(x for x in (logic.display_name(diag), group) if x)})
            if not a.get("open") and a.get("end"):
                out.append({"at": a.get("end"), "kind": "Back to normal", "color": None,
                            "text": f"after {periods(int(a.get('periods') or 0))}"})
            for ans in a.get("answers") or ([a["answer"]] if a.get("answer") else []):
                text = f"{logic.verdict_text(ans.get('verdict')).lower()} · {logic.display_name(diag)} at {clock(a.get('start'))}"
                if ans.get("by"):
                    text += f" · by {ans['by']}"
                out.append({"at": ans.get("at"), "kind": "Answer", "color": None, "text": text})
        for p in s.problems():
            out.append({"at": p.get("since"), "kind": "Problem", "color": t.WARN,
                        "text": " · ".join(x for x in (logic.capitalise(p.get("text") or p["name"]),
                                                             logic.kind_label(p.get("kind"))) if x)})
        for run in s.runs():
            if run.get("status") == "running":
                text = "report agent running"
            elif run.get("ok") and run.get("report"):
                text = f"report ready · priority {run['report'].get('priority', '')}"
            else:
                text = f"no report · {run.get('reason') or ''}".rstrip(" ·")
            out.append({"at": run.get("at"), "kind": "Agent", "color": None, "text": text})
        out.extend(self._bookmarks)
        out.sort(key=lambda e: ms_of(e.get("at")) or 0, reverse=True)
        return out[:EVENTS_MAX]

    def _show_events(self, s) -> None:
        m = self.events_model
        m.removeRows(0, m.rowCount())
        for e in self.events_list():
            time_item = QStandardItem(clock(e["at"]))
            kind = QStandardItem(e["kind"])
            if e["color"]:
                kind.setData((e["kind"], e["color"]), DataTable.PillRole)
            text = QStandardItem(e["text"])
            text.setData(True, DataTable.StrongRole)
            m.appendRow([time_item, kind, text])

    # ---- actions ------------------------------------------------------------------

    def focus_controls(self) -> None:
        w = self.parentWidget()
        while w is not None and not isinstance(w, QScrollArea):
            w = w.parentWidget()
        if w is not None:
            w.ensureWidgetVisible(self.controls_panel, 0, t.CONTENT_PAD)
        self.controls.slider.setFocus()

    def _answer(self, alarm_id: str, verdict: str) -> None:
        if self.state is not None:
            self.state.answer_alarm(alarm_id, verdict)

    def _later(self, alarm_id: str) -> None:
        # "Later" sends nothing (INTERFACES.md addition 2): it only hides the question here.
        self._snoozed.add(alarm_id)
        self.refresh()

    def _set_sensitivity(self, value: float) -> None:
        if self.state is not None:
            self.state.set_sensitivity(value)

    def _run_agent(self) -> None:
        if self.state is None or self.state.agent_running():
            return
        self.state.start_agent("standin")
        self.refresh()

    def _sim_key(self, key: str) -> None:
        sim = self.state.sim() if self.state is not None else None
        if sim is not None:
            sim.key(key)
            self.controls.show_sim(sim)

    def add_bookmark(self) -> None:
        rec = self.state.latest() if self.state is not None else None
        at = dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        text = f"bookmark at seq {rec['seq']}" if rec and rec.get("seq") is not None else "bookmark"
        self._bookmarks.append({"at": at, "kind": "Bookmark", "color": None, "text": text})
        self.refresh()

    def bookmarks(self) -> list[dict]:
        return list(self._bookmarks)

    def _export_dialog(self) -> None:
        machine = (self.state.current_machine() if self.state else None) or "session"
        path, _ = QFileDialog.getSaveFileName(self, "Export session", f"{machine}-session.csv", "CSV (*.csv)")
        if path:
            n = self.export_session(path)
            self.controls.say(f"Saved {n} periods to {path}")

    def export_session(self, path: str) -> int:
        """Write the current machine's records to a CSV file. Returns the row count."""
        return write_records_csv(path, self.state.records(logic.HISTORY_CAP) if self.state else [])
