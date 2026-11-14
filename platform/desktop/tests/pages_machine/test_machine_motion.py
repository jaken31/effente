"""The Machine page with motion on: every move lands where the still page would be, and a page that
redraws with every record moves nothing unless something changed."""

from __future__ import annotations

import pytest
from app.pages.machine import MachinePage
from app.widgets import motion
from fake_partlog import DRIVE_ROLLER, IDLE_ROLLER, MOTOR_MOUNT
from PySide6.QtWidgets import QWidget

PAGE_SIZE = (1344, 900)
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
    p = MachinePage(can_draw=lambda: False)  # no 3D view: the card stands in for the model
    qtbot.addWidget(p)
    p.resize(*PAGE_SIZE)
    p.bind(state)
    p.show()
    qtbot.waitExposed(p)
    return p


def effects(root: QWidget) -> list:
    return [w for w in [root, *root.findChildren(QWidget)] if w.graphicsEffect() is not None]


def still(qtbot, page) -> None:
    def done() -> bool:
        reveals = page.findChildren(motion.Reveal)
        return not effects(page) and not any(r.moving() for r in reveals)

    qtbot.waitUntil(done, timeout=SETTLED)


def test_picking_a_part_settles_its_readings_in(qtbot, moving, page, state):
    state.log.select_part(DRIVE_ROLLER)
    col = page.part_column
    assert col.readings_shown()
    lifted = [w for w in (col.head, col.tiles, col.chart_panel, col.evidence_panel)
              if isinstance(w.graphicsEffect(), motion.Lift)]
    assert len(lifted) == 4 and lifted[-1].graphicsEffect().opacity == 0.0  # the last waits, clear
    still(qtbot, page)
    assert col.readings_shown() and not col.blank.isVisible()


def test_a_redraw_with_nothing_new_moves_nothing(qtbot, moving, page, state):
    state.log.select_part(DRIVE_ROLLER)
    still(qtbot, page)
    for _ in range(3):  # a record a second redraws the page
        page.refresh()
    assert not effects(page)


def test_the_head_stays_put_while_the_same_part_changes_what_it_shows(qtbot, moving, page, state):
    state.log.select_part(IDLE_ROLLER)  # never probed: its head over a blank
    still(qtbot, page)
    col = page.part_column
    col.show_readings(IDLE_ROLLER)
    assert col.head.graphicsEffect() is None and isinstance(col.tiles.graphicsEffect(), motion.Lift)
    still(qtbot, page)


def test_the_new_scan_card_settles_in_and_fades_out(qtbot, moving, page, state):
    state.log.select_part(DRIVE_ROLLER)
    still(qtbot, page)
    assert not page.scan_panel.isVisible()
    page.toggle_scan_panel()
    assert page.scan_panel.isVisible() and isinstance(page.scan_panel.graphicsEffect(), motion.Lift)
    still(qtbot, page)
    page.toggle_scan_panel()
    assert page.scan_panel.isVisible()  # on its way out
    qtbot.waitUntil(page.scan_panel.isHidden, timeout=SETTLED)
    page.refresh()
    assert page.scan_panel.isHidden() and not effects(page)


def test_the_entry_form_opens_and_closes_through_the_middle(qtbot, moving, page, state):
    state.log.select_part(DRIVE_ROLLER)
    still(qtbot, page)
    panel = page.log_panel
    seen = []
    panel.form_reveal._anim.valueChanged.connect(lambda _v: seen.append(panel.form_reveal.maximumHeight()))
    panel.add_button.click()
    assert panel.form_open() and panel.form.isVisible()
    still(qtbot, page)
    full = panel.form_reveal.height()
    assert full == panel.form.height() > 0 and any(0 < h < full for h in seen)
    panel.add_button.click()  # the same button puts it away, also while it is still closing
    assert not panel.form_open()
    panel.add_button.click()
    assert panel.form_open()
    still(qtbot, page)
    assert panel.form.isVisible()


def test_closing_the_form_with_its_question_open_draws_no_effect_inside_another(qtbot, moving, page, state):
    """Qt cannot draw one effect through another. The question row closes inside the closing form."""
    state.log.select_part(DRIVE_ROLLER)
    still(qtbot, page)
    form = page.log_panel.form
    page.log_panel.add_button.click()
    form.kind.button("finding").click()
    still(qtbot, page)
    assert form.needs.isVisible()
    form.cancel_button.click()
    inside = [w for w in effects(form) if w is not form]
    assert not inside
    still(qtbot, page)
    assert not form.isVisible()


def test_agreeing_with_a_review_opens_the_form_filled(qtbot, moving, page, state):
    state.log.select_part(DRIVE_ROLLER)
    still(qtbot, page)
    if not page.review_panel.agree_button.isVisible():
        pytest.skip("the sample has no review to agree with")
    page.review_panel.agree_button.click()
    still(qtbot, page)
    assert page.log_panel.form.isVisible() and page.log_panel.form.text.text()


def test_the_review_grows_to_its_new_height(qtbot, moving, page, state):
    area = page.review_panel.area
    short = area.height()
    state.log.select_part(DRIVE_ROLLER)
    assert area._height.moving()
    still(qtbot, page)
    qtbot.waitUntil(lambda: not area._height.moving(), timeout=SETTLED)
    assert area.height() > short and area.sizeHint().height() == area._natural()


def test_a_picked_row_tints_in_and_a_hovered_row_lights(qtbot, moving, page, state):
    state.log.select_part(DRIVE_ROLLER)
    still(qtbot, page)
    state.log.select_part(MOTOR_MOUNT)
    row = next(r for r in page.parts_panel.rows if r.row["part_id"] == MOTOR_MOUNT)
    assert row.row["selected"] and row.tint.chosen.at < 1.0
    qtbot.waitUntil(lambda: row.tint.chosen.at == 1.0, timeout=SETTLED)
    other = next(r for r in page.parts_panel.rows if r.row["part_id"] == DRIVE_ROLLER)
    assert not other.tint.layers()
    other.tint.hover.jump(1.0)
    assert len(other.tint.layers()) == 1 and not other.grab().isNull()


def test_with_motion_off_everything_is_there_at_once(qtbot, page, state):
    state.log.select_part(DRIVE_ROLLER)
    page.toggle_scan_panel()
    page.log_panel.add_button.click()
    assert not effects(page)
    assert page.scan_panel.isVisible() and page.log_panel.form.isVisible() and page.part_column.readings_shown()
    page.toggle_scan_panel()
    page.log_panel.add_button.click()
    assert page.scan_panel.isHidden() and not page.log_panel.form.isVisible()
