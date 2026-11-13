# effente - Technical Requirements

Companion to [PRD.md](PRD.md). Numbers marked "starting value" are first guesses to tune once real recordings exist.

The data pipeline, model and fault types described here are built on branch `track2/data-intake` (Finn). Figures quoted from that branch are as reported there, not re-checked.

## Architecture

```
 magnetic sensor head: accelerometer
        |  short cable
 UNO Q microcontroller (STM32)      real-time sampling, 2.4" screen, button
                                    (+ optional mic, thermal camera, room sensor in the body)
        |  Arduino Bridge (RPC)
 UNO Q Linux side (Debian, 4 GB)    features, model library, calibration,
        |                           small network per machine, fault types, log
        |  WiFi (phone hotspot)
 Laptop platform                    log store, dashboard, agent fleet
        |  localhost
 Model server on the same laptop    llama-server; the agents' language model

 Nothing leaves the site: no cloud model, no web search.
```

The Arduino UNO Q is the only device board. Its microcontroller handles timing-sensitive work and its Linux side runs the same Python used for training, so there is no port of the model to C on the critical path.

The device runs only the small per-machine network (a library network calibrated to the machine, or one trained on the machine alone) and the fault-type rules. Detection, explanation and learning need no network. All agents run on the laptop, on a language model served on that same laptop. Neither the device nor the platform calls a cloud provider or any other outside service (PRD P9a).

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

Display, Recording, Training and Watching exist in `device/intake/device_app.py` on `track2/data-intake`. Calibrating and the entry picker are new (track 1 for the device app, track 3 for calibration). Neither is in the device app yet.

Picking the entry in the laptop app (PRD P1b) is not built. The device only makes outgoing requests, so the laptop cannot push an entry: the device would fetch it on its next check-in, over the local network or USB, and keep the previous one. That needs an addition to [platform/CONTRACT.md](../platform/CONTRACT.md), which all tracks must agree.

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

Status on Nov 13: the library and calibration are in review and not yet on master. Every number in this section comes from simulated machines. No recording from our own device exists yet.

- **Hackathon library:** `conveyor`, to be trained on our own recordings of the demo conveyor plus the conveyor simulator, and `generic_rotating`, trained on the simulators. Until device recordings exist, both are simulator-only and labelled demo or unverified. Public data converted from another sensor is used for evaluation only, never training. The `conveyor` entry is trained on the same conveyor it is demoed on, so the demo proves the flow, not that the entry generalises to other conveyors. Say so if asked.
- **What an entry is built on:** accelerometer and gyro readings only, a long recording (hours to days, not 30 minutes), and a 1-second summary period. With sound, heat and room readings included, the pooled alarm threshold came out so high that no simulated fault alarmed. An entry trained on 30 minutes has spreads as narrow as a short calibration's, which makes calibration noisy.
- **Calibration:** keeps the entry's network and fits this machine's preparation statistics (`Prep`: median and spread of each reading). Two rules are starting values:
    - No reading's spread goes below the entry's.
    - The alarm threshold is not refit freely from the calibration data. Refit freely from 5 minutes, it dropped well below the entry's and alarmed on most normal running (76 to 92 % of the time on unseen simulated conveyors). Held at the entry's value, false alarms stopped but a looseness fault was caught far less often (0.18 to 0.27 of the watch period, against 0.83 with only the spread rule). The rule between those two is being measured.
- **Calibration length:** starting value 10 minutes of running. Five minutes looked too short in simulation. The 10-minute trend readings have no data for the first 8 minutes of a recording, so a shorter calibration keeps the entry's values for them.
- **Fit check:** if the calibration data scores far worse on the entry than the entry's own held-out data did, the screen says the machine does not look like that type and suggests `generic_rotating` or a long stay. The rule is open; set it from recordings. In simulation the check did not separate a server rack from a conveyor, so a pass is not proof that the right entry was picked.
- **Fault classifier:** retrained during calibration on fault patterns added to this machine's own normal variation, as in full training.

After the hackathon the library grows by machine type and the setup agent picks the entry ([model-lifecycle.md](model-lifecycle.md)).

