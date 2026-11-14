# System search: the best whole detection system on the synthetic conveyor (generation 3)

Script: [system_search.py](system_search.py) (eval only, uncommitted). It imports `model_search.py` from the main checkout
and reuses its rotation, crossfit line, `Prepared` and `judge_flags`, and its cached scores (read only).
Results: `data/fake/system-search/` in the main checkout (`persist.json`, `safety.json`, `unbalance.json`, `topk.json`,
`relheat.json`, `combine.json`, `combine_relheat.json`, `final.json`, `final_relheat.json`, `final2.json`).
**Synthetic data only, one run per fault, and every choice was made on the same 30 fault runs.** Nothing here has been
checked on a real machine. The SSCC real-data check is still to come, and it can test only part of this (see the end).

All numbers are mean missed of 30 over the three rotations (learn w1+w2 / w1+heldout / w2+heldout), with 0 false alerts
per running week in every rotation unless said, under the agreed rules (PROGRESS.md).

## Recommendation

| | Missed (heldout / w2 / w1) | Without the jam rules | Worst one-step neighbour (false alerts / week) | What the device needs |
|---|---|---|---|---|
| **A. group PCA + line early warning + per-second + jam rules** (s 0.625; s 0.6 also 5.67) | **5.67 (3 / 7 / 7)** | 7.67 | **7.79** (frac 0.2); hold 5: 1.11; s 0.65: 0.56; frac 0.4, hold 7, block 60, s 0.6: 0 | 1. group field per input in the model file (format change, all five agree)<br>2. `early_warning.Line` (exists)<br>3. this branch's safety fix<br>4. two kurtosis-rate checks (new)<br>5. jam rules (new) |
| B. PCA rel \| Gauss rel + persistence + per-second + jam rules | 6.33 (4 / 7 / 8) | 8.33 | **2.21** (frac 0.2); every other neighbour 0 | A's items 1, 3–5, plus a Gaussian port and relative-heat inputs |
| C. device PCA 0.99 no cap + line early warning + per-second + jam rules | 7.33 (5 / 7 / 10) | 9.33 | **6.12** (frac 0.2); s 0.675: 1.11; hold 5: 0.56 | no format change; two trainer constants (team agreement), plus A's items 3–5 |

**All three sit on an edge somewhere.** Loosening the persistence threshold (frac 0.3 → 0.2) false-alerts in every one.

- **A** misses the fewest but is the edgier one: three of its neighbours false-alert.
- **B** is the steadiest (one bad neighbour, the smallest), but misses 0.7 more and needs a Gaussian port. Its
  relative-heat gain partly rides on a simulator artefact (N6).
- **C** needs no model-file format change.

My pick, stated with that in mind:
- **A, with the persistence settings locked** (frac and hold must not be loosened; tightening is safe: frac 0.4, hold 7
  and block 60 all keep 0 false alerts, at 8.33 / 6.67 / 7.33 missed).
- **B if the team prefers the steadier setting space and accepts the Gaussian port.**
- **The jam rules are simulator-specific (below).** Without them, A misses 7.67, B 8.33 and C 9.33.

How each system grows, part by part (`final2.json`):

