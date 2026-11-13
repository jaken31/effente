# The model library

A library entry is a pretrained normal model for one machine type. The device picks an entry, then calibrates it to the machine in front of it in about 5 minutes. Calibration keeps the entry's network and refits each reading's median and spread to that machine.

Everything here is built from simulated machines. Nothing has been checked on a real machine (unverified).

## What an entry is

```
entries/<entry>/<version>/model.json      the model file
entries/<entry>/<version>/manifest.json   what the entry is and where it came from
```

`model.json` is a plain `effente-model-1` file. `effente.model_runtime.Model` loads it as is. It differs from a trained machine's file in these ways:

- `prep` holds neutral statistics: every centre is 0 and every scale is 1. The device refits them.
- `library_entry`, `entry_version`, `pipeline` (`rotating-v1`) and `source` (`entry`) say what it is.
- `calibration.widen` has one factor per column in `prep.columns`. A 5-minute calibration sees too narrow a spread, so the device multiplies the spread it refits by this factor. Factors are between 1 and 5.
- `calibration.hold` names readings a 5-minute calibration cannot refit: names starting with `time_` or `trend_`, and `imu_temp_c`. They stay in the column list. What the device does with them is calibration's decision.
- `calibration.min_s` is 300.
- There is no `fault_classifier`. The classifier is trained per machine today. Retraining it in calibration is not designed yet.
- There is no `baseline`. The outside-normal levels belong to one machine.

## How an entry is built

1. Each source is one machine: a simulated one, or a learn-mode folder recorded with the device.
2. Rows are one a second, running rows only. The last 15 percent of each source, by time, is held out.
3. The columns are those every source keeps under `Prep.fit`.
4. Each source is standardised with its own median and spread. The network then learns the same thing calibration will feed it.
5. The standardised rows of all sources are stacked and one PCA is fitted (`effente.trainer.fit_pca`), then stored as whole-number weights.
6. The threshold comes from the held-out rows of every source, as in `train_model`: a gamma fit, never below the highest held-out score.

With one source the result equals `train_model(..., normal_model="pca")`. A test checks this.

## Manifest fields

| Field | Meaning |
|-------|---------|
| `manifest` | Always `effente-entry-1` |
| `entry`, `version`, `title` | Which entry this is |
| `status` | `demo`, `unverified` or `verified` (see below) |
| `use` | `watch` |
| `pipeline` | `rotating-v1` |
| `features_version` | The feature list the model was built on |
| `sensors` | `required` and `optional` sensors |
| `mount` | How the device is fixed to the machine |
| `profile` | `machine_type`, `drive`, `speed`: what the entry suits |
| `fault_types` | Names of the fault types in the model file |
| `calibration` | `unit` (seconds) and `min` (300) |
| `fit_check` | `entry_heldout_score_median`, `entry_threshold`, and `rule` (`open`: no pass or fail rule is set yet) |
| `trained_on` | One item per source: name, kind, description, seconds of running, its held-out median score |
| `scorecard` | `measured_on_device`, `false_alarms_per_week`, `faults`. Not measured means `false`, `null` and an empty list |
| `model_file`, `model_sha1` | The model file and its sha1 |
| `notes` | Limits, in plain words |

`entry_heldout_score_median` is the median raw score of every held-out row, each source scored with its own statistics.

## Status labels

- `unverified`: built from simulated data only, or not checked on device data.
- `demo`: built with recordings of the machine it is demoed on. It shows the flow. It does not show that the entry works on other machines.
- `verified`: has a scorecard measured on device data. The builder refuses to write this label, because it measures no scorecard.

## Build

```bash
python3 -m training.library.build                          # both entries, 3 simulated hours per source
python3 -m training.library.build conveyor --hours 3 --seed 0
python3 -m training.library.build conveyor --recording data/real/conveyor/session-01 --status demo
```

`--recording` adds a learn-mode folder recorded with the device as one more source. Repeat it for more folders. `--out` writes to another library folder. The simulators need SciPy. A full build took about 25 minutes on a laptop.

To read the library:

```python
from training.library.catalog import load_catalog, load_entry
manifests = load_catalog("training/library/entries")
manifest, model = load_entry("training/library/entries", "conveyor")   # checks the sha1 and the feature list
```

## Test

```bash
python3 -m training.library.tests.test_library
```

## The entries

- `conveyor`: five simulated conveyors with different drives (gear ratios, tooth counts, pulley size, belt length, load, room temperature, seed). The designs are in `simulators.py`. They are choices, not measurements of real conveyors.
- `generic_rotating`: the same five conveyors plus the simulated server rack.

Both use accelerometer, gyro and IMU readings only. The simulators ran with the microphone, thermal camera and room sensor absent, because that is what the device has today.

## Limits

- Simulated data only. No accuracy number is written anywhere, because none was measured.
- The threshold is never below the highest held-out score. Site disturbances (a knock, a neighbouring machine) push that score up, so the threshold sits well above the median held-out score. Each manifest's notes give the two numbers. With sound and heat readings included, a research pass found the threshold so high that nothing was caught. That is why these entries leave them out.
- Cross-type use is unproven. A research pass on the simulators found that a conveyor entry calibrated on the server rack raised no false alarms and caught none of five faults.
- A device recording with a microphone or thermal camera adds nothing from those sensors, because the columns are those every source keeps.
- The `--recording` path is tested only with a folder the recorder wrote from a simulated machine. No real recording has been through it.
- The `conveyor` entry is to be rebuilt with real recordings of the demo conveyor and relabelled `demo`.
