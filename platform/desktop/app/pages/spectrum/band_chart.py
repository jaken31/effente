"""The band chart: one record's 20 vibration bands on a log frequency axis.

Shared with the Live page. Stable API:

    chart = BandChart(parent=None, hover=True)
    chart.set_record(record, model)     # newest record (with `spectrum`) and message 1
    chart.set_baseline(values or None)  # 20 log10 levels; baseline_from() gives the default
    chart.set_speed_hz(hz or None)      # running speed for the 1x/2x/3x markers
    chart.bind(state)                   # optional: follow a DashboardState by itself
    chart.hovered -> Signal(int)        # band index under the mouse, -1 when none
    chart.hover_text(i) -> str          # the readout for band i

Drawn with QPainter, after the mockup's bandChart(). Bars well above their
baseline (maths.RISE_RED) are red; all text is white or gray.
"""

from __future__ import annotations

from app.pages.spectrum import maths
from app.theme import tokens as T
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

FILTER_NOTE = f"above sensor filter, {maths.SENSOR_FILTER_HZ:.0f} Hz"
SPEED_NOTE = "running speed not reported: no 1× 2× 3× markers"
MIN_NOTE_W = 60  # below this the speed note is dropped rather than shown as a stub

# Plot margins, as the mockup's L, R, T, B.
M_LEFT, M_RIGHT, M_TOP, M_BOTTOM = 44, 6, 8, 24


def _color(token: str, alpha: float | None = None) -> QColor:
    c = T.qcolor(token)
    if alpha is not None:
        c.setAlphaF(alpha)
    return c


def baseline_from(records):
    """The default baseline for a machine's records (see maths.baseline_from)."""
    return maths.baseline_from(records)[0]


