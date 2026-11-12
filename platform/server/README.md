# Receiver

Accepts log records, model announcements and agent runs from the device and agent, stores them, streams them live to the dashboard, passes the feature mask back to the device, and serves the dashboard's files. The routes are exactly those in [../CONTRACT.md](../CONTRACT.md), sections 1 to 5.

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

Masks are not stored: they start empty at every start, as the contract says. A cut-off or corrupt line (for example after a crash) is skipped with a warning.

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
