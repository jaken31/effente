# Simulated device

A stand-in for the handheld. It talks to the platform as the real device must ([CONTRACT.md](../CONTRACT.md) messages 1 to 3, version 3), so the platform, dashboard and agent can be built and demoed without hardware. Track 1: this is the reference for the device side.

**Everything it sends is made up.** The spectra are illustrative shapes, not measurements of any fan. The "model" is a hand-written table of cases, not the device runtime (`effente/model_runtime.py`) and not the TRD signal pipeline. The problems are switched on by hand, not found by `effente/safety.py`. Every message carries `"simulated": true`.

## Run

```
python3 platform/sim/main.py
```

Python 3.11+, standard library only. Connects to `EFFENTE_PLATFORM_URL` (default `http://localhost:8000`). It keeps running if the platform is down.

Keys (when started in a terminal, no Enter needed):

| Key | Does |
|-----|------|
| `1` to `6` | what the model says: normal, unbalance, looseness, unrecognised pattern, stopped, warming up |
| `h` | switch the `too_hot` condition on or off (a limit) |
| `v` | switch the `vibration_above_normal` condition on or off (outside normal, so it shows only while watching) |
| `m` | next mode: watching, display, calibrating, recording, training, then watching again |
| `t` | next heat source: camera, IMU only, none |
| `p` | pause: send nothing at all, as if the device dropped off the network |
| `q` | quit |

A problem shows about 10 seconds after its condition is switched on (`--hold`), and clears about 8 seconds after it is switched off, as on the device. The status line shows the mode, the case, each problem (`too_hot in 4s`, `too_hot on`, `too_hot clearing`), the heat source, what was sent last and whether the platform is reachable. Started from a script (stdin not a terminal) it reads no keys and prints one status line per message.

