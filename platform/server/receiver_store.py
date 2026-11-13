"""State and storage for the effente receiver.

Holds everything in memory and appends each change to a JSON lines file in
the data dir, so history survives a restart. Check-ins (platform/CONTRACT.md
message 3) and each device's mode and problems are not stored: after a restart
no device is connected, so they start empty until the device is heard from.
Alarms (section 6) are not stored either: they are worked out again from the
stored records at start, and the stored answers are put back on them.

Every public method takes the one lock, so the HTTP handler threads can call
them freely. Events go out through `emit(name, data)`, which must not block.
"""

import bisect
import datetime as dt
import json
import math
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
NOT_ABNORMAL = ("normal", "stopped", "warming_up")  # any other diagnosis is abnormal (section 6)
ALARMS_PER_MACHINE = 20  # newest alarms of each machine in /api/state, besides every open one
VERDICTS = ("real", "false")

DEFAULT_HISTORY_LIMIT = 300
MAX_HISTORY_LIMIT = 2000
QUIET_AFTER_S = 5.0

RECORDS_FILE = "records.jsonl"
MODELS_FILE = "models.jsonl"
RUNS_FILE = "agent_runs.jsonl"
ANSWERS_FILE = "alarm_answers.jsonl"
SETTINGS_FILE = "settings.jsonl"


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


def parse_time(text):
    """ISO time to a UTC timestamp, or None if it is not one. No zone means UTC."""
    if not isinstance(text, str):
        return None
    try:
        value = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return value.timestamp()


def _parse_time(text):
    """ISO time to a sortable UTC timestamp. Unparseable times sort first."""
    value = parse_time(text)
    return float("-inf") if value is None else value


