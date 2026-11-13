"""Panel and KpiStrip: the two card surfaces every page is built from."""

from __future__ import annotations

import itertools

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QFontMetrics, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..theme import fonts
from ..theme import tokens as t
from .marks import paint_flag_value

_order = itertools.count()


class _ElidedLabel(QLabel):
    """A one-line label that elides on the right instead of growing its parent."""

    def __init__(self, text: str = "", parent: QWidget | None = None):
        super().__init__(text, parent)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(0)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setPen(self.palette().windowText().color())
        text = QFontMetrics(self.font()).elidedText(self.text(), Qt.TextElideMode.ElideRight, self.width())
        p.drawText(self.rect(), int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), text)


class _Title(QWidget):
    """The panel's number and title, painted together: "01  Signals", with the arrow
    a clickable panel shows on hover."""

    def __init__(self, title: str, parent: QWidget | None = None):
        super().__init__(parent)
        self._title = title
        self._number: str = ""
        self.link = False
        self.arrow = False
        self.setFont(fonts.panel_title())
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)

    def set_title(self, title: str) -> None:
        self._title = title
        self.updateGeometry()
        self.update()

    def set_number(self, number: str) -> None:
        if number != self._number:
            self._number = number
            self.updateGeometry()
            self.update()

    def _prefix(self) -> str:
        return f"{self._number}  " if self._number else ""

    def sizeHint(self) -> QSize:
        fm = self.fontMetrics()
        w = fm.horizontalAdvance(self._prefix() + self._title)
        if self.link:
            w += 6 + fm.horizontalAdvance("\u2192")
        return QSize(w + 1, fm.height())

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        fm = self.fontMetrics()
        r = QRectF(self.rect())
        x = 0.0
        flags = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        if self._number:
            p.setPen(t.qcolor(t.MUTED))
            p.drawText(r, flags, self._prefix())
            x = fm.horizontalAdvance(self._prefix())
        p.setPen(t.qcolor(t.TEXT))
        p.drawText(r.adjusted(x, 0, 0, 0), flags, self._title)
        if self.arrow:
            x += fm.horizontalAdvance(self._title) + 6
            p.drawText(r.adjusted(x, 0, 0, 0), flags, "→")


class Panel(QFrame):
    """A card with a header (number, title, hint, controls) and a body.

    - `body` is a QWidget with a zero-margin QVBoxLayout; add content with
      `panel.body.layout().addWidget(w)` or `panel.add(w)`. `padded=True` gives it the
      mockup's 14px 16px padding.
    - `header_end` is a QHBoxLayout at the header's right end, for controls.
    - The "01" number counts the page's panels in creation order. Pass `number=` to
      set it, or `number=False` to hide it.
    - `link=True` makes the whole panel clickable: the border lifts on hover, the title
      shows an arrow, and `clicked` is emitted.
    """

    clicked = Signal()

    def __init__(self, title: str, hint: str = "", parent: QWidget | None = None, *,
                 padded: bool = False, link: bool = False, number: int | bool | None = None):
        super().__init__(parent)
        self._order = next(_order)
        self._number = number
        self._link = link
        self.setProperty("cls", "panel")
        self.setProperty("link", "true" if link else "false")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, link)
        if link:
            self.setCursor(Qt.CursorShape.PointingHandCursor)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(1, 1, 1, 1)
        outer.setSpacing(0)

        self.head = QWidget(self)
        self.head.setProperty("cls", "panel-head")
        self.head.setMinimumHeight(t.PANEL_HEAD_MIN_H)
        hl = QHBoxLayout(self.head)
        pv, ph = t.PANEL_HEAD_PAD
        # The mockup's .ph keeps a transparent 1px bottom border, so a header grows to
        # content + 10 + 10 + 1: 53px with a 32px control, never under 44px.
        hl.setContentsMargins(ph - 1, pv, ph - 1, pv + 1)
        hl.setSpacing(t.PANEL_HEAD_GAP)
        self.title_label = _Title(title, self.head)
        self.title_label.link = link
        self.hint_label = _ElidedLabel(hint, self.head)
        self.hint_label.setProperty("role", "hint")
        self.hint_label.setFont(fonts.body())
        # Always in the layout, even empty: it holds the stretch that keeps the title
        # at the left and the header controls at the right.
        hl.addWidget(self.title_label)
        hl.addWidget(self.hint_label, 1)
        self.header_end = QHBoxLayout()
        self.header_end.setSpacing(8)
        self.header_end.setContentsMargins(0, 0, 0, 0)
        hl.addLayout(self.header_end)
        outer.addWidget(self.head)

        self.body = QWidget(self)
        self.body.setProperty("cls", "panel-body")
        bl = QVBoxLayout(self.body)
        bl.setSpacing(0)
        if padded:
            bv, bh = t.PANEL_BODY_PAD
            bl.setContentsMargins(bh - 1, bv, bh - 1, bv)
        else:
            bl.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.body, 1)

    # content
    def add(self, widget: QWidget, stretch: int = 0) -> QWidget:
        self.body.layout().addWidget(widget, stretch)
        return widget

    def set_title(self, title: str) -> None:
        self.title_label.set_title(title)

    def set_hint(self, hint: str) -> None:
        self.hint_label.setText(hint)

    def title(self) -> str:
        return self.title_label._title

    def hint(self) -> str:
        return self.hint_label.text()

    # numbering
    def _page(self) -> QWidget:
        w = self.parentWidget()
        while w is not None:
            if isinstance(getattr(type(w), "key", None), str):
                return w
            if w.parentWidget() is None:
                return w
            w = w.parentWidget()
        return self

    def _renumber(self) -> None:
        if self._number is False:
            self.title_label.set_number("")
            return
        if isinstance(self._number, int) and self._number is not True:
            self.title_label.set_number(f"{self._number:02d}")
            return
        page = self._page()
        mine = sorted((p for p in page.findChildren(Panel) if p._number is None), key=lambda p: p._order)
        self.title_label.set_number(f"{mine.index(self) + 1:02d}" if self in mine else "")

    def showEvent(self, event) -> None:
        before = self.title_label.sizeHint()
        self._renumber()
        if self.title_label.sizeHint() != before:
            # Shown panels skip the posted relayout, so lay the header out now.
            self.head.layout().invalidate()
            self.head.layout().activate()
        super().showEvent(event)

    # link behaviour
    def event(self, e):
        if e.type() == e.Type.Polish:
            self._renumber()  # before the first layout, so the title gets its width
        if self._link and e.type() in (e.Type.HoverEnter, e.Type.HoverLeave):
            self.title_label.arrow = e.type() == e.Type.HoverEnter
            self.title_label.update()
        return super().event(e)

    def mouseReleaseEvent(self, e) -> None:
        if self._link and e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(e)


