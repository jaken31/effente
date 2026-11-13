"""The short test on the real board, without the board: IMU only (no microphone, no thermal camera),
idle -> a press -> recording for about 5 minutes -> a press -> training -> watching.

  - the header says IDLE, then RECORDING, then TRAINING n%, then what watching shows; never a complaint
    about how much was recorded or about a sensor that isn't fitted
  - it trains even when the device sat on something still (no row counts as "running")
  - what is recorded is never smoothed: the one-a-second rows, the minute summaries and the raw clip
    equal what the raw samples give, window by window

Run with:  python3 -m device.intake.tests.test_board_flow
"""

import json
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

from effente import features as F
from device.intake.device_app import DeviceApp
from device.intake.extract import to_window
from device.intake.source import Packet, SourceInfo

RATE, BATCH, LSB = 1000, 25, 4096.0
T0 = 1_700_000_000 - 1_700_000_000 % 600 - 100  # 100 s before a 10-minute mark, so a raw clip is kept
OK_HEADERS = ("@IDLE", "@RECORDING", "@TRAINING ", "@INFERENCE", "@WARMING UP", "@MACHINE STOPPED")


class Clock:
    t = float(T0)

    def __call__(self):
        return self.t


class FakeBoard:
    """A source like UnoQSource (accel 1 kHz, gyro 100 Hz, the IMU's temperature; nothing else), fed from
    made-up counts. `level_mg(second)` gives the vibration (a 60 Hz shake on top of 3 mg of sensor noise);
    `presses` are seconds after the start at which the button is pressed. Keeps every count it sent."""

    sensors = ("accel", "gyro", "imu_temp")

    def __init__(self, seconds, level_mg, presses=(), seed=0):
        self.seconds, self.level_mg, self.presses = seconds, level_mg, list(presses)
        self.info = SourceInfo(accel_lsb_per_g=LSB, accel_range_g=32768.0 / LSB)
        self.rng = np.random.default_rng(seed)
        self.app, self.clock = None, None
        self.counts, self.times, self.headers = [], [], []
        self.press_modes = []  # the mode after each press

    def packets(self):
        for s in range(self.seconds):
            self.clock.t = T0 + s
            if self.presses and s >= self.presses[0]:
                self.presses.pop(0)
                self.app.on_button()
                self.press_modes.append(self.app.mode)
            while True:  # the trainer runs in its own thread: wait for it, watching the screen meanwhile
                self.headers.append(self.app.screen_lines()[0])
                if self.app.mode != "training":
                    break
                time.sleep(0.02)
            n = np.arange(RATE)
            t = T0 + s + n / RATE
            g = np.zeros((RATE, 3))
            g[:, 2] = 1.018
            g += self.rng.normal(0, 0.003, g.shape)
            g[:, 0] += self.level_mg(s) / 1000.0 * np.sqrt(2) * np.sin(2 * np.pi * 60 * (s + n / RATE))
            counts = np.clip(np.round(g * LSB), -32768, 32767).astype(np.int16)
            self.counts.append(counts)
            self.times.append(t)
            acc = counts.astype(np.float32) / LSB  # as UnoQSource: counts / 4096, nothing else
            gyro = self.rng.normal(0, 0.05, (RATE, 3)).astype(np.float32)
            for a in range(0, RATE, BATCH):
                yield Packet("accel", t[a:a + BATCH], acc[a:a + BATCH])
                yield Packet("gyro", t[a:a + BATCH:10], gyro[a:a + BATCH:10])
                yield Packet("imu_temp", t[a + BATCH - 1:a + BATCH], np.array([[28.0]], np.float32))


def flow(tmp, seconds, level_mg, presses):
    clock, shown = Clock(), []
    board = FakeBoard(seconds, level_mg, presses)
    app = DeviceApp(tmp, board, screen=shown.append, clock=clock)
    board.app, board.clock = app, clock
    first = app.screen_lines()[0]
    app.run()
    time.sleep(1.2)  # the screen worker sends the last layout
    headers = [first] + board.headers + [s.split("\n")[0] for s in shown]
    return app, board, headers


def test_still_device_idle_record_train_watch_with_no_complaints_on_the_screen():
    """The device on something that doesn't vibrate, after it has seen both shaking and stillness (so the
    still rows count as "not running"): 4.5 minutes of recording still trains, and the screen never complains."""
    with tempfile.TemporaryDirectory() as tmp:
        rec0, stop = 11 * 60, 11 * 60 + 270
        level = lambda s: 80.0 if s < 6 * 60 else 0.0  # 6 minutes shaken, then left alone
        app, board, headers = flow(tmp, stop + 60, level, presses=(rec0, rec0 + 60, stop))
        assert headers[0] == "@IDLE", headers[0]
        assert board.press_modes == ["recording", "recording", "training"], board.press_modes  # 1 min: too soon to stop
        assert "learn 2 min" in app.message or app.mode == "watching"
        assert app.mode == "watching", (app.mode, app.message)
        seen = sorted(set(headers))
        assert all(h.startswith(OK_HEADERS) for h in seen), seen  # never "learn ...", "too little ...", "no ... data"
        for want in ("@IDLE", "@RECORDING", "@TRAINING ", "@MACHINE STOPPED"):
            assert any(h.startswith(want) for h in seen), (want, seen)
        order = [next(i for i, h in enumerate(headers) if h.startswith(w)) for w in ("@IDLE", "@RECORDING", "@TRAINING ")]
        assert order == sorted(order), order
        d = json.loads(app.model_path.read_text())
        rep = d["training_report"]
        assert rep["learned_from_all_rows"] and rep["period_s"] == 1 and 230 <= rep["rows_learned"] + rep["rows_calibration"] <= 271, rep
        assert rep["line"].startswith("tail") and rep["patterns_kept"] >= 1 and np.isfinite(rep["threshold"]), rep
        assert any("counted as running" in w for w in rep["warnings"]), rep["warnings"]
        # the notes about the learning are in status, for us, and not on the screen
        assert app.status().get("learning_notes") and d["baseline"]["warnings_screen"], app.status()
        assert not any(n in h for n in d["baseline"]["warnings_screen"] for h in headers)
        # only the IMU: nothing recorded for the sensors that aren't fitted, and the rest is there
        from effente.model_input import load_recording
        X, t, names = load_recording(app.recording)
        assert np.isnan(X[:, F.SLICES["sound"]]).all() and np.isnan(X[:, F.SLICES["heat"]]).all()
        assert np.isfinite(X[:, F.INDEX["vib_rms_x"]]).all() and np.isfinite(X[:, F.INDEX["imu_temp_c"]]).all()


