"""`state.log`: the parts and the log of the current machine (platform/desktop/INTERFACES.md
addition 19, platform/CONTRACT.md sections 8.5 to 8.8).

The state makes one of these and passes it every snapshot and every stream
event. `state` is the DashboardState or, in tests, FakeState: it gives
`current_machine()`, `alarms()`, `answerer()`, `view()` and `action_failed(str)`,
and the live one also `api` (the ApiClient) and `_background(work, done)`.
tests/fake_partlog.py `FakeLog` is this interface on fixed data.

Parts come with every snapshot and `part` event, for every machine, so `parts()`
is right the moment the machine shown changes. Entries are not in the snapshot:
the first `entries()` for a machine fetches its log, and `entries_changed` says
when it is there. A part's state is the receiver's (contract section 8.7) and is
never worked out here.

A part review is `python3 platform/part_review.py`, run as a local process, as
the report agent is: never over HTTP (contract section 8.8). Its runs come back
on the stream and are read from the runs the state holds.
"""

from __future__ import annotations

import sys

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal

from . import logic, model_server, paths
from .api import ApiError, quote

LOG_LIMIT = 2000  # the most entries the receiver gives in one read
REVIEW_AGENT = "part_review"  # `agent` of a part review run (contract section 8.8)
REVIEW_SCRIPT = paths.PLATFORM / "part_review.py"
REVIEW_MODES = ("standin", "local")
EVERY_PART = ""  # what `_reviewing` holds while one process reviews every part


def _when(entry) -> float:
    """An entry's `at` in milliseconds. One that cannot be read sorts last."""
    ms = logic.parse_time(entry.get("at"))
    return ms if ms == ms else 0.0  # NaN is the only number that is not itself


