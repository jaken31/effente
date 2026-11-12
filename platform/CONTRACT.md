# Device and platform contract

Version 2.1, pending the team's sign-off. Owner: track 5. This file is the whole interface between the handheld (or the simulated device), the laptop platform, the dashboard and the agent fleet. Build to it exactly. If it is wrong, tell track 5; do not work around it.

It follows the log record in [docs/TRD.md](../docs/TRD.md) and adds four fields to it: `model_version`, `reason`, `simulated`, and (set by the platform) `seq`.

## Changes from version 2

Additions only. Nothing a version 2 device sends or receives changes.

- The platform finds alarms in the records it stores and gives each an id (section 6).
- People answer an alarm: real problem or false alarm (section 6).
- `GET /api/state` has `alarms`, the stream has an `alarm` event, and there is a per-machine alarm history (section 5).

## Changes from version 1

- Diagnosis names are the device runtime's (`effente/model_runtime.py`, `effente/fault_types.py`), not the four demo classes.
- `features` holds the readings that moved, in spreads, not switch-off-tested feature activity.
- The feature mask (message 3, `mask` fields and the `mask` event) is removed.
- `spectrum` is 20 vibration bands in log10, not 64 magnitudes from 0 to 1.
- The agent runs on the laptop, not the device.
- Message 1 says which library entry the model came from, and whether it was calibrated or trained on the machine alone.

## Basics

- Plain HTTP and JSON. The platform listens on port `8000` on all interfaces. The device finds it through the environment variable `EFFENTE_PLATFORM_URL` (default `http://localhost:8000`).
- The device only makes outgoing requests. The platform never connects to the device.
- No login. The platform runs on the team's own hotspot.
- Times are ISO 8601 UTC, `2026-11-14T16:02:11Z`. Fractions of a second are allowed.
- Success is HTTP 200 with `"ok": true`. A bad request is HTTP 400 with `{"ok": false, "error": "what was wrong"}`. Unknown fields are stored and passed on, never rejected.
- A device counts as connected if the platform heard from it in the last 5 seconds. A device must make a request at least every 2 seconds: a record upload, or `GET /api/devices/{device_id}` when it is not watching.

## Names

| Thing | Values |
|-------|--------|
| `diagnosis` | `warming_up`, `stopped`, `normal`, one of the 21 fault type names in `effente/fault_types.py`, `unrecognised_pattern` |
| fault group | `vibration`, `sound`, `heat`, `room`, `sensing` |
| feature name | a name from the model file's feature table, for example `vib_1x`, `vib_velocity_mm_s` |
| `priority` | `none`, `low`, `medium`, `high` |
| `model_version` | any string that changes when the model file changes, for example `conveyor-07-20261114` |

## 1. Device announces its model

Sent once at start, again after every calibration or training, and whenever the platform replies `"need_model": true`. This is how the dashboard learns the fault types and what each reading means.

`POST /api/model`

```json
{
  "device_id": "effente-01",
  "machine_id": "conveyor-07",
  "model_version": "conveyor-07-20261114",
  "features_version": "c985a0908a96",
  "library_entry": "conveyor",
  "source": "calibrated",
  "threshold": 1.0,
  "fault_types": [
    {"name": "unbalance_like", "text": "once-per-turn vibration grown (unbalance, a bent shaft, a lost blade)", "group": "vibration", "validated": false},
    {"name": "device_moved", "text": "the device has been moved or knocked (its tilt changed)", "group": "sensing", "validated": false}
  ],
  "features": [
    {"name": "vib_1x", "text": "vibration once per turn", "unit": "log"},
    {"name": "vib_velocity_mm_s", "text": "vibration velocity, 10-250 Hz rms (ISO 10816-like, partial band)", "unit": "mm_s"}
  ],
  "spectrum_edges_hz": [5.0, 6.3, 7.9],
  "simulated": false
}
```

