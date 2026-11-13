import pytest
from app.widgets import Button, DataTable, KpiStrip, Panel, Segmented, Toggle
from PySide6.QtCore import Qt
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QVBoxLayout, QWidget


def test_panels_number_in_creation_order(qtbot):
    page = QWidget()
    page.key = "x"
    qtbot.addWidget(page)
    lay = QVBoxLayout(page)
    a, b, c = Panel("A"), Panel("B", number=False), Panel("C")
    for p in (a, b, c):
        lay.addWidget(p)
    page.show()
    assert a.title_label._number == "01"
    assert b.title_label._number == ""
    assert c.title_label._number == "02"
    assert c.title_label.geometry().width() >= c.title_label.sizeHint().width()


def test_link_panel_emits_clicked(qtbot):
    p = Panel("Spectrum", "hint", link=True)
    qtbot.addWidget(p)
    p.resize(400, 200)
    p.show()
    with qtbot.waitSignal(p.clicked):
        qtbot.mouseClick(p.body, Qt.MouseButton.LeftButton)


def test_segmented(qtbot):
    s = Segmented([("25", "Low"), ("50", "Balanced"), ("75", "High")], current="50")
    qtbot.addWidget(s)
    assert s.current() == "50"
    with qtbot.waitSignal(s.changed) as sig:
        qtbot.mouseClick(s.button("75"), Qt.MouseButton.LeftButton)
    assert sig.args == ["75"]
    s.set_current("25")
    assert s.current() == "25"


def test_toggle(qtbot):
    tg = Toggle()
    qtbot.addWidget(tg)
    assert tg.size().width() == 34 and tg.size().height() == 20
    qtbot.mouseClick(tg, Qt.MouseButton.LeftButton)
    assert tg.isChecked()


def test_button_kinds():
    assert Button("x", "primary").kind == "primary"
    with pytest.raises(ValueError):
        Button("x", "danger")


def test_kpi_strip():
    k = KpiStrip(["A", "B"])
    k.cell(1).set_value("3", abnormal=True)
    assert k.cell(1).value() == "3" and k.cell(1).abnormal()
    assert k.cell(0).property("first") == "true"


def test_table_row_clicked(qtbot):
    m = QStandardItemModel(3, 2)
    for r in range(3):
        m.setItem(r, 0, QStandardItem(f"r{r}"))
    tb = DataTable(clickable=True)
    tb.setModel(m)
    qtbot.addWidget(tb)
    tb.resize(300, 200)
    tb.show()
    rect = tb.visualRect(m.index(1, 0))
    with qtbot.waitSignal(tb.row_clicked) as sig:
        qtbot.mouseClick(tb.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    assert sig.args == [1]
    assert tb.verticalHeader().defaultSectionSize() == 36
