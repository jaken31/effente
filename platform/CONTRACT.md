# Device and platform contract

Version 3.5. Section 9 was agreed by the whole team on Sat 14 Nov 2026. It is not a proposal any more, and it is still not built. Section 10 (setup) was added the same day and is not built either: see the note there for what is and is not agreed. Owner: track 5. This file is the whole interface between the handheld (or the simulated device), the laptop platform, the dashboard and the agent fleet. Build to it exactly. If it is wrong, tell track 5; do not work around it.

It follows the log record in [docs/TRD.md](../docs/TRD.md) and adds nine fields to it: `model_version`, `reason`, `problems`, `wrong`, `sensitivity`, `alarm_line`, `simulated`, and (set by the platform) `seq` and `part_id`.

## Changes from version 3.4

Additions only. A version 3.4 device, dashboard or phone app keeps working. It is in sections 9 and 10. The whole team agreed section 9 on Sat 14 Nov 2026. None of it is built: see the notes there.

- A person says which part the probe is on, and the platform stamps that machine's records with the `part_id` (section 9.1). The handheld changes nothing for this.
- Every record carries the vibration velocity in mm/s, in `readings.vib_velocity_mm_s` (section 9.2). This is the one change on the sending side.
- Readings: the platform sums up each visit of the probe to a part, and a route returns one part's readings over time with their average, highest and count (section 9.3). A reading also keeps the median `unknown_score` of its records.
- A normal spectrum, worked out by the platform from stored records judged normal (section 9.4).
- A projection: a straight line through the model's score in a part's recent readings, and when it meets the model's alarm line, as a range. Worked out by code. The chart beside it still plots velocity in mm/s, with the 11 mm/s limit as a labelled line (section 9.5).
- Simulated history: the simulator writes weeks of readings through the same routes, only to a separate sample machine with the fixed id `sample-machine`, and the `simulated` mark follows them into everything worked out from them (section 9.6).
- A part review may carry `trend`, copied by code from the projection (section 9.7).
- `GET /api/state` gains `probe`, and each part gains `reading`. The stream gains the events `probe` and `reading`.
- Setup (section 10): a machine profile of five fields with routes to store and read it, a lookup in code that picks the library entry, a `setup` agent that fills the profile and never picks the model, the chosen entry sent to the device as a wanted `library_entry` in `settings`, and a local profile on the laptop that fills `by`.

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
| `readings` | Optional block, described under message 3. From version 3.5 every record carries `readings.vib_velocity_mm_s`, the vibration velocity in mm/s (section 9.2). |
| `part_id` | Never sent by the handheld. The platform adds it when a person has said which part the probe is on (section 9.1). Missing means no part. |

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
| `agent` | Which agent in the fleet wrote the run: `maintenance_report`, `part_review` (section 8.8) or `setup` (section 10.3). |
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

From version 3.5 `settings` may also carry `library_entry`, the library entry wanted for the device's machine (section 10.4). It is set only by confirming a machine profile, never through the route above.

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
| `created` | Set by the platform: `at` is when the part was made, `by` who made it. A real part is always made now. |
| `simulated` | Only on a part the simulator made (9.6): `true`. Set when the part is made and never changed. A real part has no `simulated`. |

| Route | Body | Reply |
|-------|------|-------|
| `POST /api/machines/conveyor-07/parts` | `name`, `kind`, `by`, and optionally `position`, `fault_types`, `note`, `simulated`, and `at` | `{"ok": true, "part": {...}}` |
| `PUT /api/parts/part-0007` | `by`, and any of `name`, `kind`, `position`, `fault_types`, `note`, `removed`. Never `at` or `simulated`: either is a 400. | `{"ok": true, "part": {...}}` |
| `GET /api/machines/conveyor-07/parts` | | `{"ok": true, "parts": [...]}`, in the order they were made. Add `?removed=1` to include removed parts. |

**A past `at`, only for a simulated part.** With `"simulated": true`, the body may carry `at`, a time not in the future, and it becomes `created.at`. Rule 8.7 then counts records newer than that time, so a part the simulator makes today can be `nothing_found` from its weeks of older records. Without `"simulated": true`, any `at` is a 400 and nothing is stored: nothing can make a real part older. A bad or future `at` is a 400 too.

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

## 9. Part readings

Added in version 3.5. **Agreed by the whole team on Sat 14 Nov 2026. Not built yet.** Device authors need 9.2 only. The work plan, and who builds which piece, is in section 15 of [docs/scan-and-log-plan.md](../docs/scan-and-log-plan.md).

The page this section serves is drawn in [docs/design/machine-onepage.html](../docs/design/machine-onepage.html). The machine in that file is a drawing, not a scan, and every number in it is sample data, not a measurement. Its "alarm threshold 4.5 mm/s" is one of those: see 9.5 for the lines that are really used. Where the drawing and this section differ, this section wins.

Until a piece is built, its routes answer HTTP 400 with `{"ok": false, "error": "not built yet"}`, `GET /api/state` has no `probe`, parts have no `reading`, the stream sends neither new event, and `GET /api/hello` still says `"contract": "3.4"`.

