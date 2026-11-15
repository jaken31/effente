"""The Machine page as one screen (plan 15.1, row M1; tests listed in 15.4): the probe control, the
tiles, the chart and the projection's sentence, the evidence, the review and log columns, every
"simulated" mark of contract 9.6, and no page scrolling at 1440x820 and up."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from app.pages.machine import MachinePage
from app.pages.machine import partview as pv
from app.pages.machine import readings as rd
from app.pages.machine.charts import VelocityChart
from app.theme import tokens as t
from fake_partlog import BELT, DRIVE_ROLLER, IDLE_ROLLER, MOTOR_MOUNT
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QLabel, QWidget
from sample import no_normal, stamp_visit
from test_machine_page import FakeView, text_colours

NOW = datetime(2026, 11, 14, 18, 7, tzinfo=UTC)


@pytest.fixture
def state():
    from fakes import FakeState

    s = FakeState()
    s._now = NOW.timestamp() * 1000.0
    return s


@pytest.fixture
def page(qtbot, state):
    p = MachinePage(make_view=FakeView, can_draw=lambda: True)
    qtbot.addWidget(p)
    p.resize(1160, 716)  # the page area of a 1440x820 window
    p.bind(state)
    p.show()
    return p


def click(qtbot, widget):
    qtbot.mouseClick(widget, Qt.MouseButton.LeftButton)


def shown_text(root: QWidget) -> str:
    return " ".join(w.text() for w in root.findChildren(QLabel) if w.isVisibleTo(root))


# ---- the probe ---------------------------------------------------------------------------------------------


def test_selecting_a_part_never_moves_the_probe(page, state):
    for part_id in (DRIVE_ROLLER, BELT, MOTOR_MOUNT, None):
        state.log.select_part(part_id)
        assert state.log.probe_part() is None
    assert page.probe_text.text() == rd.NO_PROBE and not page.probe_clear.isVisible()


def test_probe_is_here_sets_the_pick_and_the_strip_says_so(qtbot, page, state):
    state.log.select_part(DRIVE_ROLLER)
    head = page.part_column.head
    assert head.probe_button.text() == "Probe is here" and head.probe_button.isEnabled()
    click(qtbot, head.probe_button)
    assert state.log.probe_part() == DRIVE_ROLLER
    since = rd.clock(state.log.probe()["since"])  # the fake stamps the pick at its own clock, as the receiver does
    assert page.probe_text.text() == f"Probe on: Drive roller, since {since}"
    assert head.probe_button.text() == "Probe on this part" and not head.probe_button.isEnabled()
    state.log.select_part(BELT)  # looking at another part leaves the probe where it is
    assert state.log.probe_part() == DRIVE_ROLLER and head.probe_button.isEnabled()
    assert page.probe_text.text() == f"Probe on: Drive roller, since {since}"
    click(qtbot, page.probe_clear)
    assert state.log.probe_part() is None and page.probe_text.text() == rd.NO_PROBE


def test_the_strip_shows_the_picks_time_when_the_store_gives_it(page, state):
    state.log.probe = lambda: {"part_id": DRIVE_ROLLER, "since": "2026-11-14T16:01:30Z"}
    state.log.set_probe_part(DRIVE_ROLLER)
    assert page.probe_text.text().startswith("Probe on: Drive roller, since ")
    assert page.probe_text.text().endswith(rd.clock("2026-11-14T16:01:30Z"))


def test_a_pick_the_platform_clears_is_followed(page, state):
    state.log.set_probe_part(BELT)
    state.log._probe = None
    state.log.probe_changed.emit()  # as the receiver's 5 minute rule does
    assert page.probe_text.text() == rd.NO_PROBE


def test_the_page_says_why_the_platform_cleared_the_pick(page, state):
    state.log.set_probe_part(BELT)
    state.log._probe, state.log._cleared = None, "device moved"
    state.log.probe_changed.emit()  # as the receiver does when the device says device_moved
    assert page.probe_text.text() == f"The pick was cleared: device moved. {rd.NO_PROBE}"
    state.log.set_probe_part(BELT)
    assert page.probe_text.text().startswith("Probe on: Belt")


# ---- the three states of the drawing -----------------------------------------------------------------------


def test_weeks_of_history(qtbot, page, state):
    state.log.select_part(DRIVE_ROLLER)
    col = page.part_column
    assert col.readings_shown() and not col.blank.isVisible()
    assert [tile.label.text() for tile in col.tiles.tiles] == ["Now, mm/s", "Average, mm/s", "Highest, mm/s",
                                                                "Change a day"]
    assert col.tiles.tiles[0].value.text() == "2.94"
    click(qtbot, col.chart_panel.range.button("all"))
    assert page.range == "all" and len(col.chart_panel.chart.points) == 20
    click(qtbot, col.chart_panel.range.button("7d"))
    assert len(col.chart_panel.chart.points) == 8  # 7 Nov to 14 Nov
    assert col.tiles.tiles[1].sub.text() == "8 readings"  # the summary follows the range


def test_a_first_day(page, state):
    state.log.select_part(MOTOR_MOUNT)
    col = page.part_column
    assert col.readings_shown() and len(col.chart_panel.chart.points) == 1
    assert col.chart_panel.sentence.text() == f"Simulated: {rd.NOT_ENOUGH}"
    assert (col.tiles.tiles[3].value.text(), col.tiles.tiles[3].sub.text()) == (rd.DASH, "not enough history")


def test_a_part_the_probe_has_never_been_on(qtbot, page, state):
    state.log.select_part(IDLE_ROLLER)
    col = page.part_column
    assert not col.readings_shown() and col.blank.isVisible()
    assert col.blank.title.text() == pv.NEVER_TITLE and col.head.isVisible()
    assert col.blank.button.text() == "Probe is here"
    click(qtbot, col.blank.button)
    assert state.log.probe_part() == IDLE_ROLLER and not col.blank.button.isVisible()


def test_no_part_picked(page, state):
    assert page.part_column.blank.title.text() == pv.PICK_TITLE and not page.part_column.head.isVisible()
    assert not page.log_panel.add_button.isEnabled() and not page.review_panel.run_button.isEnabled()
    state.log.clear()
    assert page.part_column.blank.text.text() == pv.NO_PARTS_TEXT


def test_readings_on_their_way(page, state):
    state.log.readings = lambda part_id, since=None: None  # the real store, while it fetches
    state.log.select_part(DRIVE_ROLLER)
    assert page.part_column.blank.title.text() == pv.FETCH_TITLE


# ---- the chart and the projection ---------------------------------------------------------------------------


def test_the_chart_is_in_mm_s_with_the_limit_line_and_nothing_from_the_projection(page, state):
    state.log.select_part(DRIVE_ROLLER)
    chart = page.part_column.chart_panel.chart
    reply = state.log.readings(DRIVE_ROLLER, rd.since(page.range, NOW))
    assert [p.velocity for p in chart.points] == [r["velocity_mm_s"] for r in reply["readings"]]
    assert chart.top >= rd.LIMIT_MM_S and rd.LIMIT_LABEL == "limit for any machine, 11 mm/s"
    projection = reply["projection"]
    drawn = {p.velocity for p in chart.points}
    assert projection["now_score"] not in drawn  # no score is put on the mm/s axis
    assert "alarm threshold" not in shown_text(page)


def test_the_projection_reads_from_the_data(page, state):
    state.log.select_part(DRIVE_ROLLER)
    panel = page.part_column.chart_panel
    assert panel.projection_title.text() == "Projection"
    assert panel.sentence.text() == ("Simulated: On this trend the model's score reaches its alarm line in 26 to 43 "
                                     "days (10 Dec to 27 Dec)")
    assert panel.under.text().endswith("not checked on real faults.")


def test_with_show_days_off_the_page_says_trend_and_shows_no_days_and_no_date(page, state):
    state.log.set_show_days(False)
    state.log.select_part(DRIVE_ROLLER)
    panel = page.part_column.chart_panel
    assert panel.projection_title.text() == "Trend"
    assert panel.sentence.text() == "Simulated: Rising toward the model's alarm line"
    for widget in (panel.sentence, panel.under, *[t_.sub for t_ in page.part_column.tiles.tiles]):
        assert not any(w in widget.text() for w in ("26", "43", "Dec", " days ("))


def test_no_projection_means_no_sentence(page, state):
    real = state.log.readings
    state.log.readings = lambda part_id, since=None: dict(real(part_id, since), projection=None)
    state.log.select_part(DRIVE_ROLLER)
    assert not page.part_column.chart_panel.projection.isVisibleTo(page)
    assert page.part_column.chart_panel.chart.points  # the points and the limit line stay


def test_repairs_are_marked_on_the_chart(page, state):
    state.log.select_part(BELT)
    assert [m.label for m in page.part_column.chart_panel.chart.marks] == ["repair, 14 Nov"]


# ---- every simulated mark of contract 9.6 ------------------------------------------------------------------


def test_every_panel_that_draws_a_simulated_reading_says_so(page, state):
    stamp_visit(state, DRIVE_ROLLER)
    state.log.select_part(DRIVE_ROLLER)
    col = page.part_column
    assert col.head.sim_chip.isVisible() and col.head.sim_chip.text() == "Simulated"  # the tiles
    assert col.chart_panel.sim_chip.isVisible()
    assert col.evidence_panel.sim_chip.isVisible()
    assert page.review_panel.sim_chip.isVisible()
    assert col.chart_panel.sentence.text().startswith("Simulated:")
    numbers = {r.row["part_id"]: r.number.text() for r in page.parts_panel.rows}
    assert numbers[DRIVE_ROLLER].endswith(" simulated") and numbers[IDLE_ROLLER] == rd.DASH


def test_real_readings_carry_no_mark(page, state):
    log = state.log
    for r in log._readings[BELT]:
        r["simulated"] = False
    log._projections[BELT]["simulated"] = False
    log._find(BELT)["reading"]["simulated"] = False
    log.select_part(BELT)
    col = page.part_column
    assert not col.head.sim_chip.isVisible() and not col.chart_panel.sim_chip.isVisible()
    assert not col.chart_panel.sentence.text().startswith("Simulated")
    assert next(r for r in page.parts_panel.rows if r.row["part_id"] == BELT).number.text() == "1.21"


def test_the_page_tells_the_shell_when_anything_it_draws_is_simulated(qtbot, page, state):
    """The top bar's Simulated data chip follows shows_simulated() (contract 9.6)."""
    log = state.log
    log.select_part(DRIVE_ROLLER)
    assert page.shows_simulated()  # the fake's readings are simulated
    for part_id, readings in log._readings.items():
        for r in readings:
            r["simulated"] = False
        if part_id in log._projections:
            log._projections[part_id]["simulated"] = False
    for part in log._parts:
        if part.get("reading"):
            part["reading"]["simulated"] = False
    for entry in log._entries:
        entry["simulated"] = False
    log._reviews = []
    with qtbot.waitSignal(page.simulated_changed):
        log.select_part(BELT)
    assert not page.shows_simulated()
    with qtbot.waitSignal(page.simulated_changed):
        stamp_visit(state, BELT)  # one simulated visit: the list, the tiles and the chart say so again
    assert page.shows_simulated()


