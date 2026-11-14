"""Parts, log entries and part states (platform/CONTRACT.md sections 8.5 to 8.7).

The receiver's Store makes one of these, as `store.partlog`, after it has loaded its
own files, so `store.alarms` and `store.by_machine` are already there to read.
The route functions in log_api.py reach it as `request.server.store.partlog`.

Two append-only files in the data dir: `parts.jsonl` (the newest line for a
`part_id` wins) and `log_entries.jsonl`. A part's state is never stored. It is
worked out at start and again whenever an entry is added, an alarm changes or
a record arrives (platform/partlog/part_state.py), and a `part` event goes out
only when it comes out different: a record a second is not an event a second.

`state_part()` and the three hooks are called with `store.lock` already held:
they may read the Store's fields but must not take the lock or call its public
methods. Everything else here takes the lock.

A hook that fails is logged and skipped, so it can never stop a record or an
answer from being stored. The one exception: `alarm_answered` raises BadRequest
to refuse an answer that names a part it cannot be about.
"""

import bisect
import csv
import io
import os
import re
import sys

from receiver_store import BadRequest, JsonLines, parse_time, utc_now

PARTLOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "partlog")
if PARTLOG_DIR not in sys.path:
    sys.path.insert(0, PARTLOG_DIR)

import part_kinds  # noqa: E402
import part_state  # noqa: E402

PARTS_FILE = "parts.jsonl"
ENTRIES_FILE = "log_entries.jsonl"

ENTRY_KINDS = ("finding", "inspection", "repair", "note", "review")
PART_FIELDS = ("name", "kind", "position", "fault_types", "note", "removed")  # what a person may set
PART_SET_BY_PLATFORM = ("part_id", "machine_id", "created", "changed", "state", "by")
ENTRY_FIELDS = ("kind", "text", "by", "part_id", "at", "needs_repair", "alarm_id", "run_id", "simulated")
ENTRY_SET_BY_PLATFORM = ("entry_id", "machine_id", "received_at", "diagnosis", "source")
CSV_COLUMNS = ("at", "part", "kind", "text", "by", "needs_repair", "alarm_id", "diagnosis", "source", "entry_id")
DEFAULT_LOG_LIMIT = 300
ID_NUMBER = re.compile(r"-(\d+)$")


def _object(body, what):
    if not isinstance(body, dict):
        raise BadRequest(f"{what} must be a JSON object")
    return body


def _by(body, what):
    by = body.get("by")
    if not isinstance(by, str) or not by.strip():
        raise BadRequest(f"{what} is missing required field 'by' (the name the person gave)")
    return by.strip()


def _position(value):
    """A pin: null, or a scan and a point on it (contract section 8.4, Coordinates)."""
    if value is None:
        return None
    at = value.get("at") if isinstance(value, dict) else None
    good = isinstance(at, list) and len(at) == 3 and all(
        isinstance(x, (int, float)) and not isinstance(x, bool) and x == x and abs(x) != float("inf") for x in at
    )
    if not good or not isinstance(value.get("scan_id"), str) or not value["scan_id"]:
        raise BadRequest('part \'position\' must be null or {"scan_id": "...", "at": [x, y, z]}')
    return value


def _part_fields(body):
    """The fields of a part that `body` sets, checked."""
    out = {}
    if "name" in body:
        if not isinstance(body["name"], str) or not body["name"].strip():
            raise BadRequest("part 'name' must be text, what people call the part")
        out["name"] = body["name"].strip()
    if "kind" in body:
        if body["kind"] not in part_kinds.KINDS:
            raise BadRequest(f"part 'kind' must be one of {', '.join(part_kinds.KINDS)}")
        out["kind"] = body["kind"]
    if "fault_types" in body:
        names = body["fault_types"]
        if not isinstance(names, list) or not all(isinstance(n, str) and n for n in names):
            raise BadRequest("part 'fault_types' must be a list of diagnosis names")
        out["fault_types"] = list(dict.fromkeys(names))  # each once, in the order given
    if "position" in body:
        out["position"] = _position(body["position"])
    if "note" in body:
        if not isinstance(body["note"], str):
            raise BadRequest("part 'note' must be text")
        out["note"] = body["note"]
    if "removed" in body:
        if not isinstance(body["removed"], bool):
            raise BadRequest("part 'removed' must be true or false")
        out["removed"] = body["removed"]
    # Unknown fields are stored and passed on (contract, Basics).
    out.update((k, v) for k, v in body.items() if k not in PART_FIELDS and k not in PART_SET_BY_PLATFORM)
    return out