| Option | Default | |
|--------|---------|---|
| `--url` | `$EFFENTE_PLATFORM_URL` or `http://localhost:8000` | platform |
| `--device` | `effente-sim-01` | device_id |
| `--machine` | `demo-fan-1` | machine_id |
| `--case` | `normal` | `normal`, `unbalance`, `looseness`, `unrecognised`, `stopped`, `warming_up` |
| `--mode` | `watching` | `watching` sends records; `display`, `calibrating`, `recording`, `training` send check-ins |
| `--problem NAME` | none | a problem condition on from the start; repeatable. Any name in the contract's problems table |
| `--heat` | `camera` | `camera`, `imu` or `none`: what the record's `heat` field carries (see Heat below) |
| `--hold S` | `10` | seconds a problem must hold before it shows; `0` shows it at once |
| `--interval` | `1` | seconds between records or check-ins, at most 2 (the contract's rule) |
| `--seed` | random | repeatable output |
| `--count N` | forever | stop after N records or check-ins |
| `--log FILE` | none | append every record, one JSON object per line (the device log the agent reads). Records are logged whether or not the upload worked. Check-ins are not logged. |
| `--model-file FILE` | none | write a model file for the agent: `{model_version, features: [{name, unit_indices, sentence}], fault_types, simulated: true}`. `unit_indices` is empty: there are no real hidden units. `device/agent` reads `sentence`, so this file keeps the TRD shape rather than the contract's `text`. |
| `--replay FILE` | none | send records from a JSON lines file instead, at `--interval` pace, then stop |

Scenarios worth knowing:

```
python3 platform/sim/main.py --problem too_hot                 # a problem while the model says normal
python3 platform/sim/main.py --case stopped --problem too_hot  # a problem next to stopped
python3 platform/sim/main.py --mode recording --problem too_hot  # a problem while not watching: check-ins
```

Model version `sim-3`, library entry `generic_rotating`, source `calibrated`, sensitivity 0.5 (it does not take settings from the platform). Fault types: `vibration_too_high`, `unbalance_like`, `looseness_or_rattle`, all `validated: false`. Ten readings in the feature table, with the texts of `effente/features.py`.

## What a device does, in order

1. **Start:** `POST /api/model` (message 1). If it fails, keep going and retry before the next upload.
2. **While watching, once a second:** write the record to the local log, then `POST /api/records` (message 2). Every record carries `problems` (`[]` when nothing is wrong) and `wrong`.
3. **Read the reply:**
   - `need_model: true`: send message 1 again.
   - HTTP 400: the platform refused the record. Show the error; do not resend it.
   - No reply (refused, timeout): mark the platform unreachable, keep the record in a small queue (the simulator keeps the newest 30), and send the queue as one JSON array next time.
4. **In any other mode, once a second:** `POST /api/devices/<device_id>` (message 3) with `machine_id`, `mode`, `at`, `wrong` and `problems`. Only limits can be on outside watching. A check-in that does not get through is not queued: the next one replaces it.

Timing rules:

- Contact the platform at least every 2 s (contract). The platform counts a device silent for 5 s as gone, and clears its problems with the reason "device quiet".
- Short timeouts (simulator: 0.8 s per request). A dead network must never stop diagnosis or freeze the screen and buttons.
- Diagnosis and the safety checks never wait for the network. The platform is optional; detection is not.

## Problems and the diagnosis

The case sets only the model's side of the record. Problems are separate, as on the device, where the safety checks run apart from the model: any problem can be on with any case and in any mode. `wrong` is `true` when the diagnosis is a fault type or `unrecognised_pattern`, or any problem is on.

| Case | `diagnosis` | `unknown_score` | `reason` |
|------|-------------|-----------------|----------|
| normal | `normal` | 0.25 to 0.55 | empty |
| unbalance | `unbalance_like` | about 2.4 | the fault type's text |
| looseness | `looseness_or_rattle` | about 2 | the fault type's text |
| unrecognised | `unrecognised_pattern` | about 1.6 | "abnormal, no known fault type fits" |
| stopped | `stopped` | `null` | empty, with a note |
| warming_up | `warming_up` | `null` | empty, with a note |

The simulator changes the diagnosis at once when the case changes. The real device judges once per summary period (up to a minute), so on the real device a problem often shows before the diagnosis changes.

## Simulated spectra

20 vibration bands, 5 to 500 Hz on a log scale, log10 band power, as the real device sends. The demo fan is taken as about 1,800 RPM (30 Hz once per turn; an assumption, not measured). Small random variation each record, repeatable with `--seed`.

- **normal:** a gentle slope with a small 30 Hz line.
- **unbalance:** a strong 30 Hz line and a little at 60 Hz.
- **looseness:** a raised floor, harmonics of 30 Hz that come and go, and some at 15 Hz.
- **unrecognised:** a strong tone in the 251-315 Hz band.
- **stopped:** everything low.

## Heat

Records carry the contract 3.3 `heat` field ([sim_heat.py](sim_heat.py)). Made up like everything else: the thermal scene is a hand-drawn layout (a warm motor body, a hot bearing spot, the room) with 0.15 degC noise and slow drift, and the IMU temperature wanders around 42.5 degC.

- **camera:** the summary (hottest pixel and where, background, the 48-block grid, frozen, a made-up `scene_match`) and two 32 x 24 images per record, half an interval apart. The summary uses the same definitions as the `th_*` features in `effente/features.py`; a test checks they agree.
- **imu:** `{"source": "imu", "imu_temp_c": ...}` only, as a device with no camera sends it.
- **none:** no `heat` field.

Heat has its own random generator, so `--seed` gives the same vibration records with or without it.

## Replay

`--replay FILE` sends each line of a JSON lines file. Every time in it (`at`, and each problem's `since`) is moved by the same amount so the first record goes out now and the spacing is kept. `device_id` and `model_version` become the simulator's own, so they line up with its announcement; `machine_id` is kept from the file. Records from before version 3 get `problems: []` and a `wrong` worked out from their diagnosis. A record is `simulated: true` unless it says `simulated: false` explicitly.

[sample_replay.jsonl](sample_replay.jsonl): ten records, one a second: normal; `too_hot` coming on while the model still says normal; unbalance with `too_hot` still on; `too_hot` next to stopped; an unrecognised pattern with nothing else wrong. Made by `sim_model.sample_records()` (a test checks the file still matches).

## Testing a real device against this

1. **Shape:** run `python3 platform/sim/main.py --count 5 --log sim.jsonl` and compare a real device record with `sim.jsonl` field by field.
2. **Behaviour:** point the device at the small fake receiver used by the tests. It prints each announcement, record and check-in and flags contract problems (missing fields, a spectrum that is not 20 numbers, `wrong` not matching the diagnosis and problems, an outside-normal problem in a check-in, a reason on normal, a `heat` field of the wrong shape):

   ```
   python3 platform/sim/tests/sim_fake_platform.py 8000
   ```

   Heat the device's view past 100 C: about 10 seconds later records should carry `too_hot` with `wrong: true`, whatever the diagnosis. Then start a recording: check-ins should arrive once a second with mode `recording` and the same problem.
3. **Dropout:** stop the receiver for a few seconds and start it again. The device should keep diagnosing, then announce and send the queued records.

The fake receiver is a test tool, not the platform. The real one is in `platform/server/`.

## Tests

```
python3 -m pytest platform/sim
```

Covers every case with and without a problem, the problem hold and clear timing, limits only outside watching, check-ins, seeded repeatability, replay, and end-to-end runs of `main.py` against the fake receiver: announcement first, problems while normal, check-ins while recording, `need_model`, platform absent, platform coming back mid-run, keys on a pseudo-terminal, and terminal settings restored after `q`, Ctrl-C and an exception.
