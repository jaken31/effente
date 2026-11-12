"""State and storage for the effente receiver.

Holds everything in memory and appends each change to a JSON lines file in
the data dir, so history survives a restart. Check-ins (platform/CONTRACT.md
message 3) and each device's mode and problems are not stored: after a restart
no device is connected, so they start empty until the device is heard from.

Every public method takes the one lock, so the HTTP handler threads can call
them freely. Events go out through `emit(name, data)`, which must not block.
"""

import datetime as dt
import json
import os
import re
import sys
import threading
import time

REQUIRED_RECORD_FIELDS = ("device_id", "machine_id", "model_version", "at", "diagnosis")
REQUIRED_CHECK_IN_FIELDS = ("machine_id", "mode", "at")
CHECK_IN_MODES = ("display", "calibrating", "recording", "training")
WATCHING = "watching"  # the mode of a device that sends records
QUIET_REASON = "device quiet"
REQUIRED_RUN_FIELDS = ("run_id", "machine_id", "at", "status")
RUN_STATUSES = ("running", "done")
RUN_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

DEFAULT_HISTORY_LIMIT = 300
MAX_HISTORY_LIMIT = 2000
QUIET_AFTER_S = 5.0

RECORDS_FILE = "records.jsonl"
MODELS_FILE = "models.jsonl"
RUNS_FILE = "agent_runs.jsonl"


class BadRequest(Exception):
    """The request broke the contract. Becomes HTTP 400 with this message."""