def _number(value):
    """`value` if it is a finite number (not a bool), else None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) else None


def _sensitivity(value):
    """`value` if it is a sensitivity, a number from 0 to 1, else None."""
    value = _number(value)
    return value if value is not None and 0 <= value <= 1 else None


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
        self.sensitivity = None  # last value the device reported; kept while not connected
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
        self.answers_file = JsonLines(os.path.join(data_dir, ANSWERS_FILE))
        self.settings_file = JsonLines(os.path.join(data_dir, SETTINGS_FILE))
        self.records = []  # every record, in seq order
        self.by_machine = {}  # machine_id -> list of records
        self.models = {}  # model_version -> announcement
        self.runs = {}  # run_id -> latest copy
        self.run_order = {}  # run_id -> order of last update, breaks ties in sorting
        self._run_counter = 0
        self.devices = {}
        self.settings = {}  # device_id -> {"sensitivity": x}, what the dashboard set
        self.alarms = {}  # alarm_id -> alarm
        self.alarm_seq = {}  # alarm_id -> seq of the record that opened it
        self.machine_alarms = {}  # machine_id -> its alarms, oldest first
        self.open_alarms = {}  # machine_id -> its open alarm
        self.last_minutes = {}  # machine_id -> abnormal_minutes of the open alarm's newest record
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
                self._note_sensitivity(device, model.get("sensitivity"), emit=False)
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
            self._note_sensitivity(device, record.get("sensitivity"), emit=False)
        self.records.sort(key=lambda r: r["seq"])
        for history in self.by_machine.values():
            history.sort(key=lambda r: r["seq"])
        for record in self.records:
            if isinstance(record.get("diagnosis"), str):
                self._track_alarm(record, emit=False)
        for run in self.runs_file.load():
            run_id = run.get("run_id")
            if isinstance(run_id, str):
                self._put_run(run)
        lost = 0
        for answer in self.answers_file.load():
            alarm = self.alarms.get(answer.get("alarm_id"))
            if alarm is None:
                lost += 1
                continue
            alarm["answers"].append(answer)
            alarm["answer"] = answer
        if lost:
            print(f"warning: {lost} stored answer(s) belong to alarms no stored record opens", file=sys.stderr)
        for entry in self.settings_file.load():
            device_id = entry.get("device_id")
            if not isinstance(device_id, str):
                continue
            self._device(device_id)
            value = _sensitivity(entry.get("sensitivity"))
            if value is None:
                self.settings.pop(device_id, None)
            else:
                self.settings[device_id] = {"sensitivity": value}

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

    def _note_sensitivity(self, device, value, emit=True):
        """Keep the sensitivity a device reported, if it reported one, and
        emit `settings` when it changed. Caller holds the lock."""
        value = _sensitivity(value)
        if value is None or value == device.sensitivity:
            return
        device.sensitivity = value
        if emit:
            self.emit("settings", self._settings_event(device.device_id))

    def _settings_event(self, device_id):
        device = self.devices.get(device_id)
        return {
            "device_id": device_id,
            "sensitivity": device.sensitivity if device else None,
            "settings": self._wanted(device_id),
        }

    def _wanted(self, device_id):
        wanted = self.settings.get(device_id)
        return dict(wanted) if wanted else None

    def _reply_settings(self, reply, device_id):
        """Add the dashboard's settings to a reply to message 2 or 3, once set."""
        wanted = self._wanted(device_id)
        if wanted:
            reply["settings"] = wanted
        return reply

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
            library_entry = model.get("library_entry")
            if isinstance(library_entry, str):
                for alarm in self.alarms.values():
                    if alarm["model_version"] == model["model_version"] and alarm["library_entry"] is None:
                        alarm["library_entry"] = library_entry
            self.emit("model", model)
            self._note_sensitivity(device, model.get("sensitivity"))

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
                self._note_sensitivity(device, record.get("sensitivity"))
                self._track_alarm(record)
            reply = {"ok": True, "stored": len(batch), "need_model": need_model}
            return self._reply_settings(reply, batch[-1]["device_id"]) if batch else reply

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
            self._note_sensitivity(device, body.get("sensitivity"))
            return self._reply_settings({"ok": True}, device_id)

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

    # ---- section 6: alarms ------------------------------------------------

    def _track_alarm(self, record, emit=True):
        """Open, grow or close the machine's alarm with one record, taken in
        `seq` order. Caller holds the lock."""
        machine_id = record["machine_id"]
        alarm = self.open_alarms.get(machine_id)
        minutes = _number(record.get("abnormal_minutes"))
        if record["diagnosis"] in NOT_ABNORMAL:
            if alarm is not None:
                self._close_alarm(alarm, emit)
            return
        if alarm is not None:
            last = self.last_minutes.get(machine_id)
            restarted = minutes is not None and last is not None and minutes < last
            if restarted or alarm["device_id"] != record["device_id"] or alarm["model_version"] != record["model_version"]:
                self._close_alarm(alarm, emit)
                alarm = None
        if minutes is not None:
            self.last_minutes[machine_id] = minutes
        score = _number(record.get("unknown_score"))
        if alarm is None:
            alarm = {
                "alarm_id": f"alarm-{record['seq']}",
                "machine_id": machine_id,
                "device_id": record["device_id"],
                "model_version": record["model_version"],
                "library_entry": self._library_entry(record["model_version"]),
                "start": record["at"],
                "end": record["at"],
                "open": True,
                "diagnosis": record["diagnosis"],
                "periods": minutes,
                "peak_unknown_score": score,
                "sensitivity": _sensitivity(record.get("sensitivity")),
                "alarm_line": _number(record.get("alarm_line")),
                "simulated": record.get("simulated") is True,
                "answer": None,
                "answers": [],
            }
            self.alarms[alarm["alarm_id"]] = alarm
            self.alarm_seq[alarm["alarm_id"]] = record["seq"]
            self.machine_alarms.setdefault(machine_id, []).append(alarm)
            self.open_alarms[machine_id] = alarm
            if emit:
                self.emit("alarm", _alarm_copy(alarm))
            return
        changed = record["diagnosis"] != alarm["diagnosis"]
        alarm["diagnosis"] = record["diagnosis"]
        alarm["end"] = record["at"]
        if minutes is not None and (alarm["periods"] is None or minutes > alarm["periods"]):
            alarm["periods"] = minutes
            changed = True
        if score is not None and (alarm["peak_unknown_score"] is None or score > alarm["peak_unknown_score"]):
            alarm["peak_unknown_score"] = score
        if record.get("simulated") is True:
            alarm["simulated"] = True
        if changed and emit:
            self.emit("alarm", _alarm_copy(alarm))

    def _close_alarm(self, alarm, emit):
        alarm["open"] = False
        self.open_alarms.pop(alarm["machine_id"], None)
        self.last_minutes.pop(alarm["machine_id"], None)
        if emit:
            self.emit("alarm", _alarm_copy(alarm))

    def _library_entry(self, model_version):
        entry = self.models.get(model_version, {}).get("library_entry")
        return entry if isinstance(entry, str) else None

    def _newest_first(self, alarms):
        return sorted(alarms, key=lambda a: (_parse_time(a["start"]), self.alarm_seq[a["alarm_id"]]), reverse=True)

    def machine_alarm_list(self, machine_id, limit):
        with self.lock:
            alarms = self._newest_first(self.machine_alarms.get(machine_id, []))
            return [_alarm_copy(a) for a in alarms[:limit]]

    def answer_alarm(self, alarm_id, body):
        """Add a person's answer to an alarm. Returns the alarm with it."""
        if not isinstance(body, dict):
            raise BadRequest("answer must be a JSON object")
        if body.get("verdict") not in VERDICTS:
            raise BadRequest("answer 'verdict' must be 'real' or 'false'")
        by = body.get("by")
        if not isinstance(by, str) or not by.strip():
            raise BadRequest("answer is missing required field 'by' (the name the person gave)")
        if "at" in body and body["at"] is not None and not isinstance(body["at"], str):
            raise BadRequest("answer 'at' must be an ISO time string")
        if "finding" in body and body["finding"] is not None and not isinstance(body["finding"], str):
            raise BadRequest("answer 'finding' must be text")
        with self.lock:
            alarm = self.alarms.get(alarm_id)
            if alarm is None:
                raise BadRequest(f"no such alarm: {alarm_id}")
            answer = dict(body)
            if not answer.get("at"):
                answer["at"] = utc_now()
            answer["alarm_id"] = alarm_id
            answer["machine_id"] = alarm["machine_id"]
            answer["model_version"] = alarm["model_version"]
            answer["library_entry"] = alarm["library_entry"]
            answer["received_at"] = utc_now()
            self.answers_file.append([answer])
            alarm["answers"].append(answer)
            alarm["answer"] = answer
            copy = _alarm_copy(alarm)
            self.emit("alarm", copy)
            return copy

    # ---- section 7: settings ----------------------------------------------

    def put_settings(self, device_id, body):
        """Set or clear the sensitivity the dashboard wants. Returns the settings, or None once cleared."""
        if not isinstance(body, dict) or "sensitivity" not in body:
            raise BadRequest("settings must be a JSON object with 'sensitivity' (0 to 1, or null to clear)")
        value = body["sensitivity"]
        if value is not None and _sensitivity(value) is None:
            raise BadRequest("'sensitivity' must be a number from 0 to 1, or null to clear")
        with self.lock:
            self.settings_file.append([{"device_id": device_id, "sensitivity": value, "at": utc_now()}])
            self._device(device_id)
            if value is None:
                self.settings.pop(device_id, None)
            else:
                self.settings[device_id] = {"sensitivity": value}
            self.emit("settings", self._settings_event(device_id))
            return self._wanted(device_id)

    # ---- section 5: reads -------------------------------------------------

    def machine_records(self, machine_id, limit, spectrum, frames=False, since=None, after_seq=None):
        """The newest `limit` records, oldest first. With `since` (a UTC timestamp)
        or `after_seq`, the oldest `limit` records whose `at` is at or after `since`
        and whose `seq` is above `after_seq` instead, so a range reads page by page."""
        with self.lock:
            history = self.by_machine.get(machine_id, [])  # in seq order
            if limit <= 0:
                chosen = []
            elif since is None and after_seq is None:
                chosen = history[-limit:]
            else:
                start = 0 if after_seq is None else bisect.bisect_right(history, after_seq, key=lambda r: r["seq"])
                chosen = []
                for record in history[start:]:
                    if since is None or _parse_time(record.get("at")) >= since:
                        chosen.append(record)
                        if len(chosen) == limit:
                            break
        return [_slim(r, spectrum, frames) for r in chosen]

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
                    "sensitivity": device.sensitivity,
                    "settings": self._wanted(device.device_id),
                    "latest": device.latest,
                }
            alarms = {}
            for history in self.machine_alarms.values():
                newest = self._newest_first(history)
                for alarm in newest[:ALARMS_PER_MACHINE] + [a for a in newest[ALARMS_PER_MACHINE:] if a["open"]]:
                    alarms[alarm["alarm_id"]] = _alarm_copy(alarm)
            return {
                "ok": True,
                "now": utc_now(),
                "devices": devices,
                "models": dict(self.models),
                "machines": sorted(self.by_machine),
                "alarms": alarms,
            }


def _alarm_copy(alarm):
    """A copy safe to hand out of the lock: the answers list keeps growing."""
    return dict(alarm, answers=list(alarm["answers"]))
