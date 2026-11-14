# Scan and log: work plan for three people

Status: proposal, written Sat 14 Nov 2026. Not yet agreed by the team. This file is instructions only. It adds no code.

Read this whole file before you start. Then read only your own brief again (sections 8, 9 or 10).

## 1. What we are adding

Today effente catches a fault while the probe is on the machine. That is the short-term fix. This plan adds the long-term one: a record of every machine and every part, kept on the customer's own laptop, that replaces the maintenance spreadsheet.

Three pieces:

1. **Phone scan.** A minimal iPhone app makes a 3D scan of a machine when it arrives and sends it to the laptop.
2. **Machine page.** The desktop app shows the scan. A person clicks the model to mark each part with a pin.
3. **Part log.** Every part has its own history: what was found, when it started, what was repaired. One agent run per part reads that history and says whether the part needs repair.

The pitch line: the probe finds the problem today, the log remembers it for the life of the machine.

```
 iPhone (Rohan)                 Laptop (Cameron, Jake)                      Handheld probe
 +----------------+   WiFi     +----------------------------------------+   (already works)
 | guided scan    |  hotspot   | receiver, port 8000                    |        |
 | build model    | ---------> |   scan store   (Cameron)               | <------+ records,
 | upload .usdz   |            |   convert .usdz to .glb (Cameron)      |          alarms
 +----------------+            |   part and log store (Jake)            |
                               |                                        |
                               | desktop app                            |
                               |   Machine page: 3D model, pins (Cameron)|
                               |   Log page: table, entries, CSV (Jake) |
                               |   part review agent, local model (Jake)|
                               +----------------------------------------+
 Nothing leaves the site. The phone talks only to the laptop.
```

## 2. Who does what

| Person | Owns | Folders | Branch prefix |
|--------|------|---------|---------------|
| Rohan | The iPhone scan app | `phone/` (new) | `track6/` |
| Cameron | Scan upload, storage, conversion, the Machine page with the 3D view and pins | `platform/server/scan_store.py`, `platform/server/scan_api.py`, `platform/scan/`, `platform/desktop/app/pages/machine/`, `platform/desktop/app/data/scans.py` | `track5/scan-` |
| Jake | Step 0 (wiring), parts, the log, alarm linking, the Log page, the part review agent | `platform/server/log_store.py`, `platform/server/log_api.py`, `platform/partlog/`, `platform/part_review.py`, `platform/desktop/app/pages/log/`, `platform/desktop/app/data/partlog.py` | `track5/part-` |

Nobody edits a file outside their row after step 0. If you need a change in someone else's file, tell that person. This is the rule in [AGENTS.md](../AGENTS.md), and it is what lets three people and their agents work at once.

`phone/` is a new folder and a new track, 6. Step 0 adds it to the track table in AGENTS.md.

## 3. Rules that apply to everyone

- **The working demo comes first.** The four-case conveyor demo in [PRD.md](PRD.md) must keep running on `master` at every moment. All of this is stretch work on top of it. After every merge, run `python3 -m pytest platform device` and steps 0 to 3 of the demo script in [platform/README.md](../platform/README.md).
- **Additions only.** No existing route, field, page or shortcut changes meaning. An old device and the old web dashboard keep working.
- **Nothing leaves the site** (PRD P9a). The phone app makes requests to the laptop only. No analytics, no crash reporting service, no cloud reconstruction, no web lookups. The scan is built on the phone.
- **The log works with no scan.** Parts and entries must be usable when no scan exists, when conversion failed, and when the 3D view cannot draw. The scan is a way to point at a part, not a requirement.
- **Say only what we know.** The probe does not locate a fault inside a machine. A part is a suspect because a person linked a fault type to it, and it is confirmed only when a person says so. Show unconfirmed links as "possible". The agent's review is advice and never changes a part's state by itself.
- **Git.** One job, one branch, a draft PR at the first commit, rebase on `master` before pushing, small commits, only your own files, never `git add -A`. No agent co-author.
- **No em dash** in code, comments or docs.
- **Mark what you did not check** as unverified, in the doc and in the PR.

## 4. Words

| Word | Meaning |
|------|---------|
| Machine | One piece of equipment. Its `machine_id` is the one the probe already sends, for example `conveyor-07`. |
| Scan | One 3D capture of a machine, made by the phone. A machine can have several. The newest is the one shown. |
| Part | One functional part of a machine that can fail on its own: a motor, a bearing, a roller, a mount. |
| Pin | Where a part sits on a scan. A part can exist with no pin. |
| Entry | One line in the log: a finding, an inspection, a repair, a note, or an agent's review. Entries are only ever added. |
| Part state | What the log says about a part now: `needs_repair`, `possible`, `nothing_found` or `not_checked`. Worked out by the platform from entries and alarms. |
| Review | What the part review agent wrote for one part. Advice only. |

## 5. The interface

This section is the whole agreement between the three of you. Build to it exactly. If it is wrong, say so in the team channel and fix it here first. Step 0 copies it into [platform/CONTRACT.md](../platform/CONTRACT.md) as version 3.4, after which the contract is the source of truth and this section is removed.

Everything in the contract's "Basics" still holds: plain HTTP and JSON on port 8000, no login, ISO 8601 UTC times, HTTP 200 with `"ok": true` on success, HTTP 400 with `{"ok": false, "error": "what was wrong"}` on a bad request, unknown fields stored and passed on.

Ids made by the platform (`scan_id`, `part_id`, `entry_id`) are opaque. They hold only letters, digits and `-`. Never build one yourself and never read meaning out of one.

### 5.1 Hello (Cameron)

`GET /api/hello`

```json
{"ok": true, "service": "effente", "contract": "3.4", "now": "2026-11-14T18:15:02Z", "max_scan_bytes": 268435456}
```

