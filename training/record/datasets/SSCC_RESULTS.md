# SSCC: how effente's fault detection does on a real chain conveyor

Overnight benchmark, 14 Nov 2026. Data: the SSCC dataset (Zhang et al., arXiv 2603.07130, CC BY 4.0). Every number
below was measured on that data with the scripts in `training/record/sscc_eval/`. Nothing here was run on the board.

## Summary

**What was done.** All 6,724 SSCC clips (5 s each) were unpacked one folder at a time, turned into the signals our
device would see (MPU-6500-style 1 kHz tri-axis vibration with the chip's noise, 16 kHz sound), and scored the way
the device is used: learn one machine's normal from its own normal recording, then judge later normal clips and
fault clips. 18 operating conditions have faults (3 loads x speeds 60/80/100 x clean/factory noise).

**The honest headline (confirmation set, never used to choose anything: the 6 "med" load conditions, 175 held-out
normal clips = 15 minutes of normal running, 1,602 fault clips):**

| | normal clips that alarmed | fault clips caught | dry | lean | loose | screwdrop | clean | factory noise |
|---|---|---|---|---|---|---|---|---|
| **Device as it is** | 2 / 175 | **75 %** | 100 | 88 | 59 | 59 | 100 % | 48 % |
| **Recommended model** | 1 / 175 | **94 %** | 99 | 100 | 93 | 84 | 96 % | 91 % |

On the selection set (heavy and light loads, 350 normal clips, 2,196 fault clips) the same two give 5/350 and 68 %
against 1/350 and 83 %. With the best possible single sensitivity at 1 % false alarms: 67 % against 85 % (selection),
69 % against 98 % (confirmation).

**Recommendation.**
1. *Before the demo (no code change):* learn at the speed, load and room noise of the demo, and learn again after
   any change. On SSCC, a model learned at one speed alarmed on 67-87 % of normal clips at another speed, and on
   100 % of normal clips once factory noise was played; learning with the noise present brought that to 0-1 %.
   Keep sensitivity 0.5 and the spike rule; don't raise sensitivity (0.56 gives 13 false alarms in 350 clips).
2. *Next model (code change, cheap on the board):* one PCA per sensor group (sound, vibration) on plain log-band
   features (64 sound bands, 20 vibration bands per axis, plus rms, kurtosis and crest factor), each group scored
   against its own learning level and fused by the **maximum**, the line set from out-of-sample learning scores
   (99th percentile over 5 time blocks), alarm on the median of the last 3 seconds. +19 points of catches on the
   confirmation set at equal or fewer false alarms; the biggest gain is under factory noise (48 % to 91 %).
   About 8-9 s to learn on the board for 30 minutes of recording, about 1 ms to judge a second, 45 KB.
3. *Hardware:* if the SSCC vibration is in g (unverified), this conveyor's vibration is about as small as the
   MPU-6500's own noise. Vibration alone then catches 44 % with the MPU's noise and 94-97 % without it. A
   low-noise accelerometer would matter more than any model change for vibration-borne faults.

**Biggest caveats.** (1) Every method, the device's included, false-alarms heavily when the operating point or the
background changes, and when the learning comes from the end of a recording and the judging from its start
(learn-late/judge-early: 103-124 of 350 normal clips alarm). SSCC recordings drift over their first minutes.
(2) 350 + 175 held-out normal clips are 44 minutes of normal running; one false alarm in 350 clips is about two an
hour, far more than the device's one-a-week goal, and rates near one a week cannot be measured here.
(3) Normal and fault clips come from separate recording sessions, so "caught" also includes any session difference.

## The paper and its benchmark

- Paper: Zhang Yucong et al., "Toward Multimodal Industrial Fault Analysis: A Single-Speed Chain Conveyor Dataset
  with Audio and Vibration Signals", arXiv 2603.07130 (2026). Page: https://yucongzh.github.io/SSCC-Dataset/ .
  Code: https://github.com/yucongzh/SSCC-Fault-Benchmark (cloned read-only to `C:\Users\finnf\effente-sscc\SSCC-Fault-Benchmark`).
- Rig: a chain conveyor; 5 s clips; speeds 20-100 (nominal), loads heavy/med/light; clean or factory noise played
  through two loudspeakers. Faults: dry (too little lubrication), lean (guide rail misaligned), loose (slack chains),
  screwdrop (a screw dropped into the chain path). Faults only at speeds 60/80/100; speeds 20/40 are normal and clean only.
- Sensors: Zoom H5 (WAV 44.1 kHz stereo), iPhone 11 and Xiaomi audio (MP4), and a 4-channel vibration CSV at 100 kHz
  (one single-axis sensor on the motor, one tri-axis sensor at the far end). Sensor models and units are not given.
- Fault detection task (normal-only training), `make_splits_fd.py`: learn on all normal clips at speeds other than
  100 (20/40/60/80, clean and noise), test on the speed-100 normal clips plus every fault clip. Condition-level split,
  no randomness.
- Method, `fd_knn.py`: each of the 7 channels (3 audio, 4 vibration) goes through a frozen pretrained audio model
  (BEATs, CED, Dasheng, EAT, ECHO, FISHER), mean-pooled to one vector per clip, standardised, scored by the mean
  distance to the k nearest learning clips (code default k = 11, l2; the paper text says k = 1 and cosine), turned
  into a quantile against the learning scores, and averaged over channels. Metric: AUROC only (no pAUC, no
  per-fault or per-noise breakdown).
- Reported fault-detection AUROC (audio / vibration / fused): BEATs 0.868 / 0.784 / 0.891; CED 0.835 / 0.772 / 0.854;
  Dasheng 0.760 / 0.301 / 0.681; EAT 0.747 / 0.291 / 0.666; ECHO 0.807 / 0.201 / 0.658; FISHER 0.954 / 0.546 / 0.882.
  (Read through a summarising web fetch; check against the PDF before quoting them elsewhere.)
- Related work cited there: DCASE 2025 Task 2 (first-shot unsupervised anomalous sound detection), AnoPatch,
  ECOD (ECDF-based detection), and noise-robust sound-based diagnosis; see also `DCASE_TASK2_SURVEY.md` in this folder.

## The data as it really is

- 97 folders: 24 normal + 73 fault (screwdrop at light_vel100_clean is split into `_1` and `_2`; treated as one
  condition). 6,724 clips: normal 1,926, dry 1,040, lean 1,035, loose 1,715, screwdrop 1,008 (the project page's
  "6,669" disagrees with its own table; the files have 6,724). No clip was short or unreadable.
- Each folder is one continuous recording cut into 5 s clips: clip NNNN's CSV starts at t = 5 x NNNN s. 66-99 normal
  clips per condition (5.5-8 minutes).
- Vibration CSV: header `timestamp_s,ch0,ch1,ch2,ch3`, 500,000 rows at 100 kHz (10 us steps), comma separated, no
  comment lines. ADC step about 0.000352 in the file's unit.
- Audio WAV: **32-bit float**, stereo, 44.1 kHz, 5.000 s; levels about -40 dBFS rms.

**Corrected loader assumptions** (the "Unverified" list in `SSCC.md`, now checked against the files):
1. *Column order:* time, then ch0-ch3. ch0 is uncorrelated with ch1-ch3 and carries most of its energy at
   600-700 Hz (motor-like); ch1 and ch3 are correlated (r = -0.31), as two axes of one sensor can be. This supports
   "ch0 = motor single-axis, ch1-3 = tri-axis" (the loader's assumption), but does not prove it.
2. *Gravity:* every channel's mean is about 0.001 with standard deviations of 0.004-0.015: **no gravity offset**, so
   the sensors are AC-coupled (piezo/IEPE-like), and the loader's "z gets 1 g" is a simulation choice, not data.
3. *Unit:* still unknown. Taken as g. Then the tri-axis signal is about 4-15 mg rms over its whole band and only a
   few mg under 218 Hz, the same size as the MPU-6500's own noise (about 4.4 mg rms per axis after its low-pass).
   A what-if with the unit x10 (as if the file were volts from a 100 mV/g sensor) is reported below.

## How we evaluated

- **Pipeline.** `build_cache.py` unpacked one folder at a time (audio and vibration only, never the videos) into
  `tmp\`, converted every clip with 14 processes, wrote `cache\<folder>.npz` (Zoom first channel at 16 kHz int16;
  the 4 vibration channels low-passed and resampled to 16 kHz, int16 with a scale per clip and channel; each raw
  channel's mean and standard deviation; the clip's start time), checked it and deleted the unpacked folder.
  97 folders in about 10 minutes (02:13-02:23); cache 5.1 GB; free space never went below 50 GB. `make_features.py` then made,
  per 1-second window: the device's 285 features (`effente.features.extract`) on the device-style signals, and
  generic features (log band powers + log rms, kurtosis, crest) of: the device-style 1 kHz vibration (`g1`), the
  same 1 kHz vibration without MPU noise (`g1c`), the full 16 kHz vibration (`g16`), and the sound (`ga`).
- **Device-style vibration** (as `sscc.py`): ch1-3 at 16 kHz -> 1 kHz, 2 Hz high-pass, MPU-6500 noise
  (300 ug/sqrt(Hz), datasheet value) before a 218 Hz low-pass, +1 g on z, +-8 g, 1/4096 g counts.
  Gyro, temperature, thermal camera, room and history (trend) features do not exist in SSCC: they are NaN, and the
  device's own preparation (`Prep`) drops them. The device sees 90-95 inputs here.
- **Protocol A, "same conditions".** Per condition, normal clips in time order: the first 60 % learn, the next 5 % are
  dropped (gap), the last 35 % are held out (23-35 clips). Every fault clip of that condition is judged. The held-out
  normal clips are run as the continuous stream they are. Each fault clip is judged as if the fault started at its
  first second, preceded by typical normal history (the condition's median normal score), so "caught within 1/2/3 s"
  is measurable.
- **The device as it is** = `effente.trainer.train_model` (1 s periods, PCA 95 %/40 patterns, residual + T2, tail
  line, spike line 2.5x) and the Watcher's rules (median of the last 5 seconds over the line, or one second over
  the spike line; default sensitivity, margin 1), plus `effente.safety.Safety` with the baseline and spike levels
  the device learns. The vectorised rule was checked against the real `Watcher` on one condition: 119 of 120 rows agree.
- **Alternatives** are learned on the same rows. Their line uses learning data only: the learning clips are cut into
  5 blocks in time, each block is scored by a model learned on the other four, and the line is the 99th percentile
  of those out-of-sample scores after the median-of-3-seconds rule; alarm when the median of the last 3 seconds is
  over it. This line rule was chosen on the selection set (the stricter "highest out-of-sample score" line is also
  reported in `results/tables.md`).
- **Metrics.** Per clip: alarmed or not (any alarm during its 5 s). Pooled over conditions: false alarms (held-out
  normal clips that alarmed), share of fault clips caught at the learned line (with Wilson intervals in the JSON),
  AUROC and standardised partial AUROC (FPR <= 0.1) of the clip statistic, and "best@x": the share caught with the
  best single threshold for all conditions together (scores in units of each condition's line, i.e. one sensitivity
  dial) that lets at most x of the held-out normal clips alarm. Resolution: 1 clip in 350 = 0.29 %; 0 of 350 still
  allows up to 1.1 % (95 % upper bound); 0 of 175 allows up to 2.1 %.
- **Protocol B, "changed conditions".** (shift) learn on one clean condition, count false alarms on held-out normals
  of other speeds (20-100), other loads at the same speed, and the same condition with factory noise.
  (variety) learn on one load at speeds 60/80/100 together, clean only or clean + noise, judge each condition.
- **Reverse split** (robustness): learn on the last 60 % of each recording, hold out the first 35 %.
- **The paper's protocol** reproduced with kNN on our generic clip features (per channel, ECDF-fused as `fd_knn.py`).
- **Confirmation set, fixed before any result was seen:** every "med" load condition. It was used once, at the end,
  for the device as it is, the recommended model and the hardware what-if. One deviation: `eval_device.py` prints a
  line per condition, so the device-as-is results for "med" appeared in its log during the night; nothing was chosen
  on them. Two variants were looked at on the confirmation set *after* the freeze and are marked "exploratory".
- **Selection** used the heavy and light loads only (12 conditions); about 70 configurations were tried there, so
  the selection-set numbers of the winner are optimistic; the confirmation numbers are the honest ones.

## Results

All tables: `C:\Users\finnf\effente-sscc\results\tables.md` (regenerate with `make_tables.py`).
"FA" = held-out normal clips with an alarm. Fault columns are % caught at the learned line.

### 1. The device as it is (Protocol A, selection set)

| | FA | caught | dry | lean | loose | screwdrop | AUROC pooled | AUROC per condition | pAUC 0.1 | best@0 | best@1 % | best@5 % |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| all (12 conditions) | 5/350 | 68 % | 99 | 85 | 52 | 49 | 0.917 | 0.951 | 0.861 | 37 | 67 | 76 |
| clean (6) | 4/148 | 91 % | 100 | 86 | 92 | 87 | 0.985 | 1.000 | 0.963 | 83 | 83 | 95 |
| factory noise (6) | 1/202 | 45 % | 98 | 84 | 12 | 11 | 0.866 | 0.901 | 0.775 | 2 | 52 | 60 |
| median-of-5 rule alone | 4/350 | 68 % | 99 | 84 | 52 | 47 | | | | | | |
| spike rule alone | 1/350 | 45 % | 54 | 44 | 42 | 42 | | | | | | |

- In clean conditions the device separates faults perfectly per condition (AUROC 1.000), but its learned line is
  sometimes far too high: at light_vel100_clean it catches 46 % (lean 9 %, screwdrop 4 %) with AUROC 1.0. The line
  comes from the last 15 % of the learning rows (about 30 seconds) and a gamma tail for one alarm a week.
- Under factory noise, loose and screwdrop are almost never caught (12 %, 11 %).
- The safety checks start on no held-out normal clip and add very few catches: selection +2 lean clips; confirmation
  +1 lean, +8 loose, +8 screwdrop (of 348). Checks that fired on faults: sudden_impact (32 clips, 24 of them lean in
  noise), sudden_sound (4), more_impacts (4). Checks with a 10 s hold cannot fire inside a 5 s fault clip.
- Sensitivity dial (selection set; margin = how far over the line counts):

| sensitivity | 0.75 | 0.63 | 0.56 | **0.5 (default)** | 0.42 | 0.35 | 0.25 | 0.10 |
|---|---|---|---|---|---|---|---|---|
| FA | 290/350 | 69/350 | 13/350 | **5/350** | 3/350 | 3/350 | 1/350 | 0/350 |
| caught | 99 % | 86 % | 75 % | **68 %** | 57 % | 49 % | 42 % | 18 % |

### 2. Alternatives on the device's own features and on generic features (selection set)

| model (line: 5-block q99, median of 3) | FA | caught | dry | lean | loose | screwdrop | AUROC pooled | best@1 % |
|---|---|---|---|---|---|---|---|---|
| device as it is | 5/350 | 68 | 99 | 85 | 52 | 49 | 0.917 | 67 |
| device model, line changed to 5-block q99 + median of 3 | 0/350 | 62 | 94 | 79 | 46 | 40 | 0.964 | 75 |
| PCA 95 % resid + T2 (device's model) | 1/350 | 63 | 94 | 81 | 46 | 40 | 0.962 | 75 |
| PCA 99 % resid + T2 | 1/350 | 52 | 74 | 59 | 44 | 34 | 0.964 | 72 |
| PCA 95 % residual only | 1/350 | 52 | 78 | 68 | 38 | 35 | 0.936 | 61 |
| PCA, group-balanced residual (mean) + T2 | 1/350 | 62 | 94 | 80 | 45 | 40 | 0.958 | 73 |
| PCA per group, fused by max | 1/350 | 68 | 93 | 80 | 57 | 48 | 0.962 | 71 |
| Gaussian (Mahalanobis, Ledoit-Wolf) | 1/350 | 68 | 96 | 85 | 55 | 42 | 0.970 | 77 |
| Gaussian per group, max | 0/350 | 67 | 94 | 78 | 57 | 46 | 0.960 | 78 |
| kNN, k = 1 | 1/350 | 69 | 98 | 85 | 56 | 44 | 0.957 | 78 |
| kNN per group, max | 1/350 | 69 | 99 | 84 | 55 | 47 | 0.961 | 81 |
| kNN per group, ECDF mean (the paper's fusion) | 2/350 | 54 | 91 | 62 | 39 | 31 | 0.958 | 78 |
| isolation forest | 2/350 | 23 | 47 | 36 | 8 | 10 | 0.729 | 29 |
| small autoencoder (32-8-32) | 0/350 | 21 | 46 | 39 | 5 | 4 | 0.877 | 40 |
| max abs(z) over all readings | 0/350 | 12 | 34 | 10 | 4 | 5 | 0.870 | 12 |
| max abs(z) over 13 hand-picked readings | 0/350 | 20 | 40 | 28 | 9 | 8 | 0.904 | 20 |
| *generic features:* Gaussian | 2/350 | 74 | 100 | 95 | 60 | 51 | 0.985 | 83 |
| generic: one PCA over everything | 1/350 | 64 | 98 | 82 | 50 | 33 | 0.968 | 78 |
| **generic: PCA per group, fused by max (recommended)** | **1/350** | **83** | 94 | 94 | 74 | 76 | 0.977 | **85** |

- On the device's own features, no model does much better than the device at its line (62-69 %), but most give
  fewer false alarms (0-2 against 5) and better best-threshold numbers (75-81 % against 67 %). T2 helps; 95 % beats
  99 % here (opposite to the simulated week-long recordings, where 99 % won).
- The step up comes from the features: plain log-band powers. Ablation (same model, selection set): 64 sound bands
  83 %, the same sound with the device's 32 bands 72-76 %; vibration bands per axis 83 %, the device's
  summed-over-axes bands 59-70 % (with 6-7 false alarms); dropping the sound's rms/kurtosis/crest raises false
  alarms from 1 to 7.

### 3. Fusing sensor groups (the "yell" dilution)

Scoring all inputs together dilutes a change in one group. On SSCC, with generic features, one PCA over everything
catches 64 %; one PCA per group fused by the maximum catches 83 % at the same false alarms. On the device's
features: 63 % against 68 %. The paper's fusion (mean of per-channel quantiles) is worse than the maximum (54 % against
69 % for kNN). Z-score averaging is worse than the maximum (57 % against 67 % for the Gaussian). A per-group line
(each group over its own 99th percentile, then OR) was worse (50 %): its percentiles came from half-recording models
and were too noisy. So: per-group scores, normalised by their own median learning score, combined by max, one line.

### 4. Sensors, and the hardware what-if (selection set; recommended model unless noted)

| signals | FA | all | clean | noise | loose (noise) | screwdrop (noise) |
|---|---|---|---|---|---|---|
| device features, vibration only (MPU-style) | 1/350 | 27 % | 18 % | 36 % | 1 | 3 |
| device features, sound only | 3/350 | 65 % | 91 % | 38 % | 36 | 25 |
| generic, vibration 1 kHz **with** MPU noise (what the board has) | 13/350 | 44 % | 43 % | 46 % | 3 | 11 |
| generic, vibration 1 kHz **without** MPU noise | 1/350 | 94 % | 100 % | 89 % | 94 | 56 |
| generic, vibration full band (16 kHz, no added noise) | 6/350 | 97 % | 100 % | 95 % | 88 | 96 |
| generic, sound only | 2/350 | 84 % | 100 % | 68 % | 55 | 59 |
| generic, MPU vibration + sound (**recommended**) | 1/350 | 83 % | 100 % | 67 % | 49 | 53 |
| generic, noiseless 1 kHz vibration + sound | 0/350 | 94 % | 100 % | 88 % | 94 | 53 |
| generic, full-band vibration + sound | 2/350 | 98 % | 100 % | 95 % | 90 | 96 |

- **The MPU-6500's noise floor is what hides this conveyor's vibration** (with the CSV taken as g): the same 1 kHz
  signal goes from 44 % to 94 % when the chip's noise is left out. Bandwidth above 500 Hz adds mostly for screwdrop
  in noise (56 % to 96 %).
- **Unit what-if (x10, as if the CSV were volts from a 100 mV/g sensor):** the MPU-style vibration alone then catches
  83 % at 0/350, the recommended model 87 % at 0/350, the device as it is 75 % at 4/350 (`results/A_x10/`,
  `device_A_x10.*`). So whether the board's IMU is enough depends on how strongly the machine vibrates where it sits.
- Sound carries most of the detection on SSCC. In factory noise it falls from 100 % to 68 %, and vibration without
  the MPU noise does far better there (89-95 %).
- The motor's single-axis channel at full band (kNN): 100 % caught but 26/350 false alarms.

### 5. How early (fault starts at second 1 of the clip; % caught by the end of second 1/2/3/4/5)

| | 1 s | 2 s | 3 s | 4 s | 5 s |
|---|---|---|---|---|---|
| device as it is, selection | 32 | 39 | 58 | 64 | 68 |
| recommended, selection | 0 | 71 | 79 | 82 | 83 |
| device as it is, confirmation | 40 | 48 | 67 | 72 | 75 |
| recommended, confirmation | 0 | 73 | 86 | 90 | 94 |

Per fault, recommended model, selection, at 2 s / 5 s: dry 88/94, lean 82/94, loose 61/75, screwdrop 57/76.
Device: dry 43/99, lean 36/85, loose 40/52, screwdrop 34/49. The device's spike rule is the only thing that alarms
within the first second (32-40 %). A median of 3 needs two abnormal seconds. Adding a one-second spike rule to the
recommended model, at 1x its highest single out-of-sample learning score, catches 13 % of fault clips within the
first second with no extra false alarm (still 1/350) and +1 point overall (84 %); at 2x it adds nothing (selection
set only; tried after the freeze, not on the confirmation set). Worth keeping in B1. On the board the Watcher judges each second when the next one arrives: add 1 s to every figure.

### 6. Clean versus factory noise: does sound help or hurt?

- Learned and judged in the same noise (Protocol A), sound still helps: sound alone 68 % in noise, MPU vibration
  alone 46 %, together 67 %; the device as it is 45 %.
- When the background changes between learning and judging (Protocol B shift), every model that listens alarms on
  all normal clips (100 %). But so do the *vibration* models that can see small signals (noiseless 1 kHz and full
  band: 100 %): SSCC's noise recordings differ from the clean ones in vibration too (separate sessions, or the
  loudspeakers shaking the frame). Only the MPU-style vibration is unmoved (5-13 %), because it sees almost nothing.

### 7. Protocol B: changed conditions (selection set; % of held-out normal clips that alarm)

Learned on one clean condition (6 conditions):

| model | same condition | other load, same speed | other speed (20-100) | + factory noise | faults caught |
|---|---|---|---|---|---|
| device as it is | 3 % (4/148) | 18 % (52/296) | 83 % (502/605) | 100 % | 91 % |
| Gaussian (device features) | 0 % | 1 % | 49 % | 100 % | 89 % |
| generic Gaussian | 1 % | 1 % | 66 % | 100 % | 96 % |
| recommended | 0 % (0/148) | 3 % (8/296) | 80 % (484/605) | 100 % | 99 % |
| recommended + full-band vibration | 1 % | 3 % | 80 % | 100 % | 100 % |

Confirmation set, same test: device 1 % / 35 % / 87 % / 100 %, faults 100 %; recommended 0 % / 1 % / 67 % / 100 %,
faults 96 %. **A change of speed or background is, to every model, as abnormal as a fault.** Load changes are
mostly tolerated by the recommended model (1-3 %) and much less by the device (18-35 %).

How much variety must the learning hold? One model per load for speeds 60/80/100 together:

| model | learned on | FA clean | FA noise | caught clean | caught noise |
|---|---|---|---|---|---|
| device as it is | clean | 1 % | 100 % | 98 % | (all normals alarm) |
| recommended | clean | 0 % | 100 % | 67 % | (all normals alarm) |
| device as it is | clean + noise | 3 % | 0 % | 92 % | 26 % |
| recommended | clean + noise | 2 % | 1 % | 77 % | 25 % |
| recommended + full-band vibration | clean + noise | 1 % | 2 % | 90 % | 48 % |

Confirmation set: device 1 % / 0 % false alarms and 99 % / 32 % caught with clean + noise learning; recommended
0 % / 0 % and 93 % / 23 %; with clean-only learning both catch 100 % of clean faults.
- Learning with the noise present removes the noise false alarms, but faults under noise are then mostly missed
  (25-32 %): a model that has learned "noise is normal" also accepts loose and screwdrop under noise.
- **The recommended model loses its advantage when one model must cover several speeds**: on the selection set it
  catches 67 % of clean faults against the device's 98 % (light load: 24-47 % per speed against the device's
  92-99 %; heavy load: 100 % for both). On the confirmation set both catch 100 %. So: one model per operating point, or keep the device's PCA when several speeds must share one.

### 8. Reverse split: learn from the end of each recording, judge its start

Every method false-alarms heavily: device 124/350 (selection) and 68/175 (confirmation), recommended 103/350 and
51/175, Gaussian 80/350 and 37/175. False alarms are spread through the first two minutes, slowly decreasing: the
machine's normal drifts over the first minutes of each SSCC recording. The forward split (the device's real use:
learn first, judge later) does not show it. On a real machine this means: learn across a cold start and warm
running, or do not judge the first minutes after a start until a model has learned them.
Catches stay higher for the recommended model (86 % against 75 % selection; 93 % against 91 % confirmation).

### 9. The paper's protocol, for comparison with its Table 2

Learn on the 1,433 normal clips at speeds 20-80; test on 493 normal clips at speed 100 and all 4,798 fault clips;
kNN per channel on clip-mean generic features, ECDF-fused. AUROC:

| | audio (Zoom) | vibration (4 ch) | fused (mean over 5 channels) |
|---|---|---|---|
| paper, BEATs | 0.868 | 0.784 | 0.891 |
| paper, FISHER | 0.954 | 0.546 | 0.882 |
| ours, k = 1, Euclidean | 0.844 | 0.634 | 0.702 |
| ours, k = 1, cosine | 0.779 | 0.765 | 0.934 |
| ours, k = 11, Euclidean (the code's default) | 0.799 | 0.602 | 0.663 |
| ours, k = 11, cosine | 0.767 | 0.757 | 0.927 |

Per fault (k = 1, cosine, fused): dry 0.948, lean 0.936, loose 0.951, screwdrop 0.887. The device's own features
(clip means, k = 1): 0.753 (Euclidean), 0.862 (cosine). Simple band features are in the same range as the frozen
pretrained encoders. One warning about this protocol: the test normals are at a speed never learned (100) while
most faults are at learned speeds (60, 80), so a distance score partly measures speed. Our motor channel scores
AUROC 0.12 (faults look *more* normal than the speed-100 normals), the same effect as the paper's vibration AUROCs
under 0.5. That is why our main protocol learns and judges within a condition.

### 10. Confirmation set (med load; run once at the end)

| | FA | caught | dry | lean | loose | screwdrop | AUROC pooled | pAUC 0.1 | best@0 | best@1 % | best@5 % |
|---|---|---|---|---|---|---|---|---|---|---|---|
| device as it is | 2/175 | 75 % | 100 | 88 | 59 | 59 | 0.961 | 0.893 | 65 | 69 | 81 |
| recommended | 1/175 | **94 %** | 99 | 100 | 93 | 84 | 0.998 | 0.992 | 80 | 98 | 100 |
| recommended + full-band vibration | 13/175 | 94 % | 100 | 100 | 88 | 92 | 0.983 | 0.909 | 72 | 72 | 74 |
| *exploratory (seen after the freeze):* device model, 5-block q99 + median-of-3 line | 0/175 | 79 % | 99 | 81 | 72 | 67 | 0.982 | 0.937 | 79 | 79 | 89 |

Clean: device 1/74 FA, 100 % caught; recommended 0/74, 96 %. Factory noise: device 1/101, 48 %; recommended 1/101,
91 %. The full-band variant gave 13 false alarms, all in one recording (med_vel100_noise, 13 of 35 clips): it is not
robust as it stands, so the hardware what-if numbers above are an upper bound, not a promise.

## What to change on the board

### (a) Small, safe changes for the 1 pm demo (no code change, or settings only)

1. **Learn in the demo's own conditions, and learn again after any change.** Same belt speed, same load on it, same
   room: if people will talk or music will play during the demo, have that going while it learns.
   Evidence: Protocol B shift, every model including the device's: another speed made 67-87 % of normal clips alarm;
   factory noise that was not in the learning made 100 % alarm; learned with the noise present, 0-1 %.
   Where: nothing in code; it is how the learn button is used (`device/intake/device_app.py`, `_train`).
2. **Learn for longer than the start-up, and include a start.** SSCC's recordings drift over their first minutes;
   a model learned on settled running alarmed on 29-39 % of the start-up clips (reverse split). Let the belt run a
   few minutes before learning, or include a stop-start in the learning if the demo will restart the belt.
3. **Keep sensitivity at 0.5, and the spike rule on.** Raising the model's sensitivity to 0.56 took false alarms
   from 5 to 13 in 350 clips for +7 points of catches; the spike rule is the only path that alarms in the first
   second (32-40 % of faults) and alone gave 1 false alarm in 350 clips. Where: the `model_sensitivity` and
   `spike_sensitivity` dials (`effente/safety.py` `margins()`, used in `device_app.py` `_watcher`).
4. **Keep the one-moment checks (sudden_impact, sudden_sound).** They never fired on SSCC normal clips and caught a
   few faults the model missed (8 screwdrop clips on the confirmation set). For the "coin in the track" demo, the
   closest SSCC fault (screwdrop) was caught by the device in quiet conditions (87 % selection, 100 % confirmation)
   but almost never under factory noise (7-11 %). A quiet room helps the demo.
5. Not recommended before the demo: changing the line rule alone. The device's model with a 5-block q99 +
   median-of-3 line removed all false alarms (0/350, 0/175) but caught 62 % instead of 68 % on the selection set
   (79 % against 75 % on the confirmation set, seen after the freeze). It needs trainer and runtime changes and a test.

### (b) Larger redesigns

**B1. The recommended model** (expected: +15 to +19 points of faults caught at equal or fewer false alarms; most of it
under background noise. Risk: medium, see the multi-speed result. Effort: 1-2 days.)
- Features, per one-second window (reference code: `training/record/sscc_eval/make_features.py`, `generic()`):
  - vibration, per axis (x, y, z) of the 1 kHz accelerometer after removing its mean: 20 log10 band powers on
    `np.geomspace(5, 500, 21)` edges (Hann window, power summed per band), log10 rms, kurtosis, crest factor
    (69 inputs). Today `effente/features.py` `_vibration` sums the band power over the axes (`power = (amp**2).sum(1)`).
  - sound at 16 kHz: 64 log10 band powers on `np.geomspace(50, 8000, 65)`, log10 rms, kurtosis, crest factor
    (67 inputs). Today 32 bands (`SND_EDGES_HZ`, and `SND_BANDS 32` in `firmware/esp32_mic/sound_features.h`, which
    computes them on the ESP32): the firmware and the sound-feature message need the 64 bands and the 2 extra values.
  - Preparation: median and IQR of the learning rows, clipped at +-8 (as `Prep`).
- Model: one PCA per group (vibration, sound): 95 % of variance, at most 40 patterns, scored like
  `Model.score` (mean squared residual over its learning mean, plus mean T2). Each group's score is divided by the
  median of that group's out-of-fold learning scores (two halves in time), and the window's score is the
  **maximum** over groups. Reference: `training/record/sscc_eval/methods.py` (`PCA`, `Fused` with `combine="max"`).
- Line: cut the learning rows into 5 blocks in time; score each block with a model learned on the other four;
  take the median of each 3 consecutive scores; the line is their 99th percentile. Final model on all rows.
  Reference: `eval_methods.run_config` and `report.alt_stats(..., "med3", "q99")`; NumPy version of the same scheme
  for a Gaussian in `board_model.py` (`learn`).
- Runtime: alarm when the median of the last 3 one-second scores is over line x margin (`margins()["model"]`),
  or when one second's score is over the highest single out-of-sample learning score x the spike margin (keeps
  the first-second catches; see section 5). In `effente/model_runtime.py` this is `smooth_minutes = 3`, the
  group maximum in `Model.score`, and the existing spike line set to that highest score instead of 2.5x the threshold.
- Model file: per-input group index and per-group (mean, patterns, pattern spreads, residual reference, median),
  replacing the single autoencoder; `trainer.train_model` writes it (`fit_pca` per group).
- Cost, measured on this PC and scaled by the board/PC ratio measured from the brief's own figures (device PCA
  training 25 s on the board = 1.4-1.5 s here: x16-18; vibration features 8 ms = 0.70-0.72 ms here: x11):
  learning on 30 minutes (1,800 rows): 0.50 s here, about 8-9 s on the board (60 minutes: 0.90 s, about 15-16 s);
  judging one second: 0.07 ms here, about 1 ms on the board; model 45 KB; features 0.88 ms here (about 10-15 ms on
  the board if computed there; the sound part would run on the ESP32, not measured). NumPy only.
- Risks: (1) with one model across speeds 60/80/100 it caught 67 % of clean faults on the selection set against the
  device's 98 % (confirmation: both 100 %); keep one model per operating point or keep the device's PCA beside it
  for multi-speed learning. (2) Chosen out of about 70 tries on the selection set; the confirmation set holds (94 %),
  but this is one machine. (3) The board's gyro, temperature and heat inputs are not in SSCC; they would form more groups.

**B2. A better accelerometer for vibration-borne faults** (expected: vibration alone 44 % to 94 % on SSCC if its unit
is g; risk: depends on the machine's vibration level and the unknown SSCC unit; effort: hardware + driver.)
The MPU-6500's 300 ug/sqrt(Hz) gives about 4.4 mg rms in its band, the size of this conveyor's vibration. Removing
that noise alone (still 1 kHz) is worth +50 points for vibration; full bandwidth adds screwdrop under noise. Examples
to evaluate (datasheet values from memory, check them): ADXL355 (about 25 ug/sqrt(Hz), DC to about 1 kHz),
IIS3DWB (about 75 ug/sqrt(Hz), 6 kHz bandwidth). The full-band model's 13 false alarms in one confirmation
recording show it needs the same care with operating-point changes as sound.

**B3. One model per operating point.** Speed (and background) changes are the biggest false-alarm cause on SSCC. Keep
a model per belt speed (the device can read speed from the motor setting, or from `vib_speed_hz` where it is
reliable) and switch, or say "new conditions: learn again?" instead of alarming. Effort: a day; risk: low.

**B4. A per-group "outside normal" alarm instead of a global median for sound.** On SSCC the sound group alone
(generic features) catches 84 %; the board's real yell problem is the same dilution: the group maximum (B1) fixes it
inside the model.

Not worth doing on this evidence: isolation forest, the small autoencoder, max-|z| rules, kNN with k = 5, ECDF or
z-score fusion, PCA at 99 % or residual-only scoring.

## What was not done or not verified

- Nothing ran on the board; board times are scaled from PC timings by a ratio computed from two board figures in the brief.
- The CSV unit and the channel roles are inferred, not known (see "The data"). All MPU-style results assume g.
- No gyro, temperature, thermal, room or history features exist in SSCC; the device's early warning
  (`effente/early_warning.py`, 30-minute blocks) and the 10-second-hold safety checks could not be tested on 5 s clips.
- Fault clips are judged as if each fault started at the clip's first second, after typical normal history; the
  fault recordings are separate sessions, so session differences count as "caught".
- Learning data per condition is only 3.3-5 minutes (40-59 clips); the device normally learns 10-30 minutes.
- False-alarm rates can only be resolved to about 1 in 350 clips (2 an hour); the one-a-week goal is not testable here.
- Pretrained audio embeddings (BEATs, CED) were not run; the comparison with the paper uses our band features.
- Phone audio (iOS, Android) was not used.
- The paper's numbers were read through a summarising fetch of the paper; verify against the PDF.
- `training/record/datasets/sscc.py` was not changed; the new pipeline in `sscc_eval/` replaces it for this work.

## Files and how to re-run

Code (this worktree, branch `track2/sscc-benchmark`), `training/record/sscc_eval/`:
`build_cache.py` (unpack + cache, resumable, disk guard at 20 GB), `make_features.py` (features; `--unit-to-g` for
the what-if), `common.py` (splits, rules, metrics), `methods.py` (models), `eval_device.py` (device as it is),
`eval_methods.py` (alternatives), `eval_shift.py` (Protocol B and the paper protocol), `report.py`,
`make_tables.py`, `tables_b.py`, `analyze.py` (dial, early detection, per condition, safety), `board_model.py`
(NumPy reference + check against scikit-learn), `timing.py`.

Data and results, `C:\Users\finnf\effente-sscc\`: `cache\` (97 files, 5.1 GB), `features\` and `features_x10\`
(per-folder features), `results\` (`device_A.*`, `A\*.npz` raw scores per configuration, `A_x10\`, `A_rev\`,
`device_A_x10.*`, `device_A_rev.*`, `tables.md`, `analysis_select.json`, `analysis_confirm.json`,
`protocolA_select_*.json`, `shift_*.json`, `variety_*.json`, `paper_protocol.json`, `timing.json`), `RESUME.md` (log).

```bash
export PYTHONPATH=C:/Users/finnf/effente-sscc-work
PY=C:/Users/finnf/effente-sscc/venv/Scripts/python
$PY -m training.record.sscc_eval.build_cache --workers 14      # skips folders already cached
$PY -m training.record.sscc_eval.make_features --workers 14
$PY -m training.record.sscc_eval.eval_device
$PY -m training.record.sscc_eval.eval_methods --workers 12
$PY -m training.record.sscc_eval.make_tables                   # -> results/tables.md
$PY -m training.record.sscc_eval.eval_shift --part paper,shift,variety --set select
SSCC_SPLIT=reverse SSCC_A_SUBDIR=A_rev $PY -m training.record.sscc_eval.eval_methods --configs "g1+ga|fused_pca95_t2_max"
```
The venv holds numpy, scipy, scikit-learn, pandas, pyarrow and soundfile.
