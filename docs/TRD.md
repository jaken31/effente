# effente - Technical Requirements

Companion to [PRD.md](PRD.md). Numbers marked "starting value" are first guesses to tune once real recordings exist.

## Architecture

```
 accelerometer   microphone   thermal camera
        \             |             /
 UNO Q microcontroller (STM32)      real-time sampling, timestamps, 2.4" screen
        |  Arduino Bridge (RPC)
 UNO Q Linux side (Debian, 4 GB)    per-sensor features, classifier, unknown alarm,
        |                           explanation table, log, agent loop
        |  WiFi (phone hotspot)
 Laptop platform                    log store, dashboard, switch-off toggle
        |
 Claude API, Tavily                 used by the agent when online
```

The Arduino UNO Q is the only device board. Its microcontroller handles timing-sensitive work and its Linux side runs the same Python used for training, so there is no port of the model to C on the critical path.

All three sensors connect to the UNO Q. Starting choice: the accelerometer and thermal camera on the microcontroller over I2C, and the microphone on the microcontroller over I2S. Audio is the heaviest stream (16 kHz of 16-bit samples, about 32 KB/s). If the Bridge cannot carry it reliably, the microphone moves to a USB microphone on the Linux side, which still timestamps every block.

Detection, explanation and the unknown alarm run with no network. The agent needs a connection.

## Hardware

- Arduino UNO Q, 4 GB
- Accelerometer: MPU6050 or ADXL345
- Microphone: I2S MEMS microphone (INMP441 or SPH0645), with a USB microphone as the fallback
- Thermal camera: MLX90640 (32x24 pixels), with an AMG8833 (8x8) as the cheaper fallback
- 2.4" OLED screen
- Small fan or motor as the demo machine, run at one fixed speed
- Hair dryer to warm the motor housing for the overheating case
- Matte black tape for the spot the thermal camera watches (shiny metal gives false readings)
- Tang Nano 20K FPGA (stretch track only)

## Signal pipeline

Each sensor has its own rate and its own features. All three are cut into the same one-second windows.

| Step | Vibration | Sound | Heat |
|------|-----------|-------|------|
| Sensor | 3-axis accelerometer | MEMS microphone, mono | Thermal camera, 32x24 |
| Sample rate | 1 kHz, 3 axes | 16 kHz | 4 frames per second |
| Window | 1024 samples (about 1 second) | 1 second (16,000 samples) | The frames inside the same second |
| Transform | Hann window, FFT magnitude per axis | Hann window, FFT, summed into 64 log-spaced bands | Frame minus its median (the background), averaged down to an 8x6 grid; rise in the hottest pixel over the last 30 seconds |
| Normalisation | Log magnitude, scaled with statistics from the training set | Log band energy, scaled with statistics from the training set | Scaled with statistics from the training set |

All values in the table are starting values.

A fan at 1,000 to 3,000 RPM turns 17 to 50 times a second, so the once-per-turn peak sits well inside the measurable range for both the accelerometer and the microphone.

Heat is measured relative to the rest of the image, not as an absolute temperature. This keeps the model from reacting to the room getting warmer.

**Alignment.** The microcontroller stamps every accelerometer sample, audio block and thermal frame with its own clock. A window is one second of that clock, and each sensor contributes whatever falls inside it. Windows are aligned by timestamp, never by sample count.

One implementation of this pipeline, in Python, is used for recording, training and on-device inference. That is the guard against training on one transform and running another.

## Model

- Inputs: the vibration, sound and heat features of one window.
- One small hidden block per sensor, joined before the output layer. Each hidden unit therefore belongs to one sensor, which tells us where every feature comes from.
- Outputs: normal, imbalance, loose mount, overheating.
- Trained with sensor dropout: during training, one sensor's inputs are sometimes set to zero, so the model still works when a sensor is missing. On the device, a missing sensor's block is set to zero the same way.
- Trains in seconds on a laptop.
- Exported as a file of whole-number weights. The device loads it, and the FPGA track builds from the same file.

## Unknown alarm

- Score: distance of the hidden-layer activity from the training distribution.
- Threshold: set on held-out normal recordings.
- Test: train without one fault type, then check the alarm fires on it.

