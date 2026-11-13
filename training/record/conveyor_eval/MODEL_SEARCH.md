# Model search: the best "is it still normal?" model for the conveyor (generation 3 data)

Code: `training/record/conveyor_eval/model_search.py` (imports `evaluate.py`; nothing in `effente/` changed).
Results: `data/fake/model-search/`:
- `rotation.json`: the headline
- `calibration.json`, `stage1.json`, `stage2.json`, `naming.json`, `final_model.json`, `combined.json`, `persistence.json`
- `scores/v2__*.npz`: raw per-minute scores of every fit
- `cache/`: minute rows of every run

Synthetic data only, one run per fault, nothing checked on a real machine. **This replaces the 12:57 version.** The
judging rules, the line calibration and the ranking all changed since then (see [What changed](#what-changed-since-the-first-version)).

## The answer

**Ranking rule** (the user's, 13 Nov):
1. Fewest false alerts, with no slack.
2. Then fewest missed of 30.
3. Then earliest, scored over all 30 with misses counted as worst.

It is judged the way the device is used: learn on two healthy weeks, then watch. There are three rotations over w1,
w2 and heldout. False alerts are taken from the **worst** rotation; misses and lateness are the mean of the three.

**Four candidates are tied at 0 false alerts in every rotation.** Paired bootstrap over the 30 fault runs: the 95 %
interval on the difference in misses includes 0 for each.

| | Candidate | Misses by rotation (w1+w2 / w1+heldout / w2+heldout) | Mean missed | Lateness | Device change needed |
|---|---|---|---|---|---|
| 1 | **PCA, residual only, group-balanced** (mean of groups), 95 % kept; smoothing 10, sensitivity 0.625 | 8 / 16 / 17 | **13.7** | 0.580 | Training + scoring + **model-file format** (each input's group): team agreement |
| 2 | Gaussian (Mahalanobis), group-balanced conditional residuals; smoothing 10, sensitivity 0.825–0.85 | 10 / 13 / 22 | 15.0 (14.7 at 0.85) | 0.585 | New model type on the device (a mean and a precision matrix): port + `effente/` change |
| 3 | Gaussian (Mahalanobis), impulsive-max input; smoothing 10, sensitivity 0.625 | 14 / 19 / 12 | 15.0 | 0.564 | As 2, plus the minute-max input (input format change) |
| 4 | **PCA as the device scores it today (residual + T2), but 99 % kept and no 40-pattern cap**; smoothing 10, sensitivity 0.65 | 11 / 15 / 19 | 15.0 | 0.579 | **Two constants in `effente/trainer.py`** (`PCA_VARIANCE`, `PCA_MAX_K`); no format, input or runtime change |
| ref | Device PCA as it is today (95 %, cap 40); smoothing 1, sensitivity 0.5 | 22 / 21 / 20 | 21.0 | 0.818 | none |

- **By the rule, #1 is the winner:** PCA residual-only, group-balanced, 95 % kept, a 10-minute median, sensitivity
  0.625.
  - At the w1+w2 rotation it catches 22/30 (9/15 subtle). Slow faults are caught at 5/8, median wear 52.9 % of the
    obvious size at the first alert. Subtle sudden faults: 8/11. Sudden faults alert in a median 8.9 min.
  - In the other two rotations it catches only 14 and 13: the range is 13–22, not "about 14".
  - Its mean AUC is 0.85.
  - Its margin over #2–#4 is about 1.3 faults (CI −4 to +1.3), picked among about 9,300 rotation candidates on these same
    runs: **within noise.**
- **Against today's device PCA, all four are a clear win:** 7.3 fewer misses on average (95 % CI −11 to −4),
  lateness −0.24 (−0.34 to −0.13).
- **The edge:** the winner sits on one.
  - One step more sensitive (0.65): 1 false alert in the w1+w2 rotation.
  - One step less (0.6): misses 10 / 21 / 19 (mean 16.7), with lateness 0.64.
  - #2 has the widest zero-alert plateau: sensitivities 0.775 to 0.85 all give 0 false alerts in every rotation
    (misses 16.7 → 14.7). It is the safer dial setting. The plateau rule ranks it first.
- **What I'd recommend for the device:**
  - **#4 is the cheapest real improvement** (two trainer constants, needs agreement, ~6 fewer misses).
  - **#1 is the best measured**, but needs a model-file format change.
  - A fresh-seed confirmation set should decide between them before either change is made. **I generated nothing.**

**Beyond the minute alarm: hour-long persistence is the next big gain.** Both measurements below are at 0 false
alerts in every rotation; details in [Alone vs combined](#alone-vs-combined-model--early-warning) and
[Long persistence](#long-persistence-is-it-just-more-smoothing).
- **#1 OR its own "line" early warning:** misses **9.7** (7 / 11 / 11) against 13.7 alone. #4 + line: 11.3 against
  15.0, with no format change. "Line" means the score's
  30-min block median above 0.3× the line, held 6 blocks. That is a 3-hour persistence test on the same fit and
  line.
- **Persistence as the alarm** on other models: 9.3–10.3 at the best settings (Gaussian with the median+max input,
  bench PCA with the max input, group-max PCA).
- **Both best settings sit on an edge** (fraction 0.3 is the grid's floor; one step shorter hold gives false alerts).
  The steady settings around them give about 12.
- The whole system (model + persistence + the fixed safety checks + a per-second path for jam and splice damage) is
  being built by sound-fix in `system_search.py`.

**Missed faults at the winner** (kind, size):
- **w1+w2 (8):**
  - idler bearing, subtle
  - motor bearing, subtle
  - lubrication loss, subtle
  - overload, subtle
  - jam, subtle and obvious
  - splice damage, subtle and obvious
- **w1+heldout (16):** the 8 above, minus nothing, plus:
  - belt slip, subtle
  - cooling blocked, subtle
  - electrical, subtle
  - gear wear, subtle
  - idler seized, subtle
  - lubrication loss, obvious
  - unbalance, subtle and obvious
- **w2+heldout (17):** as w1+heldout, plus gear wear, obvious.

So **at 0 false alerts the winner misses 2–5 OBVIOUS faults per rotation:**
- jam and splice damage in every rotation;
- lubrication loss, unbalance and gear wear once heldout is a training week.

That is a third of the obvious faults in the worst rotation. The frontier below shows what it costs to catch them.

### The three-way trade-off (false alerts against misses against earliness)

For each level of worst-rotation false alerts per running week: the fewest mean misses reachable, the lateness there,
and the obvious faults caught. One false alert in the worst rotation is 0.55 per running week (about 18,100 judged
running minutes per rotation).

| Worst-rotation false alerts / running week | Best candidate | Misses (3 rotations) | Mean missed | Obvious caught | Lateness | Slow wear at first alert (w1+w2) | Sudden median min (w1+w2) |
|---|---|---|---|---|---|---|---|
| 0 | PCA residual, group mean, 0.95; n10 s0.625 | 8 / 16 / 17 | 13.7 | 13 / 11 / 10 | 0.58 | 52.9 % | 8.9 |
| ≤ 0.25, ≤ 0.5 | the same | | | | | | |
| ≤ 1 | PCA residual, group mean, 0.99; n10 s0.65 | 8 / 11 / 14 | 11.0 | 13 / 11 / 11 | 0.47 | 53.0 % | 5.3 |
| ≤ 2 | PCA residual, group max, 0.95; n10 s0.75 | 9 / 8 / 12 | 9.7 | 12 / 14 / 12 | 0.42 | 42.8 % | 5.5 |
| ≤ 3 | PCA residual, group mean, 0.99, +max input; n5 s0.825 | 9 / 9 / 6 | 8.0 | 13 / 12 / 13 | 0.43 | 59.5 % | 9.1 |
| ≤ 4 | PCA residual, group max, 0.95; n10 s0.8 | 7 / 8 / 9 | 8.0 | 13 / 14 / 13 | 0.37 | 49.7 % | 5.4 |
| ≤ 6 | the same, s0.85 | 2 / 5 / 8 | 5.0 | 14 / 15 / 13 | 0.27 | 32.1 % | 5.8 |
| ≤ 8 | the same, s0.875 | 2 / 5 / 7 | 4.7 | 14 / 15 / 13 | 0.25 | 16.4 % | 5.3 |

In words:
- Allowing **about 1 false alert a week** (worst rotation) catches about 3 more faults.
- **About 2** a week catches 4 more.
- **About 6** a week catches 9 more, and the obvious faults reach 14/15 on average.
- **All 15 obvious faults in every rotation** needs about 20 false alerts a running week: no candidate does it for
  less. Jam (one 3-minute jam) and splice damage are rarely caught by any minute-level model (N7), so they belong to
  the safety checks or per-second features.

## How it was judged (the agreed rules, 13 Nov)

- **Training:** two healthy weeks per rotation. The device leaves itself recording for a week or more, then learns.
  - Rotations: learn w1+w2 and watch heldout; learn w1+heldout and watch w2; learn w2+heldout and watch w1.
  - As a training week, heldout's clock is shifted 2 weeks, so it never shares timestamps with w1.
  - Only the running minutes the device itself sees (`trend_running`) are learned from.
- **Line: cross-week out-of-sample** (see [calibration](#line-calibration-what-keeps-false-alerts-put)).
  - Fit Prep + model on one training week, score the other, and the reverse.
  - The line is `gamma_threshold` of the pooled out-of-sample smoothed scores, at one false alarm a week of running.
  - The final model learns on both weeks.
  - Sensitivity moves the line (`effente.safety.margins()["model"]`: 4× the line at 0, ¼ at 1).
- **Candidate:** approach × input × setting × smoothing (median of the last 1, 3, 5 or 10 scores since the last stop,
  as the device's Watcher) × sensitivity (0 to 1 in 0.025 steps). An alert is smoothed score / line > margin, while
  running.
- **False alerts:** alert stretches, with a new one after every stop.
  - They count if they begin in the watched week's running minutes after its 6 h warm-up, or in the 30 fault runs'
    pre-onset running minutes after their warm-up.
  - The rate is per running week: stretches ÷ running minutes × 10,080. That is about 5,200 + 12,940 minutes per
    rotation.
- **Caught:** a stretch that starts inside the fault window while running and was **not on at the previous running
  minute**.
  - A stretch continues over a stop, so an alarm on at the evening stop and again at the morning restart is no
    catch.
  - The window is **per fault episode**: from the minute holding the episode's start to its end + 30 min. That
    matters for jam-subtle's five 3-minute jams. The span from onset to the last end + 30 min is only used to
    normalise sudden-fault lateness.
  - The minute holding the onset (onsets fall 3–56 s into a minute) counts as after onset, and is not judged for
    false alerts.
  - Every result reports how many catches the "not already on" rule removed; for the winner, 0.
- **Lateness:** per fault run in [0, 1], the mean over all 30; a miss counts 1.
  - Slow faults (`SLOW_FAULTS`): the wear at the first alert as a share of the obvious size (severity × wear share;
    at most 0.3 for a subtle one).
  - Sudden faults: minutes to the first alert ÷ the window's minutes.
  - The formula is the same as early-warning's. Slow-fault wear % in the tables is the median over caught faults
    only.
- **Safety checks are not included.** The current sound check fires on every passing forklift (about 32 false
  alerts a week); early-warning reports the whole device with fixed checks. There is no "+ current safety" column
  here, because I have no per-minute safety flags for these fits.
- **Checks:**
  - The float copies of the device's PCA and autoencoder match the real `train_model` + int8 runtime: the Watcher's
    `unknown_score` equals mine minute for minute (max difference 0.000), and the thresholds are identical.
  - Cached minute rows equal `evaluate.minutes_of`.
  - Every score array and calibration array is asserted finite, and every judge sees 30 fault runs plus the watched
    week.
  - The checker session recomputed the winners from `scores/*.npz` with its own code and got identical numbers.

**Selected on evaluation data**, stated plainly:
- Every sensitivity and smoothing was picked on the 30 fault runs and the watched weeks.
- So were the candidate list (stages 1–2), stage 2's top three and the inputs it tuned, and the choice of
  calibration method (by false alerts on heldout + pre-onset).
- The idea of group-balanced scoring came from sound-fix looking at the heldout week, an evaluation week.
- Only the line comes from each rotation's own training weeks.
- So every number here is optimistic; the fresh-seed confirmation set is the honest test.

## Line calibration: what keeps false alerts put

Today's line, gamma on the last 15 % of training (about 2 days), moved up to 2.5× between trainings: a setting picked
once gave 0 to 28 false alerts a week. Four ways of setting it were tested on 7 models × 4 training folds (both weeks,
swapped, w1 only, w2 only), each at the setting picked once on both weeks (`calibration.json`):

| Calibration | Worst fold's false alerts / rw (mean over models) | Same, one step less sensitive | Line ratio across folds | Mean missed (both weeks) |
|---|---|---|---|---|
| tail 15 % (today) | 12.1 | 10.3 | 2.0 | 16.4 |
| tail 30 % | 8.1 | 6.4 | 1.9 | 17.0 |
| tail 50 % | 1.4 | 1.5 | 2.0 | 19.1 |
| **cross-week out-of-sample** | **0.32** | **0.16** | 3.2 | 16.4 |

- Cross-week calibration keeps the false alerts put at no cost in misses.
- Its line moves *more* across folds (1.4–4.7×): the false alerts are stable because the line sits high enough, and
  follows the data. It is not a steady line.
- The method was chosen on evaluation data (above).
- All later stages use it.

## The approaches, under the rotation

Fewest mean misses at each worst-rotation false-alert level. Each cell: mean missed (by rotation), lateness.

| Approach / setting | 0 false alerts | ≤ 1 / rw | ≤ 2 / rw |
|---|---|---|---|
| PCA residual, group mean, 0.95 | **13.7** (8/16/17), 0.58 | 13.7, 0.55 | 12.3, 0.53 |
| PCA residual, group mean, 0.99 | 15.0 (14/14/17), 0.58 | **11.0** (8/11/14), 0.47 | 10.3, 0.45 |
| PCA residual, group max, 0.95 | 17.3, 0.63 | 11.3, 0.48 | **9.7** (9/8/12), 0.42 |
| PCA T2 (device scoring), 0.99, no cap | 15.0 (11/15/19), 0.58 | 15.0, 0.58 | 11.3, 0.48 |
| Gaussian, group mean | 14.7 (10/12/22), 0.57 | 14.3, 0.56 | 14.3, 0.56 |
| Gaussian, impulsive | 15.0 (14/19/12), 0.56 | 15.0, 0.56 | 13.7, 0.53 |
| Gaussian, median | 15.3 (14/19/13), 0.58 | 15.3, 0.58 | 13.3, 0.56 |
| PCA residual, 0.99, no cap | 19.0, 0.68 | 14.7, 0.55 | 13.0, 0.50 |
| sklearn autoencoder, best tuned | 19.0, 0.68 | 14.3, 0.57 | 11.0, 0.46 |
| device autoencoder, best tuned (mid 32) | 19.7, 0.74 | 16.7, 0.62 | 11.7, 0.49 |
| Gaussian, group max | 20.3, 0.73 | 19.7, 0.70 | 19.7, 0.70 |
| device PCA today (T2, 0.95, cap 40) | 21.0 (22/21/20), 0.82 | 19.7, 0.76 | 19.7, 0.76 |
| PCA residual, 0.95 (= bench PCA) | 22.0 (16/22/28), 0.77 | 19.3, 0.76 | 13.0, 0.52 |
| max \|z\| floor | 22.0, 0.79 | 20.0, 0.73 | 17.7, 0.66 |
| device autoencoder, group mean | 23.0, 0.81 | 16.3, 0.62 | 16.3, 0.62 |
| device autoencoder, default | 24.0, 0.86 | 21.0, 0.79 | 19.3, 0.70 |
| nearest neighbours | 26.7, 0.92 | 22.7, 0.83 | 18.0, 0.78 |
| similarity (AAKR) | 29.0, 0.98 | 24.3, 0.85 | 20.3, 0.84 |
| isolation forest | none at 0 | 24.0, 0.87 | 22.7, 0.85 |
| Gaussian mixture | none ≤ 2 | | |

**Every approach misses far more when heldout is a training week** (columns 2–3). Learning on w1+w2 is much kinder:
the winner catches 22 there against 14 and 13. Heldout is the warm week (N6: the weather warms only the camera, up to
about 9 C). A model that learns it as normal accepts more heat drift, and its line goes up 1.7× (13.1 → 22.6–22.9).
So the rotations aren't symmetric: what they measure is mostly "what if the learning weeks were unusually warm".

### What the scoring findings say

- **Group-balanced scoring is the biggest single gain.**
  - Each sensor group's (vibration + gyro, sound, heat, other) mean squared residual is divided by its learning
    level, then the groups are averaged.
  - The camera is 116 of 248 prepared inputs, and a plain mean lets its noise drown vibration and sound evidence.
  - At 0 false alerts it cuts PCA's misses from 22.0 to 13.7 (0.95 kept), and from 19.0 to 15.0 at 0.99.
  - "Worst group" is worse at 0 and better at 1–2 per week.
  - For the Gaussian it helps a little (15.3 → 14.7). For the device autoencoder it helps only above 0 false alerts.
  - The groups are fixed in code from `effente.features.FEATURE_SENSOR`, and each group's level is learned on that
    fit's own training rows.
  - **On the device it needs each input's group and residual level in the model file: a format change, all five
    people must agree.**
- **T2 (the device's "distance along the patterns") does not hurt at 0 false alerts.**
  - Today's scoring against residual-only, both at 0.95 / cap 40, rotation mean missed:

    | False alerts | Today's scoring (T2) | Residual only |
    |---|---|---|
    | 0 | 21.0 | 22.0 |
    | ≤ 1 / rw | 19.7 | 19.3 |
    | ≤ 2 / rw | 19.7 | 13.0 |

  - So T2 costs faults only once about 2 false alerts a week are allowed. At 0.99 kept, T2 is better at 0 (15.0
    against 19.0).
  - This **corrects the first version**, which said T2 "roughly halves" what is caught. That was true only at the
    2-alarm point on one split: 24 against 14 there. On that same split it was 13 against 11 at 0, and 14 against 11
    at ≤1.
- **Variance kept is a slope, not a peak, under the rotation.** Residual-only PCA, mean missed at 0 false alerts:

  | Variance kept | 0.90 | 0.92 | 0.93 | 0.94 | 0.95 | 0.96 | 0.97 | 0.98 | 0.99 |
  |---|---|---|---|---|---|---|---|---|---|
  | Mean missed | 22.0 | 22.7 | 22.3 | 21.3 | 22.0 | 20.7 | 20.0 | 18.3 | 19.0 |

  - The first version's sharp peak at 0.95 (0.94 → 16 caught, 0.95 → 24, one split, 2 false alarms) does not survive
    the rotation.
  - More patterns help a little. The device's cap of 40 (`PCA_MAX_K`) binds at 0.99 (73 patterns wanted).
- **Inputs:**
  - Minute max of the impulsive features helps the Gaussian very slightly (15.3 → 15.0, within noise).
  - Minute max of every feature hurts almost everything.
  - No case for changing the device's input format.
- **Smoothing:** 10 minutes (the grid's longest) wins nearly everywhere. Longer smoothing (15–30 min, outside the
  grid) cut false alerts but missed more on a single split (`stage2.json` → `beyond_grid_smoothing`).
- **max |z| was a broken floor in `evaluate.py`** (the checker, 13 Nov).
  - It scored the ±8-clipped inputs, so its score could never pass its own line (20.2; the score reaches at most
    0.40 of it). It caught 0/30 up to sensitivity 0.75, then alarmed always.
  - Here it scores unclipped robust z, over the readings with a real healthy spread (IQR > 0.1 spreads in learning:
    236 of 248; near-constant counts like `th_hot_pixels` dominated before).
  - Now it is a real floor: 22.0 mean missed at 0 false alerts.

## Alone vs combined (model + early warning)

`combined.json`, built on early-warning's fixed flag files of 14:09 (passed by the checker).
- **The alert is the model's alarm OR the early warning, per minute.** False alerts and catches are counted on the
  OR'ed flags, so overlapping stretches count once; same rules as everywhere else.
- **Same fits:** early-warning's rotation stage calls this script's `calibrated_fit` and `lines_for`. Its scores and
  calibration match mine exactly (max abs difference 0.0, all 31 runs) for the 4 candidates × 3 rotations.
- **The early-warning settings were picked on the fault runs and heldout,** by early warning alone in early-warning's
  grid (its 3 best, the best of each method, and the device default): another selection on evaluation data.
- **What "line" is:** the score's block median above a fraction of the model's own line, held several blocks. In
  effect a 3–6-hour persistence test on the same score.
- **Each rotation has one watched healthy week,** so "0 false alerts" rests on about 5,200 running minutes per
  rotation plus the fault runs' pre-onset minutes.

**In the rotation** (worst-rotation false alerts / running week; misses w1+w2 / w1+heldout / w2+heldout):

| Model (its best minute-alarm setting) | Alone: false alerts, misses, mean | Combined: early warning, false alerts, misses, mean | Lateness alone → combined | Slow wear at first alert (w1+w2), alone → combined | Faults only the early warning caught |
|---|---|---|---|---|---|
| #1 PCA residual, group mean, 0.95 (n10 s0.625) | 0, 8/16/17, **13.7** | line, 30-min blocks, > 0.3×, held 6: 0, 7/11/11, **9.7** | 0.58 → 0.45 | 52.9 % → 49.5 % | w1+w2: overload-subtle. w1+heldout: belt slip, electrical, idler seized, unbalance (all subtle), unbalance-obvious. w2+heldout: the same + gear-wear-obvious |
| #3 Gaussian, impulsive (n10 s0.625) | 0, 14/19/12, 15.0 | line, 60-min, > 0.4×, held 6: 0, 13/18/12, 14.3 | 0.56 → 0.55 | 56.4 % → 56.4 % | cooling-blocked-obvious (w1+w2), electrical-subtle (w1+heldout) |
| #2 Gaussian, group mean (n10 s0.85) | 0, 10/12/22, 14.7 | none helps: 14.7 | 0.57 | — | none |
| #4 PCA, today's scoring at 0.99, no cap (n10 s0.65) | 0, 11/15/19, 15.0 | line, 30-min, > 0.3×, held 6: 0, 9/11/14, **11.3** (60-min held 4: 0, 9/10/16) | | | |

- **For #1, the early warning is worth it:**
  - 4 more faults on average, at no false-alert cost in any rotation;
  - lateness 0.58 → 0.45;
  - it recovers both unbalance runs that dilution hid when heldout is a training week.
  - The cost: for sudden faults the extra catches come late (it waits 3 h), so the median minutes to a sudden-fault
    alert in w1+w2 rises from 8.9 to 21.8. The minute alarm still catches the obvious ones in minutes.
  - The catches it adds are all new stretches (0 removed by the "not on at the previous running minute" rule).
- **#4 + line is the cheapest device-ready system measured:** 11.3 missed at 0 false alerts in every rotation.
  - #4 itself is two trainer constants.
  - `Line` already exists in `effente/early_warning.py`, so using it means a different early-warning method and
    setting (today's default is the per-group CUSUM, which never reaches 0 false alerts here).
  - Both still need team agreement.
- **For the Gaussians it adds almost nothing.** early-warning's single-split "Gaussian 8 → 22 of 30" (tail-15 line)
  doesn't carry into the rotation with the cross-week line, where the Gaussian's minute alarm already catches most of
  what persistence would.
- **The rows that pair any model with the no-model CUSUM** reach 2–3 misses, but at 3.3 false alerts a running week.
  Those catches are the CUSUM's own (it catches 28/30 at that rate by itself), so they are **not** gains of the model.
- **Frontier, best model + early warning:** 9.7 missed at 0, 0.25, 0.5 and 1 false alerts a week; 9.3 at 2. Against
  the model alone: 13.7, 13.7, 13.7, 11.0, 9.7.

**For reference only: separate fits** (learn w1+w2, `evaluate.methods()` fits with the tail-15 line, early-warning's
full grid of 555 settings per model, at 0 false alerts):

| Model | Alone | + its best early warning |
|---|---|---|
| Gaussian | 14 missed | 8 (line, 60-min, > 0.4×, held 6) |
| PCA (bench) | 18 | 8 |
| PCA (device) | 19 | 10 |
| device autoencoder | 16 | 10 |
| sklearn autoencoder | 16 | 10 |
| nearest neighbours | 24 | 17 |
| similarity | 24 | 16 |

This is one split, with lines that move between trainings, so it is no evidence for the device's choice. It shows how
much a weak minute alarm leaves for persistence to pick up.

## Long persistence: is it just more smoothing?

`persistence.json`. Persistence was tested as the model's alarm by itself, and OR'ed with that configuration's best
minute alarm:
- **Rule:** blocks of 30, 60, 120 or 360 running minutes; the block median of score / line above 0.3–1.0×; held
  1–6 blocks. It is the same rule as `effente.early_warning.Line`, on the cross-week line, in the rotation.
- **Scope:** every rotation configuration except group-mean PCA 0.95 and the group-mean Gaussian, which sound-fix
  tests in `system_search.py`.

| Model | Minute alarm, 0 false alerts | Persistence alone, best at 0 false alerts | Steady neighbours |
|---|---|---|---|
| Gaussian, median + max input | 19.7 | **9.3** (5/10/13), 60-min > 0.3×, held 4 | 12.0–12.3 (held 6, or 120-min held 3) |
| PCA residual, group max, 0.95 | 17.3 | 10.0 (9/10/11), 30-min > 0.3×, held 4 | |
| PCA bench, median + max input | 22.0 | 10.3 (7/9/15), 30-min > 0.3×, held 6 | 11.7–12.0 (60-min held 6; 60-min > 0.4× held 2) |
| PCA, today's scoring at 0.99, no cap | 15.0 | 10.7 (8/10/14), 120-min > 0.3×, held 2 | 11.7 (60-min held 4) |
| PCA residual 0.95 / bench PCA, median | 22.0 | 12.0 (8/11/17) | |
| Gaussian impulsive / median | 15.0 / 15.3 | 15.0 / 15.3: none | OR'ed with the minute alarm: 13.0 / 13.3 |
| nearest neighbours | 26.7 | 11.3 (6-hour blocks) | |

- **It is smoothing, but on a scale the minute grid never reached.** The 10-minute median only sees minutes. These
  settings ask for 2–6 hours above a much lower bar (0.3× the line).
- So weak minute alarms gain the most: kNN 26.7 → 11.3, bench PCA 22 → 10–12. The Gaussian on median or impulsive
  inputs, whose minute alarm is already good, gains little.
- **Every best setting is at the grid's lowest fraction (0.3)**, and one step less hold gives false alerts. They are
  edges; the steady neighbours give about 12. That agrees with sound-fix's group-PCA result: steady 12.0, sharp peak
  8.7.
- **It is slow by design:** a sudden fault needs hold × block minutes (2–6 h) before persistence alerts. OR it with
  the minute alarm, which catches the obvious ones in minutes.

## Fault naming (a tie-breaker only)

What the screen would say for the first caught stretch, and whether it fits the fault (`evaluate.EXPECTED`;
learn w1+w2, `naming.json`):
- The wording is the most common `effente.fault_types.describe` over the stretch's first 30 minutes. That is the
  Watcher's wording path, on the device model's deviations.
- For the device's PCA and autoencoder candidates, the real int8 model trained on the same two weeks supplies the
  deviations; the alarm minutes are the candidate's own.
- For the Gaussian, its conditional residuals go through the device's deviation rule.

| Candidate | First alert fits the fault |
|---|---|
| PCA residual, group mean, 0.95 (#1) | 18 / 22 |
| Gaussian, group mean (#2) | 13 / 20 |
| Gaussian, group max | 15 / 17 |
| device autoencoder, mid 32 | 7 / 8 |

Misalignment's wording is unreliable whatever the model (the speed tracker reads double, S3). kNN, forest, mixture
and max |z| give no per-reading residuals, so they can't name faults. The bench PCA and the sklearn autoencoder could
be named the same way, but weren't.

## The final model (all three healthy weeks, for reference)

Learned on w1 + w2 + heldout, line by leave-one-week-out scores (`final_model.json`). There's no unseen healthy week
left to judge it on.

| Candidate | Line | × margin | Alarm above | Lines in the rotations |
|---|---|---|---|---|
| PCA residual, group mean, 0.95, s 0.625 | 20.6 | 0.707 | 14.6 | 13.1 / 22.6 / 22.9 |
| Gaussian, group mean, s 0.825 | 63.2 | 0.406 | 25.7 | 93 / 131 / 252 |

**Warning:** the Gaussian's three-week line is far *below* every rotation's. Its leave-one-week-out models learn on
two weeks, so they are tighter than the one-week models used in the rotations. At the same sensitivity, it would be
more sensitive than anything tested here, so expect more false alerts. The PCA's line falls inside its rotation range.
If one of them ships, check its false-alert rate on a new healthy week before trusting the dial.

**The rule I would use for the shipped line: train on the most recent two weeks, with their cross-week line.**
- That is exactly what the rotation tested.
- If more weeks are recorded, there are two other options:
  - train on all of them but never set the line below the lowest of the two-week (leave-one-week-out pair) lines; or
  - use the highest of those lines, to be conservative.
- For the PCA winner the three-week line (20.6) already sits inside its pair range (13.1–22.9).
- For the Gaussian (63 against 93–252) the floor would lift it to 93.
- Never ship a line below everything tested.

## What it would take on the device

- **#4, PCA with today's scoring at 99 % and no 40-pattern cap:** change `PCA_VARIANCE` and `PCA_MAX_K` in
  `effente/trainer.py`. No format, input or runtime change; older model files keep working. Shared pipeline, so team
  agreement.
- **The "line" early warning** (`effente.early_warning.Line`, already in the code): a different method and setting
  (30-min blocks, > 0.3× the line, held 6) instead of today's per-group CUSUM default. With #4 it gives 11.3 missed
  at 0 false alerts; with #1, 9.7. Team agreement, no format change.
- **Cross-week line calibration:** how `train_model` sets `threshold`. A trainer change, no format change. It matters
  more than the model choice: with today's tail-15 line, every model's false alerts move 0–28 a week between
  trainings.
- **#1, group-balanced residual PCA:** the trainer stops writing the T2 scale (I checked on the real int8 runtime that
  a model file without `pca_score` scores the residual alone, and the Watcher agrees minute for minute). On top of
  that, the model file needs each input's group and its residual level, and `Model.score` must use them: a **format
  change** (team agreement), small runtime code.
- **#2/#3, Gaussian:** a mean vector and a precision matrix (248 × 248, about 60 k numbers, int8-able), one quadratic
  form a minute. Small to port, but a new model type in `effente/` (team agreement). #3 also needs the minute-max
  input.
- **Every other approach** would need porting, and none beats these.

## Within noise, and the caveats

- **One run per fault.** One fault caught or missed moves mean misses by 0.33 per rotation. The top four differ by
  about 1.3 faults, CI −4 to +1.3: tied. Only "they all beat today's device PCA by about 7 faults" is clear.
- **Selection optimism:** about 9,300 rotation candidates (26,000 with stages 1–2) were judged on the same runs; see "Selected on evaluation data"
  above.
- **Training weeks matter more than the model:** misses 8–17 for the same winner across rotations.
- **Data flaws (`DATA_CHECK.md`):**
  - **N5** (no gyro drift): looseness is too easy, caught in minutes by most models.
  - **N6** (weather warms only the camera): the main reason rotations that learn on heldout do worse. It also favours
    group-balanced scoring, which down-weights the camera. Some of #1's lead may shrink once the weather reaches
    every temperature.
  - **S5** (the stop speed-up): per-second only. Smoothing resets at stops, and no alert here traces to it.
  - **N7:** splice damage and jam need minute max or per-second features. Even with the max input, minute-level
    models rarely catch them.
- **Slow faults' labels switch 3.5–16 h after onset by design,** so their minutes from onset are long; wear % is the
  measure.

## What changed since the first version

| | 12:57 version | Now |
|---|---|---|
| False alerts | per calendar week, pooled | per running week; worst of 3 rotations |
| Ranking | within 1 a week of the lowest, then lateness | fewest (no slack), then misses, then lateness |
| Caught | any alarm in the window | a new start in the per-episode window, not on at the previous running minute; the onset minute counts as after onset |
| Slow lateness | share of that run's own wear | share of the obvious size (as early-warning) |
| Line | tail-15 % (today's) | cross-week out-of-sample |
| Training | w1+w2, one split | three rotations over w1, w2 and heldout |
| Scoring | plain | + group-balanced variants |
| max \|z\| | clipped, could not alarm | a real floor |
| Winner | PCA residual-only, 24/30 at 0.89 / week (that is 2 alarms, 1.1 per running week) | four tied at 0 false alerts, 13.7–15.0 mean missed; group-balanced PCA first |

The first version's 24/30 corresponds to the "≤ 2 / rw" row of today's frontier (on one split). Its "T2 halves what is
caught" and "0.95 is a sweet spot" don't hold under the rotation (above).

## Reproduce

```bash
python3 -m training.record.conveyor_eval.model_search cache
python3 -m training.record.conveyor_eval.model_search calibration --workers 4
python3 -m training.record.conveyor_eval.model_search stage1 --workers 4
python3 -m training.record.conveyor_eval.model_search stage2 --workers 4
python3 -m training.record.conveyor_eval.model_search rotation --workers 4
python3 -m training.record.conveyor_eval.model_search naming
python3 -m training.record.conveyor_eval.model_search final
python3 -m training.record.conveyor_eval.model_search combined      # reads data/fake/early-warning-search/ew_*
python3 -m training.record.conveyor_eval.model_search persistence
```

Saved scores are reused, so a rerun only judges. One thread per worker.

The `stage3` and `analysis` stages are from the first version's folds and are superseded by `rotation`. Their old
JSON files (`stage3.json`, `analysis.json`, `baseline.json`) are kept for reference only.
