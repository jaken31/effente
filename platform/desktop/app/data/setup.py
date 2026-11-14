"""`state.setup`: the machine profile, the setup run, the starting model and the
local profile (platform/desktop/INTERFACES.md addition 24, platform/CONTRACT.md
section 10). A stub: nothing is built yet.

The state makes one of these and passes it every snapshot and every stream
event. `state` is the DashboardState or, in tests, FakeState: it gives
`current_machine()`, `device_id()`, `view()` and `action_failed(str)`, and the
live one also `api` (the ApiClient), `model_settings` (for the store from
app/settings.py) and `_background(work, done)`.
tests/fake_setup.py `FakeSetup` is this interface on fixed data.

The state's `answerer()` asks `local_profile()` for the name first. The local
profile is a name and a role kept on this laptop. It is not a password and
proves nothing: call it "local profile", nothing else.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

NOT_BUILT = "Setup is not built yet."
FIELDS = ("machine_type", "drive", "speed_rpm", "mounting", "sensor_location")  # contract section 10.1

# The local profile's keys in the store from app/settings.py (contract section 10.5).
NAME_KEY, ROLE_KEY = "local_profile/name", "local_profile/role"
NAME_MAX, ROLE_MAX = 60, 40
NO_NAME = "Local profile not saved: a name is needed."


class Setup(QObject):
    profile_changed = Signal()  # the current machine's stored profile, the draft or the choice moved
    run_changed = Signal()  # a setup run started, moved or ended
    entry_changed = Signal()  # the wanted entry, or the one the device reports, moved
    local_profile_changed = Signal()  # the local profile was set or changed

    def __init__(self, state):
        super().__init__(state)
        self.state = state
        self._profiles_seen = False  # a snapshot has carried `profiles` (contract 10.1)
        self._unsaved_local = None  # the local profile when the state has no store: this run only

    # ---- fed by the state -------------------------------------------------

    def apply_snapshot(self, snap: dict) -> None:
        """GET /api/state, after every (re)connect."""
        self._profiles_seen = self._profiles_seen or "profiles" in snap

    def apply_event(self, name: str, data: dict) -> None:
        """One stream event, of any name."""

    # ---- reads ------------------------------------------------------------

    def profile(self):
        """The current machine's confirmed machine profile (contract 10.1), or None."""
        return

    def draft(self) -> dict:
        """The five fields as they stand on the page, not yet confirmed. Starts from profile(), or all empty."""
        return dict.fromkeys(FIELDS)

    def choice(self):
        """The lookup's reply for the draft (contract 10.2), or None while it is being fetched."""
        return

    def run(self):
        """The newest setup run for the current machine, or None."""
        return

    def run_running(self) -> bool:
        """True while one runs."""
        return False

    def wanted_entry(self):
        """The library entry wanted for the current machine, or None."""
        return

    def device_entry(self):
        """The entry the shown device reports, or None."""
        return

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

    # ---- actions ----------------------------------------------------------

    def start_run(self, answers, mode="standin") -> None:
        """Run the setup agent on these answers. `mode` is 'standin' or 'local',
        as for the report agent. A run that passes fills the draft."""
        self.state.action_failed.emit(NOT_BUILT)

    def set_draft(self, **fields) -> None:
        """Change fields of the draft by hand. Asks the lookup again."""
        self.state.action_failed.emit(NOT_BUILT)

    def confirm(self) -> None:
        """Store the draft as the machine profile. The only action that sends anything toward the device."""
        self.state.action_failed.emit(NOT_BUILT)

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


def _text(value, limit: int) -> str:
    """One line of plain text, trimmed and cut to `limit` characters."""
    return " ".join(str(value or "").split())[:limit]
