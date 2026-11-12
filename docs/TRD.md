# effente - Technical Requirements

Companion to [PRD.md](PRD.md). Numbers marked "starting value" are first guesses to tune once real recordings exist.

## Architecture

```
 magnetic sensor head: accelerometer (+ mic)
        |  short cable
 UNO Q microcontroller (STM32)      real-time sampling, 2.4" screen
        |  Arduino Bridge (RPC)
 UNO Q Linux side (Debian, 4 GB)    FFT, classifier, unknown alarm,
        |                           explanation table, log, agent loop
        |  WiFi (phone hotspot)
 Laptop platform                    log store, dashboard, switch-off toggle
        |
 Claude API, Tavily                 used by the agent when online
```

The Arduino UNO Q is the only device board. Its microcontroller handles timing-sensitive work and its Linux side runs the same Python used for training, so there is no port of the model to C on the critical path.

Detection, explanation and the unknown alarm run with no network. The agent needs a connection.

## Hardware

- Arduino UNO Q, 4 GB
- Accelerometer: MPU6050 (GY-521). Its built-in filter passes vibration up to about 260 Hz (datasheet figure, not re-checked), so the usable spectrum ends there. A BNO085 is on hand as a spare only
- 2.4" OLED screen
- Sensor head: printed puck with a magnet in the base, on a short cable
- Steel discs glued to each machine as probe points
- USB power bank
- Microphone (after vibration works end to end)
- Small fan or motor as the demo machine, run at one fixed speed

The device is handheld. Only the small sensor head touches the machine, held by its magnet, so nobody presses or holds it during a reading. Research and drawing: [physical-design.md](research/physical-design.md), [mockup.svg](mockup.svg).

## Signal pipeline

| Step | Starting value |
|------|----------------|
| Sample rate | 1 kHz, 3 axes |
| Window | 1024 samples (about 1 second) |
| Windowing | Hann |
| Transform | FFT magnitude per axis |
| Normalisation | Log magnitude, scaled with statistics from the training set |

A fan at 1,000 to 3,000 RPM turns 17 to 50 times a second, so the once-per-turn peak sits well inside the measurable range.

One implementation of this pipeline, in Python, is used for recording, training and on-device inference. That is the guard against training on one transform and running another.

## Model

- Small fully connected network, one or two hidden layers, over the FFT bins.
- Outputs: normal, imbalance, loose mount.
- Trains in seconds on a laptop.
- Exported as a file of whole-number weights, which the device loads.

## Unknown alarm

- Score: distance of the hidden-layer activity from the training distribution.
- Threshold: set on held-out normal recordings.
- Test: train without one fault type, then check the alarm fires on it.

## Interpretability

1. For each hidden unit, find which frequency bins drive it and which faults it fires on.
2. Switch the unit off and measure the change in each output.
3. A unit, or group of units, becomes a named feature only if switching it off removes the matching diagnosis and leaves the others intact.
4. Each named feature gets one sentence in the explanation table.
5. If units mix several faults each, train a sparse autoencoder on the hidden activity and repeat steps 1 to 4 on its features. This runs on the university cluster and is not on the critical path.

The device applies a mask to hidden units before the output layer. The platform sets that mask over WiFi. That is the whole mechanism behind the live switch-off.

## Agent

- Runs on the UNO Q Linux side.
- Model: Claude, called over WiFi. Lookups: Tavily.
- Input: the device log, restricted to diagnoses and verified features.
- Output: a maintenance report, a repair priority, and a part or manual reference.
- Rule: it may cite only features present in the explanation table.
- Offline: the agent is skipped and the device says so. Detection is unaffected.

### Harness

The harness is the code around the Claude call. Keep it small: a short loop, a few tools, and hard checks in our own code.

- **Loop:** the tool runner in the Python `anthropic` SDK. We write tools as plain functions and host the loop on the device. Not Managed Agents, because the tools read local device data.
- **Tools:** `get_readings(machine_id, since)`, `get_feature_table()`, `search_parts(query)` (Tavily), and optionally `get_history(machine_id)`.
- **Output:** a structured report with a fixed shape: summary, priority, cited features, part reference. `cited_features` is restricted to the names in the feature table, so the API rejects any feature that is not verified.
- **Check in code:** after the response, confirm every cited feature was active in the readings supplied. If not, discard the report and say so.
- **Offline and errors:** short timeout. Handle connection and rate-limit errors separately. On failure show "agent unavailable", queue the request, retry when WiFi returns.
- **Limits:** cap the number of tool rounds and the output size.
- **Logging:** save each request, tool call, response and token count to the device log. The dashboard shows the tool calls live.
- **Tests:** about ten fake logs with known right answers (imbalance, loose mount, unknown fault, healthy). Run them after every prompt change.

Settings:

