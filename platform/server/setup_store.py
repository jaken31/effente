"""Machine profiles and the wanted library entry (platform/CONTRACT.md sections 10.1
and 10.4).

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

One append-only file in the data dir, `machine_profiles.jsonl`: every stored
profile, oldest first. The newest one per machine counts. Storing a profile is
confirming it (contract 10.1): there is no stored draft.
"""

import os
import sys

from receiver_store import BadRequest, JsonLines, utc_now

PLATFORM_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PLATFORM_DIR not in sys.path:
    sys.path.append(PLATFORM_DIR)  # for `setup.lookup`; appended so nothing here is shadowed

from setup import lookup  # noqa: E402

PROFILES_FILE = "machine_profiles.jsonl"
SET_BY_PLATFORM = ("machine_id", "confirmed", "choice")  # never taken from a client's body


def _profile_from(machine_id, body, entries_dir):
    """The profile a PUT body stores, checked (contract 10.1). Raises BadRequest."""
    if not isinstance(body, dict):
        raise BadRequest("machine profile must be a JSON object")
    by = body.get("by")
    if not isinstance(by, str) or not by.strip():
        raise BadRequest("machine profile is missing required field 'by' (the name of the person who confirmed it)")
    problems = lookup.field_problems(body)
    if problems:
        raise BadRequest(f"machine profile: {problems[0]}")
    run_id = body.get("run_id")
    if run_id is not None and (not isinstance(run_id, str) or not run_id):
        raise BadRequest("machine profile 'run_id' must be the setup run's id, or null if filled by hand")
    five = lookup.five_fields(body)
    # The lookup runs again here, whatever the app showed: this choice counts (contract 10.4).
    profile = {"machine_id": machine_id, **five, "confirmed": {"at": utc_now(), "by": by.strip()},
               "run_id": run_id, "choice": lookup.choose(five, entries_dir)}
    # Unknown fields (the lifecycle doc's other five among them) are stored and passed on.
    skip = (*lookup.FIELDS, "by", "run_id", *SET_BY_PLATFORM)
    profile.update((k, v) for k, v in body.items() if k not in skip)
    return profile


class SetupStore:
    def __init__(self, store):
        self.store = store
        self.file = JsonLines(os.path.join(store.data_dir, PROFILES_FILE))
        self.entries_dir = lookup.ENTRIES_DIR  # the library the lookup reads; only read, never written
        self.profiles = {}  # machine_id -> its newest profile
        for profile in self.file.load():
            if isinstance(profile.get("machine_id"), str):
                self.profiles[profile["machine_id"]] = profile

    # ---- the routes (contract 10.1) ----------------------------------------

    def put_profile(self, machine_id, body):
        """Store a confirmed profile in place of the machine's last one. Returns it."""
        profile = _profile_from(machine_id, body, self.entries_dir)
        with self.store.lock:
            self.file.append([profile])
            self.profiles[machine_id] = profile
            self.store.emit("profile", profile)
        return profile

    def lookup(self, body):
        """What the lookup would pick for these fields (contract 10.2). Stores and sends nothing."""
        if not isinstance(body, dict):
            raise BadRequest("lookup body must be a JSON object")
        problems = lookup.field_problems(body)
        if problems:
            raise BadRequest(f"lookup: {problems[0]}")
        return lookup.choose(lookup.five_fields(body), self.entries_dir)

    def get_profile(self, machine_id):
        with self.store.lock:
            return self.profiles.get(machine_id)

    def state_part(self):
        """This store's keys of GET /api/state: `profiles`, and under `machines`
        the machines that have a profile, which the Store adds to its own list."""
        return {"profiles": dict(self.profiles), "machines": list(self.profiles)}

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
        """The receiver is stopping. Every profile is already on disk."""
