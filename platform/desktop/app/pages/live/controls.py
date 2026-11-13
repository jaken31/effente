"""The Live page's controls panel: sensitivity, actions, and the simulator.

Sensitivity is per device (INTERFACES.md addition 3). The panel shows the
value the device reports and, while a change is on its way, the one wanted;
it never shows a wanted value as applied. Moving the slider previews the
margins (logic.margins) and sets the device's sensitivity on release.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractSlider,
    QGridLayout,
    QHBoxLayout,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ...data import logic
from ...theme import fonts
from ...theme import tokens as t
from ...widgets import Button, Segmented
from .common import label, short

PRESETS = [("0.25", "Low"), ("0.5", "Balanced"), ("0.75", "High")]
SIM_CASES = [("1", "normal"), ("2", "unbalance_like"), ("3", "looseness_or_rattle"),
             ("4", "unrecognised_pattern"), ("5", "stopped"), ("6", "warming_up")]


class SensitivitySlider(QAbstractSlider):
    """0 to 100. A 4px track, filled to the value, with a white knob in a red halo.
    Might belong in app/widgets/ if another page needs a slider."""

    KNOB = 16

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setOrientation(Qt.Orientation.Horizontal)
        self.setRange(0, 100)
        self.setPageStep(5)
        self.setFixedHeight(28)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Dashboard sensitivity")

    def _x(self, v: int) -> float:
        span = self.width() - self.KNOB
        return self.KNOB / 2 + span * (v - self.minimum()) / max(1, self.maximum() - self.minimum())

    def _value_at(self, x: float) -> int:
        span = max(1.0, self.width() - self.KNOB)
        f = min(1.0, max(0.0, (x - self.KNOB / 2) / span))
        return round(self.minimum() + f * (self.maximum() - self.minimum()))

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self.setSliderDown(True)
            self.setSliderPosition(self._value_at(e.position().x()))

    def mouseMoveEvent(self, e) -> None:
        if self.isSliderDown():
            self.setSliderPosition(self._value_at(e.position().x()))

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton and self.isSliderDown():
            self.setSliderPosition(self._value_at(e.position().x()))
            self.setSliderDown(False)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cy = self.height() / 2
        x = self._x(self.sliderPosition())
        track = QRectF(0, cy - 2, self.width(), 4)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(t.qcolor(t.RAISED))
        p.drawRoundedRect(track, 2, 2)
        p.setBrush(t.qcolor(t.DOT))
        p.drawRoundedRect(QRectF(0, cy - 2, x, 4), 2, 2)
        halo = t.qcolor(t.RED)
        halo.setAlphaF(0.35)
        p.setBrush(halo)
        p.drawEllipse(QPointF(x, cy), self.KNOB / 2 + 4, self.KNOB / 2 + 4)
        p.setBrush(t.qcolor(t.WHITE))
        p.drawEllipse(QPointF(x, cy), self.KNOB / 2, self.KNOB / 2)


class _Section(QWidget):
    """A block of the controls panel; every one after the first has a hairline above."""

    def __init__(self, first: bool, parent: QWidget | None = None):
        super().__init__(parent)
        self._first = first
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0 if first else 14, 0, 0)
        self.lay.setSpacing(10)

    def paintEvent(self, _event) -> None:
        if not self._first:
            p = QPainter(self)
            p.setPen(QPen(t.qcolor(t.BORDER), 1))
            p.drawLine(QPointF(0, 0.5), QPointF(self.width(), 0.5))


class ControlsBody(QWidget):
    """sensitivity_wanted(float) is emitted when the user lets go of a new value."""

    sensitivity_wanted = Signal(float)
    run_agent = Signal()
    bookmark = Signal()
    export_session = Signal()
    sim_key = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(14)

        sens = _Section(True, self)
        head = QHBoxLayout()
        head.setSpacing(10)
        self.sens_value = label("", "strong", self, fonts.big_value(20))
        self.sens_note = label("", "muted", self, fonts.font(t.FONT_SMALL))
        vbox = QHBoxLayout()
        vbox.setSpacing(8)
        vbox.addWidget(self.sens_value)
        vbox.addWidget(self.sens_note, 0, Qt.AlignmentFlag.AlignBaseline)
        head.addLayout(vbox)
        head.addStretch(1)
        self.presets = Segmented([(k, logic.sensitivity_name(float(k))) for k, _ in PRESETS], self, small=True)
        self.presets.changed.connect(lambda k: self._commit(float(k)))
        head.addWidget(self.presets)
        sens.lay.addLayout(head)
        self.slider = SensitivitySlider(self)
        self.slider.valueChanged.connect(self._moved)
        self.slider.sliderReleased.connect(lambda: self._commit(self.slider.value() / 100))
        ends = QHBoxLayout()
        ends.addWidget(label("Fewer false alarms", "muted", self, fonts.font(t.FONT_SMALL)))
        ends.addStretch(1)
        ends.addWidget(label("Flags drift earlier", "muted", self, fonts.font(t.FONT_SMALL)))
        slider_box = QVBoxLayout()
        slider_box.setSpacing(2)
        slider_box.addWidget(self.slider)
        slider_box.addLayout(ends)
        sens.lay.addLayout(slider_box)
        self.impact = label("", "soft", self, fonts.body())
        self.impact.setWordWrap(True)
        self.impact.setTextFormat(Qt.TextFormat.RichText)
        self.pending = label("", "soft", self, fonts.body())
        self.pending.setWordWrap(True)
        self.pending.hide()
        sens.lay.addWidget(self.impact)
        sens.lay.addWidget(self.pending)
        outer.addWidget(sens)

        acts = _Section(False, self)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.btn_agent = Button("Run Report Agent", "primary", self)
        self.btn_mark = Button("Bookmark", "secondary", self)
        self.btn_export = Button("Export Session", "secondary", self)
        self.btn_agent.clicked.connect(self.run_agent.emit)
        self.btn_mark.clicked.connect(self.bookmark.emit)
        self.btn_export.clicked.connect(self.export_session.emit)
        for b in (self.btn_agent, self.btn_mark, self.btn_export):
            row.addWidget(b)
        row.addStretch(1)
        acts.lay.addLayout(row)
        self.flash = label("", "muted", self, fonts.font(t.FONT_SMALL))
        self.flash.hide()
        acts.lay.addWidget(self.flash)
        outer.addWidget(acts)

        self.sim = _Section(False, self)
        self.sim.lay.addWidget(label("Simulator", "muted", self, fonts.font(t.FONT_SMALL)))
        cases = QGridLayout()
        cases.setHorizontalSpacing(8)
        cases.setVerticalSpacing(8)
        self.case_buttons: dict[str, Button] = {}
        for i, (k, diagnosis) in enumerate(SIM_CASES):
            b = Button(logic.display_name(diagnosis), "secondary", self, small=True)
            b.setToolTip(f"Simulator key {k}")
            b.clicked.connect(lambda _c=False, key=k: self.sim_key.emit(key))
            self.case_buttons[k] = b
            cases.addWidget(b, i // 3, i % 3)
        cases.setColumnStretch(3, 1)
        self.sim.lay.addLayout(cases)
        sim_row = QHBoxLayout()
        sim_row.setSpacing(8)
        self.btn_pause = Button("Pause", "secondary", self, small=True)
        self.btn_pause.clicked.connect(self._pause)
        self.sim_status = label("", "muted", self, fonts.font(t.FONT_SMALL))
        self.sim_status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        sim_row.addWidget(self.btn_pause)
        sim_row.addWidget(self.sim_status, 1)
        self.sim.lay.addLayout(sim_row)
        outer.addWidget(self.sim)
        self.sim.hide()
        outer.addStretch(1)

        self._reported: float | None = None
        self._wanted: float | None = None
        self._paused = False

    # ---- sensitivity --------------------------------------------------------

    def show_sensitivity(self, reported, wanted) -> None:
        self._reported, self._wanted = reported, wanted
        shown = wanted if wanted is not None else reported
        if not self.slider.isSliderDown():
            self.slider.blockSignals(True)
            self.slider.setValue(round((shown if shown is not None else logic.DEFAULT_SENSITIVITY) * 100))
            self.slider.blockSignals(False)
            self._describe(shown)
        if wanted is not None and (reported is None or abs(wanted - reported) > 1e-9):
            now = f"{reported:.2f}" if reported is not None else logic.sensitivity_name(None).lower()
            self.pending.setText(f"Device still at {now}. Change to {wanted:.2f} on its way.")
            self.pending.show()
        else:
            self.pending.hide()

    def _moved(self, v: int) -> None:
        self._describe(v / 100)
        if not self.slider.isSliderDown():
            self._commit(v / 100)  # keyboard or click on the track

    def _commit(self, value: float) -> None:
        value = round(min(1.0, max(0.0, value)), 2)
        self._describe(value)
        self.sensitivity_wanted.emit(value)

    def _describe(self, s) -> None:
        name = logic.sensitivity_name(s)
        if s is None:
            self.sens_value.setText("Sensitivity")
            self.sens_note.setText(f"{name}, default {logic.DEFAULT_SENSITIVITY:.2f}")
        else:
            self.sens_value.setText(f"Sensitivity {s:.2f}")
            self.sens_note.setText(f"· {name}, default {logic.DEFAULT_SENSITIVITY:.2f}")
        self.presets.set_current(next((k for k, _ in PRESETS if s is not None and abs(float(k) - s) < 0.005), None))
        m = logic.margins(s)
        b = f'<span style="color:{t.TEXT}; font-weight:600">'
        line = logic.alarm_line(s if s is not None else logic.DEFAULT_SENSITIVITY)
        self.impact.setText(
            f"Counts as wrong at vibration {b}{short(m['vibration'])}×</span> normal, "
            f"{b}+{m['temperature']:.1f} °C</span>, {b}+{m['sound']:.1f} dB</span>, "
            f"model line {b}{line:.2f}×</span>. Limits for any machine stay fixed.")

    # ---- actions -------------------------------------------------------------

    def set_agent_running(self, running: bool) -> None:
        self.btn_agent.setEnabled(not running)
        self.btn_agent.setToolTip("A report agent is already running for this machine." if running else "")

    def say(self, text: str) -> None:
        self.flash.setText(text)
        self.flash.setVisible(bool(text))

    # ---- simulator ---------------------------------------------------------

    def show_sim(self, sim) -> None:
        self.sim.setVisible(sim is not None)
        if sim is not None:
            self.sim_status.setText(sim.status())

    def _pause(self) -> None:
        self._paused = not self._paused
        self.btn_pause.setText("Resume" if self._paused else "Pause")
        self.sim_key.emit("p")
