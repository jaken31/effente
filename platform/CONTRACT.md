# Device and platform contract

Version 2.2, pending the team's sign-off. Owner: track 5. This file is the whole interface between the handheld (or the simulated device), the laptop platform, the dashboard and the agent fleet. Build to it exactly. If it is wrong, tell track 5; do not work around it.

It follows the log record in [docs/TRD.md](../docs/TRD.md) and adds four fields to it: `model_version`, `reason`, `simulated`, and (set by the platform) `seq`.

## Changes in version 2.2

Additions only. A version 2 device keeps working; the dashboard shows its sensitivity as not reported.

- `unknown_score` is compared with `alarm_line`, not with 1. They are equal only at the default sensitivity, 0.5.
- Records, message 1 and the check-in say the sensitivity the device is running at.
- The dashboard can set a device's sensitivity. The device picks it up from the replies it already gets (section 7). Section 6 is kept for alarms, which version 2.1 adds.

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
| `unknown_score` | The smoothed score divided by the threshold. Above `alarm_line` is abnormal. `null` while `warming_up` or `stopped`. Sensitivity does not change it, only the line it is compared with. |
| `sensitivity` | 0 (least sensitive) to 1 (most), the value this record was judged at (section 7). Missing means not reported: the dashboard then says so and draws no line. |
| `alarm_line` | The `unknown_score` above which this record counts as abnormal: `4 ** (1 - 2 * sensitivity)`, so 4 at 0, 1 at the default 0.5, and 0.25 at 1. Sent with `sensitivity`. |
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

Once the dashboard has set a sensitivity for this device, the reply also carries `"settings": {"sensitivity": 0.7}` (section 7).

The platform adds two fields before storing or showing a record: `seq` (a whole number that only goes up, across all devices) and `received_at`.

## 3. Device check-in

When the device is not watching (display, calibrating, recording or training), it checks in so the dashboard knows it is alive and what it is doing.

`GET /api/devices/effente-01?mode=recording&machine_id=conveyor-07`

`mode` is `display`, `calibrating`, `recording` or `training`. Add `&sensitivity=0.5` to say the device's sensitivity; missing means not reported.

Reply: `{"ok": true}`, with `settings` as in message 2 once the dashboard has set a sensitivity.

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
      "sensitivity": 0.5,
      "settings": {"sensitivity": 0.7},
      "latest": {"...": "the newest record from this device, with spectrum, or null"}
    }
  },
  "models": {"conveyor-07-20261114": {"...": "message 1 as announced"}},
  "machines": ["conveyor-07"]
}
```

Per device, `sensitivity` is the value the device last reported (in a record, message 1 or a check-in), or `null` if it never has. `settings` is what the dashboard set, or `null` if nothing is set. The two differ while a change is on its way, or when the device is offline.

`GET /api/stream` is a server-sent event stream. Each event has a name and one JSON object as data:

| Event | Data | Sent when |
|-------|------|-----------|
| `record` | a record, with `seq` | a record is stored |
| `model` | message 1 | a device announces a model |
| `agent_run` | message 4 | a run is created or updated |
| `device` | `{"device_id": "effente-01", "connected": false, "last_seen": "...", "mode": "..."}` | a device connects, goes quiet, or changes mode |
| `settings` | `{"device_id": "effente-01", "sensitivity": 0.5, "settings": {"sensitivity": 0.7}}`, the two values above | the dashboard sets or clears a sensitivity, or the device reports a different one |

A comment line is sent every 5 seconds to keep the connection open. After a reconnect the dashboard calls `GET /api/state` again.

`GET /api/machines/conveyor-07/records?limit=300` returns `{"ok": true, "records": [...]}`, oldest first, the newest `limit` records (default 300, at most 2,000), without `spectrum`. Add `&spectrum=1` to keep it.

`GET /api/machines/conveyor-07/agent-runs?limit=20` returns `{"ok": true, "runs": [...]}`, newest first.

Everything else under `/` is the dashboard's own files, served from `platform/web/`, with `index.html` at `/`.

## 7. Settings

The device owns its sensitivity and always reports the value it is using. The platform keeps only the value the dashboard wants. If the two differ, the device's value is what happened.

Sensitivity goes from 0 (least sensitive) to 1 (most), default 0.5. It moves how far outside normal counts as wrong: the model's `alarm_line`, and the vibration, temperature and sound checks against this machine's normal. It never changes the universal limits (over 11 mm/s, over 100 °C, and the rest in `effente/safety.py`).

`PUT /api/devices/effente-01/settings` with `{"sensitivity": 0.7}` sets the wanted value. `{"sensitivity": null}` clears it. A value that is not a number from 0 to 1 is rejected with 400. A device the platform has not heard from yet can still have a value set; it gets it on first contact. Reply: `{"ok": true, "settings": {"sensitivity": 0.7}}`, or `"settings": null` after clearing.

While a value is set, every reply to message 2 and message 3 carries it. When it differs from the device's own value, the device applies it at once and keeps it across a restart. The next record or check-in shows the new value. Expect up to 2 seconds for the reply, then up to one summary period (up to a minute) before a diagnosis is judged at the new line. The outside-normal checks still need 10 seconds of holding before they show.

The device's own `/settings` on port 8765 and `data/settings.json` stay for bench use. While the dashboard has a value set, the next reply overwrites a local change. Clear the dashboard's value to tune on the device.

## Not in version 2.2

- The dashboard cannot start an agent. A person starts it.
- Machine profiles, model delivery to the device, and alarm feedback. Planned in [docs/model-lifecycle.md](../docs/model-lifecycle.md).
- More than one platform, or any security.
