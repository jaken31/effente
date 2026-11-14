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
watching, and a check-in otherwise; a sensitivity the platform sets is applied and saved. After a power cut it carries
on watching with the saved model, or carries on training. It never carries on recording: recording starts only from a
button press. A recording the power cut ended stays in its folder, and the app comes back not recording (watching
with the saved model if there is one, else display).
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
from effente.model_input import load_columns, load_recording
from effente.model_runtime import Model, Watcher
from effente.trainer import SPIKE_LINE_TIMES as TR_SPIKE_TIMES
from effente.trainer import choose_period, load_for_training, train_model
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
            "tilt": [_g(x, f"vib_gravity_{a}") for a in "xyz"],
            # Not fitted yet: None until the INMP441 microphone and the MLX90640-D55 thermal camera send data
            # (device.intake.source.UnoQSource, audio=True / thermal=True).
            "sound_db": _g(x, "snd_level_db"), "thermal_max_c": _g(x, "th_max_c")}


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


# The commands that do something in each mode (the others are refused or change nothing there).
USEFUL_NOW = {"display": ["record", "detect", "train", "clear_history"], "recording": ["stop", "idle"],
              "training": [], "watching": ["record", "idle", "train", "clear_history"]}

ADD_MIN_S = 30.0  # the least recording worth adding to earlier ones, in seconds
JOIN_MAX_ROWS = 400_000  # rows of several recordings joined in memory: about 4 days at one a second


def _join_recordings(folders):
    """The rows of several recordings as one, for train_model: (t, X, names, period, the folders used). Each
    later recording is moved up in time to follow the one before by a second, so the time between them (an hour,
    a day) is not there to be split or summarised over; the rows themselves are unchanged. A recording made with
    other features than the newest is left out."""
    loaded = []
    for f in folders:
        X, t, names = load_recording(f)
        if len(t):
            loaded.append((f, np.asarray(t, float), X, list(names)))
    if not loaded:
        raise ValueError("no rows in the recordings")
    names = loaded[-1][3]
    loaded = [r for r in loaded if r[3] == names]
    ts, Xs, end = [], [], None
    for f, t, X, _ in loaded:
        if end is not None:
            t = t - round(t[0] - end - 1.0)
        end = float(t[-1])
        ts.append(t)
        Xs.append(X)
    t, X = np.concatenate(ts), np.concatenate(Xs)
    if len(t) > JOIN_MAX_ROWS:
        raise ValueError(f"too much to join ({len(t)} rows): record with keep false to start over")
    return t, X, names, choose_period(float(t[-1] - t[0])), [r[0] for r in loaded]


