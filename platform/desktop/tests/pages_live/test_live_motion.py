"""The Live page with motion on: the question under the status card opens and closes, and ends
where the still page would be."""

import pytest
from app.pages.live import LivePage
from app.widgets import motion
from fakes import OPEN_ALARM, FakeState
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget

SETTLED = 3000  # ms to wait for any move here to finish


@pytest.fixture
def moving():
    motion.set_enabled(True)
    yield
    motion.set_enabled(None)


@pytest.fixture
def state():
    return FakeState(with_sim=True)


@pytest.fixture
def live(qtbot, state):
    page = LivePage()
    qtbot.addWidget(page)
    page.resize(1320, 1400)
    page.bind(state)
    page.show()
    qtbot.waitExposed(page)
    return page


def effects(root: QWidget) -> list:
    return [w for w in [root, *root.findChildren(QWidget)] if w.graphicsEffect() is not None]


def still(qtbot, page) -> None:
    qtbot.waitUntil(lambda: not effects(page) and not any(r.moving() for r in page.findChildren(motion.Reveal)),
                    timeout=SETTLED)


def test_the_question_is_there_at_once_when_the_page_opens(qtbot, moving, live):
    assert live.status.asked_alarm() == OPEN_ALARM
    assert live.status.ask.isVisible() and not live.status.ask_reveal.moving()


def test_later_closes_the_question_through_the_middle(qtbot, moving, live):
    reveal = live.status.ask_reveal
    full = reveal.height()
    seen = []
    reveal._anim.valueChanged.connect(lambda _v: seen.append(reveal.maximumHeight()))
    qtbot.mouseClick(live.status.btn_later, Qt.MouseButton.LeftButton)
    assert live.status.asked_alarm() is None  # put away at once, as far as the page's logic goes
    assert reveal.moving() and live.status.ask.isVisible()  # still closing on screen
    still(qtbot, live)
    assert live.status.ask.isHidden() and any(0 < h < full for h in seen)


def test_a_redraw_with_nothing_new_moves_nothing(qtbot, moving, live, state):
    still(qtbot, live)
    for _ in range(3):
        live.status.show_state(state, state.records(), set())
    assert not effects(live) and not any(r.moving() for r in live.findChildren(motion.Reveal))


def test_a_change_on_its_way_opens_its_line(qtbot, moving, live, state):
    controls = live.controls
    assert controls.pending.isHidden()
    controls.show_sensitivity(0.5, 0.8)
    assert controls.pending.isVisible() and controls.pending_reveal.moving()
    still(qtbot, live)
    controls.show_sensitivity(0.8, 0.8)
    still(qtbot, live)
    assert controls.pending.isHidden()
