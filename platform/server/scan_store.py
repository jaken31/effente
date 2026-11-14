"""Scans of machines (platform/CONTRACT.md sections 8.1, 8.3 and 8.4).

The receiver's Store makes one of these, as `store.scans`. The route functions
in scan_api.py reach it as `request.server.store.scans`.

All of the logic is in platform/scan/scan_records.py, which knows nothing about
the receiver. This file only joins the two: the scans live in the receiver's
data folder, and a `scan` event goes out through the receiver's stream.

The scans have their own lock and never take `store.lock`, so `state_part()`,
which is called with `store.lock` held, is safe.
"""

import os
import sys

SCAN_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scan")
if SCAN_DIR not in sys.path:
    sys.path.insert(0, SCAN_DIR)

import scan_records  # noqa: E402
from scan_records import ScanError  # noqa: E402, F401  (scan_api.py takes it from here)


class ScanStore(scan_records.ScanStore):
    def __init__(self, store):
        self.store = store
        super().__init__(store.data_dir, publish=store.emit)

    def state_part(self):
        """This store's keys of GET /api/state: `scans`, and under `machines`
        the machines that have a scan, which the Store adds to its own list."""
        return dict(super().state_part(), machines=self.machines())

    # close(), inherited: the receiver is stopping, so the converter is stopped.
