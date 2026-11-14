# Device and platform contract

Version 3.4, pending the team's sign-off. Owner: track 5. This file is the whole interface between the handheld (or the simulated device), the laptop platform, the dashboard and the agent fleet. Build to it exactly. If it is wrong, tell track 5; do not work around it.

It follows the log record in [docs/TRD.md](../docs/TRD.md) and adds eight fields to it: `model_version`, `reason`, `problems`, `wrong`, `sensitivity`, `alarm_line`, `simulated`, and (set by the platform) `seq`.

## Changes from version 3.3

Additions only. A version 3.3 device or dashboard keeps working. All of it is in section 8, and none of it is built yet: see the note there.

- `GET /api/hello`, a small reply the phone scan app uses to find the laptop (section 8.1).
- Scans: the phone uploads a 3D scan of a machine as a raw file, the platform stores and converts it (sections 8.2 to 8.4).
- Parts and log entries for each machine, and a state for each part worked out from entries, alarms and records (sections 8.5 to 8.7).
- An alarm answer may name a part (section 8.6).
- A second agent, `part_review`, posts its runs with message 4 (section 8.8).
- `GET /api/state` gains `scans` and `parts`. The stream gains the events `scan`, `part` and `log_entry`.

## Changes from version 3.2

Additions only. A version 3.2 device or dashboard keeps working.

- A record may carry `heat`: the IMU's temperature, and, when a thermal camera is fitted, its summary and up to two small images (section 2).
- The per-machine history leaves out those images unless asked (section 5).

## Changes from version 3.1

Wording only. Nothing a device or the dashboard sends or receives changes.

- Who starts an agent: a person, from a terminal or the desktop app's button, never over HTTP. The desktop app runs one run per machine at a time (section 4).

## Changes from version 3

Additions only. A version 3 device keeps working; the dashboard shows its sensitivity as not reported.

- `unknown_score` is compared with `alarm_line`, not with 1. They are equal only at the default sensitivity, 0.5.
- Records, message 1 and the check-in say the sensitivity the device is running at.
- Alarms keep the sensitivity and `alarm_line` they opened at (section 6).
- The dashboard can set a device's sensitivity. The device picks it up from the replies it already gets (section 7).

## Changes from version 2.1

- Problems are in the contract. The device's safety checks (`effente/safety.py`) run apart from the model, in every mode, and until now reached only the device's own port-8765 stream.
- Names: a list of problem names, and of the two kinds of problem.
- Message 2: records carry the required fields `problems` and `wrong`.
- Message 3: the check-in is `POST /api/devices/{device_id}` with a body that carries `mode`, `wrong` and `problems`, not a `GET` with a query string.
- Section 5: `/api/state` has each device's `wrong` and `problems`. `/api/stream` has two new events, `problem` and `problem_cleared`, made by the platform. The `device` event always carries `mode`.
- Section 5: rules for showing problems next to the diagnosis.
- Section 6: problems do not open or close alarms; alarms still follow `diagnosis` only.

## Changes from version 2

Additions only. Nothing a version 2 device sends or receives changes.

- The platform finds alarms in the records it stores and gives each an id (section 6).
- People answer an alarm: real problem or false alarm (section 6).
- `GET /api/state` has `alarms`, the stream has an `alarm` event, and there is a per-machine alarm history (section 5).

## Basics

- Plain HTTP and JSON. The platform listens on port `8000` on all interfaces. The device finds it through the environment variable `EFFENTE_PLATFORM_URL` (default `http://localhost:8000`).
- The device only makes outgoing requests. The platform never connects to the device.
- No login. The platform runs on the team's own hotspot.
- Times are ISO 8601 UTC, `2026-11-14T16:02:11Z`. Fractions of a second are allowed.
- Success is HTTP 200 with `"ok": true`. A bad request is HTTP 400 with `{"ok": false, "error": "what was wrong"}`. Unknown fields are stored and passed on, never rejected.
- A device counts as connected if the platform heard from it in the last 5 seconds. A device must make a request at least every 2 seconds: a record upload while watching, or a check-in (message 3) when it is not.

## Names