The phone calls this to show "laptop found" before a scan starts. It is small and fast, unlike `/api/state`.

### 5.2 The QR code (Cameron draws it, Rohan reads it)

The desktop app shows a QR code that holds one line of text:

```
effente://scan?v=1&base=http%3A%2F%2F192.168.43.10%3A8000&machine=conveyor-07&name=Conveyor%207
```

| Part | Rule |
|------|------|
| `v` | `1`. The app refuses any other value with "update the app". |
| `base` | The receiver's address, URL-encoded, no trailing slash. The app sends every request to it. |
| `machine` | The `machine_id` the scan is for. Lowercase letters, digits and `-`, 1 to 40 characters. |
| `name` | Optional. What to show the person on the phone. |

The same line can be pasted into the app by hand. That is the fallback when the camera cannot read the code.

### 5.3 Upload a scan (Rohan sends, Cameron receives)

`POST /api/machines/conveyor-07/scans?client_id=6F9619FF-8B86-D011-B42D-00C04FC964FF&at=2026-11-14T18:14:40Z&name=scan.usdz&app=effente-scan%200.1`

The body is the file's raw bytes. Not JSON and not a multipart form: the receiver uses only the Python standard library, which has no multipart parser any more.

| Thing | Rule |
|-------|------|
| `Content-Length` | Required. 1 byte to 268,435,456 bytes (256 MB). Chunked uploads are refused. |
| `Content-Type` | `model/vnd.usdz+zip` for a USDZ file, `model/gltf-binary` for a GLB. The receiver does not trust it: it reads the first four bytes. `PK\x03\x04` is a USDZ (a USDZ is a zip file), `glTF` is a GLB. Anything else is a 400. |
| `client_id` | Required. Made by the phone, one per scan, 8 to 64 letters, digits or `-`. Sending the same `client_id` again stores nothing and returns the first scan with `"duplicate": true`. This makes a retry after a dropped connection safe. |
| `at` | When the scan was captured. Missing means the platform's time. |
| `name`, `app` | Optional. The file name, and the app's name and version, for people. |

Reply:

```json
{"ok": true, "duplicate": false, "scan": {"...": "the scan, as in 5.4"}}
```

The receiver writes the body to disk as it arrives and never holds it in memory. A body that is too large or not a known file type gets a 400 and the connection is closed.

### 5.4 The scan (Cameron)

```json
{
  "scan_id": "scan-20261114181502-3fa9",
  "machine_id": "conveyor-07",
  "client_id": "6F9619FF-8B86-D011-B42D-00C04FC964FF",
  "at": "2026-11-14T18:14:40Z",
  "received_at": "2026-11-14T18:15:02Z",
  "format": "usdz",
  "file": {"name": "scan.usdz", "bytes": 18234112},
  "app": "effente-scan 0.1",
  "model": {
    "status": "ready",
    "reason": "",
    "bytes": 9120344,
    "bounds": {"min": [-0.21, 0.0, -0.09], "max": [0.21, 0.14, 0.09]}
  }
}
```

| Field | Rule |
|-------|------|
| `format` | `usdz` or `glb`, from the file's first bytes. |
| `model.status` | `waiting` (stored, not yet converted), `converting`, `ready`, or `failed`. A GLB upload is `ready` at once. |
| `model.reason` | A plain sentence when `failed`, for example "Blender is not installed on this laptop". Empty otherwise. |
| `model.bytes`, `model.bounds` | Present when `ready`. `bounds` is the smallest box around the model, in the model's own coordinates. |

**Coordinates.** Every position in this plan is in the coordinates of the converted file, `model.glb`: three numbers `[x, y, z]`, right-handed, Y up, in the file's own units. For a scan from Apple's Object Capture that should be metres (unverified). A position belongs to one scan. It means nothing on another scan of the same machine.

Routes:

| Route | Returns |
|-------|---------|
| `GET /api/machines/conveyor-07/scans?limit=20` | `{"ok": true, "scans": [...]}`, newest `received_at` first |
| `GET /api/scans/scan-20261114181502-3fa9` | `{"ok": true, "scan": {...}}` |
| `GET /api/scans/scan-20261114181502-3fa9/model.glb` | The converted file's bytes. A 400 with the status as the error while it is not `ready`. |
| `GET /api/scans/scan-20261114181502-3fa9/file` | The file as the phone sent it |

`GET /api/state` gains `"scans": {"conveyor-07": {"...": "that machine's newest scan"}}`. A machine with a scan appears in `machines` even if no probe has ever reported on it.

The stream gains the event `scan`, with the scan as data, sent when a scan is stored and each time `model.status` changes.

Stored under `platform/server/data/`, which is not committed: `scans.jsonl` (the newest line for a `scan_id` wins), and `scans/<scan_id>/` holding the original file and `model.glb`.

### 5.5 Parts (Jake)

```json
{
  "part_id": "part-0007",
  "machine_id": "conveyor-07",
  "name": "Drive roller",
  "kind": "roller",
  "fault_types": ["unbalance_like"],
  "position": {"scan_id": "scan-20261114181502-3fa9", "at": [0.18, 0.06, 0.0]},
  "note": "",
  "created": {"at": "2026-11-14T18:20:11Z", "by": "Sam"},
  "removed": false,
  "state": {
    "name": "possible",
    "since": "2026-11-14T19:02:11Z",
    "reason": "alarm open: unbalance_like",
    "alarm_id": "alarm-1234",
    "entry_id": null
  }
}
```

