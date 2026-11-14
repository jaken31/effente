"""The window opens the Setup page by itself for a machine with no machine profile,
once a run, and only after the platform has said which machines have one."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from app.shell import MainWindow


def window(qtbot, state):
    w = MainWindow(state, placeholders=True)
    qtbot.addWidget(w)
    w.show()
    return w


@pytest.fixture
def state():
    from fakes import FakeState

    return FakeState()


def test_a_machine_with_no_profile_opens_setup(qtbot, state):
    state.setup.clear()
    assert window(qtbot, state).current == "setup"


def test_a_machine_with_a_profile_stays_where_it_was(qtbot, state):
    assert window(qtbot, state).current == "live"


def test_only_once_a_run(qtbot, state):
    state.setup.clear()
    w = window(qtbot, state)
    w.navigate("live")
    w.refresh()
    state.setup.profile_changed.emit()
    assert w.current == "live"


def test_not_before_the_platform_has_said(qtbot, state, monkeypatch):
    state.setup.clear()
    monkeypatch.setattr(state.setup, "profile_known", lambda: False)
    w = window(qtbot, state)
    assert w.current == "live"
    monkeypatch.setattr(state.setup, "profile_known", lambda: True)
    state.setup.profile_changed.emit()  # the first snapshot with `profiles` arrives
    assert w.current == "setup"


def test_another_machine_with_no_profile_opens_it_again(qtbot, state):
    w = window(qtbot, state)
    assert w.current == "live"
    machine = state.current_machine()
    state.current_machine = lambda: "another-machine"  # FakeSetup has no profile for it
    w.refresh()
    assert w.current == "setup"
    w.navigate("live")
    state.current_machine = lambda: machine
    w.refresh()
    assert w.current == "live"


def test_with_the_real_pages_it_leaves_the_machine_page_alone_once_shown(qtbot, state):
    """The Machine page and the Setup page sit side by side in the stack: after the
    one opening, going to the Machine page and back again keeps it there."""
    state.setup.clear()
    w = MainWindow(state)
    qtbot.addWidget(w)
    w.show()
    assert w.current == "setup"
    w.navigate("machine")
    w.refresh()
    state.setup.profile_changed.emit()
    assert w.current == "machine"


def test_an_alarm_notice_still_lands_on_history(qtbot, state):
    """"Answer in History" picks the alarm's machine; one with no profile does not take the click."""
    w = window(qtbot, state)
    state.current_machine = lambda: "another-machine"
    w._open_alarm("another-machine")
    assert w.current == "history"
