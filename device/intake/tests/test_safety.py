"""Tests for the no-setup checks (effente.safety), sensitivity, the settings endpoint, and training
long recordings from their per-minute summaries.

Run with:  python3 -m device.intake.tests.test_safety
"""

import json
import math
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np

from effente import features as F
from effente import safety as SF
from effente.trainer import load_for_training


def row(vel=1.0, total_g=0.03, imu=40.0, kurt=0.0, peak_g=0.1, snd=None, jolts=0.0, gravity=None):
    x = np.full(F.N_FEATURES, np.nan)
    for a in "xyz":
        x[F.INDEX[f"vib_rms_{a}"]] = math.log10(total_g / math.sqrt(3) + 1e-6)
    if gravity is not None:  # all of it on z
        x[F.INDEX["vib_gravity_x"]], x[F.INDEX["vib_gravity_y"]], x[F.INDEX["vib_gravity_z"]] = 0.0, 0.0, gravity
    x[F.INDEX["vib_velocity_mm_s"]] = vel
    x[F.INDEX["imu_temp_c"]] = imu
    x[F.INDEX["vib_kurtosis"]] = kurt
    x[F.INDEX["vib_peak_g"]] = math.log10(peak_g)
    x[F.INDEX["trend_shocks_10m"]] = jolts
    if snd is not None:
        x[F.INDEX["snd_level_db"]] = snd
    return x


def feed(s, seconds, start=0.0, **kw):
    """Rows 4 a second; returns the names that started and cleared."""
    started, cleared = [], []
    for k in range(int(seconds * 4)):
        a, b = s.update(start + k * 0.25, row(**kw) if not callable(kw.get("make")) else kw["make"](start + k * 0.25))
        started += [p["name"] for p in a]
        cleared += b
    return started, cleared


def test_a_universal_limit_needs_10_seconds_and_clears():
    s = SF.Safety()
    assert feed(s, 4, vel=14.0) == ([], [])  # a 4-second bump never shows
    feed(s, 10, start=4, vel=1.0)
    assert not s.problems()
    started, _ = feed(s, 12, start=20, vel=14.0)
    assert started == ["vibration_over_limit"] and SF.screen_text(s.problems()[0]) == "VIBRATION TOO HIGH"
    _, cleared = feed(s, 12, start=32, vel=1.0)
    assert cleared == ["vibration_over_limit"] and not s.problems()


def test_knocking_needs_impacts_and_real_peaks():
    s = SF.Safety()
    feed(s, 15, kurt=9.0, peak_g=0.1)  # impulsive but tiny: someone typing on the desk
    assert not s.problems()
    feed(s, 15, start=15, kurt=9.0, peak_g=0.8)
    assert [p["name"] for p in s.problems()] == ["knocking"]


def test_too_hot_heating_fast_and_jolts():
    s = SF.Safety()
    feed(s, 15, imu=104.0)
    assert "too_hot" in [p["name"] for p in s.problems()]
    s = SF.Safety()
    feed(s, 150, make=lambda t: row(imu=40.0 + 10.0 * t / 60))  # 10 C a minute
    assert "heating_fast" in [p["name"] for p in s.problems()]
    s = SF.Safety()
    feed(s, 15, jolts=6)
    assert [p["name"] for p in s.problems()] == ["repeated_jolts"]