| Field | Rule |
|-------|------|
| `name` | Required. What people call it. |
| `kind` | Required. One of `motor`, `bearing`, `roller`, `belt`, `gearbox`, `coupling`, `fan`, `pump`, `mount`, `frame`, `probe_point`, `other`. `probe_point` marks where the sensor head sits. |
| `fault_types` | The `diagnosis` names that point at this part. If left out when the part is made, the platform fills it from a table by `kind`. That table is a starting value, not checked on real faults, so every link it makes is shown as "possible". A person can edit the list. |
| `position` | Where the pin is, or `null`. See Coordinates in 5.4. |
| `removed` | `true` hides the part. Parts are never deleted, because entries point at them. |
| `state` | Set by the platform. Never sent by a client. Rules in 5.7. |

| Route | Body | Reply |
|-------|------|-------|
| `POST /api/machines/conveyor-07/parts` | `name`, `kind`, `by`, and optionally `position`, `fault_types`, `note` | `{"ok": true, "part": {...}}` |
| `PUT /api/parts/part-0007` | `by`, and any of `name`, `kind`, `position`, `fault_types`, `note`, `removed` | `{"ok": true, "part": {...}}` |
| `GET /api/machines/conveyor-07/parts` | | `{"ok": true, "parts": [...]}`, in the order they were made. Add `?removed=1` to include removed parts. |

`GET /api/state` gains `"parts": {"conveyor-07": [...]}`, without removed parts. A machine with a part appears in `machines`.

The stream gains the event `part`, with the part as data, sent when a part is made or changed, and when its `state` changes.

### 5.6 Log entries (Jake)

```json
{
  "entry_id": "entry-000123",
  "machine_id": "conveyor-07",
  "part_id": "part-0007",
  "at": "2026-11-14T19:12:03Z",
  "received_at": "2026-11-14T19:12:04Z",
  "kind": "finding",
  "text": "added weight found on the drive roller",
  "by": "Sam",
  "needs_repair": true,
  "alarm_id": "alarm-1234",
  "diagnosis": "unbalance_like",
  "source": "alarm_answer",
  "run_id": null,
  "simulated": false
}
```

| Field | Rule |
|-------|------|
| `part_id` | The part, or `null` for an entry about the whole machine. |
| `kind` | `finding` (a problem was found or ruled out), `inspection` (someone looked), `repair` (work was done), `note` (anything else), `review` (written by the part review agent). |
| `text` | What happened, in the person's own words. Required, except for `finding` and `inspection`, where it may be empty. |
| `by` | Required. The name the person gave. There is no login, so this is not proof. For a `review` it is `part review agent`. |
| `needs_repair` | Required for `finding` and `inspection`: `true` or `false`. `null` for every other kind. |
| `alarm_id`, `diagnosis` | The alarm this entry is about, and that alarm's `diagnosis`, or `null`. The platform fills `diagnosis` from the alarm. |
| `source` | `person` (typed in the app), `alarm_answer` (made by the platform from an alarm answer), `agent`, or `import` (from an old spreadsheet). Set by the platform, except `import`. |
| `run_id` | For a `review`, the agent run that wrote it. |
| `simulated` | `true` if the alarm it is about was simulated, or the review was not written by a real model call. |

Entries are only ever added. To correct one, add another. This is the same rule as alarm answers.

| Route | Body | Reply |
|-------|------|-------|
| `POST /api/machines/conveyor-07/log` | `kind`, `by`, and `text`, `part_id`, `at`, `needs_repair`, `alarm_id` as the table says | `{"ok": true, "entry": {...}, "part": {"...": "the part with its new state, or null"}}` |
| `GET /api/machines/conveyor-07/log?part=part-0007&kind=repair&since=2026-11-01T00:00:00Z&limit=300` | | `{"ok": true, "entries": [...]}`, newest `at` first. Every filter is optional. Default 300, at most 2,000. |
| `GET /api/machines/conveyor-07/log.csv` | | The same entries as a CSV file that opens in a spreadsheet, with the same filters. Columns, in order: `at`, `part`, `kind`, `text`, `by`, `needs_repair`, `alarm_id`, `diagnosis`, `source`, `entry_id`. |

The stream gains the event `log_entry`, with the entry as data.

**Answering an alarm with a part.** `POST /api/alarms/alarm-1234/answers` (contract section 6) may now also carry `part_id` and `needs_repair`. When `part_id` is there, the platform adds a `finding` entry for that part: `text` is the answer's `finding`, `alarm_id` is the alarm, `source` is `alarm_answer`. A `false` verdict always stores `needs_repair: false`. For a `real` verdict, a missing `needs_repair` counts as `true`. The alarm and its answers are unchanged.

### 5.7 Part state (Jake)

The platform works a part's state out again whenever an entry is added, an alarm changes, or a record arrives. First rule that fits wins:

| State | When | `since` |
|-------|------|---------|
| `needs_repair` | The part's newest `finding` or `inspection` says `needs_repair: true`, and no `repair` entry is newer than it. | That entry's `at` |
| `possible` | An alarm on this machine is open or has no answer, it started after the part's newest `repair` entry, and its `diagnosis` is in the part's `fault_types`. | The `start` of the oldest such alarm |
| `nothing_found` | The platform has at least one record from this machine newer than the part's newest `repair` entry, or newer than the part if it has none. | The `at` of the oldest such record |
| `not_checked` | Otherwise: the probe has not been on this machine since the part was made or repaired. | `null` |

`nothing_found` does not mean the part is healthy. It means the probe has been on the machine and nothing points at this part. The app must use those words.

A `review` entry never changes the state. Neither does a `note`.

### 5.8 The part review agent (Jake)

One run per part. Each run reads only that part's own history. It answers the four questions a planner asks: what is wrong, how that kind of fault comes about, how long it has been there, and whether it needs repair.

A person starts it, from a terminal or a button in the desktop app. Never over HTTP, as the contract already says for the report agent.

The run is posted with the contract's message 4, with three differences: `agent` is `part_review`, there is a `part_id`, and the result is in `review` while `report` stays `null`.

