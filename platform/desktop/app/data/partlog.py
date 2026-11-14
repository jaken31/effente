"""`state.log`: the parts and the log of the current machine (platform/desktop/INTERFACES.md
addition 19, platform/CONTRACT.md sections 8.5 to 8.8). A stub: nothing is built yet.

The state makes one of these and passes it every snapshot and every stream
event. `state` is the DashboardState or, in tests, FakeState: it gives
`current_machine()`, `alarms()`, `answerer()` and `action_failed(str)`, and the
live one also `api` (the ApiClient) and `_background(work, done)`.
tests/fake_partlog.py `FakeLog` is this interface on fixed data.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

NOT_BUILT = "The part log is not built yet."


class PartLog(QObject):
    parts_changed = Signal()  # a part was made, changed, or its state moved
    entries_changed = Signal()  # an entry was added
    selection_changed = Signal()  # another part was selected
    reviews_changed = Signal()  # a part review run started, moved or ended

    def __init__(self, state):
        super().__init__(state)
        self.state = state

    # ---- fed by the state -------------------------------------------------

    def apply_snapshot(self, snap: dict) -> None:
        """GET /api/state, after every (re)connect."""

    def apply_event(self, name: str, data: dict) -> None:
        """One stream event, of any name."""

    # ---- reads ------------------------------------------------------------

    def parts(self) -> list:
        """The current machine's parts, without removed ones."""
        return []

    def part(self, part_id):
        """One part, or None."""
        return

    def entries(self, part_id=None, kind=None) -> list:
        """The current machine's entries, newest first."""
        return []

    def suspects(self, alarm) -> list:
        """The parts whose `fault_types` hold this alarm's `diagnosis`."""
        return []

    def waiting_alarms(self) -> list:
        """The current machine's alarms that have no answer naming a part."""
        return []

    def selected_part(self):
        """The selected part's id, or None."""
        return

    def review(self, part_id):
        """The newest part review run for this part, or None."""
        return

    def review_running(self, part_id) -> bool:
        """True while a review of this part runs."""
        return False

    # ---- actions ----------------------------------------------------------

    def _not_built(self) -> None:
        self.state.action_failed.emit(NOT_BUILT)

    def select_part(self, part_id) -> None:
        """Select a part. Both pages follow the selection."""
        self._not_built()

    def create_part(self, name, kind, position=None) -> None:
        """Make a part on the current machine."""
        self._not_built()

    def update_part(self, part_id, **fields) -> None:
        """Change a part, including moving its pin."""
        self._not_built()

    def add_entry(self, kind, text, part_id=None, needs_repair=None, alarm_id=None) -> None:
        """Add an entry."""
        self._not_built()

    def answer_alarm_for_part(self, alarm_id, verdict, part_id, finding=None, needs_repair=None) -> None:
        """Answer an alarm and name the part."""
        self._not_built()

    def start_review(self, part_id=None, mode="standin") -> None:
        """Run the part review for one part, or for every part when None.
        `mode` is 'standin' or 'local', as for the report agent."""
        self._not_built()

    def export_csv(self, path) -> None:
        """Save the log as a CSV file."""
        self._not_built()
