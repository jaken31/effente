"""The Device page's reset button: asks first, then closes the window with
`reset_wanted` set, and main.py starts the app again with `--fresh`."""

import sys

import pytest
from app.pages.device import DevicePage
from app.shell import MainWindow
from shell_stub_state import StubState


def opened(qtbot, state=None, resettable=True):
    w = MainWindow(state or StubState(), placeholders=True, pages={"device": DevicePage()},
                   resettable=resettable)
    qtbot.addWidget(w)
    w.show()
    qtbot.waitExposed(w)
    w.navigate("device")
    return w


def test_the_button_is_only_there_when_the_window_can_reset(qtbot):
    assert opened(qtbot).pages["device"].reset_button.isVisible()
    assert not opened(qtbot, resettable=False).pages["device"].reset_button.isVisible()


def test_cancel_changes_nothing(qtbot):
    w = opened(qtbot)
    w.confirm_reset = lambda running=False: False
    w.pages["device"].reset_button.click()
    assert not w.reset_wanted
    assert w.isVisible()


def test_confirming_closes_the_window_with_reset_wanted(qtbot):
    w = opened(qtbot)
    w.confirm_reset = lambda running=False: True
    w.pages["device"].reset_button.click()
    assert w.reset_wanted
    assert not w.isVisible()


def test_a_running_report_is_named_and_stopped_not_asked_about_twice(qtbot):
    state = StubState()
    stopped, told = [], []
    state.own_agent_running = lambda: True
    state.stop_agent = lambda: stopped.append(True)
    w = opened(qtbot, state)
    w.confirm_reset = lambda running=False: told.append(running) or True
    w.ask_about_agent = lambda: pytest.fail("the close question was asked as well")
    w.pages["device"].reset_button.click()
    assert told == [True]
    assert stopped == [True]
    assert w.reset_wanted and not w.isVisible()


def test_restart_starts_again_with_fresh_and_drops_the_scan_cache(monkeypatch, tmp_path):
    import main
    from app.data import scans

    cache = tmp_path / "scans"
    (cache / "scan-1").mkdir(parents=True)
    monkeypatch.setattr(scans, "default_cache_dir", lambda: cache)
    calls = []
    monkeypatch.setattr(main.os, "execv", lambda exe, argv: calls.append((exe, argv)))
    main.restart_fresh(["--no-sim", "--fresh", "--port", "8123"])
    assert calls == [(sys.executable, [sys.executable, str(main.HERE / "main.py"),
                                       "--no-sim", "--port", "8123", "--fresh"])]
    assert not cache.exists()
