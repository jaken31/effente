"""Live and History pages against FakeState."""

import copy
import re

import pytest
from app.data import logic
from app.pages.history import HistoryPage
from app.pages.live import LivePage
from app.theme import tokens as t
from app.widgets import FlagValue
from fakes import ANSWERED_ALARM, OPEN_ALARM, FakeState
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QLabel, QWidget


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


@pytest.fixture
def history(qtbot, state):
    page = HistoryPage()
    qtbot.addWidget(page)
    page.resize(1320, 1200)
    page.bind(state)
    page.show()
    qtbot.waitExposed(page)
    return page


def plain(html: str) -> str:
    return re.sub(r"<[^>]+>", "", html)


def click(qtbot, widget):
    qtbot.mouseClick(widget, Qt.MouseButton.LeftButton, pos=widget.rect().center())


# ---- answering ---------------------------------------------------------------

def test_live_answer_calls_answer_alarm(qtbot, live, state, monkeypatch):
    calls = []
    real = state.answer_alarm
    monkeypatch.setattr(state, "answer_alarm", lambda a, v, finding=None: calls.append((a, v)) or real(a, v))
    assert live.status.asked_alarm() == OPEN_ALARM
    click(qtbot, live.status.btn_false)
    assert calls == [(OPEN_ALARM, "false")]
    # Answered: the question goes away, the event list shows the answer.
    assert live.status.ask.isHidden()
    assert any(e["kind"] == "Answer" and e["text"].startswith("false alarm") for e in live.events_list())


def test_live_later_sends_nothing(qtbot, live, state, monkeypatch):
    calls = []
    monkeypatch.setattr(state, "answer_alarm", lambda *a, **k: calls.append(a))
    click(qtbot, live.status.btn_later)
    assert calls == []
    assert live.status.ask.isHidden()


def test_history_answer_calls_answer_alarm(qtbot, history, state, monkeypatch):
    calls = []
    real = state.answer_alarm
    monkeypatch.setattr(state, "answer_alarm", lambda a, v, finding=None: calls.append((a, v)) or real(a, v))
    cells = history.answer_cells()
    assert [c.alarm_id for c in cells] == [OPEN_ALARM]  # alarm-5 is already answered
    click(qtbot, cells[0].btn_real)
    assert calls == [(OPEN_ALARM, "real")]
    assert history.answer_cells() == []
    pills = [history.alarms_model.index(r, 6).data(history.alarms_table.PillRole)
             for r in range(history.alarms_model.rowCount())]
    assert pills == [(logic.verdict_text("real"), t.WARN)] * 2


def test_history_shows_recorded_answer(history):
    row = [a["alarm_id"] for a in history.state.alarms()].index(ANSWERED_ALARM)
    pill = history.alarms_model.index(row, 6).data(history.alarms_table.PillRole)
    assert pill[0] == logic.verdict_text("real")


# ---- sensitivity ---------------------------------------------------------------