class KpiCell(QFrame):
    """One cell of a KpiStrip: label, value (with the abnormal dot), sub line, optional meter."""

    def __init__(self, label: str, value: str = "–", sub: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.setProperty("cls", "kpi")
        self.setProperty("first", "false")
        self._value = value
        self._abnormal = False
        self._meter: float | None = None
        lay = QVBoxLayout(self)
        pv, ph = t.KPI_PAD
        lay.setContentsMargins(ph, pv, ph, pv)
        lay.setSpacing(2)
        self.label = QLabel(label, self)
        self.label.setProperty("role", "soft")
        self.label.setFont(fonts.small())
        self.value_widget = _KpiValue(self)
        self.sub = QLabel(sub, self)
        self.sub.setProperty("role", "muted")
        self.sub.setFont(fonts.small())
        self.sub.setVisible(bool(sub))
        self.meter = _Meter(self)
        self.meter.setVisible(False)
        lay.addWidget(self.label)
        lay.addWidget(self.value_widget)
        lay.addWidget(self.sub)
        lay.addWidget(self.meter)
        lay.addStretch(1)

    def value(self) -> str:
        return self._value

    def abnormal(self) -> bool:
        return self._abnormal

    def set_value(self, text: str, abnormal: bool = False) -> None:
        self._value, self._abnormal = text, bool(abnormal)
        self.value_widget.update()

    def set_sub(self, text: str) -> None:
        self.sub.setText(text)
        self.sub.setVisible(bool(text))

    def set_label(self, text: str) -> None:
        self.label.setText(text)

    def set_meter(self, fraction: float | None) -> None:
        """A 6px bar under the value, 0 to 1. None hides it."""
        self._meter = None if fraction is None else min(1.0, max(0.0, fraction))
        self.meter.setVisible(self._meter is not None)
        self.meter.update()


class _KpiValue(QWidget):
    def __init__(self, cell: KpiCell):
        super().__init__(cell)
        self._cell = cell
        self.setFont(fonts.kpi_value())
        self.setFixedHeight(round(t.FONT_KPI * 1.25))

    def paintEvent(self, _event) -> None:
        paint_flag_value(QPainter(self), QRectF(self.rect()), self._cell._value, self._cell._abnormal,
                         self.font(), t.qcolor(t.TEXT))


class _Meter(QWidget):
    def __init__(self, cell: KpiCell):
        super().__init__(cell)
        self._cell = cell
        self.setFixedHeight(12)  # 6px bar + 6px top margin

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        r = QRectF(0, 6, self.width(), 6)
        p.setBrush(t.qcolor(t.RAISED))
        p.drawRoundedRect(r, 3, 3)
        if self._cell._meter:
            p.setBrush(t.qcolor(t.SOFT))
            p.drawRoundedRect(QRectF(0, 6, self.width() * self._cell._meter, 6), 3, 3)


class KpiStrip(QFrame):
    """One surface split into equal cells by hairlines. Build with labels, or add() cells.

        strip = KpiStrip(["Max", "Bearing", "Room"])
        strip.cell(0).set_value("46.2 °C", abnormal=True)
    """

    def __init__(self, labels: list[str] | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setProperty("cls", "kpis")
        self._cells: list[KpiCell] = []
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(1, 1, 1, 1)
        self._lay.setSpacing(0)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        for label in labels or []:
            self.add(label)

    def add(self, label: str, value: str = "–", sub: str = "") -> KpiCell:
        cell = KpiCell(label, value, sub, self)
        if not self._cells:
            cell.setProperty("first", "true")
        self._cells.append(cell)
        self._lay.addWidget(cell, 1)
        return cell

    def cell(self, i: int) -> KpiCell:
        return self._cells[i]

    def cells(self) -> list[KpiCell]:
        return list(self._cells)