- Model: `claude-opus-5-5`. Set the effort level explicitly, since its default is medium. Haiku 5.5 is the cheaper, faster option if needed.
- No forced tool calls: Opus 5.5 rejects them. Structured output is used instead.
- Caching: stable content first (instructions, tool definitions, feature table), changing log data last.
- Key: `ANTHROPIC_API_KEY` in the device's environment, never in the repo.

Fallback if time is short: one Claude call with the readings included and a structured report out, no tool loop. Add the parts lookup tool afterwards.

## Shared formats

Agreed in the first hour so the tracks do not wait on each other. Values below are examples.

**Recording file** (one per capture, `.npz`):

| Field | Example |
|-------|---------|
| `samples` | int16 array, shape (N, 3) |
| `sample_rate_hz` | 1000 |
| `label` | `imbalance` |
| `session_id` | `s03` |
| `mount_id` | `m2` (changes each time the sensor is re-attached) |
| `fan_rpm` | 1800 |
| `recorded_at` | `2026-11-13T02:15:00Z` |

**Model file** (`.json`): layer sizes, whole-number weights and biases, scale factors, normalisation statistics, class names, unknown-alarm threshold, and the feature table (feature name, unit indices, sentence).

**Log record** (one JSON object per window):

| Field | Example |
|-------|---------|
| `device_id` | `effente-01` |
| `machine_id` | `conveyor-07` (which machine was probed) |
| `at` | `2026-11-14T16:02:11Z` |
| `diagnosis` | `imbalance` |
| `confidence` | 0.94 |
| `unknown_score` | 0.12 |
| `features` | `{"once_per_turn": 0.81, "rattle": 0.03}` |
| `mask` | `[]` (features switched off) |
| `spectrum` | downsampled magnitudes for the dashboard |

A fake-data generator produces recordings in this format (a sine at the rotation frequency for imbalance, broadband bursts for looseness) so the model, interp and platform tracks start before the first real recording.

## Recording protocol

- Every fault is recorded across several sessions.
- The sensor is removed and re-attached between sessions.
- The test set contains only sessions the model never trained on.
- The sensitivity sweep records imbalance with progressively smaller weights.

## Validation

| Check | Output |
|-------|--------|
| Held-out sessions | Accuracy and confusion matrix |
| Sensitivity sweep | Smallest detected imbalance |
| NASA IMS run-to-failure data | Time between first alarm and failure |
| Leave-one-fault-out | Unknown-alarm detection rate and false-alarm rate |
| Switch-off tests | Change in each output per feature |

## Repository layout

```
firmware/    Arduino sketch for the UNO Q microcontroller
device/      Python for the UNO Q Linux side: inference service, agent
effente/     Shared Python: signal pipeline, model, formats, fake data
training/    Recording tools, training, validation
interp/      Unit analysis, switch-off tests, sparse autoencoders
platform/    Log ingestion and dashboard
docs/        PRD, TRD
```

## Tracks

| Track | Core deliverable | Stretch |
|-------|------------------|---------|
| 1. Device | Sampling, bridge, screen, inference service, WiFi upload | Local language model |
| 2. Data and signal | Protocol, multi-session recordings, sensitivity sweep, IMS dataset | Microphone, variable speed |
| 3. Model | Training, unknown alarm, model file | More fault types |
| 4. Mech interp | Unit analysis, switch-off tests, feature table, mask | Sparse autoencoders on the cluster |
| 5. Platform, agent and business | Dashboard, toggle, agent, validation slides, pitch | Fleet view |

Tracks 2 and 3 can stall the others, which is why the fake-data generator and the shared formats come first.

## Schedule and rules

- **Before Thu 9pm:** sensor wired to the UNO Q and streaming clean readings to a laptop.
- **First hour:** agree the three formats, commit the fake-data generator.
- **Sat 1pm (practice demos):** the four-case vibration demo runs end to end. Anything not working at that point is cut.
- **After Sat 1pm:** stretch work only, added on top of the working demo.
- **Sun 11am:** clock stops.

The university cluster is used only for stretch work. Nothing in the demo depends on a queued job.

## Fallbacks

| If this fails | Then |
|---------------|------|
| On-device inference | Run inference on the laptop with the UNO Q as the sensor |
| WiFi upload | Send the log over USB |
| Agent or network | Demo detection and explanation alone |

## Risks

| Risk | Mitigation |
|------|------------|
| Model memorises one sensor placement | Multi-session protocol, test on unseen sessions |
| Model learns fan speed, not the fault | Fixed speed, stated openly |
| Hidden units do not map cleanly to faults | Sparse autoencoder fallback |
| Uneven sample timing distorts the spectrum | Sampling on the microcontroller, not Linux |
| Venue WiFi | Own phone hotspot |
| Readings change with hand pressure or angle | Magnetic head holds itself on a fixed probe point |
