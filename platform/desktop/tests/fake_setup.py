"""FakeSetup: the `state.setup` interface (INTERFACES.md addition 24) on fixed data, for page tests.

    state = FakeState()
    state.setup.profile()        # a confirmed machine profile for the fake state's machine

The sample of INTERFACES.md addition 25, written here by hand, not worked out by
any real code, and copied from the examples in contract section 10:

- A finished stand-in run that filled three fields and left two empty, labelled
  as a stand-in and as simulated.
- The choice of `conveyor`, with the lists of contract 10.2's example.
- A confirmed machine profile made from that run. Its wanted entry, `conveyor`,
  differs from the one the device reports, `generic_rotating`.
- A local profile "Sam, technician". So `FakeState().answerer()` is "Sam".

Any other machine has no profile, no run and no wanted entry. `clear()` shows
the fake state's own machine that way too, with no local profile either.

Actions change only this object and emit the same signals as the real one. No
lookup and no agent runs: `set_draft` keeps the choice it has (`set_choice`
shows another, or `None` for one being fetched), and a run started with
`start_run` shows as running until `finish_run`.
"""

from __future__ import annotations

import copy

from app.data.setup import FIELDS, Setup

RUN_ID = "setup-20261114-200300-fake"
WANTED_ENTRY, DEVICE_ENTRY = "conveyor", "generic_rotating"
LOCAL_PROFILE = {"name": "Sam", "role": "technician"}
RUN_FIELDS = {"machine_type": "conveyor", "drive": "belt", "speed_rpm": None, "mounting": "rigid",
              "sensor_location": None}
EXPLANATION = ("Stand-in: you said it is a belt conveyor bolted to the floor. You did not know its speed or "
               "where the sensor sits, so those are left empty.")


def sample_choice() -> dict:
    """Contract 10.2's example, as it is written there."""
    return {
        "library_entry": WANTED_ENTRY, "version": "0.1.0", "reason": "machine type is conveyor", "status": "demo",
        "trained_on": [{"name": "sim_conveyor", "kind": "simulated"}],
        "checked_on": [],
        "not_checked_on": [{"name": "sim_server_rack", "kind": "simulated",
                            "note": "wrong machine: a server rack, for the fit check"}],
        "fault_types_validated": [],
        "fault_types_not_validated": 21,
    }


def sample_run(machine_id: str, ok: bool = True, reason: str = "") -> dict:
    """A finished setup run by the stand-in (contract 10.3). One that failed carries no result."""
    return {
        "run_id": RUN_ID, "agent": "setup", "machine_id": machine_id, "at": "2026-11-14T20:03:00Z",
        "status": "done", "model": "stand-in (no model call)", "ok": ok, "reason": reason, "problems": [],
        "tool_calls": [{"at": "2026-11-14T20:03:01Z", "name": "get_answers", "input": {},
                        "output": "{\"answers\": 5}"}],
        "report": None,
        "setup": {"profile": dict(RUN_FIELDS), "explanation": EXPLANATION, "choice": sample_choice()} if ok else None,
        "simulated": True,
    }


def sample_profile(machine_id: str) -> dict:
    """The run's five fields, confirmed (contract 10.1)."""
    return dict(RUN_FIELDS, machine_id=machine_id, confirmed={"at": "2026-11-14T20:04:10Z", "by": LOCAL_PROFILE["name"]},
                run_id=RUN_ID, choice=sample_choice())


class FakeSetup(Setup):
    def __init__(self, state):
        super().__init__(state)
        self.machine = None  # the machine the sample belongs to; set by fill()
        self._profile = None
        self._draft = dict.fromkeys(FIELDS)
        self._choice = None
        self._run = None
        self._running = False
        self._wanted = None
        self._device_entry = None
        self._local = None
        self.started_runs: list = []  # (answers, mode) of each start_run call

    def fill(self) -> None:
        """Load the sample for the fake state's current machine."""
        self.machine = self.state.current_machine()
        self._profile = sample_profile(self.machine)
        self._draft = {k: self._profile[k] for k in FIELDS}
        self._choice = sample_choice()
        self._run = sample_run(self.machine)
        self._wanted, self._device_entry = WANTED_ENTRY, DEVICE_ENTRY
        self._local = dict(LOCAL_PROFILE)

    def _here(self) -> bool:
        return self.machine is not None and self.state.current_machine() == self.machine

    # ---- reads ------------------------------------------------------------

    def profile(self):
        return self._profile if self._here() else None

    def draft(self) -> dict:
        return dict(self._draft) if self._here() else dict.fromkeys(FIELDS)

    def choice(self):
        return self._choice if self._here() else None

    def run(self):
        return self._run if self._here() else None

    def run_running(self) -> bool:
        return self._running and self._here()

    def wanted_entry(self):
        return self._wanted if self._here() else None

    def device_entry(self):
        return self._device_entry if self._here() else None

    def local_profile(self):
        return dict(self._local) if self._local else None

    # ---- actions ----------------------------------------------------------

    def start_run(self, answers, mode="standin") -> None:
        """No agent runs: the call is remembered and a run shows as running until finish_run."""
        self.started_runs.append((copy.deepcopy(answers), mode))
        self._running = True
        self.run_changed.emit()

    def set_draft(self, **fields) -> None:
        unknown = [k for k in fields if k not in FIELDS]
        if unknown:
            self.state.action_failed.emit(f"Profile not changed: unknown field: {unknown[0]}")
            return
        self._draft.update(fields)
        self.profile_changed.emit()

    def confirm(self) -> None:
        if self._choice is None:
            self.state.action_failed.emit("Profile not confirmed: the starting model is not known yet.")
            return
        self._profile = dict(self._draft, machine_id=self.machine,
                             confirmed={"at": "2026-11-14T20:04:10Z", "by": self.state.answerer()},
                             run_id=self._run["run_id"] if self._run and self._run["ok"] else None,
                             choice=copy.deepcopy(self._choice))
        self._wanted = self._choice["library_entry"]
        self.profile_changed.emit()
        self.entry_changed.emit()

    def set_local_profile(self, name, role) -> None:
        self._local = {"name": name, "role": role}
        self.local_profile_changed.emit()

    # ---- test helpers -----------------------------------------------------

    def finish_run(self, ok: bool = True, reason: str = "") -> None:
        """End the pretend run. One that passed fills the draft, as the real one does."""
        self._running = False
        self._run = sample_run(self.machine, ok, reason)
        if ok:
            self._draft = dict(RUN_FIELDS)
            self._choice = sample_choice()
            self.profile_changed.emit()
        self.run_changed.emit()

    def set_choice(self, choice) -> None:
        """Show another choice, or `None` for one that is being fetched."""
        self._choice = choice
        self.profile_changed.emit()

    def set_entries(self, wanted, device) -> None:
        """Show the wanted entry and the one the device reports. `None` for either is none."""
        self._wanted, self._device_entry = wanted, device
        self.entry_changed.emit()

    def clear(self) -> None:
        """Nothing set up yet: no profile, no run, an empty draft, no wanted entry and no local profile."""
        self._profile, self._run, self._choice, self._running = None, None, None, False
        self._draft = dict.fromkeys(FIELDS)
        self._wanted, self._device_entry, self._local = None, None, None
        self.profile_changed.emit()
        self.run_changed.emit()
        self.entry_changed.emit()
        self.local_profile_changed.emit()