def _entry_fields(body):
    """What a POST to the log says, checked as far as it can be without the store."""
    kind = body.get("kind")
    if kind not in ENTRY_KINDS:
        raise BadRequest(f"entry 'kind' must be one of {', '.join(ENTRY_KINDS)}")
    verdict = kind in part_state.VERDICT_KINDS
    out = {"kind": kind, "by": _by(body, "entry")}
    text = "" if body.get("text") is None and verdict else body.get("text")
    if not isinstance(text, str) or not (text.strip() or verdict):
        raise BadRequest("entry is missing required field 'text' (what happened): only a finding or an inspection may leave it empty")
    out["text"] = text
    needs_repair = body.get("needs_repair")
    if verdict and not isinstance(needs_repair, bool):
        raise BadRequest("entry 'needs_repair' is required for a finding or an inspection: true or false")
    if not verdict and needs_repair is not None:
        raise BadRequest(f"'needs_repair' is only for a finding or an inspection, not a {kind}")
    out["needs_repair"] = needs_repair
    at = body.get("at")
    if at is not None and parse_time(at) is None:
        raise BadRequest("entry 'at' must be an ISO 8601 time")
    out["at"] = at
    for name in ("part_id", "alarm_id", "run_id"):
        value = body.get(name)
        if value is not None and (not isinstance(value, str) or not value):
            raise BadRequest(f"entry '{name}' must be an id or null")
        out[name] = value
    if not isinstance(body.get("simulated", False), bool):
        raise BadRequest("entry 'simulated' must be true or false")
    out["simulated"] = body.get("simulated", False)
    # `source` is the platform's to set, except for a line brought in from an old spreadsheet.
    out["source"] = "import" if body.get("source") == "import" else "agent" if kind == "review" else "person"
    out.update((k, v) for k, v in body.items() if k not in ENTRY_FIELDS and k not in ENTRY_SET_BY_PLATFORM)
    return out


def _next_number(ids):
    """One more than the highest number at the end of these ids."""
    numbers = [int(m.group(1)) for m in map(ID_NUMBER.search, ids) if m]
    return max(numbers, default=0) + 1