def test_outside_normal_and_sensitivity():
    base = {"levels": {"vib_velocity_mm_s": 1.0, "vib_total_g": 0.03, "imu_temp_c": 45.0, "snd_level_db": 60.0}}
    s = SF.Safety(base)
    feed(s, 15, vel=1.6, total_g=0.045, imu=50.0, snd=63.0)  # 1.6x, +5 C, +3 dB: inside normal at sensitivity 0.5
    assert not s.problems(), s.problems()
    s.set_sensitivity(0.75)  # twice as sensitive: 1.5x, +7.5 C, +3 dB count
    feed(s, 15, start=15, vel=1.6, total_g=0.045, imu=50.0, snd=63.5)
    assert [p["name"] for p in s.problems()] == ["vibration_above_normal", "louder_than_normal"], s.problems()
    assert SF.screen_text(s.problems()[0]) == "VIBRATION TOO HIGH" and s.problems()[0]["value"] == 1.6
    s = SF.Safety(base, sensitivity=0.25)  # half as sensitive: 3x and +30 C needed
    feed(s, 15, vel=2.5, imu=70.0)
    assert not s.problems()
    feed(s, 15, start=15, vel=3.5, imu=80.0)
    assert {p["name"] for p in s.problems()} == {"vibration_above_normal", "hotter_than_normal"}
    assert SF.margins(0.5) == {"vibration": 2.0, "temperature": 15.0, "sound": 6.0, "model": 1.0}
    assert SF.margins(1.0) == {"vibration": 1.25, "temperature": 3.75, "sound": 1.5, "model": 0.25}
    assert SF.margins(0.0) == {"vibration": 5.0, "temperature": 60.0, "sound": 24.0, "model": 4.0}
    assert SF.margins(7) == SF.margins(1.0) and SF.margins(-1) == SF.margins(0.0)  # clamped


def test_a_sensor_that_goes_missing_clears_its_problem():
    s = SF.Safety({"levels": {"snd_level_db": 60.0}})
    feed(s, 15, snd=75.0)
    assert [p["name"] for p in s.problems()] == ["louder_than_normal"]
    feed(s, 15, start=15, snd=None)
    assert not s.problems()


def test_baseline_levels_dropped_limits_and_warnings():
    rng = np.random.default_rng(0)
    steady = np.array([row(vel=1.0 + 0.05 * rng.standard_normal(), imu=45.0) for _ in range(400)])
    b = SF.learn_baseline(steady, F.FEATURE_NAMES, running=np.ones(400, bool))
    assert abs(b["levels"]["vib_velocity_mm_s"] - 1.12) < 0.1 and not b["warnings"] and not b["dropped"]
    rough = np.array([row(vel=14.0, imu=45.0 + 20 * k / 400) for k in range(400)])  # over 11 mm/s, warming up
    b = SF.learn_baseline(rough, F.FEATURE_NAMES, running=np.r_[np.ones(100, bool), np.zeros(300, bool)])
    assert b["dropped"] == ["vibration_over_limit"], b
    assert b["warnings_screen"] == ["MOSTLY STOPPED", "NORMAL OVER LIMIT", "STILL WARMING UP"], b
    assert len(b["warnings"]) == 3 and all(len(w) <= 19 for w in b["warnings_screen"])
    s = SF.Safety(b)  # this machine's normal is over the universal limit: it doesn't show for ever
    feed(s, 15, vel=14.0, imu=55.0)
    assert not s.problems(), s.problems()
    feed(s, 15, start=15, vel=30.0, imu=55.0)  # but twice its normal does
    assert [p["name"] for p in s.problems()] == ["vibration_above_normal"]


def test_verdict_says_what_is_going_on_not_the_cause():
    assert SF.verdict({"diagnosis": "normal"}, []) == {"wrong": False, "what": [], "details": []}
    assert SF.verdict({"diagnosis": "warming_up"}, [])["wrong"] is False
    # the cause the model guessed stays in the details; people are told what is going on
    v = SF.verdict({"diagnosis": "unbalance_like"}, [{"name": "too_hot"}])
    assert v == {"wrong": True, "what": ["unusual_vibration", "too_hot"], "details": ["unbalance_like", "too_hot"]}
    assert SF.say(v["what"]) == ["UNUSUAL VIBRATION", "TOO HOT"]
    for guess in ("unbalance_like", "misalignment_like", "bearing_defect_like"):  # look alike at 1 kHz: one description
        assert SF.verdict({"diagnosis": guess}, [])["what"] == ["unusual_vibration"]
    assert SF.verdict({"diagnosis": "excess_temperature_fluctuation"}, [])["what"] == ["temperature_swings"]
    assert SF.verdict({"diagnosis": "unrecognised_pattern"}, [])["what"] == ["outside_normal"]
    assert SF.verdict(None, [{"name": "knocking"}])["what"] == ["knocking_or_jolts"]


