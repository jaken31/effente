# Conveyor evaluation: progress notes (track 2, 13 Nov 2026)

Where the search for the best model stands, what was decided, and what's still open. Everything here is on
**synthetic** data (generation 3), so the numbers are a guide, not a promise. Details live in the linked files.

## Resuming (written 13 Nov afternoon, before a usage pause)
Each side session writes its state and next steps to a resume file. Background jobs (downloads, grids) keep running while
the sessions are paused.

| Session | Doing | Resume file |
|---|---|---|
| model-search | finishing `MODEL_SEARCH.md`: the combined section, the group autoencoder, the final-model line rule | `data/fake/resume/model-search.md` |
| sound-fix | the system search: experiment 1 done; 2–6 next (per-second path, heat vs background, unbalance misses, combinations, final system), then SSCC | `.claude/worktrees/sound-check-fix-8eb204/data/fake/resume/system-search.md` (its own worktree) |
| sscc-data | downloading SSCC (about 200 GB), then unpacking, `sscc.py` and `features.npz` | `data/real/sscc/RESUME.md` |
| eval-checker | reviewing all of the above, then `SEARCH_CHECK.md` | `data/fake/resume/eval-checker.md` |
| early-warning | done, apart from small follow-ups | `data/fake/resume/early-warning.md` |

On resume, the main session reads this file and those resume files, then messages each session to continue.
At the 14:50 pause, only early-warning had written its resume file. The others hit the limit first, so they resume from
their own transcripts. The SSCC download had not started yet: `training/record/datasets/sscc_download.sh` is written, and
`data/real/sscc/raw/` is empty.

## The data
- Generation 3 of the simulated conveyor is in `data/fake/conveyor-eval`: 33 runs, 9.6 GB.
  - Two healthy training weeks (`normal-w1`, `normal-w2`).
  - A held-out healthy week (`heldout`: warmer, busier, with a maintenance stop).
  - 30 fault runs: 15 kinds, each subtle and obvious, two days each.
  - It was made by [generate.py](generate.py) to the levels in [REALISM.md](REALISM.md).
- **Checked:** [DATA_CHECK.md](DATA_CHECK.md) gives "YES, good to share", with known flaws to fix in the next generation:
  - N5: the gyro has no healthy drift.
  - N6: weather warms only the camera, and by about ±4 °C instead of ±2 °C.
  - S5: a 1 s speed jump at each stop.
  - N2: each fault run effectively has a different thermal camera.
  - N7: impulsive faults show in minute max, not median.
- **Shared:** a private GitHub repo, `finn-freas/effente-conveyor-data`, release `data-v1`, one `.tar` per run plus `SHA256SUMS`.
- **No room sensor:** the device's only temperature sensor is the IMU's (`imu_temp_c`), on the bearing housing. So the five `env_*` room features are NaN by design.

## The goal and how candidates are ranked
The goal, in the user's words: catch faults "as early as possible" while "as accurate as possible", with false alerts
"as low as possible, ideally 0", and missing as few faults as possible.
- **Train** on healthy weeks only. **Evaluate** on the fault runs. **False alerts** are counted on the healthy week left
  out, plus before onset in the fault runs.
