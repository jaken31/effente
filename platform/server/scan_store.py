"""Scans of machines (platform/CONTRACT.md sections 8.3 and 8.4). A stub: nothing is built yet.

The receiver's Store makes one of these, as `store.scans`. The route functions
in scan_api.py reach it as `request.server.store.scans`.

`store` is the receiver's Store. Use its `data_dir`, `emit(name, data)`, `log`
and `lock`. `state_part()` is called with `store.lock` already held, so it must
not take it. Everything else here must take it.
"""


class ScanStore:
    def __init__(self, store):
        self.store = store

    def state_part(self):
        """This store's keys of GET /api/state: `scans`, and under `machines`
        the machines that have a scan, which the Store adds to its own list."""
        return {}

    def close(self):
        """The receiver is stopping: end any work still going."""