def test_simulated_points_are_hollow_and_real_ones_filled_on_one_chart(qtbot):
    chart = VelocityChart()
    qtbot.addWidget(chart)
    chart.resize(400, 200)
    day = 86400.0
    points = [rd.Point(0.0, 4.0, True, False), rd.Point(day, 4.0, False, False)]
    chart.set_data(points, [], (-day, 2 * day))
    image = QImage(chart.size(), QImage.Format.Format_ARGB32)
    image.fill(QColor(t.PANEL))
    chart.render(image)
    hollow = image.pixelColor(round(chart.x(0.0)), round(chart.y(4.0)))
    filled = image.pixelColor(round(chart.x(day)), round(chart.y(4.0)))
    assert filled.lightness() > 180 and hollow.lightness() < 60


def test_a_chart_with_simulated_and_real_readings_marks_each(page, state):
    log = state.log
    log._readings[DRIVE_ROLLER][-1]["simulated"] = False
    log.select_part(DRIVE_ROLLER)
    flags = [p.simulated for p in page.part_column.chart_panel.chart.points]
    assert flags[-1] is False and all(flags[:-1])
    assert page.part_column.chart_panel.sim_chip.isVisible()


# ---- evidence ----------------------------------------------------------------------------------------------


def test_what_moved_comes_from_the_visits_newest_record(qtbot, page, state):
    stamp_visit(state, DRIVE_ROLLER)
    state.log.select_part(DRIVE_ROLLER)
    rows = page.part_column.evidence_panel.moved.rows
    newest = state.records()[-1]
    assert [(m.name, m.spreads) for m in rows] == list(newest["features"].items())
    assert rows[0].text == "Vibration 251-315 Hz"  # the announced text, with a capital


