"""State and storage for the effente receiver.

Holds everything in memory and appends each change to a JSON lines file in
the data dir, so history survives a restart. Masks are never stored: they
start empty each time the platform starts (platform/CONTRACT.md section 3).

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


def _slim(record, spectrum, frames):
    """A history record without the bulky parts the caller did not ask for:
    `spectrum`, and the images in `heat.frames` (contract 3.3)."""
    out = record if spectrum else {k: v for k, v in record.items() if k != "spectrum"}
    heat = out.get("heat")
    if not frames and isinstance(heat, dict) and "frames" in heat:
        out = dict(out)
        out["heat"] = {k: v for k, v in heat.items() if k != "frames"}
    return out


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
        self.mask = []
        self.mask_version = 0
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

    def _touch(self, device_id):
        """Note that a device was heard from. Caller holds the lock."""
        device = self._device(device_id)
        device.last_seen = utc_now()
        device.last_seen_mono = time.monotonic()
        if not device.connected:
            device.connected = True
            self.log(f"device {device_id} connected")
            self.emit("device", self._device_event(device))
        return device

    @staticmethod
    def _device_event(device):
        return {"device_id": device.device_id, "connected": device.connected, "last_seen": device.last_seen}

    def check_quiet(self):
        """Mark devices not heard from in `quiet_after` seconds as disconnected."""
        now = time.monotonic()
        with self.lock:
            for device in self.devices.values():
                if device.connected and now - device.last_seen_mono > self.quiet_after:
                    device.connected = False
                    self.log(f"device {device.device_id} went quiet")
                    self.emit("device", self._device_event(device))

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
        with self.lock:
            received_at = utc_now()
            for record in batch:
                record.setdefault("simulated", False)
                record["seq"] = self.next_seq
                record["received_at"] = received_at
                self.next_seq += 1
            self.records_file.append(batch)
            need_model = False
            device = None
            for record in batch:
                self._index_record(record)
                device = self._touch(record["device_id"])
                device.latest = record
                device.model_version = record["model_version"]
                if record["model_version"] not in self.models:
                    need_model = True
                self.emit("record", record)
            reply = {"ok": True, "stored": len(batch), "mask": [], "mask_version": 0, "need_model": need_model}
            if device is not None:
                reply["mask"] = list(device.mask)
                reply["mask_version"] = device.mask_version
            return reply

    # ---- message 3: mask --------------------------------------------------

    def _mask_reply(self, device):
        return {"ok": True, "device_id": device.device_id, "mask": list(device.mask), "mask_version": device.mask_version}

    def set_mask(self, device_id, body):
        if not isinstance(body, dict) or not isinstance(body.get("mask"), list):
            raise BadRequest("body must be {\"mask\": [feature names]}")
        names = body["mask"]
        if not all(isinstance(n, str) for n in names):
            raise BadRequest("mask must be a list of feature name strings")
        mask = list(dict.fromkeys(names))  # drop repeats, keep order
        with self.lock:
            device = self.devices.get(device_id)
            if mask:
                model = self.models.get(device.announced_version) if device else None
                if model is None:
                    raise BadRequest(f"device '{device_id}' has not announced a model, so it has no features to switch off")
                known = {f["name"] for f in model.get("features", []) if isinstance(f, dict)}
                unknown = [n for n in mask if n not in known]
                if unknown:
                    raise BadRequest(
                        f"unknown feature(s) {', '.join(unknown)} for device '{device_id}' model '{model['model_version']}'"
                    )
            if device is None:  # clearing the mask of a device never seen: nothing to do
                return {"ok": True, "device_id": device_id, "mask": [], "mask_version": 0}
            if set(mask) != set(device.mask):
                device.mask = mask
                device.mask_version += 1
                reply = self._mask_reply(device)
                self.emit("mask", reply)
                return reply
            return self._mask_reply(device)

    def get_mask(self, device_id):
        """The device's poll when it is not scanning. Counts as contact."""
        with self.lock:
            return self._mask_reply(self._touch(device_id))

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

    def machine_records(self, machine_id, limit, spectrum, frames=False):
        with self.lock:
            history = self.by_machine.get(machine_id, [])
            chosen = history[-limit:] if limit > 0 else []
        return [_slim(r, spectrum, frames) for r in chosen]

    def state(self):
        with self.lock:
            devices = {}
            for device in self.devices.values():
                devices[device.device_id] = {
                    "connected": device.connected,
                    "last_seen": device.last_seen,
                    "model_version": device.model_version,
                    "mask": list(device.mask),
                    "mask_version": device.mask_version,
                    "latest": device.latest,
                }
            return {
                "ok": True,
                "now": utc_now(),
                "devices": devices,
                "models": dict(self.models),
                "machines": sorted(self.by_machine),
            }
