# Calibration

Calibration fits a library entry to the machine the device was just snapped onto, from a few minutes of that machine running. The code is [effente/calibration.py](../../effente/calibration.py), NumPy only, so the device runs it.

Nothing here has been checked on a real machine. Every number below is simulated (unverified).

```python
import json
from effente.calibration import calibrate, fit_check

entry_model = json.load(open("training/library/entries/conveyor/model.json"))
manifest = json.load(open("training/library/entries/conveyor/manifest.json"))
model, report = calibrate(entry_model, t, X, names, min_running_s=300, seed=0)
verdict = fit_check(report, manifest)     # {"ok": bool, "ratio": float, "reason": str}
```

`t, X, names` are one-a-second feature rows, as for `effente.trainer.train_model`. The result loads in `effente.model_runtime.Model` unchanged. `calibrate` raises `ValueError` if the machine ran for less than `min_running_s`. It reads the model file only, never the manifest.

One more keyword, after `seed`: `threshold_floor_share` (default 0.25, see below).

## What it does

Kept from the entry: the network, the PCA score scales, the readings it uses, the summary period, the persistence.

Fitted to this machine: the median and spread of each reading (`Prep`), the residual spreads, the alarm threshold, the safety baseline, and the fault classifier if the entry carries one (the library's entries do). The time split is the trainer's: the last 15 % is held out for the threshold.

No key is added to the model file. `training_report` holds the calibration report (`"calibrated": true`), with the entry's own report under `entry_training_report`.

## The floors

A few minutes cannot show how far readings move in an hour. So the entry guards the fit:

- **Spread floor.** A reading's spread is never taken below the entry's own.
- **Threshold floor.** The threshold is `max(fitted here, threshold_floor_share x the entry's threshold)`. The default share is 0.25 (`THRESHOLD_FLOOR_SHARE`). There is no upper bound. The rule is open and may be replaced.
- **Not judged.** A reading this machine did not give while calibrating (NaN in more than half the rows) gets a huge spread and is never judged, also when it appears later. A 10-minute trend that only starts after a 5-minute calibration is one.

The default of 0.25 is a starting value, simulated only. It is tied to the entry's threshold, so a different entry may need a different share. Where it came from: reported by the coordinating session, not reproduced here, from 12 simulated conveyors on a two-day stand-in entry.

### What the library's `widen` and `hold` do here: nothing

The library's entries keep `calibration.widen` and `calibration.hold` in `manifest.json` only. Their `model.json` has no `calibration` key, and `calibrate` does not read the manifest. So on the library's entries:

- **No widen factor is applied.** The entry's own spreads are the floor. A test checks this on the committed conveyor entry. The library session reports the factors measured worse than this floor: 14 to 48 % against 9 to 15 % alarm share (simulated; reported by the library session, not reproduced here).
- **No reading is held.** The clock, trend and IMU temperature readings are treated like any other. On a simulated conveyor with the `conveyor` entry (22 such readings), a 300 s calibration left 17 not judged because they had no value yet (the trends of 10 minutes and longer, and the heat trends), and refitted and judged 5: `imu_temp_c`, `time_hour_sin`, `time_hour_cos`, `trend_shocks_10m`, `trend_since_shock_s`. Those 5 get this machine's median and at least the entry's spread.

`calibrate` still honours `calibration.widen` and `calibration.hold` if a model file itself carries them (tested with a fixture). No entry on master does.

## Measured on simulated machines

Entries: the two on master, `training/library/entries/conveyor` (threshold 50.84) and `generic_rotating` (46.14). Machines: the repo's conveyor simulator with four seeds, loads and room temperatures, and its server rack with three seeds, device sensors only. The simulators need SciPy. Default share 0.25 throughout.

### After a 300 s calibration

```bash
python3 -m training.calibration.measure --entry training/library/entries/conveyor --entry training/library/entries/generic_rotating
```

Simulated, 7 machines per entry, 40 minutes of normal running after calibration:

| | `conveyor` | `generic_rotating` |
|---|---|---|
| Share of seconds in alarm | 0.000 on 6 machines, 0.002 on one | 0.000 on 6 machines, 0.002 on one |
| The same for a model trained on the same 300 s alone | 0.730 to 0.978 | 0.730 to 0.978 |
| Threshold fitted from the machine | 1.84 to 4.16 | 1.55 to 2.23 |
| Threshold used (the floor) | 12.71 | 11.54 |
| Fit check ratio, conveyors | 0.23 to 0.24 | 0.07 to 0.08 |
| Fit check ratio, server racks | 0.37 to 0.38 | 0.09 to 0.10 |

- **The threshold refit is inert.** In 14 of 14 calibrations the fitted threshold was below the floor, so the floor decided it. A calibrated model alarms as high as 0.25 x the entry's threshold, whatever the machine.
- **False alarms and the slow readings.** The 0.002 is 4 seconds on one conveyor. None of those seconds was led by a clock, trend or IMU temperature reading (the readings that moved most were `slow_band_00`, `vib_env_peak`, `vib_peak2`). So no false alarm here traces to the readings the manifest would hold. That is 40 minutes; it says nothing about a whole day.
- **A longer watch looks worse.** Reported by the library session, not reproduced here: over the next 24 simulated hours the calibrated entries alarmed about 9 % of the running time, one episode a day at the end of the shift, which a calibration at 08:00 never saw.

### Calibration length

Same command, simulated. From 60 s to 1200 s the alarm share after calibration stayed between 0.000 and 0.002 on all 7 machines and both entries. Since the floor set the threshold every time, this says little about how short a calibration can be. It is not a reason to shorten it on real machines.

### Faults

Four faults on one simulated conveyor (seed 2), switched on 10 minutes after a 300 s calibration (simulated):

```bash
python3 -m training.calibration.measure --entry training/library/entries/conveyor --entry training/library/entries/generic_rotating --faults-only
```

| Entry | Fault | Alarm share before | Alarm share during | First alarm | Named |
|-------|-------|-------------------:|-------------------:|------------:|-------|
| `conveyor` | unbalance, obvious | 0.000 | 0.977 | 21 s | unbalance_like |
| | unbalance, subtle | 0.000 | 0.777 | 59 s | vibration_too_high |
| | looseness, obvious | 0.000 | 0.984 | 14 s | misalignment_like (wrong) |
| | looseness, subtle | 0.000 | 0.956 | 40 s | looseness_or_rattle |
| `generic_rotating` | unbalance, obvious | 0.000 | 0.976 | 22 s | unbalance_like |
| | unbalance, subtle | 0.000 | 0.000 | never | - |
| | looseness, obvious | 0.000 | 0.983 | 15 s | looseness_or_rattle |
| | looseness, subtle | 0.000 | 0.950 | 45 s | looseness_or_rattle |

The `conveyor` entry caught 4 of 4, `generic_rotating` 3 of 4 (the subtle unbalance never alarmed). Two of the four were named as another type with the `conveyor` entry. The coordinating session reports 4 of 4 for full-size unbalance, misalignment and looseness on four unseen simulated conveyors (not reproduced here). This run agrees for the two full-size faults it has; it did not run misalignment, and it used one machine.

## The fit check does not tell machine types apart

The rule is `ratio <= 3`, where ratio is this machine's median held-out score after calibration over the entry's own held-out median (`heldout_score_quantiles.p50` in the manifest). The limit is a starting value, simulated only. The TRD lists the rule as open, and it stays open.

In the table above every machine passed on every entry: server racks scored 0.37 to 0.38 on the `conveyor` entry against 0.23 to 0.24 for conveyors (simulated). A pass means only that no gross mismatch was found. It does not show the machine is of the entry's type. A check that could not be made (no held-out median in the manifest, another feature list) is reported as failed, with the reason.

## From a laptop

```bash
python3 -m training.calibration.calibrate --entry training/library/entries/conveyor --run <recording folder> --out models/conveyor-07.json
```

Exit code 0 if the fit check passed, 2 if it failed (the file is still written), 1 if there was too little running. A test runs it on the committed entry folder.

## Tests

```bash
python3 -m training.calibration.tests.test_calibration
```

23 tests, NumPy only. Two use the committed `conveyor` entry and its manifest. The rest use synthetic machines and a fixture entry built in the test file.

## Earlier results, on entries that are not on master

Kept for the record. Both entries below were superseded; neither is in `training/library/`.

- **`feat/model-library` at 73bda18, conveyor entry with threshold 59.85** (simulated, one conveyor, four faults, `--faults-only --threshold-floor-share S`): share 0 alarmed in 0.796 of normal seconds; 0.25 caught 1 of 4 faults; 0.5 and 1 caught none.
- **`track3/library-build` at 1be1879, conveyor entry with threshold 24.53 and widen and hold in the model file** (simulated, same runs): every share from 0.25 to 1 caught 4 of 4 with no alarm before them. At 0.25 three of four conveyors alarmed in 3 to 7 % of the 40 normal minutes, against at most 0.2 % at 1. A classifier fitted in calibration named an obvious unbalance "impacts"; the signatures alone named it unbalance_like.

What carries over: the right share depends on the entry's threshold.

## Known issues

- **Copied code.** `calibrate` repeats the trainer's fault-classifier block (inject, fit, quantize, about 12 lines of `effente.trainer.train_model`), and `refit_prep` repeats the median and spread statistics of `effente.model_input.Prep.fit`. Sharing them needs an edit to existing shared code, which needs the team's agreement. Until then a change to either original must be made here too.
- **Simulated only.** No number here says anything certain about a real machine.
- **Threshold floor.** The refit is inert on the simulators: the floor sets the threshold every time. The rule is open.
- **No upper bound on the threshold.** Reported by the coordinating session, not reproduced here: in 2 of 12 simulated runs the fitted threshold was 2.3 to 3 times the entry's and that machine caught nothing. `max()` cannot fix that; it needs a ceiling share, which is not built.
- **`hold` and `widen` are not applied on the library's entries**, because they are in the manifest and `calibrate` reads the model file only. Five clock, trend and IMU temperature readings are refitted from 5 minutes and judged.
- **Fit check.** Cannot tell machine types apart; rule open.
- **Fault names.** With the classifier refitted in calibration, 2 of 4 simulated faults were named as another type on the `conveyor` entry.
- **Early warning.** The device app also needs `effente.early_warning.calibrate` after this. That call is in `device/intake/device_app.py`, track 1's.