| Thing | Values |
|-------|--------|
| `diagnosis` | `warming_up`, `stopped`, `normal`, one of the 21 fault type names in `effente/fault_types.py`, `unrecognised_pattern` |
| fault group | `vibration`, `sound`, `heat`, `room`, `sensing` |
| feature name | a name from the model file's feature table, for example `vib_1x`, `vib_velocity_mm_s` |
| `priority` | `none`, `low`, `medium`, `high` |
| `model_version` | any string that changes when the model file changes, for example `conveyor-07-20261114` |
| problem `name` | limits: `vibration_over_limit`, `knocking`, `too_hot`, `heating_fast`, `repeated_jolts`. Outside normal: `vibration_above_normal`, `hotter_than_normal`, `louder_than_normal` |
| problem `kind` | `limit`, `outside_normal` |

A problem name is never a fault type name, so the two can always be told apart.

### Problems

A problem is something the device's safety checks (`effente/safety.py`) found wrong. They run apart from the model: a problem does not depend on the diagnosis, and can be on while the diagnosis is `normal`, `warming_up` or `stopped`, in every mode. A problem shows once it has held for about 10 seconds and clears once it has mostly stopped.

- A `limit` means trouble on practically any machine. Limits work from the moment the device is plugged in, before anything is learned. If the machine already went past a limit while learning, that limit is dropped for it and never shows.
- An `outside_normal` problem means a reading is well beyond this machine's own normal, learned with its model. These run only while watching. Outside watching, only limits can be on. How far beyond counts is set by the sensitivity (section 7); limits never change with it.