class BandChart(QWidget):
    hovered = Signal(int)

    def __init__(self, parent: QWidget | None = None, hover: bool = True):
        super().__init__(parent)
        self._spectrum: list[float] | None = None
        self._edges: list[float] | None = None
        self._baseline: list[float] | None = None
        self._speed_hz: float | None = None
        self._hover_enabled = hover
        self._hover = -1
        self._state = None
        self.setMouseTracking(hover)
        self.setMinimumSize(300, 160)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)

    # ---- data -------------------------------------------------------------

    def set_record(self, record: dict | None, model: dict | None) -> None:
        """Show `record`'s spectrum on `model`'s band edges. A None model clears the
        edges, so a machine without one never shows another machine's Hz labels."""
        self._spectrum = maths.spectrum_of(record)
        self._edges = maths.edges_of(model)
        self.update()
        if self._hover >= 0:
            self.hovered.emit(self._hover)

    def set_baseline(self, baseline) -> None:
        ok = baseline is not None and len(baseline) == maths.N_BANDS
        self._baseline = [float(v) for v in baseline] if ok else None
        self.update()

    def set_speed_hz(self, hz: float | None) -> None:
        self._speed_hz = hz if hz and hz > 0 else None
        self.update()

    def spectrum(self) -> list[float] | None:
        return self._spectrum

    def baseline(self) -> list[float] | None:
        return self._baseline

    def edges(self) -> list[float] | None:
        return self._edges

    def hover_index(self) -> int:
        return self._hover

    def hover_text(self, i: int) -> str:
        if self._spectrum is None or not 0 <= i < maths.N_BANDS:
            return ""
        return maths.hover_text(i, self._spectrum, self._baseline, self._edges)

    def bind(self, state) -> None:
        """Follow a DashboardState: newest record, its model, and the default baseline."""
        self._state = state
        state.snapshot_changed.connect(self._refresh_from_state)
        state.record_added.connect(self._on_record)
        self._refresh_from_state()

    def _refresh_from_state(self) -> None:
        s = self._state
        self.set_baseline(baseline_from(s.records(limit=300, spectrum=True)))
        self.set_record(s.latest(), s.model())

    def _on_record(self, rec: dict) -> None:
        if rec.get("machine_id") == self._state.current_machine():
            self.set_record(rec, self._state.model())

    # ---- geometry ---------------------------------------------------------

    def _plot(self) -> QRectF:
        return QRectF(M_LEFT, M_TOP, max(1, self.width() - M_LEFT - M_RIGHT), max(1, self.height() - M_TOP - M_BOTTOM))

    def _spans(self, plot: QRectF) -> list[tuple[float, float]]:
        return maths.band_spans(self._edges, plot.left(), plot.width())

    def _x_hz(self, hz: float, plot: QRectF) -> float:
        return plot.left() + maths.log_fraction(hz, self._edges[0], self._edges[-1]) * plot.width()

    def band_at(self, x: float) -> int:
        plot = self._plot()
        for i, (a, b) in enumerate(self._spans(plot)):
            if a <= x < b:
                return i
        return -1

    def band_center(self, i: int) -> QPointF:
        """Middle of band i's column, in widget pixels (for tests)."""
        plot = self._plot()
        a, b = self._spans(plot)[i]
        return QPointF((a + b) / 2, plot.center().y())

    def top_labels(self) -> dict[str, tuple[QRectF, str, Qt.AlignmentFlag]]:
        """The quiet notes on the plot's top line, as {name: (rect, text, align)}.

        "filter" (right) keeps its full text while it fits; "speed" (left, only
        without a running speed) takes what is left, elided, with a 16 px gap,
        and is dropped when too little is left to read. They never overlap.
        """
        edges = self._edges
        if not edges or self._spectrum is None:
            return {}
        plot = self._plot()
        fm = QFontMetrics(self._font(11))
        top, h = plot.top() + 2, 14
        out: dict[str, tuple[QRectF, str, Qt.AlignmentFlag]] = {}
        right_edge = plot.right() - 4
        free_right = right_edge
        if edges[0] < maths.SENSOR_FILTER_HZ < edges[-1]:
            text = fm.elidedText(FILTER_NOTE, Qt.TextElideMode.ElideRight, int(plot.width() - 8))
            w = fm.horizontalAdvance(text)
            out["filter"] = (QRectF(right_edge - w, top, w, h), text, Qt.AlignmentFlag.AlignRight)
            free_right = right_edge - w - 16
        if not self._speed_hz:
            left = plot.left() + 4
            room = free_right - left
            text = fm.elidedText(SPEED_NOTE, Qt.TextElideMode.ElideRight, int(room))
            if room >= MIN_NOTE_W and text != "…":
                w = fm.horizontalAdvance(text)
                out["speed"] = (QRectF(left, top, w, h), text, Qt.AlignmentFlag.AlignLeft)
        return out

    # ---- events -----------------------------------------------------------

    def mouseMoveEvent(self, e) -> None:
        if not self._hover_enabled:
            return
        i = self.band_at(e.position().x()) if self._spectrum is not None else -1
        if i != self._hover:
            self._hover = i
            self.update()
            self.hovered.emit(i)

    def leaveEvent(self, e) -> None:
        if self._hover != -1:
            self._hover = -1
            self.update()
            self.hovered.emit(-1)

    # ---- painting ---------------------------------------------------------

    def _font(self, px: float, weight=QFont.Weight.Normal) -> QFont:
        f = QFont(self.font())
        f.setPixelSize(round(px))
        f.setWeight(weight)
        return f

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        plot = self._plot()
        spec, base, edges = self._spectrum, self._baseline, self._edges
        lo, hi = maths.level_range(spec, base)

        def y_of(v: float) -> float:
            v = min(max(v, lo), hi)
            return plot.top() + plot.height() * (1 - (v - lo) / (hi - lo))

        muted, soft = _color(T.MUTED), _color(T.SOFT)
        p.setFont(self._font(12))

        # Level grid and labels.
        for v in maths.level_ticks(lo, hi):
            y = round(y_of(v)) + 0.5
            p.setPen(QPen(_color(T.GRID), 1))
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            p.setPen(muted)
            p.drawText(QRectF(0, y - 8, M_LEFT - 8, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"{v:.1f}")

        if spec is None:
            p.setPen(muted)
            p.setFont(self._font(13))
            p.drawText(plot, Qt.AlignmentFlag.AlignCenter, "This reading has no spectrum")
            return

        # Hz labels, the filter shading and its note need the announced edges.
        if edges:
            fm = p.fontMetrics()
            ticks = [hz for hz in maths.HZ_TICKS if edges[0] - 1e-6 <= hz <= edges[-1] + 1e-6]
            p.setPen(muted)
            for k, hz in enumerate(ticks):
                x = self._x_hz(hz, plot)
                text = f"{hz} Hz" if k == len(ticks) - 1 else str(hz)
                w = fm.horizontalAdvance(text)
                left = x - w / 2
                if x - plot.left() < w / 2:
                    left = x  # at the left edge: start there
                elif plot.right() - x < w / 2:
                    left = x - w  # at the right edge: end there
                p.drawText(QPointF(left, self.height() - 6), text)
            if edges[0] < maths.SENSOR_FILTER_HZ < edges[-1]:
                xf = self._x_hz(maths.SENSOR_FILTER_HZ, plot)
                p.fillRect(QRectF(xf, plot.top(), plot.right() - xf, plot.height()), QColor(*T.TINT, 9))
            # The filter note and the speed note share the top line (top_labels).
            p.setFont(self._font(11))
            p.setPen(muted)
            for rect, text, align in self.top_labels().values():
                p.drawText(rect, align | Qt.AlignmentFlag.AlignVCenter, text)
            p.setFont(self._font(12))
        else:
            p.setPen(muted)
            p.drawText(QPointF(plot.left(), self.height() - 6), "Band edges unknown until the model is announced")

        spans = self._spans(plot)

        # Hover highlight behind the bar.
        if 0 <= self._hover < len(spans):
            a, b = spans[self._hover]
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(*T.TINT, 15))
            p.drawRoundedRect(QRectF(a, plot.top(), b - a, plot.height()), 3, 3)

        # Bars.
        p.setPen(Qt.PenStyle.NoPen)
        bottom = plot.bottom()
        for i, v in enumerate(spec):
            a, b = spans[i]
            d = v - base[i] if base else None
            if maths.is_red(d):
                p.setBrush(_color(T.RED))
            else:
                above = edges is not None and edges[i] > maths.SENSOR_FILTER_HZ
                p.setBrush(_color(T.DOT, 0.18 if above else 0.4))
            y = y_of(v)
            p.drawRoundedRect(QRectF(a + 2, y, max(1.0, b - a - 4), bottom - y), 3, 3)

        # Baseline, dashed, through the band centres.
        if base:
            path = QPainterPath()
            for i, v in enumerate(base):
                a, b = spans[i]
                pt = QPointF((a + b) / 2, y_of(v))
                path.lineTo(pt) if i else path.moveTo(pt)
            pen = QPen(muted, 1.2)
            pen.setDashPattern([4 / 1.2, 4 / 1.2])
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)

        # Running-speed markers. Records carry no running speed (CONTRACT.md),
        # so they are drawn only when one is given.
        if edges and self._speed_hz:
            for k in (1, 2, 3):
                hz = self._speed_hz * k
                if not edges[0] <= hz <= edges[-1]:
                    continue
                x = self._x_hz(hz, plot)
                pen = QPen(_color(T.MUTED, 0.7), 1)
                pen.setDashPattern([2, 4])
                p.setPen(pen)
                p.drawLine(QPointF(x, plot.top() + 16), QPointF(x, bottom))
                p.setPen(soft)
                p.drawText(QRectF(x - 20, plot.top(), 40, 14), Qt.AlignmentFlag.AlignCenter, f"{k}×")
