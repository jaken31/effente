"""Keyboard shortcuts beyond the page keys, and the card that lists them."""

import pytest
from app.shell import MainWindow, shortcuts
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QWidget


class _Page(QWidget):
    """A page with the two methods the window's shortcuts look for."""

    navigate = Signal(str)
    title = "Stub"
    subtitle = ""

    def __init__(self):
        super().__init__()
        self.calls: list[str] = []

    def bind(self, state) -> None:
        return None

    def export_dialog(self) -> None:
        self.calls.append("export")

    def run_agent(self) -> None:
        self.calls.append("run")


@pytest.fixture
def pages():
    return {"live": _Page(), "agent": _Page()}


@pytest.fixture
def window(qtbot, state, pages):
    w = MainWindow(state, pages=pages, placeholders=True)
    qtbot.addWidget(w)
    w.show()
    qtbot.waitExposed(w)
    w.activateWindow()
    qtbot.waitActive(w)
    return w


def test_escape_leaves_spectrum_and_thermal_for_live(window, qtbot):
    for key in ("spectrum", "thermal"):
        window.navigate(key)
        qtbot.keyClick(window, Qt.Key.Key_Escape)
        assert window.current == "live"
    window.navigate("history")
    qtbot.keyClick(window, Qt.Key.Key_Escape)
    assert window.current == "history"


def test_brackets_step_through_machines_and_wrap(window, state, qtbot):
    qtbot.keyClick(window, Qt.Key.Key_BracketRight)
    assert state.current_machine() == "conveyor-08"
    assert window.sidebar.machine.currentText() == "conveyor-08"
    qtbot.keyClick(window, Qt.Key.Key_BracketRight)
    assert state.current_machine() == "demo-fan-1"
    qtbot.keyClick(window, Qt.Key.Key_BracketLeft)
    assert state.current_machine() == "conveyor-08"


def test_one_machine_stays_put(window, state, qtbot):
    state.machines = lambda: [state.machine]
    qtbot.keyClick(window, Qt.Key.Key_BracketRight)
    assert state.machine_calls == []


def test_ctrl_r_runs_the_report_from_any_page(window, pages, qtbot):
    window.navigate("history")
    qtbot.keyClick(window, Qt.Key.Key_R, Qt.KeyboardModifier.ControlModifier)
    assert pages["agent"].calls == ["run"]
    assert window.current == "history"


def test_ctrl_e_exports_the_page_shown_if_it_can(window, pages, qtbot):
    qtbot.keyClick(window, Qt.Key.Key_E, Qt.KeyboardModifier.ControlModifier)
    assert pages["live"].calls == ["export"]
    window.navigate("evidence")  # a page with nothing to export
    qtbot.keyClick(window, Qt.Key.Key_E, Qt.KeyboardModifier.ControlModifier)
    assert pages["live"].calls == ["export"]


def test_f11_toggles_full_screen(window, qtbot):
    qtbot.keyClick(window, Qt.Key.Key_F11)
    assert window.isFullScreen()
    qtbot.keyClick(window, Qt.Key.Key_F11)
    assert not window.isFullScreen()


@pytest.mark.parametrize(("key", "modifier"), [
    (Qt.Key.Key_Question, Qt.KeyboardModifier.NoModifier),
    (Qt.Key.Key_Question, Qt.KeyboardModifier.ShiftModifier),
    (Qt.Key.Key_F1, Qt.KeyboardModifier.NoModifier),
])
def test_question_mark_shows_the_list_and_escape_hides_it(window, qtbot, key, modifier):
    card = window.shortcut_help
    assert not card.isVisible()
    qtbot.keyClick(window, key, modifier)
    assert card.isVisible()
    # In the middle of the window, and inside it.
    assert abs(card.geometry().center().x() - window.centralWidget().rect().center().x()) <= 1
    assert window.centralWidget().rect().contains(card.geometry())
    window.navigate("thermal")
    qtbot.keyClick(window, Qt.Key.Key_Escape)
    assert not card.isVisible()
    assert window.current == "thermal"  # the first Esc only hid the list


def test_the_list_shows_every_key_that_works(window):
    shown = [keys for keys, _ in window.shortcut_help.rows]
    assert shown == ["1 to 7", *(a[1] for a in shortcuts.ACTIONS)]
    for _, _, _, method in shortcuts.ACTIONS:
        assert callable(getattr(window, method))


def test_the_list_fits_the_smallest_window(window, qtbot):
    window.resize(960, 660)
    qtbot.waitUntil(lambda: window.width() == 960)
    window.toggle_shortcut_help()
    assert window.centralWidget().rect().contains(window.shortcut_help.geometry())


def test_real_pages_have_the_methods_the_shortcuts_call():
    from app.pages.agents.page import AgentsPage
    from app.pages.history.page import HistoryPage
    from app.pages.live.page import LivePage

    assert callable(LivePage.export_dialog)
    assert callable(HistoryPage.export_dialog)
    assert callable(AgentsPage.run_agent)
