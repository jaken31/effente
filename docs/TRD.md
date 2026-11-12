# effente - Technical Requirements

Companion to [PRD.md](PRD.md). Numbers marked "starting value" are first guesses to tune once real recordings exist.

The data pipeline, model and fault types described here are built on branch `track2/data-intake` (Finn). Figures quoted from that branch are as reported there, not re-checked.

## Architecture

```
 magnetic sensor head: accelerometer (+ optional mic, thermal camera, room sensor)
        |  short cable
 UNO Q microcontroller (STM32)      real-time sampling, 2.4" screen, button
        |  Arduino Bridge (RPC)
 UNO Q Linux side (Debian, 4 GB)    features, model library, calibration,
        |                           small network per machine, fault types, log
        |  WiFi (phone hotspot)
 Laptop platform                    log store, dashboard, agent fleet
        |
 Claude API, Tavily                 used by the agents when online
```

The Arduino UNO Q is the only device board. Its microcontroller handles timing-sensitive work and its Linux side runs the same Python used for training, so there is no port of the model to C on the critical path.

The device runs only the small per-machine network (a library network calibrated to the machine, or one trained on the machine alone) and the fault-type rules. Detection, explanation and learning need no network. All agents run on the laptop and need a connection.

## Hardware

- Arduino UNO Q, 4 GB
- Accelerometer: MPU-6500, on an MPU-9250/6500 breakout. It replaces the MPU6050 (GY-521) planned earlier: record all training data with this sensor and never mix data from the two. Its usable bandwidth depends on its low-pass filter setting (not yet checked against the datasheet). A BNO085 is on hand as a spare only
- 2.4" OLED screen and one button
- Sensor head: printed puck with a magnet in the base, on a short cable
- Steel discs glued to each machine as probe points
- USB power bank
- Optional, used when fitted: microphone (INMP441), thermal camera (MLX90640), room sensor (BME280). The pipeline treats a missing sensor as missing, never as normal
- Mini belt conveyor as the demo machine: printed frame and rollers driven by a TT gear motor, run at one fixed speed. Build guide: [conveyor-build-guide.md](research/conveyor-build-guide.md)

The device is handheld. Only the small sensor head touches the machine, held by its magnet, so nobody presses or holds it during a reading. It can stay on one machine for days. Research and drawing: [physical-design.md](research/physical-design.md), [mockup.svg](mockup.svg). CAD and print files: `device/cad/`.

## Device modes

The default path is quick: pick a library entry, calibrate, watch. Staying on the machine to train a model on it alone is optional.

```
 quick (default):  DISPLAY --pick entry--> CALIBRATING --done--> WATCHING
 optional, long:   WATCHING --button--> RECORDING --button--> TRAINING --done--> WATCHING (own model)
```

| Mode | What it does |
|------|--------------|
| Display | Shows live readings: vibration, peaks, speed, temperature. The button cycles through library entries; a long press picks one |
| Calibrating | Records this machine running and fits the library network's preparation statistics and alarm threshold to it. The screen shows progress. New |
| Watching | Judges the machine and shows the diagnosis. Records in the background so a long stay can be used later |
| Recording | Optional long stay: keeps feature rows, per-minute summaries, raw snippets and events. The screen shows how long it has been learning |
| Training | Trains a model on this machine alone, on the device. Refuses a recording shorter than 5 minutes of running |

Display, Recording, Training and Watching exist in `device/intake/device_app.py` on `track2/data-intake`. Calibrating and the entry picker are new (track 1 for the device app, track 3 for calibration).

After a power cut the device resumes what it was doing.

## Signal pipeline

| Step | Value |
|------|-------|
| Vibration sample rate | 1 kHz, 3 axes |
| Window | 1 second, a new one every 0.25 s (1 a second while recording) |
| Features | 278 per window: 210 from the window itself, 68 from the stream's history (`effente/features.py`, `effente/history.py`) |
| Preparation | Running rows only, median and interquartile range from training rows, clipped to +-8, missing sensors as 0 plus a missing flag (`effente/model_input.py`, `Prep`) |

The vibration features include 20 bands from 5 to 500 Hz, running speed and the levels at 1x, 2x, 3x, 3-10x and 0.5x of it, knockiness, jolts, bearing-rate envelope analysis and velocity in mm/s. Sound, heat and room features are listed in `device/intake/README.md`.