- **Ranking:**
  1. Fewest false alerts (per running week, no slack).
  2. Then fewest missed faults (of 30).
  3. Then earliest: slow-fault wear % at the first alert, or minutes; a miss counts as worst.
  - Fault naming (whether the screen's words fit the fault) breaks ties.
  - Every write-up also shows the trade-off: the fewest misses reachable at 0 / 0.25 / 0.5 / 1 / 2 false alerts a week.
- **Sensitivity is one more setting to search.** The winner can be best at one value; it doesn't need to be good across the dial.
- **The model and early warning are judged together,** as the device runs them, and each model alone is also compared with model + early warning.
- **Rotation, as the device is used** (record at least a couple of days, likely a week or more, then train, then watch):
  - Learn on two of the three healthy weeks and count false alerts on the third, all three ways round.
  - Rank on the worst rotation's false alerts.
  - The final device model is trained on all three weeks.
  - Recording-length tests were dropped.

### Counting rules, the same in every search and the join
- An alert stretch continues over a stop for catches. For false alerts, a new stretch starts after every stop.
- **Caught:** a stretch that wasn't on at the previous running minute, and starts inside a fault episode's window
  (episode start to end + 30 min, per episode). The minute containing an onset counts as after onset, and isn't judged for false alerts.
- **Safety checks are reported separately** (with and without). The ranking is on the model and model + early warning.

## Findings so far
- **The forklift bug, fixed on branch `claude/sound-check-fix-8eb204` (commit `ede4c49`, not pushed or merged).**
  - **The bug:** the device learned its normal sound level from 1-minute medians, but judged each second against it. Every forklift's reversing beeper (83–94 dB for 9–18 s) raised LOUDER THAN NORMAL: 75 false alerts at sensitivity 0.5, about 5 a day.
  - **The fix:** outside-normal checks are now judged on each reading's median over the learned period (60 s), then the usual 10 s hold.
  - **Result:** false alerts 75 → 0 at 0.5, with the same catches.
  - **Cost:** a lasting change now shows in about 40 s, not 10.
  - It changes `effente/safety.py` and `trainer.py`, so it **needs team agreement**. Track 5's `platform/CONTRACT.md` still says "about 10 seconds".
  - Verified independently by the checker.
- **The alarm line was the weak part.** Today's line comes from the last 15 % of training (about 2 days) and moves up to 2.5× between trainings, so a sensitivity that gives 0 false alerts on one training gives 9–50 a week on another. Setting it from out-of-sample scores across both weeks brings the worst fold to about 0.3 a week at equal misses.
- **Device PCA's extra pattern-distance term (T2):** on one split it cost catches (13 vs 11 at 0 false alerts, 24 vs 14 at 2). In the rotation it doesn't hurt at 0 false alerts (21.0 vs 22.0 missed), only at about 2 a week, and at 99 % variance it helps (15.0 vs 19.0). What helps is more patterns (no 40 cap) and group-balanced scoring.
- **Group-balanced scoring, from sound-fix, at the user's request:**
  - The thermal camera is 133 of the 285 inputs, and its noise swamps vibration and sound.
  - Scoring each sensor group against its own training level, then averaging equally, takes PCA from 12/13/8/12 to 19/20/19/16 caught at 0 false alerts over four trainings.
  - It needs each input's group in the model file: a format change.
  - Being tested in the rotation.
- **The leader so far, model only, before the rotation:** Gaussian (Mahalanobis), 10-minute smoothing, catches 16–18 of 30 at 0 false alerts. It still misses 5 of the 15 obvious faults. Jams and splice damage are always missed in minute data; they need per-second checks.
- **Current best, model + early warning in the rotation** (model-search's combined join, recomputed and passed by the
  checker): group-balanced residual PCA plus its own "line" early warning gives **0 false alerts in every rotation,
  with 9.7 missed of 30 on average**, against 13.7 for the model alone. Faults still missed: the per-second ones (jam,
  splice damage) and some slow subtle ones. The system search works on those.
- **The checker has independently passed the whole model search** (13 Nov about 15:05), including the two systems below.
- **The cheapest device-ready system measured** (model-search, rotation): today's device PCA scoring with 99 % variance
  and no 40-pattern cap (two trainer constants), OR the existing `early_warning.Line` (30-min blocks > 0.3× the line,
  held 6). It gives **0 false alerts in every rotation, 11.3 missed** (9/11/14), against 15.0 for the model alone and
  21.0 for the device as it is.
- **Long persistence on the other models** (model-search, `persistence.json`; sound-fix has the two group-mean models):
  - It helps weak minute alarms most: kNN 26.7 → 11.3, bench PCA 22 → 10–12, the Gaussian on the max input 19.7 → 9.3.
  - Every best setting is at the grid's lowest fraction (0.3) and on a false-alert edge; the steady neighbours give
    about 12.
- **System search, experiment 1: long persistence** (sound-fix, `system_search.py`; checked: the checker reproduced the best setting exactly). The score's block median over
  30–360 min against a fraction of the line, held 1–6 blocks, ORed with the model alarm. Mean missed at 0 false alerts in
  the worst rotation (heldout / w2 / w1):

  | | Model alone | Model + persistence, steady setting | Model + persistence, best setting |
  |---|---|---|---|
  | Group PCA | 13.7 | **12.0** (9/13/14): block 120 min > 0.25× line, held 3 | 8.7 (6/9/11), a sharp peak, likely overfit: its worst neighbour gives 3.9 false alerts a week |
  | Group Gaussian | 14.7 | 15.0 | 13.7 |

  - **Persistence helps PCA by about 4–5 faults, and the Gaussian hardly at all.** So with persistence, PCA leads.
  - The early-warning search's single-split "8 → 22" for the Gaussian does not carry into the rotation.
- **System search, experiment 4: obvious unbalance missed in two rotations. Not a bug; it's dilution.**
  - Its vibration group is 27–30× its training level (velocity 8.3 mm/s), but the mean over 4 groups, and over about 67
    inputs within the group, dilutes it to a score of about 9–10.
  - When heldout is in training, the line is about 23, because the weeks differ (heldout is 10 % busier and warmer)
    more than this fault does, as a mean-square sees it.
  - **Top-k or max within groups doesn't help** (`topk.json`). Top-3/5 miss 16–16.7, and max misses 18–21.7, against 13.7,
    because they also pick up the busier, warmer week's inputs, which raises the line 1.2–2×. Only top-10 with max across
    groups ties (13.7) while catching obvious unbalance. **So the fix for the dilution is the device's per-second check
    vibration_above_normal at 0.5**, which catches obvious unbalance in every rotation at 0 false alerts (experiment 2), not a model change.
- **Recommended system (synthetic, 13 Nov about 15:50; `SYSTEM_SEARCH.md`, `final2.json`): 5.7 missed of 30 (3/7/7)
  at 0 false alerts in every rotation.** System A at sensitivity 0.6:
  1. Model-search's verified group-mean residual PCA, plus its "line" early warning.
  2. The per-second set: the device safety checks at 0.5 with the forklift fix, plus the `snd_kurt_hf` and `vib_kurtosis` spike rates.
  3. Two **jam signs**, neither seen even once in any training week, both 0 false alerts in every rotation:
     - **Short stop:** a stop with a restart within 30 min, alerting at the restart. Catches jam-obvious.
     - **Stall:** speed below 0.9× its 5-min median for ≥ 3 s, with the belt still running 10 min later. Catches jam-subtle.
  - **Caveat:** the simulated healthy weeks have no short stops at all. A real belt stops for operators, breaks and starved
    feed, so on a real site the short-stop sign needs the shift plan or a "that was me" button.
  - **Its line early-warning fraction and hold must not be loosened:** at a fraction of 0.2 it gives 7.8 false alerts a week.
  - **Checker's verdict (15:45):** both final systems reproduce end to end on rot_heldout with independent code (system A:
    0 false alerts, the same 3 missed). But the 5.7 needs strong caveats:
    1. The jam signs were written after seeing the simulated jams missed.
    2. "Short stop" has 0 false alerts only because the simulator has no unscheduled stops under an hour. On a real belt
       it would alert on every operator stop.
    3. "Stall" relies on the speed tracker, which is unreliable under misalignment (S3): 93 "stalls" in misalignment-subtle
       and 23 in looseness-obvious, against 5 real ones in jam-subtle.
    4. System A's neighbours reach 7.8 false alerts a week, against 2.2 for system B.
  - **So the honest headline is without the jam rules: 7.7 missed (system A) or 8.3 (system B), at 0 false alerts.** The jam
    signs are a promising idea needing real-site design (the shift plan or a "that was me" input, and a reliable speed
    measure), not a measured result.

  | System | Alone | + per-second | + jam signs | Needs on the device |
  |---|---|---|---|---|
  | A. Group PCA + line early warning | 9.7 | 7.7 | **5.7** | the forklift fix, group field in the model file, new detectors |
  | B. Relative-heat PCA \| Gaussian + persistence | 10.3 | 8.3 | 6.3 | as A, plus a Gaussian port; relies on relative heat (N6) |
  | C. Device-ready: PCA 99 %, no cap, + `early_warning.Line` | 11.3 | 9.3 | 7.3 | the forklift fix and new detectors; no model-format change |

  The device today misses 21.0 with the model alone. The Gaussian and the autoencoder aren't needed.
