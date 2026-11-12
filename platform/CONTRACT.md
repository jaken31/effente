# Device and platform contract

Version 1. Owner: track 5. This file is the whole interface between the handheld (or the simulated device), the laptop platform, the dashboard and the agent. Build to it exactly. If it is wrong, tell track 5; do not work around it.

It follows the log record in [docs/TRD.md](../docs/TRD.md) and adds four fields to it: `model_version`, `reason`, `simulated`, and (set by the platform) `seq`. `model_version` closes gap 1 in [docs/architecture.md](../docs/architecture.md).

## Basics

- Plain HTTP and JSON. The platform listens on port `8000` on all interfaces. The device finds it through the environment variable `EFFENTE_PLATFORM_URL` (default `http://localhost:8000`).
- The device only makes outgoing requests. The platform never connects to the device. The mask comes back in the reply to each upload.
- No login. The platform runs on the team's own hotspot.
- Times are ISO 8601 UTC, `2026-11-14T16:02:11Z`. Fractions of a second are allowed.
- Success is HTTP 200 with `"ok": true`. A bad request is HTTP 400 with `{"ok": false, "error": "what was wrong"}`. Unknown fields are stored and passed on, never rejected.
- A device counts as connected if the platform heard from it in the last 5 seconds. A device must make a request at least every 2 seconds: a record upload, or `GET` mask when it is not scanning.

## Names

| Thing | Values |
|-------|--------|
| `diagnosis` | `normal`, `imbalance`, `loose_mount`, `unknown` |
| feature name | the `name` from the model file's feature table, for example `once_per_turn`, `rattle` |
| `priority` | `none`, `low`, `medium`, `high` |
| `model_version` | any string that changes when the model file changes, for example `fake-0.1` |

## 1. Device announces its model

Sent once at start, and again whenever the platform replies `"need_model": true`. This is how the dashboard learns which features exist and what each means. Only features that passed a switch-off test may appear.

`POST /api/model`

```json
{
  "device_id": "effente-01",
  "model_version": "fake-0.1",
  "class_names": ["normal", "imbalance", "loose_mount"],
  "unknown_threshold": 0.5,
  "spectrum_hz_max": 500,
  "features": [
    {"name": "once_per_turn", "sentence": "once-per-turn vibration"},
    {"name": "rattle", "sentence": "rattling pattern"}
  ],
  "simulated": false
}
```

Reply: `{"ok": true}`

## 2. Device sends a log record

One record per window, about one a second while scanning.

`POST /api/records` with one record, or a JSON array of records (for catching up after a dropout).

```json
{
  "device_id": "effente-01",
  "machine_id": "conveyor-07",
  "model_version": "fake-0.1",
  "at": "2026-11-14T16:02:11Z",
  "diagnosis": "imbalance",
  "confidence": 0.94,
  "unknown_score": 0.12,
  "reason": "once-per-turn vibration",
  "features": {"once_per_turn": 0.81, "rattle": 0.03},
  "mask": [],
  "spectrum": [0.02, 0.05, 0.71, 0.04],
  "simulated": false
}
```

| Field | Rule |
|-------|------|
| `device_id`, `machine_id`, `model_version`, `at`, `diagnosis` | Required. A record missing one is rejected with 400. |
| `confidence` | 0 to 1. |
| `unknown_score` | 0 or more. `diagnosis` is `unknown` when it is above the model's `unknown_threshold`. |
| `reason` | The one line the device screen shows: the sentence of the feature behind the diagnosis. Empty string for `normal` and `unknown`. |
| `features` | Activity of every verified feature, 0 to 1, after the mask is applied. A switched-off feature reads 0. |
| `mask` | Names of the features that were switched off when this window was diagnosed. This is the device's acknowledgement of the mask. |
| `spectrum` | 64 magnitudes, each 0 to 1, evenly covering 0 Hz to `spectrum_hz_max`, strongest axis per bin. (The example above is shortened.) |
| `simulated` | `true` if the reading did not come from a real sensor. Missing means `false`. |

Reply:

```json
{"ok": true, "stored": 1, "mask": ["once_per_turn"], "mask_version": 3, "need_model": false}
```

The device applies `mask` to the next window it diagnoses. `mask_version` goes up by one each time the mask changes, so a device can skip work when it has not. `need_model` is `true` when the platform has not seen this `model_version`; the device then repeats message 1.

The platform adds two fields before storing or showing a record: `seq` (a whole number that only goes up, across all devices) and `received_at`.

## 3. Platform sends a feature mask

The mask is the list of feature names to switch off. Empty list means everything on. There is one mask per device. It starts empty each time the platform starts.

