"""The pairing QR code (app/pages/machine/qr.py): the squares from segno, and their drawing with QPainter.

No QR reader is among the test packages, so these tests check the code's structure and that what is
drawn is the matrix, square for square. Reading it with a phone is a check by hand.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")
pytest.importorskip("segno")

from app.pages.machine import qr
from app.theme import tokens as t
from PySide6.QtGui import QColor, QImage

LINE = "effente://scan?v=1&base=http%3A%2F%2F192.168.43.10%3A8000&machine=conveyor-07&name=Conveyor%207"
FINDER = ["1111111", "1000001", "1011101", "1011101", "1011101", "1000001", "1111111"]


def block(squares, top, left):
    return ["".join("1" if squares[top + r][left + c] else "0" for c in range(7)) for r in range(7)]


def test_the_matrix_is_a_square_qr_code_with_its_three_corner_marks():
    squares = qr.matrix(LINE)
    n = len(squares)
    assert n >= 21 and (n - 17) % 4 == 0  # a QR code's side is 17 + 4 x its version
    assert all(len(row) == n for row in squares)
    assert block(squares, 0, 0) == FINDER
    assert block(squares, 0, n - 7) == FINDER
    assert block(squares, n - 7, 0) == FINDER
    assert block(squares, n - 7, n - 7) != FINDER  # the fourth corner has none


def test_the_matrix_holds_this_text():
    import segno

    assert qr.matrix(LINE) == [[bool(v) for v in row] for row in segno.make(LINE, error="m", micro=False).matrix]
    assert qr.matrix(LINE) != qr.matrix(LINE.replace("conveyor-07", "conveyor-08"))


def test_no_text_is_no_code_and_no_problem():
    assert qr.matrix("") == []
    assert qr.problem("") == ""
    assert qr.problem(LINE) == ""


def test_a_line_too_long_for_one_code_says_so():
    long = "effente://scan?v=1&name=" + "x" * 8000
    assert qr.matrix(long) == []
    assert qr.problem(long) == qr.TOO_LONG


def test_a_laptop_without_segno_says_what_to_install(monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "segno", None)  # makes `import segno` raise ImportError
    assert qr.matrix(LINE) == []
    assert "segno" in qr.problem(LINE) and "requirements.txt" in qr.problem(LINE)


def test_each_square_gets_whole_pixels_and_the_clear_border_fits():
    assert qr.square_px(168, 33) == 4  # 33 squares + 4 clear each side = 41, and 41 x 4 = 164 <= 168
    assert qr.square_px(40, 33) == 0
    assert qr.square_px(168, 0) == 0


def shown(widget) -> QImage:
    """The widget as a picture, one picture pixel for each widget pixel whatever the screen's scale."""
    image = QImage(widget.size(), QImage.Format.Format_ARGB32)
    image.fill(QColor(0, 0, 0, 0))
    widget.render(image)
    return image


def test_what_is_drawn_is_the_matrix_square_for_square(qtbot):
    w = qr.QrCode(168)
    qtbot.addWidget(w)
    w.set_text(LINE)
    w.show()
    squares, area = w.squares(), w.code_rect()
    n = len(squares)
    px = area.width() / n
    assert px == int(px) and px >= 3
    image = shown(w)
    for r in range(n):
        for c in range(n):
            at = image.pixelColor(int(area.left() + (c + 0.5) * px), int(area.top() + (r + 0.5) * px))
            assert at == QColor(qr.DARK if squares[r][c] else qr.LIGHT), (r, c)


def test_the_code_has_four_clear_squares_around_it(qtbot):
    w = qr.QrCode(168)
    qtbot.addWidget(w)
    w.set_text(LINE)
    w.show()
    area, image = w.code_rect(), shown(w)
    px = area.width() / len(w.squares())
    mid = int(area.center().y())
    for step in range(1, qr.QUIET + 1):
        assert image.pixelColor(int(area.left() - (step - 0.5) * px), mid) == QColor(qr.LIGHT)
        assert image.pixelColor(int(area.right() + (step - 0.5) * px), mid) == QColor(qr.LIGHT)


def test_the_code_is_dark_on_white_and_never_red(qtbot):
    w = qr.QrCode(168)
    qtbot.addWidget(w)
    w.set_text(LINE)
    w.show()
    image = shown(w)
    seen = {image.pixelColor(x, y).name() for x in range(0, image.width(), 2) for y in range(0, image.height(), 2)}
    assert not any(t.is_red(colour) for colour in seen)
    assert {qr.DARK, qr.LIGHT} <= seen


def test_no_text_draws_no_card(qtbot):
    w = qr.QrCode(168)
    qtbot.addWidget(w)
    w.set_text(LINE)
    w.set_text("")
    w.show()
    image = shown(w)
    assert w.squares() == []
    assert qr.LIGHT not in {image.pixelColor(x, 84).name() for x in range(0, 168, 4)}