- **System search, experiments 8 and 9: no robust gain, so the recommendation stays system A at sensitivity 0.6.**
  - **Experiment 8, a slow CUSUM per physics subgroup** (on group PCA residuals, cross-week calibrated):
    - Any subgroup gives 4–24 false alerts, because low-frequency sound and IMU heat track the held-out week's load and warmth.
    - High-frequency vibration + high-frequency sound reaches 4.3 missed, catching slow wear at 32–52 % instead of about
      50–75 %. But it's not a plateau (1.5× the limit gives 2–3 false alerts), and it was chosen after the others failed.
    - **Not adopted.** Worth one test on a fresh confirmation set.
  - **Experiment 9, a load-stratified line** (belt speed as the load proxy): the per-stratum lines come out almost equal,
    so there's no gain. The held-out week's line inflation isn't a belt-speed load effect.
  - **Still missed in every rotation:** subtle idler bearing, lubrication loss and motor bearing. They're nearly invisible
    at the drive end, so they likely need another sensor (a kHz accelerometer near the idlers, or motor current), not another model.
- **Best system so far (synthetic; the checker verified its end-to-end run on w1+w2: 0 false alerts, 7 missed, the same runs): 8.3 missed of 30 (6/9/10) at 0 false alerts in every rotation,
  steady pick** (strict 6.7, on an edge). `SYSTEM_SEARCH.md` in sound-fix's worktree; `final_relheat.json`.
  1. Group-mean PCA (residual, 95 %) OR group-mean Gaussian, both on relative heat (camera max/mean, IMU temperature and
     heat history minus `th_background_c`). Shared sensitivity 0.625, smoothing 10.
  2. Persistence: the max of the normalised scores, 60-min block median over 0.3× the line, held 4 blocks.
  3. The device safety checks at 0.5, with the forklift fix.
  4. The `snd_kurt_hf` and `vib_kurtosis` spike rates (p99.9, 30 min, 2× the training maximum). Parts 3 and 4 were fixed beforehand.
  - **Neighbours:** all 0 false alerts except sensitivity 0.675 (1.1 a week); 8.7–11.7 missed.
  - **How early:** sudden faults 8–19 min (median); slow faults at 56–74 % of full wear.
  - **Frontier:** 6.7 missed at 0–1 false alerts a week, 5.0 at 2.
  - **Always missed:** both jams, and subtle idler bearing, lubrication loss, motor bearing and overload.
  - **Needs on the device:** the forklift fix, a group field in the model file, and a Gaussian port. The relative-heat gain
    is probably inflated by N6.
  - **Being added:** the same per-second set on model-search's verified PCA + line system, and on the cheapest device-ready
    system, so the team can choose by cost.
