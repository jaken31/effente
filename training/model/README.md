# The model

One model per machine, learned on the device's learn-mode recording of that machine, run on the device with NumPy only. Two parts:

```
features ──> per-minute median ──> Prep: how far from THIS machine's normal ──> autoencoder ──> "is it normal?"
                                                                                    │
                                            how far each reading is off, by name ──┘
                                                                                    │
                                                                                    v
                                                              fault types ──> "vibration_too_high", "local_hot_spot", ...
```

- **Normal model** (an autoencoder): learns to reproduce this machine's normal minutes. Its score is how badly it reproduces the current minute. An alarm needs the score over the threshold for the median of the last few minutes.
- **Fault types** ([effente/fault_types.py](../../effente/fault_types.py)): 21 general types, the same on every machine. Each is a signature over named features: which readings move, and which way. They're blended with a small classifier trained on fault patterns injected into this machine's own normal variation. If no type fits, the result is `unrecognised_pattern`, with the readings that moved.

Why an autoencoder: in the comparison of 8 models ([training/anomaly](../anomaly)), it was near the top on both real datasets tried:
- the NASA bearing run to failure: no false alarms, the alarm well before the failure
- the DCASE 2024 fan sounds: AUC 0.61–0.63, against 0.63 for the similarity model, the best

It also says how far each reading is off given the others, which the fault types need. It runs in NumPy with whole-number weights, as the TRD's model file asks, and it has hidden units for the mech-interp track. The similarity model (MSET-style) is the closest rival; re-check both on real device recordings when they exist.

## Train

```bash
python3 -m training.model.train --run data/fake/conveyor-2wk-faults --learn-days 7 --out models/conveyor.json
```

```bash
python3 -m training.model.train --rows data/real/nasa-ims/test2_bearing1.npz --learn-hours 72 --smooth 3 --out models/ims-bearing1.json
```

`--run` takes a learn-mode folder and uses its per-minute medians. `--rows` takes any file with `t`, `X` and `feature_names`. Running rows only. The last 15 % of the time is held out to set the threshold, never a random split. Training takes seconds on a laptop.

## Calibrate a library entry

```bash
python3 -m training.model.calibrate --entry library/conveyor --run data/rec/conveyor-07 --out models/conveyor-07.json
```

`--entry` is a library entry folder: `manifest.json` and `model.json`. `--run` and `--rows` are the same inputs as for training. Calibration ([effente/calibration.py](../../effente/calibration.py), the code the device runs too) keeps the entry's network and refits to this machine what the trainer fits for one machine, with the trainer's own steps:

- the preparation statistics (`Prep`: median and spread of each reading the entry uses)
- the spread of each reading's residual
- the alarm threshold
- the fault classifier
- the safety baseline

It refuses less running time than the trainer does (300 summary periods: 5 minutes at 1 s), or `--min-running-s`. The output is an ordinary model file: no format change. Its `training_report` names the entry and holds the fit check.