| `name` | `kind` | `value` |
|--------|--------|---------|
| `vibration_over_limit` | `limit` | vibration velocity in mm/s (the limit is 11) |
| `knocking` | `limit` | vibration kurtosis (smooth running is about 3, the limit 8) |
| `too_hot` | `limit` | the hotter of the IMU and the thermal camera, in degC (the limit is 100) |
| `heating_fast` | `limit` | temperature rise in degC a minute (the limit is 8) |
| `repeated_jolts` | `limit` | sudden jolts in the last 10 minutes (the limit is 5) |
| `vibration_above_normal` | `outside_normal` | times this machine's normal |
| `hotter_than_normal` | `outside_normal` | degC above this machine's normal |
| `louder_than_normal` | `outside_normal` | dB above this machine's normal |

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
  "sensitivity": 0.5,
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
| `sensitivity` | 0 to 1, the device's sensitivity when it sent this message (section 7). Missing means not reported. |
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
  "sensitivity": 0.5,
  "alarm_line": 1.0,
  "reason": "once-per-turn vibration grown (unbalance, a bent shaft, a lost blade)",
  "features": {"vib_1x": 7.2, "vib_velocity_mm_s": 3.1},
  "fault_types": {"unbalance_like": 9.1, "vibration_too_high": 4.2},
  "abnormal_minutes": 3,
  "out_of_range": 0,
  "note": "",
  "problems": [
    {"name": "vibration_above_normal", "kind": "outside_normal", "text": "vibrating well beyond its normal", "value": 2.3, "since": "2026-11-14T16:00:47Z"}
  ],
  "wrong": true,
  "spectrum": [-4.70, -4.62, -4.63],
  "simulated": false
}
```

| Field | Rule |
|-------|------|
| `device_id`, `machine_id`, `model_version`, `at`, `diagnosis`, `problems`, `wrong` | Required. A record missing one is rejected with 400. |
| `diagnosis` | During an abnormal stretch, the type named most often so far, so it does not flip. |
| `diagnosis_this_minute` | This period's own diagnosis. Present only while abnormal. |
| `confidence` | 0 to 1. |
| `unknown_score` | The smoothed score divided by the threshold. Above `alarm_line` is abnormal. `null` while `warming_up` or `stopped`. Sensitivity does not change it, only the line it is compared with. |
| `sensitivity` | 0 (least sensitive) to 1 (most), the value this record was judged at (section 7). Missing means not reported: the dashboard then says so and draws no line. |
| `alarm_line` | The `unknown_score` above which this record counts as abnormal: `4 ** (1 - 2 * sensitivity)`, so 4 at 0, 1 at the default 0.5, and 0.25 at 1. Sent with `sensitivity`. |
| `reason` | The one line the device screen shows: the fault type's `text`, "abnormal, no known fault type fits" for `unrecognised_pattern`, empty otherwise. |
| `features` | The readings that moved most, each in spreads of its normal variation, signed. While normal, the top 3. |
| `fault_types` | Every type with evidence, strongest first. Present only while abnormal. |
| `abnormal_minutes` | Periods in the current abnormal stretch. |
| `out_of_range` | How many readings are far outside anything seen while learning. |
| `note` | Plain-text caveat, for example "sound only so far: may be the room". |
| `problems` | What the safety checks find wrong now, limits first, in the order of the names table. `[]` when nothing is wrong. Each is `{name, kind, text, value, since}`: `text` is the sentence for people, `value` the number that decides (see the problems table) or `null` when there is no current reading, `since` when it started (ISO UTC). Problems do not depend on the diagnosis. |
| `wrong` | `true` if `diagnosis` is a fault type or `unrecognised_pattern`, or `problems` is not empty. Otherwise `false`. |
| `spectrum` | 20 vibration band levels, log10, on the edges announced in message 1. (The example above is shortened.) |
| `simulated` | `true` if the reading did not come from a real sensor. Missing means `false`. |

Reply:

```json
{"ok": true, "stored": 1, "need_model": false}
```

`need_model` is `true` when the platform has not seen this `model_version`; the device then repeats message 1.

Once the dashboard has set a sensitivity for this device, the reply also carries `"settings": {"sensitivity": 0.7}` (section 7).

The platform adds two fields before storing or showing a record: `seq` (a whole number that only goes up, across all devices) and `received_at`.

### Heat (optional)

A record may carry `heat`. Missing or `null` means the device sent no heat reading.

With a thermal camera (MLX90640, 32 x 24 pixels):

```json
"heat": {
  "source": "camera",
  "imu_temp_c": 42.6,
  "max_c": 61.2,
  "hot_row": 9,
  "hot_col": 21,
  "background_c": 25.0,
  "cells": [0.4, 0.6, 1.2],
  "spots": [1.1, 1.9, 3.0],
  "frozen": false,
  "scene_match": 0.93,
  "frames": [
    {"at": "2026-11-14T16:02:10.5Z", "min_c": 24.1, "step_c": 0.15, "data": "base64 of 768 bytes"},
    {"at": "2026-11-14T16:02:11Z", "min_c": 24.0, "step_c": 0.15, "data": "base64 of 768 bytes"}
  ]
}
```

Without one:

```json
"heat": {"source": "imu", "imu_temp_c": 42.6}
```

| Field | Rule |
|-------|------|
| `source` | `camera` if the thermal camera gave this reading, `imu` if there is no camera. With `imu`, only `imu_temp_c` is present. |
| `imu_temp_c` | The IMU's own temperature sensor, in degC. Always present. It follows the surface the sensor head is fixed to, slowly, and is not a surface temperature. `null` if the IMU gave none. |
| `max_c`, `background_c` | Hottest pixel and background (the image's median) over this record's window, in degC: `th_max_c` and `th_background_c` in `effente/features.py`. |
| `hot_row`, `hot_col` | Where the hottest pixel is: row 0-23, column 0-31, in the camera's pixel order (`th_hot_row`, `th_hot_col`). |
| `cells`, `spots` | 48 numbers each: the image cut into 6 rows of 8 blocks of 4 x 4 pixels, row by row from row 0. `cells` is each block's average and `spots` its hottest pixel, both in degC above `background_c`, so they can be below 0 (`th_cell_RC`, `th_spot_RC`). |
| `frozen` | `true` if every frame in the window was identical (camera stuck, `th_frozen`). |
| `scene_match` | How closely the image matches its usual layout, -1 to 1 (`trend_scene_match`). `null` until the device has learned the usual view. |
| `frames` | Optional. The images taken since the previous record, oldest first, at most 2. Each is 768 bytes, base64 encoded: row 0 first, 32 pixels a row, in the same pixel order as `hot_row` and `hot_col`. Pixel temperature in degC = `min_c` + byte x `step_c`. The device picks `min_c` and `step_c` for each image so every pixel fits in 0 to 255. |

Two images a second is the most the device's internal link can carry next to the vibration data. That is an estimate from its measured spare capacity, not yet tested with the camera.

The platform adds two fields before storing or showing a record: `seq` (a whole number that only goes up, across all devices) and `received_at`.

## 3. Device check-in

When the device is not watching (display, calibrating, recording or training), it checks in so the dashboard knows it is alive, what it is doing and whether something is wrong. It sends no records then, but the limits still run.

`POST /api/devices/effente-01`

```json
{
  "machine_id": "conveyor-07",
  "mode": "recording",
  "at": "2026-11-14T15:40:02Z",
  "wrong": true,
  "problems": [
    {"name": "too_hot", "kind": "limit", "text": "over 100 C", "value": 104.5, "since": "2026-11-14T15:39:50Z"}
  ]
}
```

| Field | Rule |
|-------|------|
| `machine_id`, `mode`, `at`, `wrong`, `problems` | Required. A check-in missing one is rejected with 400. |
| `mode` | `display`, `calibrating`, `recording` or `training`. |
| `problems` | As in message 2. Only limits can be on outside watching. |
| `wrong` | `true` if `problems` is not empty. There is no diagnosis outside watching. |
| `sensitivity` | Optional. 0 to 1, the device's sensitivity (section 7). Missing means not reported. |

Reply: `{"ok": true}`, with `settings` as in message 2 once the dashboard has set a sensitivity.

### Readings (optional, proposed: not yet agreed by the team)

A check-in or a record may carry `readings`, the numbers on the device's own screen, so the dashboard can show them in every mode:

```json
"readings": {"vib_mg": 4.5, "peak_g": 0.014, "speed_hz": 0.0, "imu_temp_c": 26.6, "tilt": [0.98, 0.41, 0.25], "sound_db": 82.6, "thermal_max_c": null}
```

These are `readings` in the device's status (`device/intake/device_app.py`). A value is `null` when its sensor gave none. The platform keeps the newest for each connected device: `readings` (`{"at", "readings"}`) in `GET /api/state`, and a `readings` event on the stream (`device_id`, `machine_id`, `at`, `readings`). A check-in may also carry `spectrum` with `spectrum_edges_hz` and `heat`, in the shapes of messages 2 and 1, so the spectrum and heat panels have something to draw while the device is not watching; the platform keeps them beside the readings. `readings` may then include features from the device's newest row under their own names: `vib_velocity_mm_s`, `snd_tone_hz`, `snd_tone_db` and `snd_centroid_hz`. They are not stored in the history. Today only `platform/link/device_link.py` sends them; `device/intake/uploader.py` does not.

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
  "model": "local: small.gguf",
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
| `reason`, `problems` | Why there is no report: for example `agent unavailable: no connection`, or the list of failed checks. These `problems` are the agent's, not the device's of message 2. |
| `tool_calls` | In order. `output` is a string, cut to 2,000 characters. |
| `simulated` | `true` if the report did not come from a real model call, or was written from simulated readings. |

Reply: `{"ok": true}`

If the platform's HTTP endpoint cannot be reached the agent still writes its own log and carries on.

The desktop app starts at most one run per machine at a time: its button is disabled while that machine's latest run is `running`. The local model call is opt-in; the default is the stand-in.

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
      "wrong": true,
      "problems": [{"name": "vibration_above_normal", "kind": "outside_normal", "text": "vibrating well beyond its normal", "value": 2.3, "since": "2026-11-14T16:00:47Z"}],
      "sensitivity": 0.5,
      "settings": {"sensitivity": 0.7},
      "latest": {"...": "the newest record from this device, with spectrum, or null"}
    }
  },
  "models": {"conveyor-07-20261114": {"...": "message 1 as announced"}},
  "machines": ["conveyor-07"],
  "alarms": {"alarm-1234": {"...": "an alarm, as in section 6"}}
}
```