def test_running_device_trains_from_four_minutes_and_shows_live_readings_while_recording():
    """Never seen stopped (the usual case on a machine): 4 minutes is enough, and while recording the layout
    still has the header, the time and the speed (vibration, peak and temperature are drawn by the sketch)."""
    with tempfile.TemporaryDirectory() as tmp:
        app, board, headers = flow(tmp, 20 + 240 + 20, lambda s: 40.0, presses=(20, 260))
        assert app.mode == "watching" and not app.message, (app.mode, app.message)
        assert all(h.startswith(OK_HEADERS) for h in set(headers)), sorted(set(headers))
        rep = json.loads(app.model_path.read_text())["training_report"]
        assert not rep["learned_from_all_rows"] and rep["rows_learned"] >= 190, rep
        lines = app.screen_lines()
        assert len(lines) == 4 and lines[1][2] == ":" and lines[3] not in ("", None), lines


def test_recorded_rows_minutes_and_clips_are_the_unsmoothed_samples():
    """One second of hard shaking in a quiet recording: it is in that second's row at full size and gone from
    the next; every recorded row equals the features of its own second of raw samples; the minute summaries
    are the plain statistics of the minute's rows; the raw clip holds the counts as they came."""
    with tempfile.TemporaryDirectory() as tmp:
        burst = 130
        app, board, _ = flow(tmp, 200, lambda s: 300.0 if s == burst else 5.0, presses=(10,))
        with np.load(next((app.recording / "features").glob("features_*.npz"))) as d:  # run() closed the recorder
            X, t = d["X"], d["t0"]
            minute_t, mmax, mmed, mmean = d["minute_t0"], d["minute_max"], d["minute_median"], d["minute_mean"]
        counts, times = np.concatenate(board.counts), np.concatenate(board.times)
        acc = counts.astype(np.float32) / LSB
        assert len(t) >= 185 and np.allclose(t, np.round(t)) and np.all(np.diff(t) == 1.0), (len(t), t[:3])

        def raw_row(t0):  # the features of [t0, t0 + 1) from the counts, with no state from any other window
            i0, i1 = np.searchsorted(times, [t0, t0 + 1.0])
            tt = times[i0:i1]
            parts = {"accel": (tt, acc[i0:i1]), "gyro": None, "imu_temp": (tt[-1:], np.array([[28.0]], np.float32))}
            return F.extract(to_window(t0, parts, board.info))

        vib = F.SLICES["vibration"]
        for k in list(range(0, len(t), 7)) + [int(np.argmax(t == T0 + burst))]:
            want = raw_row(float(t[k]))[vib].astype(np.float32)
            assert np.array_equal(X[k, vib], want, equal_nan=True), (k, t[k], np.nanmax(np.abs(X[k, vib] - want)))
        rms = X[:, F.INDEX["vib_rms_x"]]  # log10 g
        b = int(np.argmax(t == T0 + burst))
        assert t[b] == T0 + burst and abs(10 ** rms[b] - 0.300) < 0.01, 10 ** rms[b]  # the burst, at full size
        assert 10 ** rms[b - 1] < 0.01 and 10 ** rms[b + 1] < 0.01, (rms[b - 1], rms[b + 1])  # and nothing either side
        # the minute summaries: the plain max, median and mean of that minute's whole-second windows
        m = int((T0 + burst) // 60) * 60
        j = int(np.argmax(minute_t == m))
        wins = np.array([raw_row(m + float(q))[vib] for q in range(60)], np.float64)
        col = F.INDEX["vib_rms_x"]
        for got, want in ((mmax, np.nanmax(wins, 0)), (mmed, np.nanmedian(wins, 0)), (mmean, np.nanmean(wins, 0))):
            assert abs(float(got[j, col]) - float(want[col])) < 1e-4, (got[j, col], want[col])
        assert 10 ** mmax[j, col] > 0.29 and 10 ** mmed[j, col] < 0.01  # the burst is in the max, not spread about
        # the raw clip at the 10-minute mark: the counts exactly
        clips = sorted((app.recording / "raw").glob("clip_*.npz"))
        assert clips, "no raw clip kept"
        with np.load(clips[0]) as c:
            i0 = int(np.searchsorted(times, c["accel_t_us"][0] / 1e6 - 1e-7))
            assert len(c["samples"]) >= 9000 and np.array_equal(c["samples"], counts[i0:i0 + len(c["samples"])])


if __name__ == "__main__":
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                started = time.time()
                fn()
                print(f"ok    {name} ({time.time() - started:.0f} s)")
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(f"FAIL  {name}: {type(e).__name__}: {e}")
    print("all passed" if not failed else f"{failed} failed")
    sys.exit(1 if failed else 0)