The dashboard sets it:

`PUT /api/devices/effente-01/mask`

```json
{"mask": ["once_per_turn"]}
```

Reply: `{"ok": true, "device_id": "effente-01", "mask": ["once_per_turn"], "mask_version": 3}`

A name that is not in the device's announced feature table is rejected with 400.

The device receives it in every reply to message 2. When it is not uploading it asks instead:

`GET /api/devices/effente-01/mask`

Reply: `{"ok": true, "device_id": "effente-01", "mask": ["once_per_turn"], "mask_version": 3}`

What the device must do with a mask: before the output layer, zero the hidden units of each named feature. The diagnosis that depends on a switched-off feature must go away and the others must stay. A toggle is confirmed only when a record arrives whose `mask` matches; until then the dashboard shows it as pending.

## 4. Agent sends its report

The agent sends the whole run each time something happens: once when it starts, after each tool call, and once at the end. Same `run_id` every time; the platform keeps the latest copy. This is how tool calls show up live.

`PUT /api/agent/runs/run-20261114-160300-conveyor-07`

```json
{
  "run_id": "run-20261114-160300-conveyor-07",
  "device_id": "effente-01",
  "machine_id": "conveyor-07",
  "at": "2026-11-14T16:03:00Z",
  "status": "done",
  "model": "claude-opus-5-5",
  "usage": {"input_tokens": 2140, "output_tokens": 310},
  "ok": true,
  "reason": "",
  "problems": [],
  "tool_calls": [
    {"at": "2026-11-14T16:03:01Z", "name": "get_readings", "input": {"since": ""}, "output": "{\"windows\": 42}"},
    {"at": "2026-11-14T16:03:02Z", "name": "get_feature_table", "input": {}, "output": "{\"once_per_turn\": \"once-per-turn vibration\"}"}
  ],
  "report": {
    "summary": "Imbalance on conveyor-07 in 40 of 42 windows.",
    "priority": "medium",
    "cited_features": ["once_per_turn"],
    "recommended_action": "Clean and rebalance the blades.",
    "part_reference": ""
  },
  "simulated": false
}
```

| Field | Rule |
|-------|------|
| `run_id`, `machine_id`, `at`, `status` | Required. `run_id` in the body must match the URL. It may contain letters, digits, `-`, `_` and `.` only. |
| `status` | `running` or `done`. |
| `ok` | Meaningful when `status` is `done`. `true` only if the report passed the agent's checks. |
| `report` | `null` unless `ok` is `true`. Shape is fixed as shown. `cited_features` may be empty, and must be for `normal` and `unknown`. |
| `reason`, `problems` | Why there is no report: for example `agent unavailable: no connection`, or the list of failed checks. |
| `tool_calls` | In order. `output` is a string, cut to 2,000 characters. |
| `simulated` | `true` if the report did not come from a real Claude call, or was written from simulated readings. |

Reply: `{"ok": true}`

If the platform cannot be reached the agent still writes its own log and carries on. The platform is never required for a report to exist.

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
      "model_version": "fake-0.1",
      "mask": [],
      "mask_version": 0,
      "latest": {"...": "the newest record from this device, with spectrum, or null"}
    }
  },
  "models": {"fake-0.1": {"...": "message 1 as announced"}},
  "machines": ["conveyor-07"]
}
```

`GET /api/stream` is a server-sent event stream. Each event has a name and one JSON object as data:

| Event | Data | Sent when |
|-------|------|-----------|
| `record` | a record, with `seq` | a record is stored |
| `model` | message 1 | a device announces a model |
| `mask` | the reply to `PUT` mask | a mask changes |
| `agent_run` | message 4 | a run is created or updated |
| `device` | `{"device_id": "effente-01", "connected": false, "last_seen": "..."}` | a device connects or goes quiet |

A comment line is sent every 5 seconds to keep the connection open. After a reconnect the dashboard calls `GET /api/state` again.

`GET /api/machines/conveyor-07/records?limit=300` returns `{"ok": true, "records": [...]}`, oldest first, the newest `limit` records (default 300, at most 2,000), without `spectrum`. Add `&spectrum=1` to keep it.

`GET /api/machines/conveyor-07/agent-runs?limit=20` returns `{"ok": true, "runs": [...]}`, newest first.

Everything else under `/` is the dashboard's own files, served from `platform/web/`, with `index.html` at `/`.

## Not in version 1

- The dashboard cannot ask the device to run the agent. A person starts the agent.
- Machine names, locations and speeds. Machines are IDs only.
- More than one platform, or any security.