`wrong` and `problems` are from the device's newest record or check-in. While the device is not connected they are `false` and `[]`.

Per device, `sensitivity` is the value the device last reported (in a record, message 1 or a check-in), or `null` if it never has. Unlike `wrong` and `problems`, it is kept while the device is not connected. `settings` is what the dashboard set, or `null` if nothing is set. The two differ while a change is on its way, or when the device is offline.

`alarms` holds every open alarm and the 20 newest alarms of each machine, keyed by `alarm_id`.

`GET /api/stream` is a server-sent event stream. Each event has a name and one JSON object as data:

| Event | Data | Sent when |
|-------|------|-----------|
| `record` | a record, with `seq` | a record is stored |
| `model` | message 1 | a device announces a model |
| `agent_run` | message 4 | a run is created or updated |
| `device` | `{"device_id": "effente-01", "connected": false, "last_seen": "...", "mode": "recording"}` | a device connects, goes quiet, or changes mode. `mode` is always there: `watching`, or the check-in's `mode` |
| `alarm` | an alarm, as in section 6 | an alarm opens, its `diagnosis` or `periods` changes, it closes, or an answer is stored |
| `settings` | `{"device_id": "effente-01", "sensitivity": 0.5, "settings": {"sensitivity": 0.7}}`, the two values in `/api/state` | the dashboard sets or clears a sensitivity, or the device reports a different one |
| `problem` | `{"device_id": "effente-01", "machine_id": "conveyor-07", "at": "...", "problem": {"name": "too_hot", "...": "as in message 2"}}` | a problem appears |
| `problem_cleared` | `{"device_id": "effente-01", "machine_id": "conveyor-07", "at": "...", "name": "too_hot", "reason": "device quiet"}` | a problem goes away |

