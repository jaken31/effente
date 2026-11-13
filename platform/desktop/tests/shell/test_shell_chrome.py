from app.shell import format as fmt


def test_history_badge_follows_unanswered_alarms(window, state):
    assert window.sidebar.badge("history") is None
    state.unanswered = [{"alarm_id": "a1", "answer": None}, {"alarm_id": "a2", "answer": None}]
    state.alarms_changed.emit()
    assert window.sidebar.badge("history") == "2"
    state.unanswered = []
    state.alarms_changed.emit()
    assert window.sidebar.badge("history") is None


def test_agents_badge_while_a_run_is_going(window, state):
    assert window.sidebar.badge("agent") is None
    state.running = True
    state.runs_changed.emit()
    assert window.sidebar.badge("agent") == "1"
    state.running = False
    state.runs_changed.emit()
    assert window.sidebar.badge("agent") is None


def test_stale_freshness_text(window, state):
    state.fresh = {"status": "no_readings", "age_s": 12.2, "platform_up": True}
    state.record_added.emit({})
    assert window.topbar.fresh.text() == "No reading for 12s"
    assert window.topbar.stale
    assert window.sidebar.card.values["records"].text() == "0 / s"


def test_freshness_ages_without_signals(window, state, qtbot):
    state.fresh = {"status": "disconnected", "age_s": 75, "platform_up": True}
    qtbot.waitUntil(lambda: window.topbar.fresh.text() == "No reading for 1m 15s", timeout=2000)


def test_fresh_reading_shows_updated_time(window, state):
    at = state.latest()["at"]
    state.fresh = {"status": "live", "age_s": 0.3, "platform_up": True}
    state.snapshot_changed.emit()
    assert window.topbar.fresh.text() == f"Updated {fmt.fmt_time(at)}"
    assert not window.topbar.stale


def test_no_reading_yet(window, state):
    state.fresh = {"status": "no_readings", "age_s": None, "platform_up": True}
    state.snapshot_changed.emit()
    assert window.topbar.fresh.text() == "No reading yet"
    assert window.topbar.stale


def test_receiver_down_and_checking_in(window, state):
    state.fresh = {"status": "live", "age_s": 1, "platform_up": False}
    state.snapshot_changed.emit()
    assert window.topbar.fresh.text() == "Receiver not reachable"
    state.fresh = {"status": "checking_in", "age_s": None, "platform_up": True}
    state.mode = "calibrating"
    state.snapshot_changed.emit()
    assert window.topbar.fresh.text() in ("Calibrating", "calibrating")
    assert not window.topbar.stale


def test_sim_chip_follows_simulated_flag(window, state):
    assert window.topbar.sim_chip.isVisible()
    state._records[-1] = dict(state._records[-1], simulated=False)
    state.snapshot_changed.emit()
    assert not window.topbar.sim_chip.isVisible()


def test_device_card(window):
    v = {k: lab.text() for k, lab in window.sidebar.card.values.items()}
    assert window.sidebar.card.name.text() == "effente-sim-01"
    assert v["mode"] == "watching"
    assert v["model"] == "conveyor, calibrated"
    assert v["receiver"] == "0.0.0.0:8000"
    assert v["records"] == "1 / s"
    assert v["thermal"] == "none"
    assert v["stored"] == "1,349"


def test_format_helpers():
    assert fmt.fmt_dur(5) == "5s"
    assert fmt.fmt_dur(60) == "1m 0s"
    recs = [{"at": f"2026-11-14T16:00:0{i}Z"} for i in range(3)]
    latest = {"heat": {"source": "camera", "frames": [{}, {}]}}
    assert fmt.thermal_rate(latest, recs, stale=False) == "2 frames / s"
    assert fmt.thermal_rate({"heat": {"source": "imu"}}, recs, stale=False) == "no camera"
    assert fmt.model_text(None) == "–"
