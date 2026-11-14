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


def test_run_starts_the_agent_in_the_chosen_mode(qtbot, state):
    from app.pages.agents import AgentsPage

    page = make(qtbot, AgentsPage, state)
    assert [(k, page.mode.button(k).text()) for k in page.mode.keys()] == [
        ("standin", "Stand-in"), ("local", "Local model")]
    assert page.run_button.isEnabled()
    qtbot.mouseClick(page.run_button, Qt.MouseButton.LeftButton)
    assert state.started_agents == ["standin"]
    state.start_model_server()
    page.mode.button("local").click()
    qtbot.mouseClick(page.run_button, Qt.MouseButton.LeftButton)
    assert state.started_agents == ["standin", "local"]
    assert not page.message.isVisible()


def test_local_mode_with_no_model_server_shows_the_state_refusal(qtbot, state):
    from app.pages.agents import AgentsPage

    page = make(qtbot, AgentsPage, state)
    assert not page.message.isVisible()
    page.mode.button("local").click()
    qtbot.mouseClick(page.run_button, Qt.MouseButton.LeftButton)
    assert state.started_agents == ["local"]
    assert page.message.isVisible()
    assert page.message.text() == state.last_agent_message()
    assert page.message.text().startswith("No model server is running at http://127.0.0.1:8081.")


def test_local_model_shows_its_folder_size_and_server(qtbot, state):
    from app.pages.agents import AgentsPage
    from app.pages.agents import view as agents_view

    page = make(qtbot, AgentsPage, state)
    page.mode.button("local").click()
    rows = page.model_rows
    assert (rows.text("Folder"), rows.text("Size"), rows.text("Server")) == (
        "~/effente-models", "Small, small.gguf", "Not running.")
    assert page.size.current() == "small"
    assert (page.server_button.text(), page.server_button.isEnabled()) == (agents_view.START_SERVER, True)

    qtbot.mouseClick(page.server_button, Qt.MouseButton.LeftButton)
    assert rows.text("Server") == "Running with small.gguf."
    assert page.server_button.text() == agents_view.STOP_SERVER

    page.size.button("large").click()  # the running server still holds the small file
    assert state.model_size() == "large"
    assert rows.text("Size") == "Large, large.gguf"
    assert rows.text("Server") == "Running with small.gguf. Stop and start it to load large.gguf."

    qtbot.mouseClick(page.server_button, Qt.MouseButton.LeftButton)
    assert rows.text("Server") == "Not running."
    assert page.server_button.text() == agents_view.START_SERVER


def test_a_server_started_elsewhere_is_not_ours_to_start_or_stop(qtbot, state):
    from app.pages.agents import AgentsPage
    from app.pages.agents import view as agents_view

    page = make(qtbot, AgentsPage, state)
    page.mode.button("local").click()
    state.set_model_status("ready", "Running. It was started outside this app, so this app will not stop it.")
    assert (page.server_button.text(), page.server_button.isEnabled()) == (agents_view.START_SERVER, False)
    state.set_model_status("failed", "llama-server failed to start (exit code 1): out of memory")
    assert page.model_rows.text("Server") == "llama-server failed to start (exit code 1): out of memory"
    assert page.server_button.isEnabled()


def test_model_settings_show_only_in_local_mode(qtbot, state):
    from app.pages.agents import AgentsPage
    from app.pages.agents import view as agents_view

    page = make(qtbot, AgentsPage, state)
    assert not page.model_section.isVisible()
    assert page.mode_note.text() == agents_view.MODE_NOTES["standin"]
    page.mode.button("local").click()
    assert page.model_section.isVisible()
    assert page.mode_note.text() == agents_view.MODE_NOTES["local"]
    page.mode.button("standin").click()
    assert not page.model_section.isVisible()


def test_change_folder_keeps_the_folder_picked(qtbot, state, monkeypatch, tmp_path):
    from app.pages.agents import AgentsPage
    from PySide6.QtWidgets import QFileDialog

    page = make(qtbot, AgentsPage, state)
    page.mode.button("local").click()
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: "")  # cancelled
    qtbot.mouseClick(page.folder_button, Qt.MouseButton.LeftButton)
    assert state.model_folder() == "~/effente-models"
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(tmp_path))
    qtbot.mouseClick(page.folder_button, Qt.MouseButton.LeftButton)
    assert state.model_folder() == str(tmp_path)
    assert page.model_rows.text("Folder") == str(tmp_path)


def test_no_page_text_names_an_outside_service(qtbot, state):
    """Labels and tooltips the app writes itself. A run's `model` is the agent's own text (device/agent)."""
    from app.pages.agents import AgentsPage
    from PySide6.QtWidgets import QAbstractButton

    page = make(qtbot, AgentsPage, state)
    page.mode.button("local").click()
    qtbot.mouseClick(page.run_button, Qt.MouseButton.LeftButton)
    texts = [page.message.text(), page.model_rows.text("Server")]
    texts += [b.text() + " " + b.toolTip() for b in page.findChildren(QAbstractButton)]
    for text in texts:
        assert not any(word in text for word in ("Claude", "Live", "API key", "ANTHROPIC")), text


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
    assert page.run_button.text() == "Writing the report…"
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
