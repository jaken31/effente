# Calibration

Calibration fits a library entry to the machine the device was just snapped onto, from a few minutes of that machine running. The code is [effente/calibration.py](../../effente/calibration.py), NumPy only, so the device runs it.

Nothing here has been checked on a real machine. Every number below is simulated (unverified).

```python
from effente.calibration import calibrate, fit_check

model, report = calibrate(entry_model, t, X, names, min_running_s=300, seed=0)
verdict = fit_check(report, manifest)     # {"ok": bool, "ratio": float, "reason": str}
```

`entry_model` is the entry's `model.json` as a dict, `manifest` its `manifest.json`. `t, X, names` are one-a-second feature rows, as for `effente.trainer.train_model`. The result loads in `effente.model_runtime.Model` unchanged. `calibrate` raises `ValueError` if the machine ran for less than `min_running_s`.

One more keyword, after `seed`: `threshold_floor_share` (default 1.0, see below).

## What it does

Kept from the entry: the network, the PCA score scales, the readings it uses, the summary period, the persistence.

Fitted to this machine: the median and spread of each reading (`Prep`), the residual spreads, the alarm threshold, the safety baseline, and the fault classifier if the entry carries one. The time split is the trainer's: the last 15 % is held out for the threshold.

No key is added to the model file. `training_report` holds the calibration report (`"calibrated": true`), with the entry's own report under `entry_training_report`.

## The two floors are deliberate, and one of them costs sensitivity

A few minutes cannot show how far readings move in an hour. So the entry guards the fit:

- **Spread floor.** A reading's spread is never taken below the entry's own. If the entry carries `calibration.widen` factors (the entries of branch `track3/library-build` do), the fitted spread is multiplied by them instead.
- **Threshold floor.** The threshold is `max(fitted here, threshold_floor_share x the entry's threshold)`. The default share is 1.0.
- **Not judged.** Readings the entry lists in `calibration.hold`, and readings this machine did not give while calibrating, get a huge spread. That includes a 10-minute trend that only starts after a 5-minute calibration.

Evidence, all simulated. Machines: the repo's conveyor simulator with four seeds, loads and room temperatures, and its server rack with three seeds, device sensors only. Entries: the two of `feat/model-library` (73bda18) and the two of `track3/library-build` (1be1879), each copied out with `git show`.

```bash
python3 -m training.calibration.measure --entry <conveyor> --entry <generic_rotating>
```

| Share of seconds in alarm, 40 minutes of normal running after a 300 s calibration (simulated) | |
|---|---|
| Calibrated, `feat/model-library` entries, 14 calibrations | 0.000 |
| Calibrated, `track3/library-build` entries, 14 calibrations | 0.000 to 0.002 |
| A model trained on the same 300 s alone, no entry behind it | 0.730 to 0.978 |

The last row is what a short fit does with no guard at all. There is no committed run with only the spread floor switched off.

**The threshold refit is inert on the simulators.** In 28 of 28 calibrations the threshold fitted from the machine was below the entry's, so the entry's was used. On the `feat/model-library` entries the fitted value was 0.58 to 2.04 against entry thresholds of 59.85 and 36.24 (simulated). With the default share a calibrated model alarms exactly as high as its entry allows.

**The default share of 1.0 costs sensitivity.** Four faults on a simulated conveyor, switched on 10 minutes after a 300 s calibration (simulated):

```bash
python3 -m training.calibration.measure --entry <conveyor> --faults-only --threshold-floor-share 0    # then 0.5, then 1
```

| Entry | Share | Alarm share before the fault | Faults that raised an alarm | First alarm |
|-------|------:|-----------------------------:|----------------------------:|------------:|
| conveyor, `feat/model-library` (threshold 59.85) | 0 | 0.796 | 4 of 4 (already alarming) | 0 s |
| | 0.5 | 0.000 | 0 of 4 | never |
| | 1 | 0.000 | 0 of 4 | never |
| conveyor, `track3/library-build` (threshold 24.53, widen and hold) | 0 | 0.023 | 4 of 4 | 3 to 12 s |
| | 0.5 | 0.000 | 4 of 4 | 9 to 36 s |
| | 1 | 0.000 | 4 of 4 | 12 to 50 s |

