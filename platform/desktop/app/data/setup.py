"""`state.setup`: the machine profile, the setup run, the starting model and the
local profile (platform/desktop/INTERFACES.md addition 24, platform/CONTRACT.md
section 10).

The state makes one of these and passes it every snapshot and every stream
event. `state` is the DashboardState or, in tests, FakeState: it gives
`current_machine()`, `model()`, `view()`, `answerer()` and `action_failed(str)`,
and the live one also `api` (the ApiClient), `models` (the model server),
`model_settings` (for the store from app/settings.py) and
`_background(work, done)`. tests/fake_setup.py `FakeSetup` is this interface on
fixed data.

- Profiles come with every snapshot (`profiles`) and `profile` event.
- The draft lives only in this app, one for each machine, until "confirm" sends
  `PUT /api/machines/<id>/profile`. Nothing else here stores a profile or sends
  anything toward a device.
- The choice is `POST /api/library/lookup` for the draft. Code, not the agent;
  it stores and sends nothing.
- A setup run is `python3 platform/setup_run.py`, a local process, as the report
  agent is: never over HTTP (contract section 4). Its run comes back on the
  stream with the other runs. A run that passes fills the draft when it
  arrives on the stream; one read back later does not.
- The wanted entry is the confirmed profile's choice. The entry in use is what
  the shown device reports in its model announcement (message 1). The device
  does not act on the wanted entry yet (plan row D1), so the two are never
  taken to be the same.

The state's `answerer()` asks `local_profile()` for the name first. The local
profile is a name and a role kept on this laptop. It is not a password and
proves nothing: call it "local profile", nothing else.
"""

from __future__ import annotations

import json
import sys

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal

from . import model_server, paths
from .api import quote

FIELDS = ("machine_type", "drive", "speed_rpm", "mounting", "sensor_location")  # contract section 10.1
SETUP_AGENT = "setup"  # `agent` of a setup run (contract section 10.3)
SETUP_SCRIPT = paths.PLATFORM / "setup_run.py"
MODES = ("standin", "local")

# The local profile's keys in the store from app/settings.py (contract section 10.5).
NAME_KEY, ROLE_KEY = "local_profile/name", "local_profile/role"
NAME_MAX, ROLE_MAX = 60, 40
NO_NAME = "Local profile not saved: a name is needed."


def fields_of(d) -> dict:
    """The five fields of a profile or a draft, each a value or None."""
    return {k: (d or {}).get(k) for k in FIELDS}