Open question: the running-speed search covers 8 to 120 Hz. The conveyor's drive roller turns only a few times a second (TT motor through 1:48 gearing; not yet measured, see step 9 of the build guide), so its once-per-turn peak falls below that range. The motor shaft turns much faster. Tracks 2 and 3 to confirm which shaft the unbalance demo shows up on, and adjust the search range if needed.

One implementation of this pipeline, in Python, is used for recording, training and on-device inference. That is the guard against training on one transform and running another.

## Model library and calibration

The library is what makes the device quick to use: a set of pretrained networks, one per machine type, stored on the device. Each entry is an `effente-model-1` model file built on the device's exact feature list.

- **Hackathon library:** `conveyor`, trained on our own recordings of the demo conveyor plus the conveyor simulator, and `generic_rotating`, trained on the simulators and the public bearing data. The `conveyor` entry is trained on the same conveyor it is demoed on, so the demo proves the flow, not that the entry generalises to other conveyors. Say so if asked.
- **Calibration:** keeps the entry's network and fits only this machine's preparation statistics (`Prep`: median and spread of each reading) and the alarm threshold. Length: starting value 5 minutes of running, the trainer's current minimum. Track 3 to measure how short it can go.
- **Fit check:** if the calibration data scores far worse on the entry than the entry's own held-out data did, the screen says the machine does not look like that type and suggests `generic_rotating` or a long stay. The rule is open; set it from recordings.
- **Fault classifier:** retrained during calibration on fault patterns added to this machine's own normal variation, as in full training.

After the hackathon the library grows by machine type and the setup agent picks the entry ([model-lifecycle.md](model-lifecycle.md)).

## Model

The network inside every library entry, and the model a long stay trains on one machine alone, are the same kind, built with NumPy only (`effente/trainer.py`, `effente/model_runtime.py`).

- **Normal model:** a small denoising autoencoder. It learns to rebuild this machine's normal readings. Its score is how badly it rebuilds the current period.
- **Summary period:** per-period medians of the feature rows. The period follows the recording length: about 1 second for a 20-minute demo recording, one minute for a week.
- **Split by time:** the last 15 % of the recording is held out to set the alarm threshold. Never a random split.
- **Threshold:** a gamma fit to the held-out scores, set for one false alarm a week, never below the highest held-out score.
- **Persistence:** an alarm needs the median of the last 5 periods over the threshold (starting value).
- **Whole-number weights:** int8 with a scale per neuron. The device runtime checks it gives the same scores as the trained network.
- **Cost on the UNO Q:** reported on the branch as 6 ms per judgement and 8 ms to load a model file of about 50 kB.

## Fault types and explanations

When the model says the machine is abnormal, it also says how far each reading is from what it expected. A reading counts as evidence only when both of these agree, in the same direction:

- its residual, in spreads of its own normal residual (the model expected something else, given the other readings)
- its distance from its usual value

A reading 6 or more spreads from usual counts as it is.

The 21 fault types in `effente/fault_types.py` are the same on every machine. Each is a signature over named readings: which ones move, and which way. A small classifier, trained on these patterns added to the machine's own normal variation, is blended with the signatures. The type with the strongest evidence is the diagnosis. If none reaches 4 spreads, the diagnosis is `unrecognised_pattern`, with the readings that moved.

Each type has a group, used on the screen and the dashboard: `sensing` if the type is a problem with the device or its view, otherwise the first sensor it relies on (`vibration`, `sound`, `heat`, `room`).

The explanation for every alarm is the list of readings that moved most, each with how far it moved. There is no separate explanation table.

Limits:

- The fault type signatures are starting values from the failure-mode review in `device/intake/FAILURE_MODES.md`, not learned from real labelled faults. Unverified. The screen and dashboard show them as "possible".
- A sound-only change in the first 10 minutes may be the room, not the machine; the result says so.

## Unknown faults

Any period far from the machine's normal raises an alarm, whether or not a fault type fits. `unknown_score` is the smoothed score divided by the threshold: above 1 is abnormal. An abnormal period with no fitting type is `unrecognised_pattern` ("unrecognised pattern, inspect" on the screen). A count of readings far outside anything seen while learning is reported as a second signal.

## Agent fleet