| Field | Rule |
|-------|------|
| `library_entry` | The library entry the model started from, for example `conveyor` or `generic_rotating`. |
| `source` | `calibrated` (a library entry fitted to this machine) or `trained` (a model trained on this machine alone after a long stay). |
| `fault_types` | Every type the device can name. `group` is `sensing` for a problem with the device or its view, otherwise the first sensor the type relies on. `validated` is `true` only once the type has caught that fault on the test rig or a pilot; the dashboard shows unvalidated types as "possible". |
| `features` | Every reading in the model's feature table. |
| `spectrum_edges_hz` | 21 band edges for the 20 `spectrum` values. (The example above is shortened.) |

Reply: `{"ok": true}`

## 2. Device sends a log record

While watching, one record a second. The diagnosis changes once per summary period.

`POST /api/records` with one record, or a JSON array of records (for catching up after a dropout).

```json
{
  "device_id": "effente-01",
  "machine_id": "conveyor-07",
  "model_version": "conveyor-07-20261114",
  "at": "2026-11-14T16:02:11Z",
  "diagnosis": "unbalance_like",
  "diagnosis_this_minute": "unbalance_like",
  "confidence": 0.71,
  "unknown_score": 2.4,
  "reason": "once-per-turn vibration grown (unbalance, a bent shaft, a lost blade)",
  "features": {"vib_1x": 7.2, "vib_velocity_mm_s": 3.1},
  "fault_types": {"unbalance_like": 9.1, "vibration_too_high": 4.2},
  "abnormal_minutes": 3,
  "out_of_range": 0,
  "note": "",
  "spectrum": [-4.70, -4.62, -4.63],
  "simulated": false
}
```

| Field | Rule |
|-------|------|
| `device_id`, `machine_id`, `model_version`, `at`, `diagnosis` | Required. A record missing one is rejected with 400. |
| `diagnosis` | During an abnormal stretch, the type named most often so far, so it does not flip. |
| `diagnosis_this_minute` | This period's own diagnosis. Present only while abnormal. |
| `confidence` | 0 to 1. |
| `unknown_score` | The smoothed score divided by the threshold. Above 1 is abnormal. `null` while `warming_up` or `stopped`. |
| `reason` | The one line the device screen shows: the fault type's `text`, "abnormal, no known fault type fits" for `unrecognised_pattern`, empty otherwise. |
| `features` | The readings that moved most, each in spreads of its normal variation, signed. While normal, the top 3. |
| `fault_types` | Every type with evidence, strongest first. Present only while abnormal. |
| `abnormal_minutes` | Periods in the current abnormal stretch. |
| `out_of_range` | How many readings are far outside anything seen while learning. |
| `note` | Plain-text caveat, for example "sound only so far: may be the room". |
| `spectrum` | 20 vibration band levels, log10, on the edges announced in message 1. (The example above is shortened.) |
| `simulated` | `true` if the reading did not come from a real sensor. Missing means `false`. |

Reply:

```json
{"ok": true, "stored": 1, "need_model": false}
```

`need_model` is `true` when the platform has not seen this `model_version`; the device then repeats message 1.

The platform adds two fields before storing or showing a record: `seq` (a whole number that only goes up, across all devices) and `received_at`.

## 3. Device check-in

When the device is not watching (display, calibrating, recording or training), it checks in so the dashboard knows it is alive and what it is doing.

`GET /api/devices/effente-01?mode=recording&machine_id=conveyor-07`

`mode` is `display`, `calibrating`, `recording` or `training`. Reply: `{"ok": true}`

## 4. Agent sends its report

The agents run on the laptop. An agent sends the whole run each time something happens: once when it starts, after each tool call, and once at the end. Same `run_id` every time; the platform keeps the latest copy. This is how tool calls show up live.

`PUT /api/agent/runs/run-20261114-160300-conveyor-07`