- **System search, experiments 5 and 6: combinations and the final system** (`combine.json`, `final.json`; not yet checked).
  Mean missed at 0 false alerts in the worst rotation.
  - **Combining models barely helps.** PCA | autoencoder is identical to PCA. PCA | Gaussian with persistence:
    9.3 strict / 11.3 steady, against PCA's 8.7 / 12.0. A max of normalised scores is about the same.
  - **The final system,** models + persistence, ORed with a per-second set fixed beforehand (device safety checks at 0.5,
    plus the `snd_kurt_hf` and `vib_kurtosis` spike rates at p99.9 / 30 min / 2× the training maximum). 0 false alerts in every rotation:

    | System | Strict | Steady |
    |---|---|---|
    | PCA | 7.0 (4/8/9), lateness 0.41 | 9.7 (7/11/11), lateness 0.44 |
    | PCA \| Gaussian | 8.0 | 9.3 |

  - **The per-second set adds about 2 catches at no false alerts:** splice damage ×2, and obvious unbalance where heldout is in training.
  - **Open:** the per-second set on top of model-search's verified PCA + line system (9.7), which should give about 7.7.
- **System search, experiment 3: heat relative to the camera background** (`relheat.json`). Mean missed at 0 false alerts
  in the worst rotation, strict / steady:

  | | As today | Camera minus background | Camera + IMU minus background |
  |---|---|---|---|
  | Group PCA | 13.7 / 16.7 | 14.3 / 16.7 | **12.3 / 14.3** |
  | Group Gaussian | 14.7 / 15.0 | 17.3 / 17.7 | 12.0 / 13.7 |

  - **The camera alone gains nothing:** its cells, spots and hotspot are already measured above background.
  - **Making the IMU temperature and its heat trends relative to the camera background helps a little.** For PCA it brings
    back subtle idler bearing, overload, belt slip and electrical, and loses none. The Gaussian gains 9 in one rotation and loses 4 elsewhere.
  - **Caveat (N6):** this partly cancels a simulator artefact, since weather warms only the camera here. On a real device
    it's a sensible stand-in for a room sensor, but the size of the gain here is unreliable.