- Runs on the laptop platform, never on the device.
- Model: Claude, called over the network. Lookups: Tavily.
- Input: the platform's stored log records for a machine.
- Output: a maintenance report, a repair priority, and a part or manual reference.
- Rule: a report may cite only readings that appear as moved readings (`features`) in the records it read.
- Offline: the agents are skipped and the dashboard says so. Detection on the device is unaffected.

For the hackathon the fleet is one agent, the maintenance report agent. The setup and review agents in [model-lifecycle.md](model-lifecycle.md) come later.

### Harness

The harness is the code around the Claude call. Keep it small: a short loop, a few tools, and hard checks in our own code.

- **Loop:** the tool runner in the Python `anthropic` SDK. We write tools as plain functions and host the loop on the laptop. Not Managed Agents, because the tools read the platform's local store.
- **Tools:** `get_readings(machine_id, since)`, `get_fault_types()`, `search_parts(query)` (Tavily), and optionally `get_history(machine_id)`.
- **Output:** a structured report with a fixed shape: summary, priority, cited readings, part reference. `cited_features` is restricted to the feature names in the model's feature table.
- **Check in code:** after the response, confirm every cited reading moved in the records supplied. If not, discard the report and say so.
- **Offline and errors:** short timeout. Handle connection and rate-limit errors separately. On failure show "agent unavailable", queue the request, retry when the network returns.
- **Limits:** cap the number of tool rounds and the output size.
- **Logging:** save each request, tool call, response and token count to the agent log. The dashboard shows the tool calls live.
- **Tests:** about ten fake logs with known right answers (possible unbalance, possible looseness, unrecognised pattern, healthy). Run them after every prompt change.

Settings:

- Model: `claude-opus-5-5`. Set the effort level explicitly, since its default is medium. Haiku 5.5 is the cheaper, faster option if needed.
- No forced tool calls: Opus 5.5 rejects them. Structured output is used instead.
- Caching: stable content first (instructions, tool definitions, fault types and feature table), changing log data last.
- Key: `ANTHROPIC_API_KEY` in the laptop's environment, never in the repo.

Fallback if time is short: one Claude call with the readings included and a structured report out, no tool loop. Add the parts lookup tool afterwards.

## Shared formats

The device-to-platform messages are in [platform/CONTRACT.md](../platform/CONTRACT.md). Values below are examples.

**Recording** (learn mode, one folder per recording, `device/intake/recorder.py`):

| Kept | File |
|------|------|
| Feature rows, 1 a second, with per-minute min, mean and max | `features/features_NNNNN.npz`, one per hour |
| Raw sensor snippets, 10 s every 10 min and around sudden events | `raw/clip_*.npz` |
| History state, so trend features survive a restart | `history.npz` |
| Events: start, stop, sensor lost, device moved, gaps, cleanup | `events.jsonl` |

Each hour file holds `X` (rows x 278), `t0`, `feature_names`, `feature_sensor`, `feature_text`, `feature_kind`, `label` and `features_version`. `features_version` changes whenever the feature list changes; check it before training.

**Model file** (`effente-model-1`, JSON, about 50 kB): format and `features_version`, library entry name and whether it was calibrated or trained on the machine alone, the preparation statistics, summary period and persistence, the threshold, the autoencoder's int8 weights with scales, the residual spreads, the fault classifier, the fault types (name, text, sensing), the feature table (name, text, unit), and a training report. The device refuses a model built on a different feature list.

**Log record** (one JSON object per window, written by `device/intake/live.py` and uploaded per the contract):

| Field | Example |
|-------|---------|
| `device_id` | `effente-01` |
| `machine_id` | `conveyor-07` |
| `at` | `2026-11-14T16:02:11Z` |
| `diagnosis` | `unbalance_like` |
| `diagnosis_this_minute` | `unbalance_like` |
| `confidence` | 0.71 |
| `unknown_score` | 2.4 (above 1 is abnormal) |
| `features` | `{"vib_1x": 7.2, "vib_velocity_mm_s": 3.1}` (readings that moved, in spreads) |
| `fault_types` | `{"unbalance_like": 9.1, "vibration_too_high": 4.2}` |
| `note` | `""` |
| `spectrum` | 20 vibration bands, log10 levels |

A fake-data generator and two simulators (`effente/simulate_conveyor.py`, `effente/simulate_server.py`) produce recordings in this format, with realistic faults, so model and platform work starts before the first real recording.

## Recording protocol

