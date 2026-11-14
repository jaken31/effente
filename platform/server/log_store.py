"""Parts, log entries and part states (platform/CONTRACT.md sections 8.5 to 8.7).
A stub: nothing is built yet.

The receiver's Store makes one of these, as `store.partlog`, after it has loaded its
own files, so `store.alarms` and `store.by_machine` are already there to read.
The route functions in log_api.py reach it as `request.server.store.partlog`.

`store` is the receiver's Store. Use its `data_dir`, `emit(name, data)`, `log`
and `lock`. `state_part()` and the three hooks are called with `store.lock`
already held: they may read the Store's fields but must not take the lock or
call its public methods. Everything else here must take the lock.

A hook that fails is logged and skipped, so it can never stop a record or an
answer from being stored. The one exception: `alarm_answered` may raise
BadRequest to refuse the answer.
"""


class LogStore:
    def __init__(self, store):
        self.store = store

    def state_part(self):
        """This store's keys of GET /api/state: `parts`, and under `machines`
        the machines that have a part, which the Store adds to its own list."""
        return {}

    # ---- hooks, called by the Store with its lock held ---------------------

    def record_stored(self, record):
        """A record was stored (message 2). Not called for records read at start."""

    def alarm_changed(self, alarm):
        """An alarm opened, changed, closed or got an answer: every time the
        Store sends an `alarm` event. `alarm` is the Store's own, not a copy."""

    def alarm_answered(self, alarm, answer):
        """An answer is about to be stored on `alarm`, which does not carry it
        yet. Raise BadRequest to refuse it. `alarm_changed` follows once it is stored."""

    def close(self):
        """The receiver is stopping."""
