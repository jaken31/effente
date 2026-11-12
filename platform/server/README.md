# Receiver

Accepts model announcements, log records and check-ins from the device and agent runs from the agent, stores them, streams them live to the dashboard, tracks each device's mode and problems, and serves the dashboard's files. The routes are those in [../CONTRACT.md](../CONTRACT.md), sections 1 to 5. Sections 6 (alarms) and 7 (settings) are not built yet.

Python 3.11 or newer, standard library only. No install, no build step.

## Run

From anywhere:

```
python3 platform/server/main.py
```

It prints the URL to open, and the address devices should use on the hotspot. Point the device at that address with `EFFENTE_PLATFORM_URL`. Ctrl-C stops it.

| Option | Default | Meaning |
|--------|---------|---------|
| `--port` | `8000` | port to listen on |
| `--host` | `0.0.0.0` | address to listen on; `127.0.0.1` keeps it off the network |
| `--data-dir` | `platform/server/data` | where history is kept |
| `--web-dir` | `platform/web` | dashboard files served at `/` |

If the web dir or its `index.html` is missing, `/` shows a short notice and the API still works.

## Data

Three JSON lines files in the data dir, appended as things arrive and reloaded at start:

- `records.jsonl`: every record, with the `seq` and `received_at` the receiver added.
- `models.jsonl`: every model announcement. The last one per `model_version` wins.
- `agent_runs.jsonl`: every copy of every agent run. The last one per `run_id` wins.

Check-ins are not stored, and neither are each device's mode, `wrong` and `problems`: after a restart no device is connected, so they are `null`, `false` and `[]` until the device is heard from again. A cut-off or corrupt line (for example after a crash) is skipped with a warning.

## Problems and the device event

The receiver makes the `problem` and `problem_cleared` events by comparing the `problems` of each record or check-in with the previous ones from the same device, by name. Each record in a catch-up array is compared with the one before it. Problem names are not checked against a list (the device's safety checks own them); a record or check-in is refused only if `problems` is not a list, a problem has no `name`, or a name repeats.

Two choices the contract leaves open:

- A model announcement keeps a connected device connected, but does not connect one by itself, because the `device` event must carry `mode` and an announcement does not say it. The device connects with its first record (mode `watching`) or check-in. Until then its `mode` in `/api/state` is `null`.
- When a device goes quiet, its problems are cleared with `"reason": "device quiet"`. No message caused that, so `at` is the platform's time.

Writes are flushed to the OS after each request but not fsynced, so a power cut can lose the last few records.

To wipe all history, stop the receiver and delete the data dir:

```
rm -r platform/server/data
```

## Tests

```
python3 -m pytest platform/server
```

The tests start a real receiver on a free port and talk to it over HTTP, including the event stream and the `main.py` entry point.
