"""RED is for shapes only: no text on either page is drawn in the red family."""

import pytest
from app.pages.spectrum import SpectrumPage
from app.pages.thermal import ThermalPage
from app.theme import tokens as T
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QLabel
from signal_helpers import state, themed


@pytest.fixture
def text_pens(monkeypatch):
    """Record the pen colour of every QPainter.drawText made from Python."""
    seen = []
    real = QPainter.drawText

    def spy(self, *args):
        seen.append((self.pen().color(), args[-1] if isinstance(args[-1], str) else args))
        return real(self, *args)

    monkeypatch.setattr(QPainter, "drawText", spy)
    return seen


@pytest.mark.parametrize("cls", [SpectrumPage, ThermalPage])
def test_no_red_text(qtbot, qapp, cls, text_pens):
    themed(qapp)
    st = state()
    p = cls()
    qtbot.addWidget(p)
    p.resize(1320, 896)
    p.bind(st)
    p.show()
    qtbot.waitExposed(p)
    p.grab()
    assert text_pens, "no text was painted"
    red = [text for color, text in text_pens if T.is_red(color)]
    assert red == []
    for label in p.findChildren(QLabel):
        assert not T.is_red(label.palette().color(label.foregroundRole())), label.text()
    if cls is SpectrumPage:
        for r in range(p.model.rowCount()):
            for c in range(p.model.columnCount()):
                assert not T.is_red(p.model.item(r, c).foreground().color())