# Sensors this device will never have (the user, 13 Nov: there won't be a room sensor). Their readings stay
# empty in every row, as before; they are only left out of the list of sensors that are "missing", which
# is for sensors that should be there.
NOT_FITTED = ("room",)


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
        # The recordings the model is made from, oldest first: the button starts over with one; the "record"
        # command can add to them (keep), so a model can be learned in several goes (see command()).
        self.sessions: list[str] = []
        self.fresh = False  # the recording in progress starts over: the model will be learned from it alone
        try:  # the ids of the platform's commands already done
            self._commands_done = [str(v) for v in json.loads((self.dir / "commands_done.json").read_text())][-200:]
        except (OSError, ValueError):
            self._commands_done = []
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
        self._resumed_training = False  # training picked up after a power cut: if it fails, don't record
        self._resume()
        if self.uploader is not None:
            self.uploader.on_settings = self.platform_settings
            self.uploader.on_commands = self.platform_commands
            if self.watcher is not None:
                self.uploader.announce(self.model_path, self.watcher.m.d, self.sensitivity)
        if self.stream is not None:
            self.stream.status_provider = self.status
            self.stream.settings_handler = self.settings
            self.stream.command_handler = self.command
            self.stream.controls = self.controls
            self.stream.fires_at = self.fires_at
        if self.screen is not None:
            threading.Thread(target=self._screen_worker, name="screen", daemon=True).start()

    # ------------------------------------------------------------ state that survives a power cut
    def _save_state(self):
        state = {"mode": self.mode, "recording": str(self.recording) if self.recording else None,
                 "record_started": self.record_started, "model": str(self.model_path) if self.model_path else None,
                 "sessions": list(self.sessions), "fresh": self.fresh}
        tmp = self.dir / "state.json.tmp"
        tmp.write_text(json.dumps(state))
        tmp.replace(self.dir / "state.json")

    def _resume(self):
        p = self.dir / "state.json"
        state = json.loads(p.read_text()) if p.exists() else {}
        mode = state.get("mode", "display")
        self.sessions = [str(p) for p in state.get("sessions") or [] if Path(str(p)).exists()]
        self.fresh = bool(state.get("fresh"))
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
            self.mode = self._idle_mode()
        elif mode == "display" and self.watcher is not None:
            pass  # put into idle on purpose (the "idle" command): stay there, with the model kept for "detect"
        elif mode == "training" and state.get("recording"):
            self.recording = Path(state["recording"])
            self.record_started = state.get("record_started") or self.clock()
            self._resumed_training = True
            self._begin_training()
        elif mode == "recording":
            # Recording starts only from a button press, never by itself after a power cut. What was kept
            # stays in its folder; the next press starts a new recording.
            self.mode = self._idle_mode()
            self.message = "power cut ended the recording; press to record again"
            self._save_state()
        self._event("mode", {"mode": self.mode, "resumed": mode != "display", "was": mode})

    def _idle_mode(self) -> str:
        """Not recording: watching with the saved model if there is one, else display."""
        self.recording, self.record_started = None, None
        if self.watcher is not None:
            self.safety.set_baseline(SF.baseline_of(self.watcher.m.d))
            return "watching"
        return "display"

    # ------------------------------------------------------------ the button
    def on_button(self, *_):
        now = self.clock()
        with self.lock:
            if now - self.last_press < 1.0:  # one press, however bouncy
                return
            self.last_press = now
            if self.mode in ("display", "watching"):
                self.fresh = True  # the button relearns from nothing; the "record" command can add instead
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
        adding = bool(self.sessions) and not self.fresh
        need = ADD_MIN_S if adding else self.min_record_s  # adding to earlier recordings: a little is enough
        if took < need:
            self.message = (f"learn {int(need // 60)} min or more ({int(took // 60)} so far)" if need >= 60 else
                            f"record {int(need)} s or more ({int(took)} so far)")
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
            folders = [] if self.fresh else [Path(p) for p in self.sessions
                                             if Path(p).exists() and Path(p) != self.recording]
            folders.append(self.recording)
            if len(folders) == 1:
                t, X, names, period = load_for_training(self.recording)
            else:  # several recordings, joined end to end (the gaps between them taken out)
                t, X, names, period, used = _join_recordings(folders)
                folders = used

            def progress(stage, share):
                self.train_stage, self.train_share = stage, share

            d, report = train_model(t, X, names, period=period, source=" + ".join(f.name for f in folders),
                                    progress=progress)
            report["recordings"] = [f.name for f in folders]
            try:  # the more_impacts check's levels, from the recordings' one-a-second rows (an addition)
                parts = [load_columns(f, SF.SPIKE_FEATURES_SHORT + SF.MOMENT_COLUMNS + ("trend_running",)) for f in folders]
                ts = np.concatenate([p[0] for p in parts])
                cols = {c: np.concatenate([p[1][c] for p in parts]) for c in parts[0][1]}
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
                self.sessions, self.fresh = [str(f) for f in folders], False  # what this model was learned from
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
                if self._resumed_training:  # picked up after a power cut: no press started a recording
                    self.mode = self._idle_mode()
                else:
                    self._open_recorder()
                    self.mode = "recording"
                self._resumed_training = False
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
        if getattr(self, "_clear_history", False):
            self._clear_history_now()
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
            missing = [s for s in F.missing_sensors(x) if s not in NOT_FITTED]
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
                                    "sudden_sound_db_over_loudest_while_learning": round(sp["sound"], 1),
                                    "sudden_heat_degrees_over_hottest_while_learning": round(sp["temperature"] / 3.0, 1),
                                    "sudden_twist_times_highest_while_learning": round(sp["vibration"], 2),
                                    "model_spike_line_times": round(sp["model"], 2)},
                "default": SF.DEFAULT_SENSITIVITY,
                "note": "0 is least sensitive, 1 most; each 0.25 halves or doubles how far outside normal "
                        "counts as wrong; the universal limits never change. spike_sensitivity (same range and "
                        "default) sizes only single spikes: one sudden impact, and one second of the model "
                        "over its spike line. model_, vibration_, heat_, sound_ and early_warning_sensitivity "
                        "each set one group on its own (null: follow sensitivity); early_warning false switches "
                        "the early warning off"}

    def command(self, name: str | None = None, body: dict | None = None) -> dict:
        """Do something once, for POST /command on the stream; with no name, list the commands (GET /command).
        Raises ValueError for a command there isn't."""
        if name is None:
            return {"commands": COMMANDS, "send": 'POST /command with {"command": "<name>"}'}
        if name == "press":
            before = self.mode
            self.on_button()
            return {"command": name, "mode_before": before, "mode": self.mode}
        if name in ("record", "stop", "idle", "detect", "train"):
            with self.lock:
                before = self.mode
                if before == "training":
                    raise ValueError("training: wait until it has finished (about half a minute)")
                note = getattr(self, "_do_" + name)(body or {})
                self._save_state()
            self._event("command", {"command": name, "mode_before": before, "mode": self.mode})
            return {"command": name, "mode_before": before, "mode": self.mode, "state": SCREEN_NAMES[self.mode],
                    "recordings_in_model": [Path(p).name for p in self.sessions], **({"note": note} if note else {})}
        if name == "clear_history":
            self._clear_history = True  # done by the pipeline, between two rows, since it is the one using it
            return {"command": name, "note": "the history is cleared before the next row (within a second)"}
        raise ValueError(f"no such command {name!r}; there are {', '.join(COMMANDS)}")

    # Each is called with the lock held, and never while training.
    def _do_record(self, body) -> str | None:
        """Start recording. keep true (the default): what is recorded is added to the recordings the model was
        learned from; keep false: start over, as the button does (the machine is not what it was)."""
        if self.mode == "recording":
            return "already recording"
        keep = body.get("keep", True)
        if not isinstance(keep, bool):
            raise ValueError("keep must be true or false")
        self.fresh = not keep or not self.sessions
        self._start_recording()
        return ("a new start: the model will be learned from this recording alone" if self.fresh else
                "added to " + str(len(self.sessions)) + " earlier recording(s) when stopped")

    def _do_stop(self, body) -> str | None:
        """Stop recording and train (TRAINING, then DETECTING), from this recording and the ones kept."""
        if self.mode != "recording":
            raise ValueError(f"not recording (the device is {SCREEN_NAMES[self.mode]})")
        self._stop_recording_and_train()
        return self.message or None if self.mode == "recording" else None

    def _do_idle(self, body) -> str | None:
        """Stop what the device is doing, without training: for while someone works on the machine. From
        recording: the recording ends and is kept (for the next training, if it is ADD_MIN_S long). From
        detecting: nothing is judged against the learned normal any more. The model stays for "detect"."""
        note = None
        if self.mode == "recording":
            took = self.clock() - (self.record_started or self.clock())
            self._close_recorder()
            if took >= ADD_MIN_S and self.recording is not None:
                self.sessions = ([] if self.fresh else self.sessions) + [str(self.recording)]
                note = f"the recording ({int(took)} s) is kept for the next training"
            else:
                note = f"the recording was too short to keep ({int(took)} s; {int(ADD_MIN_S)} or more is kept)"
        self.recording, self.record_started, self.fresh = None, None, False
        self.safety.set_baseline(None)  # idle compares with nothing learned; the fixed limits still apply
        self.result, self.message, self.mode = None, "", "display"
        self._event("mode", {"mode": "display", "by": "command"})
        return note

    def _do_train(self, body) -> str | None:
        """Make the model again from the recordings there are, without recording more (after the device's
        software or its training settings changed, say)."""
        if self.mode == "recording":
            raise ValueError('recording: send "stop" to end it and train')
        folders = [Path(p) for p in self.sessions if Path(p).exists()]
        if not folders and self.model_path is not None:  # a model from before the recordings were listed
            guess = self.dir / "recordings" / self.model_path.stem
            folders = [guess] if guess.exists() else []
        if not folders:
            raise ValueError("no recording to train from: record first")
        self.sessions, self.fresh = [str(f) for f in folders], False
        self.recording, self.record_started = folders[-1], None
        self._resumed_training = True  # if it fails, go back to what there was, not into a recording
        self._begin_training()
        return f"training from {len(folders)} recording(s)"

    def _do_detect(self, body) -> str | None:
        """Back to checking against the model there is (from idle)."""
        if self.mode == "watching":
            return "already detecting"
        if self.mode == "recording":
            raise ValueError('recording: send "stop" to train and detect, or "idle" to end it without training')
        if self.watcher is None:
            raise ValueError("no model yet: record, then stop")
        self.safety.set_baseline(SF.baseline_of(self.watcher.m.d))
        self.mode = "watching"
        self._event("mode", {"mode": "watching", "by": "command"})
        return None

    def fires_at(self) -> dict:
        """For whoever reads the JSON: the value each check fires at, in the reading's own unit, with the
        sensitivities as they are now and what this machine's model learned. Three kinds:
          always      fixed limits: any machine, no recording needed, no dial moves them
          lasting     against this machine's normal (needs a model); must hold about 10 s (or the model's 3 of 5)
          one_moment  one second well beyond anything while learning (needs a model); shows at once, stays 30 s
        Each entry has the rule in words, "default" (the rule's number at sensitivity 0.5), "margin_now" (the same
        at the present sensitivity) and "fires_at" (the reading's value; null until a model has learned it)."""
        m, m0 = self._margins(), SF.margins(SF.DEFAULT_SENSITIVITY)
        sp = SF.margins(self.spike_sensitivity)
        lv, spikes = dict(self.safety.levels), dict(self.safety.spikes or {})
        tops = spikes.get("moment_max") or {}
        watching = self.watcher is not None and self.mode == "watching"
        model = self.watcher.m if self.watcher is not None else None

        def r(v, n=3):
            return None if v is None else round(float(v), n)

        def lasting(name, unit, reading, level, default, now, value, floor=None):
            e = {"reading": reading, "unit": unit, "learned_normal": r(level), "default": default, "margin_now": now,
                 "fires_at": r(value)}
            if floor is not None:
                e["never_below"] = floor
            return name, e

        vel, tot = lv.get("vib_velocity_mm_s"), lv.get("vib_total_g")
        out_lasting = dict([
            lasting("vibration_above_normal", "mm/s", "vib_velocity_mm_s", vel, f"{m0['vibration']:g}x normal",
                    f"{m['vibration']:g}x normal", None if not vel else max(m["vibration"] * vel, SF.VIB_FLOOR["vib_velocity_mm_s"]),
                    SF.VIB_FLOOR["vib_velocity_mm_s"]),
            lasting("vibration_above_normal (total)", "g rms", "vib_total_g", tot, f"{m0['vibration']:g}x normal",
                    f"{m['vibration']:g}x normal", None if not tot else max(m["vibration"] * tot, SF.VIB_FLOOR["vib_total_g"]),
                    SF.VIB_FLOOR["vib_total_g"]),
            lasting("hotter_than_normal", "degC", "imu_temp_c", lv.get("imu_temp_c"), f"+{m0['temperature']:g} degC",
                    f"+{m['temperature']:g} degC", None if "imu_temp_c" not in lv else lv["imu_temp_c"] + m["temperature"]),
            lasting("louder_than_normal", "dB", "snd_level_db", lv.get("snd_level_db"), f"+{m0['sound']:g} dB",
                    f"+{m['sound']:g} dB", None if "snd_level_db" not in lv else lv["snd_level_db"] + m["sound"]),
        ])
        if "th_max_c" in lv:
            out_lasting["hotter_than_normal (camera)"] = lasting(
                "", "degC", "th_max_c", lv["th_max_c"], f"+{m0['temperature']:g} degC", f"+{m['temperature']:g} degC",
                lv["th_max_c"] + m["temperature"])[1]
        out_lasting["model"] = {
            "reading": "unknown_score in each result: the model's score over its line (1.0 is the line as trained)",
            "default": "1.0", "margin_now": f"{m['model']:g}", "fires_at": r(m["model"]),
            "holds": "the middle of the last few scores must be over it (3 of 5 seconds after a short recording)",
            "line_in_model_units": r(model.threshold if model is not None else None)}

        def moment(unit, reading, top, default, now, value, extra=None):
            return {"reading": reading, "unit": unit, "highest_while_learning": r(top), "default": default,
                    "margin_now": now, "fires_at": r(value), **(extra or {})}

        peak = None if spikes.get("peak_max") is None else 10 ** float(spikes["peak_max"])
        gyr = None if "gyr_peak" not in tops else 10 ** float(tops["gyr_peak"])
        heat = lambda k: None if k not in tops else tops[k] + sp["temperature"] / 3.0  # noqa: E731
        one = {
            "sudden_impact": moment("g", "vib_peak_g (the second's biggest peak)", peak, f"{m0['vibration']:g}x the highest",
                                    f"{sp['vibration']:g}x the highest",
                                    None if peak is None else max(sp["vibration"] * peak, SF.IMPACT_MIN_G),
                                    {"never_below": SF.IMPACT_MIN_G}),
            "sudden_sound": moment("dB", "snd_level_db (the second's level)", spikes.get("sound_max"),
                                   f"+{m0['sound']:g} dB over the loudest", f"+{sp['sound']:g} dB over the loudest",
                                   None if spikes.get("sound_max") is None else spikes["sound_max"] + sp["sound"]),
            "sudden_twist": moment("deg/s", "gyr_peak (the second's biggest turn)", gyr, f"{m0['vibration']:g}x the highest",
                                   f"{sp['vibration']:g}x the highest", None if gyr is None else sp["vibration"] * gyr),
            "sudden_heat": moment("degC", "imu_temp_c", tops.get("imu_temp_c"), f"+{m0['temperature'] / 3:g} degC over the hottest",
                                  f"+{sp['temperature'] / 3:g} degC over the hottest", heat("imu_temp_c")),
            "sudden_heat (camera, hottest point)": moment("degC", "th_max_c", tops.get("th_max_c"),
                                                          f"+{m0['temperature'] / 3:g} degC over the hottest",
                                                          f"+{sp['temperature'] / 3:g} degC over the hottest", heat("th_max_c")),
            "model_spike": {"reading": "spike_score in each result: one second's score over the spike line",
                            "default": f"{TR_SPIKE_TIMES:g}x the model's line", "margin_now": f"{sp['model']:g}x the spike line",
                            "fires_at": r(sp["model"]),
                            "spike_line_in_model_units": r(model.spike_line if model is not None else None),
                            "note": "only after a recording short enough for periods under a minute"},
        }
        return {
            "note": "the value each check fires at now. \"always\" needs no recording and no dial moves it; the "
                    "others compare with what this machine's model learned, so they are null until there is a model, "
                    "and \"default\" is the rule at sensitivity 0.5 on any machine (the numbers under fires_at are "
                    "this machine's). Checks against the model are judged only in DETECTING and while the machine "
                    "counts as running",
            "judging_now": watching,
            "sensitivity": self.sensitivity, "spike_sensitivity": self.spike_sensitivity, "model": self.model_version,
            "always": {
                "vibration_over_limit": {"reading": "vib_velocity_mm_s", "unit": "mm/s", "fires_at": SF.VELOCITY_MM_S, "holds_s": SF.HOLD_S},
                "knocking": {"reading": "impulsive vibration (kurtosis over 8) with peaks", "unit": "g", "fires_at": SF.KNOCK_PEAK_G,
                             "holds_s": SF.HOLD_S},
                "too_hot": {"reading": "imu_temp_c or the camera's hottest point", "unit": "degC", "fires_at": SF.HOT_C, "holds_s": SF.HOLD_S},
                "heating_fast": {"reading": "temperature rise", "unit": "degC a minute", "fires_at": SF.HEATING_C_PER_MIN, "holds_s": SF.HOLD_S},
                "repeated_jolts": {"reading": "sudden jolts in 10 minutes", "unit": "count", "fires_at": SF.JOLTS_10M}},
            "lasting": out_lasting,
            "one_moment": {"stays_s": SF.IMPACT_HOLD_S, **one}}

    def controls(self) -> dict:
        """Everything a connected program (a dashboard, say) can tell the device: for GET /controls and GET /."""
        lo, hi = SF.SENSITIVITY_RANGE
        dials = self._dials()
        number = f"a number from {lo:g} (least sensitive) to {hi:g} (most); each 0.25 halves or doubles how far outside normal counts as wrong"
        settings = {
            "sensitivity": {"now": dials["sensitivity"], "default": SF.DEFAULT_SENSITIVITY, "takes": number,
                            "does": "how far outside this machine's normal counts as wrong, for every check that "
                                    "compares with normal (vibration, heat, sound, the model). The fixed limits "
                                    "(vibration over 11 mm/s, over 100 degC and so on) never change"},
            "spike_sensitivity": {"now": dials["spike_sensitivity"], "default": SF.DEFAULT_SENSITIVITY, "takes": number,
                                  "does": "the same, for single spikes only: one sudden impact, and one second of "
                                          "the model over its spike line"},
            **{k: {"now": dials.get(k), "default": None, "takes": number + "; or null to follow sensitivity",
                   "does": f"sensitivity for {what} alone"}
               for k, what in (("model_sensitivity", "the learned model"), ("vibration_sensitivity", "vibration against normal"),
                               ("heat_sensitivity", "temperature against normal"), ("sound_sensitivity", "sound against normal"),
                               ("early_warning_sensitivity", "the early warning"))},
            "early_warning": {"now": dials.get("early_warning", True), "default": True, "takes": "true or false",
                              "does": "the early warning (small lasting changes, before they count as wrong) on or off"},
        }
        return {"note": "settings stay until changed again and survive a restart; commands happen once. Nothing "
                        "else can be sent to the device. There is no password: anyone on the network can send these",
                "two_ways_in": {
                    "straight to the device": "POST to this server (port 8765), from a dashboard or any program on the "
                                              "same network; a browser page may too (any origin is allowed)",
                    "through the laptop platform": "when the device uploads to the platform (EFFENTE_PLATFORM_URL), the "
                                                   "platform's replies may carry \"settings\": {...} and \"commands\": "
                                                   "[{\"id\": \"...\", \"command\": \"clear_history\"}]; each id is "
                                                   "done once. The device asks every few seconds, so this way is slower"},
                "settings": {"send": 'POST /settings with a JSON object of the settings to change, e.g. {"sensitivity": 0.7}; '
                                     "GET /settings shows them and what they mean in numbers", **settings},
                "commands": {"send": 'POST /command with {"command": "<name>"}', **COMMANDS},
                "now": {"mode": self.mode, "state": SCREEN_NAMES[self.mode], "model": self.model_version,
                        "recordings_in_model": [Path(p).name for p in self.sessions]},
                "states": {"IDLE": "reading the sensors; nothing recorded, nothing compared with a learned normal",
                           "RECORDING": "learning this machine's normal",
                           "TRAINING": "making the model from what was recorded (about half a minute)",
                           "DETECTING": "checking the machine against the model"}}

    def platform_settings(self, settings: dict) -> None:
        """Settings in a reply from the platform (CONTRACT.md section 7: sensitivity; the other settings are
        taken the same way if the platform sends them): each that differs from the device's own is applied and
        saved, so it survives a restart."""
        now = self._dials()
        changes = {k: v for k, v in settings.items() if k in SETTINGS and v != now.get(k)
                   and not (v is None and k not in OVERRIDES)}  # any setting the dashboard has a value for
        if not changes:
            return
        try:
            self.settings(changes)
        except ValueError as e:
            self.message = f"platform setting not used: {e}"[:80]

    def platform_commands(self, commands: list) -> None:
        """Commands in a reply from the platform: what the dashboard asked for, passed on. Each is
        {"id": ..., "command": "clear_history"}; the platform repeats them in its replies until it sees them done,
        so each id is done once (the ids done are remembered, the last 200, also across a restart). A "command"
        event on the stream says what was done or why not."""
        for c in commands[:20]:
            if not isinstance(c, dict) or "id" not in c:
                continue
            cid = str(c["id"])[:64]
            if cid in self._commands_done:
                continue
            self._commands_done.append(cid)
            del self._commands_done[:-200]
            try:
                reply = self.command(str(c.get("command")), c)
                self._event("command", {"id": cid, "from": "platform", **reply})
            except ValueError as e:
                self._event("command", {"id": cid, "from": "platform", "error": str(e)})
        try:
            (self.dir / "commands_done.json").write_text(json.dumps(self._commands_done))
        except OSError:
            pass

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

    def _clear_history_now(self) -> None:
        """Forget what the device remembers of past vibration, sound and heat (the history, data/history.npz): the
        trends, and which level counts as the machine running. Called from the pipeline thread, between rows.
        Recordings, the model and the settings are untouched."""
        self._clear_history = False
        self.history.__init__()  # the same object the features and the recorder hold, emptied in place
        self.history.save(self.dir / "history.npz")
        self._event("history_cleared", {"mode": self.mode})

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
          "<speed>"          running speed in Hz, a whole number; 0 when no speed is found (the user, 13 Nov)
        Vibration, peak and temperature (shown in F) come from the samples on the microcontroller, so they
        move at the screen's own rate; this changes only when the mode, the minute or what is wrong does."""
        r = readings(self.latest)
        speed = f"{r['speed_hz']:.0f}" if r["speed_hz"] else "0"
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
        now = time.localtime(self.clock())  # 12-hour, with AM or PM, and 12 for midnight and noon (the user, 14 Nov)
        clock = f"{now.tm_hour % 12 or 12}:{now.tm_min:02d}{'AM' if now.tm_hour < 12 else 'PM'}"
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
            # What may be sent back, in short, in every status message (once a second on the stream); the full
            # list, with what each does and takes, is "you_can_send" in GET /latest, and GET /controls.
            s["you_can_send"] = {"commands": {"how": 'POST /command {"command": "<name>"}', "names": list(COMMANDS),
                                              "useful_now": USEFUL_NOW[self.mode]},
                                 "settings": {"how": 'POST /settings {"<name>": <value>}', "names": list(SETTINGS)},
                                 "details": "GET /controls"}
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
# What can be sent to POST /command, and what each does (shown as it is to whoever asks: GET /command, /controls).
COMMANDS = {
    "record": "start recording (from IDLE or DETECTING). With \"keep\": true (the default) what is recorded is added to "
              "the recordings the model was learned from, so a model can be learned in several goes; with "
              "\"keep\": false it starts over, for a machine that is not what it was (after a repair, say). "
              "Example: {\"command\": \"record\", \"keep\": false}",
    "stop": "stop recording and train: TRAINING, then DETECTING. The model is made again from every recording kept "
            "(not adjusted from the old model: training takes about half a minute, and starting from the rows "
            "themselves leaves nothing of an old mistake behind). Refused if too little was recorded: 2 minutes "
            "for a first recording, 30 seconds when adding",
    "idle": "stop without training, for while someone works on the machine. A recording in progress ends and is "
            "kept for the next training; in IDLE nothing is compared with the learned normal (the fixed limits "
            "still apply). The model is kept",
    "detect": "from IDLE, back to DETECTING with the model there is",
    "train": "make the model again from the recordings there are, without recording more: TRAINING, then DETECTING",
    "press": "the same as pressing the button: starts a recording from nothing (as record with keep false), or "
             "stops the recording and trains (a recording under 2 minutes carries on); does nothing while training",
    "clear_history": "forget what the device remembers of past vibration, sound and heat: the trends, and which "
                     "level counts as the machine running. Use it after the device was carried or handled and "
                     "before recording on a machine, or when it wrongly says the machine is stopped. Recordings, "
                     "the model and the settings are kept; the trend readings are empty again until time has passed",
}
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