Every number called a starting value below was picked by reasoning, not checked on a real machine. All of them are unverified.

| Word | Meaning |
|------|---------|
| Probe part | The part a person says the probe is on now. One per machine, or none. |
| Visit | One stay of the probe on one part: that part's stamped records, with no gap longer than 10 minutes between them. |
| Reading | What the platform works out from one visit: one velocity in mm/s and one model score, with when and how many records. One point on a part's chart. |
| Normal spectrum | The usual level of each of the 20 vibration bands, worked out from stored records judged normal. |
| Projection | A straight line through the model's score in a part's recent readings, and when it would meet the alarm line. |
| Alarm line | The record's `alarm_line` (message 2): the `unknown_score` above which the model counts a record as abnormal. This is what raises an alarm. |
| Limit line | 11 mm/s, the `vibration_over_limit` limit in the problems table. Drawn on the chart and labelled. The projection is not drawn against it. |

### 9.1 Which part the probe is on

The handheld does not know which part it sits on, and it is not changed. A person picks the part in the desktop app, before or while the probe is on it, and the platform stamps the records.

`PUT /api/machines/conveyor-07/probe`

```json
{"part_id": "part-0007", "by": "Sam"}
```

`{"part_id": null, "by": "Sam"}` clears it. Reply:

```json
{"ok": true, "probe": {"machine_id": "conveyor-07", "part_id": "part-0007", "since": "2026-11-14T16:01:30Z", "by": "Sam"}}
```

After clearing, `"probe": null`.

| Rule | |
|------|---|
| One per machine | The selection belongs to the machine, not to a device. Picking another part replaces it. |
| The part | Must be a part of this machine that is not removed. Anything else is a 400. |
| Stamping | While a selection is set, the platform adds its `part_id` to each record of that machine before storing it. A stored record's `part_id` never changes afterwards. |
| Catching up | A record whose `at` is more than 60 seconds before the selection's `since` is not stamped. It was measured before the person picked, and arrived late after a dropout. The 60 seconds allow for the device's clock and the laptop's differing (starting value). |
| When it ends | When a person changes or clears it, or when the platform has stored no record from that machine for 5 minutes (starting value), counted from `since` if none came at all. The platform then clears it itself. This stops an old pick from stamping a later visit to another part. |
| A restart | The selection is kept across a platform restart, and the 5 minute rule still applies. |
| Already stamped | A record that arrives carrying `part_id` keeps it, if that part belongs to the record's machine. Otherwise the record is a 400. Only the simulator and imports do this (section 9.6). The handheld never sends it. |

**Records with no part.** A record stored while no part is picked has no `part_id`. Nothing else about it changes: it is stored, shown in the machine's history, and counts for alarms (section 6) and for part state (section 8.7) exactly as in version 3.4. It belongs to no visit and no reading. The platform never gives it a part later, and never guesses one. The app says so while records arrive with no part picked.

**What the stamp means.** It is where a person said the probe was. It is not proof, and it does not say the fault is in that part: the rule in 8.5 still holds, that a part is a suspect only because a person linked a fault type to it. If two devices report on one machine at once, both are stamped with the same part. That case is not handled in version 3.5.

`GET /api/state` gains `"probe": {"conveyor-07": {"...": "the selection"}}`, holding only machines with a selection. The stream gains the event `probe`, with `{"machine_id": "conveyor-07", "probe": {"...": "the selection, or null"}, "reason": ""}` as data, sent when a selection is set, changed or cleared. `reason` is `no records for 5 minutes` when the platform cleared it, empty otherwise.

### 9.2 Velocity on every record

Today a stored record has no vibration velocity in mm/s. `features.vib_velocity_mm_s` is in spreads of normal, not mm/s, and is there only when that reading is among those that moved most. The mm/s value reaches the platform only as the `value` of a `vibration_over_limit` problem, and in the `readings` of a check-in.

The change: every record sent while watching carries the feature row's own value, in mm/s, in the optional `readings` block that message 3 already describes.

```json
"readings": {"vib_velocity_mm_s": 3.1}
```

| Rule | |
|------|---|
| Value | `vib_velocity_mm_s` from the device's feature table: vibration velocity, 10 to 250 Hz rms. A partial band, so it is not an ISO 10816 number and must not be shown as one. `null` when the sensor gave none. |
| When | Every record, whatever the diagnosis, including `normal`, `warming_up` and `stopped`. |
| Other keys | `readings` may hold its other keys as before. Only this key is asked for. |
| Who makes the change | The sender, because only it has the value. The platform stores it as it comes and works nothing out. |

There are three senders, and each needs a few lines (read from the code on 14 Nov, not run):

- `platform/link/device_link.py` (track 5) already puts this value into a check-in's `readings`. It does the same for a record.
- `device/intake/uploader.py` (track 1) has the feature row in hand when it builds a record. It adds the block. This is the only change asked of track 1.
- The simulator (`platform/sim/`) adds it to every record it makes.

A record without the value is still stored. It counts toward no reading's velocity.

Considered and not proposed: working the velocity out on the platform from the record's `spectrum`. It needs no sender change, but it would be an estimate from 20 wide bands shown beside the device's own number. Not tried.

