"""The device itself: one button, four modes, the screen and the live stream.

    plug in -> DISPLAY --button--> RECORDING --button--> TRAINING --done--> WATCHING --button--> RECORDING ...

  DISPLAY     reads the sensors and shows the readings (vibration, peaks, speed, IMU temperature)
  RECORDING   learn mode: keeps the feature rows, per-minute summaries, raw snippets and events
              (device.intake.recorder); the screen shows how long it has been learning
  TRAINING    trains this machine's model on the recording, on the device (effente.trainer, NumPy
              only); the screen shows progress. A recording that is too short is refused and
              learning carries on
  WATCHING    checks every summary period for faults (effente.model_runtime.Watcher) and shows the
              diagnosis; pressing the button again starts a new recording, to relearn

All the time, sensor data goes to the screen and to whoever connects to the live stream
(device.intake.stream: results, feature rows, events, status). After a power cut it resumes what
it was doing: still watching with the saved model, still recording into the same folder.
Until the button is wired, creating a file named `press` in the data folder counts as a press.
"""

from __future__ import annotations

import json
import math
import threading
import time
from pathlib import Path

import numpy as np

from effente import features as F
from effente.history import History
from effente.model_input import load_recording
from effente.model_runtime import Model, Watcher
from effente.trainer import train_model
from device.intake.extract import feature_stream
from device.intake.live import log_record
from device.intake.recorder import Recorder

MODES = ("display", "recording", "training", "watching")
SCREEN_NAMES = {"display": "STREAMING", "recording": "RECORDING", "training": "TRAINING",
                "watching": "INFERENCE"}  # what the screen calls each mode
SCREEN_EVERY_S = 0.5  # how often the layout is checked for changes
HISTORY_SAVE_EVERY_S = 300.0
MIN_RECORD_S = 300.0  # learning needs at least 5 minutes of the machine running


def _g(x, name):
    v = x[F.INDEX[name]] if x is not None else np.nan
    return float(v) if np.isfinite(v) else None


def readings(x) -> dict:
    """The few numbers shown on the screen, from one feature row."""
    rms = [_g(x, f"vib_rms_{a}") for a in "xyz"]
    total = math.sqrt(sum(10 ** (2 * r) for r in rms)) if all(r is not None for r in rms) else None
    peak = _g(x, "vib_peak_g")
    return {"vib_mg": None if total is None else round(1000 * total, 1),
            "peak_g": None if peak is None else round(10 ** peak, 3),
            "speed_hz": _g(x, "vib_speed_hz"), "imu_temp_c": _g(x, "imu_temp_c"),
            "tilt": [_g(x, f"vib_gravity_{a}") for a in "xyz"]}


def _clock(seconds: float) -> str:
    s = int(max(seconds, 0))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60:02d}:{s % 60:02d}"


def _wrap(text: str, width: int = 21) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + (1 if cur else 0) > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}" if cur else w
    return lines + [cur] if cur else lines