The manifest fields calibration reads, in the layout of `training/library` (PR #33, not merged yet):

```json
{"entry_id": "conveyor", "version": "0.1.0",
 "heldout_score_quantiles": {"p50": 2.09, "p90": 2.58, "p99": 26.3, "max": 40.0}}
```

Only `p50` is needed. The quantiles are the entry model's scores on its own held-out rows, the last 15 % of its training recording (`effente.calibration.score_quantiles` gives the same four numbers).

### Fit check

The TRD leaves the rule open. The proposal: score the calibration rows on the entry as shipped, before anything is refitted, and divide their median by the entry's own held-out median. The machine fits if that ratio is at most `FIT_MAX_MEDIAN_RATIO` = 20 (starting value, set on simulated data). If it doesn't fit, the model is still written and the report says so.

Measured on the simulators only, 13 Nov (unverified on real machines). Four stand-in entries: 30 minutes of a simulated conveyor, seeds 1 and 11, in both simulator modes. Each machine below gave 7 minutes of rows; the conveyors were also scored on their first 5:

| Machine | Median ratio |
|---------|--------------|
| 10 other conveyors of the entry's mode: other seeds, load 0.8 to 1.2, room -6 to +8 C, 06:00 to 20:00, one with factory disturbances, one straight after start-up | 3.7 to 12.9 |
| 7 server racks: 6 seeds and hours, one with data-center disturbances | 33.8 to 60.3 |
| The same conveyor in the simulator's other mode (another mounting and sensor set) | 10.0 to 13.5 |

The limit of 20 is about 1.5 times the worst conveyor and 1.7 times below the best server rack. Entries trained on 15 and 20 minutes gave the same picture (conveyors 5.4 to 11.7, server racks 38.7 to 52.4).

The two entries of PR #33 (built in the simulator's realistic mode), read from its branch and scored on the same machines:

| Machine | `conveyor` entry | `generic_rotating` entry |
|---------|------------------|--------------------------|
| 12 conveyors in realistic mode | 4.1 to 13.5 | 2.8 to 7.6 |
| 7 server racks | 61.1 to 65.2 | 37.5 to 40.0 |
| 10 conveyors in the other mode (another mounting and sensor set) | 16.2 to 22.2 | 9.9 to 13.3 |

So a conveyor mounted and sensed differently from the entry's machines sits at the limit on the `conveyor` entry and passes on `generic_rotating`. The server rack fails both.

What didn't work: comparing the calibrated model's scores with the entry's. Once each reading is in spreads of its own machine's normal, the server rack scored 0.6 to 0.9 times the entry's held-out median, the same as another conveyor.

Expect the limit to move. A conveyor of the right type scores 4 to 13 times the entry's own data here because these entries saw 30 minutes of one machine. An entry trained on days of several machines should put the right type much nearer 1.

### How quiet a calibrated model stays (simulator)

A short calibration only knows the minutes it saw. On the simulated conveyor the load drifts over tens of minutes, and one-a-second rows from 5 to 30 minutes weren't enough to stay quiet for the half hour after, for a calibrated model or for one trained on the machine alone from the same rows:

| Rows used | Calibrated: alarm seconds in the next 5 minutes (3 runs) | Trained alone |
|-----------|--------------|---------------|
| 5 minutes | 131, 153, 231 of 300 | 164, 228, 218 |
| 10 minutes | 7, 4, 0 | 42, 13, 0 |
| 15 minutes | 40, 90, 61 | 62, 60, 0 |
| 20 minutes | 9, 0, 0 | 1, 0, 24 |

So calibration is no noisier than full training on the same rows, but neither is safe at these lengths on this simulator. The readings that drift furthest include the hour of the day (`time_hour_sin`, `time_hour_cos`), which only ever moves one way in a few minutes. This is the TRD's risk "calibration too short to set a good threshold". It needs measuring on the real conveyor before the demo.

## Run on the device

```bash
python3 -m device.intake.live --sim conveyor --loader training/model/effente_loader.py --model models/conveyor.json --log results.jsonl
```

The model slot passes each row with its time. The runtime ([effente/model_runtime.py](../../effente/model_runtime.py)) builds minute summaries itself, the same way the recorder does, and judges once a minute. On the UNO Q a judgement takes 6 ms and loading the file 8 ms, with no scikit-learn or SciPy needed (measured). A file trained on a different feature list is refused.

Each result has the TRD's log-record fields:

| Field | Meaning |
|-------|---------|
| `diagnosis` | `warming_up`, `normal`, `stopped`, a fault type, or `unrecognised_pattern`. During an abnormal stretch, the type named most often so far, so it doesn't flip minute to minute (`diagnosis_this_minute` has this minute's) |
| `confidence` | 0–1 |
| `unknown_score` | The smoothed score over the threshold: above 1 is abnormal |
| `features` | The readings that moved most, in spreads (the evidence the agent may cite) |
| `fault_types` | Every type with evidence, strongest first |
| `note` | For example "sound only so far: may be the room", or how many readings are far outside anything seen while learning |

## The model file

JSON, about 50 kB:
- int8 weights with one scale per neuron, and int16 biases with a scale
- the normalisation statistics (`Prep`)
- the threshold (gamma fit, set for one false alarm a week)
- the fault-classifier weights and classes
- the fault-type table
- the feature table
- a training report, including how closely the int8 runtime matches the trained network (0.994–0.9996 so far)

## How a reading counts as evidence

A reading counts only as far as both of these agree, in the same direction:
- its residual, measured in spreads of its own normal residual
- its distance from its usual value

The first stops a reading that's high for a normal reason, such as full load, from counting. The second stops the autoencoder's spread-out error from blaming readings that never moved. A reading 6 or more spreads from usual counts as it is, because an autoencoder can copy an extreme value straight through.

## Tested so far

- **Synthetic machine** (`python3 -m training.model.tests.test_model`, 6 tests): the int8 runtime matches; a new normal day gives at most 1 % alarm minutes; a once-per-turn rise is named `unbalance_like`; a swinging temperature is named `excess_temperature_fluctuation`; a stopped machine isn't judged; the device's model slot loads the file and refuses one made with other features.
- **Real bearing run to failure** (NASA/IMS test 2, bearing 1, as an MPU6050 would record it): learned on 72 h, no false alarms in the next 68 h, first alarm 9.8 h before the failure, named `vibration_too_high`. The fault type is unsure there: the signatures aren't tuned on real faults, and the very last snapshot (the rig stopping) was misread.

- **Calibration** (`python3 -m training.model.tests.test_calibration`, 8 tests, about half a minute, needs scipy for the simulators): the network's int8 weights are unchanged; the result loads in the runtime and scores; the int8 runtime matches the float network on the new machine; a model calibrated on simulated conveyor B from an entry trained on conveyor A is quiet in the 4 minutes after a 10-minute calibration and alarms when looseness starts in those minutes, where the entry as shipped alarms nearly all the time; the fit check passes for conveyor B and fails for the server rack; too little running time and unusable entries are refused; the command line.

## Limits

- The fault types are signatures and injected patterns, not learned from real labelled faults (unverified). Expect them to improve once real faults are recorded and used to train the classifier.
- The accelerometer sees bearing wear late (hours before failure on the NASA bearing, as expected for a 1 kHz sensor).
- The model is per machine. A new machine needs its own learn period: days to a month, covering every normal way it runs.
- This folder is track 3's under AGENTS.md. It's here at the user's request and needs their OK.