## Model

The network inside every library entry, and the model a long stay trains on one machine alone, are the same kind, built with NumPy only (`effente/trainer.py`, `effente/model_runtime.py`).

- **Normal model:** PCA by default (`NORMAL_MODEL` in `effente/trainer.py`), scored by its residual and by Hotelling's T2. A small denoising autoencoder is the other option. Either one learns to rebuild this machine's normal readings, and its score is how badly it rebuilds the current period.
- **Summary period:** per-period medians of the feature rows. The period follows the recording length: about 1 second for a 20-minute demo recording, one minute for a week.
- **Split by time:** the last 15 % of the recording is held out to set the alarm threshold. Never a random split.
- **Threshold:** a gamma fit to the held-out scores, set for one false alarm a week, never below the highest held-out score.
- **Persistence:** an alarm needs the median of the last 5 periods over the threshold (starting value).
- **Whole-number weights:** int8 with a scale per neuron. The device runtime checks it gives the same scores as the trained network.
- **Cost on the UNO Q:** reported on the branch as 6 ms per judgement and 8 ms to load a model file of about 50 kB. That was measured for the autoencoder and has not been re-measured for PCA.

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
- Model: a language model served on the same laptop. No cloud model and no web search: nothing about a machine leaves the site.
- Input: the platform's stored log records for a machine.
- Output: a maintenance report and a repair priority. The report keeps a part reference field, left empty: parts lookup needs an outside search service, so it is off.
- Rule: a report may cite only readings that appear as moved readings (`features`) in the records it read.
- No model server running: the run ends with a plain reason and the dashboard says so. A rule-based stand-in can write the report instead, labelled as a stand-in. Detection on the device is unaffected.

For the hackathon the fleet is one agent, the maintenance report agent. The setup and review agents in [model-lifecycle.md](model-lifecycle.md) come later.

### Harness

The harness is the code around the model call (`device/agent/harness.py`). Keep it small: one call, and hard checks in our own code.

