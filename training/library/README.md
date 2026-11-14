# The model library

A set of pretrained networks, one per machine type, that the device calibrates to the machine in front of it (TRD, "Model library and calibration"). This folder builds them.

Everything here was built and measured on simulated data. Nothing has been checked on a real machine or with the device. Unverified.

## Layout

```
training/library/
  build.py        builds entries
  catalog.py      what each entry is built from (its list of sources)
  sources.py      the kinds of source: simulated machines, device recordings, public sets
  load.py         reads the library: load_catalog, and load_entry with its checks
  entries/<entry_id>/
    model.json      a plain effente-model-1 file (effente.trainer writes it, effente.model_runtime.Model loads it)
    manifest.json   what the entry is and how it was built
  tests/test_library.py
```

## Build

```bash
python3 -m training.library.build                          # every entry
python3 -m training.library.build generic_rotating --seed 0
```

Needs NumPy and SciPy (the simulators use SciPy). Both entries took 21 minutes on a 16-core laptop that was busy with other work, almost all of it simulating days of running. `generic_rotating` needs several GB of memory. The build prints the model file's size, the held-out scores and each scorecard check.

The same seed and the same code give the same bytes, on one machine with one NumPy version. A test builds a small entry twice and compares the bytes. Across machines the last digits of a float may differ; that has not been tested. To make this possible the build replaces the two clock readings the trainer puts in a model file: `created` is the date of the git commit built from, and the training report's `fit_seconds` is null.

Tests (under a minute; they simulate short machines and read the committed entries):

```bash
python3 -m training.library.tests.test_library
```

Read an entry:

```python
from training.library.load import load_entry
manifest, model = load_entry("training/library/entries", "conveyor")
```

`load_entry` refuses a model file whose sha1 is not the manifest's `model_sha1`, and one built on another feature list than this code computes.

## The manifest

| Field | Meaning |
|-------|---------|
| `entry_id`, `version` | For example `generic_rotating`, `0.1.0` |
| `normal_model` | `pca`, the trainer's default |
| `features_version` | Copied from `model.json` |
| `sources` | Each `{name, kind, used_for}`. Kind is `simulated`, `public` or `device`. Used for `training` or `evaluation`. An evaluation source also has `evaluated`: the build runs no evaluation, so it is `false` on every one. Some have a `note` |
| `heldout_score_quantiles` | `p50`, `p90`, `p99`, `max` of the model's score on the rows the trainer held out (the last 15 % of the time). Calibration's fit check compares a new machine against these |
| `scorecard` | Per fault type: `not validated`, `simulated only` or `validated on <data>` |
| `status` | `demo`, `unverified` or `verified` |
| `built` | `command`, `seed`, `git_commit` (with `-dirty` if the code differed from the commit) |
| `model_sha1` | sha1 of `model.json` |
| `calibration` | Notes for calibration, below. `model.json` gets no new key |

### Calibration notes

- `min_s`: 300, the calibration length the entries are built for.
- `hold`: readings a 5-minute calibration cannot refit (`time_` and `trend_` prefixes, `imu_temp_c`).
- `widen`: per reading the entry uses, how much wider its spread is over a whole training source than within one 300-row window (median over windows, kept within 1 to 5).
- `widen_use`: says the factors are recorded only. `effente.calibration` does not read them. Measured on simulated conveyors with the `conveyor` entry (see "After calibration"): with the factors, a calibrated machine alarmed 14 to 48 % of its running time; with the entry's own spreads as the floor, 9 to 15 %.
- `prep`, `threshold`: what the model file's own numbers stand for. In a pooled entry `Prep` holds the first source's medians and spreads, because the other machines were re-expressed onto them. Its medians are one machine's levels and its spreads are in that machine's units. The threshold is the trainer's, from the held-out rows of every machine.

## How an entry is built

1. **Rows.** Each training source gives one-a-second feature rows of one machine on one mount, from the accelerometer, the gyro and the IMU's temperature only. Running rows only, as the trainer.
2. **Several machines.** See the next section.
3. **Training.** `effente.trainer.train_model`, unchanged, with `period=1` said outright. Calibration lasts minutes, so its rows are one-second rows, and the entry is trained on the same kind. Left to itself the trainer picks 60-second periods for a recording of days, and calibration would then need 5 hours.
4. **Held-out scores.** The trained model scores the rows the trainer held out. The split is the trainer's own; a test proves it by getting the trainer's exact threshold back from those rows.
5. **Scorecard.** See below.

