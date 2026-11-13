# Search check: are the conveyor-eval searches correct?

An independent check of the 13 Nov searches on the generation-3 conveyor data (`data/fake/conveyor-eval`), by a
read-only checker session. Every number marked **verified** was recomputed with separate code: the device's own classes
(`Watcher`, `EarlyWarning`, `calibrate`, `Safety`) or my own smoothing, window, episode and catch code. It was then
compared minute by minute or run by run with the search's own output. Synthetic data only; nothing here says how the
device does on a real machine.

First written 13 Nov 15:27; updated 15:45 for the finished system search. Checked against these files:

| Search | Code | Write-up | Results checked |
|---|---|---|---|
| Early warning | `early_warning_search.py` (14:02) | `EARLY_WARNING_SEARCH.md` (14:14) | `data/fake/early-warning-search/` rows, `ew_*`, `ew_rot__*`, `search.json` (14:04–14:15) |
| Model search | `model_search.py` (14:16) | `MODEL_SEARCH.md` (14:59) | `data/fake/model-search/` stage1/2, `rotation.json`, `combined.json`, `persistence.json` (14:11–14:23) |
| System search | `system_search.py` (15:29, branch `claude/sound-check-fix-8eb204`) | `SYSTEM_SEARCH.md` (15:35, in that worktree) | `data/fake/system-search/` up to `final2.json` (14:20–15:33) |
| SSCC loader | `training/record/datasets/sscc.py` (after 15:07) | `datasets/SSCC.md` | no data yet (download blocked) |

The agreed counting rules all checks use:
- **False alerts:** stretches beginning in the watched healthy week or before onset (each after its 6 h warm-up), per
  running week, with a new stretch after every stop.
- **Caught:** a stretch beginning inside a per-episode fault window that wasn't on at the previous running minute (a
  stretch continues over a stop). The minute holding the onset counts as after onset.
- **Ranking:** fewest false alerts (no slack), then fewest missed of 30, then earliness.

## Early-warning search: correct, yes (after 12 fixes)

**Verified:**
- **Device PCA rows** (`rows_PCA_on_the_device.json`, 14:04): 7 settings (model alone at 0.5 and 0.75, + safety,
  safety alone, early warning alone, model + early warning, all three) match the device's own `Watcher`,
  `EarlyWarning` and `calibrate`. False alerts, catches and every run's first-alert minute agree exactly. The
  model-alone rows also match plain `evaluate.py`: catches 9/18/30 at 0.5/0.6/0.75, held-out stretches 0/2/12.
- **Early-warning flags** (`ew_PCA_on_the_device.npz`): line, trend and ewma at sensitivity 0.5, block 60, equal
  `EW.EarlyWarning` run fresh for each run on all 96,480 minutes.
