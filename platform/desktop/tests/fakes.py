"""FakeState: the DashboardState interface on fixed data, for page tests.

    from fakes import FakeState          # with platform/desktop/tests on sys.path
    state = FakeState()
    page.bind(state)

The data is real code's output, not made up here. Once per process, the
records of platform/sim/sample_replay.jsonl (with the simulated device's heat
added, seed 1) are posted to a real Receiver on a free port, alarm-5 is
answered, and the real stand-in agent (device/agent) writes one report on
them. FakeState holds what the receiver then returns. All of it is simulated:
`simulated: true` everywhere.

What the sample holds, record by record (seq 1 to 10, one a second):
normal x2; normal with too_hot x2; unbalance_like with too_hot x2 (opens
alarm-5, answered "real"); stopped with too_hot x2 (closes it); unrecognised
pattern x2 (opens alarm-9, open, unanswered). The device ends connected,
watching, sensitivity 0.5 reported, none wanted.

The clock is frozen one second after the newest record, so the device reads
"live". Actions change only this object and emit the same signals as the real
state. `push(name, data)` feeds a stream event, for tests of live updates.

`state.scans` is a FakeScans (fake_scans.py) and `state.log` a FakeLog
(fake_partlog.py): one scan, four parts, six entries and one part review on the
same machine and alarms. That sample is written by hand, not by real code.
"""

from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import threading
import urllib.request
from pathlib import Path

DESKTOP = Path(__file__).resolve().parents[1]
for _path in (str(DESKTOP), str(Path(__file__).resolve().parent)):  # `app`, and the two fake files beside this one
    if _path not in sys.path:
        sys.path.insert(0, _path)

from app.data import logic, model_server, paths
from app.data.state import StateReads
from fake_partlog import FakeLog
from fake_scans import FakeScans

ANSWERED_ALARM = "alarm-5"
OPEN_ALARM = "alarm-9"
FAKE_ANSWERER = "Fake tester"

_built = None
_lock = threading.Lock()