### Sensors

Entries use the accelerometer, the gyro and the IMU's temperature. The simulated sources drop the microphone, thermal camera and room sensor, so those readings are missing in every row and `Prep` leaves their columns out.

Why: with every sensor an entry was 220 columns and about 143 kB, and the sound and heat of simulated site disturbances pushed the threshold so high that no simulated fault alarmed. The cost: faults that show mainly in sound or heat are not caught (see "After calibration").

## How rows from several machines are combined

Calibration keeps an entry's network and refits only `Prep` (each reading's median and spread) to the machine. So the network only ever sees how far each reading is from that machine's own normal, in that machine's own spreads. It must be trained on the same thing.

So before pooling, each machine's rows are re-expressed with that machine's own `Prep` median and spread, on the scale of the first source. Then the rows are pooled and handed to the trainer. A test checks the result: the trainer's `Prep` on the pooled rows sees each row as far from normal as that machine's own `Prep` does.

Pooling raw rows would be wrong. The biggest differences in raw rows are between machines (one runs faster, one is mounted harder). The network would learn those, and calibration removes exactly those before the network sees anything.

Pooled rows are ordered by how far through its own recording each row is. The trainer's split by time then holds out the last 15 % of every machine.

An entry with one source skips all of this: its rows go to the trainer as recorded.

Consequences to know about:

- The `Prep` and baseline stored in a multi-machine entry are on the first source's scale. Calibration refits them. The manifest says so (`calibration.prep`).
- The held-out rows are later rows of machines the network trained on, not rows of an unseen machine.

## Entries

Both are version 0.1.0, built with seed 0. All numbers are from that build, on simulated data.

| | `generic_rotating` | `conveyor` |
|---|---|---|
| Status | `unverified` | `demo` |
| Training sources | 6 simulated drives, 24 hours each from Monday 08:00 | 1 simulated conveyor, 48 hours from Monday 08:00 |
| Running rows learned, held out | 322,779 and 56,961 | 113,521 and 7,079 |
| Inputs, patterns kept | 83, 37 | 87, 27 |
| Held-out score p50 / p90 / p99 / max | 11.34 / 18.10 / 28.14 / 36.10 | 4.66 / 12.89 / 16.82 / 19.18 |
| The entry's own alarm threshold | 46.14 | 50.84 |
| `model.json` size (target about 50 kB) | 57,614 bytes | 53,054 bytes |

### generic_rotating

Trained on six simulated machines. Each is `effente.simulate_conveyor` in realistic mode (MPU-6500 noise levels) with factory disturbances from `effente.site_noise`, for one whole day. They differ in:

| Source | Speed | Load | Mount: head turned (x, y, z degrees) | Mount: vibration passed on | Room |
|--------|-------|------|------|------|------|
| `sim_drive_a` | 1.00x | 1.00x | 0, 0, 0 | 1.0x | +0 C |
| `sim_drive_b` | 0.70x | 0.85x | 90, 0, 0 | 0.6x | +3 C |
| `sim_drive_c` | 1.30x | 1.10x | 0, 90, 0 | 1.5x | -3 C |
| `sim_drive_d` | 0.55x | 1.20x | 0, 0, 90 | 0.8x | +6 C |
| `sim_drive_e` | 1.55x | 0.70x | 30, 45, 0 | 1.2x | +0 C |
| `sim_drive_f` | 0.85x | 0.95x | 180, 0, 20 | 2.0x | -5 C |

These settings are choices to spread the machines out, not measurements. Speed is relative to the simulator's motor shaft, about 29.5 Hz. The simulator has no speed or mount setting, so `sources.py` adds both without changing it: a subclass that scales the drive's speed, and a wrapper that turns the sensor head and scales the vibration it passes on.

It is six variants of one machine type. One day each, not two: two days each did not fit in the memory of the laptop it was built on.

Evaluation-only sources, listed in the manifest with `"evaluated": false`. The build does not run them, and no evaluation has been run by it:

