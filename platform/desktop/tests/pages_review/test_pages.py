"""Evidence, Agents and Device & model pages on FakeState."""

from __future__ import annotations

import copy

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from app.theme import tokens as t
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel

PAGE_SIZE = (1320, 896)  # the page area inside a 1600x1000 window


@pytest.fixture
def state():
    from fakes import FakeState

    return FakeState()


def make(qtbot, cls, state):
    page = cls()
    qtbot.addWidget(page)
    page.resize(*PAGE_SIZE)
    page.bind(state)
    page.show()
    return page


def pushed_record(state, **changes):
    rec = copy.deepcopy(state.latest())
    rec.pop("heat", None)
    rec["seq"] = rec.get("seq", 0) + 1
    rec.update(changes)
    state.push("record", rec)
    return rec


# ---- Agents -------------------------------------------------------------


def test_run_starts_the_agent_in_the_chosen_mode(qtbot, state, monkeypatch):
    from app.pages.agents import AgentsPage

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    page = make(qtbot, AgentsPage, state)
    assert page.run_button.isEnabled()
    qtbot.mouseClick(page.run_button, Qt.MouseButton.LeftButton)
    assert state.started_agents == ["standin"]
    page.mode.button("live").click()
    qtbot.mouseClick(page.run_button, Qt.MouseButton.LeftButton)
    assert state.started_agents == ["standin", "live"]


def test_live_mode_without_a_key_shows_the_state_refusal(qtbot, state, monkeypatch):
    from app.pages.agents import AgentsPage

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    page = make(qtbot, AgentsPage, state)
    assert not page.message.isVisible()
    page.mode.button("live").click()
    qtbot.mouseClick(page.run_button, Qt.MouseButton.LeftButton)
    assert state.started_agents == ["live"]
    assert page.message.isVisible()
    assert page.message.text() == state.last_agent_message() != ""
    # The page never asks for the key.
    from PySide6.QtWidgets import QLineEdit

    assert page.findChildren(QLineEdit) == []


def test_stand_in_report_is_labelled_simulated(qtbot, state):
    from app.pages.agents import AgentsPage

    page = make(qtbot, AgentsPage, state)
    run = state.runs()[0]
    assert run["simulated"] is True
    assert page.sim_chip.isVisible()
    assert page.sim_chip.text() == "Simulated"
    assert page.report.text("Summary").startswith("Stand-in report:")
    assert page.report.text("Checks") == "all passed"


def test_button_disabled_while_a_run_is_running(qtbot, state):
    from app.pages.agents import AgentsPage

    page = make(qtbot, AgentsPage, state)
    run = dict(copy.deepcopy(state.runs()[0]), run_id="run-later", at="2030-01-01T00:00:00Z",
               status="running", ok=False, report=None, tool_calls=[])
    state.push("agent_run", run)
    assert not page.run_button.isEnabled()
    assert page.report.text("Status") == "running"
    qtbot.mouseClick(page.run_button, Qt.MouseButton.LeftButton)
    assert state.started_agents == []


def test_tool_call_shows_full_input_and_output(qtbot, state):
    from app.pages.agents import AgentsPage, view

    page = make(qtbot, AgentsPage, state)
    assert page.io.toPlainText() == view.IO_EMPTY
    page.calls.row_clicked.emit(0)
    call = state.runs()[0]["tool_calls"][0]
    assert page.io.toPlainText().startswith(call["name"] + "\n\ninput\n")
    assert page.io.toPlainText().endswith(call["output"])


# ---- Evidence -----------------------------------------------------------


def test_moved_readings_count_from_six_spreads(qtbot, state):
    from app.pages.evidence import EvidencePage

    page = make(qtbot, EvidencePage, state)
    pushed_record(state, features={"vib_1x": 6.0, "vib_2x": -5.99, "snd_level_db": -6.0})
    rows = page.moved.widget().findChildren(QLabel)
    texts = [w.text() for w in rows]
    assert "+6.0 spreads" in texts and "-6.0 spreads" in texts
    flags = {w.feature: w.evidence for w in page.moved.widget().children() if hasattr(w, "feature")}
    assert flags == {"vib_1x": True, "vib_2x": False, "snd_level_db": True}


def test_fault_types_show_as_possible(qtbot, state):
    from app.pages.evidence import EvidencePage

    page = make(qtbot, EvidencePage, state)
    m = page.types.row_model
    model_types = state.model()["fault_types"]
    assert m.rowCount() == len(model_types) > 0
    for row, ft in enumerate(model_types):
        expected = "" if ft.get("validated") is True else "possible · not validated"
        assert m.text(row, 3) == expected
    assert all(ft.get("validated") is not True for ft in model_types)


def test_fault_type_evidence_only_while_abnormal(qtbot, state):
    from app.pages.evidence import EvidencePage

    page = make(qtbot, EvidencePage, state)
    assert page.ft.empty_text == ""  # the sample ends abnormal, with fault type evidence
    pushed_record(state, diagnosis="normal", fault_types={"vibration_too_high": 3.0}, wrong=False)
    assert page.ft.empty_text.startswith("Sent only while the machine is abnormal")
    assert all(page.types.row_model.text(r, 4) == "–" for r in range(page.types.row_model.rowCount()))


# ---- Device & model -----------------------------------------------------


def test_device_page_shows_the_model_and_pauses_the_log(qtbot, state):
    from app.pages.device import DevicePage

    page = make(qtbot, DevicePage, state)
    model = state.model()
    assert page.field("Model version") == model["model_version"]
    assert page.field("Fault types") == f"{len(model['fault_types'])} · 0 validated"
    assert page.levels.steps[1][1] is True  # calibrated
    before = page.log.row_model.rowCount()
    assert before > 0
    page.pause.setChecked(True)
    run = dict(copy.deepcopy(state.runs()[0]), run_id="run-later", at="2030-01-01T00:00:00Z")
    state.push("agent_run", run)
    assert page.log.row_model.rowCount() == before
    page.pause.setChecked(False)
    assert page.log.row_model.rowCount() == before + 1


# ---- the RED rule ---------------------------------------------------------


@pytest.mark.parametrize("name", ["evidence", "agents", "device"])
def test_no_red_text(qtbot, state, name):
    from app.pages.agents import AgentsPage
    from app.pages.device import DevicePage
    from app.pages.evidence import EvidencePage
    from app.theme import apply_theme
    from PySide6.QtWidgets import QApplication

    apply_theme(QApplication.instance())
    cls = {"evidence": EvidencePage, "agents": AgentsPage, "device": DevicePage}[name]
    page = make(qtbot, cls, state)
    page.grab()  # polish every widget so stylesheet colours are applied
    reds = [c.lower() for c in t.RED_FAMILY] + ["255, 77, 46", "255,77,46"]
    for w in page.findChildren(QLabel):
        assert not t.is_red(w.palette().color(w.foregroundRole())), w.text()
        html = (w.text() + w.styleSheet()).lower()
        assert not any(r in html for r in reds), w.text()
    from app.widgets import DataTable

    for table in page.findChildren(DataTable):
        m = table.model()
        for r in range(m.rowCount()):
            for c in range(m.columnCount()):
                assert m.index(r, c).data(Qt.ItemDataRole.ForegroundRole) is None