def test_no_visit_record_on_this_laptop_says_so(page, state):
    state.log.select_part(DRIVE_ROLLER)
    ev = page.part_column.evidence_panel
    assert ev.moved.rows == [] and "not on this laptop" in ev.moved.empty_text()
    assert not ev.sim_chip.isVisible()


def test_the_spectrum_is_in_log10_levels_against_the_parts_normal(qtbot, page, state):
    stamp_visit(state, DRIVE_ROLLER)
    state.log.select_part(DRIVE_ROLLER)
    ev = page.part_column.evidence_panel
    click(qtbot, ev.view.button("spectrum"))
    view = ev.spectrum.view
    assert list(view.bars) == state.records(spectrum=True)[-1]["spectrum"]
    assert len(view.normal) == 20 and view.note == "normal"


def test_a_part_whose_normal_is_not_ready_shows_the_machines_said_so(page, state):
    stamp_visit(state, BELT)
    state.log.select_part(BELT)
    assert page.part_column.evidence_panel.spectrum.view.note == "normal for the whole machine, not for this part"


def test_no_normal_line_while_the_normal_is_not_ready(page, state):
    no_normal(state)
    stamp_visit(state, DRIVE_ROLLER)
    state.log.select_part(DRIVE_ROLLER)
    view = page.part_column.evidence_panel.spectrum.view
    assert view.normal == () and view.note == "Normal not known yet: 120 of 300 normal records"


