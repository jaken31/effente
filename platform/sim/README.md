# Simulated device

A stand-in for the handheld. It talks to the platform exactly as the real device must ([CONTRACT.md](../CONTRACT.md) messages 1 to 3), so the platform, dashboard and agent can be built and demoed without hardware. Track 1: this is the reference for the device side.

**Everything it sends is made up.** The spectra are illustrative shapes, not measurements of any fan. The "model" is a hand-written stand-in, not a trained network, and not the TRD signal pipeline. Every record and the model announcement carry `"simulated": true`.

## Run

```
python3 platform/sim/main.py
```

Python 3.11+, standard library only. Connects to `EFFENTE_PLATFORM_URL` (default `http://localhost:8000`). It keeps running if the platform is down.

Keys (when started in a terminal, no Enter needed): `1` normal, `2` imbalance, `3` loose mount, `4` unknown, `t` heat source (camera, IMU only, none), `p` pause, `q` quit. The status line shows the case, the heat source, the diagnosis sent, the active mask and whether the platform is reachable. Started from a script (stdin not a terminal) it reads no keys and prints one status line per record.

| Option | Default | |
|--------|---------|---|
| `--url` | `$EFFENTE_PLATFORM_URL` or `http://localhost:8000` | platform |
| `--device` | `effente-sim-01` | device_id |
| `--machine` | `demo-fan-1` | machine_id |
| `--case` | `normal` | `normal`, `imbalance`, `loose_mount`, `unknown` |
| `--heat` | `camera` | `camera`, `imu` or `none`: what the record's `heat` field carries (see Heat below) |
| `--interval` | `1` | seconds between records |
| `--seed` | random | repeatable output |
| `--count N` | forever | stop after N records |
| `--log FILE` | none | append every record, one JSON object per line (the device log the agent reads). Records are logged whether or not the upload worked. |
| `--model-file FILE` | none | write `{model_version, class_names, unknown_threshold, features: [{name, unit_indices, sentence}], simulated: true}` for the agent. `unit_indices` is empty: there are no real hidden units. |
| `--replay FILE` | none | send records from a JSON lines file instead, at `--interval` pace, then stop |

Model version `sim-1`. Feature table: `once_per_turn` "once-per-turn vibration", `rattle` "rattling pattern". `unknown_threshold` 0.5.

## What a device does, in order

1. **Start:** `POST /api/model` (message 1). If it fails, keep going and retry before the next upload.
2. **Each window (about 1 s):** diagnose with the current mask, write the record to the local log, then `POST /api/records` (message 2). The record's `mask` field is the mask that was applied to this window.
3. **Read the reply:**
   - `mask`: store it and apply it to the *next* window. `mask_version` only changes when the mask does.
   - `need_model: true`: send message 1 again.
   - HTTP 400: the platform refused the record. Show the error; do not resend it.
   - No reply (refused, timeout): mark the platform unreachable, keep the record in a small queue (the simulator keeps the newest 30), and send the queue as one JSON array next time.
4. **Not scanning (paused, or a long interval):** `GET /api/devices/<device_id>/mask` so the platform hears from you and you pick up mask changes.

Timing rules:

- Contact the platform at least every 2 s (contract). The simulator does it every 1 s when it has no record to send.
- Short timeouts (simulator: 0.8 s per request). A dead network must never stop diagnosis or freeze the screen and buttons.
- Diagnosis never waits for the network. The platform is optional; detection is not.

## The mask rule

The mask is a list of feature names to switch off. Apply it mechanically, never by looking up the expected answer:

```
spectrum -> feature activity -> zero the masked features -> output layer -> diagnosis
```

On the real device "zero the features" means zeroing the hidden units listed for each feature in the model file, before the output layer. In the simulator, activity is computed from the spectrum (`once_per_turn` from the 30 Hz line, `rattle` from the broadband level) and `diagnose()` in [sim_model.py](sim_model.py) is a three-class linear layer with softmax over that activity.

What must happen, and what the tests check:

| Case | Mask | Record says |
|------|------|-------------|
| imbalance | `once_per_turn` off | `normal`, reason `""`, `once_per_turn` 0 |
| imbalance | `rattle` off | unchanged: `imbalance`, "once-per-turn vibration" |
| loose mount | `rattle` off | `normal`, reason `""`, `rattle` 0 |
| loose mount | `once_per_turn` off | unchanged: `loose_mount`, "rattling pattern" |
| unknown | anything | `unknown` (the unknown score does not go through the mask) |
| any | anything | the spectrum is unchanged: the vibration is still there, only the model's use of it is off |

Names not in the feature table are ignored but still echoed in `mask` (the platform rejects them anyway).

## Simulated cases

64 bins, 0 to 500 Hz, demo fan taken as about 1,800 RPM (30 Hz once per turn; an assumption, not measured). Small random variation each window, repeatable with `--seed`.

- **normal:** low noise floor, small 30 Hz peak.
- **imbalance:** strong 30 Hz peak.
- **loose mount:** raised broadband floor, harmonics of 30 Hz up to 180 Hz that come and go window to window.
- **unknown:** a strong tone at 300 Hz with sidebands. `unknown_score` is the height of a peak above 220 Hz, where none of the three known cases put energy; it stays well above 0.5 here. This stands in for the TRD's hidden-layer distance.

## Heat

Records carry the contract 3.3 `heat` field ([sim_heat.py](sim_heat.py)). Made up like everything else: the thermal scene is a hand-drawn layout (a warm motor body, a hot bearing spot, the room) with 0.15 degC noise and slow drift, and the IMU temperature wanders around 42.5 degC.

- **camera:** the summary (hottest pixel and where, background, the 48-block grid, frozen, a made-up `scene_match`) and two 32 x 24 images per record, half an interval apart. The summary uses the same definitions as the `th_*` features in `effente/features.py`; a test checks they agree.
- **imu:** `{"source": "imu", "imu_temp_c": ...}` only, as a device with no camera sends it.
- **none:** no `heat` field.

Heat has its own random generator, so `--seed` gives the same vibration records with or without it.

## Replay

`--replay FILE` sends each line of a JSON lines file with `at` rewritten to now. `device_id` and `model_version` become the simulator's own, so the mask and announcement line up; `machine_id` is kept from the file. If a record has `features`, those values are taken as raw activity and the diagnosis is recomputed under the current mask. A record is `simulated: true` unless it says `simulated: false` explicitly.

[sample_replay.jsonl](sample_replay.jsonl): two windows of each case, generated by `sim_model.make_record` with `random.Random(7)` and no mask (a test checks the file still matches).

## Testing a real device against this

1. **Shape:** run `python3 platform/sim/main.py --count 5 --log sim.jsonl` and compare a real device record with `sim.jsonl` field by field.
2. **Behaviour:** point the device at the small fake receiver used by the tests. It prints each announcement and record and flags contract problems (missing fields, spectrum not 64 values in 0..1, a masked feature not 0, a reason on normal or unknown, a `heat` field of the wrong shape):

   ```
   python3 platform/sim/tests/sim_fake_platform.py 8000 once_per_turn
   ```

   The second argument is the mask it hands back. With a coin on the fan, the first record should say imbalance with `mask: []`, and every later one should say normal with `mask: ["once_per_turn"]`. Then repeat with `rattle` and a loosened mount.
3. **Dropout:** stop the receiver for a few seconds and start it again. The device should keep diagnosing, then announce and send the queued records.

The fake receiver is a test tool, not the platform. The real one is in `platform/` (another part).

## Tests

```
python3 -m pytest platform/sim
```

Covers the four cases, every mask combination, seeded repeatability, replay, and end-to-end runs of `main.py` against the fake receiver: announcement first, mask acknowledged in the next record, `need_model`, polling while idle, platform absent, platform coming back mid-run, keys on a pseudo-terminal, and terminal settings restored after `q`, Ctrl-C and an exception.