- **System search, experiment 2: the per-second path** (with the fixed safety checks, from each rotation's training only;
  not yet checked).
  - **The universal limits** (knocking, jolts, over limit, too hot, heating fast) give 0 false alerts but catch nothing.
  - **The outside-normal checks at 0.5** give 0 false alerts, and catch belt rub, idler seizure, misalignment and
    **unbalance** (obvious) in every rotation. So the device's own check covers the unbalance dilution.
    At 0.75, hotter_than_normal gives 13 false alerts in the warm heldout week.
  - **A new spike-rate detector:** seconds of `snd_kurt_hf` above the training p99.9, counted over 10–60 min, alarming
    above 1.5–2× the training maximum. It gives 0 false alerts, and catches **both splice damages** and obvious looseness
    in every rotation, at every window and multiplier tried. A `vib_kurtosis` rate adds looseness ×2 and obvious motor bearing.
  - **Jams stay missed:** jam-obvious by everything at ≤ 1 false alert a week, jam-subtle in only some rotations.
  - **Combined with the model** (mean missed at 0 false alerts): PCA 13.7 → 8.7; PCA + persistence (steady) 12.0 → 8.0;
    strict 8.7 → 6.3. These pick per-second checks for having 0 false alerts on the judged data, so they're optimistic.
    Experiment 6 fixes the per-second set beforehand: device checks at 0.5, plus the `snd_kurt_hf` and `vib_kurtosis`
    rates at p99.9 / 30 min / 2× the training maximum.
- **Model-only result in the rotation** ([MODEL_SEARCH.md](MODEL_SEARCH.md), rewritten under the final rules):
  - **Four are tied at 0 false alerts in all three rotations** (paired bootstrap intervals include 0):
    1. group-balanced residual PCA (var 0.95, smoothing 10, sensitivity 0.625): 13.7 missed
    2. Gaussian with group-mean scoring: 15.0, the widest 0-alert plateau
    3. Gaussian on impulsive maxima: 15.0
    4. today's device scoring keeping 99 % of variance with no 40-pattern cap: 15.0. That's two trainer constants and no
       format change, so the cheapest real improvement.
    
    All four beat today's device PCA (21.0 missed) by about 7 faults (CI −11 to −4).
  - **At 0 false alerts, 2–5 obvious faults are still missed per rotation.** Catching 14 of 15 obvious faults needs about 6 false alerts a running week.
  - **Corrections:** in the rotation, T2 doesn't hurt at 0 false alerts (21.0 vs 22.0), only at about 2 a week. The 0.95 "peak" is a slope, not a spike.
  - **The training weeks matter more than the model:** rotations that learn on the warmer heldout week (N6) catch 13–14, against 22.
  - **The final 3-week model's line lands below every rotation's**, so it would be more sensitive than anything tested. A rule that stays in the tested range is needed.
