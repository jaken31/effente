"""`state.scans`: the scans of the current machine (platform/desktop/INTERFACES.md
addition 18, platform/CONTRACT.md sections 8.2 to 8.4). A stub: nothing is built yet.

The state makes one of these and passes it every snapshot and every stream
event. `state` is the DashboardState or, in tests, FakeState: it gives
`current_machine()`, `answerer()` and `action_failed(str)`, and the live one
also `api` (the ApiClient) and `_background(work, done)`.
tests/fake_scans.py `FakeScans` is this interface on fixed data.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

NOT_BUILT = "Scans are not built yet."


class Scans(QObject):
    scans_changed = Signal()  # a scan arrived or its status moved
    model_ready = Signal(str)  # the converted file for this scan_id is on disk and can be shown

    def __init__(self, state):
        super().__init__(state)
        self.state = state

    # ---- fed by the state -------------------------------------------------

    def apply_snapshot(self, snap: dict) -> None:
        """GET /api/state, after every (re)connect."""

    def apply_event(self, name: str, data: dict) -> None:
        """One stream event, of any name."""

    # ---- reads ------------------------------------------------------------

    def current(self):
        """The current machine's newest scan, or None."""
        return

    def all(self) -> list:
        """The current machine's scans, newest first."""
        return []

    def model_path(self, scan_id: str):
        """Path of the converted file in the app's cache, or None while it is being
        fetched. Asking starts the fetch."""
        return

    def qr_text(self, machine_id: str, name: str = "") -> str:
        """The line the QR code holds (contract section 8.2), with this laptop's address on the hotspot."""
        return ""

    # ---- actions ----------------------------------------------------------

    def import_file(self, path) -> None:
        """Send a USDZ or GLB file from this laptop's disk as a scan for the current machine."""
        self.state.action_failed.emit(NOT_BUILT)
