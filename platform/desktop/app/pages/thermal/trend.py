"""Heat trend: hottest and average above room over the last 2 minutes (the
mockup's lineChart on t-trend)."""

from __future__ import annotations

from datetime import datetime

from app.pages.thermal import stats
from app.theme import tokens as T
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

M_LEFT, M_RIGHT, M_TOP, M_BOTTOM = 44, 8, 8, 22


def _hms(ms: float) -> str:
    return datetime.fromtimestamp(ms / 1000).strftime("%H:%M:%S")


class HeatTrend(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._pts: list[tuple[float, float, float | None]] = []
        self.setMinimumSize(240, 140)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_points(self, pts) -> None:
        self._pts = list(pts)
        self.update()

    def top(self) -> int:
        return stats.trend_top([p[1] for p in self._pts])

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        f = QFont(self.font())
        f.setPixelSize(12)
        p.setFont(f)
        muted = T.qcolor(T.MUTED)
        plot = QRectF(M_LEFT, M_TOP, self.width() - M_LEFT - M_RIGHT, self.height() - M_TOP - M_BOTTOM)
        top = self.top()

        def y_of(v: float) -> float:
            return plot.top() + plot.height() * (1 - min(max(v, 0), top) / top)

        for v in (0, top / 2, top):
            y = round(y_of(v)) + 0.5
            p.setPen(QPen(T.qcolor(T.GRID), 1))
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            p.setPen(muted)
            p.drawText(QRectF(0, y - 8, M_LEFT - 8, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"{v:g} °C")

        pts = self._pts
        if len(pts) < 2:
            p.setPen(muted)
            p.drawText(plot, Qt.AlignmentFlag.AlignCenter, "Not enough heat readings yet")
            return
        t0, t1 = pts[0][0], pts[-1][0]
        span = max(1.0, t1 - t0)

        def x_of(t: float) -> float:
            return plot.left() + (t - t0) / span * plot.width()

        def line(idx: int, color, width: float, dash=None) -> None:
            path, started = QPainterPath(), False
            for pt in pts:
                v = pt[idx]
                if v is None:
                    started = False
                    continue
                q = QPointF(x_of(pt[0]), y_of(v))
                path.lineTo(q) if started else path.moveTo(q)
                started = True
            pen = QPen(color, width)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            if dash:
                pen.setDashPattern(dash)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)

        line(2, T.qcolor(T.SOFT), 1.6)
        line(1, T.qcolor(T.RED), 1.6)

        p.setPen(muted)
        for k in range(6):
            t = t0 + span * k / 5
            x = x_of(t)
            align = Qt.AlignmentFlag.AlignLeft if k == 0 else Qt.AlignmentFlag.AlignRight if k == 5 else Qt.AlignmentFlag.AlignHCenter
            box = QRectF(x - 60 if k else x, self.height() - 18, 120 if 0 < k < 5 else 60, 16)
            if k == 5:
                box = QRectF(x - 60, self.height() - 18, 60, 16)
            p.drawText(box, align | Qt.AlignmentFlag.AlignVCenter, _hms(t))