- **First rotation results (model only, cross-week line; superseded by the above):** at 0 false alerts in
  the worst of the three rotations, the best mean misses (of 30) were:
  - PCA (device-style, float), median inputs: 13.7 missed, lateness 0.58
  - Gaussian, median: 14.7, lateness 0.57
  - PCA (device-style), median + max: 15.0
  - Gaussian, impulsive max: 15.0
  
  Misses vary a lot by rotation. Only the LINE comes from each rotation's training weeks. One (smoothing, sensitivity)
  is shared by all three rotations and picked on the 30 fault runs plus all three watched weeks, so the zero false
  alerts are selected on the judged data and are optimistic (the checker's correction).
- **The first combined (model + early warning) join, 14:08, is invalid.** It ran while early-warning was regenerating
  its files, so it loaded only the no-model CUSUM. It will be re-run on the regenerated, checked files.
- **Early warning: being re-checked (bug found 13 Nov afternoon).** A run's last early-warning block held its verdict
  into the next run in the concatenated arrays. That inflated the early warning's false alerts and hid its catches, in the main grid
  and in the rotation flags. Model-alone, safety and the sound-check results are unaffected. Being regenerated.
  - **Fixed and verified by the checker (14:09). The fix reverses the conclusion: early warning DOES help.**
    - Gaussian (tail-15 line) at sensitivity 0.1, plus the "line" early warning (the score's 60-min block median above
      0.4× the line, held 6 h), catches 22 of 30 with 0 false alerts, against 8 for the model alone. 14 of the 22 are
      caught only by the early warning.
    - Device PCA + line goes from 4 to 19 of 30.
    - **Caveats:** on the single split, it doesn't carry over (7.65 and 3.82 false alerts a week when learning on one
      week). It's in effect a 6-hour persistence test on the model's own score, so the rotation join is the real test.
    - The old headline "adds no catches" was wrong. `EARLY_WARNING_SEARCH.md` is rewritten from the fixed files.
    - **Frontier:** early warning adds 9–14 catches at every level up to 2 false alerts a week (0: 22 vs 8; 1: 22 vs 9;
      2: 24 vs 15).
    - **Misses at the winner:** cooling_blocked-subtle, jam ×2, lubrication_loss ×2, motor_bearing-subtle, splice ×2.
    - **Trust:** best in 39 % of resamples, and 2 slow-subtle catches at 0.3–1.6 % wear are probably chance.
    - **A good sign:** on model-search's own fits (cross-week line), every "line" setting stays OFF for 100 % of each
      rotation's watched healthy week.
    - **The device's default per-group CUSUM and EWMA never reach ≤ 2 false alerts a week** (the default gives 20.6).
      With a cross-week (out-of-sample) calibration, a CUSUM on the score alone works: 15/30 (device PCA) and 18/30
      (Gaussian) at 0 false alerts. Recommendation: replace the default with "line", or a cross-week-calibrated score CUSUM.
    - **The sound check:** a 120 s hold also gets forklifts to 0, and stays at 0 up to sensitivity 0.9. The whole
      device (winner + fixed checks at 0.5): 0 false alerts, 22/30.
  - The default early warning (CUSUM) learns its normal in-sample, so it sits about +4 spreads above normal in healthy test time
    (0.24 with out-of-sample calibration). That finding is unaffected by the bug. Recommendation so far: don't ship the default early warning as it is.
  - **Lesson:** results built from concatenated runs need a check that nothing carries across a run boundary.
- **The no-model max |z| baseline was broken** (its line sat above the clipped score) and is now fixed.