- `sim_pump`: `effente.simulate_day`'s cooling-water pump. It models an MPU6050 (noise, range and filter), so it does not train the entry.
- `sim_server_rack`: `effente.simulate_server`, another type of machine: the wrong machine for the fit check. Its accelerometer noise is 8.5 mg, not the MPU-6500's 3.5 mg, so it does not train the entry either.
- `nasa_ims_test2_bearing1`: `training/record/datasets/ims.py`, an MPU6050-like conversion.
- `ottawa_uods_vafdc`: `training/record/datasets/ottawa.py`. It uses the MPU-6500's low-pass filter but the MPU6050's noise figure.
- `dcase2024_fan_sound`: `training/record/datasets/dcase.py`. Sound only, no accelerometer.

No MPU-6500 conversion of a public set exists in the repo. The public data was not downloaded for this work.

### conveyor

Trained on the conveyor simulator alone (`sim_conveyor`: realistic mode, factory disturbances, two whole days). Status `demo`. `sim_server_rack` is listed as its wrong machine, evaluation only.

The simulator is an industrial belt conveyor, not the demo's mini conveyor. Recordings of the demo conveyor replace it with a one-line change to `CONVEYOR.sources` in `catalog.py`, one `Recording` per session:

```python
Recording("conveyor_session_01", "data/real/conveyor/session-01"),
```

Each session is treated as its own machine when pooling, so re-attaching the sensor between sessions does not blur the network. A test builds an entry from a recorded folder to check this path. A recording cannot have a fault switched on, so the scorecard then stays `not validated` until fault sessions are recorded and a check for them is written.

## Training length

The `conveyor` entry was built at several lengths from the same simulated conveyor, then calibrated for 5 minutes on four simulated conveyors it never saw (other seeds, speeds, loads and mounts) and watched for the next 24 hours. Simulated data, one training seed. In this run the calibrated threshold was never below the entry's own.

| Trained on | Entry threshold | Alarming, share of running time (4 machines) | Simulated faults caught, of 40 |
|-----------|------|------|------|
| 0.5 h | 488 | 0 % | not measured |
| 3 h | 7.1 | 64 to 99 % | not measured |
| 12 h | 57.9 | 9 to 15 % | 12 |
| 24 h | 81.4 | under 0.1 % | 0 |
| 30 h | 5.6 | 47 to 97 % | 27, with an alarm before the fault in 20 runs |
| 36 h | 4.9 | 49 to 97 % | 31, with an alarm before the fault in 30 runs |
| 48 h | 50.8 | 9 % | 8 |

48 hours was chosen: two whole working days, so every part of a day (both shifts, the breaks, the run-down, the night stop, the start-up) is in the training rows twice. Thirty minutes or three hours of mid-shift running is too little.

Length is not what decides the result, though. The entry's threshold swings from 5 to 488 with which clock hours fall in the trainer's last 15 %, and the threshold decides both how quiet and how deaf a calibrated machine is. At 24 and 48 hours from Monday 08:00 the held-out part is the shift start-up, which scores high, so the threshold is high. At 30 and 36 hours it is mid-shift, the threshold is about 5, and a machine calibrated at 08:00 alarms through most of the day, because 5 minutes do not show the lunch break, the run-down or the start-up. The split and the threshold fit are the trainer's, shared code, and are not changed here.

## After calibration

The committed entries through `effente.calibration.calibrate` (branch `track3/calibration` at 1290273, the plain `model.json` as shipped) on the same four unseen simulated conveyors and the simulated server rack. Simulated data only. That commit set the threshold with a floor of 0.25 of the entry's threshold. `calibrate` no longer does: the threshold is now a multiple of the machine's own held-out median score, and the numbers for that rule are in [training/calibration/README.md](../calibration/README.md). This section has not been rerun with it.

