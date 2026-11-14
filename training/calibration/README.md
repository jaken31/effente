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

`t, X, names` are one-a-second feature rows, as for `effente.trainer.train_model`. The result loads in `effente.model_runtime.Model` unchanged. `calibrate` reads the model file only, never the manifest.

One more keyword, after `seed`: `threshold_median_multiple` (default 5.0, see "The threshold").

`calibrate` raises `ValueError` when:

- the entry is not a model this device can run, or is itself a calibrated model (calibrate the library entry again instead);
- a time is not a finite number, or there are not as many times as rows;
- the machine ran for less than `min_running_s`, or for fewer than 60 summary periods;
- no reading the entry uses was measured (a dead or unplugged sensor);
- no threshold can be set: fewer held-out rows than the entry's smoothing window, or a median held-out score that is zero or not a number;
- `min_running_s` or `threshold_median_multiple` is not a usable number, or a model-carried `widen` or `hold` has the wrong shape.

## What it does

Kept from the entry: the network, the PCA score scales, the readings it uses, the summary period, the persistence.

Fitted to this machine: the median and spread of each reading (`Prep`), the residual spreads, the alarm threshold, the safety baseline, and the fault classifier if the entry carries one (the library's entries do).

The last 15 % of the running rows are held out for the threshold; the rest are fitted on. The share is counted in running rows, not clock time, so a stop in the middle of a calibration does not shrink the fit side. Without a stop it is the trainer's split by time.

No key is added to the model file. `training_report` holds the calibration report (`"calibrated": true`), with the entry's own report under `entry_training_report`.

## The threshold

```
threshold = threshold_median_multiple x median(held-out smoothed scores)
```

The held-out scores are scored with the calibrated model and smoothed as the runtime smooths them before it compares with the threshold. The entry's own threshold plays no part: there is no floor and no ceiling from it. The report carries `threshold`, `held_out_smoothed_median`, `held_out_smoothed_max`, `threshold_median_multiple`, and `threshold_below_highest_held_out`.

The default multiple is 5.0 (`THRESHOLD_MEDIAN_MULTIPLE`). It is a starting value, simulated only. How it was picked is under "Choosing the multiple".

## The guards

A few minutes cannot show how far readings move in an hour. So the entry guards the fit:

- **Spread floor.** A reading's spread is never taken below the entry's own.
- **Not judged.** A reading this machine did not give while calibrating (NaN in more than half the rows) gets a huge spread and is never judged, also when it appears later. A 10-minute trend that only starts after a 5-minute calibration is one. If no reading at all was measured, calibration is refused.

### What the library's `widen` and `hold` do here: nothing

The library's entries keep `calibration.widen` and `calibration.hold` in `manifest.json` only. Their `model.json` has no `calibration` key, and `calibrate` does not read the manifest. So on the library's entries:

- **No widen factor is applied.** The entry's own spreads are the floor. A test checks this on the committed conveyor entry.
- **No reading is held.** The clock, trend and IMU temperature readings are treated like any other. The `conveyor` entry has 22 such readings. On the four simulated conveyors, device sensors only, a 300 s calibration left 17 of them not judged because they had no value yet, and refitted and judged 5: `imu_temp_c`, `time_hour_sin`, `time_hour_cos`, `trend_shocks_10m`, `trend_since_shock_s`. A 600 s calibration left the same 17 not judged. A 1200 s calibration left 15: `trend_vib_10m` and `trend_vib_swing_10m` were then judged too.

`calibrate` honours `calibration.widen` and `calibration.hold` if a model file itself carries them, in the shapes the manifests use: `widen` a dict of factor by reading name, covering every reading the entry uses, and `hold` a list of names or prefixes ending in `_`. Any other shape raises `ValueError`. A test copies the committed `conveyor` manifest's values into the model to check this. No entry on master carries the key.

## Measured on simulated machines

All of this is simulated: `effente.simulate_conveyor` (realistic mode) and `effente.simulate_server`, with `effente.site_noise` on top. The simulators need SciPy.

- **Entries:** the two on master, `training/library/entries/conveyor` and `generic_rotating`.
- **Machines and seeds:** fixed in `measure.py` (`MACHINES`). Conveyors with simulator seeds 1, 2, 3, 4 and different loads and room temperatures; server racks with seeds 2, 5, 6. Site noise seed is 1000 + the machine's seed. `calibrate` runs with its default `seed=0`.
- **Sensors:** device sensors only (accelerometer, gyro, IMU temperature). The sound, heat and room readings are missing, as on the device today.
- **Calibration:** the first 300 s of each session.

### Choosing the multiple

The sweep, run once per multiple (2, 3, 4, 5, 7, 10), on the branch with the review fixes in:

```bash
for m in 2 3 4 5 7 10; do
  python3 -m training.calibration.measure --entry training/library/entries/conveyor --entry training/library/entries/generic_rotating \
    --all-faults --threshold-median-multiple $m --cache /tmp/effente-calibration-cache --out /tmp/effente-calibration-sweep-$m.json
  python3 -m training.calibration.measure --entry training/library/entries/conveyor --entry training/library/entries/generic_rotating \
    --watch-hours 24 --threshold-median-multiple $m --cache /tmp/effente-calibration-cache --out /tmp/effente-calibration-day-$m.json
done
```

The numbers below were made with `OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1` set and the six multiples running side by side. The simulated sessions take about 25 s per simulated hour and are kept in `--cache`; with them cached, one first command took 462 to 505 s and one second command 142 to 210 s.

What each column is:

- **Normal, 40 min:** alarm seconds in the 40 minutes after calibration, summed over the 7 machines (16793 judged seconds per entry).
- **Faults:** every sudden fault kind of the conveyor simulator (11 kinds) on the 4 conveyors, switched on 10 minutes after calibration, at severity 1.0 ("obvious") and 0.3 ("subtle"): 44 runs each. A fault counts only if the machine was quiet between calibration and the fault.
- **24 h:** alarm seconds over the 24 hours after a calibration at Monday 08:00, summed over 2 conveyors (seeds 2 and 3; 125998 judged seconds per entry). Split by clock hour: 22:00 to 23:59 is the run-down after the shift ends at 22:00, the rest is the working day. The split is summed from the per-hour counts the command prints.

`conveyor` entry (simulated):

| Multiple | Normal, 40 min | Faults, severity 1.0 | Faults, severity 0.3 | 24 h, working day | 24 h, run-down |
|---:|---:|---:|---:|---:|---:|
| 2 | 463 s | 22 of 33 (11 left out) | 17 of 33 (11 left out) | 84382 s | 11162 s |
| 3 | 5 s | 24 of 44 | 19 of 44 | 38433 s | 11162 s |
| 4 | 4 s | 21 of 44 | 16 of 44 | 5575 s | 11162 s |
| **5** | 4 s | 20 of 44 | 16 of 44 | 1057 s | 11162 s |
| 7 | 4 s | 20 of 44 | 16 of 44 | 651 s | 11162 s |
| 10 | 4 s | 16 of 44 | 16 of 44 | 440 s | 11156 s |

`generic_rotating` entry (simulated):

| Multiple | Normal, 40 min | Faults, severity 1.0 | Faults, severity 0.3 | 24 h, working day | 24 h, run-down |
|---:|---:|---:|---:|---:|---:|
| 2 | 335 s | 14 of 22 (22 left out) | 13 of 22 (22 left out) | 75655 s | 11162 s |
| 3 | 24 s | 29 of 44 | 23 of 44 | 40337 s | 11162 s |
| 4 | 4 s | 24 of 44 | 16 of 44 | 18556 s | 11162 s |
| **5** | 4 s | 21 of 44 | 16 of 44 | 5457 s | 11162 s |
| 7 | 4 s | 17 of 44 | 16 of 44 | 2587 s | 11162 s |
| 10 | 4 s | 16 of 44 | 16 of 44 | 652 s | 11162 s |

"Left out" are runs where the machine already alarmed before the fault. Server rack faults (4 kinds on 3 racks, 12 runs): 3 of 12 alarmed with both entries at every multiple from 3 to 10. Those 3 are the rack bump; the fan failure, the clicking drive and the UPS beeping never alarmed.

What the sweep shows:

- **No multiple does both jobs well.** The value that catches the most faults (3) alarms for 38433 and 40337 s of a simulated day. The values that are quietest through the day (7, 10) catch 16 to 20 of 44 obvious faults.
- **16 of the 44 alarm at every multiple from 3 to 10, at either severity:** jam, looseness, misalignment and unbalance, on all 4 conveyors. The multiple only decides the faults beyond those: mostly `electrical` and `overload` at severity 1.0.
- **Belt rub, belt slip, blocked cooling and splice damage alarmed at no multiple from 4 to 10**, with either entry. No multiple fixes that. The device has no sound or heat readings.
- **5.0 is kept.** Going down to 4 gains 1 obvious fault with `conveyor` and 3 with `generic_rotating`, and raises the working-day alarm seconds from 1057 to 5575 s and from 5457 to 18556 s. Going up to 7 loses nothing with `conveyor` but 4 obvious faults with `generic_rotating` (21 to 17).
- **The 40 minutes after calibration do not tell the multiples apart.** From 4 to 10 they are the same 4 seconds on one conveyor. Only the 24-hour watch shows the cost of a low multiple.
- **The run-down after the shift alarms at every multiple.** 5581 s per machine in the simulated day (5575 s in one run at 10), named `speed_change`. A calibration at 08:00 never saw the machine run down. The threshold cannot fix this.

The sweep is small: 4 conveyors for the faults and 2 for the day. Differences of one or two faults between neighbouring multiples are within what another seed could change. Set the multiple again when there are recordings of real machines.

### After a 300 s calibration, at the default 5.0

From the first command above with `--threshold-median-multiple 5`. Simulated, 7 machines per entry, 40 minutes of normal running after calibration:

| | `conveyor` | `generic_rotating` |
|---|---|---|
| Share of seconds in alarm | 0.000 on 6 machines, 0.002 on one | 0.000 on 6 machines, 0.002 on one |
| The same for a model trained on the same 300 s alone | 0.730 to 0.978 | 0.730 to 0.978 |
| Threshold, conveyors | 5.29 to 5.67 | 3.94 to 4.55 |
| Threshold, server racks | 8.63 to 8.92 | 4.96 to 5.41 |
| Held-out smoothed median | 1.06 to 1.78 | 0.79 to 1.08 |
| Highest held-out smoothed score | 1.27 to 3.36 | 1.08 to 1.88 |
| Fit check ratio, conveyors | 0.23 to 0.24 | 0.07 to 0.08 |
| Fit check ratio, server racks | 0.37 to 0.38 | 0.09 to 0.10 |

- **The threshold was above the highest held-out score in 14 of 14 calibrations.**
- **False alarms and the slow readings.** The 0.002 is 4 seconds on one conveyor (seed 2). None of those seconds was led by a clock, trend or IMU temperature reading; the readings that moved most were `slow_band_00` and `vib_env_peak`. That is 40 minutes; the 24-hour numbers are in the sweep.

### Calibration length

Same command, simulated, at 5.0. From 60 s to 1200 s the alarm share in the 40 minutes after calibration stayed between 0.000 and 0.002 on all 7 machines and both entries. This is not a reason to shorten the calibration on real machines: the same 40 minutes did not tell the multiples apart either.

### Faults, by kind, at the default 5.0

From the same run. Runs that alarmed, of 4 conveyors (simulated):

| Fault | `conveyor`, obvious | `conveyor`, subtle | `generic_rotating`, obvious | `generic_rotating`, subtle |
|-------|---:|---:|---:|---:|
| jam | 4 | 4 | 4 | 4 |
| unbalance | 4 | 4 | 4 | 4 |
| misalignment | 4 | 4 | 4 | 4 |
| looseness | 4 | 4 | 4 | 4 |
| electrical | 4 | 0 | 4 | 0 |
| overload | 0 | 0 | 1 | 0 |
| idler seized | 0 | 0 | 0 | 0 |
| belt rub | 0 | 0 | 0 | 0 |
| belt slip | 0 | 0 | 0 | 0 |
| cooling blocked | 0 | 0 | 0 | 0 |
| splice damage | 0 | 0 | 0 | 0 |
| Total | 20 of 44 | 16 of 44 | 21 of 44 | 16 of 44 |

- **Time to the first alarm**, where there was one: jam 0 s; obvious unbalance, misalignment and looseness 8 to 13 s; the subtle ones 19 to 42 s; obvious electrical 44 to 51 s with `conveyor` and 58 to 715 s with `generic_rotating`.
- **Names.** With `conveyor`, the name shown most was another fault type in 4 of 4 runs of obvious looseness (misalignment_like) and of obvious misalignment (unbalance_like 3, speed_change 1). Subtle unbalance was named vibration_too_high in 4 of 4. Detection is better than naming.
- The validation harness (`training/validation`, PR #52) measured the same rule at 5.0 on its own machines and seeds: 25 of 55 obvious conveyor faults alarmed with `conveyor`. Reported there, not reproduced here.

### A whole day, at the default 5.0

From the second command above with `--threshold-median-multiple 5`. Calibrated on 300 s from Monday 08:00, then watched for 24 hours (simulated, 62999 judged seconds per machine):

| Entry | Conveyor | Alarm share | Run-down, 22:00 to 23:59 | Working day |
|-------|----------|---:|---:|---:|
| `conveyor` | seed 2 | 0.0898 | 5581 s | 75 s |
| `conveyor` | seed 3 | 0.1042 | 5581 s | 982 s |
| `generic_rotating` | seed 2 | 0.1424 | 5581 s | 3393 s |
| `generic_rotating` | seed 3 | 0.1214 | 5581 s | 2064 s |

- **Most of it is the run-down**, named `speed_change`.
- **The rest comes in blocks,** not single seconds. With `generic_rotating` on seed 2: 1087 s in the 16:00 hour and 1775 s in the 18:00 hour. On seed 3, with both entries: the 06:00 hour, when the shift starts (718 s and 1478 s), mostly named `impacts`.
- **Not run:** the variant with the manifest's hold list (`--hold-from-manifest`).

## The fit check does not tell machine types apart

The rule is `ratio <= 3`, where ratio is this machine's median held-out score after calibration over the entry's own held-out median (`heldout_score_quantiles.p50` in the manifest). The limit is a starting value, simulated only. The TRD lists the rule as open, and it stays open.

In the table above every machine passed on every entry: server racks scored 0.37 to 0.38 on the `conveyor` entry against 0.23 to 0.24 for conveyors (simulated). A pass means only that no gross mismatch was found. It does not show the machine is of the entry's type. A check that could not be made (no held-out median in the manifest, another feature list, a held-out median of 0 or below in the report) is reported as failed, with the reason.

## From a laptop

```bash
python3 -m training.calibration.calibrate --entry training/library/entries/conveyor --run <recording folder> --out models/conveyor-07.json
```

Exit code 0 if the fit check passed, 2 if it failed (the file is still written), 1 if `calibrate` refused the rows (too little running, for one). A test runs it on the committed entry folder and expects 0.

## Tests

```bash
python3 -m training.calibration.tests.test_calibration
```

30 tests, NumPy only. Four use the committed `conveyor` entry and its manifest. The rest use synthetic machines and a fixture entry built in the test file.

## Known issues

- **Simulated only.** No number here says anything certain about a real machine.
- **The multiple is a compromise.** At 5.0 fewer than half of the simulated sudden faults alarm (20 and 21 of 44 obvious, 16 of 44 subtle), and a simulated day still has 75 to 3393 alarm seconds outside the run-down. No multiple from 2 to 10 did both well.
- **The run-down after the shift alarms,** 5581 s in the simulated day, at every multiple. Calibration at 08:00 never sees it.
- **Belt rub, belt slip, blocked cooling and splice damage are not caught** on the accelerometer and gyro alone, at any multiple from 4 to 10.
- **Server rack faults:** only the rack bump alarmed (3 of 12 runs).
- **The threshold has no bound from the entry.** A machine whose held-out scores are high for any reason gets a high threshold; nothing compares it with the entry's.
- **`hold` and `widen` are not applied on the library's entries**, because they are in the manifest and `calibrate` reads the model file only. Five clock, trend and IMU temperature readings are refitted from 5 minutes and judged. A 600 s calibration judges the same five.
- **Fit check.** Cannot tell machine types apart; rule open.
- **Fault names.** Obvious looseness and obvious misalignment were named as another type in 4 of 4 simulated runs each with the `conveyor` entry.
- **Copied code.** `calibrate` repeats the trainer's fault-classifier block (inject, fit, quantize, about 12 lines of `effente.trainer.train_model`), and `refit_prep` repeats the median and spread statistics of `effente.model_input.Prep.fit`. Sharing them needs an edit to existing shared code, which needs the team's agreement. Until then a change to either original must be made here too.
- **The TRD is behind the code.** It says the threshold is not refit freely from calibration data, gives `calibrate`'s signature without `threshold_median_multiple`, and says a short calibration keeps the entry's values for late trends. `docs/TRD.md` is track 5's.
- **Early warning.** The device app also needs `effente.early_warning.calibrate` after this. That call is in `device/intake/device_app.py`, track 1's.
