"""The Machine page's middle column: the selected part's name and state with the probe control, its
four tiles, its velocity over time with the projection's sentence, and the evidence of its newest visit.

It only shows what the page hands it (worked out in readings.py and logic.py) and reports clicks.
"""

from __future__ import annotations

from app.theme import fonts
from app.theme import tokens as t
from app.theme.qss import css
from app.widgets import Button, Panel, Segmented, SimChip, motion
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import readings as rd
from .charts import MovedChart, SpectrumChart, VelocityChart
from .marks import PinMark, keep_place, label

PAD_V, PAD_H = t.PANEL_BODY_PAD
SIM = "Simulated"
SIM_TIP = "Drawn from simulated readings, not from a real sensor (contract 9.6)."


def _sim_chip() -> SimChip:
    chip = SimChip(SIM)
    chip.setToolTip(SIM_TIP)
    keep_place(chip)
    chip.hide()
    return chip


def _box(name: str) -> QFrame:
    """A card the same as a panel's, for the tiles and the blank state."""
    f = QFrame()
    f.setObjectName(name)
    f.setStyleSheet(f"#{name} {{ background: {t.PANEL}; border: 1px solid {css(t.BORDER_PANEL)}; "
                    f"border-radius: {t.R}px; }}")
    return f


class Elided(QLabel):
    """One line that ends in an ellipsis instead of being cut, with the whole text in its tooltip."""

    def __init__(self, text: str, role: str, font):
        super().__init__(text)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setProperty("role", role)
        self.setFont(font)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(0)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setPen(self.palette().windowText().color())
        text = self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, self.width())
        p.drawText(self.rect(), int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), text)


class Fitted(Elided):
    """One line that shrinks its font to fit, down to `floor` px, so a number is never cut. Only below
    that does it end in an ellipsis, with the whole text in its tooltip."""

    def __init__(self, text: str, role: str, font, floor: int):
        super().__init__(text, role, font)
        self.floor = floor

    def setText(self, text: str) -> None:
        super().setText(text)
        self.setToolTip(text)

    def fitted_font(self) -> QFont:
        font = QFont(self.font())
        size = font.pixelSize()  # the theme sets sizes in px
        while size > self.floor and QFontMetrics(font).horizontalAdvance(self.text()) > self.width():
            size -= 1
            font.setPixelSize(size)
        return font

    def fits(self) -> bool:
        return QFontMetrics(self.fitted_font()).horizontalAdvance(self.text()) <= self.width()

    def paintEvent(self, _event) -> None:
        font = self.fitted_font()
        p = QPainter(self)
        p.setFont(font)
        p.setPen(self.palette().windowText().color())
        text = QFontMetrics(font).elidedText(self.text(), Qt.TextElideMode.ElideRight, self.width())
        p.drawText(self.rect(), int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), text)


PROBE_HERE_NOTE = ("Say the probe sits on this part now. Its records are then kept for this part. "
                   "Selecting a part does not move the probe.")
PROBE_OFF_NOTE = ("The probe is on this part. Press to say it is on no part: records that arrive are then "
                  "kept for the machine only.")


class PartHead(QWidget):
    """The part's mark, name, kind and state, what that state means, and the probe and pin buttons."""

    probe_here = Signal()
    probe_off = Signal()  # the probe is on this part and the button was pressed: it is on no part now
    move_pin = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        col = QVBoxLayout(self)
        col.setContentsMargins(4, 0, 0, 0)
        col.setSpacing(2)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        self.mark = PinMark()
        self.name = label("", "strong", fonts.font(t.FONT_PAGE_TITLE - 1, 600, tracking=t.TRACK_TITLE))
        self.kind = label("", "muted")
        self.headline = Elided("", "strong", fonts.font(t.FONT_BASE, 600))
        self.pin_button = Button("", "ghost", small=True)
        self.pin_button.setToolTip("Then click the model where this part is")
        self.pin_button.clicked.connect(self.move_pin)
        self.probe_button = Button(rd.PROBE_HERE, small=True)
        self._probe_on_this = False
        self.probe_button.clicked.connect(self._probe_pressed)
        row.addWidget(self.mark, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.name)
        row.addWidget(self.kind, 0, Qt.AlignmentFlag.AlignBaseline)
        row.addWidget(self.headline, 1, Qt.AlignmentFlag.AlignBaseline)
        row.addWidget(self.probe_button)
        col.addLayout(row)
        under = QHBoxLayout()
        under.setContentsMargins(0, 0, 0, 0)
        under.setSpacing(10)
        self.means = label("", "muted", fonts.small(), wrap=True)
        # the tiles' header: says when the readings in them are simulated (contract 9.6)
        self.sim_chip = _sim_chip()
        self.sim_chip.setToolTip("The numbers in the tiles below come from simulated readings.")
        under.addWidget(self.means, 1)
        under.addWidget(self.pin_button, 0, Qt.AlignmentFlag.AlignBottom)
        under.addWidget(self.sim_chip, 0, Qt.AlignmentFlag.AlignBottom)
        col.addLayout(under)

    def set_detail(self, detail: dict, probe_on_this: bool) -> None:
        self.mark.set_state(detail["state"])
        self.name.setText(detail["name"])
        self.kind.setText(detail["kind"].lower())
        self.headline.setText(detail["short"])
        self.headline.setToolTip(detail["headline"])
        self.means.setText(detail["means"] + (f" {detail['pin_note']}" if detail["pin_note"] else ""))
        self.pin_button.setText(detail["pin_action"])
        self.pin_button.setVisible(bool(detail["pin_action"]))
        self._probe_on_this = probe_on_this
        self.probe_button.setText(rd.PROBE_OFF if probe_on_this else rd.PROBE_HERE)
        self.probe_button.setToolTip(PROBE_OFF_NOTE if probe_on_this else PROBE_HERE_NOTE)

    def _probe_pressed(self) -> None:
        (self.probe_off if self._probe_on_this else self.probe_here).emit()


