"""Row R0 and row M2 of docs/scan-and-log-plan.md section 15.2: the `state.log`
additions of INTERFACES.md addition 22 and `state.setup` of addition 24 exist, with
the samples of additions 22 and 25 on the fake one. The live part readings are tested
in test_data_readings.py, the live `state.setup` in test_data_setup.py."""

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from app.data.api import ApiClient
from app.data.partlog import PartLog
from app.data.setup import FIELDS, Setup
from fake_partlog import BELT, DRIVE_ROLLER, IDLE_ROLLER, MOTOR_MOUNT, FakeLog
from fake_setup import FakeSetup
from fakes import FakeState

READINGS = {"signals": ("probe_changed", "readings_changed"),
            "calls": ("probe_part", "probe", "readings", "normal_spectrum", "set_probe_part")}
SETUP = {"signals": ("profile_changed", "run_changed", "entry_changed", "local_profile_changed"),
         "calls": ("profile", "draft", "choice", "run", "run_running", "wanted_entry", "device_entry",
                   "local_profile", "start_run", "set_draft", "confirm", "set_local_profile")}
READING_KEYS = {"part_id", "machine_id", "start", "end", "records", "velocity_mm_s", "unknown_score", "alarm_line",
                "model_version", "abnormal", "alarm_ids", "open", "simulated"}
PROJECTION_KEYS = {"status", "fits", "line", "window", "now_score", "slope_per_day", "slope_range", "show_days",
                   "reaches", "simulated"}


@pytest.mark.parametrize("cls,names", [(PartLog, READINGS), (FakeLog, READINGS), (Setup, SETUP), (FakeSetup, SETUP)])
def test_the_objects_have_the_agreed_names(cls, names):
    for name in names["signals"] + names["calls"] + ("apply_snapshot", "apply_event"):
        assert hasattr(cls, name), name


def test_the_live_setup_gets_every_snapshot_and_event(make_host, make_state, qtbot):
    host = make_host()
    state = make_state(host, wait=False)
    seen = []
    state.setup.apply_snapshot = lambda snap: seen.append("snapshot")
    state.setup.apply_event = lambda name, data: seen.append(name)
    qtbot.waitUntil(lambda: "snapshot" in seen, timeout=5000)
    ApiClient(host.url).put("/api/devices/effente-01/settings", {"sensitivity": 0.7})
    qtbot.waitUntil(lambda: "settings" in seen, timeout=5000)
    assert type(state.setup) is Setup  # built: its own tests are test_data_setup.py


def test_the_answerer_is_the_local_profiles_name_first(make_host, make_state, monkeypatch):
    monkeypatch.setenv("EFFENTE_ANSWERER", "Alex")
    state = make_state(make_host())
    assert state.answerer() == "Alex"  # no local profile: as before
    state.setup.local_profile = lambda: {"name": "Sam", "role": "technician"}
    assert state.answerer() == "Sam"
    state.setup.local_profile = lambda: {"name": " ", "role": "technician"}
    assert state.answerer() == "Alex"


def test_the_fake_readings_sample(qapp):
    state = FakeState()
    log = state.log
    replies = {part_id: log.readings(part_id) for part_id in (DRIVE_ROLLER, BELT, MOTOR_MOUNT, IDLE_ROLLER)}
    assert {p: (len(r["readings"]), r["projection"]["status"]) for p, r in replies.items()} == {
        DRIVE_ROLLER: (20, "rising"), BELT: (20, "no_clear_rise"), MOTOR_MOUNT: (1, "not_enough_history"),
        IDLE_ROLLER: (0, "not_enough_history")}
    for part_id, reply in replies.items():
        readings = reply["readings"]
        assert all(set(r) == READING_KEYS and r["simulated"] is True and r["part_id"] == part_id for r in readings)
        assert [r["start"] for r in readings] == sorted(r["start"] for r in readings)
        assert len({r["start"][:10] for r in readings}) == len(readings)  # each on a day of its own
        assert set(reply["projection"]) == PROJECTION_KEYS and reply["projection"]["fits"] == "unknown_score"
        assert reply["summary"]["count"] == len(readings) and reply["summary"]["simulated"] is bool(readings)
        newest = log.part(part_id)["reading"]
        assert newest == (readings and {"velocity_mm_s": readings[-1]["velocity_mm_s"], "at": readings[-1]["start"],
                                         "open": False, "simulated": True} or None)
    rising = replies[DRIVE_ROLLER]
    assert rising["projection"]["reaches"]["days"] == [26, 43] and rising["projection"]["window"]["days"] == 14
    assert rising["summary"]["highest"] == rising["summary"]["newest"] == {
        "velocity_mm_s": rising["readings"][-1]["velocity_mm_s"], "at": rising["readings"][-1]["start"]}
    assert replies[IDLE_ROLLER]["summary"] == {
        "count": 0, "average_mm_s": None, "highest": None, "newest": None, "simulated": False}
    assert all(replies[p]["projection"]["reaches"] is None for p in (BELT, MOTOR_MOUNT, IDLE_ROLLER))

    week = log.readings(DRIVE_ROLLER, since="2026-11-08T00:00:00Z")  # the summary follows `since`, the projection does not
    assert week["summary"]["count"] == len(week["readings"]) == 7 and week["projection"] == rising["projection"]

    ready, not_ready, whole = log.normal_spectrum(DRIVE_ROLLER), log.normal_spectrum(BELT), log.normal_spectrum()
    assert ready["ready"] is True and len(ready["spectrum"]) == 20 and ready["part_id"] == DRIVE_ROLLER
    assert len(ready["spectrum_edges_hz"]) == 21 and ready["simulated"] is True
    assert (not_ready["ready"], not_ready["spectrum"], not_ready["records"], not_ready["needed"]) == (False, None, 120, 300)
    assert whole["ready"] is True and whole["part_id"] is None

    log.set_show_days(False)
    off = log.readings(DRIVE_ROLLER)["projection"]
    assert (off["status"], off["show_days"], off["reaches"]) == ("rising", False, None)
    state.set_machine("another-machine")
    assert log.readings(DRIVE_ROLLER) is None and log.normal_spectrum() is None and log.probe_part() is None


