"""Thermal page: KPI strip, camera image, heat trend and 8 x 6 grid (the mockup's
renderThermal), drawn from the record's `heat` field (CONTRACT.md, Heat).

Thermal is optional: without camera data the page shows a plain empty state.
Every heat field is read through logic.heat_view, which decodes the frames;
frames are played back at the spacing they were taken with logic.frame_at.
"""

from __future__ import annotations

import time

from app.data import logic
from app.pages.spectrum.hoverline import HoverLine
from app.pages.thermal import stats
from app.pages.thermal.cells import CellGrid
from app.pages.thermal.heat_image import ColourBar, HeatImage
from app.pages.thermal.trend import HeatTrend
from app.theme import fonts
from app.theme import tokens as T
from app.widgets import Button, KpiStrip, Panel, Segmented, Toggle
from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

HISTORY = 300
HOVER_IDLE = "Hover the image to read a pixel."
KPIS = ("Hottest", "Above room", "Average", "Hot pixels", "Fastest change", "Scene match")


def empty_message(view: dict | None) -> str:
    kind = (view or {}).get("kind")
    if view is None:
        return "No readings yet."
    if kind == "none":
        return "No heat readings. This device sends no thermal data; the thermal camera is an optional sensor."
    if kind == "imu":
        imu = view.get("imu_temp_c")
        temp = f"{imu:.1f} °C" if imu is not None else "not reported"
        return f"No thermal camera on this device. IMU temperature {temp}: the sensor head's own, not the surface."
    return "This reading's heat data could not be read."


class _Swatch(QWidget):
    """A 10 px legend swatch."""

    def __init__(self, color: QColor, parent: QWidget | None = None):
        super().__init__(parent)
        self._c = color
        self.setFixedSize(10, 10)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._c)
        p.drawRoundedRect(QRectF(self.rect()), 2, 2)


def _legend_item(color: QColor, text: str) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(6)
    lay.addWidget(_Swatch(color))
    lab = QLabel(text)
    lab.setProperty("role", "soft")
    lab.setFont(fonts.small())
    lay.addWidget(lab)
    return w