class PartLog(QObject):
    parts_changed = Signal()  # a part was made, changed, or its state moved
    entries_changed = Signal()  # an entry was added
    selection_changed = Signal()  # another part was selected
    reviews_changed = Signal()  # a part review run started, moved or ended

    def __init__(self, state):
        super().__init__(state)
        self.state = state
        self._machine_parts: dict = {}  # machine_id -> {part_id: part}, in the order they were made
        self._machine_entries: dict = {}  # machine_id -> {entry_id: entry}
        self._entries_loaded: set = set()  # machines whose log has been fetched since the last snapshot
        self._entries_loading: set = set()
        self._selected = None
        self._review_process = None  # QProcess of the part review this app started
        self._reviewing = None  # the part it reviews, or EVERY_PART
        self._review_output = b""

    # ---- fed by the state -------------------------------------------------

    def apply_snapshot(self, snap: dict) -> None:
        """GET /api/state, after every (re)connect."""
        parts = snap.get("parts")
        self._machine_parts = {
            machine: {p["part_id"]: p for p in listed if isinstance(p, dict) and isinstance(p.get("part_id"), str)}
            for machine, listed in (parts.items() if isinstance(parts, dict) else ())
            if isinstance(listed, list)
        }
        self._entries_loaded.clear()  # entries may have been added while the stream was down
        self.parts_changed.emit()
        self.entries_changed.emit()

    def apply_event(self, name: str, data: dict) -> None:
        """One stream event, of any name."""
        if name == "part":
            self._take_part(data)
        elif name == "log_entry":
            self._take_entry(data)
        elif name == "agent_run" and data.get("agent") == REVIEW_AGENT:
            self.reviews_changed.emit()

    def _take_part(self, part) -> None:
        """Keep a part from an event or a reply. Either may come first, so the same one twice changes nothing."""
        if not isinstance(part, dict) or not isinstance(part.get("part_id"), str):
            return
        parts = self._machine_parts.setdefault(part.get("machine_id"), {})
        if parts.get(part["part_id"]) != part:
            parts[part["part_id"]] = part
            self.parts_changed.emit()

    def _take_entry(self, entry) -> None:
        if not isinstance(entry, dict) or not isinstance(entry.get("entry_id"), str):
            return
        entries = self._machine_entries.setdefault(entry.get("machine_id"), {})
        if entry["entry_id"] not in entries:
            entries[entry["entry_id"]] = entry
            self.entries_changed.emit()

    def _load_entries(self, machine) -> None:
        """Fetch a machine's log once, in the background."""
        api = getattr(self.state, "api", None)
        if api is None or not machine or machine in self._entries_loaded or machine in self._entries_loading:
            return
        self._entries_loading.add(machine)

        def done(reply, error):
            self._entries_loading.discard(machine)
            self._entries_loaded.add(machine)  # also after a failure: the next snapshot tries again, not every read
            if error:
                self.state.action_failed.emit(f"Log not loaded: {error}")
                return
            known = self._machine_entries.setdefault(machine, {})
            known.update({e["entry_id"]: e for e in reply["entries"]})
            self.entries_changed.emit()

        self.state._background(lambda: api.get(f"/api/machines/{quote(machine)}/log", limit=LOG_LIMIT), done)

    # ---- reads ------------------------------------------------------------

    def parts(self) -> list:
        """The current machine's parts, without removed ones."""
        parts = self._machine_parts.get(self.state.current_machine(), {})
        return [p for p in parts.values() if not p.get("removed")]

    def part(self, part_id):
        """One part, or None."""
        return self._machine_parts.get(self.state.current_machine(), {}).get(part_id)

    def entries(self, part_id=None, kind=None) -> list:
        """The current machine's entries, newest first."""
        machine = self.state.current_machine()
        self._load_entries(machine)
        chosen = [e for e in self._machine_entries.get(machine, {}).values()
                  if (part_id is None or e.get("part_id") == part_id) and (kind is None or e.get("kind") == kind)]
        return sorted(chosen, key=lambda e: (_when(e), e["entry_id"]), reverse=True)

    def suspects(self, alarm) -> list:
        """The parts whose `fault_types` hold this alarm's `diagnosis`."""
        diagnosis = (alarm or {}).get("diagnosis")
        return [p for p in self.parts() if diagnosis in (p.get("fault_types") or [])]

    def waiting_alarms(self) -> list:
        """The current machine's alarms that have no answer naming a part."""
        return [a for a in self.state.alarms() if not any(x.get("part_id") for x in a.get("answers") or [])]

    def selected_part(self):
        """The selected part's id, or None. A part of another machine is not selected."""
        return self._selected if self.part(self._selected) is not None else None

    def _reviews(self, part_id) -> list:
        runs = [e["run"] for e in (self.state.view().get("runs") or {}).values()]
        machine = self.state.current_machine()
        return [r for r in runs
                if r.get("agent") == REVIEW_AGENT and r.get("part_id") == part_id and r.get("machine_id") == machine]

    def review(self, part_id):
        """The newest part review run for this part, or None."""
        return max(self._reviews(part_id), key=lambda r: r.get("at") or "", default=None)

    def review_running(self, part_id) -> bool:
        """True while a review of this part runs: one this app started, or one its newest run says is running."""
        if self._review_alive() and self._reviewing in (EVERY_PART, part_id):
            return True
        newest = self.review(part_id)
        return bool(newest) and newest.get("status") == "running"

    def _review_alive(self) -> bool:
        return self._review_process is not None and self._review_process.state() != QProcess.NotRunning

    # ---- actions ----------------------------------------------------------

    def _send(self, failed: str, work, took) -> None:
        """Run `work()` off the Qt thread. Then `took(reply)`, or say `failed` and why."""

        def done(reply, error):
            if error:
                self.state.action_failed.emit(f"{failed}: {error}")
            else:
                took(reply)

        self.state._background(work, done)

    def select_part(self, part_id) -> None:
        """Select a part. Both pages follow the selection."""
        if part_id != self._selected:
            self._selected = part_id
            self.selection_changed.emit()

    def create_part(self, name, kind, position=None) -> None:
        """Make a part on the current machine."""
        machine = self.state.current_machine()
        if not machine:
            self.state.action_failed.emit("Part not added: no machine is shown.")
            return
        api, body = self.state.api, {"name": name, "kind": kind, "position": position, "by": self.state.answerer()}
        self._send("Part not added", lambda: api.post(f"/api/machines/{quote(machine)}/parts", body),
                   lambda reply: self._take_part(reply.get("part")))

    def update_part(self, part_id, **fields) -> None:
        """Change a part, including moving its pin."""
        api, body = self.state.api, dict(fields, by=self.state.answerer())
        self._send("Part not changed", lambda: api.put(f"/api/parts/{quote(str(part_id))}", body),
                   lambda reply: self._take_part(reply.get("part")))

    def add_entry(self, kind, text, part_id=None, needs_repair=None, alarm_id=None) -> None:
        """Add an entry."""
        machine = self.state.current_machine()
        if not machine:
            self.state.action_failed.emit("Entry not added: no machine is shown.")
            return
        api = self.state.api
        body = {"kind": kind, "text": text, "part_id": part_id, "needs_repair": needs_repair, "alarm_id": alarm_id,
                "by": self.state.answerer()}

        def took(reply):
            self._take_entry(reply.get("entry"))
            self._take_part(reply.get("part"))

        self._send("Entry not added", lambda: api.post(f"/api/machines/{quote(machine)}/log", body), took)

    def answer_alarm_for_part(self, alarm_id, verdict, part_id, finding=None, needs_repair=None) -> None:
        """Answer an alarm and name the part. The receiver makes the entry. The
        alarm, the entry and the part's new state all come back on the stream."""
        api = self.state.api
        body = {"verdict": verdict, "by": self.state.answerer(), "part_id": part_id}
        if finding:
            body["finding"] = finding
        if needs_repair is not None:
            body["needs_repair"] = needs_repair
        self._send("Answer not saved", lambda: api.post(f"/api/alarms/{quote(str(alarm_id))}/answers", body),
                   lambda reply: None)

    def start_review(self, part_id=None, mode="standin") -> None:
        """Run the part review for one part, or for every part when None.
        `mode` is 'standin' or 'local', as for the report agent. One process at a
        time: the reviews are advice, and none changes a part's state."""
        machine = self.state.current_machine()
        if mode not in REVIEW_MODES:
            self.state.action_failed.emit(f"Part review not started: unknown mode: {mode}")
            return
        if not machine:
            self.state.action_failed.emit("Part review not started: no machine is shown.")
            return
        if self._review_alive() or (part_id and self.review_running(part_id)):
            self.state.action_failed.emit("Part review not started: one is already running.")
            return
        args = [str(REVIEW_SCRIPT), "--machine", machine, "--port", str(self.state.api.port), "--backend", mode]
        if part_id:
            args += ["--part", str(part_id)]
        if mode == "local":
            url = self.state.models.url
            answer, why = model_server.probe(url)
            if answer != model_server.READY:
                self.state.action_failed.emit(f"Part review not started: {why}")
                return
            args += ["--llm-url", url]  # named on every run, so nothing in the environment points it elsewhere
        process = QProcess(self)
        process.setProcessEnvironment(QProcessEnvironment.systemEnvironment())
        process.setProcessChannelMode(QProcess.MergedChannels)
        process.readyReadStandardOutput.connect(self._review_read)
        process.finished.connect(self._review_finished)
        process.errorOccurred.connect(self._review_error)
        self._review_process, self._reviewing, self._review_output = process, part_id or EVERY_PART, b""
        process.start(sys.executable, args)
        self.reviews_changed.emit()

    def _review_read(self) -> None:
        if self._review_process is not None:
            self._review_output += bytes(self._review_process.readAllStandardOutput())

    def _review_error(self, error) -> None:
        if error == QProcess.FailedToStart:
            self._review_process = None
            self.state.action_failed.emit(f"Part review not started: could not run {REVIEW_SCRIPT}.")
            self.reviews_changed.emit()

    def _review_finished(self, code, status) -> None:
        self._review_read()
        lines = [line for line in self._review_output.decode("utf-8", errors="replace").splitlines() if line.strip()]
        self._review_process = None
        if status != QProcess.NormalExit:
            self.state.action_failed.emit("The part review stopped before it finished.")
        elif code != 0:  # a part got no checked review: the command's last line says why
            self.state.action_failed.emit(f"Part review: {lines[-1] if lines else f'exit code {code}'}")
        self.reviews_changed.emit()

    def export_csv(self, path) -> None:
        """Save the log as a CSV file: the receiver's own, so the app and the route give the same file."""
        machine = self.state.current_machine()
        if not machine:
            self.state.action_failed.emit("Log not exported: no machine is shown.")
            return
        api = self.state.api

        def work():
            try:
                return api.get_bytes(f"/api/machines/{quote(machine)}/log.csv", to=path, limit=LOG_LIMIT)
            except OSError as e:  # the file could not be written
                raise ApiError(str(e)) from None

        self._send("Log not exported", work, lambda size: None)