def test_the_same_thing_is_said_once():
    # the model and two checks all see the vibration up: one line, "vibration too high"
    v = SF.verdict({"diagnosis": "vibration_too_high"}, [{"name": "vibration_over_limit"}, {"name": "vibration_above_normal"}])
    assert v["what"] == ["vibration_too_high"]  # one description
    assert v["details"] == ["vibration_too_high", "vibration_over_limit", "vibration_above_normal"]  # the names, apart
    v = SF.verdict({"diagnosis": "unbalance_like"}, [{"name": "vibration_above_normal"}])  # too high covers unusual
    assert v["what"] == ["vibration_too_high"]
    v = SF.verdict(None, [{"name": "knocking"}, {"name": "repeated_jolts"}])
    assert v["what"] == ["knocking_or_jolts"]
    from effente import fault_types as FT
    assert set(FT.SYMPTOM) == set(FT.NAMES) | {"unrecognised_pattern"}  # every fault type has a description
    assert all(len(t) <= 19 for t, _ in FT.SYMPTOM_TEXT.values())  # and fits after "! "


def test_old_baseline_name_for_the_vibration_limit_still_drops_it():
    # models saved before the rename list the limit as "vibration_too_high" in their baseline
    s = SF.Safety({"levels": {"vib_velocity_mm_s": 14.0}, "dropped": ["vibration_too_high"]})
    assert s.dropped == {"vibration_over_limit"}
    feed(s, 15, vel=14.0)
    assert not s.problems(), s.problems()


def _hour_file(folder, hour, minutes, rows_per_minute):
    names = np.array(F.FEATURE_NAMES)
    t_min = hour * 3600.0 + 60.0 * np.arange(minutes)
    X = np.zeros((rows_per_minute * minutes, F.N_FEATURES), np.float32)
    t0 = np.repeat(t_min, rows_per_minute) + np.tile(np.arange(rows_per_minute), minutes)
    med = np.ones((minutes, F.N_FEATURES), np.float32) * hour
    from effente.model_runtime import _version
    np.savez(Path(folder) / "features" / f"features_{hour}.npz", features_version=_version(), feature_names=names,
             X=X, t0=t0, minute_t0=t_min, **{f"minute_{k}": med for k in ("min", "mean", "median", "max")})


def test_long_recordings_train_from_minute_medians():
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "features").mkdir()
        for h in range(27):  # 27 hours: one-minute rows
            _hour_file(tmp, 400_000 + h, 60, 1)
        t, X, names, period = load_for_training(tmp)
        assert period == 60 and len(t) == 27 * 60 and X.shape == (27 * 60, F.N_FEATURES) and X[-1, 0] == 400_026
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "features").mkdir()
        _hour_file(tmp, 400_000, 30, 60)  # 30 minutes: the one-a-second rows
        t, X, names, period = load_for_training(tmp)
        assert period is None and len(t) == 30 * 60


