"""Tests for the early warning (effente.early_warning) and how the device shows it.

A small model is trained on made-up normal rows (one a second, so a block is a minute); then small
lasting changes are fed through the device's own Watcher. No generated data needed.

Run with:  python3 -m device.intake.tests.test_early_warning
"""

import sys
import tempfile

import numpy as np

from effente import early_warning as EW
from effente import fault_types as FT
from effente import features as F
from effente import model_runtime as RT
from effente.simulate_conveyor import ConveyorSim
from effente.trainer import train_model
from device.intake.device_app import DeviceApp

HOT = ("imu_temp_c", "th_max_c", "th_mean_c", "env_hot_above_room_c")
_CACHE = {}


def rows(n, t0=0.0, seed=1):
    rng = np.random.default_rng(seed)
    X = 10 + rng.normal(0, 1, (n, F.N_FEATURES))
    X[:, F.INDEX["trend_running"]] = 1
    if "vib_shock_axis" in F.INDEX:
        X[:, F.INDEX["vib_shock_axis"]] = rng.integers(0, 3, n)
    return t0 + np.arange(n, dtype=float), X


def model():
    if not _CACHE:
        t, X = rows(1800, seed=0)
        d, _ = train_model(t, X, list(F.FEATURE_NAMES), period=1, time_limit_s=3)
        m = RT.Model(d)
        _CACHE.update(d=d, m=m, calib=EW.calibrate(m, t, X))
    return _CACHE["m"], _CACHE["calib"]


def feed(m, ew, t, X, stop=None):
    """Through the device's Watcher, as the device does. Returns (events with times, alarm periods)."""
    w = RT.Watcher(m)
    out, alarms = [], 0
    for k, (tk, x) in enumerate(zip(t, X)):
        w._judge(tk, x)
        st = w.state
        wrong = st["diagnosis"] not in ("normal", "warming_up", "stopped")
        alarms += wrong
        running = st["diagnosis"] != "stopped"
        out += [(tk, n, d) for n, d in ew.observe(tk, running, row=x, alarm=wrong)]
    return out, alarms


def hot(X, by):
    X = X.copy()
    X[:, [F.INDEX[n] for n in HOT]] += by
    return X


def test_tests_on_a_few_values():
    assert EW.mann_kendall_z(np.arange(10.0)) > 3
    assert abs(EW.mann_kendall_z([1, 2, 1, 2, 1, 2, 1, 2])) < 1
    assert np.isnan(EW.mann_kendall_z([1, 2]))
    assert abs(EW.theil_sen([0, 2, 4, 6, 100]) - 2.0) < 1e-9
    assert EW.scale(0.5) == 1.0 and EW.scale(1.0) == 0.5 and EW.scale(0.0) == 2.0


def test_every_symptom_has_a_short_form_that_fits_the_screen():
    for sym in list(EW.GROUP_NAMES) + ["outside_normal"]:
        assert sym in FT.SYMPTOM_TEXT and sym in EW.SHORT, sym
        for s in EW.SHORT[sym]:
            assert len(f"EARLY: {s}") <= 19, s


def test_calibration_covers_every_series():
    m, calib = model()
    assert calib["series"] == list(EW.SERIES) and calib["blocks_learned"] == 30
    assert all(v is not None and v > 0 for v in calib["spread"])


def test_normal_says_nothing():
    m, calib = model()
    t, X = rows(1800, 5000, seed=2)
    # not "line" or "sensitivity": a plain fraction of the model's line, and these made-up rows' scores
    # sit at about 0.9 of it (a line set for one false alarm a week of independent noise is close to the
    # usual score), so a lower line is crossed all the time here
    for method in [x for x in EW.METHODS if x not in ("line", "sensitivity")]:
        ev, _ = feed(m, EW.EarlyWarning(m, calib, method=method), t, X)
        assert not [e for e in ev if e[1] == "early_warning"], (method, ev[:2])


def test_a_small_lasting_change_is_named_before_any_alarm_and_clears_when_it_goes():
    m, calib = model()
    t, X = rows(3600, 5000, seed=3)
    X = np.vstack([hot(X[:1800], 1.0), X[1800:]])  # half an hour a little warmer, then back to normal
    ew = EW.EarlyWarning(m, calib, method="cusum")  # per-group naming: the CUSUM detector (no longer the default)
    ev, alarms = feed(m, ew, t, X)
    assert alarms == 0  # too small for the model's alarm
    on = [e for e in ev if e[1] == "early_warning"]
    assert on and on[0][2]["symptom"] == "too_hot" and on[0][0] - 5000 <= 5 * 60, on[:1]
    d = on[0][2]
    assert d["text"].startswith("TOO HOT ") and d["screen"].startswith("EARLY: ") and len(d["screen"]) <= 19
    assert "speed" in d and "fail" not in str(d).lower()  # no countdown
    cleared = [e for e in ev if e[1] == "early_warning_cleared"]
    assert cleared and cleared[0][0] > 5000 + 1800 and cleared[0][2]["reason"] == "the change went away"
    assert not ew.state()["on"]