The device never sends events. The platform makes `problem` and `problem_cleared` by comparing the `problems` of each record or check-in with the previous ones from the same device, by `name`. A new name is a `problem`; a missing name is a `problem_cleared`. A changed `value` makes no event; it shows in `/api/state` and the next `record`. `at` is the `at` of the record or check-in that made the change. When a device goes quiet, the platform clears each of its problems with `"reason": "device quiet"`; otherwise there is no `reason`. If the device comes back with the problem still on, it appears again as a new `problem`.

A comment line is sent every 5 seconds to keep the connection open. After a reconnect the dashboard calls `GET /api/state` again.

`GET /api/machines/conveyor-07/records?limit=300` returns `{"ok": true, "records": [...]}`, oldest first, the newest `limit` records (default 300, at most 2,000), without `spectrum` and without `heat.frames`. Add `&spectrum=1` or `&frames=1` to keep them.

To read a longer stretch, page forward. `&since=2026-11-14T15:00:00Z` (an ISO 8601 time; no zone means UTC) returns the oldest `limit` records whose `at` is at or after it, instead of the newest. `&after_seq=N` returns only records with `seq` above `N`. Pass both, with `N` the `seq` of the last record you got, until a page comes back shorter than `limit`. A bad `since` or `after_seq` is a 400.

`GET /api/machines/conveyor-07/agent-runs?limit=20` returns `{"ok": true, "runs": [...]}`, newest first.

`GET /api/machines/conveyor-07/alarms?limit=20` returns `{"ok": true, "alarms": [...]}`, newest `start` first (default 20, at most 2,000).

Everything else under `/` is the dashboard's own files, served from `platform/web/`, with `index.html` at `/`.

### Showing problems next to the diagnosis

- `wrong` decides the headline, in every mode: something is wrong, or nothing is.
- When `diagnosis` is `normal`, label the model's panel "Model: nothing unusual yet". Problems react in about 10 seconds; the model judges only once per summary period, so a problem can be on before the model has had its say.
- Show `warming_up` and `stopped` next to any problems. Never hide either one, and never let a problem hide them.
- List the fault type first, then the problems. Never merge them by name, even when they sound alike (`vibration_too_high` the fault type, `vibration_over_limit` the problem).
- Label each problem's `kind`: `limit` as "limit for any machine", `outside_normal` as "beyond this machine's normal".

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
- Problems (message 2) do not open or close alarms. A record with `problems` but a `normal` diagnosis is not abnormal here.

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
  "sensitivity": 0.5,
  "alarm_line": 1.0,
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
| `sensitivity`, `alarm_line` | From the record that opened the alarm, so a false-alarm count can say which sensitivity raised it. `null` if that record did not report them. A change while the alarm is open does not close it or change these. |
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

## 7. Settings

The device owns its sensitivity and always reports the value it is using. The platform keeps only the value the dashboard wants. If the two differ, the device's value is what happened.

Sensitivity goes from 0 (least sensitive) to 1 (most), default 0.5. It moves how far outside normal counts as wrong: the model's `alarm_line`, and the `outside_normal` problems. It never changes the limits.