| | Models alone | + per-second set | + per-second + jam rules |
|---|---|---|---|
| A. group PCA + line EW (model-search's verified system, s 0.625) | 9.67 (7 / 11 / 11) | 7.67 (5 / 9 / 9) | 5.67 (3 / 7 / 7) |
| B. PCA rel \| Gauss rel + persistence (block 60 > 0.3× held 4) | 10.33 (8 / 11 / 12) | 8.33 (6 / 9 / 10) | 6.33 (4 / 7 / 8) |
| C. device PCA 0.99 no cap + line EW | 11.33 (9 / 11 / 14) | 9.33 (7 / 9 / 12) | 7.33 (5 / 7 / 10) |

The checker reproduced A (rot_heldout: 3 missed) and B (6 missed) end to end with independent code.

**What A still misses:**
- Every rotation: subtle idler bearing, lubrication loss and motor bearing.
- With heldout in training, also: subtle cooling blocked, gear wear and overload, and obvious lubrication loss.
- How early (before the jam rules): sudden faults 8–19 min median; slow-fault wear at the first alert about 50–75 %.

## The parts

1. **The model:** device-style PCA, residual only, 95 % variance kept, scored per sensor group (vibration, sound, heat,
   other) against each group's own training level, then averaged equally. The alarm is the 10-minute median over the
   crossfit line × margin(sensitivity).
2. **Persistence ("line" early warning):** the median of each 30-running-minute block over 0.3× the 10-minute line,
   held 6 blocks (3 hours). It catches slow drifts that never cross the alarm line.
3. **The device's safety checks** at sensitivity 0.5, with this branch's fix (outside normal judged on 60 s medians).
   - `vibration_above_normal` catches obvious unbalance and misalignment in every rotation.
   - The universal limits catch nothing here, and raise no false alerts.
4. **Two impulsiveness rates:** the seconds with `snd_kurt_hf`, or with `vib_kurtosis`, over the training p99.9 in the
   last 30 minutes, above 2× the most any training window had.
   - They catch both splice-damage runs and looseness.
   - The checker recomputed the `snd_kurt_hf` rate from the raw hour files: identical.
   - This is a sound feature, so the stop speed-up artefact (S5) doesn't touch it.
5. **Jam rules** (`final2.json`), causal:
   - **Short stop:** the belt stops and restarts within 30 minutes. The alert comes at the restart.
   - **Stall:** belt speed under 0.9× its 5-minute running median for ≥ 3 s, with the belt still running 10 minutes
     later. The alert waits for that confirmation.
   - Neither happens in any training week. Short stop catches jam-obvious in every rotation (at 10 / 30 / 60 minutes);
     stall catches jam-subtle (at ≥ 3 and ≥ 4 s; ≥ 2 s gives 1 false alert per rotation).
   - **Strong caveats; on real data both rules are unsupported:**
     - **Written for the simulated fault.** Both rules were designed after seeing jam missed, to match the
       simulator's jam (a 3-minute stop; 5-second stalls). That's selection on the fault runs at the level of the
       method, not only the setting.
     - **"Short stop" has 0 false alerts only because the simulator has no unscheduled short stops** (no healthy stop
       shorter than 60 minutes in any week). Real operators stop and restart for material, people and blockages, and
       this rule would alert on every one. On its own it is not a jam detector.
     - **"Stall" relies on the speed tracker,** which is unreliable (S3: misalignment doubles its reading).
       - jam-subtle's 5 stalls are real: speed 29.3 → 21.8 Hz, sound +2.5 dB, vibration down.
       - Its catches of misalignment-subtle (93 events) and looseness-obvious (23) are tracker jumps, not stalls. In
         misalignment the tracker flips between 29.4 and 58.8 Hz.
     - **Restarts:** soft-start restarts don't trigger it here only because the running flag is false during the ramp
       (and readings under 1 Hz are ignored). A device whose running flag switches on earlier would see a "stall" at
       every restart.
     - A tighter stall (the speed dip AND the motor's sound rising AND belt vibration falling together) would exclude
       the tracker jumps. That is untested.

Parts 3–5 were chosen from exp 2's results (and the jam check) on the same data, before the final comparison. That is a
milder selection than picking columns by having 0 false alerts, but it is still a selection on these runs.

## What each experiment found

1. **Long persistence** (`persist.json`):
   - PCA alone 13.67. With persistence on the 1-minute line, strict 8.67 (6 / 9 / 11, on an edge), plateau 12.0.
   - Model-search's line rule on the 10-minute line gives 9.67, verified.
   - The Gaussian gains nothing.
   - The early warning's single-split "8 → 22" does not carry into the rotation.
2. **The per-second path** (`safety.json`; uses this branch's unmerged `effente/safety.py`):
   - Outside normal at 0.5: 0 false alerts; catches four obvious faults.
   - Outside normal at 0.75: 13 false alerts in rot_heldout (hotter_than_normal).
   - Universal limits: 0 false alerts, no catches.
   - The `snd_kurt_hf` rate catches both splice-damage runs and looseness-obvious. It holds over 10 / 30 / 60-minute
     windows and 1.5 / 2× multipliers.
   - Nothing per second catches jam-obvious, apart from the stop sign (5).
3. **Heat relative to the camera background** (`relheat.json`):
   - Camera only: no gain (14.33), since the cells, spots and hotspot are already above the background.
   - With the IMU and its heat history relative too: PCA 12.33, Gaussian 12.0.
4. **Why unbalance-obvious was missed** (`unbalance.json`): not a bug.
   - The vibration group sits at 27–30× its training level, diluted ÷4 by the group mean.
   - With heldout in training, the crossfit line doubles (13 → 23): the busier held-out week scores 34–42× in vibration
     out of sample.
   - Top-k or max inside each group (`topk.json`) doesn't help (13.67–21.7).
   - The fix in the system is `vibration_above_normal` at 0.5.
5. **Combinations** (`combine.json`, `combine_relheat.json`):
   - **The system doesn't need three models.** The autoencoder adds nothing.
   - The Gaussian adds at most about 1 fault, within noise.
   - The max of normalised scores is no better than the OR.
6. **The final systems** (`final.json`, `final_relheat.json`, `final2.json`): above.

## Can it be made better? (items 7–9, as deltas on system A at sensitivity 0.6: 5.67)

7. **Jams:** done. These are the jam rules above, already in A: 7.67 without them, 5.67 with them (simulator-specific).
8. **Faint slow faults: a slow CUSUM per physics subgroup** (`faint.json`, `faint_or.json`).
   - **Setup:** on group PCA's per-input residuals, each over its own training spread. The subgroups:
     - high-frequency vibration (bands 10–19, envelope, kurtosis, harmonics)
     - low-frequency vibration
     - high-frequency sound (bands 16–31, `snd_kurt_hf`, centroid, envelope)
     - low-frequency sound and level
     - the IMU's heat. The camera is left out: each fault run is effectively another camera (N2).
   - **Calibration:** block medians of 60 or 120 running minutes, standardised and limited cross-week. Each training
     week is scored by a model that never saw it, and the limit is factor × the highest CUSUM any training week reached.
   - **"Any subgroup":** 4–24 false alerts in the heldout and w2 rotations. Low-frequency sound and IMU heat follow
     the held-out week's load and warmth. Not usable.
   - **High-frequency vibration + high-frequency sound only** (60-min blocks, k 0.5, 2× the training maximum): **4.33**
     missed (2 / 4 / 7), 0 false alerts in every rotation, slow wear caught at 32–52 % instead of about 50–75 %. It adds
     subtle idler bearing, motor bearing and gear wear in some rotations, plus obvious lubrication loss.
   - **But it is not a plateau.**
     - 1.5× gives 2–3 false alerts.
     - 2.5× gains only 0.33, and 3× gains nothing.
     - The two subgroups were also kept after seeing the others false-alert.
   - **Verdict: likely fitting these 30 runs. Not added to the recommended system.** Worth one test on a fresh-seed
     confirmation set.
9. **Load conditioning:** a line per load stratum (2 or 3 strata, by belt speed, which falls under load through slip;
   each stratum's line from its own crossfit minutes).
   - The stratum lines come out almost equal (rot_w2: 22.4 / 15.7 / 22.6 against 22.6 unstratified). The held-out
     week's inflation is not a belt-speed-load effect.
   - Result: 5.67 at 0 false alerts (no gain). At sensitivities where it gains 1 fault, it costs 1–5 false alerts.
     **Stopped.**

10. **Per-group kNN, the SSCC paper's method on our groups** (`knn.json`).
    - **Setup:** each sensor group scored by the mean distance to its k nearest training minutes (k 5 / 10 / 20,
      memory 5,000 per group). Each group's score becomes its ECDF quantile against cross-week out-of-sample training
      scores (as −log exceedance), then the groups are averaged or maxed.
    - **Alone:** 18.3–25.7 missed at its best (0–1 false alerts; plateau 20–24), against group PCA's 13.67.
    - **With the per-second set and jam rules:** 13–17.
    - **With the line early warning:** 6–46 false alerts a week. The quantile score of an unseen week sits high for
      hours, so a 0.3× persistence line is far too low for it.
    - **OR'd with system A:** 5.33 at best (k 5, mean, s 0.525; 0 false alerts), and one step more sensitive gives 1
      false alert. k 10 / 20 add nothing (5.67).
    - **Not adopted.** On minute medians from one fixed machine, the linear residual model (PCA) carries the useful
      structure better than distances. The kNN is kept for the like-for-like SSCC comparison.

**What is left** (A, every rotation): subtle idler bearing, lubrication loss and motor bearing. With heldout in
training, also: subtle cooling blocked, gear wear and overload, and obvious lubrication loss. DATA_CHECK calls the
subtle bearing and lubrication runs "nearly invisible at the drive end". Catching them would most likely need another
sensor (a kHz accelerometer near the idlers, or motor current), not another model.

## Trade-off: what a small false-alert budget buys (`tradeoff.json`)

The user relaxed the goal: as few false alerts as possible, not necessarily 0.
- **Search:** the full grid for each system (smoothing × shared sensitivity, or the alarm off; persistence block × frac ×
  hold, or none), every setting OR'ed with the per-second set, with and without the jam rules.
- **Budget:** the worst rotation's false alerts per running week. 1/week is about 4 a month.
- **Strict pick:** the fewest missed at that budget.
- **Plateau pick:** the fewest missed where the setting AND all its one-step neighbours stay within the budget
  (sensitivity ±0.025; frac, hold and block one grid step). Plateau picks are the ones to trust.

**Plateau picks**, mean missed of 30 (heldout / w2 / w1). In brackets: the setting's own worst false alerts / week.

| Budget (worst rotation, per week) | 0 | 0.25 | 0.5 | 1 | 2 | 3 | 5 |
|---|---|---|---|---|---|---|---|
| **B with jam rules** | 6.33 (0) | 6.33 | 6.33 | 6.0 (0.56) | **5.0 (0)** | 4.33 (2.2) | 4.0 (2.8) |
| B without jam rules | 8.33 (0) | 8.33 | 8.33 | 8.0 (0.56) | 7.0 (0) | 6.0 (2.2) | 5.67 (2.8) |
| **A with jam rules** | 9.0 (0) | 9.0 | 9.0 | **7.0 (0)** | 5.33 (1.7) | 4.33 (1.7) | 3.67 (2.2) |
| A without jam rules | 11.0 (0) | 11.0 | 11.0 | 9.0 (0) | 7.33 (1.7) | 6.33 (1.7) | 5.67 (2.2) |
| C with jam rules | 9.33 (0) | 9.33 | 9.33 | 9.33 | 7.0 (1.1) | 6.67 (1.7) | 4.0 (3.3) |
| C without jam rules | 11.33 (0) | 11.33 | 11.33 | 11.33 | 9.0 (1.1) | 8.67 (1.7) | 6.0 (3.3) |

**Strict picks** (on edges; their neighbours break the budget):

| | 0 | 0.25 | 0.5 | 1 | 2 | 3 | 5 |
|---|---|---|---|---|---|---|---|
| B with jam rules | 4.67 | 4.67 | 4.67 | 4.67 | 3.0 | 3.0 | 0.0 |
| A with jam rules | 5.33 | 5.33 | 5.33 | 5.33 | 4.0 | 3.67 | 0.0 |
| C with jam rules | 6.0 | 6.0 | 6.0 | 5.67 | 5.33 | 4.33 | 0.0 |

"0 missed at 5 a week" is a setting that alerts often enough to overlap every fault window. It is not a detector.

**What each step buys** (plateau picks, faults newly caught in some rotation):
- **B at 2 / week** (setting itself 0, neighbours ≤ 1.7): subtle idler bearing and subtle overload.
  - Slow wear at the first alert drops from 56–69 % to 43–59 %.
  - Sudden faults: 9–15 min median.
- **A at 1 / week** (setting 0, neighbours ≤ 0.56): subtle gear wear, at a 360-minute persistence block.
- **A at 2 / week:** catches more, but moves to smoothing 1, where sudden faults take 15–85 min median instead of
  11–16. That's a worse system for the same faults.
- **C at 2 / week:** subtle gear wear, idler bearing and overload.
- **C at 5 / week:** obvious and subtle lubrication loss.
- **0.25 and 0.5 a week buy nothing on the plateau:** no setting has its whole neighbourhood between 0 and 0.5.

**What the false alerts are** (plateau picks):
- **Mostly not nuisances.**
  - B at 1 / week: all 3 are jam-subtle at hour 6.0, the first judged minute. That run is a Monday-start run that
    begins with heat carried over from the simulator (N3), so the persistence is already on when judging starts.
  - Many of the others also start at hour 6.0 of a fault run, before onset: misalignment-subtle, idler_seized-subtle.
- **The rest are model alarms with nothing particular going on:**
  - before onset in unbalance-subtle (hour 15) and misalignment-obvious (hour 11.6)
  - one in each healthy week (normal-w1 hour 54, normal-w2 hour 93)
  - in the held-out week, two with a person in front of the camera (hours 9.7 and 83)
- **Forklifts, slams and dropped objects cause none.** The per-second set adds none.
- **About half of these come from simulator quirks:** the start-of-run carry-over (N3), and each fault run being
  another camera (N2). On a real device's continuous recording, the start-of-run ones wouldn't recur.

**Recommended operating point, if a few false alerts a month are acceptable:**
- **B, smoothing 10, sensitivity 0.65, persistence 30-minute blocks > 0.25× the line, held 6.**
  - With the jam rules: **5.0 missed (2 / 6 / 7)**. Without: 7.0.
  - 0 false alerts at the setting itself in every rotation; no one-step neighbour exceeds 1.7 a week.
  - It beats the 0-budget plateau pick (6.33) by 1.33, and its own worst case on these data is 0.
- **PCA only (no Gaussian port):** A, smoothing 10, sensitivity 0.625, persistence 360-minute blocks > 0.3×, held 3.
  - With the jam rules: **7.0 (3 / 9 / 9)**. Without: 9.0.
  - 0 at the setting; neighbours ≤ 0.56 a week.
- **Expect worse on fresh data:** both were picked on these 30 runs.
- **The jam rules carry the caveats above.**

## Read this before using it

- **Selection on evaluation data.** Tens of thousands of settings were judged on the same 30 fault runs and healthy weeks.
  - Strict picks sit on edges; the neighbour columns above show how far.
  - The exp 2 "every 0-false-alert column" numbers chose their columns by having 0 on the judged data. They are an
    optimistic upper bound and are not used.
  - Expect fresh runs to do worse. A fresh-seed confirmation set is the honest test.
- **S5 (the stop speed-up artefact):**
  - `vib_peak_g`, `vib_jerk` and `gyr_peak` carry a speed-up at every stop. Jam-obvious's jolt is 4.4 g, about 3× realistic.
  - The final per-second set uses none of these.
  - The jam catches come from the stop and speed-dip signs, not the jolt.
- **S3:** the speed tracker doubles under misalignment, which the stall sign partly picks up.
- **N6 (weather reaches only the camera; heldout is +4 °C by design):** relative heat (system B) partly cancels this
  artefact.
- **Unmerged shared code:** every per-second result uses this branch's `effente/safety.py` (outside normal on 60 s
  medians) and `trainer.py` (`period_s`). Both need team agreement.
- **The checker independently verified:**
  - the strict persistence pick (8.67)
  - the `snd_kurt_hf` column
  - a whole earlier headline system on rot_heldout (PCA + persistence + the real Safety class + the rates)

## SSCC (real chain conveyor): what it can and can't test

SSCC clips are 5 s: 5 one-second rows, with no minute medians, history, camera, gyro or IMU temperature. So:
- **What it can't run:** the systems above as built. Minute smoothing, 30-minute to 3-hour persistence, the cross-week
  line, the heat group and the jam rules all need longer recordings.
- **What it can test:**
  - per-window scoring by the group-PCA model on 1 s rows, with no heat group
  - the per-second impulsiveness rates in a short form
  - relative checks
- **What doesn't transfer:** the units are uncalibrated (dB relative to the recorder's full scale; vibration unit and
  axes unverified), so absolute limits don't apply.
- **How it will be judged:**
  - Learning uses whole normal conditions only (load × speed × noise), holding whole conditions out for false positives.
  - Both sides cover the fault speeds and the same noise conditions.
  - The line is cross-condition, out of sample.
  - Anything re-picked on SSCC faults is labelled optimistic.
