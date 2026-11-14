"""The Log page with motion on: every move lands where the still page would be."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from app.pages.log import LogPage, view
from app.widgets import motion
from fake_partlog import DRIVE_ROLLER, MOTOR_MOUNT
from PySide6.QtWidgets import QWidget

SETTLED = 3000  # ms to wait for any move here to finish


@pytest.fixture
def moving():
    motion.set_enabled(True)
    yield
    motion.set_enabled(None)


@pytest.fixture
def state():
    from fakes import FakeState

    return FakeState()


@pytest.fixture
def page(qtbot, state):
    p = LogPage()
    qtbot.addWidget(p)
    p.resize(1320, 900)
    p.bind(state)
    p.show()
    qtbot.waitExposed(p)
    return p


def effects(root: QWidget) -> list:
    return [w for w in [root, *root.findChildren(QWidget)] if w.graphicsEffect() is not None]


def still(qtbot, page) -> None:
    qtbot.waitUntil(lambda: not effects(page) and not any(r.moving() for r in page.findChildren(motion.Reveal)),
                    timeout=SETTLED)


def test_picking_a_part_brings_its_log_up_and_tints_its_row(qtbot, moving, page, state):
    state.log.select_part(DRIVE_ROLLER)
    row = next(r for r in page.parts_panel.rows if r.row.part_id == DRIVE_ROLLER)
    assert row.tint.chosen.at < 1.0
    assert isinstance((page.table if page.table.isVisible() else page.empty).graphicsEffect(), motion.Lift)
    still(qtbot, page)
    assert row.tint.chosen.at == 1.0


def test_a_redraw_with_nothing_new_moves_nothing(qtbot, moving, page, state):
    state.log.select_part(DRIVE_ROLLER)
    still(qtbot, page)
    for _ in range(3):
        page.refresh()
    assert not effects(page)


def test_the_add_part_form_opens_and_closes(qtbot, moving, page, state):
    panel = page.parts_panel
    assert not panel.form.isVisible()
    panel.add_button.click()
    assert panel.form.isVisible() and panel.form_reveal.moving()
    still(qtbot, page)
    assert panel.form_reveal.height() == panel.form.height() > 0
    panel.add_button.click()
    still(qtbot, page)
    assert not panel.form.isVisible()


def test_the_needs_repair_question_fades_in_and_out(qtbot, moving, page, state):
    state.log.select_part(MOTOR_MOUNT)
    still(qtbot, page)
    compose = page.compose
    assert not compose.needs.isVisible()
    compose.kind.button("finding").click()
    assert compose.needs.isVisible()
    still(qtbot, page)
    compose.kind.button(view.DEFAULT_KIND).click()
    qtbot.waitUntil(compose.needs.isHidden, timeout=SETTLED)
    page.refresh()
    assert compose.needs.isHidden()


def test_answering_an_alarm_opens_the_form_and_cancel_puts_it_away(qtbot, moving, page, state):
    panel = page.alarms_panel
    if not panel.rows:
        pytest.skip("the sample has no alarm waiting")
    panel.rows[0].button.click()
    assert panel.form.isVisible() and panel.form_reveal.moving()
    still(qtbot, page)
    assert panel.problem.isVisible()  # nothing chosen yet: it says what is missing
    panel.cancel.click()
    still(qtbot, page)
    assert not panel.form.isVisible()
