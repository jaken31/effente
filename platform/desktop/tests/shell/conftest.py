import os
import sys

import pytest

# Skip cleanly without PySide6, as the web logic tests do without node.
pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

HERE = os.path.dirname(os.path.abspath(__file__))
DESKTOP = os.path.dirname(os.path.dirname(HERE))
for path in (DESKTOP, HERE):
    if path not in sys.path:
        sys.path.insert(0, path)


@pytest.fixture(scope="session", autouse=True)
def _theme(qapp):
    from app.theme import apply_theme

    apply_theme(qapp)
    return qapp


@pytest.fixture
def state():
    from shell_stub_state import StubState

    return StubState()


@pytest.fixture
def window(qtbot, state):
    from app.shell import MainWindow

    w = MainWindow(state, placeholders=True, receiver_address="0.0.0.0:8000")
    qtbot.addWidget(w)
    w.resize(1600, 1000)
    w.show()
    qtbot.waitExposed(w)
    return w
