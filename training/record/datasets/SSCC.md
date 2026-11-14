# SSCC chain conveyor: real data as our device would see it

`sscc.py` turns the SSCC dataset into effente's own features, one row per 1 s window, so a detection
model built on fake conveyor data can be checked on a real conveyor.

**Source:** Zhang Yucong et al., "Toward Multimodal Industrial Fault Analysis: A Single-Speed Chain
Conveyor Dataset with Audio and Vibration Signals", arXiv 2603.07130 (2026).
- Page: https://yucongzh.github.io/SSCC-Dataset/
- Code: https://github.com/yucongzh/SSCC-Fault-Benchmark
- Licence: CC BY 4.0. Cite it wherever results from it are shown.

## The dataset

A real chain conveyor, recorded in 6,669 clips of 5 s each.

| | |
|---|---|
| Speeds | 20, 40, 60, 80, 100: nominal levels, not physical units (the paper says so). 20 and 40 are normal and clean only. |
| Loads | heavy, med, light: relative, not weights |
| Noise | clean, or real factory noise played through two loudspeakers (folder names `noiseA`, `noiseB`, ...) |
| Labels | normal; dry (too little chain lubrication); lean (guide rail misaligned); loose (chains too slack on both sides); screwdrop (a foreign object in the chain path) |

Each clip has three audio streams and a vibration CSV:
- Audio: a Zoom H5 recorder (WAV, 44.1 kHz, stereo), an iPhone 11 (MP4, 44.1 kHz) and a Xiaomi phone (MP4, 48 kHz).
- Vibration: a CSV holding a timestamp and 4 channels at 100 kHz:
  - a single-axis sensor on the motor
  - a tri-axis sensor "at the opposite end of the system", measuring the structure

Layout after unpacking (from the benchmark's `make_csv.py`; files of one clip share the index NNNN, the CSV's first column is a timestamp, `#` lines are comments):
- normal clips: `normal/<load>_vel<speed>_<clean|noiseX>/{audio,ios,android,vibration}/<kind>_part_NNNN.*`
- fault clips: the same under `abnormal/<fault>/...`

## What the loader does

**Vibration** uses the tri-axis sensor: it sits on the structure, like our device on a conveyor frame.
- Steady offset removed, converted to g.
- Resampled 100 kHz → 1 kHz (with an anti-aliasing low-pass), then a 2 Hz high-pass against source drift.
- The MPU-6500's noise added before its 218 Hz low-pass, as on the chip: 300 µg/√Hz (datasheet value, not
  measured), white over 0–500 Hz, which leaves about 4.4 mg rms per axis after the low-pass (checked: 301 µg/√Hz in band).
- 1 g of gravity added on z, clipped at ±8 g, rounded to the chip's counts (1/4096 g at ±8 g).
- A mirrored 0.5 s lead-in lets the filters settle first, so the first window has no start-up
  transient (checked: on a steady sine, window 0's rms equals windows 1–4's).
- The motor's single-axis sensor is not used: our device has one IMU. Its raw mean and spread are kept for checking.

**Sound** is the Zoom H5 WAV: a fixed recorder, the closest to our fixed microphone.
- First channel, resampled 44.1 kHz → 16 kHz.
- Scaled to 16-bit counts, so dB is relative to the recorder's full scale.
- dB SPL is not calibrated: the Zoom's gain and our microphone's sensitivity differ.

**Features:** `effente.features.extract` on each whole 1 s window gives 5 rows a clip.
- NaN, because SSCC has no such sensor: gyro (`gyr_*`), IMU temperature, thermal camera (`th_*`), room (`env_*`).
- Time-of-day features are NaN, and so are history (`trend_*`) features: each window is computed on its own.

## Output: `data/real/sscc/features.npz`

| Key | Shape | What |
|---|---|---|
| `X` | (rows, 285) float32 | features in `feature_names` order |
| `feature_names` | (285,) | `effente.features.FEATURE_NAMES` |
| `clip` | (rows,) | index of the row's clip in the per-clip arrays |
| `window` | (rows,) | 0–4, which second of the clip |
| `clip_id` | (clips,) | `<condition path>/<NNNN>` |
| `benchmark_id` | (clips,) | the official benchmark's id (`normal/heavy_vel100_clean_0001`, `abnormal/dry/med_vel80_noiseB_0012`), to reuse its condition-level splits |
| `speed` | (clips,) int | 20–100 |
| `load` | (clips,) | heavy / med / light |
| `noise` | (clips,) | clean / noise |
| `noise_kind` | (clips,) | the folder's own name for it: clean, noisea, noiseb, ... |
| `label` | (clips,) | normal / dry / lean / loose / screwdrop |
| `condition` | (clips,) | the condition folder |
| `vib_raw_mean`, `vib_raw_std` | (clips, 4) | each raw CSV channel's mean and spread, before any processing |

Per-row metadata: `label[clip]`, `speed[clip]`, and so on.

One difference from the benchmark: it skips a clip whose phone MP4s are missing; this loader needs only the
WAV and the CSV, so its clip count can be slightly higher.

Clips of one condition were recorded one after another. Split train and test by condition (as the
paper does), not by clip, or neighbouring clips leak between them.

## Run

```bash
nice -n 10 python3 -m training.record.datasets.sscc data/real/sscc/unpacked --cache data/real/sscc/cache --out data/real/sscc/features.npz --workers 2
```

- Each condition folder's rows are cached, so a rerun resumes.
- `--only normal/` limits a run to some folders.
- `--vib-unit` and `--axes` change the assumptions listed below.

Download: `sscc_download.sh` (resumable; Google Drive's per-file quota can block it for up to a day).

## Unverified

The paper does not say these:
1. **Column order.** Taken as: time, motor single-axis, tri-axis x, y, z (`--axes 1,2,3`).
2. **Axis directions.** Which tri-axis direction is vertical, along the chain or across it is unknown. Our z gets gravity, so on the device z would be the axis facing the floor.
3. **Unit.** Taken as g (`--vib-unit g`).

`vib_raw_mean` / `vib_raw_std` are there to check these once real files are in:
- A MEMS sensor shows gravity as a steady offset on its vertical axis: about 1 (g) or 9.8 (m/s²).
- A piezo sensor shows none.

Status of these checks: not yet done; the data has not downloaded yet.
