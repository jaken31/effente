# Conveyor evaluation: progress notes (track 2, 13 Nov 2026)

Where the search for the best model stands, what was decided, and what's still open. Everything here is on
**synthetic** data (generation 3), so the numbers are a guide, not a promise. Details live in the linked files.

## Resuming (written 13 Nov afternoon, before a usage pause)
Each side session writes its state and next steps to a resume file. Background jobs (downloads, grids) keep running while
the sessions are paused.

| Session | Doing | Resume file |
|---|---|---|
| model-search | finishing `MODEL_SEARCH.md`: the combined section, the group autoencoder, the final-model line rule | `data/fake/resume/model-search.md` |
| sound-fix | the system search: experiment 1 done; 2–6 next (per-second path, heat vs background, unbalance misses, combinations, final system), then SSCC | `data/fake/resume/system-search.md` |
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
- **Device PCA's extra pattern-distance term (T2) costs catches at every level.** At 0 false alerts, residual-only catches 13 against 11 with T2; at 2 false alerts, 24 against 14.
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
- **System search, experiment 1: long persistence** (sound-fix, `system_search.py`, not yet checked). The score's block median over
  30–360 min against a fraction of the line, held 1–6 blocks, ORed with the model alarm. Mean missed at 0 false alerts in
  the worst rotation (heldout / w2 / w1):

  | | Model alone | Model + persistence, steady setting | Model + persistence, best setting |
  |---|---|---|---|
  | Group PCA | 13.7 | **12.0** (9/13/14): block 120 min > 0.25× line, held 3 | 8.7 (6/9/11), a sharp peak, likely overfit |
  | Group Gaussian | 14.7 | 15.0 | 13.7 |

  - **Persistence helps PCA by about 4–5 faults, and the Gaussian hardly at all.** So with persistence, PCA leads.
  - The early-warning search's single-split "8 → 22" for the Gaussian does not carry into the rotation.
- **System search, experiment 4: obvious unbalance missed in two rotations. Not a bug; it's dilution.**
  - Its vibration group is 27–30× its training level (velocity 8.3 mm/s), but the mean over 4 groups, and over about 67
    inputs within the group, dilutes it to a score of about 9–10.
  - When heldout is in training, the line is about 23, because the weeks differ (heldout is 10 % busier and warmer)
    more than this fault does, as a mean-square sees it.
  - **Next:** an aggregation that doesn't dilute (top-k or max within groups). The fixed safety check
    vibration_above_normal should catch this fault directly; experiment 2 will confirm.
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
  
  Misses vary a lot by rotation; the winner's line and settings were picked per rotation from training only. The group-scoring variants are still to report.
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
- [MODEL_SEARCH.md](MODEL_SEARCH.md) and [model_search.py](model_search.py): the model search (approaches × inputs × smoothing × sensitivity × settings; rotation; group scoring; fault naming; final model; combined ranking). Still being rewritten under the final rules.
- [EARLY_WARNING_SEARCH.md](EARLY_WARNING_SEARCH.md) and [early_warning_search.py](early_warning_search.py): every early-warning method and setting on every model, plus no-model CUSUM, and the sound-check variants.
- `SEARCH_CHECK.md` (to come): the independent checker's verdict on both searches.
- Results data: `data/fake/model-search/`, `data/fake/early-warning-search/` (not committed).

## Open
- **The user gave nearly full autonomy (13 Nov, about 14:30):** use any model or combination, new or changed, to get the
  fewest false positives, the fewest misses, and still early. Use the synthetic data AND the real SSCC conveyor dataset.
  - **SSCC:** to be downloaded (about 200 GB, 21 split 7-Zip parts; `training/record/datasets/sscc_download.sh`) into `data/real/sscc/`, with a loader in
    `training/record/datasets/sscc.py` that converts it to what our device sees.
  - It's used as an independent real-data check of the final system: learn on its normal clips, test on its faults and on
    held-out normal clips. It isn't used to tune.
- **Building the best whole system** (the user, 13 Nov afternoon): the fewest false positives, the fewest misses, still early.
  The sound-fix session runs `system_search.py` on top of the model search, judged in the rotation, and the checker reviews it.
  - **Long persistence** on the score, which the early-warning result suggests.
  - **A per-second path** for jam and splice damage: the fixed safety checks, plus a spike detector if needed.
  - **Heat relative to the camera's background,** standing in for the missing room sensor.
  - **Why obvious unbalance is missed** in two rotations.
  - **Combinations** of group-scored PCA, Gaussian and autoencoder.
  - **A final system:** model(s) + persistence + the fixed safety checks.
  - Then, if it holds up, a fresh-seed confirmation set to measure how much of the gain is fitted to these 30 runs.
- Finish the model search: the rotation, the group-scoring variants, Gaussian fault naming, the final three-week model, the combined join on the same fits, and the write-up. Then the checker's verdict.
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