```json
{
  "run_id": "review-20261114-193000-part-0007",
  "agent": "part_review",
  "machine_id": "conveyor-07",
  "part_id": "part-0007",
  "at": "2026-11-14T19:30:00Z",
  "status": "done",
  "model": "local: small.gguf",
  "ok": true,
  "reason": "",
  "problems": [],
  "tool_calls": [{"at": "2026-11-14T19:30:01Z", "name": "get_part_history", "input": {"part_id": "part-0007"}, "output": "{\"entries\": 3, \"alarms\": 1}"}],
  "report": null,
  "review": {
    "what": "Possible unbalance on the drive roller, confirmed by Sam on 14 Nov.",
    "how": "once-per-turn vibration grown (unbalance, a bent shaft, a lost blade)",
    "since": "2026-11-14T19:02:11Z",
    "needs_repair": "yes",
    "why": "One alarm, answered as a real problem, and no repair entry since.",
    "cited_alarms": ["alarm-1234"],
    "cited_entries": ["entry-000123"]
  },
  "simulated": false
}
```

| Field | Rule |
|-------|------|
| `what`, `why` | Written by the model. |
| `how` | Not written by the model. The code copies the fault type's own `text` from the model announcement (contract message 1). If there is no fault type, it is empty. This keeps the agent from inventing a cause the readings do not show. |
| `since` | Not written by the model. The code copies the part's `state.since`. |
| `needs_repair` | `yes`, `no` or `not_sure`. Advice. A person turns it into a `finding` or `inspection` entry if they agree. |
| `cited_alarms`, `cited_entries` | Only ids that were in the history the agent was given. |

Checks in code, for every review, whoever wrote it. They follow the report agent's checks in [TRD.md](TRD.md):

- The shape: these keys, these types, nothing extra.
- Every cited id was in the history supplied.
- Every number in `what` and `why` appears in the history supplied.
- `what` and `why` name no fault type and no part that is not in the history supplied.
- With no alarm and no entry that says `needs_repair: true`, `needs_repair` must not be `yes`.
- One retry on a failed check. A second failure is discarded and the run says why.

When a review passes, the command also adds a `review` entry to the log, so the review is part of the part's history.

The rule-based stand-in writes a review with no model, labelled as a stand-in and as simulated, exactly as the report agent's stand-in does. The local model is opt-in.

## 6. Step 0: wiring (Jake, first, about two hours)

The three of you would otherwise all edit the same few files: the receiver's route table, the store, the desktop app's page list, its state object and its test fakes. Step 0 makes every one of those edits once, with empty stubs behind them, and merges. After that nobody touches a shared file again.

Rohan does not wait for step 0. Cameron does not wait either: conversion and the 3D view (section 9, items C2 and C4) touch no shared file.

Step 0 contains exactly this, and nothing that works yet:

| File | Change |
|------|--------|
| `platform/CONTRACT.md` | Version 3.4: section 5 of this file, as additions. |
| `AGENTS.md` | Track 6, phone scan, folder `phone/`. |
| `docs/PRD.md`, `docs/TRD.md` | One stretch row each for the scan and the part log, pointing here. |
| `platform/desktop/INTERFACES.md` | Section 7 of this file, as numbered additions. |
| `platform/server/receiver_server.py` | Every route in section 5, each one calling a function in `scan_api.py` or `log_api.py`. A way to read a raw body to a file, beside the JSON one. |
| `platform/server/receiver_store.py` | The store makes one scan store and one log store. `state()` asks each for its part of the reply. Three hooks call into the log store: a record was stored, an alarm changed, an alarm was answered. |
| `platform/server/scan_api.py`, `scan_store.py` | Stubs. Every route answers 400 "not built yet". Cameron's from here on. |
| `platform/server/log_api.py`, `log_store.py` | Stubs. Jake's from here on. |
| `platform/desktop/app/shell/pages.py`, `sidebar.py` | Two new page keys, `machine` and `log`, after the existing seven, so shortcuts 1 to 7 are unchanged and the new ones are 8 and 9. With no page package yet, the app already shows its "not built yet" placeholder. |
| `platform/desktop/app/data/api.py` | General `get`, `post`, `put` and `get_bytes` calls, so the two new data files need no further change here. |
| `platform/desktop/app/data/state.py` | `state.scans` and `state.log`, the two objects in section 7. The state passes them each snapshot and each stream event. |
| `platform/desktop/app/data/scans.py`, `partlog.py` | Stubs with the names in section 7. |
| `platform/desktop/tests/fakes.py` and two new fake files | `FakeScans` and `FakeLog` with the fixed sample data in section 7.3, so each person can build pages against the other's data from the first hour. |
| Tests | The shell test that lists the seven page keys now lists nine. Every existing test still passes. |

Done when: `python3 -m pytest platform device` passes, the app opens with two new placeholder pages, and the demo script's steps 0 to 3 still play.

## 7. Inside the desktop app

Additions to [platform/desktop/INTERFACES.md](../platform/desktop/INTERFACES.md). All of its existing rules hold, with one named exception: the Machine page's 3D view uses Qt Quick 3D, because QPainter cannot draw a textured model. It is still not a web view, and every chart is still drawn with QPainter.

### 7.1 `state.scans` (Cameron)

| Kind | Name | Meaning |
|------|------|---------|
| signal | `scans_changed()` | A scan arrived or its status moved |
| signal | `model_ready(str)` | The converted file for this `scan_id` is on disk and can be shown |
| read | `current()` | The current machine's newest scan, or `None` |
| read | `all()` | The current machine's scans, newest first |
| read | `model_path(scan_id)` | Path of the converted file in the app's cache, or `None` while it is being fetched. Asking starts the fetch. |
| read | `qr_text(machine_id, name="")` | The line in 5.2, with this laptop's address on the hotspot |
| action | `import_file(path)` | Send a USDZ or GLB file from this laptop's disk as a scan for the current machine. The fallback when the phone cannot upload. |