- **Model server:** llama.cpp's `llama-server` on `127.0.0.1:8081`. The call uses only the OpenAI-style routes (`GET /v1/models`, `POST /v1/chat/completions`), so Ollama or any server with those routes also works. Standard library only; no extra package.
- **No tool loop:** the model is given no tools to call. The harness fetches `get_readings` and `get_feature_table` itself, records both on the run like any tool call, and sends their output with the instructions.
- **Output:** a structured report with a fixed shape: summary, priority, cited readings, recommended action, part reference. The server is asked to force that shape (`response_format` with the report's JSON schema); if it refuses, plain JSON is asked for instead. The shape is in the prompt either way.
- **Checks in code** (`check_report` in `device/agent/report.py`), for every report whoever wrote it:
    - the shape: the right keys and types, nothing extra
    - every cited reading moved in the records supplied, in a window diagnosed as a fault type
    - the free text names no reading that is not cited, unless that reading was switched off
    - every number in the free text appears in the readings supplied
- **Failed checks:** one retry. If the report still fails, it is discarded and the run says why.
- **Errors:** a timeout on every request. "No server running", "did not answer in time" and "answer was not a report" are reported separately.
- **Logging:** save each request, tool call, response and token count to the agent log. The dashboard shows the tool calls live.
- **Tests:** fake logs with known right answers (possible unbalance, possible looseness, unrecognised pattern, healthy), run against a fake model server. Run them after every prompt change.

Settings:

- Backend: `--backend local` (the default) or `--backend standin`, on `python3 -m device.agent`.
- Server address: `--llm-url`, else `EFFENTE_LLM_URL`, else `http://127.0.0.1:8081`.
- Model files: outside the repo, never committed. They reach a customer's laptop on a USB stick or with the installer, not by a download on first run.

Not done yet:

- The local call has only been run against the fake server in the tests, never a real model.
- No model has been chosen. A comparison of models on our own alarm records decides which one, and the smallest laptop that can run it.
- The desktop app does not start the model server, has no model setting, and its Agents page still offers "Live Claude".
- `platform/report.py` still takes `--live`.

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

**Model file** (`effente-model-1`, JSON, about 50 kB): format and `features_version`, library entry name and whether it was calibrated or trained on the machine alone, the preparation statistics, summary period and persistence, the threshold, the normal model's int8 weights with scales (a PCA model is stored as a linear autoencoder), the residual spreads, the fault classifier, the fault types (name, text, sensing), the feature table (name, text, unit), and a training report. The device refuses a model built on a different feature list.

**Library entry** (in review, not yet on master): one folder per entry, `training/library/entries/<entry_id>/`, holding two files.

- `model.json`: a plain `effente-model-1` file, exactly as `effente.trainer.train_model` writes it. No added fields. A new key in the model file is a format change and needs all five people.
- `manifest.json`: everything else about the entry.

| Manifest field | Holds |
|----------------|-------|
| `entry_id` | For example `generic_rotating` |
| `version` | For example `0.1.0` |
| `normal_model` | `pca` |
| `features_version` | Copied from `model.json` |
| `sources` | List of `{name, kind, used_for}`. `kind` is `simulated`, `public` or `device`; `used_for` is `training` or `evaluation` |
| `heldout_score_quantiles` | `{p50, p90, p99, max}` of the normal model's score on the last 15 % of training time |
| `scorecard` | Per fault type: `not validated`, `simulated only`, or `validated on <data>` |
| `status` | `demo`, `unverified` or `verified`. Nothing is `verified` without recordings from our own device |
| `built` | `{command, seed, git_commit}` |

Settings that calibration reads per entry, such as readings to hold at the entry's values, also go in the manifest.

**Calibration interface** (`effente/calibration.py`, in review, not yet on master):

```
calibrate(entry_model: dict, t, X, names, *, min_running_s=300, seed=0) -> (model: dict, report: dict)
fit_check(report: dict, manifest: dict) -> {"ok": bool, "ratio": float, "reason": str}
```

The returned model is a valid `effente-model-1` dict that `effente.model_runtime.Model` loads unchanged.

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
| 5. Platform, agent and business | Dashboard, agent fleet, validation slides, pitch | Alarm feedback, fleet view, log in flow (accounts kept on the laptop, no outside service), user onboarding |

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
| Calibration on a library entry | Use the existing flow: record the conveyor and train a model on it alone, before going on stage. Record 20 minutes or more: in simulation a model trained on 5 minutes alone alarmed on most normal running. Untested on the real conveyor |
| Training on the device | Train on the laptop and copy the model file over |
| On-device inference | Run inference on the laptop with the UNO Q as the sensor |
| WiFi upload | Send the log over USB |
| Local model or its server | The rule-based stand-in report, labelled as a stand-in. Failing that, demo detection and explanation alone |
| Fault type named wrongly on stage | Show the group and the readings that moved; the name is already shown as "possible" |

## Risks

| Risk | Mitigation |
|------|------------|
| Model memorises one sensor placement | Re-attach between sessions, test on unseen sessions |
| Re-attaching shifts readings enough to alarm | Steel probe points, re-attachment in the test set |
| Fault names wrong on real faults | Shown as "possible"; group and readings that moved shown with them |
| Too little conveyor data to build the library entry | Record well before Saturday; the trainer refuses under 5 minutes |
| Calibration too short to set a good threshold | Measure on recordings; lengthen calibration rather than ship false alarms |
| Keeping the entry's alarm threshold makes a calibrated device miss faults | Measure false alarms and faults caught together before choosing the threshold rule |
| A pass on the fit check is read as proof of machine type | It is not; the screen and the pitch do not say so |
| A local model writes advice the readings do not support | Checks in code reject what they can; a person reads the reports in the model comparison |
| Conveyor roller speed below the speed search range | See the signal pipeline open question |
| Uneven sample timing distorts the spectrum | Sampling on the microcontroller, not Linux |
| Venue WiFi | Own phone hotspot |
| Readings change with hand pressure or angle | Magnetic head holds itself on a fixed probe point |
