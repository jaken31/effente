"""What the pages read: DashboardState, fed by the receiver's API and stream.

`StateReads` holds the data and every read; `DashboardState` adds the live
feed and the actions; tests/fakes.py `FakeState` adds fixed data. Pages bind
to either. Every label and judgement comes from logic.py.
"""

from __future__ import annotations

import datetime as dt
import getpass
import math
import os
import signal
import sys
import threading
import time
from collections import deque
from urllib.parse import urlsplit

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Qt, QTimer, Signal

from . import logic, model_server, paths
from .api import ApiClient, ApiError, StreamReader
from .model_settings import ModelSettings

READINGS_KEPT = 300  # readings kept per device for the Live page's sparklines: 5 minutes at one a second


def _ms(record) -> float | None:
    v = logic.parse_time(record.get("at"))
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else v


def _within(records: list, span_s: float | None) -> list:
    """Records from `span_s` seconds before the newest one's `at` on; all when None.
    A record whose time does not parse is kept."""
    if span_s is None or not records:
        return list(records)
    end = _ms(records[-1])
    if end is None:
        return list(records)
    start = end - span_s * 1000.0
    return [r for r in records if (_ms(r) or end) >= start]


def _iso(ms: float) -> str:
    return dt.datetime.fromtimestamp(ms / 1000.0, dt.UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _slim(record: dict, spectrum: bool, frames: bool) -> dict:
    out = record if spectrum else {k: v for k, v in record.items() if k != "spectrum"}
    heat = out.get("heat")
    if not frames and isinstance(heat, dict) and "frames" in heat:
        out = dict(out)
        out["heat"] = {k: v for k, v in heat.items() if k != "frames"}
    return out


class StateReads(QObject):
    """The data interface of platform/desktop/INTERFACES.md: signals and reads.

    Everything here runs on the Qt thread. Returned dicts and lists are the
    state's own: read them, do not change them.
    """

    snapshot_changed = Signal()
    record_added = Signal(dict)
    alarms_changed = Signal()
    runs_changed = Signal()
    problems_changed = Signal()
    settings_changed = Signal()
    connection_changed = Signal(bool)
    # Additions to INTERFACES.md: a sentence about the agent run for the Agents
    # page (a refusal, or report.py's own message), and why an action failed.
    agent_message = Signal(str)
    action_failed = Signal(str)
    # An alarm on any machine that opened while the app was watching (contract
    # section 6). Alarms there at the first look are not new.
    alarm_opened = Signal(dict)
    # range_records() has the range it was asked for, read from the receiver.
    range_loaded = Signal()
    # The local model's folder or size was changed, or its server's state moved.
    model_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._s = logic.create_state()
        self._s["alarms"] = {}
        self._s["machines"] = []
        self._machine = None  # picked by hand with set_machine; None = follow the shown device
        self._agent_message = ""
        self._seen_alarms = None  # alarm ids already looked at; None before the first look
        self._readings_history: dict = {}  # device_id -> deque of readings dicts, for the sparklines
        self.model_settings = ModelSettings()  # remembers nothing unless given a store
        self._model_status = model_server.Status(model_server.STOPPED, "Not running.")
        self.alarms_changed.connect(self._note_alarms)  # first, so alarm_opened precedes page refreshes

    def _note_alarms(self) -> None:
        """Emit alarm_opened for each open alarm not seen before. The first call only looks."""
        alarms = self._s.get("alarms") or {}
        if self._seen_alarms is None:
            self._seen_alarms = set(alarms)
            return
        for alarm_id, alarm in alarms.items():
            if alarm_id not in self._seen_alarms:
                self._seen_alarms.add(alarm_id)
                if alarm.get("open"):
                    self.alarm_opened.emit(alarm)

    # ---- clock ------------------------------------------------------------

    def now_ms(self) -> float:
        """The clock every age is measured on."""
        return time.time() * 1000.0

    # ---- machine and device -------------------------------------------------

    def view(self) -> dict:
        """The logic.py state, for calling logic functions not wrapped here."""
        return self._s

    def machines(self) -> list:
        return logic.known_machines(self._s)

    def current_machine(self):
        if self._machine:
            return self._machine
        d = self._s["devices"].get(logic.shown_device_id(self._s))
        if d and d.get("machine_id"):
            return d["machine_id"]
        machines = self.machines()
        return machines[0] if machines else None

    def set_machine(self, machine_id) -> None:
        if machine_id == self._machine:
            return
        self._machine = machine_id or None
        self._machine_picked()
        self.snapshot_changed.emit()
        self.alarms_changed.emit()
        self.runs_changed.emit()
        self.problems_changed.emit()
        self.settings_changed.emit()

    def _machine_picked(self) -> None:
        """Hook: the live state loads the machine's history here."""

    def device_id(self):
        """The device shown for the current machine, or None."""
        if self._machine:
            return logic.device_for_machine(self._s, self._machine)
        return logic.shown_device_id(self._s)

    def _device(self):
        return self._s["devices"].get(self.device_id())

    def devices(self) -> dict:
        """device_id -> device view (logic.ensure_device's fields, plus `sensitivity` and `settings`)."""
        return self._s["devices"]

    def model(self):
        """Message 1 for the shown device, or None."""
        return logic.model_for(self._s, self.device_id())

    # ---- readings -----------------------------------------------------------

    def latest(self):
        """The newest record of the current machine, with spectrum and images, or None."""
        d = self._device()
        if d and d.get("latest") and d["latest"].get("machine_id") == self.current_machine():
            return d["latest"]
        history = self._s["history"].get(self.current_machine()) or []
        return history[-1] if history else None

    def records(self, limit=logic.HISTORY_CAP, spectrum=False, frames=False) -> list:
        """The current machine's newest `limit` records, oldest first, without
        `spectrum` and `heat.frames` unless asked, as the history route."""
        history = self._s["history"].get(self.current_machine()) or []
        chosen = history[-limit:] if limit and limit > 0 else []
        return [_slim(r, spectrum, frames) for r in chosen]

    def range_records(self, span_s: float | None = None) -> list:
        """The current machine's records from `span_s` seconds before its newest one
        (every one when None), oldest first, without `spectrum` and `heat.frames`.
        Here from the live history only; the live state reads the whole range."""
        return _within(self.records(logic.HISTORY_CAP), span_s)

    def readings(self) -> dict:
        """The shown device's own readings (the numbers on its screen: vib_mg, sound_db,
        imu_temp_c, ...), in any mode. {} when it sent none or is not connected."""
        d = self._device()
        kept = d.get("readings") if d and d.get("connected") else None
        return dict(kept["readings"]) if kept else {}

    def live_record(self):
        """What the spectrum and heat panels draw. While the shown device is connected and not
        watching it sends no records, so this is the `spectrum` and `heat` of its newest
        check-in when it carried them (contract section 3); otherwise latest()."""
        d = self._device()
        kept = d.get("readings") if d and d.get("connected") and d.get("mode") != "watching" else None
        if kept and ("spectrum" in kept or "heat" in kept):
            return {k: kept[k] for k in ("at", "spectrum", "heat") if k in kept}
        return self.latest()

    def spectrum_model(self):
        """model(), or just the band edges a check-in carried when no model is announced."""
        model = self.model()
        d = self._device()
        edges = ((d or {}).get("readings") or {}).get("spectrum_edges_hz")
        if model is None and isinstance(edges, list):
            return {"spectrum_edges_hz": edges}
        return model

    def readings_history(self) -> list:
        """The shown device's readings since this app started, oldest first, at most 5 minutes."""
        return list(self._readings_history.get(self.device_id(), ()))

    def problems(self) -> list:
        """The shown device's problems now, limits first as sent. [] while not connected."""
        d = self._device()
        return logic.problem_order(d["problems"]) if d else []

    def freshness(self) -> dict:
        """{"status": logic.device_status, "age_s": seconds since the newest record or None,
        "platform_up": the stream is open}."""
        device_id = self.device_id()
        now = self.now_ms()
        d = self._s["devices"].get(device_id)
        age = None
        if d and d.get("last_record_local_ms") is not None:
            age = max(0.0, (now - d["last_record_local_ms"]) / 1000.0)
        return {
            "status": logic.device_status(self._s, device_id, now),
            "age_s": age,
            "platform_up": bool(self._s["platform_up"]),
        }

    # ---- logic wrappers (pages may call logic directly with view()) ---------

    def headline(self) -> dict:
        return logic.headline(self._s, self.device_id(), self.now_ms())

    def model_line(self):
        return logic.model_line(self._s, self.device_id())

    def moved_readings(self) -> list:
        return logic.moved_readings(self._s, self.device_id())

    def feature_texts(self) -> dict:
        return logic.feature_texts(self._s, self.device_id())

    def simulated_sources(self) -> list:
        return logic.simulated_sources(self._s, self.device_id(), self.latest_run())

    # ---- alarms -------------------------------------------------------------

    def alarms(self) -> list:
        """The current machine's alarms, newest `start` first."""
        return logic.alarms_for_machine(self._s, self.current_machine())

    def unanswered_alarms(self) -> list:
        return [a for a in self.alarms() if logic.is_unanswered(a)]

    def answerer(self) -> str:
        """The name sent as an answer's `by`: EFFENTE_ANSWERER, else the laptop's login name."""
        name = os.environ.get("EFFENTE_ANSWERER", "").strip()
        if name:
            return name
        try:
            return getpass.getuser()
        except (OSError, KeyError):
            return "laptop"

    # ---- agent runs ---------------------------------------------------------

    def runs(self) -> list:
        """The current machine's agent runs, newest first."""
        machine = self.current_machine()
        entries = [e for e in self._s["runs"].values() if e["run"].get("machine_id") == machine]

        def key(e):
            t = logic.parse_time(e["run"].get("at"))
            return (-math.inf if math.isnan(t) else t, e["order"])

        return [e["run"] for e in sorted(entries, key=key, reverse=True)]

    def latest_run(self):
        return logic.latest_run_for_machine(self._s, self.current_machine())

    def agent_running(self) -> bool:
        run = self.latest_run()
        return bool(run and run.get("status") == "running") or self._agent_process_alive()

    def _agent_process_alive(self) -> bool:
        return False

    def own_agent_running(self) -> bool:
        """Whether a report run this app started is still going. Closing the app ends it:
        the receiver it reads and writes to runs in this process."""
        return self._agent_process_alive()

    def stop_agent(self) -> None:
        """End the report run this app started, if any, with every process it started."""

    def last_agent_message(self) -> str:
        return self._agent_message

    def _say(self, text: str) -> None:
        self._agent_message = text
        self.agent_message.emit(text)

    # ---- local model --------------------------------------------------------

    def model_folder(self) -> str:
        """The folder the model files are in, as chosen (default ~/effente-models)."""
        return self.model_settings.folder()

    def model_size(self) -> str:
        """'small' or 'large': small.gguf or large.gguf in the model folder."""
        return self.model_settings.size()

    def set_model_folder(self, folder) -> None:
        self.model_settings.set_folder(folder)
        self.model_changed.emit()

    def set_model_size(self, size: str) -> None:
        self.model_settings.set_size(size)
        self.model_changed.emit()

    def model_status(self) -> model_server.Status:
        """The model server's state as last seen: `.state`, `.message`, `.ours`."""
        return self._model_status

    def start_model_server(self) -> None:
        """Start llama-server with the chosen model file, unless one already answers."""

    def stop_model_server(self) -> None:
        """Stop the model server this app started. One started elsewhere is left alone."""

    def refresh_model_status(self) -> None:
        """Look at the model server again; model_changed fires if its state moved."""

    def _model_status_is(self, status: model_server.Status) -> None:
        if status != self._model_status:
            self._model_status = status
            self.model_changed.emit()

    # ---- settings -----------------------------------------------------------

    def sensitivity(self) -> dict:
        """{"reported": the device's value or None, "wanted": the dashboard's or None}."""
        return logic.sensitivity_of(self._s, self.device_id())

    # ---- simulator ----------------------------------------------------------

    def sim(self):
        """None, or the running simulated device: `.key(k)` and `.status()`."""
        return


MODES = ("standin", "local")
NO_SERVER_HINT = "Start the model server, or run the stand-in."
MODEL_POLL_MS = 2000


class DashboardState(StateReads):
    """The live state: GET /api/state and /api/stream from the receiver, and
    the actions. `start()` begins reading; `stop()` ends it and any agent run.

    `api` is an ApiClient or a base URL. `host` is the ReceiverHost, when the
    app runs one, for `sim()`. `report_script` is platform/report.py unless a
    test passes a copy. `settings` is the QSettings the model folder and size
    are kept in; without one they last for this run only. `models` is the
    ModelServer, which `stop()` also stops.
    """

    _done = Signal(object)  # a callable to run on the Qt thread, from a worker thread

    def __init__(self, api, host=None, report_script=None, parent=None, settings=None, models=None):
        super().__init__(parent)
        self.model_settings = ModelSettings(settings)
        self.models = models or model_server.ModelServer()
        self._model_poll = QTimer(self)
        self._model_poll.setInterval(MODEL_POLL_MS)
        self._model_poll.timeout.connect(self.refresh_model_status)
        self._model_asking = False
        self._model_changes = 0  # starts and stops so far: an answer asked for before one is out of date
        self.api = api if isinstance(api, ApiClient) else ApiClient(api)
        self.host = host
        self.report_script = str(report_script or paths.REPORT_SCRIPT)
        self.reader = StreamReader(self.api, self)
        self.reader.connected.connect(self._on_connected, Qt.QueuedConnection)
        self.reader.snapshot.connect(self._on_snapshot, Qt.QueuedConnection)
        self.reader.event.connect(self._on_event, Qt.QueuedConnection)
        self._done.connect(self._run_done, Qt.QueuedConnection)
        self._loaded_machine = None
        self._loading = None  # machine whose history is being fetched
        self._agent = None  # QProcess of the agent run this app started
        # The range History asked for: {"key": (machine, span_s), "records": list, or
        # None while loading or after a failure, "pending": streamed while loading}.
        self._range = None
        self._agent_output = b""

    # ---- lifecycle --------------------------------------------------------

    def start(self) -> None:
        self.reader.start()
        self._model_poll.start()
        self.refresh_model_status()

    def stop(self) -> None:
        self._model_poll.stop()
        self.reader.stop()
        self.stop_agent()
        self.models.stop()

    # ---- background work --------------------------------------------------

    def _background(self, work, done) -> None:
        """Run `work()` on a worker thread, then `done(result, error)` on the Qt thread."""

        def run():
            try:
                result, error = work(), None
            except ApiError as e:
                result, error = None, str(e)
            self._done.emit(lambda: done(result, error))

        threading.Thread(target=run, name="effente-api", daemon=True).start()

    def _run_done(self, fn) -> None:
        fn()

    # ---- the stream -------------------------------------------------------

    def _on_connected(self, up: bool) -> None:
        self._s["platform_up"] = up
        self._s["platform_lost"] = not up
        self.connection_changed.emit(up)
        self.snapshot_changed.emit()

    def _on_snapshot(self, snap) -> None:
        logic.apply_snapshot(self._s, snap, self.now_ms())
        logic.apply_laptop_snapshot(self._s, snap)
        self._range = None  # records may have been stored while the stream was down: read it again
        self._load_machine(force=True)
        self.snapshot_changed.emit()
        self.alarms_changed.emit()
        self.problems_changed.emit()
        self.settings_changed.emit()
        self.runs_changed.emit()

    def _on_event(self, name: str, data) -> None:
        shown = self.device_id()
        before = self._problem_key()
        logic.apply_event(self._s, name, data, self.now_ms(), slim=False)
        logic.apply_laptop_event(self._s, name, data)
        if self.current_machine() != self._loaded_machine:
            self._load_machine()
        if name == "record":
            self._range_add(data)
            if data.get("machine_id") == self.current_machine():
                self.record_added.emit(data)
            if data.get("device_id") != shown:
                self.snapshot_changed.emit()  # the shown device may have changed
        elif name == "alarm":
            self.alarms_changed.emit()
        elif name == "agent_run":
            self.runs_changed.emit()
        elif name == "settings":
            self.settings_changed.emit()
        elif name in ("model", "device"):
            self.snapshot_changed.emit()
        elif name == "readings" and isinstance(data, dict) and isinstance(data.get("readings"), dict):
            kept = self._readings_history.setdefault(data.get("device_id"), deque(maxlen=READINGS_KEPT))
            kept.append(data["readings"])
            if data.get("device_id") == self.device_id():
                self.snapshot_changed.emit()
        if self._problem_key() != before:
            self.problems_changed.emit()

    def _problem_key(self):
        d = self._device()
        if not d:
            return None
        return d["wrong"], tuple((p.get("name"), p.get("value")) for p in d["problems"])

    def _machine_picked(self) -> None:
        self._load_machine()

    def _load_machine(self, force=False) -> None:
        """Fetch the current machine's records (with spectrum and images) and runs, merged with what streamed in."""
        machine = self.current_machine()
        if not machine or (not force and machine in (self._loaded_machine, self._loading)):
            return
        self._loading = machine
        self._s["history"].setdefault(machine, [])  # streamed records are kept from now and merged in

        def work():
            return (self.api.machine_records(machine, logic.HISTORY_CAP, spectrum=True, frames=True),
                    self.api.machine_runs(machine))

        def done(result, error):
            if self._loading == machine:
                self._loading = None
            if error:
                self._loaded_machine = None  # try again on the next event
                return
            records, runs = result
            logic.set_history(self._s, machine, records, slim=False)
            logic.add_runs(self._s, runs)
            self._loaded_machine = machine
            if machine == self.current_machine():
                self.snapshot_changed.emit()
                self.runs_changed.emit()

        self._background(work, done)

    # ---- ranges -----------------------------------------------------------

    def range_records(self, span_s: float | None = None) -> list:
        """As StateReads, but over every stored record: the first call for a machine
        and span reads the range from the receiver in the background and returns the
        live history meanwhile; range_loaded fires when it is in."""
        key = (self.current_machine(), span_s)
        r = self._range
        if r is None or r["key"] != key:
            r = self._load_range(key)
        if r["records"] is None:
            return super().range_records(span_s)
        return _within(r["records"], span_s)

    def _load_range(self, key) -> dict:
        machine, span_s = key
        r = self._range = {"key": key, "records": None, "pending": []}
        if machine is None:
            r["records"] = []
            return r
        live = self.records(logic.HISTORY_CAP)
        newest = _ms(live[-1]) if live else None
        since = _iso(newest - span_s * 1000.0) if span_s is not None and newest is not None else None

        def done(records, error):
            if self._range is not r:
                return  # another range was asked for meanwhile
            if error:
                del r["pending"]  # no retry until the span, the machine or the stream changes
                self.action_failed.emit(f"History not loaded: {error}")
                return
            last = records[-1].get("seq") if records else None
            r["records"] = records + [p for p in r.pop("pending") if last is None or p.get("seq", 0) > last]
            self.range_loaded.emit()

        self._background(lambda: self.api.machine_range(machine, since), done)
        return r

    def _range_add(self, record: dict) -> None:
        """A streamed record joins the loaded range of its machine; a span drops what fell out."""
        r = self._range
        if r is None or record.get("machine_id") != r["key"][0]:
            return
        slim = _slim(record, False, False)
        if r["records"] is None:
            if "pending" in r:
                r["pending"].append(slim)
            return
        records = r["records"]
        if records and isinstance(records[-1].get("seq"), int) and slim.get("seq", 0) <= records[-1]["seq"]:
            return  # already read
        records.append(slim)
        span_s = r["key"][1]
        if span_s is not None and (end := _ms(slim)) is not None:
            cut = 0
            while cut < len(records) - 1 and (_ms(records[cut]) or end) < end - span_s * 1000.0:
                cut += 1
            del records[:cut]

    # ---- actions ----------------------------------------------------------

    def answer_alarm(self, alarm_id, verdict, finding=None) -> None:
        """Send an answer (contract section 6) with `by` from answerer(). alarms_changed fires once stored."""
        if verdict not in logic.VERDICT_TEXT:
            self.action_failed.emit(f"Unknown verdict: {verdict}")
            return
        by = self.answerer()

        def done(alarm, error):
            if error:
                self.action_failed.emit(f"Answer not saved: {error}")
                return
            # The stream may already have brought this answer, or a later one.
            have = self._s["alarms"].get(alarm_id)
            if have is None or len(alarm.get("answers") or []) > len(have.get("answers") or []):
                logic.apply_laptop_event(self._s, "alarm", alarm)
            self.alarms_changed.emit()

        self._background(lambda: self.api.answer_alarm(alarm_id, verdict, by, finding=finding), done)

    def set_sensitivity(self, value) -> None:
        """Ask the shown device for a sensitivity from 0 to 1, or None to clear (contract section 7).
        settings_changed fires when the receiver has it, and again when the device reports it."""
        device_id = self.device_id()
        if device_id is None:
            self.action_failed.emit("No device to set the sensitivity of.")
            return
        if value is not None and logic._sensitivity_or_none(value) is None:
            self.action_failed.emit("Sensitivity must be from 0 to 1.")
            return
        value = None if value is None else float(value)

        def done(settings, error):
            if error:
                self.action_failed.emit(f"Sensitivity not set: {error}")
                return
            d = logic.ensure_device(self._s, device_id)
            d["settings"] = settings
            d.setdefault("sensitivity", None)
            self.settings_changed.emit()

        self._background(lambda: self.api.put_settings(device_id, value), done)

    def start_agent(self, mode="standin") -> bool:
        """Run `python3 platform/report.py` for the current machine, as a local process.
        'standin' writes a rule-based report with no model. 'local' asks the model server
        on this laptop, and is refused with the reason when that server does not answer.
        Returns whether a run started. The run's tool calls arrive over the stream
        (runs_changed); agent_message says why a run did not start or failed."""
        machine = self.current_machine()
        if mode not in MODES:
            raise ValueError("mode is 'standin' or 'local'")
        if machine is None:
            self._say("No machine to report on yet.")
            return False
        if self.agent_running():
            self._say(f"A report for {machine} is already running.")
            return False
        args = [self.report_script, "--machine", machine, "--port", str(self._port()), "--backend", mode]
        if mode == "local":
            answer, why = model_server.probe(self.models.url)
            if answer != model_server.READY:
                self._say(why if answer == model_server.LOADING else f"{why} {NO_SERVER_HINT}")
                self.refresh_model_status()
                return False
            # Named on every run, so nothing in the environment can point the agent elsewhere.
            args += ["--llm-url", self.models.url]
        proc = QProcess(self)
        proc.setProcessEnvironment(QProcessEnvironment.systemEnvironment())
        proc.setProcessChannelMode(QProcess.MergedChannels)
        proc.readyReadStandardOutput.connect(self._agent_read)
        proc.finished.connect(self._agent_finished)
        proc.errorOccurred.connect(self._agent_error)
        if _OWN_SESSION is not None:
            # report.py runs the agent as its own child. A session of its own makes
            # them one process group, so stop_agent can end both.
            proc.setUnixProcessParameters(_OWN_SESSION)
        self._agent = proc
        self._agent_output = b""
        self._say("")
        proc.start(sys.executable, args)
        self.runs_changed.emit()
        return True

    def stop_agent(self) -> None:
        proc = self._agent
        if proc is None or proc.state() == QProcess.NotRunning:
            return
        group = int(proc.processId()) if _OWN_SESSION is not None else 0
        if group > 0:
            _signal_group(group, signal.SIGTERM)  # report.py and the agent it started
            if not proc.waitForFinished(3000):
                _signal_group(group, signal.SIGKILL)
        else:
            proc.kill()
        proc.waitForFinished(3000)
        if group > 0:
            _signal_group(group, signal.SIGKILL)  # anything left in the group once report.py is gone

    def _port(self) -> int:
        return urlsplit(self.api.base).port or 80

    def _agent_process_alive(self) -> bool:
        return self._agent is not None and self._agent.state() != QProcess.NotRunning

    def _agent_read(self) -> None:
        if self._agent is not None:
            self._agent_output += bytes(self._agent.readAllStandardOutput())

    def _agent_error(self, error) -> None:
        if error == QProcess.FailedToStart:
            self._say(f"Could not start {self.report_script}.")
            self._agent = None
            self.runs_changed.emit()

    def _agent_finished(self, code, status) -> None:
        self._agent_read()
        text = self._agent_output.decode("utf-8", errors="replace")
        self._agent = None
        if status != QProcess.NormalExit:
            self._say("The report agent stopped before it finished.")
        elif code != 0:
            self._say(_agent_failure(text))
        else:
            self._say("")
        self.runs_changed.emit()

    # ---- local model ------------------------------------------------------

    def start_model_server(self) -> None:
        self._model_changes += 1
        self._model_status_is(self.models.start(self.model_settings.model_file()))

    def stop_model_server(self) -> None:
        self._model_changes += 1
        self.models.stop()
        self.refresh_model_status()

    def refresh_model_status(self) -> None:
        """Ask the model server how it is, off the Qt thread; model_changed fires if it moved."""
        if self._model_asking:
            return
        self._model_asking = True
        asked_at = self._model_changes

        def ask():
            status = self.models.status()
            self._done.emit(lambda: self._model_asked(status, asked_at))

        threading.Thread(target=ask, name="effente-model", daemon=True).start()

    def _model_asked(self, status, asked_at: int) -> None:
        self._model_asking = False
        if asked_at == self._model_changes:
            self._model_status_is(status)
        else:
            self.refresh_model_status()

    def stored_count(self):
        """Records the receiver has stored for the current machine, or None when the
        receiver is not in this process. Read from the embedded receiver's store:
        no route gives the count, and records() stops at the history length."""
        machine = self.current_machine()
        store = getattr(getattr(self.host, "receiver", None), "store", None)
        if store is None or machine is None:
            return None
        with store.lock:
            return len(store.by_machine.get(machine, []))

    # ---- simulator --------------------------------------------------------

    def sim(self):
        return self.host.sim if self.host is not None else None


# QProcess's "start in a new session" flag, where the platform has sessions.
_OWN_SESSION = (QProcess.UnixProcessFlag.CreateNewSession
                if os.name == "posix" and hasattr(QProcess, "setUnixProcessParameters") else None)


def _signal_group(group: int, sig) -> None:
    try:
        os.killpg(group, sig)
    except (ProcessLookupError, PermissionError):
        pass  # already gone


def _agent_failure(output: str) -> str:
    """The line worth showing from a failed report.py run: its own message, as written."""
    lines = [x.strip() for x in output.splitlines() if x.strip()]
    for line in reversed(lines):
        if not line.startswith(("{", "}", "[", "]", '"')) and not line.endswith((",", "{", "[")):
            return line
    return "The report agent did not write a report."