### 7.2 `state.log` (Jake)

| Kind | Name | Meaning |
|------|------|---------|
| signal | `parts_changed()` | A part was made, changed, or its state moved |
| signal | `entries_changed()` | An entry was added |
| signal | `selection_changed()` | Another part was selected |
| signal | `reviews_changed()` | A part review run started, moved or ended |
| read | `parts()` | The current machine's parts, without removed ones |
| read | `part(part_id)` | One part, or `None` |
| read | `entries(part_id=None, kind=None)` | The current machine's entries, newest first |
| read | `suspects(alarm)` | The parts whose `fault_types` hold this alarm's `diagnosis` |
| read | `waiting_alarms()` | The current machine's alarms that have no answer naming a part |
| read | `selected_part()` | The selected part's id, or `None` |
| read | `review(part_id)` | The newest part review run for this part, or `None` |
| read | `review_running(part_id)` | `True` while one runs |
| action | `select_part(part_id)` | Select a part. Both pages follow the selection. |
| action | `create_part(name, kind, position=None)` | Make a part on the current machine |
| action | `update_part(part_id, **fields)` | Change a part, including moving its pin |
| action | `add_entry(kind, text, part_id=None, needs_repair=None, alarm_id=None)` | Add an entry |
| action | `answer_alarm_for_part(alarm_id, verdict, part_id, finding=None, needs_repair=None)` | Answer an alarm and name the part |
| action | `start_review(part_id=None, mode="standin")` | Run the part review for one part, or for every part when `None`. `mode` is `standin` or `local`, as for the report agent. |
| action | `export_csv(path)` | Save the log as a CSV file |

Both objects send `by` from the state's existing `answerer()`. A failed action is reported through the state's existing `action_failed(str)`.

The two pages talk only through `state.log`'s selection. The Machine page's "Open log" button calls `select_part` and then `navigate("log")`. The Log page's "Show on machine" button calls `select_part` and then `navigate("machine")`. Neither page imports the other.

### 7.3 Sample data in the fakes

So both pages can be built before the real stores work:

- One machine, `conveyor-07`.
- One scan, `ready`, whose model is a small box written by code when the tests run. No binary file is committed.
- Four parts: "Drive roller" (`roller`, `needs_repair`), "Motor mount" (`mount`, `possible`), "Belt" (`belt`, `nothing_found`), and "Idle roller" (`roller`, `not_checked`, no pin).
- Six entries covering every `kind`, one of them made from an alarm answer.
- One finished part review for "Drive roller".

## 8. Rohan: the phone scan app

**Goal.** An iPhone app with one job: scan a machine, build the 3D model on the phone, send it to the laptop. Nothing else.

**What you need.** A Mac with Xcode 16 or newer, and an iPhone with a LiDAR scanner, an A14 chip or later, and iOS 18 or later. Apple's sample states those three device requirements, and that it does not run in the Simulator.

**Start from Apple's sample**, "Scanning objects using Object Capture" ([developer.apple.com/documentation/realitykit/scanning-objects-using-object-capture](https://developer.apple.com/documentation/realitykit/scanning-objects-using-object-capture)). It already has the guided capture and the on-device reconstruction. Keep its licence file in the folder. Your work is to strip it down and add the pairing and the upload.

**Folder.** `phone/EffenteScan/` for the Xcode project and `phone/README.md` for how to build and run it. Add a `.gitignore` for Xcode's per-user and build files.

### The five screens

```
 1 Pair        2 Capture        3 Build          4 Send            5 Done
 scan the QR   guided scan      model is built   upload with a     "Sent to laptop"
 or paste the  from Apple's     on the phone,    progress bar,     scan another
 line          sample           with progress    retry on failure
```

1. **Pair.** Read the QR code from the laptop, or paste the line by hand (5.2). Then call `GET /api/hello` (5.1). Show the machine's name and "laptop found", or a plain reason why not. Remember the last pairing.
2. **Capture.** Apple's guided capture. The demo conveyor is small and sits on a table, so object mode fits it. Keep the sample's area mode if it costs nothing, for machines too big to walk around.
3. **Build.** Reconstruction on the phone, to one USDZ file. Show progress. Use the detail level the sample uses.
4. **Send.** The upload in 5.3: the file's raw bytes, one fresh `client_id` per scan, kept the same across retries of that scan. Show progress. On a 400, show the `error` text from the reply. Keep the file until the laptop answers 200.
5. **Done.** Show that it arrived. Offer "scan another".

Also add a share button on screens 4 and 5 that hands the USDZ file to the system share sheet. That is the fallback: AirDrop the file to a Mac, or copy it by cable, then use "Import scan file" on the laptop.

### Settings the project needs

| Setting | Why |
|---------|-----|
| A camera usage description | Capture needs the camera |
| A local network usage description | iOS asks the person before an app may reach devices on the local network. The first request can fail before they answer, so retry after. |
| App Transport Security: allow local networking (`NSAllowsLocalNetworking`) | The laptop serves plain HTTP on an IP address. Apple's documentation says that from iOS 17 this is blocked unless this key is set. |
| The URL scheme `effente` | So a QR code read by the system camera can open the app. Not checked: whether the camera offers this for a custom scheme. The in-app reader and the paste box do not depend on it. |

### Rules

- The app makes requests to the `base` address from the QR code and nowhere else.
- No account, no analytics, no third-party package that phones home.
- Photos and the model stay in the app's own storage. Offer to delete them after a successful send.

### First hour, before any changes

Build Apple's sample unchanged, scan the demo conveyor or any object about its size, and share the USDZ file with Cameron and Jake. Do not commit it if it is more than a few megabytes. Write down how many photos it took, how long the build took, and the file size. This one file answers the biggest open question in the plan (section 13).

### Tests