- **Join files** (`ew_rot__*`, 12 files: 4 of model-search's candidates × 3 rotations):
  - each file's line equals model-search's, and its scores are model-search's (difference 0.0);
  - every "line" setting, rebuilt from model-search's raw scores, matches with 0 minutes differing;
  - each rotation judges its own left-out week, and the line settings are off for that whole week.
- **Run boundaries:** none of the 5,727 early-warning settings has an alert before a run's first block closes.
- **Winner** (`search.json`, 14:09): Gaussian at 0.1 + line (0.4× the line, held 6 one-hour blocks). Refitted from
  scratch: 0 false alerts and the same 22 catches at the same minutes, 14 of them caught only by the early warning.
  The older Gaussian-alone result (8/30) is also exact.
- **Safety:** the vectorised safety copy equals `Safety.update` second by second (0 of 2,880 minutes differ). The 120 s
  sound fix equals `Safety(hold_s=120)` on 4 runs.

**Problems found, all fixed:**

| # | Problem | Effect | Fixed |
|---|---|---|---|
| 1 | **`to_minutes` leaked a run's last early-warning verdict into every later run** | inflated false alerts and hid catches. The 13:53 write-up said "early warning adds no catches", which was wrong. The 13:58 join files had a line setting on 99 % of the time instead of 7 % | yes; everything regenerated, old files in `superseded_leak/`. The conclusion reversed |
| 2 | an alert on at an evening stop counted as a new catch at the morning restart | the default CUSUM "caught" 30/30, all of them restarts; 0/30 really | yes (a stretch continues over stops) |
| 3 | no per-run flags for model-search to join on | no combined ranking possible | yes (`ew_*`, `ew_rot__*`) |
| 4 | the join used early-warning flags from a different fit than model-search's alarms | Gaussian 8 against 18 caught for "the same" model | yes; `ew_rot__*` use model-search's fits and lines |
| 5 | one long fault window for jam-subtle (5 separate jams) | 1 catch too many at some settings | yes (per-episode windows) |
| 6 | an alert starting in the onset minute, after onset, counted as a false alert and a lost catch | 1 run per setting (belt_rub-obvious, looseness-subtle) | yes (onset minute counts as after) |
| 7 | false alerts per calendar week, with a 1/week tie band | units and ranking differed from model-search | yes (running weeks, no slack) |
| 8 | safety checks always in the alert | the forklift false alerts dominated every row | yes (with/without as a grid dimension) |
| 9 | NaN levels silently meant "no alarm" | none found | counted; 0 |
| 10 | the sound-fix pick rule rewarded keeping today's "catches", which were forklift noise | wrong pick rule | yes |
| 11 | write-up missing N5 (2 of the winner's 22 are looseness) and S5 | caveats | yes |
| 12 | the full grid died once (12:47, BrokenPipe while being restarted with new code) | none: rerun | yes |

**Not verified by me:** the separate cross-week calibrated score-only CUSUM (15–18/30 at 0 false alerts). It's
labelled a separate check in the write-up.

## Model search: correct, yes (after 12 fixes)

**Verified:**
- **Stage 1, old and new rules,** from `scores/*.npz` with my own judge, for 4 candidates: lines, false alerts and
  catches are exact. The device-PCA score file equals the device `Watcher` within 0.0005 over 72,112 minutes. Plain
  `evaluate.py` reproduced exactly (device PCA at 0.5: 2/15 subtle, 7/15 obvious, 0 false alerts).
- **T2 claim:** 24 against 14 holds at 2 false alarms. Under the strict rule it's 13 against 11 at 0, and 14 against 11
  at ≤1 per running week. The final write-up words this correctly.
- **Rotations:**
  - pass 1's winner (Gaussian, impulsive: 14/19/12 missed) is exact;
  - pass 2's winner (group-balanced residual PCA 0.95: 8/16/17 missed; lines 13.097/22.582/22.884) is exact;
  - the frontier's ≤2 row (group-max PCA 0.75: 9/8/12, worst rotation 3 false alerts) is exact.
- **Combined, model OR its line early warning:**
  - group PCA + line: worst rotation 0 false alerts, 7/11/11 missed, mean 9.67 against 13.7 alone;
  - today's scoring at 99 % + line: 9/11/14 (and 9/10/16);
  - persistence as the only alarm (Gaussian, max input): 5/10/13, and one hold shorter gives 3 false alerts.
  - The section-B pairing uses the same fits: early-warning's saved levels equal model-search's tail-15 scores
    (difference 0).
- **Honest by construction:**
  - every line comes from its own training weeks (the rotation's two weeks; cross-week out-of-sample);
  - the group normalisation is learned on the fit rows;
  - the heldout week is never in a model's training except in the rotations, where it's an explicit training week.

**Problems found, all fixed:**

| # | Problem | Fixed |
|---|---|---|
| 1 | `max \|z\|` in `evaluate.py` could never alarm: its line (20.2) sat above its ±8-clipped score, so it wasn't a floor | yes (`MaxZFloor`) |
| 2 | ranking by a 1/week band, calendar weeks and any alarm in the window | yes (agreed rules) |
| 3 | headline "PCA 24/30 at 0.89/week" was the 2-alarm point; at 0 false alarms the leader was Gaussian (18/30) | yes (re-ranked) |
| 4 | "T2 roughly halves catches" overstated; the "0.95 sweet spot" was one split | yes (corrected under rotation) |
| 5 | selection on the fault runs not stated: settings, candidates, top 3, calibration method (chosen by heldout false alerts), the group-scoring idea | yes (stated plainly) |
| 6 | no thread limits; then orphaned stage-3 workers ran alongside the new pool (load 9.5–11 on 8 cores, 13:33) | yes (limits; orphans stopped) |
| 7 | silent NaN or missing runs possible | yes (asserts: 31 judged runs, finite scores) |
| 8 | one-off: a combined join at 14:08 ran on half-regenerated early-warning files | yes (deleted; rerun on checked files) |
| 9 | a stale line saying early warning never adds a catch | yes (removed) |
| 10 | the winner's three-rotation range (13–22 caught) not shown | yes |
| 11 | the zero-alert winner misses 2–5 obvious faults per rotation, unstated | yes (stated, with the cost to catch them) |
| 12 | catches mixed per-episode and continuous windows across the two searches | yes (per-episode both) |

## System search: correct, yes; its recommended system A needs the jam caveats below

`system_search.py` builds the whole system on model-search's fits: long persistence, the per-second checks on the
sound-fix branch, heat relative to the camera background, and combinations.

**Code reviewed:**
- **No leakage:** the persistence line comes from each rotation's calibration scores. The safety baseline and the
  per-second spike thresholds come from each rotation's two training weeks only.
- **Judging:** `model_search.judge_flags`, the agreed rules.
- **Run isolation:** blocks, rolling counts and minute mapping all run per run.

**Verified:**
- **Persistence:** group PCA (0.625) + block 120 > 0.15× line held 6: 0 false alerts, 6/9/11, mean 8.67. It's an edge:
  the worst neighbour has 3.87 false alerts a week. The steady pick (0.6, 0.25×, held 3) gives 12.0.
- **The first catch of splice damage:** the per-second sound-kurtosis rate (> p99.9, 10 min, > 1.5× the training
  maximum), rebuilt from the raw hour files. 0 false alerts; splice damage caught at 4.6 and 3.7 min.
- **Final system, steady setting,** on the w1+w2 rotation, end to end: my own alarm and persistence, the branch's real
  `Safety` class second by second, and my own two rate columns. 0 false alerts and the same 7 missed runs
  (5 subtle slow or overload faults, both jams).
- **Heat-relative variant:** judging from its saved scores gives 8/11/14 with 0 false alerts, as reported.
- **Two-model headline,** end to end on the w1+w2 rotation: heat-relative PCA OR Gaussian (n10, 0.625), OR
  persistence on the larger of their normalised scores (60-min blocks, > 0.3×, held 4), OR the per-second set.
  0 false alerts and the same 6 missed runs.
- **System A** (`final2.json`), end to end on the w1+w2 rotation: group PCA (n10, 0.625) + its line early warning
  (30-min blocks, > 0.3× the 10-min line, held 6) + the per-second set + the two jam signs, all from my own code.
  0 false alerts and the same 3 missed runs (subtle idler bearing, lubrication loss, motor bearing).

**Final results** (`final2.json`; 0 false alerts in every rotation; the neighbour column is the worst false-alert rate
one setting step away):

| System | Missed (w1+w2 / w1+heldout / w2+heldout) | Mean | Worst neighbour, false alerts / week |
|---|---|---|---|
| Group PCA alone (model-search's leader) | 8 / 16 / 17 | 13.7 | |
| Group PCA + line early warning | 7 / 11 / 11 | 9.67 | 7.8 |
| + per-second checks | 5 / 9 / 9 | 7.67 | 7.8 |
| **System A:** + jam signs | **3 / 7 / 7** | **5.67** | **7.8** |
| Two models, heat-relative, + persistence + per-second | 6 / 9 / 10 | 8.33 | 2.2 |
| the same + jam signs | 4 / 7 / 8 | 6.33 | 2.2 |
| Device-ready (today's scoring at 99 %, no cap) + line + per-second + jam | 5 / 7 / 10 | 7.33 | 6.1 |

**The jam signs are the weak part of system A,** and the 5.67 should be read as "with simulator-specific jam rules":
- **Written for the simulated fault.** Both were designed after seeing that jams were missed, to match how the
  simulator makes a jam (a 3-minute stop; 5-second stalls). That is selection on the fault runs at the level of the
  method, not only the setting.
- **"Short stop" (a restart within 30 min) has 0 false alerts only because the simulator has no unscheduled stops.**
  The healthy weeks contain no stop shorter than an hour. On a real conveyor, operators stop and restart often, and
  each would alert.
- **"Stall" (speed under 0.9× its 5-min running median) leans on the speed tracker,** which S3 calls unreliable. My own
  count finds 93 "stalls" in misalignment-subtle and 23 in looseness-obvious, against 5 real ones in jam-subtle. Its
  catches of misalignment-subtle and looseness-obvious are tracker jumps, not stalls.
- **The stall rule skips restarts only because `trend_running` switches on after the speed ramp** (checked on a healthy
  week), plus a speed > 1 guard. A device whose running flag switched earlier would see a "stall" at every restart.
- **System A also sits on an edge:** its neighbours reach 7.8 false alerts a week, against 2.2 for the two-model system.

**Other caveats SYSTEM_SEARCH.md carries** (checked in its 15:35 version):
- the steady pick as the headline, the strict one as the optimistic edge;
- the 40-column OR labelled optimistic and not used;
- the per-second set "chosen from experiment 2 on the same data";
- S5 on per-second results, and no jam catch from the per-second peak features;
- the unmerged `effente/` changes;
- N6 for the heat-relative gain;
- the autoencoder and Gaussian not needed (the Gaussian adds about 0.3–1).

**Its 15:35 version already carries most of the jam caveats:**
- the healthy weeks have no short stops, so a real site needs the shift plan or the user to confirm a stop;
- the stall's misalignment catch rides on the S3 tracker;
- system A's frac and hold sit at edges (frac 0.2: 7.8 false alerts a week; hold 5: 1.1);
- it moves system A one notch less sensitive (0.6, the same 3/7/7 in `final2.json`).

**Not in it:** the stall rule skips restarts only through `trend_running`'s lag, and the 5.67 isn't labelled "with
simulator-specific jam rules". Both were sent at 15:40.

## SSCC loader: conversion correct, yes; no data yet

- **Matches the device:** 1 kHz vibration, ±8 g at 4,096 LSB/g, 16 kHz audio.
- **Two fixes, both verified with my own synthetic test:**
  - the chip noise was too low in band; now about 305 µg/√Hz, 4.3–4.5 mg rms per axis;
  - each clip's first second carried a filter start-up transient; now equal to the other windows.
- **Unverified until data arrives:** column order, unit and which axis is vertical. The loader flags these.
- **The download is blocked by Google Drive's quota;** the user is fetching the 21 parts by hand.
- **Agreed for its use:** SSCC's 5 s clips can test only the per-second scoring and checks, not the minute and
  hour-scale system. Learn on normal conditions only, and hold out whole normal conditions at the fault speeds and
  noise levels. Label any setting re-picked on SSCC faults as optimistic.

## What the data and device showed (not search bugs)

- **The safety check "louder than normal" fires on every forklift with a reversing beeper** (83.6–93.7 dB, 9–18 s),
  because its limit comes from minute medians but it judges per-second sound. That's 18 false alerts on the held-out
  week at the default sensitivity, about 40 a running week with the pre-onset minutes. REALISM puts real beepers at
  95–110 dB, so it's realistic. The fix branch (60 s medians) gives 0, verified with its real `Safety`. **It changes
  `effente/`, so it needs team agreement.**
- **The device's default early warning (per-group CUSUM) is always on:** its normal is learned in-sample, and each run
  has its own offsets (N2, N6, S4). It shouldn't ship as it is.
- **Device PCA at the default sensitivity** catches 2/15 subtle and 7/15 obvious faults.
- **Data flaws are accounted for in all write-ups:**
  - N5: looseness too easy;
  - N6: camera-only weather, which also makes rotations that learn on heldout worse;
  - S5: the stop speed-up, per second only;
  - N7: jam and splice damage invisible in minute medians.

## Running well

- **CPU:** within 4 cores per search, except once. At 13:33, model-search's orphaned workers pushed the load to
  9.5–11 on 8 cores for a few minutes; they were stopped.
- **Memory:** at most about 1.4 GB per worker; no swapping.
- **Time estimates held,** apart from the one restart.

## Verdict

**All three searches are correct now,** but only after substantial fixes, two of which changed the answer. The early-warning search's cross-run leak hid the fact that a long,
low persistence test on the model's own score is the biggest gain available. Model-search's first headline ranked by
the wrong rule. Every headline number in the current write-ups that I recomputed matched exactly, with independent
code, minute by minute or run by run. That includes all three rotations, the combined join, persistence, and the final
system on one rotation. There's no leakage into any alarm line: lines and early-warning normals come only from each
rotation's training weeks. What is NOT clean, and is stated as such, is the selection: every sensitivity, smoothing,
persistence setting, candidate and calibration method was picked on the same 30 fault runs and watched weeks it is
judged on. With one run per fault, the top candidates (13.7–15.0 missed alone; 7.7–8.3 for the whole system without jam rules)
are within noise of each other. Training weeks matter more than the model (13–22 caught for the same winner), and the
best settings sit on edges. System A's 5.67 adds two jam rules written for the simulator's jams; they would likely alert
on every short operator stop on a real belt. Treat the numbers as optimistic. The real-data (SSCC) check is still to
come. A fresh-seed confirmation set, judged at fixed settings, is the honest next test before any `effente/` change.
