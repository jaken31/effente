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
    assert tb.verticalHeader().defaultSectionSize() == 38


def test_hintless_panel_keeps_title_left(qtbot):
    p = Panel("Report", number=False)
    p.header_end.addWidget(Button("Copy", small=True))
    qtbot.addWidget(p)
    p.resize(600, 200)
    p.show()
    assert p.title_label.geometry().left() == 15
    end = p.header_end.itemAt(0).widget()
    assert end.geometry().right() > 500


def test_cell_roles_paint(qtbot):
    from app.widgets import CellDelegate

    m = QStandardItemModel(2, 4)
    name = QStandardItem("Unbalance")
    name.setData("unbalance_like", DataTable.CodeRole)
    m.setItem(0, 0, name)
    tag = QStandardItem("possible · not validated")
    tag.setData(True, DataTable.TagRole)
    m.setItem(0, 1, tag)
    long = QStandardItem("once-per-turn vibration grown (unbalance, a bent shaft, a lost blade) " * 2)
    long.setData(True, DataTable.WrapRole)
    m.setItem(0, 2, long)
    code = QStandardItem("conveyor-07-20261114")
    code.setData(True, DataTable.MonoRole)
    code.setData(True, DataTable.StrongRole)
    m.setItem(0, 3, code)
    tint = QStandardItem("x")
    tint.setData(True, DataTable.TintRole)
    m.setItem(1, 0, tint)
    tb = DataTable(wrap=True)
    tb.setModel(m)
    qtbot.addWidget(tb)
    tb.resize(700, 300)
    tb.setColumnWidth(2, 200)
    tb.show()
    tb.resizeRowsToContents()
    assert isinstance(tb.itemDelegate(), CellDelegate)
    assert tb.rowHeight(0) > 38
    assert tb.rowHeight(1) == 38
    assert tb.itemDelegate().text_color(m.index(0, 3)).name() == "#ededed"
    assert not tb.grab().isNull()


def test_panel_header_fits_its_controls(qtbot):
    from PySide6.QtWidgets import QComboBox

    plain, ctl = Panel("Plain", "hint"), Panel("With control")
    combo = QComboBox()
    combo.addItem("Last hour")
    combo.setFixedHeight(32)
    ctl.header_end.addWidget(combo)
    for p in (plain, ctl):
        qtbot.addWidget(p)
        p.resize(500, 200)
        p.show()
    assert plain.head.height() == 44
    assert ctl.head.height() == 53


def test_page_scroll_keeps_a_page_at_its_natural_height(qtbot):
    from app.shell.window import MainWindow
    from app.widgets import Panel as P

    page = QWidget()
    lay = QVBoxLayout(page)
    for i in range(12):
        panel = P(f"Panel {i}")
        panel.header_end.addWidget(Segmented(["a", "b"]))
        lay.addWidget(panel)
    area = MainWindow._wrap(page)
    qtbot.addWidget(area)
    area.resize(800, 300)  # far shorter than the page
    area.show()
    qtbot.waitExposed(area)
    assert page.height() >= lay.sizeHint().height()  # scrolls instead of squeezing


def test_link_panel_arrow_follows_hover(qtbot):
    panel = Panel("Signals", link=True)
    qtbot.addWidget(panel)
    panel.show()
    title = panel.title_label
    title.set_arrow(True)
    assert title.arrow and title._arrow.at == 1.0
    title.set_arrow(False)
    assert not title.arrow and title._arrow.at == 0.0
