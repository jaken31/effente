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


def row(vel=1.0, total_g=0.03, imu=40.0, kurt=0.0, peak_g=0.1, snd=None, jolts=0.0):
    x = np.full(F.N_FEATURES, np.nan)
    for a in "xyz":
        x[F.INDEX[f"vib_rms_{a}"]] = math.log10(total_g / math.sqrt(3))
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
    assert started == ["vibration_over_limit"] and SF.screen_text(s.problems()[0]) == "VIB OVER LIMIT"
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
    assert SF.screen_text(s.problems()[0]) == "VIB 1.6x NORMAL"
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


def test_verdict_is_wrong_or_not_and_what():
    assert SF.verdict({"diagnosis": "normal"}, []) == {"wrong": False, "what": []}
    assert SF.verdict({"diagnosis": "warming_up"}, []) == {"wrong": False, "what": []}
    assert SF.verdict({"diagnosis": "unbalance"}, [{"name": "too_hot"}]) == {"wrong": True, "what": ["unbalance", "too_hot"]}
    assert SF.verdict(None, [{"name": "knocking"}]) == {"wrong": True, "what": ["knocking"]}
    # the model's fault type and the universal limit have different names, so both read clearly
    assert SF.verdict({"diagnosis": "vibration_too_high"}, [{"name": "vibration_over_limit"}]) == \
        {"wrong": True, "what": ["vibration_too_high", "vibration_over_limit"]}


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
            assert lines[0].startswith("STREAMING") and "! VIB OVER LIMIT" in lines, lines
            st = app.status()
            assert st["wrong"] and st["what"] == ["vibration_over_limit"] and st["sensitivity"] == 0.5
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
