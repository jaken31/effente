"""Tests for the device's modes: display -> recording -> training -> watching, with the button.

Run with:  python3 -m device.intake.tests.test_device_app
"""

import json
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

from effente import features as F
from effente.simulate_conveyor import ConveyorSim
from device.intake.device_app import DeviceApp
from device.intake.extract import feature_stream

START = 6 * 3600 + 10 * 60  # Monday 06:10, the conveyor is running


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


def run(app, clock, rows, press_at=(), until_mode=None):
    """Feed rows to the app as its pipeline would, pressing the button at the given row times."""
    presses = list(press_at)
    for t0, x in rows:
        clock.t = t0
        if presses and t0 >= presses[0]:
            presses.pop(0)
            app.on_button()
        app._row(t0, x)
        if app.mode == "training":  # the trainer runs in its own thread: wait for it
            deadline = time.time() + 300
            while app.mode == "training" and time.time() < deadline:
                time.sleep(0.05)
        if until_mode and app.mode == until_mode:
            return


def rows(start, minutes, fault=None):
    sim = ConveyorSim(days=1, start_s=start, end_s=start + minutes * 60, faults=[])
    for t0, x in feature_stream(sim, hop_s=1.0):
        if fault is not None:
            x = x.copy()
            fault(x)
        yield t0, x


def test_display_record_train_watch_and_resume():
    with tempfile.TemporaryDirectory() as tmp:
        shown = []
        clock = Clock()
        app = DeviceApp(tmp, ConveyorSim(days=1), screen=shown.append, clock=clock)
        assert app.mode == "display"
        run(app, clock, rows(START, 14), press_at=(START + 60, START + 13 * 60))
        assert app.mode == "watching", (app.mode, app.message)
        assert (Path(tmp) / "models").exists() and json.loads((Path(tmp) / "state.json").read_text())["mode"] == "watching"
        lines = app.screen_lines()
        assert lines[0] == "@INFERENCE" and lines[1][:2].isdigit() and len(lines) == 4, lines  # design B: header, time, tile, speed
        assert not any("button" in x or "btn" in x for x in lines)
        # a fault after learning: the once-per-turn vibration four times larger
        run(app, clock, rows(START + 14 * 60, 3, fault=lambda x: x.__setitem__(F.INDEX["vib_1x"], x[F.INDEX["vib_1x"]] + 0.6)))
        assert app.result["diagnosis"] not in ("normal", "warming_up"), app.result
        # a power cut: a new app on the same folder carries on watching with the saved model
        again = DeviceApp(tmp, ConveyorSim(days=1), clock=clock)
        assert again.mode == "watching" and again.watcher is not None
        time.sleep(0.6)  # the screen worker sends what was shown last
        assert any(s.startswith("@RECORDING") for s in shown) and any(s.startswith("@INFERENCE") for s in shown)


def test_too_short_a_recording_keeps_learning_and_the_press_file_works():
    with tempfile.TemporaryDirectory() as tmp:
        clock = Clock()
        app = DeviceApp(tmp, ConveyorSim(days=1), clock=clock)
        (Path(tmp) / "press").touch()  # a press from the computer, until the button is wired
        run(app, clock, rows(START, 2), press_at=(START + 90,))
        assert app.mode == "recording" and "learn 5 min" in app.message, (app.mode, app.message)


def test_rows_kept_on_whole_seconds_even_when_windows_start_off_the_second():
    from device.intake.recorder import Recorder
    from device.intake.source import SourceInfo
    from effente.model_input import load_recording
    from effente.trainer import summarise
    with tempfile.TemporaryDirectory() as tmp:
        rec = Recorder(tmp, SourceInfo, ("accel",), snippet_every_s=0)
        for k in range(1, 4 * 400):  # 4 windows a second, the first at 0.25 s past a whole second
            rec.on_window(1000.0 + 0.25 * k, np.zeros(F.N_FEATURES))
        rec.close()
        X, t, _ = load_recording(tmp)
        assert len(t) >= 398 and np.allclose(t, np.round(t))  # one row a second, on whole seconds
        off = t + 0.25  # rows from an older recording, a quarter-second off: the trainer still uses them
        assert len(summarise(off, X, 1)[0]) == len(t) and len(summarise(off, X, 10)[0]) >= 39


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