# ---- review and log ----------------------------------------------------------------------------------------


def test_run_review_runs_it_for_the_selected_part(qtbot, page, state):
    state.log.select_part(BELT)
    click(qtbot, page.review_panel.run_button)
    assert state.log.started_reviews == [(BELT, "standin")]
    assert page.review_panel.run_button.text() == rd.REVIEWING and not page.review_panel.run_button.isEnabled()
    state.log.finish_review()
    assert page.review_panel.run_button.isEnabled()


def test_agree_only_fills_the_form_and_the_person_adds_it(qtbot, page, state):
    state.log.select_part(DRIVE_ROLLER)
    before = len(state.log.entries())
    click(qtbot, page.review_panel.agree_button)
    form = page.log_panel.form
    assert form.isVisible() and form.kind.current() == "finding" and form.needs.current() == "yes"
    assert form.text.text().startswith("Stand-in review: possible unbalance")
    assert len(state.log.entries()) == before
    click(qtbot, form.add_button)
    newest = state.log.entries()[0]
    assert (newest["kind"], newest["part_id"], newest["needs_repair"]) == ("finding", DRIVE_ROLLER, True)
    assert not form.isVisible()


def test_add_entry_from_the_log_column(qtbot, page, state):
    state.log.select_part(BELT)
    click(qtbot, page.log_panel.add_button)
    form = page.log_panel.form
    assert form.isVisible() and not form.add_button.isEnabled()
    qtbot.keyClicks(form.text, "belt tension checked")
    qtbot.keyClick(form.text, Qt.Key.Key_Return)
    assert state.log.entries(BELT)[0]["text"] == "belt tension checked"
    assert page.log_panel.rows[0].text.text() == "belt tension checked"


def test_a_finding_must_say_whether_it_needs_repair(qtbot, page, state):
    state.log.select_part(BELT)
    click(qtbot, page.log_panel.add_button)
    form = page.log_panel.form
    click(qtbot, form.kind.button("finding"))
    qtbot.keyClicks(form.text, "worn edge")
    assert not form.add_button.isEnabled() and form.add_button.toolTip() == "Say whether it needs repair."


# ---- nothing_found, and red -------------------------------------------------------------------------------


def test_nothing_found_reads_nothing_found_everywhere_on_the_page(page, state):
    state.log.select_part(BELT)
    assert page.part_column.head.headline.text() == "Nothing found"
    words = shown_text(page).lower()
    for wrong in ("healthy", "good", " fine", " ok ", "no problem"):
        assert wrong not in words


@pytest.mark.parametrize("prepare", [
    lambda s: (stamp_visit(s, DRIVE_ROLLER), s.log.select_part(DRIVE_ROLLER), s.log.set_probe_part(DRIVE_ROLLER)),
    lambda s: s.log.select_part(IDLE_ROLLER),
    lambda s: (s.log.set_show_days(False), s.log.select_part(BELT)),
])
def test_no_text_on_the_one_screen_page_is_red(page, state, prepare):
    prepare(state)
    assert not any(t.is_red(colour) for colour in text_colours(page))


