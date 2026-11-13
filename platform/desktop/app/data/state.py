"""What the pages read: DashboardState, fed by the receiver's API and stream.

`StateReads` holds the data and every read; `DashboardState` adds the live
feed and the actions; tests/fakes.py `FakeState` adds fixed data. Pages bind
to either. Every label and judgement comes from logic.py.
"""

from __future__ import annotations

import getpass
import math
import os
import time

from PySide6.QtCore import QObject, Signal

from . import logic


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
    # Additions (see the report): a sentence about the agent run for the
    # Agents page, such as a refusal or report.py's own message.
    agent_message = Signal(str)

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
        return d["problems"] if d else []

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