Looseness alone, the obvious one: with the `track3/library-build` entry it alarmed in 0.994, 0.987 and 0.982 of the fault's seconds at shares 0, 0.5 and 1. With the `feat/model-library` entry it never alarmed at 0.5 or 1.

So the right share depends on the entry. With an entry whose own threshold is high, the full floor hides faults, and no floor alarms most of the time. The default of 1.0 is a starting value, not a recommendation. One machine and four faults were measured here; that is too few to choose a default from.

## Fault names

Simulated, `track3/library-build` conveyor entry, default share, run with `--classifier` added:

| Fault | Named, signatures alone | Named, classifier fitted in calibration |
|-------|-------------------------|------------------------------------------|
| unbalance, obvious | unbalance_like | impacts |
| unbalance, subtle | unbalance_like | vibration_too_high |
| looseness, obvious | unbalance_like (wrong) | unbalance_like (wrong) |
| looseness, subtle | looseness_or_rattle | looseness_or_rattle |

A classifier is fitted only if the entry carries one. The `track3/library-build` entries carry none. The `feat/model-library` entries do, so calibrating them refits it.

## Calibration length

Simulated, `track3/library-build` entries, default share: from 60 s to 1200 s the alarm share after calibration stayed between 0.000 and 0.002 on all 7 machines. Since the entry's threshold was the one used every time, this says little about how short a calibration can be. It is not a reason to shorten it on real machines.

## The fit check does not tell machine types apart

The rule is `ratio <= 3`, where ratio is this machine's median held-out score after calibration over the entry's own held-out median (`heldout_score_quantiles.p50` in the manifest). The limit is a starting value, simulated only. The TRD lists the rule as open, and it stays open.

On the simulators every machine passed on every entry (simulated):

| Entry | Conveyors | Server racks |
|-------|----------:|-------------:|
| conveyor, `feat/model-library` | 0.23 to 0.25 | 0.25 to 0.27 |
| conveyor, `track3/library-build` | 0.38 to 0.42 | 0.52 to 0.56 |

Once each reading is in spreads of its own machine, a server rack scores about like a conveyor. A pass means only that no gross mismatch was found. It does not show the machine is of the entry's type. A check that could not be made (no held-out median in the manifest, another feature list) is reported as failed, with the reason.

## From a laptop

```bash
python3 -m training.calibration.calibrate --entry training/library/entries/conveyor --run <recording folder> --out models/conveyor-07.json
```

Exit code 0 if the fit check passed, 2 if it failed (the file is still written), 1 if there was too little running.

## Tests

```bash
python3 -m training.calibration.tests.test_calibration
```

21 tests on synthetic machines, NumPy only. The library entry in them is a fixture built in the test file.

## Known issues

- **Copied code.** `calibrate` repeats the trainer's fault-classifier block (inject, fit, quantize, about 12 lines of `effente.trainer.train_model`), and `refit_prep` repeats the median and spread statistics of `effente.model_input.Prep.fit`. Sharing them needs an edit to existing shared code, which needs the team's agreement. Until then a change to either original must be made here too.
- **Simulated only.** No number here says anything certain about a real machine.
- **Threshold floor.** See above: inert refit, and a default that costs sensitivity.
- **Fit check.** Cannot tell machine types apart; rule open.
- **Short calibration and the classifier.** A classifier fitted to 5 minutes named a simulated unbalance wrongly.
- **Early warning.** The device app also needs `effente.early_warning.calibrate` after this. That call is in `device/intake/device_app.py`, track 1's.
- **Two library layouts.** `feat/model-library` and `track3/library-build` write different manifests and model files. `fit_check` reads both manifests.