def test_creeping_up_says_so_with_a_coarse_speed():
    m, calib = model()
    t, X = rows(60 * 20, 5000, seed=4)  # 20 blocks, the readings rising throughout
    ramp = np.linspace(0.0, 3.0, len(X))[:, None]
    X[:, [F.INDEX[n] for n in HOT]] += ramp
    ew = EW.EarlyWarning(m, calib, method="cusum")
    ev, _ = feed(m, ew, t, X)
    said = [e[2] for e in ev if e[1] == "early_warning"]
    assert said and said[0]["symptom"] == "too_hot", said
    assert any(s["rising"] and s["text"] == "TOO HOT CREEPING UP" and s["speed"] for s in said), said
    assert ew.screen() == "EARLY: HEATING UP" or ew.alarm


def test_the_default_is_system_cs_line():
    from effente import trainer as TR
    assert EW.DEFAULT_METHOD == "line" and EW.LINE_BLOCK == 30 and EW.LINE_HOLD == 6
    assert abs(EW.LINE_FRAC * TR.THRESHOLD_FACTOR - 0.3) < 1e-12  # 0.3 x the crossfit line, which models store x factor
    m, calib = model()
    assert EW.EarlyWarning(m, calib).block == 30 and EW.EarlyWarning(m, calib, method="cusum").block == calib["block"]


def test_the_line_needs_six_blocks_over_its_fraction_and_scales_with_sensitivity():
    n = len(EW.SERIES)
    for s, frac in ((0.5, EW.LINE_FRAC), (1.0, EW.LINE_FRAC / 2), (0.0, EW.LINE_FRAC * 2)):
        assert abs(EW.LINE_FRAC * EW.scale(s) - frac) < 1e-12
        det = EW.Line(n, frac=frac)
        on = [bool(det.step_raw(1.05 * frac)[EW.SCORE]) for _ in range(EW.LINE_HOLD)]
        assert on == [False] * (EW.LINE_HOLD - 1) + [True], on  # on at the sixth block in a row
        assert not det.step_raw(0.95 * frac)[EW.SCORE]  # off as soon as a block falls back


def test_steps_aside_while_the_alarm_is_on():
    m, calib = model()
    t, X = rows(600, 5000, seed=5)
    ew = EW.EarlyWarning(m, calib)
    for tk, x in zip(t[:300], hot(X[:300], 1.5)):
        ew.observe(tk, True, row=x, alarm=False)
    assert ew.state()["on"]
    ev = ew.observe(t[300], True, row=X[300], alarm=True)
    assert ev and ev[0][0] == "early_warning_cleared" and ev[0][1]["reason"] == "the model's alarm is on"
    st = ew.state()
    assert not st["on"] and st.get("note", "").startswith("set aside")
    assert ew.screen() is None


def test_stopped_periods_are_skipped_and_sensitivity_scales_how_soon():
    m, calib = model()
    t, X = rows(600, 5000, seed=6)
    ew = EW.EarlyWarning(m, calib)
    for tk, x in zip(t, X):
        ew.observe(tk, False, row=x)
    assert ew.blocks == 0
    first = {}
    for s in (0.25, 0.5, 1.0):
        ew = EW.EarlyWarning(m, calib, sensitivity=s)
        ev, _ = feed(m, ew, t, hot(X, 0.6))
        on = [e[0] for e in ev if e[1] == "early_warning"]
        first[s] = on[0] if on else np.inf
    assert first[1.0] <= first[0.5] <= first[0.25] and first[1.0] < np.inf, first


def test_an_uncalibrated_model_says_why_it_is_off():
    m, _ = model()
    ew = EW.EarlyWarning(m, None)
    t, X = rows(200, 5000)
    assert not any(ew.observe(tk, True, row=x) for tk, x in zip(t, hot(X, 3)))
    assert not ew.state()["on"] and "not calibrated" in ew.state()["note"]


class FakeStream:
    def __init__(self):
        self.events = []

    def publish_event(self, t, name, details=None):
        self.events.append((name, details))

    def publish_features(self, *a, **k):
        return True

    def publish_result(self, *a, **k):
        pass


def test_the_device_shows_it_on_the_screen_status_and_stream():
    m, calib = model()
    with tempfile.TemporaryDirectory() as tmp:
        stream = FakeStream()
        app = DeviceApp(tmp, ConveyorSim(days=1), stream=stream)
        app.mode = "watching"
        app.watcher = app._watcher(m, calib)
        t, X = rows(400, 5000, seed=7)
        for tk, x in zip(t, hot(X, 1.0)):
            app.watcher.predict_at(tk, x)
            app.result = app.watcher.state
        lines = app.screen_lines()
        assert lines[0].startswith("@EARLY: ") and lines[2] == "", lines  # design B: the early warning in the header
        assert all(len(x) <= 24 for x in lines)
        st = app.status()
        assert st["early_warning"]["on"] and st["early_warning"]["symptom"] == "too_hot" and not st["wrong"], st
        assert any(n == "early_warning" and d["symptom"] == "too_hot" for n, d in stream.events), stream.events
        app.settings({"sensitivity": 0.9})
        assert app.early.sensitivity == 0.9
        app.on_button()  # relearning: what it had seen no longer counts
        assert not app.early.state()["on"]


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print("ok  ", name)
        except Exception as e:  # noqa: BLE001
            failed += 1
            print("FAIL", name, type(e).__name__, e)
    print(f"{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