def test_device_shows_problems_and_takes_sensitivity_from_the_stream_and_the_file():
    from device.intake.device_app import DeviceApp
    from device.intake.stream import Stream
    from effente.simulate_conveyor import ConveyorSim

    class Clock:
        t = 1000.0

        def __call__(self):
            return self.t

    with tempfile.TemporaryDirectory() as tmp:
        clock = Clock()
        stream = Stream("127.0.0.1", 0, status_every_s=60).start()
        try:
            app = DeviceApp(tmp, ConveyorSim(days=1), stream=stream, clock=clock)
            for k in range(60):
                clock.t = 1000.0 + k * 0.25
                app._row(clock.t, row(vel=14.0))
            lines = app.screen_lines()
            assert lines[0] == "@!VIBRATION TOO HIGH" and lines[2] == "vib", lines  # inverted header, vib tile
            st = app.status()
            assert st["wrong"] and st["what"] == ["vibration_too_high"] and st["sensitivity"] == 0.5
            assert st["details"] == ["vibration_over_limit"]  # the check's own name, for the data
            url = f"http://127.0.0.1:{stream.port}/settings"
            got = json.loads(urllib.request.urlopen(url, timeout=5).read())
            assert got["ok"] and got["sensitivity"] == 0.5 and got["counts_as_wrong"]["vibration_times_normal"] == 2.0
            req = urllib.request.Request(url, data=json.dumps({"sensitivity": 0.75}).encode(), method="POST",
                                         headers={"Content-Type": "application/json"})
            got = json.loads(urllib.request.urlopen(req, timeout=5).read())
            assert got["sensitivity"] == 0.75 and app.safety.margins["vibration"] == 1.5
            assert json.loads((Path(tmp) / "settings.json").read_text()) == {"sensitivity": 0.75}
            bad = urllib.request.Request(url, data=b'{"sensitivity": 1.5}', method="POST")
            try:
                urllib.request.urlopen(bad, timeout=5)
                raise AssertionError("a sensitivity of 1.5 was accepted")
            except urllib.error.HTTPError as e:
                assert e.code == 400
            (Path(tmp) / "settings.json").write_text('{"sensitivity": 0.25}')  # by hand, e.g. over adb
            app._settings_seen = None
            app._load_settings()
            assert app.sensitivity == 0.25 and app.safety.margins["vibration"] == 3.0
        finally:
            stream.stop()


def test_corrupted_samples_say_sensor_problem_not_vibration_too_high():
    # 12 Nov on the board: a sensor buffer overflow put samples out of step. At rest the device saw
    # 0.1-2.2 g of "gravity" and up to 2 g of "vibration", and said VIBRATION TOO HIGH.
    rng = np.random.default_rng(1)
    s = SF.Safety()
    feed(s, 20, make=lambda t: row(vel=float(rng.uniform(15, 200)), total_g=float(rng.uniform(0.3, 3)),
                                   gravity=float(rng.uniform(0.1, 2.2)), kurt=9.0, peak_g=8.0))
    assert [p["name"] for p in s.problems()] == ["sensor_problem"], s.problems()
    assert SF.screen_text(s.problems()[0]) == "SENSOR PROBLEM"
    v = SF.verdict({"diagnosis": "unbalance_like"}, s.problems())  # the model, judging bad data, is set aside
    assert v["wrong"] and v["what"] == ["sensor_problem"] and v["details"] == ["sensor_problem"]
    # the fix went in: clean data at rest clears it, and nothing else shows
    _, cleared = feed(s, 15, start=20, vel=0.4, total_g=0.005, gravity=1.018)
    assert cleared == ["sensor_problem"] and not s.problems()


def test_a_working_sensor_and_a_piezo_sensor_are_fine():
    s = SF.Safety()
    feed(s, 15, vel=0.4, total_g=0.005, gravity=1.018)  # the MPU at rest after the fix
    assert not s.problems()
    feed(s, 15, start=15, vel=0.4, total_g=0.005, gravity=0.93)  # tilted or offset a little: still fine
    assert not s.problems()
    piezo = SF.Safety(gravity_check=False)  # industrial sensors remove gravity and read 0 g
    feed(piezo, 15, vel=0.4, total_g=0.005, gravity=0.0)
    assert not piezo.problems()


def test_a_frozen_or_mad_sensor_is_a_sensor_problem():
    s = SF.Safety()
    feed(s, 15, total_g=0.0, gravity=1.0)  # no noise at all on any axis: stuck or disconnected
    assert [p["name"] for p in s.problems()] == ["sensor_problem"]
    s = SF.Safety()
    feed(s, 15, imu=-60.0, gravity=1.0)  # outside the IMU's rated range
    assert [p["name"] for p in s.problems()] == ["sensor_problem"]


