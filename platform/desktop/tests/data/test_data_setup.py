"""`state.setup` on a real receiver (contract section 10): profiles, the lookup,
confirm, and a stand-in setup run started as a local process."""

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from app.data.api import ApiClient
from app.data.setup import FIELDS

MACHINE = "conveyor-07"
ANSWERS = [
    {"about": "machine_type", "question": "What kind of machine is it?", "answer": "a belt conveyor"},
    {"about": "drive", "question": "How does the motor turn it?", "answer": "through a belt"},
    {"about": "speed_rpm", "question": "How fast does it turn?", "answer": None},
    {"about": "mounting", "question": "How is it held in place?", "answer": "bolted to the floor"},
    {"about": "sensor_location", "question": "Where is the sensor fixed?", "answer": None},
]


@pytest.fixture
def state(make_host, make_state, monkeypatch):
    monkeypatch.setenv("EFFENTE_ANSWERER", "Alex")
    s = make_state(make_host())
    s.set_machine(MACHINE)
    return s


@pytest.fixture
def failures(state):
    seen = []
    state.action_failed.connect(seen.append)
    return seen


def test_the_snapshot_says_which_machines_have_a_profile(state):
    assert state.setup.profile_known()
    assert state.setup.profile() is None
    assert state.setup.draft() == dict.fromkeys(FIELDS)


def test_the_choice_comes_from_the_lookup(qtbot, state):
    setup = state.setup
    assert setup.choice() is None  # asked now
    qtbot.waitUntil(lambda: setup.choice() is not None, timeout=5000)
    assert setup.choice()["library_entry"] == "generic_rotating"
    assert setup.choice()["reason"] == "machine type not given, so the general one"
    setup.set_draft(machine_type="conveyor")
    qtbot.waitUntil(lambda: (setup.choice() or {}).get("library_entry") == "conveyor", timeout=5000)
    assert setup.choice()["checked_on"] == []
    assert setup.lookup_problem() == ""


def test_nothing_is_stored_before_confirm_and_confirm_stores_it(qtbot, state, failures):
    setup, api = state.setup, ApiClient(state.api.base)
    setup.set_draft(machine_type="conveyor", drive="belt", speed_rpm=[1400, 1500])
    qtbot.waitUntil(lambda: setup.choice() is not None, timeout=5000)
    assert api.get(f"/api/machines/{MACHINE}/profile")["profile"] is None
    assert setup.wanted_entry() is None
    with qtbot.waitSignal(setup.entry_changed, timeout=5000):
        setup.confirm()
    stored = api.get(f"/api/machines/{MACHINE}/profile")["profile"]
    assert stored["confirmed"]["by"] == "Alex" and stored["run_id"] is None
    assert (stored["machine_type"], stored["speed_rpm"], stored["mounting"]) == ("conveyor", [1400, 1500], None)
    assert setup.profile()["choice"]["library_entry"] == "conveyor"
    assert setup.wanted_entry() == "conveyor"
    assert setup.device_entry() is None  # no device reports this machine
    assert failures == []


def test_the_local_profiles_name_is_by(qtbot, state):
    state.setup.set_local_profile("Sam", "technician")
    with qtbot.waitSignal(state.setup.profile_changed, timeout=5000):
        state.setup.confirm()
    assert state.setup.profile()["confirmed"]["by"] == "Sam"


def test_a_stand_in_run_fills_the_draft_and_stores_nothing(qtbot, state, failures):
    setup = state.setup
    setup.start_run(ANSWERS, mode="standin")
    assert setup.run_running()
    qtbot.waitUntil(lambda: not setup.run_running() and setup.run() is not None, timeout=30000)
    run = setup.run()
    assert run["ok"] and run["simulated"] and run["setup"]["explanation"].startswith("Stand-in:")
    profile = run["setup"]["profile"]
    assert profile["machine_type"] == "conveyor" and profile["speed_rpm"] is None
    qtbot.waitUntil(lambda: setup.draft() == profile, timeout=5000)
    assert setup.choice()["library_entry"] == "conveyor"
    assert setup.profile() is None and setup.wanted_entry() is None  # a run stores nothing
    with qtbot.waitSignal(setup.profile_changed, timeout=5000):
        setup.confirm()
    assert setup.profile()["run_id"] == run["run_id"]
    assert failures == []


def test_a_second_run_waits_for_the_first(state, failures):
    state.setup.start_run(ANSWERS)
    state.setup.start_run(ANSWERS)
    assert failures == ["Setup run not started: one is already running for this machine."]


def test_an_unknown_field_is_refused(state, failures):
    state.setup.set_draft(library_entry="conveyor")
    assert failures == ["Profile not changed: unknown field: library_entry"]
