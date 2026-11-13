"""The window opens where the last run closed: size, page and machine."""

import pytest
from app.shell import MainWindow
from PySide6.QtCore import QSettings
from shell_stub_state import StubState


@pytest.fixture
def store(tmp_path):
    return QSettings(str(tmp_path / "effente.ini"), QSettings.Format.IniFormat)


def opened(qtbot, store, state=None):
    w = MainWindow(state or StubState(), placeholders=True, settings=store)
    qtbot.addWidget(w)
    w.show()
    qtbot.waitExposed(w)
    return w


def test_next_run_opens_where_this_one_closed(qtbot, store):
    first = opened(qtbot, store)
    first.resize(1100, 700)  # the test screen is 800 wide, so only the height comes back
    first.navigate("history")
    first.sidebar.machine.setCurrentIndex(1)
    first.close()

    state = StubState()
    second = opened(qtbot, store, state)
    assert second.height() == 700
    assert second.current == "history"
    assert state.current_machine() == "conveyor-08"
    assert second.sidebar.machine.currentText() == "conveyor-08"


def test_first_run_opens_on_live_at_the_default_size(qtbot, store):
    w = opened(qtbot, store)
    assert w.current == "live"
    assert (w.width(), w.height()) == (1440, 900)


def test_a_page_that_no_longer_exists_opens_live(qtbot, store):
    store.setValue("window/page", "gone")
    assert opened(qtbot, store).current == "live"


def test_a_machine_that_is_gone_is_dropped_not_waited_for(qtbot, store):
    store.setValue("window/machine", "press-99")
    state = StubState()
    w = opened(qtbot, store, state)
    assert state.machine_calls == []
    # It turns up later: the window stays on the machine it is showing.
    state.machines = lambda: ["demo-fan-1", "press-99"]
    w.refresh()
    assert state.machine_calls == []


def test_the_machine_waits_for_the_first_machine_list(qtbot, store):
    store.setValue("window/machine", "conveyor-08")
    state = StubState()
    known = state.machines()
    state.machines = list
    w = opened(qtbot, store, state)
    assert state.machine_calls == []
    state.machines = lambda: known
    state.snapshot_changed.emit()
    assert state.machine_calls == ["conveyor-08"]
    assert w.topbar.machine.text() == "conveyor-08"


def test_nothing_is_remembered_without_a_store(qtbot):
    w = MainWindow(StubState(), placeholders=True)
    qtbot.addWidget(w)
    w.navigate("history")
    w.close()
    assert w.settings is None