class Setup(QObject):
    profile_changed = Signal()  # the current machine's stored profile, the draft or the choice moved
    run_changed = Signal()  # a setup run started, moved or ended
    entry_changed = Signal()  # the wanted entry, or the one the device reports, moved
    local_profile_changed = Signal()  # the local profile was set or changed

    def __init__(self, state):
        super().__init__(state)
        self.state = state
        self._profiles_seen = False  # a snapshot has carried `profiles` (contract 10.1)
        self._profiles: dict = {}  # machine_id -> its newest profile
        self._drafts: dict = {}  # machine_id -> the five fields as they stand on the page
        self._draft_runs: dict = {}  # machine_id -> the run_id its draft was filled from
        self._choices: dict = {}  # key of five fields -> the lookup's choice
        self._asking: set = set()  # the same keys, while a lookup is out
        self._lookup_problems: dict = {}  # the same keys -> why the lookup failed
        self._filled_from: set = set()  # setup run_ids that have filled a draft
        self._process = None  # QProcess of the setup run this app started
        self._process_machine = None
        self._process_output = b""
        self._unsaved_local = None  # the local profile when the state has no store: this run only

    # ---- fed by the state -------------------------------------------------

    def apply_snapshot(self, snap: dict) -> None:
        """GET /api/state, after every (re)connect."""
        profiles = snap.get("profiles")
        if isinstance(profiles, dict):
            self._profiles_seen = True
            self._profiles = {m: p for m, p in profiles.items() if isinstance(p, dict)}
        self._lookup_problems.clear()  # the receiver may have come back: ask again
        self.profile_changed.emit()
        self.run_changed.emit()
        self.entry_changed.emit()

    def apply_event(self, name: str, data: dict) -> None:
        """One stream event, of any name."""
        if name == "profile" and isinstance(data.get("machine_id"), str):
            self._take_profile(data)
        elif name == "agent_run" and data.get("agent") == SETUP_AGENT:
            self._take_run(data)
            self.run_changed.emit()
        elif name in ("model", "settings"):
            self.entry_changed.emit()

    def _take_profile(self, profile: dict) -> None:
        machine = profile["machine_id"]
        if self._profiles.get(machine) == profile:
            return
        self._profiles[machine] = profile
        self._drafts.pop(machine, None)  # a stored profile is what the page shows next
        self._draft_runs.pop(machine, None)
        self._remember_choice(fields_of(profile), profile.get("choice"))
        self.profile_changed.emit()
        self.entry_changed.emit()

    def _take_run(self, run: dict) -> None:
        """A run that has just passed fills its machine's draft and brings its choice.
        Only from the stream: a run read back later (the runs route) never fills one."""
        run_id = run.get("run_id")
        if run_id in self._filled_from or run.get("status") == "running":
            return
        self._filled_from.add(run_id)
        result = run.get("setup")
        if not run.get("ok") or not isinstance(result, dict) or not isinstance(run.get("machine_id"), str):
            return
        machine = run["machine_id"]
        draft = fields_of(result.get("profile"))
        self._drafts[machine] = draft
        self._draft_runs[machine] = run_id
        self._remember_choice(draft, result.get("choice"))
        self.profile_changed.emit()

    # ---- reads ------------------------------------------------------------

    def profile(self):
        """The current machine's confirmed machine profile (contract 10.1), or None."""
        return self._profiles.get(self.state.current_machine())

    def draft(self) -> dict:
        """The five fields as they stand on the page, not yet confirmed. Starts from profile(), or all empty."""
        machine = self.state.current_machine()
        if machine in self._drafts:
            return dict(self._drafts[machine])
        return fields_of(self._profiles.get(machine))

    def choice(self):
        """The lookup's reply for the draft (contract 10.2), or None while it is being fetched."""
        if not self.state.current_machine():
            return None
        draft = self.draft()
        key = _key(draft)
        if key not in self._choices:
            self._look_up(draft)
        return self._choices.get(key)

    def lookup_problem(self) -> str:
        """Why the lookup for the draft failed, or "". `choice()` stays None then."""
        return self._lookup_problems.get(_key(self.draft()), "")

    def _runs(self) -> list:
        machine = self.state.current_machine()
        runs = [e.get("run") for e in (self.state.view().get("runs") or {}).values()]
        return [r for r in runs if isinstance(r, dict) and r.get("agent") == SETUP_AGENT
                and r.get("machine_id") == machine]

    def run(self):
        """The newest setup run for the current machine, or None."""
        return max(self._runs(), key=lambda r: r.get("at") or "", default=None)

    def run_running(self) -> bool:
        """True while one runs: one this app started, or one the newest run says is running."""
        if self._alive() and self._process_machine == self.state.current_machine():
            return True
        newest = self.run()
        return bool(newest) and newest.get("status") == "running"

    def wanted_entry(self):
        """The library entry wanted for the current machine, or None: the confirmed profile's choice."""
        entry = ((self.profile() or {}).get("choice") or {}).get("library_entry")
        return entry if isinstance(entry, str) else None

    def device_entry(self):
        """The entry the shown device reports in its model announcement, or None."""
        entry = (self.state.model() or {}).get("library_entry")
        return entry if isinstance(entry, str) else None

    def profile_known(self) -> bool:
        """True once the platform has said which machines have a profile: a snapshot
        carried `profiles`. Until then `profile()` being None says nothing, so the
        window does not open the Setup page for it."""
        return self._profiles_seen

    def local_profile(self):
        """{"name", "role"}, or None if none is set. `role` may be ""."""
        store = self._store()
        if store is None:
            return dict(self._unsaved_local) if self._unsaved_local else None
        name = _text(store.value(NAME_KEY, ""), NAME_MAX)
        return {"name": name, "role": _text(store.value(ROLE_KEY, ""), ROLE_MAX)} if name else None

    # ---- the lookup -------------------------------------------------------

    def _remember_choice(self, draft: dict, choice) -> None:
        if isinstance(choice, dict):
            self._choices[_key(draft)] = choice
            self._lookup_problems.pop(_key(draft), None)

    def _look_up(self, draft: dict) -> None:
        """Ask the lookup for this draft, once, in the background. A failure is
        kept until the next snapshot, so a broken lookup is not asked on every draw."""
        api = getattr(self.state, "api", None)
        key = _key(draft)
        if api is None or key in self._asking or key in self._lookup_problems:
            return
        self._asking.add(key)
        body = {k: v for k, v in draft.items() if v is not None}

        def done(reply, error):
            self._asking.discard(key)
            if error:
                self._lookup_problems[key] = f"The starting model could not be looked up: {error}"
            else:
                self._remember_choice(draft, reply.get("choice"))
            self.profile_changed.emit()

        self.state._background(lambda: api.post("/api/library/lookup", body), done)

    # ---- actions ----------------------------------------------------------

    def start_run(self, answers, mode="standin") -> None:
        """Run the setup agent on these answers ([{"about", "question", "answer"}],
        platform/setup/agent/README.md). `mode` is 'standin' or 'local', as for the
        report agent. A run that passes fills the draft."""
        machine = self.state.current_machine()
        if mode not in MODES:
            self.state.action_failed.emit(f"Setup run not started: unknown mode: {mode}")
            return
        if not machine:
            self.state.action_failed.emit("Setup run not started: no machine is shown.")
            return
        if self.run_running():
            self.state.action_failed.emit("Setup run not started: one is already running for this machine.")
            return
        args = [str(SETUP_SCRIPT), "--machine", machine, "--answers", "-", "--port", str(self.state.api.port),
                "--backend", mode]
        if mode == "local":
            url = self.state.models.url
            answer, why = model_server.probe(url)
            if answer != model_server.READY:
                self.state.action_failed.emit(f"Setup run not started: {why}")
                return
            args += ["--llm-url", url]  # named on every run, so nothing in the environment points it elsewhere
        process = QProcess(self)
        process.setProcessEnvironment(QProcessEnvironment.systemEnvironment())
        process.setProcessChannelMode(QProcess.MergedChannels)
        process.readyReadStandardOutput.connect(self._read)
        process.finished.connect(self._finished)
        process.errorOccurred.connect(self._error)
        self._process, self._process_machine, self._process_output = process, machine, b""
        process.start(sys.executable, args)
        process.write(json.dumps(list(answers)).encode())
        process.closeWriteChannel()
        self.run_changed.emit()

    def _alive(self) -> bool:
        return self._process is not None and self._process.state() != QProcess.NotRunning

    def _read(self) -> None:
        if self._process is not None:
            self._process_output += bytes(self._process.readAllStandardOutput())

    def _error(self, error) -> None:
        if error == QProcess.FailedToStart:
            self._process = None
            self.state.action_failed.emit(f"Setup run not started: could not run {SETUP_SCRIPT}.")
            self.run_changed.emit()

    def _finished(self, _code, status) -> None:
        """The run posts itself, passed or failed, and the page shows it from the
        stream. Only a run that ended before it could post is said here."""
        self._read()
        self._process = None
        if status != QProcess.NormalExit:
            self.state.action_failed.emit("The setup run stopped before it finished.")
        self.run_changed.emit()

    def set_draft(self, **fields) -> None:
        """Change fields of the draft by hand. Asks the lookup again."""
        machine = self.state.current_machine()
        unknown = [k for k in fields if k not in FIELDS]
        if unknown or not machine:
            self.state.action_failed.emit(f"Profile not changed: unknown field: {unknown[0]}" if unknown
                                          else "Profile not changed: no machine is shown.")
            return
        draft = self.draft()
        draft.update(fields)
        self._drafts[machine] = draft
        self.profile_changed.emit()

    def confirm(self) -> None:
        """Store the draft as the machine profile. The only action that sends anything toward the device."""
        machine = self.state.current_machine()
        if not machine:
            self.state.action_failed.emit("Profile not confirmed: no machine is shown.")
            return
        body = dict(self.draft(), by=self.state.answerer(), run_id=self._draft_runs.get(machine))
        api = self.state.api

        def done(reply, error):
            if error:
                self.state.action_failed.emit(f"Profile not confirmed: {error}")
            elif isinstance(reply.get("profile"), dict):
                self._take_profile(dict(reply["profile"], machine_id=machine))

        self.state._background(lambda: api.put(f"/api/machines/{quote(machine)}/profile", body), done)

    def set_local_profile(self, name, role) -> None:
        """Keep the local profile in the store from app/settings.py. No password."""
        name, role = _text(name, NAME_MAX), _text(role, ROLE_MAX)
        if not name:
            self.state.action_failed.emit(NO_NAME)
            return
        store = self._store()
        if store is None:
            self._unsaved_local = {"name": name, "role": role}
        else:
            store.setValue(NAME_KEY, name)
            store.setValue(ROLE_KEY, role)
        self.local_profile_changed.emit()

    def _store(self):
        """The QSettings the state keeps the model folder in, or None (tests): the
        local profile sits beside it, so both last as long as each other."""
        return getattr(getattr(self.state, "model_settings", None), "_store", None)


def _key(draft: dict) -> str:
    return json.dumps(fields_of(draft), sort_keys=True)


def _text(value, limit: int) -> str:
    """One line of plain text, trimmed and cut to `limit` characters."""
    return " ".join(str(value or "").split())[:limit]
