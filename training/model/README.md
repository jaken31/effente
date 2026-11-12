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

## Limits

- The fault types are signatures and injected patterns, not learned from real labelled faults (unverified). Expect them to improve once real faults are recorded and used to train the classifier.
- The accelerometer sees bearing wear late (hours before failure on the NASA bearing, as expected for a 1 kHz sensor).
- The model is per machine. A new machine needs its own learn period: days to a month, covering every normal way it runs.
- This folder is track 3's under AGENTS.md. It's here at the user's request and needs their OK.
