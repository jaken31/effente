"""The Setup page with motion on: every move lands where the still page would be."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from app.pages.setup import SetupPage, view
from app.pages.setup.steps import DeviceMark
from app.widgets import motion
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

PAGE_SIZE = (1320, 1400)
SETTLED = 3000  # ms to wait for any move here to finish


@pytest.fixture
def moving():
    motion.set_enabled(True)
    yield
    motion.set_enabled(None)


@pytest.fixture
def state():
    from fakes import FakeState

    s = FakeState()
    s.setup.clear()
    return s


@pytest.fixture
def page(qtbot, state):
    p = SetupPage()
    qtbot.addWidget(p)
    p.resize(*PAGE_SIZE)
    p.bind(state)
    p.show()
    qtbot.waitExposed(p)
    return p


def no_effects(root: QWidget) -> bool:
    return all(w.graphicsEffect() is None for w in [root, *root.findChildren(QWidget)])


def host_with_reveal(qtbot, text="Two lines of words that wrap inside a narrow host, so the height needs the width."):
    host = QWidget()
    lay = QVBoxLayout(host)
    child = QLabel(text)
    child.setWordWrap(True)
    reveal = motion.Reveal(child)
    lay.addWidget(reveal)
    lay.addStretch(1)
    qtbot.addWidget(host)
    host.resize(160, 400)
    host.show()
    qtbot.waitExposed(host)
    return host, reveal, child


# ---- the pieces -----------------------------------------------------------------


def test_reveal_opens_through_the_middle_and_lands_at_full_height(qtbot, moving):
    _host, reveal, child = host_with_reveal(qtbot)
    reveal.set_open(False)
    qtbot.waitUntil(lambda: not reveal.moving(), timeout=SETTLED)
    assert not child.isVisible()

    seen = []
    reveal._anim.valueChanged.connect(lambda _v: seen.append(reveal.maximumHeight()))
    reveal.set_open(True)
    assert reveal.moving() and child.isVisible()
    qtbot.waitUntil(lambda: not reveal.moving(), timeout=SETTLED)
    full = reveal.height()
    assert full >= child.heightForWidth(reveal.width()) > 0
    assert any(0 < h < full for h in seen)  # it grew, it did not pop
    assert reveal.maximumHeight() == motion._MAX and child.graphicsEffect() is None


def test_reveal_closes_and_hides(qtbot, moving):
    _host, reveal, child = host_with_reveal(qtbot)
    reveal.set_open(False)
    assert reveal.moving() and reveal.isVisible()  # still on the way
    qtbot.waitUntil(lambda: not reveal.moving(), timeout=SETTLED)
    assert not reveal.isVisible() and not child.isVisible()
    assert reveal.maximumHeight() == motion._MAX


def test_reveal_turned_back_halfway_ends_open(qtbot, moving):
    _host, reveal, child = host_with_reveal(qtbot)
    reveal.set_open(False)
    qtbot.wait(60)
    reveal.set_open(True)
    qtbot.waitUntil(lambda: not reveal.moving(), timeout=SETTLED)
    assert child.isVisible() and reveal.height() > 0


def test_reveal_jumps_with_motion_off(qtbot):
    _host, reveal, child = host_with_reveal(qtbot)
    reveal.set_open(False)
    assert not reveal.moving() and not child.isVisible()
    reveal.set_open(True)
    assert not reveal.moving() and child.isVisible()


def test_lift_paints_while_it_moves(qtbot, moving):
    host, _reveal, child = host_with_reveal(qtbot)
    motion.lift_in(child, ms=200)
    assert isinstance(child.graphicsEffect(), motion.Lift)
    qtbot.wait(60)
    assert not host.grab().isNull()  # draws through sourcePixmap mid-move
    qtbot.waitUntil(lambda: child.graphicsEffect() is None, timeout=SETTLED)


def test_sweep_runs_only_with_motion_on(qtbot, moving):
    host = QWidget()
    qtbot.addWidget(host)
    host.resize(300, 40)
    host.show()
    sweep = motion.Sweep(host, Qt.GlobalColor.red)
    sweep.set_running(True)
    assert sweep.running() and sweep.isVisible() and sweep.width() == 300
    sweep.set_running(False)
    assert not sweep.running() and not sweep.isVisible()
    motion.set_enabled(False)
    sweep.set_running(True)
    assert not sweep.running() and not sweep.isVisible()  # a still line would read as progress


def test_device_mark_turns_only_while_waiting(qtbot, moving):
    mark = DeviceMark()
    qtbot.addWidget(mark)
    mark.show()
    mark.set_status(view.WAITING)
    assert mark.spinning()
    for status in (view.TAKEN, view.DIFFERS, view.NO_DEVICE, view.NOTHING_SENT):
        mark.set_status(status)
        assert not mark.spinning() and mark.angle == 0.0
    mark.set_status(view.WAITING)
    mark.hide()
    assert not mark.spinning()


# ---- the page -----------------------------------------------------------------------


def test_the_steps_settle_in_when_the_page_opens(qtbot, moving, page):
    page.hide()
    page.show()
    assert all(isinstance(w.graphicsEffect(), motion.Lift) for w in page.steps)
    later = page.steps[-1].graphicsEffect()
    assert later.opacity == 0.0  # the last waits its turn, clear, never flashing first
    qtbot.waitUntil(lambda: no_effects(page), timeout=SETTLED)


def test_a_run_with_motion_on_ends_as_the_still_page_does(qtbot, moving, page, state):
    qtbot.waitUntil(lambda: no_effects(page), timeout=SETTLED)
    q = page.questions
    assert not q.result.isVisible()
    q.fill_button.click()
    assert q.result.isVisible() and q.sweep.running()
    qtbot.waitUntil(lambda: not q.result_reveal.moving(), timeout=SETTLED)
    state.setup.finish_run(ok=True)
    assert not q.sweep.running()
    qtbot.waitUntil(lambda: no_effects(page), timeout=SETTLED)
    assert q.result_title.text() == "What the stand-in understood" and q.standin_chip.isVisible()
    assert q.result_reveal.height() == q.result.height() >= q.result.heightForWidth(q.result.width())


def test_using_the_model_fades_the_button_out_once(qtbot, moving, page, state):
    qtbot.waitUntil(lambda: no_effects(page), timeout=SETTLED)
    page.questions.fill_button.click()
    state.setup.finish_run(ok=True)
    page.model.use_button.click()
    assert state.setup.profile() is not None
    qtbot.waitUntil(lambda: page.model.use_button.isHidden(), timeout=SETTLED)
    page.refresh()  # the page redraws often; a confirmed profile must not bring the button back
    assert page.model.use_button.isHidden()