```json
{
  "run_id": "run-20261114-160300-conveyor-07",
  "agent": "maintenance_report",
  "machine_id": "conveyor-07",
  "at": "2026-11-14T16:03:00Z",
  "status": "done",
  "model": "claude-opus-5-5",
  "usage": {"input_tokens": 2140, "output_tokens": 310},
  "ok": true,
  "reason": "",
  "problems": [],
  "tool_calls": [
    {"at": "2026-11-14T16:03:01Z", "name": "get_readings", "input": {"since": ""}, "output": "{\"records\": 42}"},
    {"at": "2026-11-14T16:03:02Z", "name": "get_fault_types", "input": {}, "output": "{\"unbalance_like\": \"once-per-turn vibration grown\"}"}
  ],
  "report": {
    "summary": "Possible unbalance on conveyor-07: alarm for 40 of 42 periods, once-per-turn vibration up about 7 spreads.",
    "priority": "medium",
    "cited_features": ["vib_1x"],
    "recommended_action": "Check the drive roller for added mass or damage, then rebalance.",
    "part_reference": ""
  },
  "simulated": false
}
```

| Field | Rule |
|-------|------|
| `run_id`, `machine_id`, `at`, `status` | Required. `run_id` in the body must match the URL. It may contain letters, digits, `-`, `_` and `.` only. |
| `agent` | Which agent in the fleet wrote the run. `maintenance_report` for now. |
| `status` | `running` or `done`. |
| `ok` | Meaningful when `status` is `done`. `true` only if the report passed the agent's checks. |
| `report` | `null` unless `ok` is `true`. Shape is fixed as shown. `cited_features` holds only readings that appear in `features` of the records the agent read. It may be empty, and must be for `normal`. |
| `reason`, `problems` | Why there is no report: for example `agent unavailable: no connection`, or the list of failed checks. |
| `tool_calls` | In order. `output` is a string, cut to 2,000 characters. |
| `simulated` | `true` if the report did not come from a real Claude call, or was written from simulated readings. |

Reply: `{"ok": true}`

If the platform's HTTP endpoint cannot be reached the agent still writes its own log and carries on.

## 5. What the dashboard reads

Device authors can skip this section.

`GET /api/state` returns everything needed to draw the page from cold:

```json
{
  "ok": true,
  "now": "2026-11-14T16:02:12Z",
  "devices": {
    "effente-01": {
      "connected": true,
      "last_seen": "2026-11-14T16:02:11Z",
      "mode": "watching",
      "machine_id": "conveyor-07",
      "model_version": "conveyor-07-20261114",
      "latest": {"...": "the newest record from this device, with spectrum, or null"}
    }
  },
  "models": {"conveyor-07-20261114": {"...": "message 1 as announced"}},
  "machines": ["conveyor-07"],
  "alarms": {"alarm-1234": {"...": "an alarm, as in section 6"}}
}
```

`alarms` holds every open alarm and the 20 newest alarms of each machine, keyed by `alarm_id`.

`GET /api/stream` is a server-sent event stream. Each event has a name and one JSON object as data:

| Event | Data | Sent when |
|-------|------|-----------|
| `record` | a record, with `seq` | a record is stored |
| `model` | message 1 | a device announces a model |
| `agent_run` | message 4 | a run is created or updated |
| `device` | `{"device_id": "effente-01", "connected": false, "last_seen": "...", "mode": "..."}` | a device connects, goes quiet, or changes mode |
| `alarm` | an alarm, as in section 6 | an alarm opens, its `diagnosis` or `periods` changes, it closes, or an answer is stored |

A comment line is sent every 5 seconds to keep the connection open. After a reconnect the dashboard calls `GET /api/state` again.

`GET /api/machines/conveyor-07/records?limit=300` returns `{"ok": true, "records": [...]}`, oldest first, the newest `limit` records (default 300, at most 2,000), without `spectrum`. Add `&spectrum=1` to keep it.

`GET /api/machines/conveyor-07/agent-runs?limit=20` returns `{"ok": true, "runs": [...]}`, newest first.

`GET /api/machines/conveyor-07/alarms?limit=20` returns `{"ok": true, "alarms": [...]}`, newest `start` first (default 20, at most 2,000).