## Interpretability

1. For each hidden unit, find which inputs drive it (frequency bins, sound bands or thermal grid cells) and which faults it fires on.
2. Switch the unit off and measure the change in each output.
3. A unit, or group of units, becomes a named feature only if switching it off removes the matching diagnosis and leaves the others intact.
4. Each named feature gets one sentence and its sensor in the explanation table. Expected examples: `once_per_turn` (vibration), `rattle` (vibration or sound), `hot_spot` (heat).
5. If units mix several faults each, train a sparse autoencoder on the hidden activity and repeat steps 1 to 4 on its features. This runs on the university cluster and is not on the critical path.

The device applies a mask to hidden units before the output layer. The platform sets that mask over WiFi. That is the whole mechanism behind the live switch-off.

## Agent

- Runs on the UNO Q Linux side.
- Model: Claude, called over WiFi. Lookups: Tavily.
- Input: the device log, restricted to diagnoses and verified features.
- Output: a maintenance report, a repair priority, and a part or manual reference.
- Rule: it may cite only features present in the explanation table.
- Offline: the agent is skipped and the device says so. Detection is unaffected.

## Shared formats

Agreed in the first hour so the tracks do not wait on each other. Values below are examples.

**Recording file** (one per capture, `.npz`). A sensor that was not connected has empty arrays.

| Field | Example |
|-------|---------|
| `accel` | int16 array, shape (N, 3) |
| `accel_t_us` | int64 array, shape (N,), microcontroller time of each sample |
| `accel_rate_hz` | 1000 |
| `audio` | int16 array, shape (M,) |
| `audio_t_us` | int64 array, microcontroller time of the first sample of each block |
| `audio_block_len` | 256 (samples per block) |
| `audio_rate_hz` | 16000 |
| `thermal` | float32 array, shape (F, 24, 32), degrees C |
| `thermal_t_us` | int64 array, shape (F,), microcontroller time of each frame |
| `thermal_rate_hz` | 4 |
| `label` | `imbalance` |
| `session_id` | `s03` |
| `mount_id` | `m2` (changes each time the device is re-attached) |
| `fan_rpm` | 1800 |
| `recorded_at` | `2026-11-13T02:15:00Z` |

**Model file** (`.json`): layer sizes, which inputs and hidden units belong to each sensor, whole-number weights and biases, scale factors, normalisation statistics, class names, unknown-alarm threshold, and the feature table (feature name, sensor, unit indices, sentence).

**Log record** (one JSON object per window):

| Field | Example |
|-------|---------|
| `device_id` | `effente-01` |
| `at` | `2026-11-14T16:02:11Z` |
| `diagnosis` | `imbalance` |
| `confidence` | 0.94 |
| `unknown_score` | 0.12 |
| `features` | `{"once_per_turn": 0.81, "rattle": 0.03, "hot_spot": 0.02}` |
| `mask` | `[]` (features switched off) |
| `sensors` | `{"vibration": "ok", "sound": "ok", "heat": "missing"}` |
| `vibration_spectrum` | downsampled magnitudes for the dashboard |
| `sound_bands` | the 64 band energies |
| `thermal_grid` | the 8x6 grid, degrees above background |

A fake-data generator produces recordings in this format so the model, interp and platform tracks start before the first real recording:

- Imbalance: a sine at the rotation frequency in the accelerometer, and the same tone and its harmonics in the audio.
- Loose mount: broadband bursts in the accelerometer, and clicks in the audio.
- Overheating: a hot spot in the thermal frames that warms over about a minute.
- Normal: low noise on all three, with a flat thermal image.

## Recording protocol

- Every fault is recorded across several sessions.
- The device is removed and re-attached between sessions, which moves the accelerometer, the microphone and the thermal camera's view together.
- The test set contains only sessions the model never trained on.
- The sensitivity sweep records imbalance with progressively smaller weights.
- Overheating: warm the housing with the hair dryer, switch the dryer off, then record. The fan stays on. Let the housing cool fully before the next session. Recording only with the dryer off stops the model from learning the dryer's noise and airflow instead of the heat.
- Some normal sessions are recorded with venue noise around, so loud surroundings are not taken as a fault.