### 9.3 Readings, and one part's history

The device sends one record a second. A part's chart needs one point per visit. The platform makes that point.

- A part's stamped records are taken in order of `at`. A gap of more than 10 minutes between two neighbours starts a new visit (starting value).
- Records with `diagnosis` `stopped`, or with no velocity, are left out: a machine that is not running would pull the number down.
- A visit with at least 10 records left makes a reading (starting value). A shorter one makes none.
- The reading's velocity is the median of those records' `readings.vib_velocity_mm_s`. A median, so a knock while the probe is being placed does not move it.
- The reading's score is the median of the same records' `unknown_score`, leaving out the records where it is `null` (the model is still `warming_up`). This is the number the projection fits (9.5).
- Readings are worked out, never stored, and worked out again from the stored records when the platform starts. This is how alarms already work.

```json
{
  "part_id": "part-0007",
  "machine_id": "conveyor-07",
  "start": "2026-11-14T16:01:41Z",
  "end": "2026-11-14T16:09:40Z",
  "records": 466,
  "velocity_mm_s": 3.1,
  "unknown_score": 2.4,
  "alarm_line": 1.0,
  "model_version": "conveyor-07-20261114",
  "abnormal": true,
  "alarm_ids": ["alarm-1234"],
  "open": false,
  "simulated": false
}
```

| Field | Rule |
|-------|------|
| `start`, `end` | `at` of the visit's first and last record. A reading is named by its `part_id` and `start`; it has no id of its own. |
| `records` | How many records the velocity was taken from. |
| `velocity_mm_s` | The median, rounded to 2 places. |
| `unknown_score` | The median of the records' `unknown_score`, rounded to 2 places. `null` if no record of the visit had one. It has no unit: it is the model's score divided by its threshold (message 2). |
| `alarm_line`, `model_version` | From the visit's newest record. `alarm_line` is `null` if that record reported none. The projection needs both: the line to project to, and which model the score belongs to. |
| `abnormal` | `true` if any record of the visit was abnormal, as section 6 defines it. |
| `alarm_ids` | The alarms those abnormal records belong to. |
| `open` | `true` while the visit's newest record is less than 10 minutes old, so the reading can still move. |
| `simulated` | `true` if any record of the visit was simulated. |

`GET /api/parts/part-0007/readings?since=2026-09-15T00:00:00Z&limit=300`

```json
{
  "ok": true,
  "part_id": "part-0007",
  "machine_id": "conveyor-07",
  "readings": [{"...": "readings, oldest first"}],
  "summary": {
    "count": 57,
    "average_mm_s": 2.14,
    "highest": {"velocity_mm_s": 3.1, "at": "2026-11-14T16:01:41Z"},
    "newest": {"velocity_mm_s": 3.1, "at": "2026-11-14T16:01:41Z"},
    "simulated": true
  },
  "projection": {"...": "as in 9.5"}
}
```

| Thing | Rule |
|-------|------|
| `since` | Optional. Only readings whose `start` is at or after it. A bad one is a 400. |
| `limit` | The newest `limit` readings, default 300, at most 2,000. |
| `summary` | Over the readings returned, so it follows `since`. `average_mm_s` is the plain average of their velocities: each reading counts once, however long the visit. `highest` is the reading with the highest velocity and `newest` the last one, each with its `start` as `at`. With no readings, `count` is 0 and the other three are `null`. `simulated` is `true` if any reading returned is. |
| `projection` | Always from its own window (9.5), whatever `since` says. |
| A removed part | Still answers. An unknown `part_id` is a 400. |

Each part (8.5) gains `reading`, set by the platform: its newest reading as `{"velocity_mm_s": 3.1, "at": "2026-11-14T16:01:41Z", "open": true, "simulated": false}`, or `null` if it has none. This is the number beside each part in the list.

The stream gains the event `reading`, with `{"part_id": "part-0007", "machine_id": "conveyor-07", "reading": {"...": "the reading"}}` as data. It is sent when a reading is first made and when it closes, and at most once every 10 seconds while it grows. A record a second must not mean an event a second.

### 9.4 The normal spectrum

`GET /api/machines/conveyor-07/normal-spectrum?part=part-0007`

```json
{
  "ok": true,
  "machine_id": "conveyor-07",
  "part_id": "part-0007",
  "ready": true,
  "records": 1840,
  "needed": 300,
  "model_version": "conveyor-07-20261114",
  "spectrum": [-4.71, -4.60, -4.64],
  "spectrum_edges_hz": [5.0, 6.3, 7.9],
  "from": "2026-11-14T15:20:02Z",
  "to": "2026-11-14T16:00:40Z",
  "simulated": false
}
```

(The two lists are shortened, as in messages 1 and 2.)

A record is **judged normal** when its `diagnosis` is `normal`, its `wrong` is `false` (so no problem is on either), and it carries a `spectrum` of 20 numbers. The judging is the device's. The platform judges nothing itself.

