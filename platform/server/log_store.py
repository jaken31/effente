"""Parts, log entries and part states (platform/CONTRACT.md sections 8.5 to 8.7).

The receiver's Store makes one of these, as `store.partlog`, after it has loaded its
own files, so `store.alarms` and `store.by_machine` are already there to read.
The route functions in log_api.py reach it as `request.server.store.partlog`.

Two append-only files in the data dir: `parts.jsonl` (the newest line for a
`part_id` wins) and `log_entries.jsonl`. A part's state is never stored. It is
worked out at start and again whenever an entry is added, an alarm changes or
a record arrives (platform/partlog/part_state.py), and a `part` event goes out
only when it comes out different: a record a second is not an event a second.

`state_part()` and the hooks are called with `store.lock` already held:
they may read the Store's fields but must not take the lock or call its public
methods. Everything else here takes the lock.

A hook that fails is logged and skipped, so it can never stop a record or an
answer from being stored. The exceptions: `alarm_answered` raises BadRequest
to refuse an answer that names a part it cannot be about, and `stamp_record`
may raise it to refuse a batch of records.

The probe's part (contract 9.1) is kept in a third append-only file,
`probe.jsonl`: one line each time a pick is set or cleared, the newest line for
a machine wins. Its 5 minute rule runs on the wall clock (`self.clock`), not a
monotonic one, so it still holds across a restart.

Readings (contract 9.3) are never stored: platform/partlog/readings.py works
them out from each part's stamped records, kept here sorted by `at`. Each part's
newest reading is kept worked out, for the part's `reading`. The `reading` event
goes out when a reading is first made, when it closes, and while it grows at
most every 10 seconds, the rest being sent by `tick` once those are up.
"""

import bisect
import csv
import io
import os
import re
import sys
import time

from receiver_store import BadRequest, JsonLines, parse_time, utc_now

PARTLOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "partlog")
if PARTLOG_DIR not in sys.path:
    sys.path.insert(0, PARTLOG_DIR)

import part_kinds  # noqa: E402
import part_state  # noqa: E402
import projection  # noqa: E402
import readings  # noqa: E402

PARTS_FILE = "parts.jsonl"
ENTRIES_FILE = "log_entries.jsonl"
PROBE_FILE = "probe.jsonl"

# Contract 9.1 and 9.6. The times are starting values, unverified.
CATCH_UP_S = 60  # a record measured this long before a pick is still stamped (clock difference)
PROBE_QUIET_S = 5 * 60  # a pick clears itself after this long with no record from its machine
PROBE_QUIET_REASON = "no records for 5 minutes"
SAMPLE_MACHINE = "sample-machine"  # the only machine simulated history is written to
HISTORY_DEVICE = "sim-history"  # the device the history command sends as
READING_EVENT_EVERY_S = 10  # a growing reading sends its `reading` event at most this often (9.3)

ENTRY_KINDS = ("finding", "inspection", "repair", "note", "review")
PART_FIELDS = ("name", "kind", "position", "fault_types", "note", "removed")  # what a person may set
PART_SET_BY_PLATFORM = ("part_id", "machine_id", "created", "changed", "state", "reading", "by")
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