## Validation

| Check | Output |
|-------|--------|
| Held-out sessions | Accuracy and confusion matrix |
| Sensitivity sweep | Smallest detected imbalance |
| NASA IMS run-to-failure data | Time between first alarm and failure |
| Leave-one-fault-out | Unknown-alarm detection rate and false-alarm rate |
| Switch-off tests | Change in each output per feature |
| Sensor ablation | Accuracy with each sensor removed, per fault |
| Live unplug | Diagnosis still works with each sensor disconnected in turn |

## Repository layout

```
firmware/    Arduino sketch for the UNO Q microcontroller: all three sensors, timestamps, screen
device/      Python for the UNO Q Linux side: inference service, agent
effente/     Shared Python: signal pipeline per sensor, model, formats, fake data
training/    Recording tools, training, validation
interp/      Unit analysis, switch-off tests, sparse autoencoders
platform/    Log ingestion and dashboard
fpga/        Tang Nano 20K design (stretch)
docs/        PRD, TRD
```

## Tracks

| Track | Core deliverable | Stretch |
|-------|------------------|---------|
| 1. Device | Sampling and timestamps for all three sensors, bridge, screen, inference service, WiFi upload | Local language model |
| 2. Data and signal | Protocol, multi-session recordings with all three sensors, sensitivity sweep, IMS dataset | Variable speed |
| 3. Model and FPGA | Training, sensor dropout, unknown alarm, model file | Trimmed classifier as a circuit on the Tang Nano 20K |
| 4. Mech interp | Unit analysis, switch-off tests, feature table, mask | Sparse autoencoders on the cluster |
| 5. Platform, agent and business | Dashboard with spectra and thermal image, toggle, agent, validation slides, pitch | Fleet view |

Tracks 2 and 3 can stall the others, which is why the fake-data generator and the shared formats come first.

## Schedule and rules

- **Before Thu 9pm:** accelerometer wired to the UNO Q and streaming clean readings to a laptop. Microphone and thermal camera wired and each returning readings.
- **First hour:** agree the three formats, commit the fake-data generator.
- **Build order:** vibration end to end first, then sound, then heat. A sensor joins the model only once its own data is streaming, timestamped and recorded.
- **Sat 1pm (practice demos):** the five-case demo (normal, imbalance, loose mount, overheating, unknown) runs end to end. Anything not working at that point is cut. A sensor that is cut takes its fault case with it, and the demo runs on the rest.
- **After Sat 1pm:** stretch work only, added on top of the working demo.
- **Sun 11am:** clock stops.

The university cluster is used only for stretch work. Nothing in the demo depends on a queued job.

## Fallbacks

| If this fails | Then |
|---------------|------|
| On-device inference | Run inference on the laptop with the UNO Q as the sensor |
| WiFi upload | Send the log over USB |
| Audio over the Bridge | USB microphone on the Linux side |
| MLX90640 | AMG8833, with the thermal grid reduced to match |
| Any one sensor | Drop it. The model is trained with sensor dropout and runs on the other two. |
| Agent or network | Demo detection and explanation alone |
| FPGA track | Drop it. Nothing depends on it. |

## Risks

| Risk | Mitigation |
|------|------------|
| Model memorises one sensor placement | Multi-session protocol, test on unseen sessions |
| Model learns fan speed, not the fault | Fixed speed, stated openly |
| Hidden units do not map cleanly to faults | Sparse autoencoder fallback |
| Uneven sample timing distorts the spectrum | Sampling on the microcontroller, not Linux |
| Sensors drift out of step with each other | One microcontroller clock for all timestamps, windows aligned by time |
| Bridge too slow for audio | USB microphone on the Linux side |
| Venue noise read as a fault | Normal sessions recorded with venue noise, microphone mounted close to the motor |
| Model learns the hair dryer, not the heat | Record overheating only after the dryer is off |
| Shiny metal gives false thermal readings | Matte tape on the watched spot, heat judged relative to the background |
| One sensor dominates and the others add nothing | Sensor ablation shows it. Report the result honestly. |
| Venue WiFi | Own phone hotspot |