class ThermalPage(QWidget):
    key = "thermal"
    title = "Thermal"
    subtitle = "live heat image, optional sensor"
    navigate = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._state = None
        self._view: dict | None = None
        self._arrived = 0.0  # monotonic seconds the shown record arrived
        self._index = -1  # shown frame
        self._prev_frame: dict | None = None  # the frame shown before this one
        self._rate: float | None = None
        self._hover: tuple[int, int] | None = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._tick)
        self.frame_ms: list[float] = []  # time to put each frame on screen, for the timing check

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        # Empty state.
        self.empty_panel = Panel("Thermal camera", "optional sensor", number=1)
        self.empty_label = QLabel(empty_message(None))
        self.empty_label.setProperty("role", "empty")
        self.empty_label.setFont(fonts.body())
        self.empty_label.setWordWrap(True)
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_panel.add(self.empty_label, 1)
        outer.addWidget(self.empty_panel)

        # Content.
        self.content = content = QWidget()
        grid = QGridLayout(content)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(T.PAGE_GAP)
        self.kpis = KpiStrip(list(KPIS))
        self.kpis.cell(3).set_sub(f"{stats.HOT_ABOVE_ROOM_C:.0f} °C or more above room")
        self.kpis.cell(4).set_sub("any pixel, per second")

        self.cam_panel = Panel("Thermal camera", "optional sensor · MLX90640 · 32 × 24")
        self.scale_seg = Segmented(
            [("auto", "Auto range"), ("fixed", f"{stats.FIXED_RANGE[0]} to {stats.FIXED_RANGE[1]} °C")], small=True
        )
        self.view_seg = Segmented([("smooth", "Smooth"), ("pixels", "Pixels")], small=True)
        grid_label = QLabel("Grid")
        grid_label.setProperty("role", "muted")
        grid_label.setFont(fonts.body())
        self.grid_toggle = Toggle(checked=True)
        for w in (self.scale_seg, self.view_seg, grid_label, self.grid_toggle):
            self.cam_panel.header_end.addWidget(w)
        self.cam_panel.header_end.setSpacing(6)
        body = QWidget()
        bl = QHBoxLayout(body)
        bl.setContentsMargins(16, 12, 16, 12)
        bl.setSpacing(14)
        self.image = HeatImage(full=True)
        self.bar = ColourBar()
        bl.addWidget(self.image, 1)
        bl.addWidget(self.bar)
        self.cam_panel.add(body, 1)
        self.hover_line = HoverLine(HOVER_IDLE, min_height=46)
        fl = self.hover_line.row
        self.sim_label = QLabel("Simulator")
        self.sim_label.setProperty("role", "muted")
        self.sim_label.setFont(fonts.body())
        self.sim_button = Button("Next heat source", kind="ghost", small=True)
        self.sim_button.setToolTip("The simulator's t key: camera, IMU only, none")
        self.sim_button.clicked.connect(self._next_heat_source)
        fl.addWidget(self.sim_label)
        fl.addWidget(self.sim_button)
        self.cam_panel.add(self.hover_line)

        self.trend_panel = Panel("Heat trend", "last 2 min, above room")
        self.trend_panel.header_end.setSpacing(16)
        self.trend_panel.header_end.addWidget(_legend_item(T.qcolor(T.RED), "hottest"))
        self.trend_panel.header_end.addWidget(_legend_item(T.qcolor(T.SOFT), "average"))
        self.trend = HeatTrend()
        tw = QWidget()
        tl = QVBoxLayout(tw)
        tl.setContentsMargins(14, 12, 14, 8)
        tl.addWidget(self.trend)
        self.trend_panel.add(tw, 1)

        self.cells_panel = Panel("Grid", "8 × 6 cells · average and hottest point above room")
        self.cells = CellGrid()
        cw = QWidget()
        cl = QVBoxLayout(cw)
        cl.setContentsMargins(16, 12, 16, 12)
        cl.addWidget(self.cells)
        self.cells_panel.add(cw, 1)

        grid.addWidget(self.kpis, 0, 0, 1, 2)
        grid.addWidget(self.cam_panel, 1, 0, 2, 1)
        grid.addWidget(self.trend_panel, 1, 1)
        grid.addWidget(self.cells_panel, 2, 1)
        grid.setColumnStretch(0, 13)
        grid.setColumnStretch(1, 10)
        grid.setRowStretch(1, 1)
        grid.setRowStretch(2, 1)
        outer.addWidget(content)

        self.scale_seg.changed.connect(lambda _k: self._redraw())
        self.view_seg.changed.connect(lambda k: self.image.set_smooth(k == "smooth"))
        self.grid_toggle.toggled.connect(self.image.set_grid)
        self.image.hovered.connect(self._on_hover)
        self._show_empty(None)

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
        self._update_sim()
        self.show_record(s.latest())
        self._draw_trend()

    def _on_record(self, rec: dict) -> None:
        s = self._state
        if s is None or rec.get("machine_id") != s.current_machine():
            return
        self.show_record(rec)
        self._draw_trend()

    def _update_sim(self) -> None:
        sim = self._state.sim() if self._state is not None and hasattr(self._state, "sim") else None
        self.sim_label.setVisible(sim is not None)
        self.sim_button.setVisible(sim is not None)

    def _next_heat_source(self) -> None:
        sim = self._state.sim() if self._state is not None else None
        if sim is not None:
            sim.key("t")

    # ---- a record ---------------------------------------------------------

    def show_record(self, rec: dict | None) -> None:
        """Show a record's heat: summary now, its frames at their own spacing."""
        view = logic.heat_view(rec) if rec is not None else None
        if view is None or view["kind"] != "camera":
            self._show_empty(view)
            return
        if self._view is not None and self._index >= 0 and self._view["frames"]:
            self._prev_frame = self._view["frames"][self._index]
        self._view = view
        self._arrived = time.monotonic()
        self._index = -1
        self._set_empty(False)
        self._fill_summary()
        self._tick()

    def _show_empty(self, view: dict | None) -> None:
        self._timer.stop()
        self._view, self._index, self._prev_frame = None, -1, None
        self.empty_label.setText(empty_message(view))
        self._set_empty(True)

    def _set_empty(self, empty: bool) -> None:
        # Shown and hidden rather than stacked, so the empty state does not
        # inherit the full page's minimum size.
        self.content.setVisible(not empty)
        self.empty_panel.setVisible(empty)

    def is_empty(self) -> bool:
        return self.empty_panel.isVisibleTo(self)

    def _tick(self) -> None:
        """Show the frame due now and wait for the next one."""
        view = self._view
        if view is None:
            return
        due = logic.frame_at(view["frames"], (time.monotonic() - self._arrived) * 1000)
        if due["index"] != self._index:
            if self._index >= 0:
                self._prev_frame = view["frames"][self._index]
            self._index = due["index"]
            if self.isVisible():  # a hidden page redraws when shown
                self._redraw()
        elif self._index < 0 and self.isVisible():
            self._redraw()  # no frames: the grid image
        if due["next_ms"] is not None:
            self._timer.start(max(1, round(due["next_ms"])))

    def current_range(self) -> tuple[float, float] | None:
        if self._view is None:
            return None
        if self.scale_seg.current() == "fixed":
            return stats.FIXED_RANGE
        r = logic.heat_range(self._view)
        return (r["lo"], r["hi"]) if r else None

    def _redraw(self) -> None:
        view = self._view
        if view is None:
            return
        t0 = time.perf_counter()
        rng = self.current_range()
        if rng is None:
            return
        lo, hi = rng
        bg = view["background_c"]
        frame = view["frames"][self._index] if self._index >= 0 else None
        if frame is not None:
            fs = stats.frame_stats(frame["pixels"], bg)
            self.image.set_image(frame["pixels"], stats.W, stats.H, lo, hi, (fs.hot_row, fs.hot_col, fs.hot_c))
            self._rate = stats.fastest_change(self._prev_frame, frame)
            self._fill_frame_kpis(fs)
        else:
            vals = [bg + c for c in view["cells"]]
            hot = None
            if view["hot_row"] is not None and view["hot_col"] is not None and view["max_c"] is not None:
                hot = (view["hot_row"], view["hot_col"], view["max_c"])
            self.image.set_image(vals, stats.COLS, stats.ROWS, lo, hi, hot)
            self._fill_frame_kpis(None)
        self.bar.set_range(lo, hi)
        self.cells.set_cells(view["cells"], view["spots"], bg, (lo, hi))
        if self.image.underMouse() and self._hover is not None:
            self._on_hover(*self._hover)
        if frame is not None:
            self.frame_ms.append((time.perf_counter() - t0) * 1000)
            del self.frame_ms[:-200]

    # ---- KPIs -------------------------------------------------------------

    def _fill_summary(self) -> None:
        v = self._view
        k = self.kpis
        mx, bg = v["max_c"], v["background_c"]
        k.cell(0).set_value(f"{mx:.1f} °C" if mx is not None else "–")
        where = f"at row {v['hot_row']}, column {v['hot_col']}" if v["hot_row"] is not None and v["hot_col"] is not None else ""
        k.cell(0).set_sub(where)
        if mx is not None and bg is not None:
            k.cell(1).set_value(f"{mx - bg:+.1f} °C")
            k.cell(1).set_sub(f"room {bg:.1f} °C, the image median")
        else:
            k.cell(1).set_value("–")
            k.cell(1).set_sub("")
        mean = stats.cells_mean(v["cells"])
        k.cell(2).set_value(f"{bg + mean:.1f} °C" if mean is not None and bg is not None else "–")
        sm = v["scene_match"]
        k.cell(5).set_value(f"{round(sm * 100)}%" if sm is not None else "–", abnormal=v["frozen"])
        if v["frozen"]:
            k.cell(5).set_sub("camera image frozen")
        elif sm is None:
            k.cell(5).set_sub("usual view not learned yet")
        else:
            k.cell(5).set_sub("likeness to the usual view")
        n = len(v["frames"])
        hint = "optional sensor · MLX90640 · 32 × 24"
        self.cam_panel.set_hint(f"{hint} · {n} fps" if n else f"{hint} · grid only, no images in this reading")

    def _fill_frame_kpis(self, fs) -> None:
        k = self.kpis
        if fs is None:
            k.cell(2).set_sub("")
            k.cell(3).set_value("–")
            k.cell(4).set_value("–")
            return
        k.cell(2).set_sub(f"spread {fs.spread_c:.1f} °C")
        k.cell(3).set_value(str(fs.hot_pixels) if fs.hot_pixels is not None else "–")
        k.cell(4).set_value(f"{self._rate:.1f} °C" if self._rate is not None else "–")

    # ---- hover ------------------------------------------------------------

    def _on_hover(self, col: int, row: int) -> None:
        self._hover = None if col < 0 else (col, row)
        self.hover_line.set_text(self.hover_text(col, row))

    def hover_text(self, col: int, row: int) -> str:
        v = self._view
        if v is None or col < 0:
            return HOVER_IDLE
        bg = v["background_c"]
        if self._index >= 0:
            t = v["frames"][self._index]["pixels"][row * stats.W + col]
            above = f" · {stats.one_decimal(t - bg, sign=True)} above room" if bg is not None else ""
            return f"Pixel {col}, {row} · {t:.1f} °C{above}"
        i = (row // 4) * stats.COLS + col // 4
        spot = v["spots"][i] if v["spots"] else None
        hot = f" · hottest {spot:+.1f}" if spot is not None else ""
        return f"Block {col // 4}, {row // 4} · average {v['cells'][i]:+.1f} above room{hot}"

    # ---- trend ------------------------------------------------------------

    def _draw_trend(self) -> None:
        s = self._state
        if s is None:
            return
        recs = s.records(limit=HISTORY)
        latest = s.latest()
        if latest is not None and (not recs or recs[-1].get("seq") != latest.get("seq")):
            recs = [*recs, latest]
        self.trend.set_points(stats.trend_points(recs, logic.parse_time))
        note = ""
        imu = self._view["imu_temp_c"] if self._view else None
        if imu is not None:
            tr = logic.imu_trend(recs)
            change = f", {tr['delta_c']:+.1f} °C in {round(tr['span_ms'] / 60000, 1):g} min" if tr else ""
            note = f"IMU {imu:.1f} °C{change}"
        self.trend.set_note(note)

    def showEvent(self, e) -> None:
        super().showEvent(e)
        if self._view is not None:
            self._redraw()
