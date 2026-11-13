"""The device itself: one button, four modes, the screen and the live stream.

    plug in -> DISPLAY --button--> RECORDING --button--> TRAINING --done--> WATCHING --button--> RECORDING ...

  DISPLAY     reads the sensors and shows the readings (vibration, peaks, speed, IMU temperature)
  RECORDING   learn mode: keeps the feature rows, per-minute summaries, raw snippets and events
              (device.intake.recorder); the screen shows how long it has been learning
  TRAINING    trains this machine's model on the recording, on the device (effente.trainer, NumPy
              only); the screen shows progress. A recording under 2 minutes isn't stopped: learning
              carries on, the screen still says RECORDING, and status has the reason
  WATCHING    checks every summary period for faults (effente.model_runtime.Watcher) and shows the
              diagnosis; pressing the button again starts a new recording, to relearn. While nothing
              is wrong it also shows an early warning (effente.early_warning): a small change that
              has lasted or is creeping up, "! EARLY: ...", never counted as wrong

All the time, sensor data goes to the screen and to whoever connects to the live stream
(device.intake.stream: results, feature rows, events, status). With an uploader
(device.intake.uploader), it also goes to the laptop platform: the model, a record a second while
watching, and a check-in otherwise; a sensitivity the platform sets is applied and saved. After a power cut it resumes what
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
from effente import early_warning as EW
from effente import safety as SF
from effente.history import History
from effente.model_input import load_columns
from effente.model_runtime import Model, Watcher
from effente.trainer import load_for_training, train_model
from device.intake import contract as C
from device.intake.extract import feature_stream
from device.intake.live import log_record
from device.intake.recorder import Recorder

MODES = ("display", "recording", "training", "watching")
SCREEN_NAMES = {"display": "IDLE", "recording": "RECORDING", "training": "TRAINING",
                "watching": "DETECTING"}  # what the screen calls each mode, and status's "state"
SCREEN_EVERY_S = 0.5  # how often the layout is checked for changes
HISTORY_SAVE_EVERY_S = 300.0
# A press sooner than this after learning started doesn't stop it (2 minutes: a bounce or a second thought, and
# enough rows for effente.trainer's MIN_ROWS_SHORT). The screen never says so: it keeps showing RECORDING, and
# the reason goes to status ("message") and the stream (a "stop_refused" event).
MIN_RECORD_S = 120.0
NOTES_SHOWN_S = 120.0  # how long the screen shows what was odd about the learning
# Which tile goes with what is wrong (design B): the reading a person would look at.
VIB_SYMPTOMS = {"vibration_too_high", "unusual_vibration", "knocking_or_jolts", "vibration_unsteady", "sensor_problem",
                "device_moved"}
TEMP_SYMPTOMS = {"too_hot", "heating_up", "heating_fast", "hot_spot", "running_cold", "temperature_swings"}
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
                 simulated: bool | None = None, uploader=None):
        self.dir = Path(data_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.source, self.screen, self.stream, self.uploader = source, screen, stream, uploader
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
        self.early: EW.EarlyWarning | None = None  # the early warning, for the model being watched with
        self.model_path: Path | None = None
        self.model_version = C.NO_MODEL  # the contract's model_version: name and hash of the model file
        self.result: dict | None = None
        self.train_stage, self.train_share, self.message = "", 0.0, ""
        self.latest, self.latest_t = None, None
        self.last_press = -math.inf
        self.history = History.load(self.dir / "history.npz")
        self.settings_path = self.dir / "settings.json"
        self.sensitivity, self._settings_seen = SF.DEFAULT_SENSITIVITY, None
        self.spike_sensitivity = SF.DEFAULT_SENSITIVITY  # the second dial: single spikes only (added 13 Nov)
        self.overrides: dict[str, float | None] = {k: None for k in OVERRIDES}  # None: follow the main sensitivity
        self.early_on = True  # the early warning switched on (the "early_warning" setting)
        self.safety = SF.Safety(gravity_check=bool(getattr(getattr(source, "info", None), "accel_measures_gravity", True)))
        self.problems: list[dict] = []
        self.notes: list[str] = []
        self.notes_until = -math.inf
        self._load_settings()
        self._last_history_save = None
        self._screen_text, self._screen_event = None, threading.Event()
        self._stop = threading.Event()
        self._resume()
        if self.uploader is not None:
            self.uploader.on_settings = self.platform_settings
            if self.watcher is not None:
                self.uploader.announce(self.model_path, self.watcher.m.d, self.sensitivity)
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
                self.watcher = self._watcher(Model.load(self.model_path), _load_early(self.model_path))
                self.model_version = C.model_version_of(self.model_path)
            except Exception as e:  # noqa: BLE001  a model made with other features: learn again
                self.message = f"saved model not usable: {e}"
                self.watcher, self.model_path, self.model_version = None, None, C.NO_MODEL
                self.early = None
        if mode == "watching" and self.watcher is not None:
            self.mode = "watching"
            self.safety.set_baseline(SF.baseline_of(self.watcher.m.d))
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
        if self.early is not None:
            self.early.reset()  # and nothing it had seen counts either
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
            self._event("stop_refused", {"reason": self.message, "learning_s": round(took, 1)})
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
            try:  # the more_impacts check's levels, from the recording's one-a-second rows (an addition)
                ts, cols = load_columns(self.recording, SF.SPIKE_FEATURES_SHORT + ("trend_running",))
                spikes = SF.learn_spike_levels(ts, cols, ~(cols["trend_running"] < 0.5))
                if spikes:
                    d["baseline"]["spikes"] = spikes
            except Exception as e:  # noqa: BLE001  watching works without it
                self._event("spike_levels_off", {"reason": str(e)[:200]})
            models = self.dir / "models"
            models.mkdir(exist_ok=True)
            path = models / f"{self.recording.name}.json"
            path.write_text(json.dumps(d))
            model = Model(d)
            calib = None
            try:  # this machine's normal for the early warning, from the same rows
                calib = EW.calibrate(model, t, X, names)
                _early_path(path).write_text(json.dumps(calib))
            except Exception as e:  # noqa: BLE001  watching works without it
                self._event("early_warning_off", {"reason": str(e)[:200]})
            watcher = self._watcher(model, calib)
            baseline = SF.baseline_of(d) or {}
            version = C.model_version_of(path)
            with self.lock:
                self.model_path, self.watcher, self.mode = path, watcher, "watching"
                self.model_version = version
                self.safety.set_baseline(baseline)
                self.message = ""
                self.notes = list(baseline.get("warnings_screen", []))
                self.notes_until = self.clock() + NOTES_SHOWN_S
                self._save_state()
            if self.uploader is not None:
                self.uploader.announce(path, d, self.sensitivity)
            self._event("model_trained", {"file": path.name, **report})
            self._event("mode", {"mode": "watching"})
        except Exception as e:  # noqa: BLE001  e.g. too little recorded: keep learning
            with self.lock:
                self.message = str(e)[:80]
                self._open_recorder()
                self.mode = "recording"
                self._save_state()
            self._event("training_failed", {"reason": str(e)})

    def _watcher(self, model, calib) -> Watcher:
        """A Watcher for the model, with its early warning fed every period the Watcher judges."""
        w = Watcher(model, margin=SF.margins(self._dial("model_sensitivity"))["model"],
                    spike_margin=SF.margins(self.spike_sensitivity)["model"])
        early = EW.EarlyWarning(model, calib, sensitivity=self._dial("early_warning_sensitivity"))
        judge = w._judge

        def judged(minute_t, row):
            judge(minute_t, row)
            st = w.state
            if self.watcher is not w and self.watcher is not None:
                return  # an old watcher (relearned meanwhile)
            # the real alarm: the model's or the checks'; the early warning steps aside while it is on
            wrong = SF.verdict(st, self.problems)["wrong"]
            if not self.early_on:
                return  # switched off: nothing watched, nothing shown
            for name, details in early.observe(minute_t, st.get("diagnosis") != "stopped", row=row, alarm=wrong):
                self._event(name, details)

        w._judge = judged
        self.early = early
        return w

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
        if self.uploader is not None:  # only queues: the sending is on the uploader's own thread
            self.uploader.on_row(t0, x, mode, result, problems, self.sensitivity,
                                 watcher.margin if watcher is not None else None)
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
    def _dial(self, name: str) -> float:
        """An override's value, or the main sensitivity when it isn't set."""
        v = self.overrides.get(name)
        return self.sensitivity if v is None else v

    def _dials(self) -> dict:
        """Every setting as it is now, for /settings and status (overrides None: following the main one)."""
        return {"sensitivity": self.sensitivity, "spike_sensitivity": self.spike_sensitivity, **self.overrides,
                "early_warning": self.early_on}

    def _margins(self) -> dict:
        """The margins in force: the main sensitivity's, with each overridden group's from its own dial."""
        m = SF.margins(self.sensitivity)
        for group, name in (("model", "model_sensitivity"), ("vibration", "vibration_sensitivity"),
                            ("temperature", "heat_sensitivity"), ("sound", "sound_sensitivity")):
            m[group] = SF.margins(self._dial(name))[group]
        return m

    def settings(self, changes: dict | None = None) -> dict:
        """The device's settings; with changes, apply and save them first (raises ValueError if bad, and then
        changes nothing). Used by POST /settings on the stream; editing data/settings.json by hand works too."""
        if changes:
            unknown = sorted(set(changes) - set(SETTINGS))
            if unknown:
                raise ValueError(f"unknown settings {unknown}; there are {', '.join(SETTINGS)}")
            for name, v in changes.items():  # check them all before applying any
                _check_setting(name, v)
            self._assign(changes)
            keep = {"sensitivity": self.sensitivity, "spike_sensitivity": self.spike_sensitivity,
                    **{k: v for k, v in self.overrides.items() if v is not None}}
            if not self.early_on:
                keep["early_warning"] = False
            tmp = self.settings_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(keep))
            tmp.replace(self.settings_path)
            self._settings_seen = self.settings_path.stat().st_mtime_ns
        m, sp = self._margins(), SF.margins(self.spike_sensitivity)
        return {**self._dials(), "sensitivity_range": list(SF.SENSITIVITY_RANGE),
                "counts_as_wrong": {"vibration_times_normal": round(m["vibration"], 2),
                                    "degrees_over_normal": round(m["temperature"], 1),
                                    "db_over_normal": round(m["sound"], 1),
                                    "model_score_over": round(m["model"], 2),
                                    "sudden_impact_times_highest_while_learning": round(sp["vibration"], 2),
                                    "model_spike_line_times": round(sp["model"], 2)},
                "default": SF.DEFAULT_SENSITIVITY,
                "note": "0 is least sensitive, 1 most; each 0.25 halves or doubles how far outside normal "
                        "counts as wrong; the universal limits never change. spike_sensitivity (same range and "
                        "default) sizes only single spikes: one sudden impact, and one second of the model "
                        "over its spike line. model_, vibration_, heat_, sound_ and early_warning_sensitivity "
                        "each set one group on its own (null: follow sensitivity); early_warning false switches "
                        "the early warning off"}

    def platform_settings(self, settings: dict) -> None:
        """Settings in a reply from the platform (CONTRACT.md section 7): a sensitivity that differs
        from the device's own is applied and saved, so it survives a restart."""
        v = settings.get("sensitivity")
        if v is None or v == self.sensitivity:
            return
        try:
            self.settings({"sensitivity": v})
        except ValueError as e:
            self.message = f"platform setting not used: {e}"[:80]

    def _assign(self, values: dict) -> None:
        """Set checked settings and apply the result everywhere it is used; a "settings" event says what changed."""
        with self.lock:
            before = self._dials()
            for name, v in values.items():
                if name in OVERRIDES:
                    self.overrides[name] = None if v is None else float(v)
                elif name == "early_warning":
                    self.early_on = bool(v)
                else:
                    setattr(self, name, float(v))
            self.safety.set_sensitivity(self.sensitivity)
            self.safety.set_group_sensitivity({"vibration": self.overrides["vibration_sensitivity"],
                                               "temperature": self.overrides["heat_sensitivity"],
                                               "sound": self.overrides["sound_sensitivity"]})
            self.safety.set_spike_sensitivity(self.spike_sensitivity)
            if self.watcher is not None:
                self.watcher.margin = SF.margins(self._dial("model_sensitivity"))["model"]
                self.watcher.spike_margin = SF.margins(self.spike_sensitivity)["model"]
            if self.early is not None:
                self.early.set_sensitivity(self._dial("early_warning_sensitivity"))
                if not self.early_on:
                    self.early.reset()  # nothing shown, and it starts afresh if switched on again
            changed = {k: v for k, v in self._dials().items() if v != before[k]}
        if changed:
            self._event("settings", changed)

    def _load_settings(self):
        """Read data/settings.json if it is new or changed (it is checked every couple of seconds). A setting
        missing from the file goes back to its default."""
        try:
            seen = self.settings_path.stat().st_mtime_ns
        except OSError:
            return
        if seen == self._settings_seen:
            return
        self._settings_seen = seen
        try:
            d = json.loads(self.settings_path.read_text())
            values = {name: d.get(name, default) for name, default in SETTINGS.items()}
            for name, v in values.items():
                _check_setting(name, v)
            self._assign(values)
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
        """The screen's layout, design B (tiles): four lines the microcontroller draws as a header and four
        boxed readings (device/app/effente/sketch/sketch.ino, composeTiles()):
          "@<header left>"   the mode (IDLE, RECORDING, TRAINING n%, DETECTING), or what is wrong with a leading
                             "!" (not drawn; nothing is inverted). Messages (a recording too short to stop, a
                             training that failed, a setting not used) are never shown: they are in status
          "<header right>"   the time, hours and minutes
          "<tile>"           vib, peak, temp or hz: the reading that goes with what is wrong; else empty
          "<speed>"          running speed in Hz, a whole number, or "--"
        Vibration, peak and temperature (shown in F) come from the samples on the microcontroller, so they
        move at the screen's own rate; this changes only when the mode, the minute or what is wrong does."""
        r = readings(self.latest)
        speed = f"{r['speed_hz']:.0f}" if r["speed_hz"] else "--"
        with self.lock:
            mode, problems = self.mode, list(self.problems)
            early_obj = getattr(self, "early", None)
            early = early_obj.screen() if early_obj is not None and mode == "watching" else None
        res = (self.result or {}) if mode == "watching" else None
        what = SF.verdict(res, problems)["what"]  # in plain words, the model's first, then the checks
        if what:
            left = "!" + SF.say(what)[0]
        elif mode == "watching" and early:
            left = early  # a small lasting change; never shown with a real problem
        elif mode == "training":
            left = f"TRAINING {int(100 * self.train_share)}%"
        elif mode == "watching" and res.get("diagnosis") in ("warming_up", "stopped"):
            left = SCREEN_DIAG[res["diagnosis"]]
        else:
            left = SCREEN_NAMES[mode]
        tile = ""
        if what:
            first = what[0]
            if first == "speed_changed":
                tile = "hz"
            elif first in TEMP_SYMPTOMS:
                tile = "temp"
            elif first in VIB_SYMPTOMS:
                tile = "vib"
        clock = time.strftime("%H:%M", time.localtime(self.clock()))
        return [f"@{left}"[:24], clock, tile, speed]

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
            # "state" is the name the screen uses (IDLE, RECORDING, TRAINING, DETECTING); "mode" keeps the
            # platform contract's values (display, recording, training, watching)
            s = {"mode": self.mode, "state": SCREEN_NAMES[self.mode], "mode_shown": SCREEN_NAMES[self.mode].lower(),
                 "message": self.message, "readings": readings(self.latest)}
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
                     problems=list(self.problems), **self._dials())
            if self.mode == "watching" and self.early is not None:
                s["early_warning"] = self.early.state() if self.early_on else {"on": False, "switched_off": True}
            if self.watcher is not None and (self.watcher.m.d.get("baseline") or {}).get("warnings"):
                s["learning_notes"] = list(self.watcher.m.d["baseline"]["warnings"])
        if hasattr(self.source, "stats"):
            s["sensors"] = dict(self.source.stats)
        return s

    def _event(self, name, details):
        if self.stream is not None:
            self.stream.publish_event(self.clock(), name, details)