class Tile(QFrame):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("machineTile")
        self.setStyleSheet(f"#machineTile {{ background: {t.PANEL}; border: 1px solid {css(t.BORDER_PANEL)}; "
                           f"border-radius: {t.R - 4}px; }}")
        col = QVBoxLayout(self)
        col.setContentsMargins(14, 8, 10, 8)
        col.setSpacing(0)
        self.label = label("", "muted", fonts.small())
        self.simulated = False
        self.value = Fitted("", "strong", fonts.font(22, 600, tracking=t.TRACK_TITLE), 13)
        self.sub = Fitted("", "muted", fonts.small(), 10)
        col.addWidget(self.label)
        col.addWidget(self.value)
        col.addWidget(self.sub)

    def set_tile(self, tile: rd.Tile) -> None:
        self.label.setText(tile.label)
        self.value.setText(tile.value)
        self.sub.setText(tile.sub)
        self.simulated = tile.simulated


class Tiles(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        self.tiles = [Tile() for _ in range(4)]
        for tile in self.tiles:
            row.addWidget(tile, 1)

    def set_tiles(self, tiles: list[rd.Tile]) -> None:
        for widget, tile in zip(self.tiles, tiles, strict=True):
            widget.set_tile(tile)

    def simulated(self) -> bool:
        return any(tile.simulated for tile in self.tiles)


class ChartPanel(Panel):
    """Vibration over time, in mm/s, with the range buttons, and the projection's sentence under it."""

    range_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__("Vibration over time", parent=parent)  # the axis says mm/s; there is no room for a hint
        self.sim_chip = _sim_chip()
        self.range = Segmented([(k, lab) for k, lab, _ in rd.RANGES], current=rd.DEFAULT_RANGE, small=True)
        self.range.changed.connect(self.range_changed)
        self.header_end.addWidget(self.sim_chip)
        self.header_end.addWidget(self.range)
        self.chart = VelocityChart()
        self.add(self.chart, 1)
        self.projection = _box("machineProjection")
        self.projection.setStyleSheet(f"#machineProjection {{ background: transparent; "
                                      f"border: 1px solid {css(t.BORDER)}; border-radius: 8px; }}")
        pl = QVBoxLayout(self.projection)
        pl.setContentsMargins(12, 7, 12, 8)
        pl.setSpacing(1)
        self.projection_title = label("", "muted", fonts.small())
        self.sentence = label("", "strong", fonts.font(t.FONT_BASE, 600), wrap=True)
        self.under = label("", "muted", fonts.small(), wrap=True)
        pl.addWidget(self.projection_title)
        pl.addWidget(self.sentence)
        pl.addWidget(self.under)
        holder = QWidget()
        hl = QVBoxLayout(holder)
        hl.setContentsMargins(PAD_H, 4, PAD_H, PAD_H - 2)
        hl.addWidget(self.projection)
        self.add(holder)
        self._holder = holder

    def set_projection(self, p: rd.Projection | None) -> None:
        self._holder.setVisible(p is not None)
        if p is None:
            return
        self.projection_title.setText(p.title)
        self.sentence.setText(p.sentence)
        self.under.setText(p.under)
        self.under.setVisible(bool(p.under))


class EvidencePanel(Panel):
    """The newest visit's last record: what moved, or its spectrum against the normal."""

    view_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__("Evidence", parent=parent)
        self.sim_chip = _sim_chip()
        self.view = Segmented([("moved", "What moved"), ("spectrum", "Spectrum")], current="moved", small=True)
        self.view.changed.connect(self._show)
        self.header_end.addWidget(self.sim_chip)
        self.header_end.addWidget(self.view)
        self.stack = QStackedWidget()
        self.moved = MovedChart()
        self.spectrum = SpectrumChart()
        self.stack.addWidget(self.moved)
        self.stack.addWidget(self.spectrum)
        holder = QWidget()
        hl = QVBoxLayout(holder)
        hl.setContentsMargins(PAD_H - 8, 0, PAD_H - 4, 8)
        hl.addWidget(self.stack)
        self.add(holder, 1)

    def _show(self, key: str) -> None:
        self.stack.setCurrentWidget(self.moved if key == "moved" else self.spectrum)
        motion.swap(self.stack.currentWidget())
        self.view_changed.emit(key)


class Blank(QFrame):
    """In place of the readings: no part picked, the probe never on this part, or its readings on their way."""

    pressed = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("machineBlank")
        self.setStyleSheet(f"#machineBlank {{ background: {t.PANEL}; border: 1px solid {css(t.BORDER_PANEL)}; "
                           f"border-radius: {t.R}px; }}")
        col = QVBoxLayout(self)
        col.setContentsMargins(PAD_H * 2, PAD_V, PAD_H * 2, PAD_V)
        col.addStretch(1)
        self.title = label("", "strong", fonts.font(16, 600), wrap=True)
        self.text = label("", "muted", wrap=True)
        self.button = Button("", "secondary")
        self.button.clicked.connect(self.pressed)
        for w in (self.title, self.text):
            w.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            col.addWidget(w)
        col.addSpacing(14)
        col.addWidget(self.button, 0, Qt.AlignmentFlag.AlignHCenter)
        col.addStretch(1)

    def show_state(self, title: str, text: str, button: str = "") -> None:
        self.title.setText(title)
        self.text.setText(text)
        self.button.setText(button)
        self.button.setVisible(bool(button))


PICK_TITLE = "Pick a part"
PICK_TEXT = "Pick a part on the scan or in the list to see how it is running, how it has run, and where it is heading."
NO_PARTS_TEXT = "Add a part to the machine first. Its readings show here once the probe has been on it."
NEVER_TITLE = "The probe has not been on this part"
NEVER_TEXT = ("Put the probe on it and press Probe is here. Its readings, average and trend appear here "
              "after the first visit.")
FETCH_TITLE = "Fetching this part's readings"
FETCH_TEXT = "Asking the receiver for them."


class PartColumn(QWidget):
    """The middle column. `show_blank` or `show_readings` decides which half is shown."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(t.PAGE_GAP - 4)
        self.head = PartHead()
        self.tiles = Tiles()
        self.chart_panel = ChartPanel()
        self.evidence_panel = EvidencePanel()
        self.blank = Blank()
        col.addWidget(self.head)
        col.addWidget(self.tiles)
        col.addWidget(self.chart_panel, 14)
        col.addWidget(self.evidence_panel, 10)
        col.addWidget(self.blank, 1)
        for w in (self.chart_panel, self.evidence_panel, self.blank):
            w.setMinimumHeight(0)
        self._showing = None  # (what is shown, for which part) as last drawn

    def readings_shown(self) -> bool:
        return self.chart_panel.isVisibleTo(self)

    def _settle(self, showing: tuple, widgets: list) -> None:
        """Settle `widgets` in when this is another thing to show than last time. The page redraws
        with every record: only a real change may move anything. The head stays put while what is
        under it changes for the same part."""
        was, self._showing = self._showing, showing
        if showing == was or was is None or not self.isVisible():
            return
        if showing[1] is not None and showing[1] == was[1]:
            widgets = [w for w in widgets if w is not self.head]
        # From one part's readings to another's is done often: a shorter, tighter move than the
        # first time the readings come up.
        quick = showing[0] == was[0]
        step, ms, drop = (22, motion.REVEAL, 5.0) if quick else (motion.STAGGER, motion.RISE, 8.0)
        for i, w in enumerate(w for w in widgets if not w.isHidden()):
            motion.lift_in(w, delay=i * step, ms=ms, drop=drop)

    def show_blank(self, title: str, text: str, button: str = "", *, head: bool, part=None) -> None:
        """`part` is the id of the part it is about, if any: another part's blank settles in again."""
        self.head.setVisible(head)
        for w in (self.tiles, self.chart_panel, self.evidence_panel):
            w.setVisible(False)
        self.blank.show_state(title, text, button)
        self.blank.setVisible(True)
        self._settle((title, part), [self.head, self.blank])

    def show_readings(self, part=None) -> None:
        self.head.setVisible(True)
        for w in (self.tiles, self.chart_panel, self.evidence_panel):
            w.setVisible(True)
        self.blank.setVisible(False)
        self._settle(("readings", part), [self.head, self.tiles, self.chart_panel, self.evidence_panel])
