"""Ctrl+K: find a page, a machine or an action by typing."""

import pytest
from PySide6.QtCore import Qt


@pytest.fixture
def window(window, qtbot):
    window.activateWindow()
    qtbot.waitActive(window)
    return window


def open_search(window, qtbot):
    qtbot.keyClick(window, Qt.Key.Key_K, Qt.KeyboardModifier.ControlModifier)
    assert window.search.isVisible()
    assert window.search.field.hasFocus()
    return window.search


def labels(search):
    return [e.label for e in search.shown]


def test_ctrl_k_lists_pages_other_machines_and_actions(window, qtbot):
    search = open_search(window, qtbot)
    assert labels(search)[:7] == [window.pages[k].title for k in window.pages]
    assert "conveyor-08" in labels(search)
    assert "demo-fan-1" not in labels(search)  # the machine already shown
    assert "Run the report agent" in labels(search)
    assert "Search pages, machines and actions" not in labels(search)


def test_typing_narrows_and_enter_opens_the_page(window, qtbot):
    search = open_search(window, qtbot)
    qtbot.keyClicks(search.field, "hist")
    assert labels(search) == ["History", "Export CSV, on Live and History"]  # pages come first
    qtbot.keyClick(search.field, Qt.Key.Key_Return)
    assert window.current == "history"
    assert not search.isVisible()


def test_digits_type_into_the_field_instead_of_changing_page(window, qtbot):
    search = open_search(window, qtbot)
    qtbot.keyClicks(search.field, "08")
    assert search.field.text() == "08"
    assert window.current == "live"
    assert labels(search) == ["conveyor-08"]
    qtbot.keyClick(search.field, Qt.Key.Key_Return)
    assert window.state.current_machine() == "conveyor-08"


def test_every_word_must_match_in_any_order_and_case(window, qtbot):
    search = open_search(window, qtbot)
    qtbot.keyClicks(search.field, "AGENT run")
    assert labels(search) == ["Run the report agent"]


def test_arrows_move_the_choice_and_wrap(window, qtbot):
    search = open_search(window, qtbot)
    assert search.results.currentRow() == 0
    qtbot.keyClick(search.field, Qt.Key.Key_Down)
    qtbot.keyClick(search.field, Qt.Key.Key_Down)
    assert search.results.currentRow() == 2
    qtbot.keyClick(search.field, Qt.Key.Key_Return)
    assert window.current == "thermal"
    search = open_search(window, qtbot)
    qtbot.keyClick(search.field, Qt.Key.Key_Up)
    assert search.results.currentRow() == len(search.shown) - 1


def test_no_match_shows_no_rows_and_enter_does_nothing(window, qtbot):
    search = open_search(window, qtbot)
    qtbot.keyClicks(search.field, "zzz")
    assert labels(search) == []
    assert not search.results.isVisible()
    qtbot.keyClick(search.field, Qt.Key.Key_Return)
    assert window.current == "live"
    assert not search.isVisible()


def test_escape_closes_the_search_only(window, qtbot):
    window.navigate("thermal")
    search = open_search(window, qtbot)
    qtbot.keyClick(search.field, Qt.Key.Key_Escape)
    assert not search.isVisible()
    assert window.current == "thermal"


def test_ctrl_k_again_closes_it_and_it_opens_empty_next_time(window, qtbot):
    search = open_search(window, qtbot)
    qtbot.keyClicks(search.field, "hist")
    qtbot.keyClick(search.field, Qt.Key.Key_K, Qt.KeyboardModifier.ControlModifier)
    assert not search.isVisible()
    search = open_search(window, qtbot)
    assert search.field.text() == ""
    assert len(search.shown) == len(search.entries)


def test_a_click_on_a_row_runs_it(window, qtbot):
    search = open_search(window, qtbot)
    row = search.results.visualItemRect(search.results.item(3)).center()
    qtbot.mouseClick(search.results.viewport(), Qt.MouseButton.LeftButton, pos=row)
    assert window.current == "history"
    assert not search.isVisible()


def test_the_card_fits_the_smallest_window(window, qtbot):
    window.resize(960, 660)
    qtbot.waitUntil(lambda: window.width() == 960)
    search = open_search(window, qtbot)
    assert window.centralWidget().rect().contains(search.geometry())
    assert abs(search.geometry().center().x() - window.centralWidget().rect().center().x()) <= 1