def test_the_fake_probe(qapp, qtbot):
    state = FakeState()
    log = state.log
    failed = []
    state.action_failed.connect(failed.append)
    assert log.probe_part() is None
    log.select_part(BELT)  # looking at a part does not move the probe
    assert log.probe_part() is None
    with qtbot.waitSignal(log.probe_changed):
        log.set_probe_part(DRIVE_ROLLER)
    assert log.probe_part() == DRIVE_ROLLER
    pick = log.probe()
    assert set(pick) == {"machine_id", "part_id", "since", "by"}
    assert (pick["machine_id"], pick["part_id"], pick["by"]) == (state.current_machine(), DRIVE_ROLLER, state.answerer())
    log.set_probe_part("part-9999")
    assert log.probe_part() == DRIVE_ROLLER and failed == ["Probe part not set: no such part: part-9999"]
    with qtbot.waitSignal(log.probe_changed):
        log.set_probe_part(None)
    assert log.probe_part() is None and log.probe() is None


def test_the_fake_setup_sample(qapp):
    state = FakeState()
    setup = state.setup
    assert type(setup) is FakeSetup
    profile, run, choice = setup.profile(), setup.run(), setup.choice()
    assert profile["machine_id"] == state.current_machine() and profile["run_id"] == run["run_id"]
    assert [k for k in FIELDS if profile[k] is None] == ["speed_rpm", "sensor_location"]
    assert setup.draft() == {k: profile[k] for k in FIELDS} == run["setup"]["profile"]
    assert (run["agent"], run["status"], run["simulated"], run["report"]) == ("setup", "done", True, None)
    assert run["setup"]["explanation"].startswith("Stand-in:")
    assert choice == profile["choice"] == run["setup"]["choice"]
    assert (choice["library_entry"], choice["status"], choice["checked_on"], choice["fault_types_not_validated"]) == (
        "conveyor", "demo", [], 21)
    assert (setup.wanted_entry(), setup.device_entry()) == ("conveyor", "generic_rotating")
    assert setup.local_profile() == {"name": "Sam", "role": "technician"} and state.answerer() == "Sam"
    state.set_machine("another-machine")  # a machine with no profile
    assert setup.profile() is None and setup.run() is None and setup.wanted_entry() is None
    assert setup.draft() == dict.fromkeys(FIELDS) and setup.local_profile() is not None


def test_the_fake_setup_actions(qapp, qtbot):
    state = FakeState()
    setup = state.setup
    setup.clear()
    assert setup.profile() is None and setup.run() is None and setup.choice() is None and setup.local_profile() is None
    with qtbot.waitSignal(setup.run_changed):
        setup.start_run({"How is it driven?": "I don't know"}, mode="local")
    assert setup.run_running() and setup.started_runs == [({"How is it driven?": "I don't know"}, "local")]
    setup.finish_run(ok=False, reason="the profile named a speed the answers do not")
    assert not setup.run_running() and setup.run()["ok"] is False and setup.run()["setup"] is None
    assert setup.draft() == dict.fromkeys(FIELDS)  # a failed run fills nothing
    setup.finish_run()
    assert setup.draft()["machine_type"] == "conveyor" and setup.choice()["library_entry"] == "conveyor"
    with qtbot.waitSignal(setup.profile_changed):
        setup.set_draft(speed_rpm=1450)
    assert setup.profile() is None and setup.wanted_entry() is None  # nothing is stored or wanted before confirm
    with qtbot.waitSignal(setup.entry_changed):
        setup.confirm()
    assert setup.profile()["speed_rpm"] == 1450 and setup.profile()["confirmed"]["by"] == state.answerer()
    assert setup.wanted_entry() == "conveyor" and setup.device_entry() is None
    with qtbot.waitSignal(setup.local_profile_changed):
        setup.set_local_profile("Kim", "planner")
    assert setup.local_profile() == {"name": "Kim", "role": "planner"} and state.answerer() == "Kim"
    setup.set_entries("conveyor", "conveyor")
    assert setup.device_entry() == "conveyor"
