"""The pairing QR code: the line from `state.scans.qr_text()` as squares the phone's camera reads
(contract section 8.2).

The pure-Python package `segno` only works out which squares are dark. The drawing is QPainter, as
for every chart in this app. No image file, no web view.

Always dark squares on a white card with a clear border, whatever the app's colour scheme: a camera
reads a QR code by contrast, and a code drawn in the theme's own colours on a dark panel may not
read. Never red: red is for the app's own marks.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QSizePolicy, QWidget

QUIET = 4  # clear squares around the code: the QR standard asks for four
DARK = "#000000"
LIGHT = "#ffffff"
CORNER = 10  # px, the card's rounded corner. It stays inside the clear border.
NO_PACKAGE = ("The QR code cannot be drawn: the package segno is not installed. Install it with "
              "pip install -r platform/desktop/requirements.txt. The line below still works when pasted "
              "into the phone app.")
TOO_LONG = "The QR code cannot be drawn: the line is too long for one code."


def matrix(text: str) -> list[list[bool]]:
    """The code's squares for this text, row by row, True where dark, without the clear border.
    [] when there is no text or it cannot be drawn: `problem()` says which."""
    if not text:
        return []
    try:
        import segno
    except ImportError:
        return []
    try:
        code = segno.make(text, error="m", micro=False)
    except ValueError:  # segno's DataOverflowError is one
        return []
    return [[bool(v) for v in row] for row in code.matrix]


def problem(text: str) -> str:
    """Why `matrix(text)` is empty although there is a text, as a sentence for the person. "" otherwise."""
    if not text or matrix(text):
        return ""
    try:
        import segno  # noqa: F401
    except ImportError:
        return NO_PACKAGE
    return TOO_LONG


def square_px(side_px: float, squares: int) -> int:
    """How many whole pixels one square gets when a code of `squares` a side, plus its clear border,
    is drawn in `side_px`. Whole pixels keep every edge sharp. 0 means it does not fit."""
    return int(side_px // (squares + 2 * QUIET)) if squares > 0 else 0


class QrCode(QWidget):
    """One QR code on a white card. `set_text("")` leaves the card away."""

    def __init__(self, side: int = 168, parent: QWidget | None = None):
        super().__init__(parent)
        self._side = side
        self._text = ""
        self._squares: list[list[bool]] = []
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setAccessibleName("Pairing QR code")

    def sizeHint(self) -> QSize:
        return QSize(self._side, self._side)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def text(self) -> str:
        return self._text

    def squares(self) -> list[list[bool]]:
        return self._squares

    def set_text(self, text: str) -> None:
        text = text or ""
        if text != self._text:
            self._text = text
            self._squares = matrix(text)
            self.update()

    def code_rect(self) -> QRectF:
        """Where the squares are drawn, in widget pixels: centred, each square a whole number of pixels."""
        n = len(self._squares)
        px = square_px(min(self.width(), self.height()), n)
        size = px * n
        return QRectF((self.width() - size) // 2, (self.height() - size) // 2, size, size)

    def paintEvent(self, _event) -> None:
        n = len(self._squares)
        if not n:
            return
        p = QPainter(self)
        area = self.code_rect()
        px = area.width() / n
        if px < 1:
            return
        border = QUIET * px
        p.setPen(Qt.PenStyle.NoPen)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor(LIGHT))
        p.drawRoundedRect(area.adjusted(-border, -border, border, border), CORNER, CORNER)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        p.setBrush(QColor(DARK))
        for r, row in enumerate(self._squares):
            c = 0
            while c < n:  # one rectangle for each run of dark squares, so no hairline shows between two
                if not row[c]:
                    c += 1
                    continue
                start = c
                while c < n and row[c]:
                    c += 1
                p.drawRect(QRectF(area.left() + start * px, area.top() + r * px, (c - start) * px, px))
