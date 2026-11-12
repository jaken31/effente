"""Tests for data intake and feature processing.

Run with pytest, or without it:  python3 -m device.intake.tests.test_intake
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

from effente import features as F
from device.intake import extract as E
from device.intake.live import ModelSlot, run_live
from device.intake.recorder import Recorder, recover
from device.intake.source import FileSource, Packet, SourceInfo
from device.intake.windowing import Windower

RNG = np.random.default_rng(0)
_TMP = tempfile.TemporaryDirectory()
_CLIPS: dict = {}


def clip(start: str, minutes: int) -> str:
    """A fake recording, generated once per test run."""
    key = (start, minutes)
    if key not in _CLIPS:
        d = Path(_TMP.name) / f"clip_{start.replace(':', '')}_{minutes}"
        subprocess.run([sys.executable, "-m", "effente.simulate_day", "--out", str(d), "--start", start,
                        "--minutes", str(minutes), "--workers", "1"], check=True, capture_output=True)
        _CLIPS[key] = str(d)
    return _CLIPS[key]


LOADER = '''
import time
import numpy as np
FEATURES_VERSION = "{version}"
class Model:
    def __init__(self, delay):
        self.delay = delay
    def predict(self, x):
        if self.delay:
            time.sleep(self.delay)
        return {{"mean": float(np.nanmean(x))}}
def load(path):
    return Model(float(open(path).read()) if path else 0.0)
'''


def write_loader(folder, version=None) -> Path:
    p = Path(folder) / "test_model.py"
    p.write_text(LOADER.format(version=version or E.features_version()))
    return p


def accel_window(freq=25.0, amp=0.05, rate=1000.0):
    t = np.arange(int(rate)) / rate
    a = RNG.normal(0, 0.002, (len(t), 3))
    a[:, 0] += amp * np.sin(2 * np.pi * freq * t)
    a[:, 2] += 1.0
    return a


# ----------------------------------------------------------------- features

def test_vibration_finds_speed_and_1x():
    x = F.extract(F.Window(0.0, accel_g=accel_window(25.0, 0.05)))
    assert abs(x[F.INDEX["vib_speed_hz"]] - 25.0) < 0.5
    assert abs(x[F.INDEX["vib_1x"]] - np.log10(0.05)) < 0.1
    assert abs(x[F.INDEX["vib_gravity_z"]] - 1.0) < 0.01


def test_sound_tone_lands_in_its_band():
    t = np.arange(16000) / 16000
    x = F.extract(F.Window(0.0, audio=3000 * np.sin(2 * np.pi * 1000 * t)))
    bands = [x[F.INDEX[f"snd_band_{i:02d}"]] for i in range(len(F.SND_EDGES_HZ) - 1)]
    k = int(np.argmax(bands))
    assert F.SND_EDGES_HZ[k] <= 1000 < F.SND_EDGES_HZ[k + 1]


def test_heat_hotspot():
    frames = np.full((4, 24, 32), 22.0)
    frames[:, 8:12, 12:16] += 10.0  # exactly grid cell row 3, column 4
    x = F.extract(F.Window(0.0, thermal_c=frames))
    assert abs(x[F.INDEX["th_hotspot_c"]] - 10.0) < 1e-6
    assert abs(x[F.INDEX["th_cell_23"]] - 10.0) < 1e-6


def test_room_sensor_and_heat_above_room():
    frames = np.full((4, 24, 32), 22.0)
    frames[:, 0, 0] = 40.0
    x = F.extract(F.Window(0.0, thermal_c=frames, room=np.array([[21.0, 40.0], [21.2, 42.0]])))
    assert abs(x[F.INDEX["env_room_c"]] - 21.1) < 1e-9
    assert abs(x[F.INDEX["env_humidity_pct"]] - 41.0) < 1e-9
    assert abs(x[F.INDEX["env_hot_above_room_c"]] - 18.9) < 1e-9


def test_missing_sensor_is_nan():
    x = F.extract(F.Window(0.0, accel_g=accel_window()))
    assert F.missing_sensors(x) == ["rotation", "sound", "heat", "room"]
    assert not np.isnan(x[F.SLICES["vibration"]]).any()


# ----------------------------------------------------------------- windowing

def test_windower_waits_for_bursty_sensors():
    """A 10-second burst from one sensor must not make the others look missing."""
    wd = Windower(hop_s=1.0, sensors=("accel", "audio"))
    ta = np.arange(0, 10, 0.001)
    tb = np.arange(0, 10, 1 / 16000)
    wd.push(Packet("accel", ta, np.zeros((len(ta), 3))))
    assert list(wd.ready()) == []  # audio has not arrived yet: wait, do not drop it
    wd.push(Packet("audio", tb, np.zeros(len(tb))))
    wins = list(wd.ready())
    assert len(wins) == 9 and all(parts["audio"] is not None for _, parts in wins)


def test_windower_moves_on_when_a_sensor_dies():
    wd = Windower(hop_s=1.0, sensors=("accel", "audio"))
    ta = np.arange(0, 40, 0.001)
    tb = np.arange(0, 5, 1 / 16000)  # audio stops after 5 s
    wd.push(Packet("audio", tb, np.zeros(len(tb))))
    wd.push(Packet("accel", ta, np.zeros((len(ta), 3))))
    wins = list(wd.ready())
    assert len(wins) >= 20  # did not stall waiting for audio
    assert wins[2][1]["audio"] is not None and wins[10][1]["audio"] is None


def test_windows_overlap_four_times_a_second():
    wd = Windower(sensors=("accel",))  # defaults: 1 s windows, a new one every 0.25 s
    ta = np.arange(0, 5, 0.001)
    wd.push(Packet("accel", ta, ta[:, None] * np.ones((1, 3))))
    wd.finish()
    wins = list(wd.ready())
    starts = [t0 for t0, _ in wins]
    assert np.allclose(np.diff(starts), 0.25) and starts[0] == 0.0
    a, b = wins[4][1]["accel"][0], wins[5][1]["accel"][0]  # windows starting at 1.00 s and 1.25 s
    assert len(a) == len(b) == 1000
    assert abs(a[0] - 1.0) < 1e-9 and abs(b[0] - 1.25) < 1e-9
    assert len(np.intersect1d(np.round(a, 6), np.round(b, 6))) == 750  # 0.75 s shared


def test_windower_aligns_and_reports_missing():
    wd = Windower(hop_s=1.0, timeout_s=1.0)
    ta = np.arange(0, 3, 0.001)
    tb = np.arange(0, 3, 1 / 16000)
    tr = np.arange(0.4, 3, 1.0)
    wd.push(Packet("accel", ta, np.zeros((len(ta), 3))))
    wd.push(Packet("audio", tb, np.zeros(len(tb))))
    wd.push(Packet("room", tr, np.zeros((len(tr), 2))))
    wd.finish()
    wins = list(wd.ready())
    assert [round(t0) for t0, _ in wins] == [0, 1, 2]
    for _, parts in wins:
        assert len(parts["accel"][0]) == 1000
        assert len(parts["audio"][0]) == 16000
        assert len(parts["room"][0]) == 1
        assert parts["thermal"] is None


# ----------------------------------------------------------------- end to end

def test_clip_to_feature_file():
    d = clip("05:44", 4)
    if True:
        out = Path(d) / "features.npz"
        assert E.main(["--data", d, "--out", str(out)]) == 0
        r = np.load(out)
        X, t0 = r["X"], r["t0"]
        assert X.shape[1] == F.N_FEATURES and 950 <= len(X) <= 960  # 4 windows a second for 4 minutes
        assert float(r["window_s"]) == 1.0 and float(r["hop_s"]) == 0.25
        assert list(r["feature_names"]) == F.FEATURE_NAMES
        assert str(r["features_version"]) == E.features_version()
        assert np.allclose(np.diff(t0), 0.25)
        assert not np.isnan(X[:, F.BASE_SLICE]).any()  # all four sensors present in the fake data
        assert np.isnan(X[0, F.HISTORY_SLICE]).all()  # no history yet at the very start
        assert not np.isnan(X[-1, F.INDEX["trend_heat_1m"]])  # a minute of history by the end
        assert set(r["label"]) == {"normal"}
        assert {"off", "starting", "running"} <= set(r["truth_state"])  # this clip covers a start-up
        speed = X[:, F.INDEX["vib_speed_hz"]]
        running = r["truth_state"] == "running"
        assert np.all(np.abs(speed[running][20:] - 29.5) < 0.6)  # found the pump's running speed
        one = Path(d) / "one_per_second.npz"
        assert E.main(["--data", d, "--out", str(one), "--hop", "1"]) == 0
        r1 = np.load(one)
        assert 235 <= len(r1["X"]) <= 240 and np.allclose(np.diff(r1["t0"]), 1.0)
        # Overlapping windows are the same windows, just more of them: every
        # one-per-second row, history features included, appears unchanged in
        # the four-per-second file. This is what keeps training and live in step.
        idx = np.searchsorted(t0, r1["t0"])
        assert np.allclose(X[idx], r1["X"], equal_nan=True)


# ----------------------------------------------------------------- learn mode

def test_recorder_writes_hours_minutes_snippets_and_events():
    d = clip("05:58", 4)  # crosses 06:00, so two hour files
    with tempfile.TemporaryDirectory() as out:
        out = Path(out)
        src = FileSource(d)
        rec = Recorder(out, lambda: src.info, src.sensors, snippet_every_s=60, snippet_len_s=10)
        for t0, x in E.feature_stream(src, tap=rec.on_packet):
            rec.on_window(t0, x)
        rec.close()

        hours = sorted((out / "features").glob("features_*.npz"))
        assert [h.name for h in hours] == ["features_00005.npz", "features_00006.npz"]
        assert not list((out / "features").glob("*.partial.npz"))
        a, b = np.load(hours[0]), np.load(hours[1])
        assert len(a["X"]) == 120 and len(b["X"]) == 120  # one row per second kept
        assert a["t0"].max() < 6 * 3600 <= b["t0"].min()
        assert np.allclose(np.diff(a["t0"]), 1.0)
        assert list(a["minute_t0"] / 60) == [358, 359] and list(b["minute_t0"] / 60) == [360, 361]
        mn, mean, mx = a["minute_min"], a["minute_mean"], a["minute_max"]
        ok = np.isnan(mean) | ((mn <= mean + 1e-6) & (mean <= mx + 1e-6))  # history features may be empty
        assert ok.all() and not np.isnan(mean[:, F.BASE_SLICE]).any()

        snippets = sorted((out / "raw").glob("clip_*.npz"))
        assert len(snippets) == 4
        # A raw snippet replays to exactly the features that were recorded live.
        replay = {round(t, 3): x for t, x in E.feature_stream(FileSource(out / "raw"), hop_s=1.0)}
        rows = np.vstack([a["X"], b["X"]])
        t0s = np.concatenate([a["t0"], b["t0"]])
        matched = [(replay[round(t, 3)], x) for t, x in zip(t0s, rows) if round(t, 3) in replay]
        assert len(matched) >= 36  # 4 snippets x 9-10 full windows
        b = F.BASE_SLICE  # history features need continuous data, which snippets are not
        assert all(np.allclose(r[b], x[b], atol=1e-4, equal_nan=True) for r, x in matched)

        events = [json.loads(line)["event"] for line in open(out / "events.jsonl")]
        assert events[0] == "recording_started" and events[-1] == "recording_stopped"
        assert "sensor_lost" not in events and "device_moved" not in events


def test_power_cut_loses_at_most_a_minute():
    with tempfile.TemporaryDirectory() as out:
        rec = Recorder(out, lambda: SourceInfo(), ("accel",), snippet_every_s=0, checkpoint_s=60)
        x = np.zeros(F.N_FEATURES)
        for i in range(130 * 4):  # 130 s of windows, then the power goes (no close)
            rec.on_window(1000 + i * 0.25, x)
        assert len(list(Path(out, "features").glob("*.partial.npz"))) == 1
        assert recover(out) == ["features_00000.npz"]
        kept = np.load(Path(out, "features", "features_00000.npz"))
        assert len(kept["X"]) >= 120  # the checkpoint at 120 s survived


def test_device_moved_event():
    with tempfile.TemporaryDirectory() as out:
        rec = Recorder(out, lambda: SourceInfo(), ("accel",), snippet_every_s=0)
        x = np.zeros(F.N_FEATURES)
        for i in range(200):
            x[F.INDEX["vib_gravity_z"]] = 1.0 if i < 100 else 0.9  # tilted after 25 s
            rec.on_window(i * 0.25, x.copy())
        rec.close()
        events = [json.loads(line)["event"] for line in open(Path(out) / "events.jsonl")]
        assert events.count("device_moved") == 1


# ----------------------------------------------------------------- watch mode

def test_no_model_features_still_flow():
    stats = run_live(FileSource(clip("05:44", 4)), ModelSlot(), console=False)
    assert stats.produced == stats.scored >= 950 and stats.dropped == 0


def test_live_feeds_every_window_to_the_model():
    with tempfile.TemporaryDirectory() as tmp:
        slot = ModelSlot(write_loader(tmp))
        assert slot.status == "ready"
        log = Path(tmp) / "results.jsonl"
        stats = run_live(FileSource(clip("05:44", 4)), slot, log_path=log, console=False, queue_size=2000)
        lines = [json.loads(line) for line in open(log)]
        assert stats.dropped == 0 and len(lines) == stats.produced >= 950
        assert lines[0]["status"] == "warming_up" and lines[-1]["status"] == "ready"
        assert "mean" in lines[-1]["result"] and lines[-1]["missing"] == []


def test_live_refuses_a_model_trained_on_other_features():
    with tempfile.TemporaryDirectory() as tmp:
        slot = ModelSlot(write_loader(tmp, version="0123456789ab"))
        assert slot.status == "refused" and "0123456789ab" in slot.error
        stats = run_live(FileSource(clip("05:44", 4)), slot, console=False)
        assert stats.scored == stats.produced  # the device keeps running without it


def test_slow_model_drops_oldest_windows_not_sensor_data():
    with tempfile.TemporaryDirectory() as tmp:
        model_file = Path(tmp) / "model.txt"
        model_file.write_text("0.02")  # 20 ms per window: slower than intake replays
        stats = run_live(FileSource(clip("05:44", 4)), ModelSlot(write_loader(tmp), model_file),
                         console=False, queue_size=5)
        assert stats.produced >= 950  # every window was still made
        assert stats.dropped > 0 and stats.scored + stats.dropped == stats.produced


def test_model_slot_reloads_when_the_file_changes():
    with tempfile.TemporaryDirectory() as tmp:
        model_file = Path(tmp) / "model.txt"
        model_file.write_text("0.0")
        slot = ModelSlot(write_loader(tmp), model_file, check_every_s=0)
        assert slot.loads == 1 and not slot.maybe_reload()
        model_file.write_text("0.001")
        st = model_file.stat()
        os.utime(model_file, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))
        assert slot.maybe_reload() and slot.loads == 2 and slot.model.delay == 0.001


# ----------------------------------------------------------------- model input

def test_model_input_is_bounded_has_no_nan_and_round_trips():
    from effente.model_input import CLIP, Prep, day_split, persistent
    rng = np.random.default_rng(0)
    n = 4000
    X = np.full((n, F.N_FEATURES), np.nan)
    X[:, F.INDEX["vib_rms_x"]] = rng.normal(-2, 0.1, n)
    X[:, F.INDEX["vib_kurtosis"]] = rng.normal(0, 1, n)
    X[:, F.INDEX["vib_shock_axis"]] = rng.integers(0, 3, n)
    X[:, F.INDEX["trend_cell_rise_1h_vs_24h_at"]] = rng.integers(0, 48, n)
    X[:, F.INDEX["trend_since_shock_s"]] = np.where(rng.random(n) < 0.5, np.nan, rng.random(n) * 1e5)
    X[:, F.INDEX["trend_heat_7d"]] = np.where(np.arange(n) > 3500, 5.0, np.nan)  # mostly NaN in training
    X[:, F.INDEX["trend_running"]] = 1.0
    t0 = np.arange(n, dtype=float) * 60
    prep = Prep.fit(X, F.FEATURE_NAMES, t0)
    assert "trend_cell_rise_1h_vs_24h_at" not in prep.columns and "trend_heat_7d" not in prep.columns
    assert "vib_shock_axis_z" in prep.names_out and "missing_sound" in prep.names_out
    Y = X[:10].copy()
    Y[0, F.INDEX["vib_rms_x"]] = 50.0  # far outside training
    Y[1, F.INDEX["vib_rms_x"]] = np.nan  # accelerometer value missing
    Z = prep.transform(Y)
    assert Z.shape == (10, prep.n_out) and not np.isnan(Z).any() and np.abs(Z).max() <= CLIP
    assert prep.out_of_range(Y)[0] == 1
    with tempfile.TemporaryDirectory() as tmp:
        prep.save(Path(tmp) / "prep.json")
        assert np.array_equal(Prep.load(Path(tmp) / "prep.json").transform(Y), Z)
    train, test = day_split(t0, test_days=1)
    assert not (train & test).any() and t0[train].max() < t0[test].min() - 3000
    assert persistent([1] * 8 + [0] * 4, k=8, n=12)[7] and not persistent([1, 0] * 6, k=8, n=12).any()


# ----------------------------------------------------------------- fixes from the code review

def test_restart_within_an_hour_keeps_the_rows_before_it():
    from device.intake.recorder import Recorder
    from device.intake.source import SourceInfo
    with tempfile.TemporaryDirectory() as out:
        def run(a, b):
            rec = Recorder(out, SourceInfo, ("accel",), snippet_every_s=0)
            for t in range(a, b):
                rec.on_window(float(t), np.zeros(F.N_FEATURES))
            rec.close()
        run(0, 1200)  # stops at 00:20 ...
        run(1500, 3600)  # ... and starts again at 00:25, in the same hour
        with np.load(Path(out) / "features" / "features_00000.npz") as d:
            assert len(d["t0"]) == 1200 + 2100 and d["t0"][0] == 0


def test_storage_thins_hours_with_wall_clock_numbers():
    from device.intake.storage import StorageManager
    with tempfile.TemporaryDirectory() as out:
        (Path(out) / "features").mkdir()
        (Path(out) / "raw").mkdir()
        for h in (492000, 492100):  # Unix-time hours have 6 digits
            np.savez_compressed(Path(out) / "features" / f"features_{h}.npz", X=np.random.rand(3600, 8),
                                t0=h * 3600.0 + np.arange(3600.0))
        sm = StorageManager(out, max_gb=0.0001)
        actions = sm.check(492100 * 3600.0)
        assert any(a.startswith("thinned features_492000") for a in actions)


def test_windower_skips_a_clock_jump_quickly():
    import time as _t
    from device.intake.source import Packet
    from device.intake.windowing import Windower
    wd = Windower(sensors=("accel",))
    for t in (0.0, 1.0, 2.0):
        wd.push(Packet("accel", t + np.arange(1000) / 1000, np.zeros((1000, 3))))
    list(wd.ready())
    jump = 1.7e9  # the clock set from 1970 to today
    for t in (jump, jump + 1, jump + 2):
        wd.push(Packet("accel", t + np.arange(1000) / 1000, np.zeros((1000, 3))))
    started = _t.perf_counter()
    got = list(wd.ready())
    assert _t.perf_counter() - started < 1.0 and len(got) < 20  # not 7 billion empty windows
    assert [t for t, _ in got if t > 10][0] >= jump - 1  # picks up right where the data starts again


def test_results_log_has_the_trd_fields_and_survives_odd_results():
    from device.intake.live import log_record
    x = np.zeros(F.N_FEATURES)
    rec = json.loads(log_record(1.79e9, x, {"diagnosis": "normal", "unknown_score": np.float32(0.2),
                                             "scores": np.array([0.1, 0.9])}, "ready", [], 0.001, "effente-01", "fan-1"))
    assert rec["machine_id"] == "fan-1" and rec["diagnosis"] == "normal" and rec["at"].startswith("2026")
    assert abs(rec["unknown_score"] - 0.2) < 1e-6 and rec["result"]["scores"] == [0.1, 0.9] and len(rec["spectrum"]) == 20
    rec = json.loads(log_record(5.0, x, {"unknown_score": float("nan")}, "ready", [], 0.0, "d", "m"))
    assert "unknown_score" not in rec and "error" in rec["result"]  # NaN is not valid JSON for the agent


def test_a_broken_new_model_keeps_the_old_one_running():
    with tempfile.TemporaryDirectory() as tmp:
        loader = write_loader(tmp)
        slot = ModelSlot(loader, check_every_s=0.0)
        assert slot.status == "ready"
        loader.write_text("this is not python (")  # a half-copied file
        os.utime(loader, ns=(1, 1))
        assert slot.maybe_reload() and slot.model is not None and slot.status == "ready"
        assert "previous one" in slot.error


def test_24_bit_audio_gives_the_same_level_and_is_not_wrapped_when_stored():
    from device.intake.recorder import _audio_ints
    t = np.arange(16000) / 16000
    tone = np.sin(2 * np.pi * 1000 * t)
    a16 = F.extract(F.Window(0.0, audio=np.round(0.1 * 32767 * tone)))
    a24 = F.extract(F.Window(0.0, audio=np.round(0.1 * 8388607 * tone), audio_full_scale=8388607.0))
    assert abs(a16[F.INDEX["snd_level_db"]] - a24[F.INDEX["snd_level_db"]]) < 0.01
    assert a24[F.INDEX["snd_clip_frac"]] == 0
    stored = _audio_ints(np.array([9e6, -9e6, 1000.0]), 24)
    assert stored.dtype == np.int32 and stored[0] == 8388607 and stored[1] == -8388608


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
