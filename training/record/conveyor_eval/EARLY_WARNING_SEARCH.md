# Early-warning search on the realistic conveyor (generation 3)

The early-warning half of the 13 Nov comparison. The question: does an early warning on top of the model's alarm catch more faults, or catch them earlier, without more false alerts? It's [early_warning_search.py](early_warning_search.py), with results in `data/fake/early-warning-search/search.json` (not committed). The model half, and the combined ranking of model + early warning across the three rotations, is model-search's (`model_search.py`).

> **Corrected 13 Nov, 14:20.** An earlier version of this file said early warning adds no catches. That came from a bug: a run's last early-warning block was held to the end of the whole concatenated data, so an early warning still on at a run's end leaked into every later run, making false alerts there and hiding catches. Every early-warning number below is re-derived after the fix, which the checker verified against `EW.EarlyWarning` itself (0 minutes differ on 3 settings, no alert before a run's first block). The model-alone numbers, the safety-check section and the in-sample calibration finding weren't affected.

## The answer

**Yes: one kind of early warning, "line", adds a lot.** It alerts when the model's score, as an hourly median, stays above a fraction of its alarm line for several hours. At 0 false alerts on the held-out week and before every onset, it takes the best model from 8 to 22 of 30 faults. The CUSUM-type early warnings (including the device's default) don't work as they are.

| | false alerts / running week | caught | earliness (0 best, 1 worst) | slow subtle | slow obvious | sudden subtle | sudden obvious |
|---|---|---|---|---|---|---|---|
| **Gaussian (Mahalanobis), sensitivity 0.1 + line (0.4 × the line, held 6 blocks of 60 min)** | **0** (held-out 0, before onset 0) | **22 / 30** | 0.421 | 2 / 4 | 3 / 4 | 8 / 11 | 9 / 11 |
| the same model alone, no early warning | 0 | 8 / 30 | 0.755 | 0 / 4 | 0 / 4 | 3 / 11 | 5 / 11 |
| the line alone, no model alarm | 0 | 22 / 30 | 0.455 | 2 / 4 | 3 / 4 | 8 / 11 | 9 / 11 |
| next less sensitive: model sensitivity 0.0 | 0 | 22 / 30 | 0.443 | | | | |
| next less sensitive: line at 0.5 × | 0 | 20 / 30 | 0.501 | 2 / 4 | 2 / 4 | 7 / 11 | 9 / 11 |

- **The learn-one-week check fails:** learning on one healthy week and watching the other gives **7.7 and 3.8 false alerts a running week**. The 0 above needs both weeks to learn from (details under "How much to trust it").
- **Who catches what.** The early warning makes 16 of the 22 first alerts. In 14 runs the model's alarm never fires at all, so only the early warning catches them (the checker's count). The model's alarm makes 6: belt_rub-obvious 108 min, electrical-obvious 55, idler_seized-obvious 63, looseness-obvious 75, looseness-subtle 71 and misalignment-subtle 105.
- **The early warning's catches**, in minutes from the onset:
  - belt_rub-subtle 299
  - belt_slip-obvious 828, belt_slip-subtle 356
  - cooling_blocked-obvious 830
  - electrical-subtle 342
  - gear_wear-obvious 1,526 (at 42.7 % wear of the obvious size), gear_wear-subtle 116 (0.3 %)
  - idler_bearing-obvious 1,438 (47.9 %), idler_bearing-subtle 464 (1.6 %)
  - idler_seized-subtle 314
  - misalignment-obvious 272
  - motor_bearing-obvious 1,685 (67.3 %)
  - overload-obvious 273, overload-subtle 1,493
  - unbalance-obvious 378, unbalance-subtle 301
- **Missed:** cooling_blocked-subtle, jam ×2, lubrication_loss ×2, motor_bearing-subtle and splice_damage ×2. The impulsive ones are invisible in minute medians (DATA_CHECK N7).
- **Wording:** 16 of the 22 first alerts describe the fault sensibly. The misfits are "BEEPING" for belt_rub-subtle, belt_slip-obvious and gear_wear-obvious, and "OUTSIDE NORMAL" for gear_wear-subtle, idler_bearing-subtle and overload-subtle.
- **It's slow, by design:** 6 hours of a raised hourly median before it says anything. Sudden faults it catches come at 4–25 h, and the slow ones at 40–70 % wear, except two lucky ones (below).

