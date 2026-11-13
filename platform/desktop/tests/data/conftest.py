"""Puts platform/desktop and platform/desktop/tests on sys.path for the data layer's tests.

platform/ is not a package, so the desktop app is imported as `app` from its
own folder, and fakes.py as `fakes`.
"""

import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parents[1]
for path in (TESTS.parent, TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import pytest
from app.data import paths

SERVER_TESTS = paths.SERVER_DIR / "tests"
if str(SERVER_TESTS) not in sys.path:
    sys.path.insert(0, str(SERVER_TESTS))  # receiver_testkit: records and an HTTP client for the receiver


@pytest.fixture
def make_host(tmp_path):
    """Start a ReceiverHost on a free local port; all are stopped after the test."""
    pytest.importorskip("PySide6")
    from app.data.host import ReceiverHost

    hosts = []

    def make(data_dir=None, port=0, **kw):
        host = ReceiverHost(data_dir=data_dir or tmp_path / "data", port=port, host="127.0.0.1", **kw)
        host.start()
        hosts.append(host)
        return host

    yield make
    for host in hosts:
        host.stop()


@pytest.fixture
def make_state(qtbot):
    """A started DashboardState on a host, connected and loaded."""
    from app.data.state import DashboardState

    states = []

    def make(host, wait=True, **kw):
        state = DashboardState(host.url, host=host, **kw)
        states.append(state)
        state.start()
        if wait:
            qtbot.waitUntil(lambda: state.view()["platform_up"] and state.view()["snapshot_loaded"], timeout=5000)
        return state

    yield make
    for state in states:
        state.stop()