def _iso(timestamp):
    """A wall-clock timestamp as ISO 8601 UTC with milliseconds, as `utc_now` writes it."""
    whole = time.gmtime(timestamp)
    return time.strftime("%Y-%m-%dT%H:%M:%S.", whole) + f"{int(timestamp * 1000) % 1000:03d}Z"


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
        self.clock = time.time  # wall clock for the probe's 5 minute rule; tests replace it
        self.probe_file = JsonLines(os.path.join(store.data_dir, PROBE_FILE))
        self.probes = {}  # machine_id -> its selection (contract 9.1)
        self.heard = {}  # machine_id -> wall-clock time its newest record was stored
        self.part_records = {}  # part_id -> its stamped records, sorted by readings.key
        self.newest = {}  # part_id -> its newest reading, worked out
        self.sent = {}  # part_id -> {"reading", "at", "pending"}: the last `reading` event and when
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
        for line in self.probe_file.load():
            machine_id = line.get("machine_id")
            if isinstance(machine_id, str):
                probe = line.get("probe")
                if isinstance(probe, dict) and parse_time(probe.get("since")) is not None:
                    self.probes[machine_id] = probe
                else:
                    self.probes.pop(machine_id, None)
        for machine_id, records in store.by_machine.items():
            heard = [parse_time(r.get("received_at")) for r in records[-1:]]
            if heard and heard[0] is not None:
                self.heard[machine_id] = heard[0]
        self._expire_probes()
        for record in store.records:
            if isinstance(record.get("part_id"), str) and readings.key(record)[0] is not None:
                self.part_records.setdefault(record["part_id"], []).append(record)
        now = self.clock()
        for part_id, records in self.part_records.items():
            records.sort(key=readings.key)
            newest = self.newest[part_id] = readings.newest_reading(records, self._alarm_of, now)
            if newest is not None and newest["open"]:  # so its closing is still announced
                self.sent[part_id] = {"reading": newest, "at": now, "pending": None}

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
        """The part as the contract shows it: with its state and its newest reading."""
        return dict(part, state=dict(self.states[part["part_id"]]),
                    reading=readings.short(self.newest.get(part["part_id"])))

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
        probe = {m: dict(p) for m, p in self.probes.items()}
        return {"parts": parts, "machines": list(parts), "probe": probe}

    # ---- the probe's part (section 9.1) -------------------------------------

    def set_probe(self, machine_id, body):
        """Set or clear which part the probe is on. Returns the selection, or None."""
        body = _object(body, "probe")
        by = _by(body, "probe")
        if "part_id" not in body:
            raise BadRequest("probe is missing required field 'part_id' (a part's id, or null to clear)")
        part_id = body["part_id"]
        if part_id is not None and (not isinstance(part_id, str) or not part_id):
            raise BadRequest("probe 'part_id' must be a part's id or null")
        with self.store.lock:
            if part_id is None:
                if machine_id in self.probes:
                    self._put_probe(machine_id, None, "")
                return None
            if self._part_on(machine_id, part_id).get("removed"):
                raise BadRequest(f"part {part_id} is removed: the probe cannot be on it")
            probe = {"machine_id": machine_id, "part_id": part_id, "since": _iso(self.clock()), "by": by}
            self._put_probe(machine_id, probe, "")
            return dict(probe)

    def _put_probe(self, machine_id, probe, reason):
        """Keep a selection, or its clearing, and send the `probe` event. Inside the lock."""
        self.probe_file.append([{"machine_id": machine_id, "probe": probe, "at": _iso(self.clock()), "reason": reason}])
        if probe is None:
            self.probes.pop(machine_id, None)
        else:
            self.probes[machine_id] = probe
        self.store.emit("probe", {"machine_id": machine_id, "probe": dict(probe) if probe else None, "reason": reason})

    def _expire_probes(self):
        """Clear each pick whose machine has stored no record for 5 minutes,
        counted from `since` if none came since then. Inside the lock."""
        now = self.clock()
        for machine_id, probe in list(self.probes.items()):
            last = max(parse_time(probe["since"]), self.heard.get(machine_id, float("-inf")))
            if now - last > PROBE_QUIET_S:
                self._put_probe(machine_id, None, PROBE_QUIET_REASON)

    # ---- readings (section 9.3) ---------------------------------------------

    def part_readings(self, part_id, since=None, limit=300):
        """The readings route's reply (section 9.3). `projection` (9.5) is worked
        out from every reading and the newest repair, whatever `since` and `limit` say."""
        with self.store.lock:
            part = self.parts.get(part_id)
            if part is None:
                raise BadRequest(f"no such part: {part_id}")
            now = self.clock()
            every = readings.readings(self.part_records.get(part_id, []), self._alarm_of, now)
            repairs = [e["at"] for e in self.part_entries.get(part_id, ()) if e.get("kind") == "repair" and e.get("at")]
        last_repair = max(repairs, key=parse_time, default=None)
        shown = readings.chosen(every, since, limit)
        return {"part_id": part_id, "machine_id": part["machine_id"], "readings": shown,
                "summary": readings.summary(shown),
                "projection": projection.project(every, last_repair, now=_iso(now))}

    def _alarm_of(self, record):
        """The alarm an abnormal stored record belongs to: its machine's newest
        alarm opened at or before it, since every abnormal record opens or grows one."""
        alarms = self.store.machine_alarms.get(record["machine_id"], ())
        opened = [self.store.alarm_seq[a["alarm_id"]] for a in alarms]
        i = bisect.bisect_right(opened, record.get("seq", 0))
        return alarms[i - 1]["alarm_id"] if i else None

    def _reading_moved(self, part_id, record):
        """A stamped record was stored: work out its visit's reading again, keep
        the part's newest, and send the `reading` event if it is due."""
        records = self.part_records[part_id]
        now = self.clock()
        lo, hi = readings.visit_around(records, bisect.bisect_left(records, readings.key(record), key=readings.key))
        made = readings.reading(records[lo:hi], self._alarm_of, now)
        if hi == len(records):  # it is in the newest visit; an older one cannot change the newest reading
            self.newest[part_id] = made or readings.newest_reading(records, self._alarm_of, now)
        if made is None:
            return
        sent = self.sent.get(part_id)
        if sent is None or sent["reading"]["start"] != made["start"] or now - sent["at"] >= READING_EVENT_EVERY_S:
            self._send_reading(made, now)
        elif made != sent["reading"]:
            sent["pending"] = made

    def _send_reading(self, made, now):
        self.sent[made["part_id"]] = {"reading": made, "at": now, "pending": None}
        self.store.emit("reading", {"part_id": made["part_id"], "machine_id": made["machine_id"], "reading": made})

    def _tick_readings(self):
        """Send what the 10 seconds held back, and each reading that has closed."""
        now = self.clock()
        for part_id, sent in list(self.sent.items()):
            if sent["pending"] is not None and now - sent["at"] >= READING_EVENT_EVERY_S:
                self._send_reading(sent["pending"], now)
            elif sent["reading"]["open"] and now - readings.timestamp(sent["reading"]["end"]) >= readings.OPEN_FOR_S:
                records = self.part_records.get(part_id, [])
                made = readings.reading_starting(records, sent["reading"]["start"], self._alarm_of, now)
                if made is None:  # cannot happen: a visit only grows. Stop watching it.
                    del self.sent[part_id]
                    continue
                if made["open"]:
                    continue
                newest = self.newest.get(part_id)
                if newest is not None and newest["start"] == made["start"]:
                    self.newest[part_id] = made
                self._send_reading(made, now)

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

    def stamp_record(self, record):
        """A record is about to be stored (message 2): checked, but with no `seq`
        or `received_at` yet, and `simulated` may be missing. Called for each
        record of a batch before any of them is stored. May add `part_id` to it
        (contract 9.1). Raises BadRequest to refuse the whole batch: nothing of it
        is stored (9.1 Already stamped, 9.6)."""
        machine_id = record["machine_id"]
        if machine_id == SAMPLE_MACHINE and record.get("simulated") is not True:
            raise BadRequest(f'a record for {SAMPLE_MACHINE} must carry "simulated": true')
        if record["device_id"] == HISTORY_DEVICE and machine_id != SAMPLE_MACHINE:
            raise BadRequest(f"device {HISTORY_DEVICE} may only send records for {SAMPLE_MACHINE}")
        if record.get("part_id") is not None:  # already stamped: by the simulator or an import
            part_id = record["part_id"]
            if not isinstance(part_id, str):
                raise BadRequest("record 'part_id' must be a part's id")
            self._part_on(machine_id, part_id)
            return
        record.pop("part_id", None)  # an explicit null is the same as none
        self._expire_probes()
        probe = self.probes.get(machine_id)
        at = parse_time(record.get("at"))
        if probe is not None and at is not None and at >= parse_time(probe["since"]) - CATCH_UP_S:
            record["part_id"] = probe["part_id"]

    def tick(self):
        """Called about four times a second, with records arriving or not, so a
        pick can clear itself (9.1) and a reading can close (9.3)."""
        self._expire_probes()
        self._tick_readings()

    def record_stored(self, record):
        """A record was stored (message 2). Not called for records read at start.

        A record can only move a part from `not_checked` to `nothing_found`, or,
        arriving late with an older `at`, move where `nothing_found` starts. So
        nothing is worked out for the usual record, one a second."""
        machine_id = record["machine_id"]
        self.heard[machine_id] = self.clock()
        timestamp = parse_time(record.get("at"))
        if timestamp is None:
            return
        part_id = record.get("part_id")
        if isinstance(part_id, str):
            bisect.insort(self.part_records.setdefault(part_id, []), record, key=readings.key)
            self._reading_moved(part_id, record)
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
