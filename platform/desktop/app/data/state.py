"""What the pages read: DashboardState, fed by the receiver's API and stream.

`StateReads` holds the data and every read; `DashboardState` adds the live
feed and the actions; tests/fakes.py `FakeState` adds fixed data. Pages bind
to either. Every label and judgement comes from logic.py.
"""

from __future__ import annotations

import getpass
import math
import os
import sys
import threading
import time
from urllib.parse import urlsplit

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Qt, Signal

from . import logic, paths
from .api import ApiClient, ApiError, StreamReader


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

    def __init__(self, parent=None):
        super().__init__(parent)
        self._s = logic.create_state()
        self._s["alarms"] = {}
        self._s["machines"] = []
        self._machine = None  # picked by hand with set_machine; None = follow the shown device
        self._agent_message = ""

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

    def last_agent_message(self) -> str:
        return self._agent_message

    def _say(self, text: str) -> None:
        self._agent_message = text
        self.agent_message.emit(text)

    # ---- settings -----------------------------------------------------------

    def sensitivity(self) -> dict:
        """{"reported": the device's value or None, "wanted": the dashboard's or None}."""
        return logic.sensitivity_of(self._s, self.device_id())

    # ---- simulator ----------------------------------------------------------

    def sim(self):
        """None, or the running simulated device: `.key(k)` and `.status()`."""
        return


LIVE_KEY = "ANTHROPIC_API_KEY"
NO_KEY_MESSAGE = (
    "Live mode needs ANTHROPIC_API_KEY in the laptop's environment. Set it before starting the app, "
    "or run the stand-in."
)


class DashboardState(StateReads):
    """The live state: GET /api/state and /api/stream from the receiver, and
    the actions. `start()` begins reading; `stop()` ends it and any agent run.

    `api` is an ApiClient or a base URL. `host` is the ReceiverHost, when the
    app runs one, for `sim()`. `report_script` is platform/report.py unless a
    test passes a copy.
    """

    _done = Signal(object)  # a callable to run on the Qt thread, from a worker thread

    def __init__(self, api, host=None, report_script=None, parent=None):
        super().__init__(parent)
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
        self._agent_output = b""

    # ---- lifecycle --------------------------------------------------------

    def start(self) -> None:
        self.reader.start()

    def stop(self) -> None:
        self.reader.stop()
        if self._agent is not None and self._agent.state() != QProcess.NotRunning:
            self._agent.kill()
            self._agent.waitForFinished(3000)

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
        Live mode needs ANTHROPIC_API_KEY in the environment and is refused without it;
        the key is never asked for. Returns whether a run started. The run's tool calls
        arrive over the stream (runs_changed); agent_message says why a run did not start or failed."""
        machine = self.current_machine()
        if mode not in ("standin", "live"):
            raise ValueError("mode is 'standin' or 'live'")
        if machine is None:
            self._say("No machine to report on yet.")
            return False
        if self.agent_running():
            self._say(f"A report for {machine} is already running.")
            return False
        if mode == "live" and not os.environ.get(LIVE_KEY, "").strip():
            self._say(NO_KEY_MESSAGE)
            return False
        args = [self.report_script, "--machine", machine, "--port", str(self._port())]
        if mode == "live":
            args.append("--live")
        proc = QProcess(self)
        proc.setProcessEnvironment(QProcessEnvironment.systemEnvironment())
        proc.setProcessChannelMode(QProcess.MergedChannels)
        proc.readyReadStandardOutput.connect(self._agent_read)
        proc.finished.connect(self._agent_finished)
        proc.errorOccurred.connect(self._agent_error)
        self._agent = proc
        self._agent_output = b""
        self._say("")
        proc.start(sys.executable, args)
        self.runs_changed.emit()
        return True

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


def _agent_failure(output: str) -> str:
    """The line worth showing from a failed report.py run: its own message, as written."""
    lines = [x.strip() for x in output.splitlines() if x.strip()]
    for line in reversed(lines):
        if not line.startswith(("{", "}", "[", "]", '"')) and not line.endswith((",", "{", "[")):
            return line
    return "The report agent did not write a report."