- Unit tests for reading the QR line: a good one, a wrong `v`, a missing `base`, a bad `machine`.
- Unit tests for building the upload request, against a stubbed network: the path, the query, the headers, and that a retry reuses the `client_id`.
- On a real phone, by hand, and written in the README: pair, scan, send, see it in the desktop app. Then with the laptop off: a clear message, and the file still there to retry.

### Done when

A person who has never seen the app can pair it, scan the conveyor and see the model on the laptop without help, and a failed send can be retried without scanning again.

## 9. Cameron: scan intake and the Machine page

**Goal.** A scan sent by the phone ends up as a 3D model on a Machine page, where a person marks parts with pins and sees each part's state at a glance.

Work in this order. C2 and C4 need no shared file, so start them before step 0 lands.

### C1. Receive and store (after step 0)

Build 5.1, 5.3 and 5.4 behind the stubs step 0 left you. Standard library only, like the rest of the receiver.

- Write the body to a temporary file as it arrives, then move it into place. Never read it into memory.
- Check the size and the first four bytes before keeping anything.
- Same `client_id` twice: return the first scan, store nothing.
- Scans survive a restart: load `scans.jsonl` at start. A scan that was `converting` when the receiver stopped goes back to `waiting`.

Test with a fake phone, one command:

```
curl -X POST --data-binary @scan.usdz -H "Content-Type: model/vnd.usdz+zip" \
  "http://localhost:8000/api/machines/conveyor-07/scans?client_id=test-0001&name=scan.usdz&app=curl"
```

Tests: a good USDZ, a good GLB, a text file, an empty body, a body over the limit, no `Content-Length`, a repeated `client_id`, a bad `machine_id`, a restart.

### C2. Convert USDZ to GLB (can start now)

The phone sends USDZ. The 3D view reads GLB. Blender converts one to the other from the command line with no window.

- Put the conversion in `platform/scan/`: one function that takes a USDZ path and a GLB path and returns success or a plain reason.
- Find Blender in this order: the environment variable `EFFENTE_BLENDER`, then `blender` on the PATH, then the usual macOS location.
- Run it as a separate process with a time limit, on a background thread, one conversion at a time. Move `model.status` through `converting` to `ready` or `failed`, and send a `scan` event at each step.
- After a conversion, read the GLB's bounds for `model.bounds`.
- No Blender: `failed`, with a reason that tells the person to install it. The scan is kept, and "convert again" works once Blender is there.

Checked on one laptop: Blender 5.2.2 converted a small textured USDZ to a GLB with positions, normals, texture coordinates and its image in under a second. That USDZ was made by Blender, not by a phone. A real Object Capture file is not yet checked: do that the moment Rohan shares one.

Blender is now needed on the demo laptop. Add that to the platform README.

### C3. `state.scans` (after step 0)

Fill in 7.1. The app fetches the converted file over the API into a cache folder, so it also works when the receiver is on another machine.

### C4. The 3D view (can start now)

Use Qt Quick 3D. Its runtime loader reads a GLB file, and its view can report which point of the model a click landed on.

Checked on one laptop (Linux, Wayland, PySide6 6.12 from pip): the loader read a GLB, drew it with its texture, and a click returned the point that was hit. Three things that test showed:

- A loaded model cannot be picked until each mesh in it is marked pickable, and one frame has been drawn after that.
- A pick gives the point in the scene's coordinates. Convert it to the model's own coordinates before storing it (5.4), and convert back when drawing a pin. Otherwise pins move when the model is scaled to fit the window.
- With `QT_QPA_PLATFORM=offscreen`, which the desktop tests use, the file loads but nothing is drawn and no click hits. So keep the logic out of the view.

Not checked: the view embedded inside the existing widget window, and macOS. Make that your first hour: a window with the app's shell, the view inside it, a sample model on screen, a click printing a position. If it cannot be made to work on the demo laptop, say so at once (section 12).

Note for Linux: a PySide6 installed by the system's package manager may lack Qt Quick 3D. The pip one in `platform/desktop/requirements.txt` has it.

Split the page so tests can run without a graphics card:

- **Logic, plain Python, fully tested offscreen:** which pins to show, each pin's colour and label from its part's state, converting positions, what a click means in each mode, the empty and error states.
- **The view:** as thin as possible. One test that it loads and draws, skipped when the machine cannot draw.

### C5. The Machine page (after step 0, against `FakeLog` until Jake's parts work)

Page key `machine`. It follows the theme, the widgets and the page rules in INTERFACES.md.

```
 +---------------------------------------------+---------------------------+
 |                                             | Parts                     |
 |                                             |  (o) Drive roller         |
 |          3D model, drag to turn,            |      needs repair, 2 h    |
 |          scroll to zoom                     |  (o) Motor mount          |
 |                                             |      possible, 40 min     |
 |             (o) pin      (o) pin            |  (o) Belt                 |
 |                                             |      nothing found        |
 |                                             |  ( ) Idle roller, no pin  |
 |                                             +---------------------------+
 |                                             | Drive roller              |
 | [Add part] [New scan] [Import scan file]    | state, since, last entries|
 |                                             | [Open log] [Add entry]    |
 +---------------------------------------------+---------------------------+
```

- **Pins.** One per part that has a position on the scan shown. Its look comes from the part's state. Red is for shapes only, never text, as the theme says. `not_checked` and `nothing_found` must look different from each other, and neither may look like "healthy".
- **Selecting.** Clicking a pin or a row selects the part through `state.log.select_part`. The selection is shared with the Log page.
- **Adding a part.** "Add part", then a click on the model, then a small form: name and kind. Calls `state.log.create_part`. Dragging a pin, or "move pin", calls `update_part`.
- **Parts from an older scan.** A part whose position belongs to another scan is listed with "no pin on this scan" and a "place" button.
- **New scan.** Shows the QR code from `state.scans.qr_text`, with the line under it as text to copy. A new machine starts here: the person types its id first. Drawing a QR code needs an encoder: add one small pure-Python package to the desktop requirements and draw the squares with QPainter.
- **Import scan file.** A file picker for USDZ or GLB, calling `import_file`. Build this early. It is your own test tool and the fallback for the demo.
- **States to design, not leave blank:** no scan yet, scan arriving, converting, conversion failed with its reason and a retry, the view cannot draw on this laptop (show the parts list alone), no parts yet.

