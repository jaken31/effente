"""The Setup page on FakeState: every step, every action and every state it has to draw."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from app.pages.setup import SetupPage, view
from fake_setup import DEVICE_ENTRY, RUN_FIELDS, WANTED_ENTRY, sample_choice
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractButton, QComboBox, QLabel, QLineEdit

PAGE_SIZE = (1320, 1400)


@pytest.fixture
def state():
    from fakes import FakeState

    return FakeState()


@pytest.fixture
def fresh(state):
    """A machine with no profile and no local profile."""
    state.setup.clear()
    return state


@pytest.fixture
def page(qtbot, state):
    p = SetupPage()
    qtbot.addWidget(p)
    p.resize(*PAGE_SIZE)
    p.bind(state)
    p.show()
    return p


@pytest.fixture
def failures(state):
    seen = []
    state.action_failed.connect(seen.append)
    return seen


def click(qtbot, widget):
    qtbot.mouseClick(widget, Qt.MouseButton.LeftButton)


def answer(page, **typed):
    """Type answers by question id; a value of None presses "I don't know"."""
    for a in page.questions.items:
        if a.q.id in typed:
            if typed[a.q.id] is None:
                a.dont_know.setChecked(True)
            else:
                a.text.setText(typed[a.q.id])


def shown_texts(page) -> str:
    """Every word on the page a person can see: labels, buttons, fields, panel titles."""
    out = []
    for w in page.findChildren(QLabel) + page.findChildren(QAbstractButton):
        if w.isVisibleTo(page):
            out.append(w.text())
    for w in page.findChildren(QLineEdit):
        out += [w.text(), w.placeholderText()]
    for w in page.findChildren(QComboBox):
        out += [w.itemText(i) for i in range(w.count())]
    for panel in (page.local, page.questions, page.profile, page.model, page.device):
        out += [panel.title(), panel.hint()]
    return "\n".join(out)


# ---- the shell finds it ---------------------------------------------------------


def test_the_shell_builds_this_page_for_the_setup_key():
    from app.shell.pages import find_page_class

    assert find_page_class("setup") is SetupPage


def test_the_page_imports_no_other_page():
    import inspect

    from app.pages.setup import page as page_module
    from app.pages.setup import steps

    for mod in (page_module, steps):
        src = inspect.getsource(mod)
        for other in ("pages.live", "pages.log", "pages.machine", "pages.agents", "pages.device"):
            assert other not in src or (mod is steps and other == "pages.log"), (mod.__name__, other)


# ---- the local profile ---------------------------------------------------------


def test_no_local_profile_asks_for_one_once(qtbot, page, fresh):
    lp = page.local
    assert lp.form.isVisible() and lp.ask.isVisible()
    assert not lp.save_button.isEnabled()
    assert not lp.cancel_button.isVisible()  # nothing to go back to
    assert "Until one is saved, your name is sent as" in lp.fallback.text()
    qtbot.keyClicks(lp.name, "Robin")
    qtbot.keyClicks(lp.role, "line lead")
    click(qtbot, lp.save_button)
    assert fresh.setup.local_profile() == {"name": "Robin", "role": "line lead"}
    assert not lp.form.isVisible()
    assert lp.hint() == "Robin, line lead · kept on this laptop only"
    assert fresh.answerer() == "Robin"


def test_change_opens_the_form_filled_and_cancel_keeps_it(qtbot, page, state):
    lp = page.local
    assert not lp.form.isVisible() and lp.change_button.isVisible()
    assert lp.hint().startswith("Sam, technician")
    click(qtbot, lp.change_button)
    assert lp.form.isVisible() and lp.name.text() == "Sam" and lp.role.text() == "technician"
    lp.name.setText("Alex")
    click(qtbot, lp.cancel_button)
    assert not lp.form.isVisible()
    assert state.setup.local_profile()["name"] == "Sam"


def test_there_is_no_password_and_no_login_words(page, fresh):
    for field in page.findChildren(QLineEdit):
        assert field.echoMode() == QLineEdit.EchoMode.Normal
    for state_setup in (lambda: None, lambda: fresh.setup.set_local_profile("Sam", "technician")):
        state_setup()
        words = shown_texts(page).lower()
        for banned in ("login", "log in", "account", "sign in", "security"):
            assert banned not in words, banned
        assert all("no password" in line for line in words.splitlines() if "password" in line)
        assert "local profile" in words


# ---- step 1: questions and the run ---------------------------------------------


def test_fill_sends_the_answers_with_dont_know_as_none(qtbot, page, fresh):
    answer(page, machine_type="a belt conveyor", drive="through a belt", speed_rpm=None, sensor_location=None)
    assert page.questions.count.text() == "2 answered, 3 not known"
    click(qtbot, page.questions.fill_button)
    (answers, mode), = fresh.setup.started_runs
    assert mode == "standin"
    assert {a["about"]: a["answer"] for a in answers} == {
        "machine_type": "a belt conveyor", "drive": "through a belt", "speed_rpm": None, "mounting": None,
        "sensor_location": None}
    assert all(set(a) == {"about", "question", "answer"} for a in answers)


def test_i_dont_know_clears_and_locks_the_line(qtbot, page):
    a = page.questions.items[2]
    a.text.setText("1450")
    click(qtbot, a.dont_know)
    assert a.text.text() == "" and not a.text.isEnabled() and a.text.placeholderText() == view.DONT_KNOW
    click(qtbot, a.dont_know)
    assert a.text.isEnabled()


def test_no_model_server_runs_the_stand_in_and_says_so(page, fresh):
    assert page.questions.mode.text() == view.STANDIN_NOTE


def test_a_running_model_server_runs_the_local_model(qtbot, page, fresh):
    fresh.set_model_status("ready", "Running with small.gguf.", True, "small.gguf")
    page.refresh()
    assert "local model (small.gguf)" in page.questions.mode.text()
    click(qtbot, page.questions.fill_button)
    assert fresh.setup.started_runs[-1][1] == "local"


def test_a_running_run_disables_fill_and_the_form(qtbot, page, fresh):
    click(qtbot, page.questions.fill_button)
    assert page.questions.fill_button.text() == view.FILLING
    assert not page.questions.fill_button.isEnabled()
    assert not page.profile.inputs["machine_type"].isEnabled()
    assert not page.model.use_button.isEnabled()
    assert page.questions.result_title.text() == "Reading your answers"
    click(qtbot, page.questions.fill_button)
    assert len(fresh.setup.started_runs) == 1


def test_the_stand_in_result_is_labelled_and_fills_the_draft(qtbot, page, fresh):
    click(qtbot, page.questions.fill_button)
    fresh.setup.finish_run(ok=True)
    q = page.questions
    assert q.result_title.text() == "What the stand-in understood"
    assert q.standin_chip.isVisible() and q.result_text.text().startswith("Stand-in:")
    assert page.profile.inputs["machine_type"].currentData() == RUN_FIELDS["machine_type"]
    assert page.profile.inputs["speed_rpm"].text() == ""
    assert page.profile.hint() == view.NOT_CONFIRMED
    assert fresh.setup.profile() is None and fresh.setup.wanted_entry() is None  # nothing stored or sent


def test_a_failed_run_says_why_and_points_at_the_form(qtbot, page, fresh):
    click(qtbot, page.questions.fill_button)
    fresh.setup.finish_run(ok=False, reason="an extra key in its reply")
    q = page.questions
    assert q.result_title.text() == "The agent could not fill the profile"
    assert "Why: an extra key in its reply." in q.result_text.text()
    assert not q.standin_chip.isVisible()
    assert q.fill_button.isEnabled()
    assert page.profile.inputs["machine_type"].isEnabled()  # by hand, then


# ---- step 2: the machine profile -------------------------------------------------


def test_every_field_empty_shows_empty(page, fresh):
    for field, w in page.profile.inputs.items():
        shown = w.currentText() if isinstance(w, QComboBox) else w.placeholderText()
        assert shown == view.EMPTY, field
    assert "allowed" in page.profile.note.text()


def test_a_field_set_by_hand_asks_the_lookup_again(qtbot, page, fresh):
    combo = page.profile.inputs["machine_type"]
    combo.setCurrentIndex(combo.findData("pump"))
    combo.activated.emit(combo.currentIndex())
    assert fresh.setup.draft()["machine_type"] == "pump"


def test_a_field_can_be_set_back_to_empty(page, state):
    combo = page.profile.inputs["drive"]
    combo.setCurrentIndex(0)
    combo.activated.emit(0)
    assert state.setup.draft()["drive"] is None


def test_speed_by_hand(qtbot, page, fresh):
    speed = page.profile.inputs["speed_rpm"]
    speed.setFocus()
    qtbot.keyClicks(speed, "1400 to 1500")
    speed.editingFinished.emit()
    assert fresh.setup.draft()["speed_rpm"] == [1400, 1500]


def test_a_bad_speed_is_not_sent_and_blocks_use(qtbot, page, fresh):
    fresh.setup.set_choice(sample_choice())
    speed = page.profile.inputs["speed_rpm"]
    speed.setFocus()
    qtbot.keyClicks(speed, "fast")
    speed.editingFinished.emit()
    assert fresh.setup.draft()["speed_rpm"] is None
    assert page.profile.speed_problem.isVisible()
    page.refresh()
    assert not page.model.use_button.isEnabled()
    assert page.model.use_button.toolTip().startswith("Speed:")


# ---- step 3: the starting model and "Use this model" -------------------------------


def test_the_choice_in_the_contracts_words(page):
    m = page.model
    assert m.entry.text() == "conveyor (demo)"
    assert m.values["Checked on"].text() == "Nothing yet"
    assert m.values["Trained on"].text() == "sim_conveyor (simulated)"
    assert m.values["Fault types validated"].text() == "0 of 21"
    assert m.warning.isVisible()
    assert m.by_code.text() == view.PICKED_BY_CODE


def test_a_failed_lookup_says_why_and_use_waits(page, state):
    state.setup.set_lookup_problem("The starting model could not be looked up: no entry could be read")
    assert page.model.entry.text() == view.NO_CHOICE
    assert page.model.problem.isVisible() and "no entry could be read" in page.model.problem.text()
    assert not page.model.use_button.isEnabled()
    assert "no entry could be read" in page.model.use_button.toolTip()


def test_no_choice_yet_reads_looking_up_and_use_waits(page, fresh):
    assert page.model.entry.text() == view.LOOKING_UP
    assert not page.model.rows.isVisible()
    assert not page.model.use_button.isEnabled()


def test_nothing_is_sent_before_use(qtbot, page, fresh):
    answer(page, machine_type="a belt conveyor")
    click(qtbot, page.questions.fill_button)
    fresh.setup.finish_run(ok=True)
    combo = page.profile.inputs["mounting"]
    combo.setCurrentIndex(combo.findData("flexible"))
    combo.activated.emit(combo.currentIndex())
    assert fresh.setup.profile() is None and fresh.setup.wanted_entry() is None
    assert page.model.confirm_line.text() == view.SAVING_LINE
    assert page.device.status == view.NOTHING_SENT


def test_use_confirms_with_the_local_profiles_name(qtbot, page, fresh):
    fresh.setup.set_local_profile("Robin", "line lead")
    click(qtbot, page.questions.fill_button)
    fresh.setup.finish_run(ok=True)
    assert page.model.use_button.isEnabled()
    click(qtbot, page.model.use_button)
    assert fresh.setup.profile()["confirmed"]["by"] == "Robin"
    assert "role" not in fresh.setup.profile()["confirmed"]
    assert page.profile.hint() == view.CONFIRMED
    assert page.model.confirm_line.text().startswith("Confirmed by Robin")
    assert not page.model.use_button.isVisible()


def test_a_change_after_confirming_can_be_used_again(page, state):
    combo = page.profile.inputs["machine_type"]
    combo.setCurrentIndex(combo.findData("fan"))
    combo.activated.emit(combo.currentIndex())
    assert page.profile.hint() == view.CHANGED
    assert page.model.use_button.isVisible() and page.model.use_button.isEnabled()
    assert "not sent" in page.model.confirm_line.text()


# ---- step 4: the device's answer --------------------------------------------------


def test_wanted_and_in_use_differ(page, state):
    assert (state.setup.wanted_entry(), state.setup.device_entry()) == (WANTED_ENTRY, DEVICE_ENTRY)
    assert page.device.line.text() == "Chosen: conveyor. Device is using: generic_rotating."
    assert page.device.mark.status == view.DIFFERS
    assert page.device.note.text() == view.NOT_ACTED


def test_the_device_has_not_answered(page, state):
    state.setup.set_entries(WANTED_ENTRY, None)
    assert page.device.status == view.WAITING
    assert page.device.line.text() == "Chosen: conveyor, not yet loaded on the device."


def test_no_device_for_this_machine(page, state):
    state.device_id = lambda: None
    page.refresh()
    assert page.device.status == view.NO_DEVICE
    assert page.device.line.text() == "Chosen: conveyor, not yet loaded on the device."


def test_the_device_took_it(page, state):
    state.setup.set_entries(WANTED_ENTRY, WANTED_ENTRY)
    assert page.device.line.text() == "Device is using: conveyor, the chosen one."
    assert page.device.mark.status == view.TAKEN


def test_another_machine_starts_from_nothing(page, state):
    state.setup.machine = "another-machine"
    page.refresh()
    assert page.profile.hint() == view.NOT_CONFIRMED
    assert page.device.status == view.NOTHING_SENT
    assert page.model.entry.text() == view.LOOKING_UP


def test_no_machine_disables_everything(page, state):
    state.current_machine = lambda: None
    page.refresh()
    assert page.questions.mode.text() == view.NO_MACHINE
    assert not page.questions.fill_button.isEnabled()
    assert not page.profile.inputs["drive"].isEnabled()
    assert page.model.use_button.toolTip() == view.NO_MACHINE