class LogStore:
    def __init__(self, store):
        self.store = store
        self.parts_file = JsonLines(os.path.join(store.data_dir, PARTS_FILE))
        self.entries_file = JsonLines(os.path.join(store.data_dir, ENTRIES_FILE))
        self.parts = {}  # part_id -> the part as stored, without its state
        self.machine_parts = {}  # machine_id -> part ids, in the order they were made
        self.states = {}  # part_id -> its state, worked out
        self.machine_entries = {}  # machine_id -> its entries, in the order they were added
        self.part_entries = {}  # part_id -> its entries, likewise
        self.newness = {}  # entry_id -> (timestamp of `at`, order added), for newest first
        self.record_times = {}  # machine_id -> (timestamp, `at`) of each record, sorted
        if part_kinds.PROBLEM:
            print(f"warning: part kind table: {part_kinds.PROBLEM}", file=sys.stderr)
        for part in self.parts_file.load():
            part_id, machine_id = part.get("part_id"), part.get("machine_id")
            if not isinstance(part_id, str) or not isinstance(machine_id, str):
                continue
            if part_id not in self.parts:
                self.machine_parts.setdefault(machine_id, []).append(part_id)
            self.parts[part_id] = part
        for entry in self.entries_file.load():
            if isinstance(entry.get("entry_id"), str) and isinstance(entry.get("machine_id"), str):
                self._index_entry(entry)
        self.next_part = _next_number(self.parts)
        self.next_entry = _next_number(self.newness)
        for machine_id, records in store.by_machine.items():
            times = ((parse_time(r.get("at")), r.get("at")) for r in records)
            self.record_times[machine_id] = sorted(t for t in times if t[0] is not None)
        for machine_id in self.machine_parts:
            self._refresh(machine_id, emit=False)

    def close(self):
        """The receiver is stopping. Every change is already on disk."""

    # ---- inside the lock ----------------------------------------------------

    def _index_entry(self, entry):
        self.newness[entry["entry_id"]] = (part_state.timestamp(entry.get("at")), len(self.newness))
        self.machine_entries.setdefault(entry["machine_id"], []).append(entry)
        if entry.get("part_id") is not None:
            self.part_entries.setdefault(entry["part_id"], []).append(entry)

    def _first_record_after(self, machine_id, timestamp):
        times = self.record_times.get(machine_id, ())
        i = bisect.bisect_right(times, timestamp, key=lambda t: t[0])
        return times[i][1] if i < len(times) else None

    def _work_out(self, part):
        machine_id = part["machine_id"]
        return part_state.work_out(
            part,
            self.part_entries.get(part["part_id"], ()),
            self.store.machine_alarms.get(machine_id, ()),
            lambda timestamp: self._first_record_after(machine_id, timestamp),
        )

    def _view(self, part):
        """The part as the contract shows it: with its state."""
        return dict(part, state=dict(self.states[part["part_id"]]))

    def _refresh(self, machine_id, emit=True):
        """Work out the state of every part of a machine again, and send a `part`
        event for each one that changed. A removed part is hidden, so it sends none."""
        for part_id in self.machine_parts.get(machine_id, ()):
            part = self.parts[part_id]
            state = self._work_out(part)
            if state != self.states.get(part_id):
                self.states[part_id] = state
                if emit and not part.get("removed"):
                    self.store.emit("part", self._view(part))

    def _store_part(self, part):
        """Keep a new or changed part, and send its `part` event with the state it has now."""
        self.parts_file.append([part])
        self.parts[part["part_id"]] = part
        self.states[part["part_id"]] = self._work_out(part)
        view = self._view(part)
        self.store.emit("part", view)
        return view

    def _part_on(self, machine_id, part_id):
        part = self.parts.get(part_id)
        if part is None or part["machine_id"] != machine_id:
            raise BadRequest(f"no such part on {machine_id}: {part_id}")
        return part

    def _add_entry(self, machine_id, fields):
        """Number, store and announce an entry, then let it move its part's state."""
        now = utc_now()
        entry = {"entry_id": f"entry-{self.next_entry:06d}", "machine_id": machine_id, "part_id": fields["part_id"],
                 "at": fields["at"] or now, "received_at": now}
        entry.update((k, v) for k, v in fields.items() if k not in entry)
        self.next_entry += 1
        self.entries_file.append([entry])
        self._index_entry(entry)
        self.store.emit("log_entry", entry)
        self._refresh(machine_id)
        return entry

    def _chosen(self, machine_id, part, kind, since, limit):
        """A machine's entries that pass the filters, newest `at` first."""
        entries = self.part_entries.get(part, ()) if part is not None else self.machine_entries.get(machine_id, ())
        chosen = [
            e for e in entries
            if e["machine_id"] == machine_id
            and (kind is None or e.get("kind") == kind)
            and (since is None or self.newness[e["entry_id"]][0] >= since)
        ]
        chosen.sort(key=lambda e: self.newness[e["entry_id"]], reverse=True)
        return chosen[:limit]

    # ---- parts (section 8.5) ------------------------------------------------

    def create_part(self, machine_id, body):
        body = _object(body, "part")
        by = _by(body, "part")
        for name in ("name", "kind"):
            if body.get(name) is None:
                raise BadRequest(f"part is missing required field '{name}'")
        if body.get("fault_types") is None:  # left out: the kind's starting values, unverified
            body = dict(body, fault_types=part_kinds.fault_types_for(body["kind"]))
        fields = _part_fields(body)
        with self.store.lock:
            part = {"part_id": f"part-{self.next_part:04d}", "machine_id": machine_id, "name": "", "kind": "",
                    "fault_types": [], "position": None, "note": "", "created": {"at": utc_now(), "by": by},
                    "removed": False}
            part.update(fields)
            self.next_part += 1
            self.machine_parts.setdefault(machine_id, []).append(part["part_id"])
            return self._store_part(part)

    def update_part(self, part_id, body):
        body = _object(body, "part")
        by = _by(body, "part")
        fields = _part_fields(body)
        if not fields:
            raise BadRequest(f"nothing to change: send any of {', '.join(PART_FIELDS)}")
        with self.store.lock:
            part = self.parts.get(part_id)
            if part is None:
                raise BadRequest(f"no such part: {part_id}")
            changed = dict(part, **fields)
            if changed == part:
                return self._view(part)
            changed["changed"] = {"at": utc_now(), "by": by}  # an addition to the contract: who last changed it
            return self._store_part(changed)

    def machine_part_list(self, machine_id, removed=False):
        with self.store.lock:
            parts = (self.parts[i] for i in self.machine_parts.get(machine_id, ()))
            return [self._view(p) for p in parts if removed or not p.get("removed")]

    def state_part(self):
        """This store's keys of GET /api/state: `parts`, and under `machines`
        the machines that have a part, which the Store adds to its own list."""
        parts = {}
        for machine_id, ids in self.machine_parts.items():
            shown = [self._view(self.parts[i]) for i in ids if not self.parts[i].get("removed")]
            if shown:
                parts[machine_id] = shown
        return {"parts": parts, "machines": list(parts)}

    # ---- entries (section 8.6) ----------------------------------------------

    def add_entry(self, machine_id, body):
        """Add an entry. Returns it, and its part with the state it has now (or None)."""
        fields = _entry_fields(_object(body, "entry"))
        with self.store.lock:
            part_id = fields["part_id"]
            if part_id is not None:
                self._part_on(machine_id, part_id)
            fields["diagnosis"] = None
            if fields["alarm_id"] is not None:
                alarm = self.store.alarms.get(fields["alarm_id"])
                if alarm is None or alarm["machine_id"] != machine_id:
                    raise BadRequest(f"no such alarm on {machine_id}: {fields['alarm_id']}")
                fields["diagnosis"] = alarm["diagnosis"]
                fields["simulated"] = fields["simulated"] or alarm["simulated"]
            entry = self._add_entry(machine_id, fields)
            return entry, self._view(self.parts[part_id]) if part_id is not None else None

    def machine_entries_list(self, machine_id, part=None, kind=None, since=None, limit=DEFAULT_LOG_LIMIT):
        with self.store.lock:
            return self._chosen(machine_id, part, kind, since, limit)

    def machine_csv(self, machine_id, part=None, kind=None, since=None, limit=DEFAULT_LOG_LIMIT):
        """The same entries as a CSV file's bytes. UTF-8 with a byte order mark,
        which is what makes a spreadsheet read accents right (unverified in Excel:
        checked with Python's csv reader only)."""
        with self.store.lock:
            entries = self._chosen(machine_id, part, kind, since, limit)
            names = {part_id: p.get("name", "") for part_id, p in self.parts.items()}
        text = io.StringIO(newline="")
        out = csv.writer(text)  # quotes commas, quotes and line breaks; lines end \r\n
        out.writerow(CSV_COLUMNS)
        for entry in entries:
            row = dict(entry, part=names.get(entry.get("part_id"), ""))
            row["needs_repair"] = {True: "yes", False: "no"}.get(entry.get("needs_repair"), "")
            out.writerow(["" if row.get(c) is None else row[c] for c in CSV_COLUMNS])
        return ("﻿" + text.getvalue()).encode("utf-8")

    # ---- hooks, called by the Store with its lock held ---------------------

    def record_stored(self, record):
        """A record was stored (message 2). Not called for records read at start.

        A record can only move a part from `not_checked` to `nothing_found`, or,
        arriving late with an older `at`, move where `nothing_found` starts. So
        nothing is worked out for the usual record, one a second."""
        timestamp = parse_time(record.get("at"))
        if timestamp is None:
            return
        machine_id = record["machine_id"]
        bisect.insort(self.record_times.setdefault(machine_id, []), (timestamp, record["at"]))
        for part_id in self.machine_parts.get(machine_id, ()):
            state = self.states[part_id]
            if state["name"] == part_state.NOT_CHECKED or (
                state["name"] == part_state.NOTHING_FOUND and timestamp < part_state.timestamp(state["since"])
            ):
                self._refresh(machine_id)
                return

    def alarm_changed(self, alarm):
        """An alarm opened, changed, closed or got an answer: every time the
        Store sends an `alarm` event. `alarm` is the Store's own, not a copy."""
        self._refresh(alarm["machine_id"])

    def alarm_answered(self, alarm, answer):
        """An answer is about to be stored on `alarm`, which does not carry it
        yet. One that names a part becomes a `finding` entry for that part.
        Raises BadRequest to refuse the answer. `alarm_changed` follows once it is stored."""
        part_id = answer.get("part_id")
        if part_id is None:
            return
        if not isinstance(part_id, str):
            raise BadRequest("answer 'part_id' must be a part's id")
        self._part_on(alarm["machine_id"], part_id)
        needs_repair = answer.get("needs_repair")
        if needs_repair is not None and not isinstance(needs_repair, bool):
            raise BadRequest("answer 'needs_repair' must be true or false")
        if answer["verdict"] == "false":
            needs_repair = False  # a false alarm never leaves a part needing repair
        elif needs_repair is None:
            needs_repair = True
        self._add_entry(alarm["machine_id"], {
            "part_id": part_id,
            # The Store does not check an answer's `at`: fall back on when it arrived.
            "at": answer["at"] if parse_time(answer["at"]) is not None else answer["received_at"],
            "kind": "finding",
            "text": answer.get("finding") or "",
            "by": answer["by"],
            "needs_repair": needs_repair,
            "alarm_id": alarm["alarm_id"],
            "diagnosis": alarm["diagnosis"],
            "source": "alarm_answer",
            "run_id": None,
            "simulated": alarm["simulated"],
        })