def test_slider_sets_sensitivity_and_updates_impact(qtbot, live, state, monkeypatch):
    calls = []
    real = state.set_sensitivity
    monkeypatch.setattr(state, "set_sensitivity", lambda v: calls.append(v) or real(v))
    before = live.controls.impact.text()
    slider = live.controls.slider
    x = slider._x(80)
    qtbot.mousePress(slider, Qt.MouseButton.LeftButton, pos=QPoint(int(slider._x(50)), slider.height() // 2))
    qtbot.mouseMove(slider, QPoint(int(x), slider.height() // 2))
    # Dragging previews the margins without setting anything yet.
    assert calls == []
    m = logic.margins(slider.value() / 100)
    assert f"+{m['temperature']:.1f} °C" in live.controls.impact.text()
    qtbot.mouseRelease(slider, Qt.MouseButton.LeftButton, pos=QPoint(int(x), slider.height() // 2))
    assert len(calls) == 1 and calls[0] == pytest.approx(slider.value() / 100)
    after = plain(live.controls.impact.text())
    assert after != before
    assert f"model line {logic.alarm_line(calls[0]):.2f}" in after
    # The device still reports 0.5: the page says a change is on its way, not that it applied.
    assert state.sensitivity() == {"reported": 0.5, "wanted": calls[0]}
    assert not live.controls.pending.isHidden()
    assert "on its way" in live.controls.pending.text()
    assert live.status.tile_sensitivity.value.text() == logic.sensitivity_name(0.5)


def test_preset_sets_sensitivity(qtbot, live, state, monkeypatch):
    calls = []
    monkeypatch.setattr(state, "set_sensitivity", lambda v: calls.append(v))
    click(qtbot, live.controls.presets.button("0.75"))
    assert calls == [0.75]
    assert logic.sensitivity_name(0.75) in live.controls.sens_note.text()


# ---- navigation ------------------------------------------------------------------

@pytest.mark.parametrize("key, target", [("score", "history"), ("moved", "evidence"), ("heat", "thermal"),
                                         ("velocity", "spectrum")])
def test_signal_row_click_navigates(qtbot, live, key, target):
    row = live.signals.row(key)
    assert row is not None
    with qtbot.waitSignal(live.navigate, timeout=1000) as sig:
        click(qtbot, row)
    assert sig.args == [target]


def test_links_navigate(qtbot, live):
    for widget, target in ((live.status.tile_protection, "device"), (live.status.tile_state, "history"),
                           (live.spectrum_panel, "spectrum"), (live.thermal_panel, "thermal"),
                           (live.timeline_panel, "history")):
        with qtbot.waitSignal(live.navigate, timeout=1000) as sig:
            click(qtbot, widget)
        assert sig.args == [target]


# ---- values ------------------------------------------------------------------------

def test_abnormal_values_use_flag_value(live, state):
    rec = state.latest()
    assert rec["unknown_score"] > rec["alarm_line"]  # the fixture's newest record is abnormal
    score = live.signals.row("score")
    assert isinstance(score.value, FlagValue)
    assert score.value.abnormal() and score.value.text() == f"{rec['unknown_score']:.2f}×"
    assert live.signals.row("moved").value.abnormal()  # vib_band_17 moved 6.8 spreads
    assert not live.signals.row("confidence").value.abnormal()
    for row in live.signals.rows():
        assert isinstance(row.value, FlagValue)


def test_value_back_to_normal_drops_flag(live, state):
    rec = copy.deepcopy(state.latest())
    rec.update(seq=11, at="2026-11-14T16:00:10.000Z", diagnosis="normal", unknown_score=0.4, features={"vib_1x": 0.3},
               problems=[], wrong=False, abnormal_minutes=0)
    rec.pop("diagnosis_this_minute", None)
    state.push("record", rec)
    assert not live.signals.row("score").value.abnormal()
    assert live.status.headline.text() == "Nothing is wrong"


def test_history_flags_scores_over_the_line(history):
    m = history.records_model
    flagged = [m.index(r, 5).data(history.records_table.FlagRole) for r in range(m.rowCount())]
    scores = [m.index(r, 5).data() for r in range(m.rowCount())]
    assert flagged[0] and scores[0] == "1.61"
    assert not any(f for f, s in zip(flagged, scores) if s == "–" or float(s) <= 1.0)


# ---- wording from the contract ------------------------------------------------------

def test_headline_follows_logic(live, state):
    assert live.status.headline.text() == state.headline()["text"] == "Something is wrong"
    assert live.status.sub.text().startswith(state.model_line()["label"])
    state.set_connected(False)
    assert live.status.headline.text() == "Not live"


def test_records_filter(history):
    history.filter.setCurrentIndex(1)  # abnormal only
    diags = {history.records_model.index(r, 2).data(history.records_table.PillRole)[0]
             for r in range(history.records_model.rowCount())}
    assert diags == {logic.display_name("unbalance_like"), logic.display_name("unrecognised_pattern")}
    assert history.records_panel.hint() == "4 periods"


# ---- actions ------------------------------------------------------------------------

def test_run_agent_and_sim_keys(qtbot, live, state):
    click(qtbot, live.controls.btn_agent)
    assert state.started_agents == ["standin"]
    click(qtbot, live.controls.case_buttons["2"])
    click(qtbot, live.controls.btn_pause)
    assert state.sim().keys == ["2", "p"]


def test_sim_controls_hidden_without_sim(qtbot):
    page = LivePage()
    qtbot.addWidget(page)
    page.bind(FakeState())
    assert page.controls.sim.isHidden()


def test_export_session(live, tmp_path):
    path = tmp_path / "s.csv"
    assert live.export_session(str(path)) == 10
    assert path.read_text().splitlines()[0].startswith("seq,at,")


# ---- no red text ----------------------------------------------------------------------

TEXT_ROLES = [QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText,
              QPalette.ColorRole.BrightText, QPalette.ColorRole.HighlightedText]
HTML_COLOR = re.compile(r"color\s*[:=]\s*[\"']?(#[0-9a-f]{3,8}|rgba?\([^)]*\))", re.IGNORECASE)


def _red_text(root: QWidget) -> list[str]:
    bad = []
    for w in [root, *root.findChildren(QWidget)]:
        for role in TEXT_ROLES:
            if t.is_red(w.palette().color(role)):
                bad.append(f"{type(w).__name__} palette {role.name}")
        for value in HTML_COLOR.findall(w.styleSheet() + (w.text() if isinstance(w, QLabel) else "")):
            if t.is_red(QColor(value) if value.startswith("#") else t.qcolor(value)):
                bad.append(f"{type(w).__name__} colour {value}")
    return bad


def _pixels_red_in_text(widget: QWidget) -> bool:
    """True if any glyph pixel of a text label is red. Labels only hold text."""
    img = widget.grab().toImage()
    red = t.qcolor(t.RED)
    for y in range(img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            if abs(c.red() - red.red()) < 24 and abs(c.green() - red.green()) < 24 and abs(c.blue() - red.blue()) < 24:
                return True
    return False


def test_no_red_text(live, history):
    for page in (live, history):
        assert _red_text(page) == []
        for lab in page.findChildren(QLabel):
            if lab.isVisible() and lab.text():
                assert not _pixels_red_in_text(lab), lab.text()