## Files
- [MODEL_SEARCH.md](MODEL_SEARCH.md) and [model_search.py](model_search.py): the model search (approaches × inputs × smoothing × sensitivity × settings; rotation; group scoring; fault naming; final model; combined ranking; long persistence). Final under the agreed rules.
- [EARLY_WARNING_SEARCH.md](EARLY_WARNING_SEARCH.md) and [early_warning_search.py](early_warning_search.py): every early-warning method and setting on every model, plus no-model CUSUM, and the sound-check variants.
- [SEARCH_CHECK.md](SEARCH_CHECK.md): the checker's verdict. Both searches are correct after 12 fixes each; two of those
  changed the answer (the early-warning leak, and model-search's first ranking). Every headline number recomputed exactly.
  There's no leakage into any line, and selection is on the evaluation data, as stated, so the numbers are optimistic.
- Results data: `data/fake/model-search/`, `data/fake/early-warning-search/` (not committed).

## Open
- **The user gave nearly full autonomy (13 Nov, about 14:30):** use any model or combination, new or changed, to get the
  fewest false positives, the fewest misses, and still early. Use the synthetic data AND the real SSCC conveyor dataset.
  - **SSCC:** to be downloaded (about 200 GB, 21 split 7-Zip parts; `training/record/datasets/sscc_download.sh`) into `data/real/sscc/`, with a loader in
    `training/record/datasets/sscc.py` that converts it to what our device sees.
  - **Blocked, 13 Nov about 15:10:** Google Drive answers "Quota exceeded" for every part, meaning too many downloads
    recently ("may take up to 24 hours"). The downloader runs detached, retries every up to 30 min, and resumes parts
    (log: `data/real/sscc/download.log`). There's no other mirror. The ways round: the user copies the folder into
    their own Drive (needs about 210 GB of Drive space), or asks the authors.
  - **About 15:20, the user is downloading the parts themselves,** so the automatic downloader is stopped.
  - `training/record/datasets/sscc.py` and `SSCC.md` are written and tested on fake clips, ready for when the parts arrive.
  - It's used as an independent real-data check of the final system: learn on its normal clips, test on its faults and on
    held-out normal clips. It isn't used to tune.
- **Audio embeddings (BEATs), 13 Nov about 16:00, the user's request:** the `beats-test` session compares BEATs
  (plus one small model, for the board) with our device sound features on the real DCASE 2024 fan clips. It uses the same
  kNN and PCA detectors and reports AUROC, pAUC, catches at zero false alarms, and cost on the UNO Q. It reuses the same
  code on SSCC later. Extended at the user's request: vibration through the embeddings (on SSCC), timing on the UNO Q
  itself if connected, shorter clips (2 and 5 s), and **all the DCASE 2024 dev machine types** (bearing, fan, gearbox,
  slider, valve; ToyCar and ToyTrain reported separately), to test a range of equipment. Files: `training/record/datasets/audio_embed.py` and `AUDIO_EMBED.md`; resume note in
  `data/real/dcase2024-fan/embed/RESUME.md`.
- **Building the best whole system** (the user, 13 Nov afternoon): the fewest false positives, the fewest misses, still early.
  The sound-fix session runs `system_search.py` on top of the model search, judged in the rotation, and the checker reviews it.
  - **Long persistence** on the score, which the early-warning result suggests.
  - **A per-second path** for jam and splice damage: the fixed safety checks, plus a spike detector if needed.
  - **Heat relative to the camera's background,** standing in for the missing room sensor.
  - **Why obvious unbalance is missed** in two rotations.
  - **Combinations** of group-scored PCA, Gaussian and autoencoder.
  - **A final system:** model(s) + persistence + the fixed safety checks.
  - Then, if it holds up, a fresh-seed confirmation set to measure how much of the gain is fitted to these 30 runs.
- The model search is finished (MODEL_SEARCH.md). Waiting on the checker's verdict.
- Team decisions on `effente/` changes:
  - the sound-check fix
  - residual-only PCA scoring
  - group-balanced scoring (model file format)
  - a cross-week alarm line
  - possibly a Gaussian model on the device (a mean vector and a precision matrix; small to port)
- Track 5: `platform/CONTRACT.md` timing for outside-normal problems.
- The next data generation: fix N5, N6 and S5. Consider fault runs with fresh seeds as a confirmation set, since the winner's settings were picked on these 30 runs.
- **Real data:**
  - Real recordings from the board are the only way to measure false alarms over a week.
  - Public sets are already in `data/real/` (Ottawa, used; NASA IMS, DCASE fans, SKAB, MetroPT-3, `uaq-thermal`, unused).
  - **SSCC** (arXiv 2603.07130, [yucongzh.github.io/SSCC-Dataset](https://yucongzh.github.io/SSCC-Dataset/), CC BY 4.0) is the closest public match: a real chain conveyor with vibration and audio, factory noise, and dry/loose/screw-drop/lean faults. It's about 200 GB in split 7-Zip parts on Google Drive. Not downloaded.
  - MaFaulDa would test naming across fault types.