### Done when

A file sent with the curl command above appears on the Machine page as a model within a few seconds, a pin can be placed and survives a restart, and every state above shows something sensible.

## 10. Jake: parts, the log and the part agents

**Goal.** A log that a maintenance planner would use instead of a spreadsheet, where every part has a history and a state, alarms turn into entries, and one agent run per part gives advice the records support.

### J0. Step 0

Section 6. First, and merged before anything else of yours.

### J1. Parts and entries in the receiver

Build 5.5, 5.6 and 5.7 behind the stubs. Standard library only.

- Two append-only files beside the others in `platform/server/data/`: one for parts (the newest line for a `part_id` wins), one for entries.
- The kind table that fills `fault_types`: one small file, built from the fault type names in `effente/fault_types.py`. Mark it in the file as starting values, not checked on real faults. Read the names from that module. Do not copy them.
- Part state is worked out, never stored. Work it out again when the store loads, as alarms already are.
- The three hooks from step 0 (a record was stored, an alarm changed, an alarm was answered) are what move states. Send a `part` event only when a state actually changes: a record a second must not mean an event a second.
- The CSV export must open cleanly in a spreadsheet, including names with commas, quotes and accents.

Tests: every row of the state table in 5.7, in both directions. An answer with a part makes one entry, and a repeated answer makes another. A false verdict never leaves a part in `needs_repair`. A repair clears `needs_repair` and `possible`. A restart gives the same parts, entries and states. Every bad request in 5.5 and 5.6 gets a 400 with a useful sentence.

### J2. `state.log`

Fill in 7.2. Cameron's page depends on `parts()`, `create_part`, `update_part` and the selection, so land those first.

### J3. The Log page

Page key `log`. This is the page that replaces the spreadsheet, so it must be at least as quick to use as one.

```
 +------------------------+----------------------------------------------------+
 | Parts                  | Drive roller      needs repair since 14 Nov 19:12  |
 |  (o) Drive roller      | [Show on machine] [Add entry] [Run part review]    |
 |  (o) Motor mount       +----------------------------------------------------+
 |  (o) Belt              | WHEN         KIND      WHAT              BY   ALARM|
 |  ( ) Idle roller       | 14 Nov 19:30 review    Possible unbal... agent     |
 |  Whole machine         | 14 Nov 19:12 finding   added weight ...  Sam  1234 |
 |                        | 14 Nov 18:20 note      part added        Sam       |
 | Alarms waiting         +----------------------------------------------------+
 |  unbalance_like 19:02  | Review: needs repair: yes (advice)                 |
 |  possible: Drive roller| what, how, since, why, and what it cited           |
 | [Export CSV]           | [Agree: add a finding]                             |
 +------------------------+----------------------------------------------------+
```

- **Table.** The contract's entries for the selected part, or the whole machine. Filter by kind. Use the app's `DataTable`.
- **Add entry.** Kind, text, and for a finding or inspection, whether it needs repair. Two clicks and some typing, no more.
- **Alarms waiting.** Alarms on this machine with no answer naming a part. Each shows its suspects, marked "possible". Answering there asks for the verdict, the part and the finding, and calls `answer_alarm_for_part`. The Live page is not changed: it is the centre of the working demo.
- **Review panel.** The newest review for the part, labelled as advice, with what it cited. "Agree" opens a finding with the review's text filled in for the person to edit. Nothing is added without that click.
- **Export CSV.** Takes part in the app's existing Ctrl+E.
- **States to design:** no parts yet (point to the Machine page, and allow adding a part with no pin right here), no entries yet, no review yet, review running, review failed with its reason, simulated data.

### J4. The part review agent

Build 5.8. Reuse the report agent's pieces in `device/agent/`: the run that posts itself, the local model call, the retry on failed checks, the stand-in pattern. Do not write a second harness, and do not edit `device/agent/`. If it needs a change to be reused, say so.