- Only records with the `model_version` of the machine's newest record are used. A recalibration or a retrained model starts the normal again, because what counts as normal changed.
- The normal is the median of each band over the newest such records, at most 2,000 of them. Same unit as `spectrum` in message 2: log10 band levels. The drawing's mm/s spectrum axis is a drawing.
- It needs 300 records judged normal: five minutes of normal running at one record a second. A starting value. Whether five minutes gives a steady median is not checked.
- With `part`, only records stamped with that part are used, and `part_id` in the reply names it. Without it, every judged-normal record of the machine is used, wherever the probe was, and `part_id` is `null`.
- Real and simulated records are never mixed. If the machine has any real record judged normal, only real ones are used. Otherwise simulated ones are, and `simulated` is `true`. This still matters outside the sample machine (9.6): the live simulated device reports on a machine of its own choosing.

**Before there are enough.** `ready` is `false`, `spectrum` is `null`, and `records` and `needed` say how far along it is. The app then draws the current bars alone, with no normal line, and the words "Normal not known yet: 120 of 300 normal records". It never draws a line it does not have.

The app asks for the selected part's normal first. If that is not ready it may show the machine's, labelled "normal for the whole machine, not for this part".

### 9.5 The projection

Worked out by code, in the receiver, from readings alone. A language model never produces, rounds or rewords any number in it.

The projection is drawn against the model's score reaching its alarm line, because that is what raises an alarm (section 6). It fits each reading's `unknown_score` (9.3), not its velocity.

```json
{
  "status": "rising",
  "fits": "unknown_score",
  "line": {"name": "alarm_line", "unknown_score": 1.0},
  "window": {"from": "2026-11-02T09:10:00Z", "to": "2026-11-14T16:01:41Z", "readings": 12, "days": 12},
  "now_score": 0.62,
  "slope_per_day": 0.005,
  "slope_range": [0.0036, 0.0075],
  "show_days": true,
  "reaches": {"days": [51, 106], "earliest": "2027-01-04", "latest": "2027-02-28"},
  "simulated": true
}
```

(The numbers show the shape. They are not from a machine.)

The rule, in order:

1. **Window.** The part's readings from the last 14 days that have an `unknown_score`. None from before the part's newest `repair` entry: a repair starts the trend again. None with a `model_version` other than the newest reading's: a recalibration or a retrained model changes what the score means, so it starts the trend again too, as it does for the normal spectrum (9.4). 14 days is a starting value. `window.days` is how many different days (UTC) have a reading.
2. **The alarm line.** `line.unknown_score` is the `alarm_line` of the newest reading in the window. A change of sensitivity moves the line, not the scores, so the newest line is the one a future record will be judged at. If that reading has no `alarm_line` (a device older than version 3.1), `status` is `not_enough_history`.
3. **Not enough history.** If the window holds readings on fewer than 3 different days, `status` is `not_enough_history` and every field after `window` except `show_days` and `simulated` is `null`. The app says "Not enough history to say where this is heading: a trend needs readings on at least 3 different days." 3 days is a starting value: it is the fewest points for which a range can be worked out at all, and the range it gives is very wide.
4. **The fitted line.** Least squares through the readings: time in days against `unknown_score`. `slope_per_day` is its slope and `now_score` its value at the newest reading's time.
5. **The range.** `slope_range` is the slope plus and minus its standard error times Student's t for an 80% range with n minus 2 degrees of freedom. 80% is a starting value.
6. **The status.** First row that fits wins.

| `status` | When | `reaches` | The app says |
|----------|------|-----------|--------------|
| `over_line` | The newest reading's `unknown_score` is at or above the alarm line. | `null` | "At or above the model's alarm line now" |
| `rising` | Both ends of `slope_range` are above 0. | From `now_score` to the alarm line, at the steepest and the gentlest slope. Whole days and dates (UTC). Past 365 days, that end is `null`. | "On this trend the model's score reaches its alarm line in 51 to 106 days (4 Jan to 28 Feb)", or "in more than a year" |
| `no_clear_rise` | `slope_range` holds 0. | `null` | "No clear rise yet: the change is inside the day-to-day spread" |
| `not_rising` | Both ends are at or below 0. | `null` | "Level or falling over the last 12 days" |

`simulated` is `true` if any reading in the window is.

**The check on run-to-failure data, and "trend".** A straight line through a score has not been checked against any real fault. The team agreed one check: fit this same rule to run-to-failure bearing data and see how well the days it gives match what happened. The check has not been run (unverified), and what counts as coming out poorly is not written down yet. Write that down before running it, not after.

`show_days` is a fixed value in the receiver's code, not something worked out from a part's readings:

- `true`: the app calls it a projection and shows the days and dates, as in the table.
- `false`, set if the check comes out poorly: the app calls it "Trend", never "Projection". `reaches` is `null` for every status, and no days figure and no date is shown anywhere. For `rising` the app says "Rising toward the model's alarm line". The other three sentences stay as they are.

Either way the app says under it: "a straight line through the model's score over the last N days, worked out by code, not checked on real faults". Those last five words go only when the check has been run and has come out well.

**What the chart shows.** The projection does not change the chart.