# ---- one screen ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("size", [(1440, 820), (1600, 1000), (1920, 1080)])
@pytest.mark.parametrize("part_id", [DRIVE_ROLLER, MOTOR_MOUNT, IDLE_ROLLER, None])
def test_the_page_does_not_scroll_at_1440x820_and_up(qtbot, state, size, part_id):
    from app.shell import MainWindow
    from app.shell.window import PageScroll

    w = MainWindow(state, receiver_address="0.0.0.0:8000")
    qtbot.addWidget(w)
    w.setFixedSize(*size)
    w.show()
    w.navigate("machine")
    p = w.pages["machine"]
    p._can_draw, p._make_view = (lambda: True), FakeView
    stamp_visit(state, DRIVE_ROLLER)
    state.log.select_part(part_id)
    qtbot.waitExposed(w)
    area = next(a for a in w.findChildren(PageScroll) if a.isVisible())
    qtbot.waitUntil(lambda: area.widget().height() <= area.viewport().height() + 1)
    assert area.verticalScrollBar().maximum() == 0 and area.horizontalScrollBar().maximum() == 0


# ---- labels fit where there is room --------------------------------------------------------------------------


def test_tile_numbers_and_times_are_never_cut_at_1440x820(qtbot, page):
    tiles = page.findChild(pv.Tiles)
    tiles.set_tiles([rd.Tile("Now, mm/s", "1.52", "13 Nov 05:18", True),
                     rd.Tile("Average, mm/s", "1.55", "30 readings", True),
                     rd.Tile("Highest, mm/s", "1.62", "28 Oct", True),
                     rd.Tile("Change a day", "-0.000604", "model score", True)])
    qtbot.waitUntil(lambda: tiles.tiles[0].value.width() > 0)
    for tile in tiles.tiles:
        assert tile.value.fits() and tile.sub.fits(), (tile.value.text(), tile.sub.text())


def test_a_tile_number_too_wide_for_its_tile_shrinks_instead_of_being_cut(qtbot):
    tile = pv.Tile()
    qtbot.addWidget(tile)
    tile.set_tile(rd.Tile("Change a day", "-0.000604", "model score", True))
    full = pv.QFontMetrics(tile.value.font()).horizontalAdvance("-0.000604")
    tile.value.resize(full - 20, 30)
    assert tile.value.fits()
    assert tile.value.fitted_font().pixelSize() < tile.value.font().pixelSize()


def test_what_moved_names_are_whole_when_the_bars_leave_room(qtbot):
    from app.pages.machine.charts import MovedChart

    chart = MovedChart()
    qtbot.addWidget(chart)
    chart.resize(440, 160)  # the evidence panel's chart at 1440x820
    rows = [rd.Moved("vib_kurtosis", "vibration impulsiveness", 1.73), rd.Moved("snd_level_db", "sound level", 1.1)]
    chart.set_rows(rows)
    assert [chart.name_shown(m) for m in rows] == ["vibration impulsiveness", "sound level"]
    names_w, values_w = chart.columns()
    assert chart.width() - 12 - names_w - values_w >= MovedChart.BAR_MIN


def test_the_newest_value_sits_by_the_newest_point(qtbot):
    chart = VelocityChart()
    qtbot.addWidget(chart)
    chart.resize(440, 260)
    day = 86400.0
    falling = [rd.Point(i * day, 3.0, True, False) for i in range(5)] + [rd.Point(5 * day, 1.3, True, True)]
    chart.set_data(falling, [], (0.0, 5 * day))
    box, x, y = chart.newest_label_rect(), chart.x(5 * day), chart.y(1.3)
    assert box.right() <= x and x - box.right() < 12  # just left of it
    assert box.top() >= y  # under it, away from the line coming down into it
    chart.set_data(falling[:-1] + [rd.Point(5 * day, 4.0, True, False)], [], (0.0, 5 * day))
    assert chart.newest_label_rect().bottom() <= chart.y(4.0)  # rising: above it
    for height in (120, 160, 260):  # falling near the axis: under, over or level, never on another point
        chart.resize(440, height)
        chart.set_data(falling, [], (0.0, 5 * day))
        box = chart.newest_label_rect()
        assert chart.plot().contains(box)
        assert not any(box.contains(QPointF(chart.x(p.at), chart.y(p.velocity))) for p in falling[:-1])


def test_a_name_too_long_for_one_line_takes_two(qtbot):
    from app.pages.machine.charts import MovedChart

    chart = MovedChart()
    qtbot.addWidget(chart)
    chart.resize(440, 160)
    long = rd.Moved("vib_velocity_mm_s", "vibration velocity, 10-250 Hz rms (ISO 10816-like, partial band)", 2.0)
    chart.set_rows([long])
    lines = chart.name_shown(long).split("\n")
    assert len(lines) == 2 and lines[0].startswith("vibration velocity")
    assert all(chart.fontMetrics().horizontalAdvance(line) <= chart.columns()[0] - 12 for line in lines)