`PUT /api/devices/effente-01/settings` with `{"sensitivity": 0.7}` sets the wanted value. `{"sensitivity": null}` clears it. A value that is not a number from 0 to 1 is rejected with 400. A device the platform has not heard from yet can still have a value set; it gets it on first contact. Reply: `{"ok": true, "settings": {"sensitivity": 0.7}}`, or `"settings": null` after clearing.

While a value is set, every reply to message 2 and message 3 carries it. When it differs from the device's own value, the device applies it at once and keeps it across a restart. The next record or check-in shows the new value. Expect up to 2 seconds for the reply, then up to one summary period (up to a minute) before a diagnosis is judged at the new line. `outside_normal` problems still need about 10 seconds of holding before they show.

The device's own `/settings` on port 8765 and `data/settings.json` stay for bench use. While the dashboard has a value set, the next reply overwrites a local change. Clear the dashboard's value to tune on the device.

## 8. Scans, parts and the log

Added in version 3.4. Device authors can skip this section: the handheld sends nothing new. Three things use it: the phone scan app (track 6), the desktop app, and the part review agent.

Everything in Basics still holds. The work plan, and who builds which part, is in [docs/scan-and-log-plan.md](../docs/scan-and-log-plan.md).

**Not built yet.** Version 3.4 is wired in but not built. Until a part lands, each of its routes answers HTTP 400 with `{"ok": false, "error": "not built yet"}`, `GET /api/state` has no `scans` or `parts`, the stream sends none of the new events, and an alarm answer's `part_id` and `needs_repair` are stored and passed on like any unknown field.

Ids made by the platform (`scan_id`, `part_id`, `entry_id`) are opaque. They hold only letters, digits and `-`. Never build one yourself and never read meaning out of one.

| Word | Meaning |
|------|---------|
| Machine | One piece of equipment. Its `machine_id` is the one the probe already sends, for example `conveyor-07`. |
| Scan | One 3D capture of a machine, made by the phone. A machine can have several. The newest is the one shown. |
| Part | One functional part of a machine that can fail on its own: a motor, a bearing, a roller, a mount. |
| Pin | Where a part sits on a scan. A part can exist with no pin. |
| Entry | One line in the log: a finding, an inspection, a repair, a note, or an agent's review. Entries are only ever added. |
| Part state | What the log says about a part now: `needs_repair`, `possible`, `nothing_found` or `not_checked`. Worked out by the platform from entries and alarms. |
| Review | What the part review agent wrote for one part. Advice only. |

### 8.1 Hello (Cameron)

`GET /api/hello`

```json
{"ok": true, "service": "effente", "contract": "3.4", "now": "2026-11-14T18:15:02Z", "max_scan_bytes": 268435456}
```

The phone calls this to show "laptop found" before a scan starts. It is small and fast, unlike `/api/state`.

### 8.2 The QR code (Cameron draws it, Rohan reads it)

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

