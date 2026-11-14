# SSCC round 3: a model that beats lin50 and log64?

14 Nov 2026 (machine clock 11:30-12:10 EST). Data: SSCC (Zhang et al., arXiv 2603.07130, CC BY 4.0). Everything here
was measured on this PC with the scripts in `C:\Users\finnf\effente-sscc\round3\`. Nothing ran on the board.

## 1. Verdict

**Yes, one model beat both on every load, in quiet and in factory noise, with no extra false alarms. The gain is clear
on the held-back med load. It is smaller on the selection loads, and short of the target at heavy load under noise.**
The model ("hyb60", `round3_model.py`) is lin50's machinery with one change of sound layout and one setting:
- the sound layout: 30 log bands from 50 to 600 Hz (fine at low frequency, like log64), then lin50's 50 Hz linear
  bands from 600 to 8000 Hz, plus the same 3 statistics. That is 181 values per second, in one group;
- the setting: PCA up to 60 patterns instead of 40.

It was frozen at 12:02 (`FREEZE.md`, hash in `FREEZE.sha256`) after 149 configurations on heavy + light. Then med
load was scored once:
- hyb60: **1/175 false alarms, 99.9 % of all fault clips** (99.9 % without screwdrop; quiet 99.8 %, noise 100 %);
- log64: 1/175, 91.9 % (94.9 % without screwdrop);
- lin50: 1/175, 94.6 % (93.7 % without screwdrop).

On med every one of the 24 fault recordings is ≥ 98 % caught. Of the 18 dry, lean and loose recordings, log64 has 5
under 95 % and lin50 has 6.
On the selection loads (optimistic, because chosen there) it is 0/350 and 98.6 % without screwdrop, against lin50's
0/350 and 96.7 %. The selection gain comes mostly from quiet light load (98.0 against 92.5 %). At heavy load under
noise it is 96.5 %: better than lin50's 94.6 %, but under the 97 % target.

How sure:
- The med result is one run on one machine. Its 1,602 fault clips come from only 24 recordings, so the effective
  sample is small.
- The med set has now been used three times. Also, the weak spots of lin50 (quiet) and log64 (noise) that pointed me
  towards a hybrid layout were partly learned on med in earlier rounds.
- The FA figure is 1/175 for all three models. That cannot show a difference in false alarms smaller than about 2 %.
- Robustness checks on normal data only (selection): an other-load change makes 3 % of normal clips alarm (lin50 6 %,
  log64 1 %), and the reverse split 24 % (lin50 24 %).

## 2. Headline table (same layout as the top of SSCC_RESULTS.md)

The protocol is unchanged: per condition, the normal clips in time order are split 60 % learn / 5 % gap / 35 % held
out. The line comes from learning data only. A clip alarms when the median of the last 3 seconds is over the line.
The log64 and lin50 rows are the presets as shipped (`effente.band_model.make_model`) and match the checker's figures
exactly.

### All conditions of a load

| load | model | normal clips alarmed | all faults | without screwdrop | dry | lean | loose | screwdrop |
|---|---|---|---|---|---|---|---|---|
| med (confirmation, single run) | `log64` | 1/175 | 91.9 % | 94.9 % | 98.6 % | 99.4 % | 89.7 % | 81.0 % |
| med (confirmation, single run) | `lin50` | 1/175 | 94.6 % | 93.7 % | 87.2 % | 90.6 % | 99.8 % | 98.0 % |
| med (confirmation, single run) | **`hyb60`** | **1/175** | **99.9 %** | **99.9 %** | 99.7 % | 100 % | 100 % | 100 % |
| light (selection) | `log64` | 0/174 | 88.7 % | 90.6 % | 100 % | 98.3 % | 80.2 % | 81.5 % |
| light (selection) | `lin50` | 0/174 | 95.8 % | 96.2 % | 96.8 % | 91.5 % | 98.8 % | 94.2 % |
| light (selection) | **`hyb60`** | 0/174 | 98.7 % | 99.0 % | 100 % | 96.9 % | 99.7 % | 97.8 % |
| heavy (selection) | `log64` | 1/176 | 81.1 % | 82.7 % | 88.9 % | 90.4 % | 74.6 % | 75.2 % |
| heavy (selection) | `lin50` | 0/176 | 95.9 % | 97.2 % | 99.1 % | 100 % | 94.3 % | 91.3 % |
| heavy (selection) | **`hyb60`** | 0/176 | 96.8 % | 98.2 % | 99.4 % | 100 % | 96.4 % | 91.3 % |

### Quiet against factory noise (all fault clips; without screwdrop in brackets)

| load | model | quiet: normal alarmed | quiet: caught | noise: normal alarmed | noise: caught | noise: loose |
|---|---|---|---|---|---|---|
| med (confirmation) | `log64` | 0/74 | 96.3 % (98.1) | 1/101 | 87.2 % (91.7) | 82.8 % |
| med (confirmation) | `lin50` | 0/74 | 89.5 % (87.4) | 1/101 | 100 % (100) | 100 % |
| med (confirmation) | **`hyb60`** | 0/74 | **99.9 % (99.8)** | 1/101 | **100 % (100)** | 100 % |
| light (selection) | `log64` | 0/72 | 99.4 % (99.4) | 0/102 | 78.1 % (81.6) | 60.9 % |
| light (selection) | `lin50` | 0/72 | 91.6 % (92.5) | 0/102 | 100 % (100) | 100 % |
| light (selection) | **`hyb60`** | 0/72 | 97.5 % (98.0) | 0/102 | 100 % (100) | 100 % |
| heavy (selection) | `log64` | 0/76 | 100 % (100) | 1/100 | 62.3 % (65.5) | 49.3 % |
| heavy (selection) | `lin50` | 0/76 | 99.6 % (99.7) | 0/100 | 92.2 % (94.6) | 89.0 % |
| heavy (selection) | **`hyb60`** | 0/76 | 99.9 % (99.8) | 0/100 | 93.6 % (96.5) | 93.2 % |

The med false alarm of hyb60 and the one of lin50 are both in `med_vel100_noise`. The one missed med fault clip is a
dry clip at `med_vel100_clean`. Raw data: `res/final_med.json` and `res/final_med_*.npz`, `res/final_select.json`,
`logs/final_med.log` (run 12:03:15, guarded by `logs/final_med.done` so that it cannot run twice).

Other checks (selection loads, normal clips only; share of 5 s clips that alarm):

| | other load, same speed | learn late, judge the first minutes | learn first 40 %, judge last 40 % | several speeds in one model (FA; caught w/o screwdrop: quiet / noise) |
|---|---|---|---|---|
| log64 | 1 % | 27 % | 2 % | 3/350; 93 / 71 % |
| lin50 | 6 % | 24 % | 1 % | 0/350; 28 / 96 % |
| hyb60 | 3 % | 24 % | 1 % | 0/350; 70 / 96 % |

On several speeds in one model, hyb60 is between the two presets. lin50 used `BandModes` (one model per speed) for
that in round 2. hyb60 would need the same; not tested with hyb60.

## 3. Every family tried (selection set: heavy + light, 350 held-out normal clips)

The "without screwdrop" catch is given as all (quiet / noise). Selection uses heavy + light only; each line is the
best (or a typical) member of its family. The full log has one line per configuration (`logs/run.log`).

| family | best setting tried | FA | w/o screwdrop: all (quiet / noise) | note |
|---|---|---|---|---|
| lin50 preset (reference) | as shipped | 0 | 96.7 (96.1 / 97.3) | |
| log64 preset (reference) | as shipped | 1 | 86.6 (99.7 / 73.6) | |
| **hybrid layout, one group (final)** | `hyb:30:600 + stats`, max_k 60 | 0 | **98.6 (98.9 / 98.3)** | log-low/linear-high variants 20:400, 40:800 and 45:1000 all scored 97.4-99.4 |
| hybrid, other settings | max_k 60, blocks 10 | 0 | 99.4 (99.7 / 99.1) | **not chosen**: blocks 6/8/12/15 give 98.1-98.6. One condition (heavy_vel60_noise) swings with where the block borders fall |
| both layouts as two groups (round-2 hypothesis) | log64 + lin50, max, q 0.993 | 1 | 98.7 (98.5 / 98.9) | at q 0.995: 0 FA, 97.2; more sensitive to q than hyb60; 226 values |
| both layouts, mean fusion | log64 + lin50, mean | 0 | 95.2 (99.4 / 90.9) | |
| OR of the two presets (each own line) | lin50 OR log64 | 1 | 98.7 (99.8 / 97.6) | two models, 226 values |
| lin50 with max fusion (one group) | | 0 | 96.7 | identical, one group |
| sub-band groups, max-fused | lin50 in 2/3/4/6 groups | 1 | 95.1 (2 groups) | worse with more groups (76-90) |
| spectral shape / noise-robust transforms | row-mean removed (`rel`); local contrast (`con:3`) | 0-1 | 70.7 (43.7 / 97.8); 48.3 | noise holds up but quiet collapses |
| other band layouts | lin 25 Hz; 100 Hz combinations; 128 log | 3 / 0 / 0 | 96.4; 93.8-94.8; 84.1 | |
| PCA settings on lin50 | var 0.9 / 0.98, max_k 20 / 60, blocks 3 / 10, residual/T2 weights, per-dim residual | 0-2 | 85.5-98.5 | blocks 10: 98.5 (1 FA), same fragility as above |
| line/normaliser variants | group level at q75/q90, power-mean fusion, time-interleaved folds | 0-1 | 94.5-98.3 | no gain |
| Gaussian (shrinkage Mahalanobis) | lin50 | 1 | 94.8 (97.9 / 91.8) | log64: 72.6 |
| GMM (on PCA scores + residual) | lin50, 4 comp., 10 dims | 2 | 97.0 (97.3 / 96.6) | 1-2 comp.: 96.8; many near-misses |
| kNN | lin50, k = 1 | 2 | 92.9 (90.6 / 95.3) | k = 5: 69.0; on 20 PCA dims: 74.3 (5 FA); log64: 36.5 |
| **isolation forest** (sklearn, 200 trees) | lin50 on 20 PCA dims | 7 | 73.7 (71.6 / 75.8) | raw lin50: 23.0 (0.6 / 45.5), 3 FA; log64: 5.0; log64 on PCA: 44.6 |
| one-class SVM (RBF) | lin50 | 2 | 77.8 (73.0 / 82.6) | on PCA dims: 76.0; log64: 51.8 |
| **autoencoder** (NumPy MLP, Adam, tanh) | lin50, 128-40-128, denoising (noise 0.5), 500 epochs | 1 | 79.1 (73.5 / 84.8) | 64-16-64: 47.7 (12.8 / 82.7); 32-8-32: 46.1 (5 FA); 128-32-128: 59.0; one layer of 40: 57.7 (4 FA); denoising 64-16-64: 76.8 (2 FA); log64 32-8-32: 36.8; log64 denoising: 55.5. With about 160 learning rows per block they stay weak in quiet |
| frame statistics: median over 64 ms frames per 50 Hz band | `wfmed50` | 5 | 100 (99.9 / 100) | **rejected**: 33 % of normal clips alarm after a load change. At a line matched to lin50's load-change rate (6 %) it falls to 94.5 |
| frame statistics: 20th percentile over frames | `wfp2050` | 1 | 99.1 (100 / 98.3) | rejected for the same reason (30 %; 87.0 at matched rate) |
| frame statistics: Welch mean over frames | | 1 | 91.8 | worse than the 1 s FFT |
| lin50 + frame-median group, max | | 1 | 99.5 (99.1 / 100) | 17 % of normal clips alarm after a load change; 98.7 at the matched rate |
| lin50 + frame std per band | max / mean | 0-1 | 89.5 / 81.8 | |

Count: **149 distinct configurations** were scored on heavy + light before the freeze, plus 2 preset baselines. Also
run: 36 normal-only shift runs (`drift.py`) and 4 several-speeds runs. After the freeze: 1 module check on heavy +
light and 1 med run. The envelope spectrum (`env`) and the 32 ms frame features (`frm`) were computed in `spec/` but
never scored.

How hyb60 was chosen:
- inner check: the low-log + high-linear layouts (hybrid, or log64 + lin50 as two groups) lead on heavy alone and on
  light alone. The one-group hybrid was preferred because it moved less with q and the layout split;
- settings: lin50's defaults kept (q 0.995, 5 blocks, var 0.95). max_k 60 was preferred over 40 because it scored
  0 FA with 3 % load-shift alarms (max_k 40: 1 FA);
- an iso-shift check (`tradeoff.py`): each model's line moved until its other-load alarm rate is at most 6 %. There,
  hyb60 catches 99.2 % (lin50 96.0 %, log64 86.6 %). This rules out "just more sensitive": the frame-median models
  failed this test.

## 4. What it needs from the ESP32, and what it costs

**ESP32.** It sends **181 sound values** per second: lin50 sends 162, log64 67, today's firmware 32 bands.
- The values: 30 log10 band powers on `geomspace(50, 600, 31)` Hz, 148 bands of 50 Hz from 600 to 8000 Hz, then log10
  rms, kurtosis and crest factor.
- All come from the same one-second power spectrum and sample statistics that lin50 uses. There is no extra FFT and
  no frame work.
- The lowest log bands are only about 4 Hz wide. That needs the 1 s spectrum's 1 Hz resolution, which the firmware's
  16,384-point FFT has (0.98 Hz bins).

**Cost on this PC** (`timing.py`, `logs/timing.log`, single thread, real SSCC sound rows):

| | features of 1 s | learn 30 min (1,800 rows) | judge 1 s | file |
|---|---|---|---|---|
| hyb60 (sound only, as on SSCC) | 1.23 ms | 0.26 s | 0.18 ms | 202 kB (npz, float32) |
| hyb60 with the vibration group on | | 0.30 s | 0.30 ms | 269 kB |
| lin50 (same script) | 1.50 ms | 0.26 s | 0.21 ms | 789 kB (JSON) |
| log64 (same script) | 1.37 ms | 0.37 s | 0.08 ms | 121 kB (JSON) |

On the board, scaled from lin50's measured board times (9.5 ms features, 2.0 s learn 30 min, 3.4 ms judge, both
groups on) by the PC ratios above, expect:
- about 8-10 ms of feature work per second;
- about 2-2.5 s to learn 30 min;
- about 3-5 ms to judge a second;
- a file of about 0.2-0.3 MB.

All are inside the budget (< 50 ms per second, < 1 min to learn, < 2 MB). Not measured on the board.

**Code.** `round3_model.py` is NumPy only. It has `window_row` / `sound_row`, and `Round3Model` with `learn`,
`judge`, `scores`, `save` and `load`. Saving is one `.npz` file with float32 matrices and a JSON header; load is
checked to give the same scores.
- Run with `features from the cached audio + Round3Model()`, it reproduces the harness result on heavy + light
  exactly (`logs/final_select.log`).
- Sensor groups are scored separately and fused by the **max**. A thermal-camera group is one more `groups` entry with
  its own PCA and normal level.
- The vibration group keeps lin50's gate (≥ 25 % of bands 6 dB over the MPU floor). On SSCC it is always off, so this
  is a sound-only result. Max fusion with vibration on has not been tested on any data here.

## 5. Unverified, caveats, what went wrong

- **Med has now been used three times.** This run was frozen before it and run once. But the brief's description of
  the presets' weak spots (lin50 in quiet, log64 under noise) came partly from earlier med runs, and a hybrid layout
  is the obvious answer to them. Treat the 99.9 % as optimistic too, though less so than selection.
- **One machine.** SSCC has 24 med fault recordings and one normal recording per condition. Every fault was recorded
  in a different session from the normal data, so session or ambient differences can look like faults to every
  model here.
  - The frame-median models showed that being more sensitive to anything stationary raises catches and load-change
    alarms together.
  - hyb60 passed the iso-shift check, but that check is itself on the same machine.
- **Heavy load under noise on the selection set is 96.5 %**, under the brief's 97 % target. Almost all of the misses
  are in one condition (heavy_vel60_noise, loose chains, 85 %). There a stretch of the learning recording that
  sounds different raises the line.
- **False alarms.** 1/175 for every model on med; it cannot show a difference.
- **Several speeds** in one model: 70 % quiet on the selection set, so it needs one model per speed (like lin50's
  `BandModes`; not tested with hyb60).
- **Not tested:** the vibration group switched on (SSCC's vibration is at the sensor floor), any board or ESP32
  timing, the stream and time-to-alarm columns of round 2, extended isolation forest, and the envelope / 32 ms
  frame features that were computed.
  - I did not search the DCASE literature on the web this round. The DCASE-style methods that need machine IDs or
    attribute labels do not apply to one machine anyway.
- **What went wrong:**
  - The first autoencoder batch ran 10x slow from BLAS thread oversubscription. It was stopped and its remaining
    configurations rerun single-threaded.
  - A first parser bug put the 3 statistics in their own group, which made "lin:25" look like 10 %. It was found and
    fixed; those rows are not used.
  - The best-looking setting (5 → 10 blocks, 99.4 %) turned out to be luck of where the block borders fall, and was
    not chosen.
- Files: scripts and results are in `C:\Users\finnf\effente-sscc\round3\`, with feature caches `spec\` (257 MB) and
  `frames\` (82 MB). `SSCC_RESULTS.md` and the effente / effente-deploy trees were not touched.
