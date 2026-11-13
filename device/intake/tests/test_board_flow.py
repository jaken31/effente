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
OK_HEADERS = ("@IDLE", "@RECORDING", "@TRAINING ", "@DETECTING", "@WARMING UP", "@MACHINE STOPPED")


class Clock:
    t = float(T0)

    def __call__(self):
        return self.t


class FakeBoard:
    """A source like UnoQSource (accel 1 kHz, gyro 100 Hz, the IMU's temperature; nothing else), fed from
    made-up counts. `level_mg(second)` gives the vibration (a 60 Hz shake on top of 3 mg of sensor noise);
    `presses` are seconds after the start at which the button is pressed. Keeps every count it sent."""

    sensors = ("accel", "gyro", "imu_temp")

    def __init__(self, seconds, level_mg, presses=(), seed=0, knock_g=None, at=None):
        self.seconds, self.level_mg, self.presses = seconds, level_mg, list(presses)
        self.knock_g = knock_g  # knock_g(second) -> the size of a 30 ms knock 0.3 s into that second (0: none)
        self.at = dict(at or {})  # {second: fn(app)}: something done to the app at that second
        self.problems = []  # (second, names of the problems the checks have on), once a second
        self.results = []  # (second, the model's own result: diagnosis, symptoms, spike ...), once a second
        self.info = SourceInfo(accel_lsb_per_g=LSB, accel_range_g=32768.0 / LSB)
        self.rng = np.random.default_rng(seed)
        self.app, self.clock = None, None
        self.counts, self.times, self.headers = [], [], []
        self.press_modes = []  # the mode after each press
        self.seen = []  # with a stream: (mode, state, readings, feature rows published so far), once a second

    def packets(self):
        for s in range(self.seconds):
            if self.app is not None:  # without an app it is only a source of packets (test_model_spike)
                self.clock.t = T0 + s
            if s in self.at:
                self.at[s](self.app)
            if self.presses and s >= self.presses[0]:
                self.presses.pop(0)
                self.app.on_button()
                self.press_modes.append(self.app.mode)
            while self.app is not None:  # the trainer runs in its own thread: wait for it, watching the screen meanwhile
                self.headers.append(self.app.screen_lines()[0])
                if self.app.stream is not None:  # what the local network sees, once a second
                    st = self.app.stream.status()
                    self.seen.append((st["mode"], st["state"], st["readings"], st["stream"]["published"]["features"]))
                if self.app.mode != "training":
                    break
                time.sleep(0.02)
            n = np.arange(RATE)
            t = T0 + s + n / RATE
            g = np.zeros((RATE, 3))
            g[:, 2] = 1.018
            g += self.rng.normal(0, 0.003, g.shape)
            g[:, 0] += self.level_mg(s) / 1000.0 * np.sqrt(2) * np.sin(2 * np.pi * 60 * (s + n / RATE))
            k = self.knock_g(s) if self.knock_g else 0.0
            if k:
                ph = np.arange(30) / RATE
                g[300:330, 2] += k * np.sin(2 * np.pi * 180 * ph) * np.exp(-ph / 0.01)
            if self.app is not None:
                self.problems.append((s, [p["name"] for p in self.app.problems]))
                self.results.append((s, dict(self.app.result or {})))
            counts = np.clip(np.round(g * LSB), -32768, 32767).astype(np.int16)
            self.counts.append(counts)
            self.times.append(t)
            acc = counts.astype(np.float32) / LSB  # as UnoQSource: counts / 4096, nothing else
            gyro = self.rng.normal(0, 0.05, (RATE, 3)).astype(np.float32)
            for a in range(0, RATE, BATCH):
                yield Packet("accel", t[a:a + BATCH], acc[a:a + BATCH])
                yield Packet("gyro", t[a:a + BATCH:10], gyro[a:a + BATCH:10])
                yield Packet("imu_temp", t[a + BATCH - 1:a + BATCH], np.array([[28.0]], np.float32))


def flow(tmp, seconds, level_mg, presses, stream=None, knock_g=None, at=None):
    clock, shown = Clock(), []
    board = FakeBoard(seconds, level_mg, presses, knock_g=knock_g, at=at)
    app = DeviceApp(tmp, board, screen=shown.append, clock=clock, stream=stream)
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
        assert rep["patterns_kept"] >= 1 and np.isfinite(rep["threshold"]), rep
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


def test_a_coin_after_a_five_minute_recording_shows_as_knocking_and_clears():
    """The demo: 5 minutes of learning, 10 untouched minutes, then a knock every 4 s (a coin on the belt) for a
    minute, then the coin out. The impact counter learned from the short recording stays off while untouched and
    through one bump, is on by the third knock, and clears within about a minute of the coin coming out."""
    with tempfile.TemporaryDirectory() as tmp:
        rec0, rec1 = 20, 320
        bump, coin0, coin1, end = rec1 + 300, rec1 + 600, rec1 + 660, rec1 + 760
        knock = lambda s: 0.15 if s == bump or (coin0 <= s < coin1 and (s - coin0) % 4 == 0) else 0.0
        app, board, headers = flow(tmp, end, lambda s: 30.0, presses=(rec0, rec1), knock_g=knock)
        d = json.loads(app.model_path.read_text())
        sp = d["baseline"]["spikes"]
        assert sp["short"] and sp["window_s"] == 60 and {"vib_kurtosis", "vib_peak_g"} <= set(sp["features"]), sp
        on = {s: "more_impacts" in names for s, names in board.problems}
        assert not any(on[s] for s in range(rec1, coin0)), [s - rec1 for s in range(rec1, coin0) if on[s]][:5]
        first = next((s for s in range(coin0, coin1) if on[s]), None)
        assert first is not None and first - coin0 <= 15, first  # knocks at 0, 4 and 8 s: on by the third
        assert all(on[s] for s in range(first, coin1)), "went off while the coin was in"
        assert not on[end - 1] and any(h == "@!KNOCKING OR JOLTS" for h in headers), sorted(set(headers))


