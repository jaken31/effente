"""Machine profiles and the wanted library entry (platform/CONTRACT.md sections 10.1
and 10.4). A stub: nothing is built yet.

The receiver's Store makes one of these, as `store.setup`, after it has loaded its
own files and made the scan and log stores. The route functions in setup_api.py
reach it as `request.server.store.setup`.

`store` is the receiver's Store. Use its `data_dir`, `emit(name, data)`, `log`
and `lock`. `state_part()` and the three calls below it are made with
`store.lock` already held: they may read the Store's fields but must not take
the lock or call its public methods. Everything else here must take the lock.

A call that fails is logged and skipped, so it can never stop a record, a
check-in or `/api/state`. The one exception: `check_settings` may raise
BadRequest to refuse the settings.

To send a `settings` event when the wanted entry changes, emit
`store._settings_event(device_id)` for each device on that machine: it asks
`wanted_settings` and `device_part` itself.
"""


class SetupStore:
    def __init__(self, store):
        self.store = store

    def state_part(self):
        """This store's keys of GET /api/state: `profiles`, and under `machines`
        the machines that have a profile, which the Store adds to its own list."""
        return {}

    # ---- called by the Store with its lock held ----------------------------

    def wanted_settings(self, machine_id):
        """Keys to add to the wanted `settings` of a device that reports this
        machine (`None` if it has reported none): `{"library_entry": ...}` once a
        profile is confirmed (section 10.4). They go into the replies to
        messages 2 and 3, the device's `settings` in `/api/state`, and the
        `settings` event."""
        return {}

    def device_part(self, device):
        """Keys to add to this Device's entry in `/api/state` and to its
        `settings` event: `library_entry`, the entry the device reports (section 10.4)."""
        return {}

    def check_settings(self, device_id, body):
        """`PUT /api/devices/<device_id>/settings` is about to store `body`, a
        JSON object that holds `sensitivity`. Raise BadRequest to refuse it."""

    def close(self):
        """The receiver is stopping."""