def _http(base, method, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def sample_records(heat=True) -> list:
    """sample_replay.jsonl, with the simulated device's heat (camera, seed 1) added when `heat`."""
    sim_heat = paths.sim_modules()[2]
    records = [json.loads(line) for line in paths.SAMPLE_REPLAY.read_text().splitlines() if line.strip()]
    if heat:
        heat_sim = sim_heat.HeatSim("camera", seed=1)
        for r in records:
            r["heat"] = heat_sim.heat(r["at"], 1.0)
    return records


def build_fixture() -> dict:
    """Run the real receiver and stand-in agent on the sample once. Returns what the receiver answers."""
    global _built
    with _lock:
        if _built is not None:
            return _built
        receiver_server = paths.server_modules()
        _, sim_model, _ = paths.sim_modules()
        if str(paths.REPO) not in sys.path:
            sys.path.insert(0, str(paths.REPO))
        from device.agent.harness import run_agent
        from device.agent.standin import stand_in_call

        records = sample_records()
        first = records[0]
        with tempfile.TemporaryDirectory(prefix="effente-fake-") as tmp:
            receiver = receiver_server.Receiver(
                "127.0.0.1", 0, os.path.join(tmp, "data"), str(paths.WEB_DIR), log=lambda m: None
            )
            receiver.start()
            base = f"http://127.0.0.1:{receiver.port}"
            try:
                _http(base, "POST", "/api/model", sim_model.model_announcement(first["device_id"], first["machine_id"]))
                for r in records:
                    _http(base, "POST", "/api/records", r)
                _http(base, "POST", f"/api/alarms/{ANSWERED_ALARM}/answers",
                      {"verdict": "real", "by": FAKE_ANSWERER, "at": "2026-11-14T16:05:00Z"})
                log = os.path.join(tmp, "device.jsonl")
                with open(log, "w", encoding="utf-8") as f:
                    f.writelines(json.dumps(r) + "\n" for r in records)
                model_file = os.path.join(tmp, "model.json")
                with open(model_file, "w", encoding="utf-8") as f:
                    json.dump(sim_model.model_file(), f)
                run_agent(first["machine_id"], log, model_file, os.path.join(tmp, "agent.jsonl"),
                          call=stand_in_call, platform_url=base)
                machine = first["machine_id"]
                _built = {
                    "state": _http(base, "GET", "/api/state"),
                    "records": _http(base, "GET", f"/api/machines/{machine}/records?limit=300&spectrum=1&frames=1")[
                        "records"],
                    "runs": _http(base, "GET", f"/api/machines/{machine}/agent-runs?limit=20")["runs"],
                }
            finally:
                receiver.stop()
        return _built


class FakeSim:
    """Stands in for the simulated device's controls: remembers keys pressed."""

    def __init__(self):
        self.keys = []

    def key(self, k: str) -> None:
        self.keys.append(k)

    def status(self) -> str:
        return "[SIM] watching | case unrecognised | fake"


class FakeState(StateReads):
    """DashboardState's interface over the fixture. See the module docstring."""

    def __init__(self, parent=None, with_sim=False, connected=True):
        super().__init__(parent)
        built = copy.deepcopy(build_fixture())
        snap = built["state"]
        self._now = logic.parse_time(snap["now"]) + 1000.0
        logic.apply_snapshot(self._s, snap, self._now)
        logic.apply_laptop_snapshot(self._s, snap)
        for d in self._s["devices"].values():
            if d["last_record_local_ms"] is not None:
                d["last_record_local_ms"] = self._now - 1000.0  # the newest record arrived a second ago
        self._s["platform_up"] = connected
        machine = built["records"][0]["machine_id"] if built["records"] else None
        if machine:
            logic.set_history(self._s, machine, built["records"], slim=False)
        logic.add_runs(self._s, built["runs"])
        self._sim = FakeSim() if with_sim else None
        self.started_agents = []  # modes start_agent was called with
        self.own_agent_alive = False  # set True to act as if this app's report run is going
        self.stopped_agents = 0  # how many times stop_agent was called
        self._note_alarms()  # the fixture's alarms were there at the first look
        self.scans.fill()
        self.log.fill()

    def _make_scans(self) -> FakeScans:
        return FakeScans(self)

    def _make_log(self) -> FakeLog:
        return FakeLog(self)

    def now_ms(self) -> float:
        return self._now

    def stored_count(self):
        """Every fixture record is stored, so the history is the whole count."""
        return len(self._s["history"].get(self.current_machine()) or [])

    def advance(self, seconds: float) -> None:
        """Move the frozen clock on, for staleness tests."""
        self._now += seconds * 1000.0

    # ---- actions --------------------------------------------------------

    def answer_alarm(self, alarm_id, verdict, finding=None) -> bool:
        alarm = self._s["alarms"].get(alarm_id)
        if alarm is None or verdict not in logic.VERDICT_TEXT:
            return False
        answer = {"verdict": verdict, "by": self.answerer(), "at": logic_now_iso(self._now), "alarm_id": alarm_id}
        if finding:
            answer["finding"] = finding
        alarm = dict(alarm, answer=answer, answers=list(alarm.get("answers") or []) + [answer])
        self._s["alarms"][alarm_id] = alarm
        self.alarms_changed.emit()
        return True

    def set_sensitivity(self, value) -> bool:
        device_id = self.device_id()
        d = self._s["devices"].get(device_id)
        if d is None or (value is not None and logic._sensitivity_or_none(value) is None):
            return False
        d["settings"] = None if value is None else {"sensitivity": float(value)}
        self.settings_changed.emit()
        return True

    def start_agent(self, mode="standin") -> bool:
        self.started_agents.append(mode)
        if mode == "local" and self.model_status().state != model_server.READY:
            self._say(f"No model server is running at {model_server.URL}. Start the model server, or run the stand-in.")
            return False
        return True

    def start_model_server(self) -> None:
        """No process is started: the server is shown as running with the chosen file."""
        name = self.model_settings.model_file().name
        self._model_status_is(model_server.Status(model_server.READY, f"Running with {name}.", True, name))

    def stop_model_server(self) -> None:
        self._model_status_is(model_server.Status(model_server.STOPPED, "Not running."))

    def set_model_status(self, state: str, message: str, ours: bool = False, model: str = "") -> None:
        """Show the model server in any state, for page tests."""
        self._model_status_is(model_server.Status(state, message, ours, model))

    def _agent_process_alive(self) -> bool:
        return self.own_agent_alive

    def stop_agent(self) -> None:
        self.stopped_agents += 1
        self.finish_agent()

    def sim(self):
        return self._sim

    # ---- test helpers ---------------------------------------------------

    def push(self, name: str, data: dict) -> None:
        """Feed one stream event as the live state would, with its signals."""
        data = copy.deepcopy(data)
        logic.apply_event(self._s, name, data, self._now, slim=False)
        logic.apply_laptop_event(self._s, name, data)
        self._feed_event(name, data)
        if name == "record":
            self.record_added.emit(data)
            self.problems_changed.emit()
            self.settings_changed.emit()
        elif name == "alarm":
            self.alarms_changed.emit()
        elif name == "agent_run":
            self.runs_changed.emit()
        elif name == "settings":
            self.settings_changed.emit()
        elif name in ("problem", "problem_cleared", "device"):
            self.problems_changed.emit()
            self.snapshot_changed.emit()
        else:
            self.snapshot_changed.emit()

    def finish_agent(self) -> None:
        """End the pretend report run, as the real one ends: runs_changed fires."""
        self.own_agent_alive = False
        self.runs_changed.emit()

    def set_connected(self, up: bool) -> None:
        self._s["platform_up"] = up
        self.connection_changed.emit(up)


def logic_now_iso(ms: float) -> str:
    import datetime as dt

    return dt.datetime.fromtimestamp(ms / 1000.0, dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