### 8.3 Upload a scan (Rohan sends, Cameron receives)

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
{"ok": true, "duplicate": false, "scan": {"...": "the scan, as in 8.4"}}
```

The receiver writes the body to disk as it arrives and never holds it in memory. A body that is too large or not a known file type gets a 400 and the connection is closed.

### 8.4 The scan (Cameron)

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

**Coordinates.** Every position in this section is in the coordinates of the converted file, `model.glb`: three numbers `[x, y, z]`, right-handed, Y up, in the file's own units. For a scan from Apple's Object Capture that should be metres (unverified). A position belongs to one scan. It means nothing on another scan of the same machine.

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

### 8.5 Parts (Jake)

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
| `position` | Where the pin is, or `null`. See Coordinates in 8.4. |
| `removed` | `true` hides the part. Parts are never deleted, because entries point at them. |
| `state` | Set by the platform. Never sent by a client. Rules in 8.7. |

| Route | Body | Reply |
|-------|------|-------|
| `POST /api/machines/conveyor-07/parts` | `name`, `kind`, `by`, and optionally `position`, `fault_types`, `note` | `{"ok": true, "part": {...}}` |
| `PUT /api/parts/part-0007` | `by`, and any of `name`, `kind`, `position`, `fault_types`, `note`, `removed` | `{"ok": true, "part": {...}}` |
| `GET /api/machines/conveyor-07/parts` | | `{"ok": true, "parts": [...]}`, in the order they were made. Add `?removed=1` to include removed parts. |

`GET /api/state` gains `"parts": {"conveyor-07": [...]}`, without removed parts. A machine with a part appears in `machines`.

The stream gains the event `part`, with the part as data, sent when a part is made or changed, and when its `state` changes.

### 8.6 Log entries (Jake)

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

**Answering an alarm with a part.** `POST /api/alarms/alarm-1234/answers` (section 6) may now also carry `part_id` and `needs_repair`. When `part_id` is there, the platform adds a `finding` entry for that part: `text` is the answer's `finding`, `alarm_id` is the alarm, `source` is `alarm_answer`. A `false` verdict always stores `needs_repair: false`. For a `real` verdict, a missing `needs_repair` counts as `true`. The alarm and its answers are unchanged.

### 8.7 Part state (Jake)

The platform works a part's state out again whenever an entry is added, an alarm changes, or a record arrives. First rule that fits wins:

| State | When | `since` |
|-------|------|---------|
| `needs_repair` | The part's newest `finding` or `inspection` says `needs_repair: true`, and no `repair` entry is newer than it. | That entry's `at` |
| `possible` | An alarm on this machine is open or has no answer, it started after the part's newest `repair` entry, and its `diagnosis` is in the part's `fault_types`. | The `start` of the oldest such alarm |
| `nothing_found` | The platform has at least one record from this machine newer than the part's newest `repair` entry, or newer than the part if it has none. | The `at` of the oldest such record |
| `not_checked` | Otherwise: the probe has not been on this machine since the part was made or repaired. | `null` |

`nothing_found` does not mean the part is healthy. It means the probe has been on the machine and nothing points at this part. The app must use those words.

A `review` entry never changes the state. Neither does a `note`.

### 8.8 The part review agent (Jake)

One run per part. Each run reads only that part's own history. It answers the four questions a planner asks: what is wrong, how that kind of fault comes about, how long it has been there, and whether it needs repair.

A person starts it, from a terminal or a button in the desktop app. Never over HTTP, as section 4 already says for the report agent.

The run is posted with message 4, with three differences: `agent` is `part_review`, there is a `part_id`, and the result is in `review` while `report` stays `null`.

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
| `how` | Not written by the model. The code copies the fault type's own `text` from the model announcement (message 1). If there is no fault type, it is empty. This keeps the agent from inventing a cause the readings do not show. |
| `since` | Not written by the model. The code copies the part's `state.since`. |
| `needs_repair` | `yes`, `no` or `not_sure`. Advice. A person turns it into a `finding` or `inspection` entry if they agree. |
| `cited_alarms`, `cited_entries` | Only ids that were in the history the agent was given. |

Checks in code, for every review, whoever wrote it. They follow the report agent's checks in [docs/TRD.md](../docs/TRD.md):

- The shape: these keys, these types, nothing extra.
- Every cited id was in the history supplied.
- Every number in `what` and `why` appears in the history supplied.
- `what` and `why` name no fault type and no part that is not in the history supplied.
- With no alarm and no entry that says `needs_repair: true`, `needs_repair` must not be `yes`.
- One retry on a failed check. A second failure is discarded and the run says why.

When a review passes, the command also adds a `review` entry to the log, so the review is part of the part's history.

The rule-based stand-in writes a review with no model, labelled as a stand-in and as simulated, exactly as the report agent's stand-in does. The local model is opt-in.

## Not in version 3.4

- No HTTP endpoint starts an agent, and the web dashboard cannot start one. A person starts it on the laptop: from a terminal (`python3 platform/report.py`), or with the Run Report Agent button in the desktop app, which runs the same command as a local process. The model runs on the laptop or elsewhere on the site, so there is no key, and nothing is sent off the site.
- Machine profiles and model delivery to the device. Planned in [docs/model-lifecycle.md](../docs/model-lifecycle.md).
- Answering an alarm on the device, reminders for unanswered alarms, and any use of answers by training or thresholds. Also planned there.
- More than one platform, or any security.
- Whether the agent may cite problems in its report, as it cites readings in `cited_features`. Open for track 5.
- Whether a problem should open an alarm, and be answerable like one. Open for track 5.