- Each normal session is recorded long enough for training: at least 5 minutes of running, longer is better.
- The sensor is removed and re-attached between sessions.
- Fault sessions are recorded after the normal ones, on the same mount and on a fresh mount.
- The test set contains only sessions the model never trained on.
- The sensitivity sweep records imbalance with progressively smaller weights.

## Validation

| Check | Output |
|-------|--------|
| Held-out normal sessions, including re-attached ones | Share of periods that alarm |
| Fault sessions | Alarm or not, time to alarm, fault type named |
| Sensitivity sweep | Smallest detected imbalance |
| NASA IMS run-to-failure data | Time between first alarm and failure. Reported on the branch: no false alarms in the 68 h after learning, first alarm 9.8 h before failure (MPU6050-like conversion) |
| A fault no type covers | `unrecognised_pattern` rather than a wrong name |
| Detector comparison (`training/anomaly/bench.py`) | 8 detectors on simulated and public data |
| int8 runtime vs trained network | Score correlation |

## Repository layout

```
firmware/    Arduino sketch for the UNO Q microcontroller
device/      Python for the UNO Q Linux side: intake, recording, device app
device/cad/  Enclosure, sensor head and demo conveyor CAD and STL files
effente/     Shared Python: features, history, preparation, trainer, runtime, fault types, simulators
training/    Recording tools, model CLI, detector comparison
platform/    Log ingestion, dashboard and agent fleet
docs/        PRD, TRD, architecture, model lifecycle
```

The agent currently lives in `device/agent/`. It moves under `platform/` as part of the move to the laptop.

## Tracks

| Track | Core deliverable | Stretch |
|-------|------------------|---------|
| 1. Device | Sampling, bridge, screen and button, device app with entry picker and calibrating mode, WiFi upload | Day-0 rules |
| 2. Data and signal | Protocol, multi-session recordings, sensitivity sweep, IMS dataset | Microphone, thermal camera, variable speed |
| 3. Model | Library entries (`conveyor`, `generic_rotating`), calibration on top of an entry, trainer, runtime, fault types, model file, validation | Fault test rig, more library entries |
| 5. Platform, agent and business | Dashboard, agent fleet, validation slides, pitch | Alarm feedback, fleet view |

Track 4 was merged into track 3. The number is left unused so track 5 keeps its name.

Tracks 2 and 3 can stall the others, which is why the fake-data generator and the shared formats come first.

## Schedule and rules

- **Before Thu 9pm:** sensor wired to the UNO Q and streaming clean readings to a laptop.
- **First hour:** agree the formats, commit the fake-data generator.
- **Sat 1pm (practice demos):** the demo runs end to end: snap on, pick `conveyor`, calibrate, then normal, possible unbalance, possible looseness, unrecognised pattern. Anything not working at that point is cut.
- **After Sat 1pm:** stretch work only, added on top of the working demo.
- **Sun 11am:** clock stops.

The university cluster is used only for stretch work. Nothing in the demo depends on a queued job.

## Fallbacks

| If this fails | Then |
|---------------|------|
| Calibration on a library entry | Use the existing flow: record the conveyor for 5+ minutes and train a model on it alone, before going on stage |
| Training on the device | Train on the laptop and copy the model file over |
| On-device inference | Run inference on the laptop with the UNO Q as the sensor |
| WiFi upload | Send the log over USB |
| Agent or network | Demo detection and explanation alone |
| Fault type named wrongly on stage | Show the group and the readings that moved; the name is already shown as "possible" |

## Risks

| Risk | Mitigation |
|------|------------|
| Model memorises one sensor placement | Re-attach between sessions, test on unseen sessions |
| Re-attaching shifts readings enough to alarm | Steel probe points, re-attachment in the test set |
| Fault names wrong on real faults | Shown as "possible"; group and readings that moved shown with them |
| Too little conveyor data to build the library entry | Record well before Saturday; the trainer refuses under 5 minutes |
| Calibration too short to set a good threshold | Measure on recordings; lengthen calibration rather than ship false alarms |
| Conveyor roller speed below the speed search range | See the signal pipeline open question |
| Uneven sample timing distorts the spectrum | Sampling on the microcontroller, not Linux |
| Venue WiFi | Own phone hotspot |
| Readings change with hand pressure or angle | Magnetic head holds itself on a fixed probe point |
