# The model library

A set of pretrained networks, one per machine type, that the device calibrates to the machine in front of it (TRD, "Model library and calibration"). This folder builds them.

Everything here was built and measured on simulated data. Nothing has been checked on a real machine or with the device. Unverified.

## Layout

```
training/library/
  build.py        builds entries
  catalog.py      what each entry is built from (its list of sources)
  sources.py      the kinds of source: simulated machines, device recordings, public sets
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

Needs NumPy and SciPy (the simulators use SciPy). Both entries took 2 to 5 minutes on a 16-core laptop that was busy with other work. The build prints the model file's size, the held-out scores and each scorecard check.

The same seed and the same code give the same bytes, on one machine with one NumPy version. Checked for the committed entries: a rebuild from a clean checkout of the commit named in their manifests gave the same four files, byte for byte. Across machines the last digits of a float may differ; that has not been tested. To make this possible the build replaces the two clock readings the trainer puts in a model file: `created` is the date of the git commit built from, and the training report's `fit_seconds` is null.

Tests (slow, they simulate machines: 9 minutes of processor time, 18 minutes by the clock on that laptop when it was heavily loaded):

```bash
python3 -m training.library.tests.test_library
```

## The manifest

| Field | Meaning |
|-------|---------|
| `entry_id`, `version` | For example `generic_rotating`, `0.1.0` |
| `normal_model` | `pca`, the trainer's default |
| `features_version` | Copied from `model.json` |
| `sources` | Each `{name, kind, used_for}`. Kind is `simulated`, `public` or `device`. Used for `training` or `evaluation` |
| `heldout_score_quantiles` | `p50`, `p90`, `p99`, `max` of the model's score on the rows the trainer held out (the last 15 % of the time). Calibration's fit check compares a new machine against these |
| `scorecard` | Per fault type: `not validated`, `simulated only` or `validated on <data>` |
| `status` | `demo`, `unverified` or `verified` |
| `built` | `command`, `seed`, `git_commit` (with `-dirty` if the code differed from the commit) |

## How an entry is built

1. **Rows.** Each training source gives one-a-second feature rows of one machine on one mount. Running rows only, as the trainer.
2. **Several machines.** See the next section.
3. **Training.** `effente.trainer.train_model`, unchanged, with one-second rows. Calibration lasts minutes, so the trainer gives it one-second rows; the entry is trained on the same kind of row.
4. **Held-out scores.** The trained model scores the rows the trainer held out. The split is the trainer's own; a test proves it by getting the trainer's exact threshold back from those rows.
5. **Scorecard.** See below.

## How rows from several machines are combined

Calibration keeps an entry's network and refits only `Prep` (each reading's median and spread) to the machine. So the network only ever sees how far each reading is from that machine's own normal, in that machine's own spreads. It must be trained on the same thing.

So before pooling, each machine's rows are re-expressed with that machine's own `Prep` median and spread, on the scale of the first source. Then the rows are pooled and handed to the trainer. A test checks the result: the trainer's `Prep` on the pooled rows sees each row as far from normal as that machine's own `Prep` does.

Pooling raw rows would be wrong. The biggest differences in raw rows are between machines (one runs faster, one is mounted harder). The network would learn those, and calibration removes exactly those before the network sees anything.

Pooled rows are ordered by how far through its own recording each row is. The trainer's split by time then holds out the last 15 % of every machine.

An entry with one source skips all of this: its rows go to the trainer as recorded.

Consequences to know about:

- The `Prep`, baseline and alarm threshold stored in a multi-machine entry are on the first source's scale. They are placeholders. Calibration must refit them.
- The held-out rows are later rows of machines the network trained on, not rows of an unseen machine. A new machine may score higher than `heldout_score_quantiles` for that reason alone. The fit check's rule should allow for it.

## Entries

Both are version 0.1.0, built with seed 0. All numbers are from that build, on simulated data.

| | `generic_rotating` | `conveyor` |
|---|---|---|
| Status | `unverified` | `demo` |
| Training rows | 6 simulated drives, 30 minutes each (10,800 rows: 9,180 learned, 1,620 held out) | 1 simulated conveyor, 30 minutes (1,800 rows: 1,530 learned, 270 held out) |
| Inputs, patterns kept | 228, 40 | 227, 40 |
| Held-out score p50 / p90 / p99 / max | 2.585 / 3.339 / 4.632 / 36.242 | 2.086 / 2.579 / 26.307 / 40.002 |
| The entry's own alarm threshold | 36.24 | 59.85 |
| `model.json` size | 143,404 bytes | 143,385 bytes |

### generic_rotating

Trained on six simulated machines. Each is `effente.simulate_conveyor` in realistic mode (MPU-6500 noise levels) with factory disturbances from `effente.site_noise`, Monday 08:00 to 08:30. They differ in:

| Source | Speed | Load | Mount: head turned (x, y, z degrees) | Mount: vibration passed on | Room |
|--------|-------|------|------|------|------|
| `sim_drive_a` | 1.00x | 1.00x | 0, 0, 0 | 1.0x | +0 C |
| `sim_drive_b` | 0.70x | 0.85x | 90, 0, 0 | 0.6x | +3 C |
| `sim_drive_c` | 1.30x | 1.10x | 0, 90, 0 | 1.5x | -3 C |
| `sim_drive_d` | 0.55x | 1.20x | 0, 0, 90 | 0.8x | +6 C |
| `sim_drive_e` | 1.55x | 0.70x | 30, 45, 0 | 1.2x | +0 C |
| `sim_drive_f` | 0.85x | 0.95x | 180, 0, 20 | 2.0x | -5 C |

These settings are choices to spread the machines out, not measurements. Speed is relative to the simulator's motor shaft, about 29.5 Hz. The simulator has no speed or mount setting, so `sources.py` adds both without changing it: a subclass that scales the drive's speed, and a wrapper that turns the sensor head and scales the vibration it passes on.

Evaluation-only sources, listed in the manifest. The build does not run them, and no evaluation has been run:

- `sim_pump`: `effente.simulate_day`'s cooling-water pump. It models an MPU6050 (noise, range and filter), so it does not train the entry. This is a deviation from the request to train on `simulate_day`; see "Decisions".
- `nasa_ims_test2_bearing1`: `training/record/datasets/ims.py`, an MPU6050-like conversion.
- `ottawa_uods_vafdc`: `training/record/datasets/ottawa.py`. It uses the MPU-6500's low-pass filter but the MPU6050's noise figure.
- `dcase2024_fan_sound`: `training/record/datasets/dcase.py`. Sound only, no accelerometer.

No MPU-6500 conversion of a public set exists in the repo. The public data was not downloaded for this work.

### conveyor

Trained on the conveyor simulator alone (`sim_conveyor`: realistic mode, factory disturbances, Monday 08:00 to 08:30). Status `demo`.

The simulator is an industrial belt conveyor, not the demo's mini conveyor. Recordings of the demo conveyor replace it with a one-line change to `CONVEYOR.sources` in `catalog.py`, one `Recording` per session:

```python
Recording("conveyor_session_01", "data/real/conveyor/session-01"),
```

Each session is treated as its own machine when pooling, so re-attaching the sensor between sessions does not blur the network. A test builds an entry from a recorded folder to check this path. A recording cannot have a fault switched on, so the scorecard then stays `not validated` until fault sessions are recorded and a check for them is written.

## Scorecard

For each check in `catalog.py`, the build carries on the first training machine from the end of its training rows, runs one normal minute, then switches one simulated fault on at full size for 5 minutes. The device's own `Watcher` judges it. A fault type is `simulated only` if the Watcher stayed quiet before the fault and named that type at the end. Otherwise it is `not validated`.

Result for both entries: all 21 fault types are `not validated`.

No simulated fault raised an alarm. The scores did rise, but stayed under each entry's own alarm threshold. Highest smoothed score as a share of the threshold (above 1.0 is an alarm), simulated data:

| Simulated fault | Expected fault type | `generic_rotating` | `conveyor` |
|-----------------|---------------------|------|------|
| unbalance | `unbalance_like` | 0.24 | 0.21 |
| misalignment | `misalignment_like` | 0.37 | 0.33 |
| looseness | `looseness_or_rattle` | 0.64 | 0.60 |
| splice_damage | `repeating_knock` | 0.10 | 0.08 |
| motor_bearing | `bearing_defect_like` | 0.33 | 0.34 |
| belt_slip | `speed_change` | 0.44 | 0.31 |
| electrical | `electrical_motor` | 0.34 | 0.27 |
| belt_rub | `new_tone_whine_squeal` | 0.40 | 0.33 |
| lubrication_loss | `grinding_or_hiss` | 0.21 | 0.17 |
| cooling_blocked | `overheating` | 0.14 | 0.09 |

Why the thresholds are so high: the trainer never sets the threshold below the highest held-out score, and the held-out rows contain simulated site disturbances. With one-second rows, a disturbance a few seconds long gets past the 5-row persistence (in the `conveyor` entry, 6 held-out rows in a row score far above normal). So the thresholds are 36 and 60, against a normal median score of 2.6 and 2.1.

This matters beyond the scorecard. An entry used without calibration would not alarm on these faults. A calibration whose held-out part happens to contain no disturbance will set a low threshold and may then alarm on the next disturbance. Both are for the calibration and validation work to measure.

The checks run on a machine the entry trained on, with the entry's own threshold. They do not go through calibration, which did not exist when this was built.

## What is unverified

- Everything. No device recording exists, so no fault type is validated on real data, and the entries have never seen a real machine.
- The simulators' levels are plausible, not measured.
- `generic_rotating` is six variants of one simulated machine type. No pump, fan or other machine is in its training rows.
- Each machine contributes 30 minutes of steady mid-shift running. Breaks, low load, start-ups and other normal states are not in the training rows.
- The public and pump evaluations were not run.
- Whether calibration works on these entries. That is the calibration track's to measure.
- Byte-identical builds across machines or NumPy versions.

## Decisions

- **`simulate_day` is evaluation only.** The request was to train `generic_rotating` on it too. It models an MPU6050, and the TRD says never to mix MPU6050 data with MPU-6500 training data. The build refuses any training source that is not MPU-6500. To overrule this, the sensor rule in `build.py` has to change, not only the catalog.
- **Site disturbances are in the training rows**, as in `training/record/conveyor_eval`. Training on clean simulated data gives a normal no real site has. The cost is the high thresholds above.
- **One-second rows.** Matches what calibration uses. An entry trained on one-minute rows would be scored on a different kind of row after calibration.
- **Model file size.** About 143 kB, nearly three times the TRD's "about 50 kB". Most of it is the network (228 inputs x 40 patterns, 64 kB), the fault classifier (29 kB), the feature table (22 kB) and `Prep` (20 kB). The build already writes the JSON without spaces. Getting to 50 kB needs a trainer or format change (fewer patterns or inputs), which is out of scope here.
- **`built.git_commit`** is written with an underscore; the shared header says "git commit".
- **No change to `effente/`.** The trainer, runtime and model file format are untouched.

## Open questions

- Should the fit check compare against held-out rows of an unseen machine instead of later rows of trained-on machines?
- Should the alarm threshold of a library entry be set at all, given calibration replaces it?
- Is one second the right row period for calibration, given how site disturbances get past the persistence?
- Which shaft does the demo conveyor's unbalance show up on (TRD open question)? The simulated conveyor's motor turns at about 29.5 Hz; the demo conveyor is not measured.
