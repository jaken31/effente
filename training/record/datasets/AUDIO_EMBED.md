# Pretrained audio embeddings vs our sound features — UNFINISHED (paused 13 Nov 2026, 16:16)

**Status: unfinished.** Paused at the user's request while real device recordings are made. Only the **fan**
machine type is evaluated, at whole-clip length. Nothing below answers the question for the other machine types,
for shorter clips, or for the board. Resume note with the exact state:
`data/real/dcase2024-fan/embed/RESUME.md`. Code: `training/record/datasets/audio_embed.py`; its docstring has the commands.

## Question

Do embeddings from networks pretrained on general audio catch real machine faults better than our device's
hand-built sound features (`effente/features.py`)? We test this with the same detectors and the fewest false alarms. Could a
small model run on the UNO Q?

## What was compared

Data: the DCASE 2024 Task 2 dev set (real machines in factory noise, 10 s at 16 kHz, our microphone's rate), CC BY-NC-SA 4.0.
Each machine type has 1,000 normal training clips (990 source domain, 10 target domain) and 200 test clips:
100 normal and 100 anomalous, half of each per domain.

Representations, each a vector per clip:
- **ours:** our sound features over 1 s windows, as the per-clip median (44 columns) or median + max (88). Columns are
  chosen on the training clips only.
- **BEATs iter3+ AS2M**, 90.3 M parameters: every transformer layer, mean-pooled over time.
- **EfficientAT mn10_as**, 4.9 M parameters: the network's own pooled 960-d feature, plus two earlier blocks.
- **CED-tiny**, 5.5 M parameters: added; it works and is exported to ONNX, but **not yet evaluated** on any machine.

Detectors, each fitted on normal training clips only, on embeddings standardised with the training mean and sd:
- kNN, k = 1/3/5, the mean distance to the k nearest training clips (as SSCC's `fd_knn.py`)
- density kNN (K = 16)
- Mahalanobis with a Ledoit-Wolf shrunk covariance
- PCA residual (95 % of variance)
- fusions: the mean of z-scores (`fuse z-mean`) and their min; z-scaling uses out-of-fold training scores
- ours + BEATs fused the same way

Thresholds come from **training normals only**, as quantiles of 5-fold out-of-sample training scores. For 0 %, the
threshold is the max of those scores. "Caught" counts anomalous test clips above the threshold. "Flagged" counts normal test clips
above it: the false alarms actually realised. The **oracle** columns are a reference only. They pick the threshold on the
test set itself: the most anomalies caught with at most floor(rate × 100) normals flagged.

pAUC is DCASE's own: the raw area under the ROC up to FPR 0.1, divided by 0.1, so chance is 0.05. An earlier version of the
code reported sklearn's standardised pAUC instead (chance 0.5). The checker caught this, and it is fixed.

## Units

- Every rate is **per 10 s clip**.
- Per running week, ASSUMING the device judges one 10 s clip a minute: 10,080 clips a week, so 1 % ≈ 100 false alerts a
  week and 0.1 % ≈ 10.
- **Only 100 normal test clips per machine.** "0 of 100 flagged" shows only that the rate is probably under about 3 %
  (95 % upper bound, rule of three). It does not show zero false alarms.
- Persistence (alert only after N clips in a row over threshold), IF clips were independent: about 10,080 × p^N
  a week. At p = 1 %, that is ~1 a week for N = 2 and ~0.01 for N = 3. **Unverified:** DCASE clips are not a time
  series, so this can't be checked here. Real consecutive clips are correlated, so the true rate will be higher.

## Results so far: fan, whole 10 s clip

### All test clips (DCASE's setting: 10 target clips in the bank)

From `data/real/dcase2024-fan/embed/smoke.json`. Caught are of 100 anomalous clips; the number in brackets is normals
flagged, of 100.

| representation | detector | AUROC | DCASE pAUC | caught @0 % | @1 % | @5 % | @10 % | oracle @0 / 1 / 5 % |
|---|---|---|---|---|---|---|---|---|
| ours median (44) | kNN k=5 | 0.593 | 0.237 | 0 (0) | 8 (0) | 47 (37) | 53 (48) | 15 / 15 / 28 |
| ours median (44) | fuse z-mean | 0.623 | 0.214 | 0 (0) | 12 (0) | 42 (27) | 56 (36) | 14 / 15 / 22 |
| ours median+max (88) | fuse z-mean | 0.633 | 0.146 | 0 (0) | 3 (0) | 17 (7) | 36 (25) | 6 / 9 / 16 |
| BEATs L12 | kNN k=5 | 0.607 | 0.209 | 0 (0) | 9 (0) | 28 (8) | 38 (20) | 10 / 11 / 22 |
| BEATs L12 | Mahalanobis | 0.615 | 0.210 | 0 (0) | 20 (3) | 36 (19) | 51 (36) | 13 / 15 / 24 |
| BEATs L12 | PCA residual | 0.623 | 0.229 | 0 (0) | 17 (1) | 35 (19) | 55 (34) | 14 / 18 / 24 |
| BEATs L12 | fuse z-mean | 0.656 | 0.263 | 0 (0) | 14 (0) | 29 (8) | 40 (21) | 22 / 24 / 27 |
| BEATs mean L01-12 | fuse z-mean | 0.651 | 0.202 | 0 (0) | 17 (3) | 31 (17) | 44 (24) | 12 / 12 / 21 |
| mn10 last (960) | fuse z-mean | 0.610 | 0.182 | 0 (0) | 3 (0) | 15 (4) | 22 (6) | 4 / 9 / 22 |
| ours median+max + BEATs L12 | fuse z-mean of both | 0.659 | 0.253 | 0 (0) | 9 (0) | 27 (8) | 35 (21) | 20 / 21 / 27 |

The full table, with every detector and per-domain counts, is in the JSON.

### Source domain only (learn and test in one condition, the device's own case)

These use the earlier evaluation code (kNN k=5/10/20 and PCA only; standardised pAUC). Caught at a zero-false-alarm threshold,
of 50 anomalous clips:
- **ours:** median+max, kNN k=5, AUROC 0.592, caught 0; PCA residual 0.584, caught 0.
- **BEATs:** last layer, kNN k=5, 0.602, caught 1; PCA residual 0.644, caught 1 (0 normals flagged); layer 8, PCA residual 0.658, caught 0.
- **mn10:** last layer, kNN k=5, 0.551, caught 0.

### What fan says so far (one machine type only)

- **Nothing catches a fan fault at a zero-false-alarm threshold.** Our features score AUROC 0.59–0.63 and BEATs 0.61–0.66,
  a small gain, not a decisive one.
- **At a 1 % training threshold, BEATs catches somewhat more with no realised false alarms.** The BEATs L12 z-mean fusion
  catches 14 of 100 with 0 of 100 normals flagged. Our best is median z-mean, at 12 of 100 with 0 flagged.
- **Training thresholds don't transfer across domains.** At 5 %, our median kNN flags 37 of 100 test normals. Target-domain normals
  look anomalous because only 10 target clips are in training. The source-only view is the fair one for the device.
  That view has not yet been run with the new evaluation.
- **Density kNN behaves differently here.** It flips the domains (source AUC 0.41–0.48, target 0.73–0.83).
- **mn10 is the weakest of the three on fan.**

## Cost (measured on this Mac, Apple M2, one thread unless stated)

| model | params | GFLOP / 10 s clip | ONNX f32 | ONNX int8 (dynamic) | onnxruntime f32 s / 10 s clip | int8 s / 10 s clip | int8 vs f32 embedding (cosine) | peak RSS (ORT f32 / int8) |
|---|---|---|---|---|---|---|---|---|
| BEATs iter3+ | 90.3 M | 98.7 | 350 MB | 92 MB | 2.58 | 0.91 | 0.97 | 699 / 391 MB |
| mn10_as | 4.9 M | 1.22 | 11.3 MB | 3.0 MB | 0.042 | 0.042 | 0.91 | 113 / 116 MB |
| CED-tiny | 5.5 M | not measured | 20.7 MB | 5.4 MB | 0.060 | 0.035 | 0.999 | 105 / 79 MB |

- The float32 ONNX models match PyTorch: cosine 1.0, max difference about 1e-5.
- The front end (waveform to spectrogram) is not in the ONNX files. It takes 2–14 ms per 10 s clip in PyTorch.
- **PyTorch on the Mac is misleading for the board:** it uses Apple's AMX matrix unit. BEATs runs 0.39–0.64 s in PyTorch but 2.58 s in onnxruntime (plain NEON,
  as an A53 has). Use the onnxruntime numbers.
- 2 s ONNX files are exported (`data/models/onnx/*_2s_*`) but not yet timed. The 2-thread and 4-thread timings have not been run.

**UNO Q (QRB2210, 4× Cortex-A53): NOT MEASURED.** No board showed on `adb devices`. A rough estimate, **unverified**:
- **Assumption:** one A53 core is 8–15× slower than an M2 performance core on onnxruntime NEON kernels.
- **BEATs f32:** ≈ 20–40 s per 10 s clip on one core; int8 ≈ 7–14 s. Feasible once a minute only by taking most of a core.
- **mn10 and CED-tiny:** ≈ 0.3–0.9 s per 10 s clip on one core. Once a minute is easy.

## Not done yet (see RESUME.md)

- **The other machine types:** bearing, gearbox, slider, valve. All downloaded, embeddings partly done.
- **The summary across machine types,** mean and worst.
- **The full survey set** on the new evaluation for every machine: the every-layer sweep with ONE layer chosen for all
  machines; source-only; ours + embedding fusion.
- **CED-tiny** on every machine.
- **Int8 detection check:** do int8 embeddings still detect?
- **Clip length:** 2 s and 5 s clips.
- **Vibration:** the synthetic check, then SSCC.
- **Timing:** onnxruntime 2- and 4-thread timing, and on the board itself.
