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


class Setup(QObject):
    profile_changed = Signal()  # the current machine's stored profile, the draft or the choice moved
    run_changed = Signal()  # a setup run started, moved or ended
    entry_changed = Signal()  # the wanted entry, or the one the device reports, moved
    local_profile_changed = Signal()  # the local profile was set or changed

    def __init__(self, state):
        super().__init__(state)
        self.state = state

    # ---- fed by the state -------------------------------------------------

    def apply_snapshot(self, snap: dict) -> None:
        """GET /api/state, after every (re)connect."""

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

    def local_profile(self):
        """{"name", "role"}, or None if none is set."""
        return

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
        self.state.action_failed.emit(NOT_BUILT)