- The chart still plots each reading's `velocity_mm_s`, in mm/s.
- It still shows the limit line, 11 mm/s, labelled "limit for any machine, 11 mm/s". Never "alarm threshold": the drawing's "alarm threshold 4.5 mm/s" is wrong and is not to be built.
- The projection's own numbers are scores with no unit. They are never drawn on the mm/s axis: no dotted line and no fan through the velocity points. The projection is shown as its sentence under the chart. Whether the page also draws the score over time on a strip of its own is the Machine page's to settle (M1). Not decided here.

**Why the alarm line.** An alarm (section 6) opens when the model's `unknown_score` crosses `alarm_line`. A planner asks "when will this alarm", and only the score can answer that. The 11 mm/s limit raises a problem, not an alarm, and the model may alarm long before it.

What this leaves open, all unverified:

- The score has no unit, and it describes the whole machine as the model learned it at one probe position. Whether its median differs from part to part in a way that means anything is not checked.
- A record counts as abnormal only after several periods over the line (section 6). So the day the fitted line crosses is not exactly the day an alarm would open.
- `unknown_score` is `null` while the machine is `stopped` or the model is `warming_up`. A visit made only of such records gives a reading with no score, and that reading is left out of the window.
- A straight line is the simplest honest model, not a model of how faults grow.
- 11 mm/s is itself a starting value (`effente/safety.py`), and the velocity is a partial band. A machine that passed the limit while learning has that limit dropped on the device. The platform is not told, so the chart would still draw the line.

### 9.6 Simulated history

For the hackathon the simulator writes weeks of per-part history, so the page has something to draw. It uses the routes above and nothing else: no file is written behind the receiver's back.

**Only on the sample machine.** Simulated history is written to one machine, kept apart for it, with the fixed `machine_id` `sample-machine`. Never to any other machine, and never to a machine that has any real record.

- The history command takes no machine id. It always writes to `sample-machine`.
- The receiver keeps the sample machine free of real records: a record for `sample-machine` without `"simulated": true` is a 400. So is a record from `device_id` `sim-history` for any other machine.
- Before it writes, the simulator still reads the sample machine's records. If it finds one that is not simulated (stored before this rule was built), it refuses and says so.
- The app shows the machine as "Sample machine (simulated)" wherever machines are listed. It is not the demo conveyor, and its history says nothing about any real machine.

The id `sample-machine` and the two refusals in the receiver were chosen when this rule was written down. The team agreed the rule, not those details: confirm them (unverified).

| What it writes | Through | Marked |
|----------------|---------|--------|
| Parts, if the sample machine has none | `POST /api/machines/sample-machine/parts` (8.5), with `by` as `simulator`, `"simulated": true`, and an `at` no later than its oldest record | The part's `simulated`, set when it is made and never changed |
| Records, in visits | `POST /api/records` (message 2), each with `part_id`, a past `at`, `readings.vib_velocity_mm_s`, a `spectrum`, and `"simulated": true` | The record's `simulated` |
| Past findings and repairs | `POST /api/machines/sample-machine/log` (8.6), with a past `at` and `"simulated": true` | The entry's `simulated` |

Two small additions make this possible. A record may arrive already stamped (9.1). And the log route accepts `"simulated": true` in the body: the platform keeps a `true` it is given, and still sets `true` itself where 8.6 says so. Nothing can set it back to `false`.

Rules for the simulator:

- **Its own device.** History is sent as `device_id` `sim-history`, never as the live simulated device, so the live device's newest record in `/api/state` is not replaced by one from weeks ago. That device will show as connected for 5 seconds after the last batch. Not checked: how the Live page behaves meanwhile.
- **Oldest first.** Alarms are found in the order records are stored (section 6), so a normal record from the past, stored now, would close an alarm that is open now. The sample machine has no live device, so this can only happen between two runs of the history command. The simulator reads `/api/state` first and refuses if the sample machine has an open alarm or a connected device.
- **Short visits.** A visit is 30 records a second apart, not weeks of one record a second. One visit a part a day for 60 days is 1,800 records a part. Records go in arrays of at most 500.
- **No undo.** There is no route that deletes records. Simulated history is removed by clearing the receiver's data folder, which removes every real record too. Until then the sample machine stays in the list of machines.

**The mark is in the data, and it travels.** A record's `simulated` makes its reading's `simulated`, which makes `simulated` in the part's `reading`, the `summary`, the `projection`, the normal spectrum and, as section 4 already says, the review. None of these can be `false` when something under it is `true`.

**The app shows it wherever a reading is drawn.** These are requirements, not suggestions:

- The top bar's "Simulated data" chip is on whenever anything on the page is simulated, as today.
- Every panel that draws a simulated reading says "Simulated" in its header: the chart, the tiles, the spectrum and the review.
- A simulated point on the chart is drawn hollow, a real one filled, so a chart with both can be read.
- In the parts list, a number from a simulated reading has "simulated" after it.
- A projection with `simulated: true` begins with the word "Simulated:".
- An exported CSV keeps a `simulated` column.

### 9.7 The part review and the projection

The part review agent (8.8) gets the part's readings summary and its projection in the history it is given, beside the entries and alarms.

A review may carry one more key:

| Field | Rule |
|-------|------|
| `trend` | Not written by the model. The code builds it from the projection: the sentence in the table in 9.5 for its `status`, with "Simulated:" in front when the projection is simulated. Empty when the part has no readings. |

The checks in 8.8 hold, with `trend` allowed in the shape. Two of them now reach further:

- Every number in `what` and `why` appears in the history supplied. The readings summary and the projection are part of that history, so the model may quote them and nothing else.
- With no alarm and no entry that says `needs_repair: true`, `needs_repair` must not be `yes`. A rising projection does not change this. A trend alone is never a reason to repair.

## 10. Setup

Added in version 3.5, on Sat 14 Nov 2026. **Not built yet.** Written from track 5's brief on the day section 9 was agreed. This file does not record the whole team agreeing this section (unverified), and its device side, 10.4, waits for review by tracks 1 and 2. Device authors need 10.4 only. The work plan, and who builds which piece, is in section 16 of [docs/scan-and-log-plan.md](../docs/scan-and-log-plan.md).

It builds the smallest useful part of [docs/model-lifecycle.md](../docs/model-lifecycle.md) (Machine profile, Setup agent, Model selection): five of that doc's ten profile fields, the lookup, and the name of the chosen entry sent to the device. No model file is delivered and no day-0 rule is set from the profile.

Until a piece is built, its routes answer HTTP 400 with `{"ok": false, "error": "not built yet"}`, `GET /api/state` has no `profiles`, `settings` carries no `library_entry`, and the stream sends no `profile` event.

| Word | Meaning |
|------|---------|
| Machine profile | Five facts about one machine, given by a person. Kept by the platform, one per machine. |
| Library entry | A starting model in track 3's library, named by its `entry_id`: today `conveyor` and `generic_rotating`. |
| Lookup | The code that picks a library entry from a machine profile. A table, not a model. |
| Wanted entry | The library entry the lookup picked for a machine, once a person has confirmed the profile. What the device is asked to use. |
| Local profile | A name and a role kept on one laptop, so the app can fill `by`. Not a login. |

"Machine profile" and "local profile" are two different things. Always use both words.

### 10.1 The machine profile

```json
{
  "machine_id": "conveyor-07",
  "machine_type": "conveyor",
  "drive": "belt",
  "speed_rpm": null,
  "mounting": "rigid",
  "sensor_location": null,
  "confirmed": {"at": "2026-11-14T20:04:10Z", "by": "Sam"},
  "run_id": "setup-20261114-200300-conveyor-07",
  "choice": {"...": "what the lookup picked, as in 10.2"}
}
```

| Field | Values | Used for |
|-------|--------|----------|
| `machine_type` | `conveyor`, `fan`, `pump`, `motor`, `other` | The lookup |
| `drive` | `electric_motor_direct`, `belt`, `gearbox` | The lookup |
| `speed_rpm` | A number above 0, or a range as `[low, high]` | Nothing yet. Kept for where the once-per-turn peak should sit. |
| `mounting` | `rigid`, `flexible` | Nothing yet |
| `sensor_location` | `motor_drive_end`, `gearbox`, `frame` | Nothing yet |
| `confirmed` | Set by the platform: who confirmed, from the body's `by`, and when | |
| `run_id` | The setup run the profile came from, or `null` if a person filled the form by hand | |
| `choice` | Set by the platform when the profile is stored (10.2). Never sent by a client. | |

- **Unknown stays empty.** A field the person does not know is `null`. A missing key means the same. Nothing fills it in later, and nobody guesses a number. An empty field is not an error.
- **The value lists** are the examples in the lifecycle doc's Machine profile table, taken as starting lists. Nobody has checked that they cover real machines (unverified). A value outside its list is a 400.
- **The other five fields** of the lifecycle doc's profile (`size_class`, `operating_modes`, `schedule`, `room_noise`, `site_id`) are not in version 3.5. Sent anyway, they are stored and passed on like any unknown field.

| Route | Body | Reply |
|-------|------|-------|
| `PUT /api/machines/conveyor-07/profile` | Any of the five fields, `by`, and optionally `run_id` | `{"ok": true, "profile": {...}}` |
| `GET /api/machines/conveyor-07/profile` | | `{"ok": true, "profile": {...}}`, or `"profile": null` if the machine has none |

Storing a profile is confirming it. The app sends the `PUT` only after a person has looked at the five fields and said yes (10.3). There is no stored draft: before that click the profile lives only in the app, or in a setup run.

A `PUT` replaces the whole profile: a field left out becomes empty. Profiles are only ever added to the store, as alarm answers are, and the newest one counts. `by` is required.

`GET /api/state` gains `"profiles": {"conveyor-07": {"...": "that machine's profile"}}`, holding only machines that have one. A machine with a profile appears in `machines`. The stream gains the event `profile`, with the profile as data, sent when one is stored.

### 10.2 The lookup

The library entry is picked by code. No agent and no model takes part, so the choice is predictable, can be tested, and a wrong one can be traced.

`POST /api/library/lookup`, with any of the five fields as the body, answers what the lookup would pick. It stores nothing and sends nothing to any device. The app uses it to show the choice before the person confirms.