| | `conveyor` | `generic_rotating` |
|---|---|---|
| Threshold fitted on the calibration rows, as a share of the entry's | 0.03 to 0.10 | 0.03 to 0.06 |
| Threshold used (the floor: 0.25 of the entry's) | 12.71 | 11.54 |
| Alarming in the next 24 h, share of running time, after 5 minutes | 8.9, 9.0, 14.5, 9.0 % | 8.9, 9.0, 39.1, 8.9 % |
| The same after 10 minutes | 9.0, 9.0, 15.4, 9.0 % | 8.9, 9.0, 36.5, 9.0 % |
| Simulated faults caught after 5 minutes, of 40 | 15 | 12 |
| The same after 10 minutes | 15 | 13 |
| Fault named as the expected type, after 5 / 10 minutes, of 40 | 4 / 1 | 5 / 3 |
| Alarm before the fault | none in 40 runs | none in 40 runs |
| Server rack: fit check | passes (ratio 0.42 to 0.46) | passes (ratio 0.12 to 0.13) |

What this shows:

- **The fitted threshold is far below the floor**, so the entry's threshold decides everything (previous section).
- **One false alarm a day on every machine.** The 9 % is one episode from about 22:02 to 23:37, named `speed_change`: the run-down after the shift ends at 22:00. A calibration at 08:00 never saw it. The third machine (turned 45 and 30 degrees, vibration passed on 1.4x) also alarms `device_moved` on and off.
- **Faults caught are unbalance, misalignment and looseness (4 of 4 each)**, and with the `conveyor` entry the motor bearing (3 of 4). Splice damage, belt slip, electrical, belt rub, lubrication loss and blocked cooling are not caught on the accelerometer and gyro alone. The faults were switched on at full size, 25 minutes after calibration.
- **The server rack passes the fit check** and never alarms. The check does not tell it from a conveyor.

## Scorecard

For each check in `catalog.py`, the build carries on the first training machine from the end of its training rows, runs one normal minute, then switches one simulated fault on at full size for 5 minutes. The device's own `Watcher` judges it with the entry's own threshold, without calibration. A fault type is `simulated only` if the Watcher stayed quiet before the fault and named that type at the end. Otherwise it is `not validated`.

Result for both entries: all 21 fault types are `not validated`. Highest smoothed score as a share of the threshold (above 1.0 is an alarm), simulated data:

| Simulated fault | Expected fault type | `generic_rotating` | `conveyor` |
|-----------------|---------------------|------|------|
| unbalance | `unbalance_like` | 0.94 | 0.81 |
| misalignment | `misalignment_like` | 1.31, named `impacts` | 1.57, named `vibration_too_high` |
| looseness | `looseness_or_rattle` | 1.20, named `misalignment_like` | 1.53, named `misalignment_like` |
| splice_damage | `repeating_knock` | 0.23 | 0.09 |
| motor_bearing | `bearing_defect_like` | 0.39 | 0.37 |
| belt_slip | `speed_change` | 0.21 | 0.06 |
| electrical | `electrical_motor` | 0.25 | 0.21 |
| belt_rub | `new_tone_whine_squeal` | 0.20 | 0.06 |
| lubrication_loss | `grinding_or_hiss` | 0.22 | 0.07 |
| cooling_blocked | `overheating` | 0.20 | 0.06 |

Two faults now alarm, but under another type's name, so no type earns `simulated only`.

## What is unverified

- Everything. No device recording exists, so no fault type is validated on real data, and the entries have never seen a real machine.
- The simulators' levels are plausible, not measured.
- `generic_rotating` is six variants of one simulated machine type. No pump, fan or other machine is in its training rows.
- The public, pump and server-rack evaluation sources were not run by the build.
- The "Training length" and "After calibration" numbers are one training seed and four simulated machines, against a calibration branch that is still changing.
- Byte-identical builds across machines or NumPy versions.

## Decisions

- **`simulate_day` and the server rack are evaluation only.** `simulate_day` models an MPU6050; the server rack simulator's noise is not the MPU-6500's. The device uses the MPU-6500 and the TRD says never to mix the two, so the build refuses any training source that is not MPU-6500.
- **Accelerometer, gyro and IMU temperature only.** See "Sensors".
- **Site disturbances are in the training rows**, as in `training/record/conveyor_eval`. Training on clean simulated data gives a normal no real site has.
- **One-second rows.** Matches what calibration uses.
- **Model file size.** 53 and 58 kB against "about 50 kB". The build writes the JSON without spaces.
- **`built.git_commit`** is written with an underscore, as in the TRD's "Shared formats".
- **No change to `effente/`.** The trainer, runtime and model file format are untouched.

## Open questions

- Should the entry's threshold come from the trainer's last 15 % at all? Which clock hours land there decides it (see "Training length").
- How should a short calibration cope with parts of the day it never saw, such as the run-down?
- Should the fit check compare against held-out rows of an unseen machine instead of later rows of trained-on machines? Today it passes a server rack.
- Which shaft does the demo conveyor's unbalance show up on (TRD open question)? The simulated conveyor's motor turns at about 29.5 Hz; the demo conveyor is not measured.
