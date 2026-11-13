# Calibration

Calibration fits a library entry to the machine the device was just snapped onto, from a few minutes of that machine running. The code is [effente/calibration.py](../../effente/calibration.py), NumPy only, so the device runs it.

Nothing here has been checked on a real machine. Every number below is simulated (unverified).

```python
from effente.calibration import calibrate, fit_check

model, report = calibrate(entry_model, t, X, names, min_running_s=300, seed=0)
verdict = fit_check(report, manifest)     # {"ok": bool, "ratio": float, "reason": str}
```

`entry_model` is the entry's `model.json` as a dict, `manifest` its `manifest.json`. `t, X, names` are one-a-second feature rows, as for `effente.trainer.train_model`. The result loads in `effente.model_runtime.Model` unchanged. `calibrate` raises `ValueError` if the machine ran for less than `min_running_s`.

## What it does

Kept from the entry: the network, the PCA score scales, the readings it uses, the persistence.

Fitted to this machine: the median and spread of each reading (`Prep`), the residual spreads, the alarm threshold, the safety baseline. The time split is the trainer's: the last 15 % is held out for the threshold.

Three guards, because a few minutes cannot show how far slow readings move in a day:

- Spreads are multiplied by the entry's `calibration.widen` factors. An entry without them (a plain trained model) has its own spreads as the floor.
- Readings the entry lists in `calibration.hold`, and readings this machine did not give while calibrating, are not judged. That includes a 10-minute trend that only starts after a 5-minute calibration.
- The threshold is fitted here but never set below the entry's.

The fault classifier is fitted only if the entry carries one. The library's entries carry none, so faults are named by the signatures alone. See the fault table below for why.

No key is added to the model file. `training_report` holds the calibration report (`"calibrated": true`), with the entry's own report under `entry_training_report`.

## From a laptop

```bash
python3 -m training.calibration.calibrate --entry training/library/entries/conveyor/0.1.0 --run <recording folder> --out models/conveyor-07.json
```

Exit code 0 if the machine fits the entry, 2 if not (the file is still written), 1 if there was too little running.

## Tests

```bash
python3 -m training.calibration.tests.test_calibration
```

18 tests on synthetic machines, NumPy only. The library entry in them is a fixture built in the test file.

## Measured on simulated machines

```bash
python3 -m training.calibration.measure --entry training/library/entries/conveyor/0.1.0 --entry training/library/entries/generic_rotating/0.1.0 --classifier
```

Run on 13 Nov with the two entries of branch `track3/library-build` (commit 1be1879). The simulators need SciPy. The machines are the repo's conveyor simulator with four seeds, loads and room temperatures, and its server rack with three seeds, cut down to the sensors the device has today. The conveyor entry was built from the same simulator, so these are new seeds, not a new conveyor design.

Share of seconds in alarm during the 40 minutes of normal running after a 300 s calibration (simulated):

| Entry | Machine | Calibrated | Trained on the same 300 s alone |
|-------|---------|-----------:|--------------------------------:|
| conveyor | 4 conveyors | 0.000 to 0.002 | 0.938 to 0.978 |
| conveyor | 3 server racks | 0.000 | 0.730 to 0.899 |
| generic_rotating | all 7 | 0.000 | as above |

Calibration length (simulated): from 60 s to 1200 s the alarm share stayed between 0.000 and 0.002 on every machine and both entries. So on the simulators 60 s was as good as 300 s. This is not a reason to shorten it on real machines yet.

Faults on a simulated conveyor, switched on 10 minutes after a 300 s calibration (simulated):

| Entry | Fault | Alarm share during | First alarm | Named, signatures alone | Named, classifier fitted in calibration |
|-------|-------|-------------------:|------------:|-------------------------|------------------------------------------|
| conveyor | unbalance, obvious | 0.987 | 12 s | unbalance_like | impacts |
| conveyor | unbalance, subtle | 0.914 | 33 s | unbalance_like | vibration_too_high |
| conveyor | looseness, obvious | 0.982 | 16 s | unbalance_like (wrong) | unbalance_like (wrong) |
| conveyor | looseness, subtle | 0.940 | 50 s | looseness_or_rattle | looseness_or_rattle |
| generic_rotating | all four | 0.000 | never | - | - |

No alarm before any fault began. The `generic_rotating` entry caught nothing: its threshold (122.6) is too high. That is the entry's, not calibration's.

## The fit check does not work yet

The rule is `ratio <= 3`, where ratio is this machine's median held-out score after calibration over the entry's own held-out median. It is a starting value.

On the simulators it passed every machine on every entry: ratios 0.38 to 0.42 for conveyors on the conveyor entry, and 0.52 to 0.56 for server racks on it (simulated). Once each reading is in spreads of its own machine, a server rack scores about like a conveyor. So today the check only catches a gross mismatch (the synthetic "other type" machine in the tests scores 3.6). A rule that tells machine types apart needs recordings of real machines, or another statistic. The TRD already lists the rule as open.

A fit that cannot be checked (no held-out median in the manifest, another feature list) is reported as not fitting, with the reason.

## Limits

- Simulated only. No number here says anything certain about a real machine.
- The threshold floor and the widen factors trade sensitivity for fewer false alarms. How much sensitivity is lost on real faults is not measured.
- The device app also needs the early warning's own calibration (`effente.early_warning.calibrate`) after this. That call is in `device/intake/device_app.py`, track 1's.