```json
{
  "ok": true,
  "choice": {
    "library_entry": "conveyor",
    "version": "0.1.0",
    "reason": "machine type is conveyor",
    "status": "demo",
    "trained_on": [{"name": "sim_conveyor", "kind": "simulated"}],
    "checked_on": [],
    "not_checked_on": [{"name": "sim_server_rack", "kind": "simulated", "note": "wrong machine: a server rack, for the fit check"}],
    "fault_types_validated": [],
    "fault_types_not_validated": 21
  }
}
```

(The example is what the `conveyor` entry's manifest said on `master` at `78d726b`, read on 14 Nov. It is trained on simulated data only, and checked on nothing.)

**The rule.** Match on `machine_type`, then on `drive`. No match, or an empty `machine_type`: `generic_rotating`. With the two entries that exist today that comes to one line: `machine_type` `conveyor` picks `conveyor`, everything else picks `generic_rotating`. `drive` decides nothing until two entries share a machine type. `speed_rpm`, `mounting` and `sensor_location` never change the choice in version 3.5.

| Field | Rule |
|-------|------|
| `library_entry` | The entry's `entry_id`. |
| `reason` | One sentence from a fixed list in the code, naming the profile field that decided: "machine type is conveyor", "no entry for machine type pump, so the general one", "machine type not given, so the general one". |
| `version`, `status` | The manifest's `version` and `status`, as they are. Today `status` is `demo` for `conveyor` and `unverified` for `generic_rotating`. |
| `trained_on` | The manifest's `sources` with `used_for` `training`: `name` and `kind`. |
| `checked_on` | Its `sources` with `used_for` `evaluation` and `evaluated` `true`. Empty for both entries today. |
| `not_checked_on` | Its `sources` with `used_for` `evaluation` where `evaluated` is not `true`, with the `note` if there is one. |
| `fault_types_validated` | The names in the manifest's `scorecard` whose value is not `not validated`. Empty for both entries today. No entry has a validated type yet, so what a validated value looks like is unverified. |
| `fault_types_not_validated` | How many names in the `scorecard` say `not validated`. 21 for both entries today. |

Everything after `reason` is copied by code from `training/library/entries/<entry_id>/manifest.json`, which the platform only reads. Nothing in it is written, rounded or reworded by a model. An entry whose manifest cannot be read is never picked.

The app shows the entry, the reason, the status and both lists, in these words: "trained on", "checked on", "not checked on". Trained on is not checked on, and the app must not merge them. An empty `checked_on` reads "Checked on: nothing yet".

Where the table lives is open. The lifecycle doc puts it with the library (track 3), and says each entry names the profile fields it suits. The manifests have no such field today. Until track 3 adds one, the table is a few lines in the platform's code, marked as a starting value. Tell track 3.

### 10.3 The setup agent

The Setup page asks a few guided questions, in plain words, and "I don't know" is an answer. Which questions, and how they are worded, is the page's (plan section 16, U5). Then one agent run reads the answers, fills the five fields, and explains what it understood.

**The agent never picks the model.** It is not given the library, the lookup table or any manifest, and its output has no place to name an entry. The lookup (10.2) picks, in code, after the agent is done.

A person starts the run, from a terminal or the Setup page. Never over HTTP, as section 4 says. One run per machine at a time.

The run is posted with message 4, with two differences: `agent` is `setup`, and the result is in `setup` while `report` stays `null`.

```json
{
  "run_id": "setup-20261114-200300-conveyor-07",
  "agent": "setup",
  "machine_id": "conveyor-07",
  "at": "2026-11-14T20:03:00Z",
  "status": "done",
  "model": "local: small.gguf",
  "ok": true,
  "reason": "",
  "problems": [],
  "tool_calls": [{"at": "2026-11-14T20:03:01Z", "name": "get_answers", "input": {}, "output": "{\"answers\": 5}"}],
  "report": null,
  "setup": {
    "profile": {"machine_type": "conveyor", "drive": "belt", "speed_rpm": null, "mounting": "rigid", "sensor_location": null},
    "explanation": "You said it is a belt conveyor bolted to the floor. You did not know its speed or where the sensor sits, so I left those empty.",
    "choice": {"...": "as in 10.2"}
  },
  "simulated": false
}
```

| Field | Rule |
|-------|------|
| `profile` | Written by the model. The five fields, each a value from its list in 10.1 or `null`. |
| `explanation` | Written by the model. What it took from the answers, and which fields it left empty and why. |
| `choice` | Not written by the model. After the checks pass, the code runs the lookup on `profile` and copies the result in. When the profile is confirmed the platform runs the lookup again (10.4), and that one counts. |

Checks in code, for every run, whoever wrote it:

- The shape: these keys, these types, nothing extra. So no key can carry an entry.
- Each field is a value from its list, or `null`.
- A `speed_rpm` that is not `null` appears as a number in the person's answers. The agent never guesses a speed.
- Every number in `explanation` appears in the answers.
- One retry on a failed check. A second failure is discarded and the run says why. The person then fills the five fields by hand.

**The stand-in.** When no model server runs, a rule-based stand-in fills the profile: it looks for a fixed list of words in the answers, and leaves a field empty when it finds none. Its explanation begins "Stand-in:" and the run is `simulated: true`, exactly as the report agent's stand-in. The local model is opt-in.

**Nothing is stored or sent by the run.** The run posts only itself. The app shows the five fields, the explanation and the choice. The person can change any field, which asks the lookup again. Only their "confirm" sends the `PUT` of 10.1.

### 10.4 The wanted library entry

Device authors: this is the part that reaches you.

When a profile is stored, the platform runs the lookup on it, keeps the result as the profile's `choice`, and makes `choice.library_entry` the wanted entry for that machine. From then on, every reply to message 2 and message 3 from a device whose `machine_id` is that machine carries it in the same `settings` that carries the sensitivity (section 7):

```json
{"ok": true, "stored": 1, "need_model": false, "settings": {"sensitivity": 0.7, "library_entry": "conveyor"}}
```

`settings` is there when at least one of the two is set, and holds only the keys that are.

| Rule | |
|------|---|
| Who sets it | Only a confirmed profile. Nothing is sent to a device before a person confirms. `PUT /api/devices/{device_id}/settings` does not take `library_entry`: sending it there is a 400. |
| Per machine | The wanted entry belongs to the machine, not to a device. A device gets it while it reports that `machine_id`, and stops getting it when it moves to a machine with no confirmed profile. |
| What the device reports | The entry it is using, in message 1's `library_entry`, as it does today. The platform never assumes the wanted entry was taken. |
| When they differ | The device's entry is what happened, as with sensitivity. The app shows both: "Wanted: conveyor. Device is using: generic_rotating." |
| What the device does with it | Not decided here. It is piece D1 in the plan, for tracks 1 and 2 to review. Changing the entry means calibrating again (each manifest's `calibration.min_s` is 300 seconds), so it cannot be applied at once the way a sensitivity is. |
| An entry the device does not hold | The device keeps its own and goes on reporting it. |
| Until D1 is built | The device does nothing with the key, and the app shows the difference. |

`GET /api/state` gains two things per device: `library_entry`, the entry the device reports (from the message 1 for its `model_version`, or `null`), and `settings.library_entry`, the wanted one. The `settings` event carries both the same way.

Not checked, so unverified: that a version 3.4 device ignores the new key. Read from the code on 14 Nov, not run: `device/intake/uploader.py` hands the whole `settings` to the device's own handler, which was not read, and `platform/link/device_link.py` reads only `sensitivity`.

### 10.5 The local profile

So that a person does not type their name into every answer, entry and pick, the desktop app keeps a local profile: a name and a role.

| Rule | |
|------|---|
| Where | On that laptop only, in the app's own settings. The platform has no route for it and never stores it. |
| No password | There is none, and nothing to check one against. Anyone at the laptop can pick or change the local profile. |
| What it fills | `by`, everywhere the app sends one: alarm answers, parts, log entries, the probe's part, a confirmed machine profile. `by` is the name. |
| The role | Shown in the app beside the name. Not sent to the platform in version 3.5. |
| With none set | `by` falls back to what the app sends today: the `EFFENTE_ANSWERER` environment variable, else the laptop's login name. |

**It is not security, and nothing may call it that.** It does not prove who did something: `by` is still "the name the person gave", as sections 6 and 8.6 say. No screen, doc, commit or pitch calls it a login, an account, signing in, access control or security. The words are "local profile".

## Not in version 3.5

- No HTTP endpoint starts an agent, and the web dashboard cannot start one. A person starts it on the laptop: from a terminal (`python3 platform/report.py`), or with the Run Report Agent button in the desktop app, which runs the same command as a local process. The model runs on the laptop or elsewhere on the site, so there is no key, and nothing is sent off the site.
- Delivery of a model file, the machine profile or day-0 rule settings to the device. Section 10 sends only the name of a library entry. The rest of the machine profile (five more fields) is not in either. Planned in [docs/model-lifecycle.md](../docs/model-lifecycle.md).
- The fit check asking the person a question, and re-picking an entry after it fails. Also planned there.
- Answering an alarm on the device, reminders for unanswered alarms, and any use of answers by training or thresholds. Also planned there.
- More than one platform, or any security. The local profile (section 10.5) is a name, not security.
- Whether the agent may cite problems in its report, as it cites readings in `cited_features`. Open for track 5.
- Whether a problem should open an alarm, and be answerable like one. Open for track 5.
- Giving a part to records after they are stored, or correcting a wrong pick. A wrong pick stays in that part's history.
- The device knowing its part, or clearing the pick when it feels itself moved (its `device_moved` fault type). Open for tracks 1 and 5.
- A projection against the device's own `vibration_above_normal` line. It needs the learned levels in message 1, which is a device change. Open for all five.
- A projection of anything but the model's score: velocity, temperature, sound.
- A normal spectrum in mm/s, and any use of records from alarms answered as false alarms to widen the normal.
- Part state (8.7) using a part's own readings. `nothing_found` still means the probe was on the machine, not on the part. Open for track 5.
- Deleting records, simulated or real.
