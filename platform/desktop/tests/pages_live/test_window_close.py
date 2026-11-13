"""Closing the window while a report run this app started is still going."""

import pytest
from app.shell.window import MainWindow
from fakes import FakeState
from PySide6.QtWidgets import QApplication


@pytest.fixture
def window(qtbot, monkeypatch):
    w = MainWindow(FakeState(with_sim=True))
    qtbot.addWidget(w)
    w.resize(1600, 1000)
    w.show()
    qtbot.waitExposed(w)
    w.asked = 0
    w.quits = 0
    monkeypatch.setattr(QApplication, "quit", lambda: setattr(w, "quits", w.quits + 1))
    return w


def answer(window, monkeypatch, choice):
    def ask():
        window.asked += 1
        return choice
    monkeypatch.setattr(window, "ask_about_agent", ask)


def test_closes_at_once_without_a_run(window, monkeypatch):
    answer(window, monkeypatch, MainWindow.CANCEL)
    assert window.close() is True
    assert window.asked == 0


def test_cancel_keeps_the_window_and_the_run(window, monkeypatch):
    window.state.own_agent_alive = True
    answer(window, monkeypatch, MainWindow.CANCEL)
    assert window.close() is False
    assert window.asked == 1 and window.isVisible()
    assert window.state.own_agent_alive and window.state.stopped_agents == 0
    assert window.quits == 0


def test_stop_ends_the_run_and_closes(window, monkeypatch):
    window.state.own_agent_alive = True
    answer(window, monkeypatch, MainWindow.STOP)
    assert window.close() is True
    assert window.state.stopped_agents == 1 and not window.state.own_agent_alive


def test_wait_hides_then_quits_when_the_run_ends(window, monkeypatch):
    state = window.state
    state.own_agent_alive = True
    answer(window, monkeypatch, MainWindow.WAIT)
    assert window.close() is False
    assert not window.isVisible()
    assert window.quits == 0 and state.stopped_agents == 0
    state.runs_changed.emit()  # a tool call arrives: still running
    assert window.quits == 0
    state.finish_agent()
    assert window.quits == 1
    assert window.close() is True and window.asked == 1  # no second question