- A command, `platform/part_review.py`, in the style of `platform/report.py`: a machine, optionally one part, the backend, the model server address.
- It reads the part's history from the receiver's API, builds a plain fact sheet in code (entries, the alarms linked to the part, the state and its `since`, counts, the fault type's text), and gives the model only that.
- The stand-in first. It is the demo's safety net. Then the local model.
- One run per part at a time. The desktop button is disabled while that part's run is running.
- Tests in the style of `device/agent/tests`: fixed histories with known right answers (never checked, possible only, confirmed and waiting for repair, repaired, false alarm), run through the stand-in, plus a rejected review for each check in 5.8.

### Done when

A planner can open the Log page, see which parts need repair and since when, add what they did, export it, and read an agent's review that cites only what is in the log.

## 11. Order and gates

Times are targets for Saturday 14 and Sunday 15 November, local time. If a gate is missed, cut scope (section 12). Do not move the gate.

```
 Rohan    [ Apple sample runs, share a real scan ][ strip, pair, upload ][ polish, test on device ]
 Cameron  [ C2 convert, C4 view spike ][ C1 receive, C3 ][ C5 Machine page       ][ polish ]
 Jake     [ J0 step 0 ][ J1 parts and log, J2    ][ J3 Log page ][ J4 part agent ][ polish ]
          start        gate 0         gate 1               gate 2            gate 3   freeze
```

| Gate | Target | What must be true |
|------|--------|-------------------|
| 0 | Two hours after starting | Step 0 is merged. Rohan has shared one real scan file. Cameron has a sample model on screen in a window and a click that prints a position. |
| 1 | Sat 12:00, before the practice demo | The skeleton, end to end, with rough edges: the curl command sends a file, it converts, it draws on the Machine page, a pin can be placed and is saved as a part, an entry can be added and shows in the Log table. The real scan file has been through the converter. |
| Practice demo | Sat 13:00 | Show the existing conveyor demo. Show the skeleton only if gate 1 passed. |
| 2 | Sat 20:00 | The phone app sends a real scan of the conveyor to the laptop over the hotspot. An alarm from the probe or the simulator shows a suspect part as "possible", an answer naming the part turns its pin, a repair entry turns it back. CSV export works. |
| 3 | Sun 02:00 | The part review runs with the stand-in from the desktop button and its entry lands in the log. The local model is tried once. Every empty and error state is drawn. |
| Freeze | Sun 07:00 | Fixes only from here. Two full rehearsals of section 14 on the demo laptop and the demo phone. A backup video of the whole run is recorded by 09:30. |
| Clock stops | Sun 11:00 | Nothing more is merged. |

Merge to `master` at every gate at the latest, and smaller pieces sooner. A branch that lives longer than a few hours is a merge conflict waiting.

**Ten-minute check, run by anyone after any merge that touches this work:**

1. `python3 -m pytest platform device` passes.
2. `python3 platform/desktop/main.py` opens, and demo steps 0 to 3 play on the Live page.
3. The curl command in C1 answers `"ok": true`, and the model appears on the Machine page.
4. A pin can be added, moved, and is still there after closing and opening the app.
5. Pressing the simulator's unbalance key shows a suspect on the Log page. Answering it with a part turns that pin.
6. Export CSV opens in a spreadsheet.

## 12. What to cut, in order

| If this is not working | Then |
|------------------------|------|
| The local model's part review | Show the stand-in, labelled as a stand-in |
| The part review at all | Demo the log and the pins without it |
| Upload from the phone app | Share the file from the phone to the laptop and use "Import scan file" |
| Our own phone app | Scan with Apple's unchanged sample and import the file. Say on stage that the capture is Apple's. |
| Conversion of a real scan | Try another export from the phone first. Failing that, show the parts list and the log without the model. |
| The 3D view on the demo laptop | The Machine page shows the parts list alone. The log is unaffected. |
| Everything here | The conveyor demo that already works. That is why it must never break. |

## 13. Checked, and not checked

Checked while writing this plan, on one Linux laptop:

- Blender 5.2.2, with no window, converted a small textured USDZ to a GLB in under a second. The USDZ came from Blender, not from a phone.
- Qt Quick 3D (PySide6 6.12 from pip, Python 3.12, Wayland) loaded that GLB, drew it with its texture, and returned the clicked point once the mesh was marked pickable and a frame had been drawn.
- In offscreen mode the same code loads the file but draws nothing and picks nothing.
- The system's own PySide6 package on that laptop has no Qt Quick 3D. The pip one does.
- From Apple's documentation: Object Capture's session is in iOS 17 and later. Apple's sample needs a LiDAR scanner, an A14 chip or later and iOS 18 or later, and does not run in the Simulator. A reconstruction request writes "a USDZ file or a folder (for OBJ output)". From iOS 17, connections to IP addresses are blocked by App Transport Security unless local networking is allowed.
- Every existing name used here (routes, files, classes, page keys) was read from `master` on 14 Nov.

Not checked. Each of these has an owner and should be answered in the first hours:

| Open question | Who | Why it matters |
|---------------|-----|----------------|
| Does Blender convert a real Object Capture scan, with its texture, and how long does it take? | Rohan makes the file, Cameron converts it | It decides whether the whole path works |
| How big is a scan of the conveyor, and how long do capture and build take? | Rohan | The upload limit, and whether a live scan fits on stage |
| Does the 3D view work inside the existing window, and on macOS? | Cameron | It decides whether there is a Machine page |
| Which laptop runs the demo, and does it have Blender and a pip PySide6? | Everyone | Install both today, not on Sunday |
| Can the phone reach the laptop on the team's hotspot, including when the scanning phone is the hotspot? | Rohan and Cameron | The upload |
| Does the system camera open the app from an `effente://` QR code? | Rohan | Only convenience. The in-app reader and the paste box do not need it. |
| Which detail levels can be built on the phone? | Rohan | Scan quality against build time |
| Are a scan's units metres and its up axis Y after conversion? | Cameron | Only matters if we ever show sizes. Pins do not depend on it. |
| Does a local model write a review that passes the checks? | Jake | Otherwise the stand-in is shown |

## 14. The demo, about ninety seconds on top of the existing one

1. "A new machine arrives." Show the phone: a sped-up clip of the scan, made before going on stage. A live scan is too slow and too easy to spoil under stage lighting.
2. The model is already on the laptop. Turn it. Click the drive roller and add it as a part.
3. The probe is on the conveyor. Add the weight. The alarm fires as it does today, and the drive roller's pin changes to "possible".
4. Answer the alarm: real problem, drive roller, "added weight". The pin changes to "needs repair", and a line appears in the log.
5. Run the part review. Read out one line of it, and point at what it cited.
6. Remove the weight, add a repair entry. The pin clears. Export the log and open it in a spreadsheet: "this is the sheet they keep today, and it filled itself".
7. Turn the WiFi's internet off, or show that it was never on. Everything still works.

What not to say on stage:

- That the probe found which part failed. A person linked the fault to the part.
- That `nothing_found` means healthy.
- That the review is the agent's decision. It is advice a person accepts or not.
- Any size or measurement taken from the scan.
- That the fault type to part table is validated. It is a starting value.