# The settings and their defaults. sensitivity is the main dial (the platform's); spike_sensitivity sizes single
# spikes only and doesn't follow it. The overrides each set one group on its own, None (null) following the main
# one: the model's slow (median) alarm, the outside-normal checks per sensor, and the early warning. With none set
# the device does exactly what the main dial alone did. early_warning False switches the early warning off.
OVERRIDES = ("model_sensitivity", "vibration_sensitivity", "heat_sensitivity", "sound_sensitivity",
             "early_warning_sensitivity")
SETTINGS = {"sensitivity": SF.DEFAULT_SENSITIVITY, "spike_sensitivity": SF.DEFAULT_SENSITIVITY,
            **{k: None for k in OVERRIDES}, "early_warning": True}


def _check_setting(name: str, v) -> None:
    if name == "early_warning":
        if not isinstance(v, bool):
            raise ValueError("early_warning must be true or false")
        return
    if v is None and name in OVERRIDES:
        return
    lo, hi = SF.SENSITIVITY_RANGE
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
        raise ValueError(f"{name} must be a number from {lo:g} to {hi:g} ({SF.DEFAULT_SENSITIVITY:g} is the default)"
                         + (" or null" if name in OVERRIDES else ""))


def _early_path(model_path: Path) -> Path:
    return model_path.with_name(model_path.stem + ".early.json")


def _load_early(model_path: Path) -> dict | None:
    """The early warning's calibration saved beside a model (None for models trained before it)."""
    try:
        return json.loads(_early_path(model_path).read_text())
    except (OSError, ValueError):
        return None