def utc_now():
    """Current time as ISO 8601 UTC with milliseconds, for example 2026-11-14T16:02:11.123Z."""
    now = dt.datetime.now(dt.timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def _parse_time(text):
    """ISO time to a sortable UTC timestamp. Unparseable times sort first."""
    if not isinstance(text, str):
        return float("-inf")
    try:
        value = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return float("-inf")
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return value.timestamp()


def _require_strings(obj, names, what):
    for name in names:
        value = obj.get(name)
        if not isinstance(value, str) or value == "":
            raise BadRequest(f"{what} is missing required field '{name}'")


def _require_problems(obj, what):
    """Check `wrong` and `problems` (contract messages 2 and 3).

    Problem names are not checked against a list: the device's safety checks
    own them. A name is needed, and must not repeat, because the platform
    tells problems apart by name.
    """
    if not isinstance(obj.get("wrong"), bool):
        raise BadRequest(f"{what} is missing required field 'wrong' (true or false)")
    problems = obj.get("problems")
    if not isinstance(problems, list):
        raise BadRequest(f"{what} is missing required field 'problems' (a list, [] when nothing is wrong)")
    seen = set()
    for i, problem in enumerate(problems):
        name = problem.get("name") if isinstance(problem, dict) else None
        if not isinstance(name, str) or name == "":
            raise BadRequest(f"{what} problem {i} needs a 'name' string")
        if name in seen:
            raise BadRequest(f"{what} lists problem '{name}' twice")
        seen.add(name)


class JsonLines:
    """One append-only JSON lines file."""

    def __init__(self, path):
        self.path = path

    def load(self):
        """Read every good line. Bad lines (for example a half-written last
        line after a crash) are skipped with a warning, never fatal."""
        if not os.path.exists(self.path):
            return []
        items, bad = [], 0
        with open(self.path, "rb") as f:
            data = f.read()
        for raw in data.split(b"\n"):
            if not raw.strip():
                continue
            try:
                item = json.loads(raw)
            except ValueError:
                bad += 1
                continue
            if isinstance(item, dict):
                items.append(item)
            else:
                bad += 1
        if bad:
            print(f"warning: skipped {bad} unreadable line(s) in {self.path}", file=sys.stderr)
        # If the last line was cut off, end it so the next append starts clean.
        if data and not data.endswith(b"\n"):
            with open(self.path, "ab") as f:
                f.write(b"\n")
        return items

    def append(self, items):
        if not items:
            return
        text = "".join(json.dumps(item, separators=(",", ":")) + "\n" for item in items)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(text)
            f.flush()


class Device:
    def __init__(self, device_id):
        self.device_id = device_id
        self.connected = False
        self.last_seen = None  # ISO string
        self.last_seen_mono = None  # time.monotonic() of last contact, this run only
        self.model_version = None  # newest version seen in a record or announcement
        self.announced_version = None  # version of the last POST /api/model
        self.mode = None  # "watching" after a record, else the check-in's mode; None until either
        self.machine_id = None  # from the newest record or check-in
        self.wrong = False  # from the newest record or check-in, this run only
        self.problems = []  # likewise; [] while not connected
        self.latest = None


class Store:
    def __init__(self, data_dir, emit=None, quiet_after=QUIET_AFTER_S, log=print):
        os.makedirs(data_dir, exist_ok=True)
        self.data_dir = data_dir
        self.emit = emit or (lambda name, data: None)
        self.quiet_after = quiet_after
        self.log = log
        self.lock = threading.Lock()
        self.records_file = JsonLines(os.path.join(data_dir, RECORDS_FILE))
        self.models_file = JsonLines(os.path.join(data_dir, MODELS_FILE))
        self.runs_file = JsonLines(os.path.join(data_dir, RUNS_FILE))
        self.records = []  # every record, in seq order
        self.by_machine = {}  # machine_id -> list of records
        self.models = {}  # model_version -> announcement
        self.runs = {}  # run_id -> latest copy
        self.run_order = {}  # run_id -> order of last update, breaks ties in sorting
        self._run_counter = 0
        self.devices = {}
        self.next_seq = 1
        self._load()

    # ---- start-up -------------------------------------------------------

    def _load(self):
        for model in self.models_file.load():
            version = model.get("model_version")
            if not isinstance(version, str):
                continue
            self.models[version] = model
            device_id = model.get("device_id")
            if isinstance(device_id, str):
                device = self._device(device_id)
                device.announced_version = version
                device.model_version = version
        for record in self.records_file.load():
            if not isinstance(record.get("device_id"), str) or not isinstance(record.get("machine_id"), str):
                continue
            seq = record.get("seq")
            if not isinstance(seq, int) or isinstance(seq, bool):
                seq = self.next_seq
                record["seq"] = seq
            self.next_seq = max(self.next_seq, seq + 1)
            self._index_record(record)
            device = self._device(record["device_id"])
            device.latest = record
            device.machine_id = record["machine_id"]
            device.last_seen = record.get("received_at", device.last_seen)
            if isinstance(record.get("model_version"), str):
                device.model_version = record["model_version"]
        self.records.sort(key=lambda r: r["seq"])
        for history in self.by_machine.values():
            history.sort(key=lambda r: r["seq"])
        for run in self.runs_file.load():
            run_id = run.get("run_id")
            if isinstance(run_id, str):
                self._put_run(run)

    def _device(self, device_id):
        device = self.devices.get(device_id)
        if device is None:
            device = self.devices[device_id] = Device(device_id)
        return device

    def _index_record(self, record):
        self.records.append(record)
        self.by_machine.setdefault(record["machine_id"], []).append(record)

    def _put_run(self, run):
        self._run_counter += 1
        self.runs[run["run_id"]] = run
        self.run_order[run["run_id"]] = self._run_counter

    # ---- device contact -------------------------------------------------

    def _touch(self, device_id, mode=None):
        """Note that a device was heard from. Caller holds the lock.

        `mode` is "watching" for a record and the check-in's mode for a
        check-in. A model announcement passes None: it keeps a connected
        device connected but does not connect one by itself, because the
        `device` event must say the mode and an announcement does not.
        """
        device = self._device(device_id)
        device.last_seen = utc_now()
        if mode is None and not device.connected:
            return device
        device.last_seen_mono = time.monotonic()
        changed = False
        if mode is not None and mode != device.mode:
            device.mode = mode
            changed = True
        if not device.connected:
            device.connected = True
            changed = True
            self.log(f"device {device_id} connected ({device.mode})")
        if changed:
            self.emit("device", self._device_event(device))
        return device

    @staticmethod
    def _device_event(device):
        return {
            "device_id": device.device_id,
            "connected": device.connected,
            "last_seen": device.last_seen,
            "mode": device.mode,
        }

    def _set_problems(self, device, message):
        """Take `wrong` and `problems` from a record or check-in, and emit a
        `problem` for each new name and a `problem_cleared` for each name that
        went away. Caller holds the lock."""
        before = {p["name"]: p for p in device.problems}
        now = {p["name"]: p for p in message["problems"]}
        device.wrong = message["wrong"]
        device.problems = list(message["problems"])
        device.machine_id = message["machine_id"]
        for name in before:
            if name not in now:
                self.emit("problem_cleared", self._cleared_event(device, message["at"], name))
        for name, problem in now.items():
            if name not in before:
                self.emit(
                    "problem",
                    {"device_id": device.device_id, "machine_id": device.machine_id, "at": message["at"], "problem": problem},
                )

    @staticmethod
    def _cleared_event(device, at, name, reason=None):
        event = {"device_id": device.device_id, "machine_id": device.machine_id, "at": at, "name": name}
        if reason is not None:
            event["reason"] = reason
        return event

    def check_quiet(self):
        """Mark devices not heard from in `quiet_after` seconds as disconnected,
        and clear their problems with the reason "device quiet"."""
        now = time.monotonic()
        with self.lock:
            for device in self.devices.values():
                if device.connected and now - device.last_seen_mono > self.quiet_after:
                    device.connected = False
                    self.log(f"device {device.device_id} went quiet")
                    self.emit("device", self._device_event(device))
                    # No message made this change, so `at` is the platform's time.
                    at = utc_now()
                    for problem in device.problems:
                        self.emit("problem_cleared", self._cleared_event(device, at, problem["name"], QUIET_REASON))
                    device.problems = []
                    device.wrong = False

    # ---- message 1: model -------------------------------------------------

    def announce_model(self, model):
        if not isinstance(model, dict):
            raise BadRequest("model announcement must be a JSON object")
        _require_strings(model, ("device_id", "model_version"), "model announcement")
        features = model.get("features")
        if not isinstance(features, list):
            raise BadRequest("model announcement 'features' must be a list")
        for i, feature in enumerate(features):
            if not isinstance(feature, dict) or not isinstance(feature.get("name"), str) or not feature["name"]:
                raise BadRequest(f"model announcement feature {i} needs a 'name' string")
        with self.lock:
            self.models_file.append([model])
            self.models[model["model_version"]] = model
            device = self._touch(model["device_id"])
            device.announced_version = model["model_version"]
            device.model_version = model["model_version"]
            self.emit("model", model)

    # ---- message 2: records -----------------------------------------------

    def add_records(self, body):
        """Store one record or a list. Returns the reply for the device."""
        batch = body if isinstance(body, list) else [body]
        for i, record in enumerate(batch):
            what = "record" if not isinstance(body, list) else f"record {i}"
            if not isinstance(record, dict):
                raise BadRequest(f"{what} must be a JSON object")
            _require_strings(record, REQUIRED_RECORD_FIELDS, what)
            _require_problems(record, what)
        with self.lock:
            received_at = utc_now()
            for record in batch:
                record.setdefault("simulated", False)
                record["seq"] = self.next_seq
                record["received_at"] = received_at
                self.next_seq += 1
            self.records_file.append(batch)
            need_model = False
            for record in batch:
                self._index_record(record)
                device = self._touch(record["device_id"], WATCHING)
                device.latest = record
                device.model_version = record["model_version"]
                if record["model_version"] not in self.models:
                    need_model = True
                self.emit("record", record)
                self._set_problems(device, record)
            return {"ok": True, "stored": len(batch), "need_model": need_model}

    # ---- message 3: check-in ----------------------------------------------

    def check_in(self, device_id, body):
        """A device that is not watching says it is alive, its mode and its problems."""
        if not isinstance(body, dict):
            raise BadRequest("check-in must be a JSON object")
        _require_strings(body, REQUIRED_CHECK_IN_FIELDS, "check-in")
        if body["mode"] not in CHECK_IN_MODES:
            raise BadRequest(f"check-in mode must be one of {', '.join(CHECK_IN_MODES)}")
        _require_problems(body, "check-in")
        with self.lock:
            device = self._touch(device_id, body["mode"])
            self._set_problems(device, body)

    # ---- message 4: agent runs --------------------------------------------

    def put_run(self, run_id, run):
        if not RUN_ID_RE.match(run_id):
            raise BadRequest("run_id may contain letters, digits, '-', '_' and '.' only")
        if not isinstance(run, dict):
            raise BadRequest("agent run must be a JSON object")
        _require_strings(run, REQUIRED_RUN_FIELDS, "agent run")
        if run["run_id"] != run_id:
            raise BadRequest("run_id in the body does not match the URL")
        if run["status"] not in RUN_STATUSES:
            raise BadRequest("status must be 'running' or 'done'")
        with self.lock:
            self.runs_file.append([run])
            self._put_run(run)
            self.emit("agent_run", run)

    def machine_runs(self, machine_id, limit):
        with self.lock:
            runs = [r for r in self.runs.values() if r.get("machine_id") == machine_id]
            runs.sort(key=lambda r: (_parse_time(r.get("at")), self.run_order[r["run_id"]]), reverse=True)
            return runs[:limit]

    # ---- section 5: reads -------------------------------------------------

    def machine_records(self, machine_id, limit, spectrum):
        with self.lock:
            history = self.by_machine.get(machine_id, [])
            chosen = history[-limit:] if limit > 0 else []
        if spectrum:
            return list(chosen)
        return [{k: v for k, v in r.items() if k != "spectrum"} for r in chosen]

    def state(self):
        with self.lock:
            devices = {}
            for device in self.devices.values():
                connected = device.connected
                devices[device.device_id] = {
                    "connected": connected,
                    "last_seen": device.last_seen,
                    "mode": device.mode,
                    "machine_id": device.machine_id,
                    "model_version": device.model_version,
                    "wrong": device.wrong if connected else False,
                    "problems": list(device.problems) if connected else [],
                    "latest": device.latest,
                }
            return {
                "ok": True,
                "now": utc_now(),
                "devices": devices,
                "models": dict(self.models),
                "machines": sorted(self.by_machine),
            }
