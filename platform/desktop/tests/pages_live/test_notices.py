"""The window's notices: failed actions and new alarms, against FakeState."""

import copy

import pytest
from app.data import logic
from app.shell import notices as notices_mod
from app.shell.window import MainWindow
from fakes import OPEN_ALARM, FakeState
from PySide6.QtCore import Qt


@pytest.fixture
def window(qtbot):
    state = FakeState(with_sim=True)
    w = MainWindow(state)
    qtbot.addWidget(w)
    w.resize(1600, 1000)
    w.show()
    qtbot.waitExposed(w)
    return w


def shown(w):
    return [(n.kind, n.title_label.text(), n.detail_label.text()) for n in w.notices.notices()]


def new_alarm(state, alarm_id="alarm-new", **over):
    alarm = copy.deepcopy(state.view()["alarms"][OPEN_ALARM])
    alarm.update({"alarm_id": alarm_id, "answer": None, "answers": [], "open": True, **over})
    return alarm


def test_nothing_shows_at_start(window):
    assert shown(window) == []
    assert not window.notices.isVisible()


def test_failed_action_shows_and_goes_away(qtbot, window, monkeypatch):
    monkeypatch.setattr(notices_mod, "FAILURE_MS", 50)
    window.state.action_failed.emit("Answer not saved: receiver did not answer")
    assert shown(window) == [("failure", "Answer not saved", "receiver did not answer")]
    assert window.notices.isVisible()
    card = window.notices.notices()[0].geometry().translated(window.notices.pos())
    assert window.centralWidget().rect().contains(card)  # inside the window, bottom right
    qtbot.waitUntil(lambda: shown(window) == [], timeout=2000)
    assert not window.notices.isVisible()


def test_new_alarm_asks_and_opens_history(qtbot, window):
    state = window.state
    alarm = new_alarm(state)
    state.push("alarm", alarm)
    assert len(shown(window)) == 1
    kind, title, detail = shown(window)[0]
    assert kind == "alarm" and title == f"New alarm on {alarm['machine_id']}"
    assert logic.diagnosis_text(state.model(), alarm["diagnosis"], alarm.get("model_version")) in detail
    assert "Real problem or false alarm?" in detail
    window.navigate("live")
    qtbot.mouseClick(window.notices.notices()[0].action_button, Qt.MouseButton.LeftButton)
    assert window.current == "history"
    assert state.current_machine() == alarm["machine_id"]
    qtbot.waitUntil(lambda: shown(window) == [], timeout=1000)


def test_alarm_stays_until_closed(qtbot, window, monkeypatch):
    monkeypatch.setattr(notices_mod, "FAILURE_MS", 50)
    window.state.push("alarm", new_alarm(window.state))
    qtbot.wait(150)
    assert len(shown(window)) == 1  # alarms do not time out
    qtbot.mouseClick(window.notices.notices()[0].close_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: shown(window) == [], timeout=1000)


def test_only_new_open_alarms_notify(window):
    state = window.state
    known = copy.deepcopy(state.view()["alarms"][OPEN_ALARM])
    known["periods"] = (known.get("periods") or 0) + 1
    state.push("alarm", known)  # an update to an alarm already there
    state.push("alarm", new_alarm(state, "alarm-closed", open=False))  # opened and closed unseen
    assert shown(window) == []
    state.push("alarm", new_alarm(state, "alarm-a"))
    state.push("alarm", dict(new_alarm(state, "alarm-a"), periods=9))  # the same alarm again
    assert len(shown(window)) == 1


def test_stack_keeps_the_newest(window):
    for i in range(notices_mod.MAX_SHOWN + 2):
        window.state.action_failed.emit(f"Failure {i}: x")
    titles = [title for _k, title, _d in shown(window)]
    assert titles == [f"Failure {i}" for i in range(2, notices_mod.MAX_SHOWN + 2)]
