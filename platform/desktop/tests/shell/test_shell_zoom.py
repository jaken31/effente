"""The size shortcuts: Ctrl with + and - and 0. A new size is kept, and a moment
after the last key the window closes with `restart_wanted` set, so main.py
starts the app again at that size (app/zoom.py)."""

import sys

import pytest
from app import zoom
from app.shell import MainWindow
from PySide6.QtCore import QSettings, Qt


@pytest.fixture
def store(tmp_path):
    return QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)


@pytest.fixture
def window(qtbot, state, store):
    w = MainWindow(state, placeholders=True, settings=store)
    qtbot.addWidget(w)
    w.show()
    qtbot.waitExposed(w)
    w.activateWindow()
    qtbot.waitActive(w)
    w._zoom_limit = lambda: zoom.STEPS[-1]  # the offscreen screen is smaller than the smallest window
    return w


def test_steps_stay_inside_the_list_and_under_the_limit():
    assert zoom.stepped(1.0, 1) == 1.1
    assert zoom.stepped(1.0, -1) == 0.9
    assert zoom.stepped(zoom.STEPS[-1], 1) == zoom.STEPS[-1]
    assert zoom.stepped(zoom.STEPS[0], -1) == zoom.STEPS[0]
    assert zoom.stepped(1.1, 1, limit=1.2) == 1.1
    assert zoom.stepped(1.0, 1, limit=0.8) == 1.0  # a small screen never forces it below normal
    assert zoom.nearest("1.24") == 1.25
    assert zoom.nearest("nonsense") == zoom.DEFAULT


def test_apply_multiplies_a_scale_factor_set_outside(monkeypatch):
    monkeypatch.setattr(zoom, "_current", zoom.DEFAULT)
    monkeypatch.delenv(zoom.BASE_ENV, raising=False)
    monkeypatch.setenv("QT_SCALE_FACTOR", "2")
    assert zoom.apply(1.25) == zoom.current() == 1.25
    assert float(sys.modules["os"].environ["QT_SCALE_FACTOR"]) == 2.5
    zoom.apply(1.0)  # as after a restart: the outside factor is not multiplied twice
    assert float(sys.modules["os"].environ["QT_SCALE_FACTOR"]) == 2.0


@pytest.mark.parametrize(("key", "modifiers", "size"), [
    (Qt.Key.Key_Equal, Qt.KeyboardModifier.ControlModifier, 1.1),
    (Qt.Key.Key_Plus, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier, 1.1),
    (Qt.Key.Key_Minus, Qt.KeyboardModifier.ControlModifier, 0.9),
])
def test_a_size_key_keeps_the_size_and_closes_to_restart(window, store, qtbot, key, modifiers, size):
    qtbot.keyClick(window, key, modifiers)
    assert float(store.value(zoom.KEY)) == size
    assert window.isVisible() and not window.restart_wanted  # waiting for more presses
    qtbot.waitUntil(lambda: window.restart_wanted, timeout=3000)
    assert not window.isVisible()
    assert store.value("window/zoom_rect")


def test_several_presses_restart_once_at_the_last_size(window, store, qtbot):
    for _ in range(3):
        qtbot.keyClick(window, Qt.Key.Key_Equal, Qt.KeyboardModifier.ControlModifier)
    assert float(store.value(zoom.KEY)) == 1.5
    assert len(window.notices.notices()) == 1  # one card for the size, not three
    qtbot.waitUntil(lambda: window.restart_wanted, timeout=3000)


def test_back_to_the_size_already_shown_does_not_restart(window, store, qtbot):
    qtbot.keyClick(window, Qt.Key.Key_Equal, Qt.KeyboardModifier.ControlModifier)
    qtbot.keyClick(window, Qt.Key.Key_0, Qt.KeyboardModifier.ControlModifier)
    assert float(store.value(zoom.KEY)) == 1.0
    qtbot.wait(900)
    assert window.isVisible() and not window.restart_wanted


def test_no_larger_than_the_screen_has_room_for(window, store, qtbot):
    window._zoom_limit = lambda: 1.0
    window.zoom_in()
    qtbot.wait(900)
    assert store.value(zoom.KEY) is None
    assert window.isVisible() and not window.restart_wanted
    assert "no room" in window.notices.notices()[-1].detail_label.text()


def test_a_running_report_is_not_cut_off(window, state, store, qtbot):
    state.own_agent_running = lambda: True
    window.ask_about_agent = lambda: pytest.fail("the size keys never ask to close")
    window.zoom_in()
    qtbot.waitUntil(lambda: "report is running" in window.notices.notices()[-1].detail_label.text(), timeout=3000)
    assert window.isVisible() and not window.restart_wanted
    assert float(store.value(zoom.KEY)) == 1.1  # kept for the next start
    state.own_agent_running = lambda: False  # so closing the window after the test does not ask


def test_the_window_comes_back_where_it_was(qtbot, state, store, monkeypatch):
    store.setValue("window/zoom_rect", "100,50,1500,1050")  # screen pixels, left by the run before
    monkeypatch.setattr(zoom, "_current", 1.5)
    w = MainWindow(state, placeholders=True, settings=store)
    qtbot.addWidget(w)
    assert (w.width(), w.height()) == (1000, 700)
    assert store.value("window/zoom_rect") is None


def test_restart_starts_again_with_the_same_arguments(monkeypatch):
    import main

    calls = []
    monkeypatch.setattr(main.os, "execv", lambda exe, argv: calls.append((exe, argv)))
    main.restart(["--no-sim", "--port", "8123"])
    assert calls == [(sys.executable, [sys.executable, str(main.HERE / "main.py"), "--no-sim", "--port", "8123"])]