class DeviceApp:
    def __init__(self, data_dir, source, screen=None, stream=None, device_id: str = "effente-01",
                 machine_id: str = "machine-1", hop_s: float = 0.25, min_record_s: float = MIN_RECORD_S,
                 max_gb: float | None = None, min_free_gb: float = 0.5, clock=time.time):
        self.dir = Path(data_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.source, self.screen, self.stream = source, screen, stream
        self.device_id, self.machine_id, self.hop_s = device_id, machine_id, hop_s
        self.min_record_s, self.max_gb, self.min_free_gb = min_record_s, max_gb, min_free_gb
        self.clock = clock
        self.lock = threading.RLock()
        self.mode = "display"
        self.recorder: Recorder | None = None
        self.recording: Path | None = None
        self.record_started: float | None = None
        self.watcher: Watcher | None = None
        self.model_path: Path | None = None
        self.result: dict | None = None
        self.train_stage, self.train_share, self.message = "", 0.0, ""
        self.latest, self.latest_t = None, None
        self.last_press = -math.inf
        self.history = History.load(self.dir / "history.npz")
        self._last_history_save = None
        self._screen_text, self._screen_event = None, threading.Event()
        self._stop = threading.Event()
        self._resume()
        if self.stream is not None:
            self.stream.status_provider = self.status
        if self.screen is not None:
            threading.Thread(target=self._screen_worker, name="screen", daemon=True).start()

    # ------------------------------------------------------------ state that survives a power cut
    def _save_state(self):
        state = {"mode": self.mode, "recording": str(self.recording) if self.recording else None,
                 "record_started": self.record_started, "model": str(self.model_path) if self.model_path else None}
        tmp = self.dir / "state.json.tmp"
        tmp.write_text(json.dumps(state))
        tmp.replace(self.dir / "state.json")

    def _resume(self):
        p = self.dir / "state.json"
        state = json.loads(p.read_text()) if p.exists() else {}
        mode = state.get("mode", "display")
        if state.get("model") and Path(state["model"]).exists():
            try:
                self.model_path = Path(state["model"])
                self.watcher = Watcher(Model.load(self.model_path))
            except Exception as e:  # noqa: BLE001  a model made with other features: learn again
                self.message = f"saved model not usable: {e}"
                self.watcher, self.model_path = None, None
        if mode == "watching" and self.watcher is not None:
            self.mode = "watching"
        elif mode in ("recording", "training") and state.get("recording"):
            self.recording = Path(state["recording"])
            self.record_started = state.get("record_started") or self.clock()
            if mode == "training":
                self._begin_training()
            else:
                self._open_recorder()
                self.mode = "recording"
        self._event("mode", {"mode": self.mode, "resumed": mode != "display"})

    # ------------------------------------------------------------ the button
    def on_button(self, *_):
        now = self.clock()
        with self.lock:
            if now - self.last_press < 1.0:  # one press, however bouncy
                return
            self.last_press = now
            if self.mode in ("display", "watching"):
                self._start_recording()
            elif self.mode == "recording":
                self._stop_recording_and_train()
            # while training, a press does nothing
            self._event("button", {"mode": self.mode})

    def _start_recording(self):
        self.recording = self.dir / "recordings" / time.strftime("rec-%Y%m%d-%H%M%S", time.localtime(self.clock()))
        self.record_started = self.clock()
        self.result, self.message = None, ""
        self._open_recorder()
        self.mode = "recording"
        self._save_state()
        self._event("mode", {"mode": "recording", "folder": self.recording.name})

    def _open_recorder(self):
        self.recorder = Recorder(self.recording, lambda: self.source.info, self.source.sensors, label="normal",
                                 hop_s=self.hop_s, max_gb=self.max_gb, min_free_gb=self.min_free_gb,
                                 history=self.history)

    def _stop_recording_and_train(self):
        took = self.clock() - (self.record_started or self.clock())
        if took < self.min_record_s:
            self.message = f"learn {int(self.min_record_s // 60)} min or more ({int(took // 60)} so far)"
            return
        self._close_recorder()
        self._begin_training()

    def _close_recorder(self):
        if self.recorder is not None:
            try:
                self.recorder.close()
            except Exception as e:  # noqa: BLE001
                self.message = f"closing the recording: {e}"
            self.recorder = None

    def _begin_training(self):
        self.mode = "training"
        self.train_stage, self.train_share = "starting", 0.0
        self._save_state()
        self._event("mode", {"mode": "training"})
        threading.Thread(target=self._train, name="train", daemon=True).start()

    def _train(self):
        try:
            X, t, names = load_recording(self.recording)

            def progress(stage, share):
                self.train_stage, self.train_share = stage, share

            d, report = train_model(t, X, names, source=str(self.recording), progress=progress)
            models = self.dir / "models"
            models.mkdir(exist_ok=True)
            path = models / f"{self.recording.name}.json"
            path.write_text(json.dumps(d))
            watcher = Watcher(Model(d))
            with self.lock:
                self.model_path, self.watcher, self.mode = path, watcher, "watching"
                self.message = ""
                self._save_state()
            self._event("model_trained", {"file": path.name, **report})
            self._event("mode", {"mode": "watching"})
        except Exception as e:  # noqa: BLE001  e.g. too little recorded: keep learning
            with self.lock:
                self.message = str(e)[:80]
                self._open_recorder()
                self.mode = "recording"
                self._save_state()
            self._event("training_failed", {"reason": str(e)})

    # ------------------------------------------------------------ the pipeline
    def _tap(self, p):
        rec = self.recorder
        if rec is not None and self.mode == "recording":
            rec.on_packet(p)

    def run(self):
        """Read sensors and process rows until stop() (or the source ends)."""
        try:
            for t0, x in feature_stream(self.source, hop_s=self.hop_s, tap=self._tap, history=self.history):
                self._row(t0, x)
                if self._stop.is_set():
                    break
        finally:
            with self.lock:
                self._close_recorder()
            self.history.save(self.dir / "history.npz")

    def _row(self, t0, x):
        self.latest, self.latest_t = x, t0
        self._check_press_file()
        with self.lock:
            mode, rec, watcher = self.mode, self.recorder, self.watcher
        if mode == "recording" and rec is not None:
            rec.on_window(t0, x)
        elif self._last_history_save is None or t0 - self._last_history_save >= HISTORY_SAVE_EVERY_S:
            self.history.save(self.dir / "history.npz")  # while recording, the recorder saves it
            self._last_history_save = t0
        result = None
        if mode == "watching" and watcher is not None:
            result = watcher.predict_at(t0, x)
            self.result = result
        if self.stream is not None:
            missing = F.missing_sensors(x)
            self.stream.publish_features(t0, x, missing)
            if result is not None:
                self.stream.publish_result(log_record(t0, x, result, mode, missing, 0.0, self.device_id,
                                                      self.machine_id), t0)
        if self.screen is not None and (getattr(self, "_screen_t", None) is None
                                        or t0 - self._screen_t >= SCREEN_EVERY_S):
            self._screen_t = t0
            text = "\n".join(self.screen_lines())
            # Only a changed layout crosses the Bridge (and once in a while, in case the sketch restarted);
            # the live numbers are drawn by the microcontroller.
            if text != self._screen_text or t0 - getattr(self, "_screen_sent", -1e9) >= 5.0:
                self._screen_text, self._screen_sent = text, t0
                self._screen_event.set()

    def _check_press_file(self):
        p = self.dir / "press"
        if p.exists():
            try:
                p.unlink()
            except OSError:
                pass
            self.on_button()

    def stop(self):
        self._stop.set()

    # ------------------------------------------------------------ what is shown
    def screen_lines(self) -> list[str]:
        """The screen's layout: the mode and the current time on top. {vib}, {peak} and {temp} and a
        "{bar}" line are filled in by the microcontroller from every sample, so they move at the
        screen's own rate; the rest changes only when the mode, the time or the diagnosis does."""
        r = readings(self.latest)
        speed = f"speed {r['speed_hz']:.1f} Hz" if r["speed_hz"] else "speed --"
        live = ["vib  {vib} mg", "{bar}", "peak {peak} g"]
        with self.lock:
            mode, msg = self.mode, self.message
        top = f"{SCREEN_NAMES[mode]:<12s}{time.strftime('%H:%M:%S', time.localtime(self.clock()))}"
        if mode == "display":
            lines = [top] + live + [speed, "temp {temp} C"]
        elif mode == "recording":
            lines = [top] + live + [speed, "temp {temp} C"] + (_wrap(msg)[:2] if msg else [])
        elif mode == "training":
            lines = [top, f"{int(100 * self.train_share)}% {self.train_stage}"[:21], ""] + live[:2] + ["temp {temp} C"]
        else:
            res = self.result or {}
            diag = res.get("diagnosis", "warming_up")
            score = res.get("unknown_score")
            lines = [top] + _wrap(diag.replace("_", " ").upper())[:2]
            lines += [f"score {score:.2f}x" if isinstance(score, (int, float)) else "score --"]
            feats = list((res.get("features") or {}).items())
            if diag not in ("normal", "warming_up", "stopped") and feats:
                lines.append(f"{feats[0][0][:14]} {feats[0][1]:+.1f}")
            lines += live[:2] + ["temp {temp} C"]
        return [x[:21] if "{" not in x else x for x in lines[:8]]

    def _screen_worker(self):
        while not self._stop.is_set():
            if not self._screen_event.wait(1.0):
                continue
            self._screen_event.clear()
            text = self._screen_text
            try:
                self.screen(text)
            except Exception:  # noqa: BLE001  a busy or missing screen must not stop anything
                pass

    def status(self) -> dict:
        with self.lock:
            s = {"mode": self.mode, "mode_shown": SCREEN_NAMES[self.mode].lower(), "message": self.message,
                 "readings": readings(self.latest)}
            if self.mode == "recording" and self.record_started:
                s["learning_s"] = round(self.clock() - self.record_started, 1)
            if self.mode == "training":
                s["training"] = {"stage": self.train_stage, "share": round(self.train_share, 2)}
            if self.model_path:
                s["model"] = self.model_path.name
            if self.result:
                s["diagnosis"] = self.result.get("diagnosis")
        if hasattr(self.source, "stats"):
            s["sensors"] = dict(self.source.stats)
        return s

    def _event(self, name, details):
        if self.stream is not None:
            self.stream.publish_event(self.clock(), name, details)