**What it is.** "line" is a **6-hour persistence test on the model's own score at 0.4× its line**: the score's median over each hour of running is above 0.4 of the alarm line, 6 hours in a row. It isn't a trend or change detector. It catches a small rise that lasts, which the per-minute alarm (a 5–10 minute median against the full line) can't wait for. That's far longer than anything in model-search's smoothing grid (1–10 minutes).

**How much to trust it.** It's the winner of about 157,000 settings, judged on the same runs it was picked on, and it fails the learn-one-week check badly:

| check | result |
|---|---|
| learn on normal-w1, watch normal-w2 / the reverse | **7.7 and 3.8 false alerts / running week** (0 on the held-out week when learning on both) |
| resampling the 15 fault kinds (1,000 draws) | best in 39 % of draws among the 12 top candidates (PCA + line 16 %, device autoencoder + line 45 %) |
| catches at implausibly small wear | gear_wear-subtle at 0.3 % and idler_bearing-subtle at 1.6 %, both "OUTSIDE NORMAL". DATA_CHECK calls subtle slow faults nearly invisible at the drive end, so these are probably chance |

So the 0 false alerts hold only with two weeks to learn from, and only for this held-out week. The rotations on model-search's own fits are the real test. There, on all 4 of model-search's top candidates and in all 3 rotations, **every line setting is off for 100 % of the watched healthy week** (the rotation files below; model-search judges their combined false alerts and catches).

This grid uses evaluate.py's way of fitting each model (the bench models on the first 85 % with a tail-15 line). model-search's fits and cross-week line are better: the Gaussian alone, smoothed 10 minutes, at its sensitivity 0.675, catches 18 / 30. The final answer is model-search's combined ranking on the rotations.

### Frontier: the fewest misses reachable at each false-alert level

All models and settings, ranked without the current safety checks (see the sound-check section):

| false alerts at most (/ running week) | best without early warning | caught | earliness | best with early warning | caught | earliness |
|---|---|---|---|---|---|---|
| 0 | Gaussian, sensitivity 0.1 | 8 / 30 | 0.755 | Gaussian 0.1 + line (0.4, hold 6, block 60) | **22** | 0.421 |
| 0.25 | the same | 8 | 0.755 | the same | 22 | 0.421 |
| 0.5 | the same | 8 | 0.755 | the same | 22 | 0.421 |
| 1 | device PCA, sensitivity 0.5 (0.56 / week) | 9 | 0.793 | bench PCA 0.4 + line (0.3, hold 2, block 60) (0.56) | 22 | 0.384 |
| 2 | Gaussian, sensitivity 0.3 (1.67) | 15 | 0.581 | Gaussian 0.1 + line (0.4, hold 4, block 60) (1.11) | 24 | 0.369 |
| 5 | Gaussian, sensitivity 0.4 (3.9) | 22 | 0.366 | no-model CUSUM per reading (k 2, h 80, block 60) (3.3) | 28 | 0.356 |
| 10 | sklearn autoencoder, sensitivity 0.75 (8.9) | 26 | 0.236 | similarity 0.75 + line (0.3, hold 1, block 30) (9.5) | 28 | 0.205 |

At every level up to 2 a week, an early warning catches 9–14 more faults than the best model alone at the same false-alert level. With no model alarm at all, the line alone does nearly as well: 22 at 0, 24 at 1.1 a week.

### Per early-warning method

The best at 0 false alerts, on any model:

| method | at 0 false alerts | what it does in practice |
|---|---|---|
| **line** (the score's block median over a fraction of the line, held N blocks) | **22 / 30** (Gaussian, bench PCA). Also device PCA 19, device autoencoder 20, sklearn autoencoder 19, similarity 14, nearest neighbours 13 | a long, low version of the alarm (above) |
| cusum_score (CUSUM on the score only) | 6 / 30 (isolation forest). **With out-of-sample calibration: 15 (device PCA) and 18 (Gaussian)**, alone (a separate check, not independently verified) | the score's in-sample bias keeps it on; calibrating it cross-week fixes that (below) |
| trend (Mann–Kendall) | adds nothing (8, the same as the model alone) | quiet; rarely fires |
| cusum per group (the device's default) | **never reaches 2 a week or fewer**, on any model, any setting, either calibration | always on: below |
| ewma per group | never reaches 2 a week or fewer | always on |
| no-model CUSUM per reading | never below 1 a week; 28 / 30 at 3.3 a week | every reading's small offsets add up |
| sensitivity (the alarm at a higher sensitivity) | the model-sensitivity axis itself | |

evaluate.py's max |z| alarms never or always, since its line sits above anything its clipped score can reach. model-search's MaxZFloor is the meaningful no-model floor.

### Why the default early warning (CUSUM k 0.5, h 5, block 60, per group) is always on

On device PCA it gives 20.6 false alerts a running week and catches nothing. It's on before the onset in all 30 fault runs; under the old counting, its 29 morning restarts looked like catches. It's mostly the method, with the data adding to it:

1. **Its normal is learned in-sample.** The model's score on the very weeks it was fitted on is lower than on any other week. In healthy test blocks (the held-out week, and before each onset), the score series sits a median **4.0 spreads above "normal"**, and over 2 in 97 % of blocks. Learning the early warning's normal out-of-sample instead (fit on one healthy week, score the other, both ways) brings that to **0.24**. That alone makes the score-only CUSUM work: 15–18 / 30 at 0 false alerts (above; a separate run, not independently verified). **The device should calibrate its early warning on cross-week scores, as model-search does for the line.**
2. **The per-group CUSUM stays on even then**, because the reading groups themselves sit higher in other runs:
   - vibration level, a median 1.5 spreads up in healthy test blocks: each run has its own drift (DATA_CHECK S4: "machines differ from run to run")
   - too_hot, 6 spreads up on the held-out week: +4 °C by design, plus N6's camera-only weather
   - camera_moved, up to 6 in some runs: N2, the per-run camera offsets

   A CUSUM per group adds up any persistent offset, benign or not.

So the per-group CUSUM against a fixed learned normal can't tell a warmer week, a remounted camera or a slightly different machine from slow wear. On a real machine that keeps running after learning, the run-to-run offsets would be smaller. Seasons and remounting are real, though. The camera_moved and thermal-group warnings here are N2/N6 artefacts as much as method failures. **The device's default early warning should not ship as it is.** The evidence here favours "line", or a score-only CUSUM with cross-week calibration, over the per-group CUSUM.

### What is noise

- **One run per fault, 30 faults:** each catch is 1 in 30. Settings within a catch or two of each other aren't really different. The top candidates (Gaussian + line, bench PCA + line, device autoencoder + line) all sit at 20–22.
- **Settings are picked on the same runs they're judged on**, out of about 157,000. The learn-one-week check (3.8–7.7 false alerts a week for the winner) shows how optimistic "0" is.
- **Two of the winner's slow-fault catches are probably chance** (0.3 % and 1.6 % wear).
- **Impulsive faults are under-counted** (N7): splice damage and jam barely move minute medians. model-search tests minute-max inputs.
- **Looseness is too easy** (N5: no gyro drift). 2 of the 22 are looseness.
- **Misalignment's wording is unreliable** (S3: the speed tracker doubles).
- **The safety checks and sound variants judge per-second rows**, which carry S5's 1 s speed-up at every stop. The vibration checks give 0 false alerts throughout, so S5 doesn't show here.

## Safety check: sound false alerts

**Cause:** the safety checks' "louder than normal" fires on every passing forklift that has a reversing beeper. It's about 84–94 dB for 9–18 s per second of sound, against a "learned loudest" of 77.3 dB, a +6 dB margin and a 10 s hold. The learned loudest is the 99th percentile of minute medians (`effente.safety.learn_baseline`), but the check judges per-second sound (the checker's root cause). Alone, at the default sensitivity, that's **40.1 false alerts a running week** (18 on the held-out week, 54 before the onsets). Its 27 "catches" are noise: only 2 aren't during a forklift, and they land up to 1,400 min after the onset, which a check that noisy gets by chance.

Every other check, at the default, gives **0 false alerts**:

| check | false alerts / running week | catches |
|---|---|---|
| louder_than_normal | 40.1 | 27 (2 without a forklift) |
| vibration_above_normal | 0 | 2 |
| hotter_than_normal | 0 | 2 |
| sensor_problem, vibration_over_limit, knocking, too_hot, heating_fast, repeated_jolts | 0 | 0 |

From sensitivity 0.6 up, the other checks give 5–16 false alerts a week (hotter_than_normal: a hot object in view, also seen by sound-check-fix).

**63 variants of the sound check** were tested: the learned level × how it's judged × the margin. A selection, at the default sensitivity:

| variant | false alerts / running week | catches |
|---|---|---|
| today: minute-median p99 (77.3 dB), per second, hold 10 s, +6 dB | 40.1 | 27 (noise) |
| root cause, level from per-second p99 (77.6 dB), hold 10 s, +6 dB | 37.8 | 27 (noise): forklifts pass it too |
| level from per-second p99.9 (85.8 dB), hold 10 s, +6 dB | 0 | 0 |
| **today's level, hold 30 s** | **0** | 3: belt_rub-obvious in the onset minute, idler_seized-obvious 0.9 min, overload-obvious 32 h later (likely chance) |
| **today's level, hold 120 s** | **0** | 2: belt_rub-obvious, idler_seized-obvious |
| today's level, minute median, 2 min in a row | 0 | 2 (the same) |
| today's level, hold 10 s, +9 dB | 9.5 | 18 (noise) |
| today's level, hold 10 s, +12 dB | 0 | 0 |

**The least-change fix: keep the level and the +6 dB, and require the sound to hold longer.** By the pick rule (0 false alerts, then most catches, then least change), that's 30 s. **120 s is the safer choice**: across the sensitivity dial it stays at 0 false alerts all the way to 0.9 (catching 0, 0, 2, 2, 3, 3, 3 and 4 as the dial goes from 0.3 to 0.9). 30 s gives 0.56 a week at 0.75, 2.8 at 0.8 and 7.8 at 0.9, though it catches more (up to 21 at 0.9).
- **Root-cause fix alone doesn't help:** learning the level from per-second rows doesn't remove the forklifts, which are rarer than 1 % of seconds but louder than the machine's loudest 1 %.
- **The implemented fix:** sound-check-fix's (outside-normal checks on a rolling 60 s median, branch `claude/sound-check-fix-8eb204`) is the same idea. With the device's real Safety it measured 75 → 0 false alerts at 0.5, catches not started by a forklift unchanged.

**The whole device**, the winner (Gaussian 0.1 + line) with the fixed checks (30 s or 120 s, the same here):

| | false alerts / running week | caught | earliness |
|---|---|---|---|
| fixed checks at the winner's dial (0.1) | 0 | 22 / 30 (they add nothing at 0.1) | 0.421 |
| fixed checks kept at their default (0.5) | 0 | 22 / 30, a little earlier | 0.393 |
| fixed checks alone at 0.5 | 0 | 6 / 30 | |

## How it was done

- **Data:** generation 3 (DATA_CHECK.md), per-minute medians through `evaluate.minutes_of` (trend features of a week or more left out). It learns from normal-w1 + normal-w2 and is judged on the held-out week and the 30 fault runs (2 days each).
- **Models:** all 10 of `evaluate.methods()`, fitted as evaluate.py fits them. The device's PCA and autoencoder use the device's own trainer and Watcher level; the bench models use BenchMethod's line, from the gamma fit on the last 15 %.
- **Group series:**
  - from `Model.deviation_vector` for the device's two
  - from the same rule on residuals for PCA, the autoencoder and similarity
  - from Z for models without residuals
  - plus the score over the line
- **Early warning:** `effente.early_warning`'s own detectors (Cusum, Ewma, Line; the trend test computed the same way as `Trend`) on blocks of 15, 30 or 60 running minutes, against `calibrate_values` on the training weeks (in-sample, as the device does). Each block's verdict holds until the next block, or the run's end. The settings grid:
  - cusum and cusum_score: k 0.25–1 × h 2–20
  - ewma: λ 0.1–0.5 × L 1.5–6
  - trend: n 4–12 × z 1.28–2.5 × rise 0.5–2.5
  - line: frac 0.3–0.9 × hold 1–6 blocks
- **Model sensitivity:** off, then 0 to 1 in 13 steps. The safety checks ran at the same sensitivity (a vectorised copy of `Safety.update`, checked row for row against it), with and without them.
- **11th approach:** a CUSUM on every prepared reading's z, both directions, per minute or on blocks.
- **Settings in all:** 157,110, about 7 min on 3 cores.
- **The out-of-sample calibration check** (cusum, cusum_score and ewma on device PCA and the Gaussian, with the early warning's normal from cross-week scores) was a separate run, not part of the grid.
- **Definitions** (agreed with model-search and the checker, 13 Nov):
  - **Alerts:** count only while running.
  - **False alerts:** stretches that begin in held-out running minutes (after its first 6 h) or before the onset in fault runs (after their first 6 h), per running week (÷ those minutes × 10,080). A new stretch starts after every stop.
  - **Caught:** a stretch that begins after the onset, while running, inside a fault window (each episode's start to its end + 30 min), and wasn't on at the previous running minute. For catches, a stretch continues over a stop.
  - **The onset's own minute:** the minute holding the onset (minute_t0 + 60 > onset) counts as after it for catches and isn't judged for false alerts. The same goes for each episode's start.
  - **Earliness:** the mean over all 30 faults. A slow fault counts its wear at the first alert (as a share of the obvious size), a sudden one its minutes from the onset as a share of the window. A miss counts 1.
  - **Ranking:** the fewest false alerts (no slack, among settings that catch something), then the fewest misses, then the earliest.
  - **Also reported:** evaluate.py's unit (held-out stretches per running day), and how many catches or false alerts are only restarts after a stop.
- **Checked independently** by the checker session, using the device's own Watcher, EarlyWarning, calibrate and Safety. After the fix: line, trend and ewma flags on device PCA match per minute (0 of 96,480 differ). Before it, model-alone and safety rows matched exactly on 7 settings, and the 120 s sound fix matched with real `Safety(hold_s=120)`.

## For the combined ranking (model-search)

- `ew_<model>.jsonl` / `.npz`: per early-warning setting, its key and the per-minute flags over the 31 judged runs (the early warning only, the same at every model sensitivity), on this grid's evaluate.py fits.
- `level_<model>.npz`: each model's alarm level.
- **The main grid is a separate study.** It fits each model the way evaluate.py does, so its flags come from those fits, not from model-search's, and shouldn't be ORed with model-search's alarms. The rotation files are the ones for that.
- **`ew_rot__<candidate>__<rotation>.*`:** model-search's top 3, and its 4th tie (the device's PCA with 99 % of variance and no 40-pattern cap), in its three rotations (learn on two healthy weeks, watch the third), plus the no-model CUSUM.
  - **Fits:** model-search's own: `model_search.calibrated_fit(…, "crossfit")` and `lines_for`, so the same Prep, detector and line per rotation, with residuals from that detector.
  - **The early warning's normal:** from that rotation's two training weeks only.
  - **Identity:** each key records `same_fit_as_model_search`. The scores match model-search's saved ones exactly (max difference 0.0, all 31 runs, all 12 candidate files). The checker rebuilt every line setting from model-search's raw scores: 0 minutes differ.
  - **Settings per candidate:** its 3 best by early warning alone in the grid, the best of each method, and the device default.
  - **Healthy-week result:** every line setting is off for the whole watched healthy week in every rotation.

```bash
python3 -m training.record.conveyor_eval.early_warning_search --data data/fake/conveyor-eval --out data/fake/early-warning-search --workers 4
```

```bash
python3 -m training.record.conveyor_eval.early_warning_search --data data/fake/conveyor-eval --out data/fake/early-warning-search --stage rotations --candidates <top3.json> --workers 4
```