def test_something_falling_off_shows_at_once_and_the_sensitivity_decides_how_big():
    """After a 5-minute recording at 30 mg (peaks of about 0.05 g): a 0.2 g knock (3x the highest while learning)
    shows within 2 s as KNOCKING OR JOLTS and clears about 30 s later; a 0.11 g one (about 1.6x) doesn't at the
    default sensitivity, and does once the sensitivity is set to 0.75 through the settings (POST /settings)."""
    from device.intake.stream import Stream
    with tempfile.TemporaryDirectory() as tmp:
        stream = Stream(port=0).start()
        try:
            rec1 = 320
            big, small, change, small2, end = rec1 + 60, rec1 + 130, rec1 + 180, rec1 + 200, rec1 + 260
            knock = lambda s: {big: 0.2, small: 0.11, small2: 0.11}.get(s, 0.0)
            app, board, headers = flow(tmp, end, lambda s: 30.0, presses=(20, rec1), knock_g=knock, stream=stream,
                                       at={change: lambda app: app.settings({"sensitivity": 0.75})})
            assert "peak_max" in json.loads(app.model_path.read_text())["baseline"]["spikes"]
            on = {s: "sudden_impact" in names for s, names in board.problems}
            first = next(s for s in range(rec1, end) if on[s])
            assert big < first <= big + 3, first - big  # the row of the knock's second is ready a second later
            assert not any(on[s] for s in range(rec1, big)) and not any(on[s] for s in range(big + 35, small2))
            assert all(on[s] for s in range(first, big + 30)) and not on[big + 33]  # held about 30 s, then clear
            assert any(on[s] for s in range(small2 + 1, small2 + 4)), "not shown at 0.75"
            assert any(h == "@!KNOCKING OR JOLTS" for h in headers), sorted(set(headers))
            events = [e.payload for e in stream._rings["events"]]
            hits = [e["details"] for e in events if e["event"] == "problem" and e["details"]["name"] == "sudden_impact"]
            assert len(hits) == 2 and 0.14 < hits[0]["peak_g"] < 0.19 and 2.5 < hits[0]["value"] < 3.5, hits
            assert app.settings()["counts_as_wrong"]["sudden_impact_times_highest_while_learning"] == 1.5
            # the model's own alarm reacted too: that one second's score over its spike line, said at once
            spikes = [s for s, r in board.results if r.get("spike")]
            assert spikes and big < spikes[0] <= big + 3 and "knocking_or_jolts" in dict(board.results)[spikes[0]]["symptoms"]
        finally:
            stream.stop()


def test_the_stream_has_the_state_and_live_readings_in_every_mode():
    """What the local network gets (port 8765): "state" is IDLE, RECORDING, TRAINING or DETECTING beside the
    contract's unchanged "mode", and the readings and a feature row a second arrive in every one of them."""
    from device.intake.stream import Stream
    with tempfile.TemporaryDirectory() as tmp:
        stream = Stream(port=0, status_every_s=1.0).start()
        try:
            app, board, _ = flow(tmp, 30 + 240 + 40, lambda s: 40.0 + 10 * np.sin(s / 5), presses=(30, 270), stream=stream)
            names = {"display": "IDLE", "recording": "RECORDING", "training": "TRAINING", "watching": "DETECTING"}
            assert {m: s for m, s, _, _ in board.seen} == names, {m: s for m, s, _, _ in board.seen}
            for mode in ("display", "recording", "watching"):
                rows = [(r, n) for m, _, r, n in board.seen if m == mode][3:]  # after the first seconds of data
                assert len(rows) >= 20, (mode, len(rows))
                assert all(r["vib_mg"] is not None and r["peak_g"] is not None and r["imu_temp_c"] is not None
                           and r["speed_hz"] is not None for r, _ in rows), (mode, rows[0])
                assert len({r["vib_mg"] for r, _ in rows}) > len(rows) // 2, mode  # they move: live, not a held value
                per_s = (rows[-1][1] - rows[0][1]) / (len(rows) - 1)
                assert 0.9 <= per_s <= 1.1, (mode, per_s)  # a feature row a second on the stream
            latest = json.loads(stream.latest_json())
            assert latest["status"]["state"] == "DETECTING" and latest["status"]["mode"] == "watching"
            assert len(latest["features"]["values"]) == F.N_FEATURES and latest["features"]["missing"] == ["sound", "heat", "room"]
            assert latest["results"] is not None  # while detecting, the results line too
            import urllib.request
            with urllib.request.urlopen(f"http://127.0.0.1:{stream.port}/view", timeout=5) as r:  # the page for a browser
                page = r.read().decode()
                assert r.headers["Content-Type"].startswith("text/html") and "/latest" in page and "s.state" in page
        finally:
            stream.stop()


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