Everything else under `/` is the dashboard's own files, served from `platform/web/`, with `index.html` at `/`.

## 6. Alarms and answers

Device authors can skip this section. The device sends nothing new: the platform finds alarms in the records of message 2.

### What an alarm is

An alarm is one abnormal stretch on one machine. The platform reads each machine's records in `seq` order.

- A record is **abnormal** when its `diagnosis` is a fault type or `unrecognised_pattern`, that is, not `normal`, `stopped` or `warming_up`. The device already needs several periods over its line before it says so, so one odd reading does not open an alarm.
- An abnormal record when no alarm is open for the machine **opens** an alarm.
- A `normal`, `stopped` or `warming_up` record **closes** it.
- An abnormal record closes the open alarm and opens a new one when its `device_id` or `model_version` differs from the open alarm's, or when its `abnormal_minutes` is lower than the last one seen (the device restarted or recalibrated).
- `alarm_id` is `alarm-` and the `seq` of the record that opened it, for example `alarm-1234`. It never changes, and it is the same after a platform restart because `seq` is stored.
- A device that goes quiet leaves its alarm open until its next record.

Alarms are worked out again from the stored records when the platform starts.

### The alarm

```json
{
  "alarm_id": "alarm-1234",
  "machine_id": "conveyor-07",
  "device_id": "effente-01",
  "model_version": "conveyor-07-20261114",
  "library_entry": "conveyor",
  "start": "2026-11-14T16:02:11Z",
  "end": "2026-11-14T16:09:40Z",
  "open": false,
  "diagnosis": "unbalance_like",
  "periods": 7,
  "peak_unknown_score": 2.4,
  "simulated": false,
  "answer": {"verdict": "real", "by": "Sam", "at": "2026-11-14T16:12:03Z", "finding": "loose bolt on the motor mount"},
  "answers": [{"...": "every answer, oldest first"}]
}
```

| Field | Rule |
|-------|------|
| `start` | `at` of the record that opened the alarm. |
| `end` | `at` of its newest abnormal record. Moves while the alarm is open. |
| `open` | `true` until a record closes the alarm. |
| `diagnosis` | `diagnosis` of its newest abnormal record. The device keeps this steady over a stretch. |
| `periods` | The highest `abnormal_minutes` seen. |
| `peak_unknown_score` | The highest `unknown_score` seen. |
| `library_entry` | From the model announcement for `model_version`; `null` if the platform has none. |
| `simulated` | `true` if any of its records was simulated. |
| `answer` | The newest answer, or `null` if nobody has answered. |
| `answers` | Every answer, oldest first. |

### Answering an alarm

`POST /api/alarms/alarm-1234/answers`

```json
{"verdict": "real", "by": "Sam", "at": "2026-11-14T16:12:03Z", "finding": "loose bolt on the motor mount"}
```

| Field | Rule |
|-------|------|
| `verdict` | Required. `real` (a real problem) or `false` (a false alarm). |
| `by` | Required. The name the person gave. There is no login, so this is not proof of who answered. |
| `at` | When they answered. If missing, the platform's time. |
| `finding` | What they found, as text. Optional, and may come later. |

- An open alarm can be answered.
- Answers are only ever added. To change a verdict, or to add a finding days later, post another answer. The newest one counts.
- The platform adds `alarm_id`, `machine_id`, `model_version`, `library_entry` and `received_at` to each answer before storing it.
- An unknown `alarm_id` is a bad request (HTTP 400).

Reply: `{"ok": true, "alarm": {"...": "the alarm with the new answer"}}`

## Not in version 2.1

- The dashboard cannot start an agent. A person starts it.
- Machine profiles and model delivery to the device. Planned in [docs/model-lifecycle.md](../docs/model-lifecycle.md).
- Answering an alarm on the device, reminders for unanswered alarms, and any use of answers by training or thresholds. Also planned there.
- More than one platform, or any security.
