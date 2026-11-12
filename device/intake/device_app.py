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
from effente import safety as SF
from effente.history import History
from effente.model_runtime import Model, Watcher
from effente.trainer import load_for_training, train_model
from device.intake import contract as C
from device.intake.extract import feature_stream
from device.intake.live import log_record
from device.intake.recorder import Recorder

MODES = ("display", "recording", "training", "watching")
SCREEN_NAMES = {"display": "STREAMING", "recording": "RECORDING", "training": "TRAINING",
                "watching": "INFERENCE"}  # what the screen calls each mode
SCREEN_EVERY_S = 0.5  # how often the layout is checked for changes
HISTORY_SAVE_EVERY_S = 300.0
MIN_RECORD_S = 300.0  # learning needs at least 5 minutes of the machine running
NOTES_SHOWN_S = 120.0  # how long the screen shows what was odd about the learning
SCREEN_DIAG = {"normal": "NOTHING WRONG", "warming_up": "WARMING UP", "stopped": "MACHINE STOPPED"}
# when something is wrong, the screen says what is going on (effente.fault_types.SYMPTOM_TEXT)


def _g(x, name):
    v = x[F.INDEX[name]] if x is not None else np.nan
    return float(v) if np.isfinite(v) else None


def readings(x) -> dict:
    """The few numbers shown on the screen, from one feature row."""
    total = SF.vib_total_g(x) if x is not None else math.nan
    peak = _g(x, "vib_peak_g")
    return {"vib_mg": round(1000 * total, 1) if math.isfinite(total) else None,
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
                 max_gb: float | None = None, min_free_gb: float = 0.5, clock=time.time,
                 simulated: bool | None = None):
        self.dir = Path(data_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.source, self.screen, self.stream = source, screen, stream
        self.device_id, self.machine_id, self.hop_s = device_id, machine_id, hop_s
        self.min_record_s, self.max_gb, self.min_free_gb = min_record_s, max_gb, min_free_gb
        self.clock = clock
        self.simulated = C.is_simulated(source) if simulated is None else simulated  # the contract's flag
        self.lock = threading.RLock()
        self.mode = "display"
        self.recorder: Recorder | None = None
        self.recording: Path | None = None
        self.record_started: float | None = None
        self.watcher: Watcher | None = None
        self.model_path: Path | None = None
        self.model_version = C.NO_MODEL  # the contract's model_version: name and hash of the model file
        self.result: dict | None = None
        self.train_stage, self.train_share, self.message = "", 0.0, ""
        self.latest, self.latest_t = None, None
        self.last_press = -math.inf
        self.history = History.load(self.dir / "history.npz")
        self.settings_path = self.dir / "settings.json"
        self.sensitivity, self._settings_seen = SF.DEFAULT_SENSITIVITY, None
        self.safety = SF.Safety(gravity_check=bool(getattr(getattr(source, "info", None), "accel_measures_gravity", True)))
        self.problems: list[dict] = []
        self.notes: list[str] = []
        self.notes_until = -math.inf
        self._load_settings()
        self._last_history_save = None
        self._screen_text, self._screen_event = None, threading.Event()
        self._stop = threading.Event()
        self._resume()
        if self.stream is not None:
            self.stream.status_provider = self.status
            self.stream.settings_handler = self.settings
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
                self.watcher = Watcher(Model.load(self.model_path), margin=SF.margins(self.sensitivity)["model"])
                self.model_version = C.model_version_of(self.model_path)
            except Exception as e:  # noqa: BLE001  a model made with other features: learn again
                self.message = f"saved model not usable: {e}"
                self.watcher, self.model_path, self.model_version = None, None, C.NO_MODEL
        if mode == "watching" and self.watcher is not None:
            self.mode = "watching"
            self.safety.set_baseline(self.watcher.m.d.get("baseline"))
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
        self.safety.set_baseline(None)  # relearning: the old normal no longer counts
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
            t, X, names, period = load_for_training(self.recording)

            def progress(stage, share):
                self.train_stage, self.train_share = stage, share

            d, report = train_model(t, X, names, period=period, source=str(self.recording), progress=progress)
            models = self.dir / "models"
            models.mkdir(exist_ok=True)
            path = models / f"{self.recording.name}.json"
            path.write_text(json.dumps(d))
            watcher = Watcher(Model(d), margin=SF.margins(self.sensitivity)["model"])
            baseline = d.get("baseline") or {}
            version = C.model_version_of(path)
            with self.lock:
                self.model_path, self.watcher, self.mode = path, watcher, "watching"
                self.model_version = version
                self.safety.set_baseline(baseline)
                self.message = ""
                self.notes = list(baseline.get("warnings_screen", []))
                self.notes_until = self.clock() + NOTES_SHOWN_S
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
        started, cleared = self.safety.update(t0, x)
        for p in started:
            self._event("problem", p)
        for name in cleared:
            self._event("problem_cleared", {"name": name})
        self.problems = problems = self.safety.problems()
        result = None
        if mode == "watching" and watcher is not None:
            result = watcher.predict_at(t0, x)
            result.update(SF.verdict(result, problems), problems=problems)
            self.result = result
        if self.stream is not None:
            missing = F.missing_sensors(x)
            self.stream.publish_features(t0, x, missing)
            if result is not None:
                self.stream.publish_result(log_record(t0, x, result, mode, missing, 0.0, self.device_id,
                                                      self.machine_id, model_version=self.model_version,
                                                      simulated=self.simulated), t0)
        if self.screen is not None and (getattr(self, "_screen_t", None) is None
                                        or t0 - self._screen_t >= SCREEN_EVERY_S):
            self._screen_t = t0
            text = "\n".join(self.screen_lines())
            # Only a changed layout crosses the Bridge (and once in a while, in case the sketch restarted);
            # the live numbers are drawn by the microcontroller.
            if text != self._screen_text or t0 - getattr(self, "_screen_sent", -1e9) >= 5.0:
                self._screen_text, self._screen_sent = text, t0
                self._screen_event.set()

    # ------------------------------------------------------------ settings
    def settings(self, changes: dict | None = None) -> dict:
        """The device's settings; with changes, apply and save them first (raises ValueError if bad).
        Used by POST /settings on the stream; editing data/settings.json by hand works too."""
        if changes:
            unknown = sorted(set(changes) - {"sensitivity"})
            if unknown:
                raise ValueError(f"unknown settings {unknown}; there is only sensitivity")
            v = changes.get("sensitivity")
            lo, hi = SF.SENSITIVITY_RANGE
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
                raise ValueError(f"sensitivity must be a number from {lo:g} to {hi:g} ({SF.DEFAULT_SENSITIVITY:g} is the default)")
            self._set_sensitivity(float(v))
            tmp = self.settings_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"sensitivity": self.sensitivity}))
            tmp.replace(self.settings_path)
            self._settings_seen = self.settings_path.stat().st_mtime_ns
        m = SF.margins(self.sensitivity)
        return {"sensitivity": self.sensitivity, "sensitivity_range": list(SF.SENSITIVITY_RANGE),
                "counts_as_wrong": {"vibration_times_normal": round(m["vibration"], 2),
                                    "degrees_over_normal": round(m["temperature"], 1),
                                    "db_over_normal": round(m["sound"], 1),
                                    "model_score_over": round(m["model"], 2)},
                "default": SF.DEFAULT_SENSITIVITY,
                "note": "0 is least sensitive, 1 most; each 0.25 halves or doubles how far outside normal "
                        "counts as wrong; the universal limits never change"}

    def _set_sensitivity(self, v: float):
        with self.lock:
            changed = v != self.sensitivity
            self.sensitivity = v
            self.safety.set_sensitivity(v)
            if self.watcher is not None:
                self.watcher.margin = SF.margins(v)["model"]
        if changed:
            self._event("settings", {"sensitivity": v})

    def _load_settings(self):
        """Read data/settings.json if it is new or changed (it is checked every couple of seconds)."""
        try:
            seen = self.settings_path.stat().st_mtime_ns
        except OSError:
            return
        if seen == self._settings_seen:
            return
        self._settings_seen = seen
        try:
            v = json.loads(self.settings_path.read_text()).get("sensitivity", SF.DEFAULT_SENSITIVITY)
            lo, hi = SF.SENSITIVITY_RANGE
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
                raise ValueError(f"sensitivity must be from {lo:g} to {hi:g}")
            self._set_sensitivity(float(v))
        except (ValueError, AttributeError) as e:
            self.message = f"settings.json not used: {e}"[:80]

    def _check_press_file(self):
        now = time.monotonic()
        if now - getattr(self, "_settings_checked", -1e9) >= 2.0:
            self._settings_checked = now
            self._load_settings()
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
            mode, msg, problems = self.mode, self.message, list(self.problems)
            notes = self.notes if self.clock() < self.notes_until else []
        top = f"{SCREEN_NAMES[mode]:<12s}{time.strftime('%H:%M:%S', time.localtime(self.clock()))}"
        # What is wrong, in every mode, in plain words, each said once (effente.safety.verdict).
        bang = [f"! {t}" for t in SF.say(SF.verdict(None, problems)["what"])]
        if mode == "display":
            head, tail = [top] + live + [speed, "temp {temp} C"], []
        elif mode == "recording":
            head, tail = [top] + live + [speed, "temp {temp} C"], []
            bang += _wrap(msg)[:2] if msg else []
        elif mode == "training":
            head, tail = [top, f"{int(100 * self.train_share)}% {self.train_stage}"[:21], ""] + live[:2], ["temp {temp} C"]
        else:
            res = self.result or {}
            said = SF.say(SF.verdict(res, problems)["what"])  # the model's first, then the checks
            head = [top, said[0] if said else SCREEN_DIAG.get(res.get("diagnosis", "warming_up"), "NOTHING WRONG")]
            bang = [f"! {t}" for t in said[1:]] + [f"note: {n}"[:21] for n in notes]
            tail = live[:2] + ["temp {temp} C"]
        room = max(0, 8 - len(head) - len(tail))
        lines = head + bang[:room] + tail
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
            s["model_version"], s["simulated"] = self.model_version, self.simulated
            if self.result:
                s["diagnosis"] = self.result.get("diagnosis")
            s.update(SF.verdict(self.result if self.mode == "watching" else None, self.problems),
                     problems=list(self.problems), sensitivity=self.sensitivity)
            if self.watcher is not None and (self.watcher.m.d.get("baseline") or {}).get("warnings"):
                s["learning_notes"] = list(self.watcher.m.d["baseline"]["warnings"])
        if hasattr(self.source, "stats"):
            s["sensors"] = dict(self.source.stats)
        return s

    def _event(self, name, details):
        if self.stream is not None:
            self.stream.publish_event(self.clock(), name, details)
