"""Pages open with the choices made last run: a range, a filter, a view."""

import pytest
from app.pages.agents import AgentsPage
from app.pages.history import HistoryPage
from app.pages.log import LogPage
from app.pages.machine import MachinePage
from app.pages.thermal import ThermalPage
from app.settings import Choices
from app.shell import MainWindow
from fakes import FakeState
from PySide6.QtCore import QSettings


@pytest.fixture
def store(tmp_path):
    return QSettings(str(tmp_path / "effente.ini"), QSettings.Format.IniFormat)


def made(qtbot, cls, store):
    page = cls()
    qtbot.addWidget(page)
    page.remember(Choices(store, cls.key))
    page.bind(FakeState())
    return page


def pick(combo, key):
    combo.setCurrentIndex(combo.findData(key))


def test_choices_keep_nothing_without_a_store():
    choices = Choices()
    choices.set("range", "all")
    assert choices.get("range", ["all"]) is None


def test_a_kept_value_that_is_no_longer_offered_is_dropped(store):
    Choices(store, "history").set("range", "86400")
    assert Choices(store, "history").get("range", ["300", "3600", "all"]) is None
    assert Choices(store, "thermal").get("range", ["86400"]) is None  # another page's choice


def test_history_keeps_its_range_and_filter(qtbot, store):
    first = made(qtbot, HistoryPage, store)
    assert (first.range.currentData(), first.filter.currentData()) == ("300", "")
    pick(first.range, "all")
    pick(first.filter, "abn")
    second = made(qtbot, HistoryPage, store)
    assert (second.range.currentData(), second.filter.currentData()) == ("all", "abn")


def test_thermal_keeps_its_scale_view_and_grid(qtbot, store):
    first = made(qtbot, ThermalPage, store)
    first.scale_seg.button("fixed").click()
    first.view_seg.button("pixels").click()
    first.grid_toggle.click()
    second = made(qtbot, ThermalPage, store)
    assert (second.scale_seg.current(), second.view_seg.current()) == ("fixed", "pixels")
    assert not second.grid_toggle.isChecked()
    assert not second.image.smooth()


def test_log_keeps_its_kind_filter(qtbot, store):
    first = made(qtbot, LogPage, store)
    kind = first.kind_filter.keys()[1]
    first.kind_filter.button(kind).click()
    second = made(qtbot, LogPage, store)
    assert (second.kind_filter.current(), second.kind) == (kind, kind)


def test_agents_keeps_its_mode(qtbot, store):
    first = made(qtbot, AgentsPage, store)
    first.mode.button("local").click()
    second = made(qtbot, AgentsPage, store)
    assert second.mode.current() == "local"
    assert not second.model_section.isHidden()


def test_machine_keeps_its_chart_range(qtbot, store):
    first = made(qtbot, MachinePage, store)
    first.part_column.chart_panel.range.button("7d").click()
    second = made(qtbot, MachinePage, store)
    assert (second.part_column.chart_panel.range.current(), second.range) == ("7d", "7d")


def test_the_window_hands_every_page_its_choices(qtbot, store):
    store.setValue("pages/history/range", "3600")
    page = HistoryPage()
    w = MainWindow(None, pages={"history": page}, placeholders=True, settings=store)
    qtbot.addWidget(w)
    assert page.range.currentData() == "3600"


def test_a_page_built_without_the_window_still_works(qtbot):
    page = LogPage()
    qtbot.addWidget(page)
    page.bind(FakeState())
    page.set_kind(page.kind_filter.keys()[1])  # nothing to keep it in, and no error