def test_learning_on_bad_sensor_data_is_warned_and_piezo_data_is_not():
    rng = np.random.default_rng(2)
    bad = np.array([row(gravity=float(rng.uniform(0.1, 2.2))) for _ in range(400)])
    b = SF.learn_baseline(bad, F.FEATURE_NAMES)
    assert "SENSOR PROBLEM" in b["warnings_screen"] and "sensor_problem" not in b["dropped"], b
    piezo = np.array([row(gravity=0.0) for _ in range(400)])  # like the NASA bearing data
    assert "SENSOR PROBLEM" not in SF.learn_baseline(piezo, F.FEATURE_NAMES)["warnings_screen"]
    good = np.array([row(gravity=1.02) for _ in range(400)])
    assert not SF.learn_baseline(good, F.FEATURE_NAMES)["warnings"]


def test_inference_screen_puts_a_sensor_problem_before_the_models_diagnosis():
    from device.intake.device_app import DeviceApp
    from effente.simulate_conveyor import ConveyorSim
    with tempfile.TemporaryDirectory() as tmp:
        app = DeviceApp(tmp, ConveyorSim(days=1), clock=lambda: 1000.0)
        app.mode = "watching"
        app.result = {"diagnosis": "unbalance_like", "features": {"vib_1x": 4.2}}
        app.problems = [{"name": "sensor_problem", "value": 0.4}]
        lines = app.screen_lines()
        assert lines[0] == "@!SENSOR PROBLEM" and not any("UNBALANCE" in x for x in lines), lines


def test_described_straight_from_what_moved():
    from effente import fault_types as FT
    assert FT.describe({"vib_rms_x": 8, "vib_band_07": 8, "vib_band_10": -8, "vib_band_12": 8}) == [("vibration_too_high", 8.0)]
    assert FT.describe({"vib_kurtosis": 6, "vib_crest": 5, "vib_peak_g": 4})[0][0] == "knocking_or_jolts"
    assert FT.describe({"trend_heat_swing_10m": 5, "th_change_c": 4})[0][0] == "temperature_swings"
    assert FT.describe({"vib_speed_hz": 6, "trend_speed_1h": 4}) == [("speed_changed", 5.0)]  # a real speed change
    assert FT.describe({"vib_rms_x": 1.0}) == [("outside_normal", 0.0)]  # nothing moved enough
    assert len(FT.describe({"vib_rms_x": 8, "vib_kurtosis": 7, "imu_temp_c": 6, "snd_level_db": 5})) == FT.SAY_AT_MOST


def test_guards_against_misleading_speed_and_moved_descriptions():
    from effente import fault_types as FT
    # a real ball-bearing fault (Ottawa, bearing 12): the spectrum changed everywhere, the speed reading
    # jumped to a new peak, but the shaft speed did not change
    said = FT.describe({"vib_rms_x": -8, "vib_band_13": -8, "vib_band_16": -8, "vib_1x": -8, "vib_speed_hz": 7})
    assert [s for s, _ in said] == ["unusual_vibration"], said
    # heavy vibration and clipping shift the gravity reading: not "device moved"
    said = FT.describe({"vib_rms_x": 8, "vib_gravity_x": 6, "vib_clip_frac": 5})
    assert "device_moved" not in [s for s, _ in said], said
    assert FT.describe({"vib_gravity_x": 6})[0][0] == "device_moved"  # tilted, nothing else: it was moved


def test_the_verdict_uses_the_models_description():
    v = SF.verdict({"diagnosis": "misalignment_like", "symptoms": ["knocking_or_jolts"]}, [{"name": "too_hot"}])
    assert v["what"] == ["knocking_or_jolts", "too_hot"] and v["details"] == ["misalignment_like", "too_hot"]
    v = SF.verdict({"diagnosis": "unbalance_like"}, [])  # an older state without symptoms: from the fault type
    assert v["what"] == ["unusual_vibration"]


if __name__ == "__main__":
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok    {name}")
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(f"FAIL  {name}: {type(e).__name__}: {e}")
    print("all passed" if not failed else f"{failed} failed")
    sys.exit(1 if failed else 0)
