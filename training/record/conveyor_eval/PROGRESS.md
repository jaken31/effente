# Conveyor evaluation: progress notes (track 2, 13 Nov 2026)

Where the search for the best model stands, what was decided, and what's still open. Everything here is on
**synthetic** data (generation 3), so the numbers are a guide, not a promise. Details live in the linked files.

## Paused (13 Nov, about 17:15)
The user paused every search to record real data with the device itself: "pause everythig well do that". The system
search's trade-off section, the audio-embedding test (BEATs, EfficientAT, CED-tiny) and the SSCC check are unfinished.
Each session's resume note says where it stopped. The plan now is system C on the board, a healthy recording on a real
machine to measure false alarms, then staged faults.

**About 17:25: the user asked for system C to be built and pushed** ("okay fix C and push it"). The sound-fix session is
implementing it in the device's real code on a new branch, `track2/system-c`: PCA at 99 % with no cap, a cross-fitted
alarm line, the "line" early warning as the default, the forklift fix, and the two spike-rate detectors. It then verifies
with the device's real classes on the synthetic data. The other searches stay paused.

**About 18:15, system C on `track2/system-c` (pushed up to `29b714f`; all device tests pass, one library test pending a small fix):**
- **The trainer:** for recordings with 1-minute periods (a day or more), PCA keeps 99 % with no cap, smoothing is 10, the
  line is cross-fitted, and the threshold is scaled so the device's default 0.5 is C's operating point. **Shorter recordings
  (the demo path) keep today's settings:** the crossfit line missed the demo test's fault, and 200 patterns overfit short data.
- **The early warning:** the default is "line" (30-period blocks over 0.3× the crossfit line, held 6).
- **A new check, more_impacts** (the spike-rate detectors), learned after training from the recording's per-second rows;
  it needs an hour or more of running data.
- **Still to do:** verification with the device's real classes on the synthetic data.

**The board test** (the user, about 18:15): a 5-minute recording on the real UNO Q, with no thermal camera. The `board-test`
session prepares it on a branch cut from `track2/system-c`: the header goes IDLE → RECORDING, no "not enough data" text,
a short recording that trains, live data on the screen, recorded values never smoothed, plus monitor and pull scripts.

**The demo plan** (the user, 13 Nov evening): record for 5 minutes on the mini conveyor, then put a coin in the track. IMU
only: the mic is the INMP441, and the live code has no audio path yet. Risks to check in tonight's rehearsal:
- A coin gives intermittent knocks, and the short-recording model alarms on the median of the last 5 one-second scores,
  so knocks in fewer than half the seconds may be hidden.
- After only 5 minutes of normal, drift may alarm before the coin goes in.

**System C checked with the device's real classes** (`verify_system_c.py`, rot_heldout only, commit `4278984`): the whole
device misses 5 of 30 with 3 false alerts (1.7 a week: none in the held-out week, 3 before onset), against the eval's 7
missed and 0. The device's crossfit line is 9 % lower than the eval's (45.6 vs 50.1), and the cause is being looked into
before anything is changed.

**For the board test, the user also wants** the local network stream (port 8765) to carry live data in every state, and
the state named IDLE, RECORDING or DETECTING, on the screen and as an added `state` field (the contract's `mode` values stay).

**Board test code, ready at about 19:00** (branch `claude/board-test-ce7d7f`, 3 commits, not pushed; not deployed, since the
board isn't connected):
- **Shared `effente/trainer.py`:** short recordings (1 s periods) train from 100 rows, not 300; and when fewer than 100 rows
  count as running, it learns from every row, saying so in the report, instead of failing.
- **`device_app.py`:** the header says IDLE; the minimum recording is 2 minutes, not 5; messages are never drawn on the screen.
- **Tested without the board, IMU only:** idle → record 4.5 min → train → watch shows only the mode headers, and the recorded
  rows, minute summaries and raw clip equal the unsmoothed samples.
- **Tools:** `device/app/board_monitor.py`, `pull_recording.sh`, `recording_summary.py`.

**jaken31's model work (track 3), read on 13 Nov evening.** A model library is merged (PR #33: `conveyor` and
`generic_rotating` entries, PCA on accelerometer and gyro readings only, built on simulated days). Calibrating an entry to a
machine from about 5 minutes is open in two competing PRs (#34 and #42), with a validation harness (#35) built on a third branch.
- **His simulated result that matters for our demo:** after a 300 s calibration, about 0 alarms over 40 min; a model trained
  alone on the same 300 s alarmed 73–98 % of the time.
- **Limits he reports:** only about 38 % of simulated faults alarm (no sound or heat readings); fault names are often wrong; the
  refitted threshold is inert (a floor at 0.25× the entry's decides it); about 9 % alarm time over 24 h.
- **How it fits with ours:** his is for the first minutes on a machine; ours (cross-fitted line, persistence, per-second checks)
  is for days of recording. Conflict risk: PR #34 restructures `train_model`, which system C and the board test also change.
- **Tonight:** compare the board's own 5-minute model with his calibrated entries on the same recording, offline.

**The crossfit line is fragile with two halves** (sound-fix): moving the split by 50 rows moves the line between 42 and 50,
which explains the 9 % gap to the eval. It isn't the int8 weights (1 % off). Four folds keep it within 7 %. Not changed yet.

**The coin demo, read from the code** (board-test, about 19:30; not yet measured): with a 5-minute recording the coin is
likely NOT caught if it knocks once per belt lap of 3 s or longer.
- The model alarms only when 3 of 5 consecutive seconds are over the line, and a knock is in one second.
- The safety checks need sustained impacts.
- The impact counter needs an hour of learning.
- The alarm line from about 40 held-out seconds is likely far up, so the model is deaf rather than jumpy.
- **The fix being built on the board-test branch:** the impact counter learns from a short recording (a level above the
  highest kurtosis and peak seen, a 60 s window, alarm at 2–3 knock-seconds).

**Also done there:** the screen and stream say IDLE / RECORDING / TRAINING / DETECTING (an added `state` field; `mode` is
unchanged); status goes out every second; and a small page at `http://<board>:8765/view` shows the state, the readings and what is wrong.

**Correction to the coin read** (board-test, simulated on the fake board; not the real one): sparse knocks CAN be caught by
the model. A 0.4 g knock every 4 s took the level from 0.48 to 2.1 of the line within about 3 s, because the model keeps the
`slow_*` readings (a spectrum over several seconds), which stay raised between knocks.
- **The false-alarm margin is thinner than thought:** on perfectly steady simulated data the quiet level peaked at 0.79 of the
  line, and 3 % of single seconds were over it.
- **The count rule** (more than 2 seconds in 60 s over the learning maximum of `vib_kurtosis` or `vib_peak_g`) had no false
  alarm and fired 7–8 s after the coin. It stays the fallback.
- **Unknown until real data:** how hard a coin knocks, and the belt's lap time.

**Decision (the user, 13 Nov evening):** "just keep our current model, thats the one we will put on the board. i want to see
if it can find the fault in testing". So the test build keeps the model as it is: no short-recording impact counter, and no
swap to a calibrated library entry. Those are compared offline on the captured data only.

**The INMP441 can't be read on the UNO Q as shipped** (board-test, from the core, Zephyr and Arduino docs; not checked on
hardware). The STM32U585 has SAI only, the Arduino Zephyr core has no I2S or SAI library, and the usable SAI pins collide with
A4/A5, the IMU and screen's I2C bus. The quickest real sound is a USB microphone on the Linux side, through a powered USB-C hub;
that needs an audio thread in `UnoQSource` (about 1–2 hours) and adb over the network. Tonight is IMU only.

**Simulated preview of the rehearsal** (board-test; fake board, a weak 0.15 g knock every 4 s; not real data):

| On the same simulated data | Alarm while untouched | The coin |
|---|---|---|
| The board's own 5-minute model | 0 % (level up to 0.79 of the line) | 1 s of alarm in 2 minutes: effectively missed |
| jaken's `conveyor` / `generic_rotating`, calibrated | 0 % (level up to 0.26 / 0.32) | no alarm |
| The short-recording impact counter (`29d8d3b`, NOT deployed) | 0 % | on after 8 s, off 56 s after the coin came out |

- With the stronger 0.4 g knock, the board's own model did catch it. So the real test depends on how hard a coin knocks.
- jaken's calibrate command refuses under 300 s of running rows, so record about 5:30.
- A hard bump during the recording would make the counter deaf to smaller knocks.

**Spikes** (the user, 13 Nov evening): "make sure our model notices spikes, such as those in vibration if something falls off
conveyer. maybe the sensitivty helps decide that". A single-spike rule is being added to the impact counter on
`track2/short-impact-counter`: one second with a peak over F × the highest seen while learning alerts for about 30 s, with F
set by the sensitivity (2× at 0.5, 1.5× at 0.75, 3× at 0.25). The model itself is unchanged. The user said to include it on the board for tonight's
test ("Yes, include it"), so the test build is the current model plus the impact checks.

**The model itself must detect spikes** (the user: "fix the model so it can detect spikes, not just slow building thigs").
Being added to the Watcher in `effente/model_runtime.py`: a single period whose score is over a spike line alarms at once and
holds about 30 s. The spike line comes from unsmoothed scores, since the existing line is fitted to median-smoothed ones,
and it scales with sensitivity.
- **Limit:** with 1-minute periods (long recordings) the model is fed minute medians, so a 1-second spike can't reach it. There
  the per-second impact check catches it.

**The single-impact check is built** (`sudden_impact`, commit `19f2f4a` on `track2/short-impact-counter`; simulated and test
data only). One whole second, while running, with `vib_peak_g` over F × the highest peak seen while learning, and at least
0.05 g, shows KNOCKING OR JOLTS for 30 s. F is the existing vibration margin: 5× at sensitivity 0, 3× at 0.25, 2× at 0.5,
1.5× at 0.75, 1.25× at 1.
- **On the fake board at 30 mg rms** (highest normal peak 0.055 g), the line is 0.11 g at 0.5: a 0.15 g knock alerts at 0.5
  and up; 0.10 g only at about 0.75 and up; 0.5 g at every setting.
- **On a very quiet machine** (5 mg), the 0.05 g floor decides: anything over about 0.06 g alerts at 0.5.
- **Cost:** every bump over the line shows for 30 s. Real sizes of a fall, a table bump or a footstep are unknown until measured.
- **New problem names** for track 5's `platform/CONTRACT.md`: `sudden_impact` and `more_impacts`.

**The deploy build is `3b85492`** (branch `claude/board-test-ce7d7f`; not pushed, not deployed): the current model, both impact
checks, and a spike rule on the model's own score.
- **The model's spike rule** (shared `effente/model_runtime.py` and `trainer.py`): one period whose score is over a spike line
  alarms at once and holds 30 s. The spike line is 4× the one-a-week gamma tail of the UNSMOOTHED held-out scores, kept in the
  baseline as `model_spike` (no new model-file key), and it scales with the model margin. It's off for 1-minute periods.
- **Headroom on the fake board:** the highest healthy single second is 0.81–1.12 of that tail, so the line has 3.6× headroom at
  sensitivity 0.5, 1.8× at 0.75, and none at 1. No spike alarms in healthy minutes at 0.5 or 0.75.
- **A single knock:** 0.15 g and up alarms at 0.5 (borderline); 0.10 g needs 0.75.
- **Finding 1, the shared sensitivity dial:** at 0.75 the existing median rule alarms 24–58 % of the time on clean fake-board
  data after a 5-minute recording (0–0.1 % at 0.5; 99.8 % at 1). So raising the shared dial to catch smaller spikes makes the
  model alarm constantly. Spikes would need their own setting.
- **Finding 2, drift:** with vibration rising 15 % over 35 minutes, the median rule alone alarms 43–70 % of the time at 0.5. On
  the simulated conveyor, a 5-minute recording from 06:10 alarms 90 % of the next 30 minutes (0 % from 14:00). That reproduces
  jaken's 73–98 %. The likeliest failure in the rehearsal is a false alarm before the coin, not a missed spike.

**A separate spike dial** (the user agreed, 13 Nov evening): `spike_sensitivity` (0 to 1, default 0.5) scales only the
single-spike paths, i.e. the model's spike rule and `sudden_impact`. The main `sensitivity` keeps its meaning. It's an added field
in `/settings`, the settings file, `/latest` and `/view`. Being built on the deploy branch.

**More dials** (the user: "you can add dials for what you think would help to be configurable"). Chosen: optional overrides
that default to following the main sensitivity, so nothing changes unless one is set: `model_sensitivity`;
`vibration_sensitivity`, `heat_sensitivity` and `sound_sensitivity` for the outside-normal checks; `early_warning_sensitivity`
and an early-warning on/off switch; plus `spike_sensitivity` (default 0.5, decoupled).

**`sudden_impact` after a long recording is deaf with the maximum as its level** (sound-fix, simulated conveyor, rot_heldout):
the highest `vib_peak_g` in two training weeks is 0.58 g, from a few unexplained jolts, against a p99.99 of 0.10 g. So the
line at sensitivity 0.5 is 1.16 g: 0 false alerts at 0.25, 0.5 and 0.75, and nothing caught. A high percentile as the level is
being measured. more_impacts is unchanged by the whole-second fix: 0 false alerts; looseness and both splice damages caught.

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
- **Relaxed on 13 Nov, about 16:40:** "it doesnt have to have 0 false alarms, just as few as possible". Zero is no longer
  a hard requirement. Weigh a small false-alert budget against misses, and prefer a steady setting within a small budget
  over an edge setting at exactly 0. A trade-off section (0–5 false alerts a week) is being added to `SYSTEM_SEARCH.md`.
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
- **Trade-off, false alerts against misses** (`SYSTEM_SEARCH.md`, `tradeoff.json`; not yet checked). Steady picks, where the
  setting's neighbours also stay within the budget; mean missed of 30, without the jam rules:

  | Worst-rotation false alerts a week | 0 | 0.5 | 1 | 2 | 3 | 5 |
  |---|---|---|---|---|---|---|
  | B (two heat-relative models) | 8.3 | 8.3 | 8.0 | 7.0 | 6.0 | 5.7 |
  | A (group PCA) | 11.0 | 11.0 | 9.0 | 7.3 | 6.3 | 5.7 |
  | C (device-ready) | 11.3 | 11.3 | 11.3 | 9.0 | 8.7 | 6.0 |

  - **Budgets under about 1 a week buy nothing** on the steady picks. About 2 a week buys one to three subtle faults
    (gear wear, idler bearing, overload).
  - **Judged by steady picks, B is the best and A is no better than C at zero.** A's earlier 7.7 was an edge setting.
  - **What the false alerts are:** none come from forklifts, slams or the per-second set. About half are start-of-run
    artefacts (simulator heat carry-over, N3); the rest are model alarms with nothing in particular going on.
- **System search, experiment 10: per-group kNN, the SSCC paper's method on our sensor groups. Worse than group PCA, so not adopted.**
  - **Setup:** kNN per group (k 5/10/20, memory 5,000), each group's score mapped to its quantile against cross-week
    out-of-sample training scores, then averaged or maxed.
  - **Alone:** 18.3–25.7 missed, against 13.7 for group PCA. With the per-second set and jam rules: 13–17.
  - **With the line early warning:** 6–46 false alerts a week, since an unseen week's quantile score sits high for hours.
  - **ORed with system A:** 5.3 at best, on an edge (the next sensitivity step gives a false alert), so no robust gain.
  - **Lesson:** on minute medians of one fixed machine, PCA's residual beats distances. The same per-group kNN will be
    reused on SSCC, for a like-for-like comparison with the paper.
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
  - **Status, about 16:25:** PyTorch is installed, and the BEATs (361.5 MB, hash verified) and EfficientAT mn10 (19.7 MB) weights
    are downloaded from their official sources. Running the downloaded model code is blocked until the user approves it in
    that session. The device-feature baseline runs meanwhile.
  - **About 16:50:** the user approved running the models in that session, and it's unblocked. The bearing, gearbox,
    slider and valve dev sets are downloaded from the official Zenodo record 10902294 (about 300 MB each, MD5s verified;
    1,200 clips of 10 s each; 2024 has no pump). Speed on one M2 thread per 10 s clip: BEATs 0.39 s, EfficientAT mn10
    0.06 s. No board was attached for on-device timing.
  - **About 17:40:** CED-tiny is added (22 MB, Zenodo record 8275347, MD5 verified, CC BY 4.0). Every checkpoint loads in
    weights-only mode. The checker's fixes and the survey's experiments are in the script.
    - **Speed, onnxruntime, one Mac thread, per 10 s clip:** BEATs 2.58 s float / 0.91 s int8; EfficientAT mn10 0.042 s;
      CED-tiny 0.060 s float / 0.035 s int8. The int8 CED-tiny matches its float output (cosine 0.999); BEATs 0.97, mn10 0.91.
    - **Early fan finding:** thresholds from training clips don't carry over when test clips come from the shifted
      condition (a 2 % training threshold flags 22 of 100 normal test clips). Familiar-condition clips are the fair device case.
    - Embedding results are still to come; no board was attached for on-device timing.
  - **Paused at about 17:50, with only the fans evaluated** (whole 10 s clips; unfinished, not checked):
    - AUROC: our features 0.59–0.63, BEATs 0.61–0.66, EfficientAT mn10 ≤ 0.61. CED-tiny isn't evaluated yet.
    - Nothing catches a fault at a zero-false-alarm threshold.
    - At a 1 % training threshold: BEATs (layer 12, fused detectors) catches 14 of 100 with no normal clip flagged;
      our features (fused detectors) catch 12 of 100 with none flagged.
    - **So on real fans, BEATs is only slightly better than our own features.** The other four machine types, CED-tiny,
      the layer choice and the board timing are still to do. State: `data/real/dcase2024-fan/embed/RESUME.md`;
      write-up, marked unfinished: `training/record/datasets/AUDIO_EMBED.md`.
  - **Baseline on real DCASE fans (device sound features, not yet checked):** AUROC 0.595, and 0 of 100 real fan faults
    caught at zero false alarms. Our hand-built sound features barely separate real fan faults from normal.
- **DCASE Task 2 survey** ([DCASE_TASK2_SURVEY.md](../datasets/DCASE_TASK2_SURVEY.md); research only, nothing run by us,
  and items marked "summary" there are unverified). What carries over to one machine with no labels:
  1. **A frozen pretrained encoder + kNN, with no training,** and three label-free tweaks: standardise the embeddings,
     use one intermediate layer, and divide the kNN distance by local density. Add it as one more group in the score.
  2. **A small encoder first** (CED-tiny, 5.5 M parameters; or EfficientAT). BEATs is about 34× the compute.
  3. **Fuse several detectors by z-score** rather than picking one. PCA residual on frozen BEATs was the worst backend
     on fans in one paper, so embeddings must not be judged by PCA alone.
  - **Doesn't carry over:** the challenge winners' fine-tuning, which needs labels from many machines.
  - **Caveat:** DCASE only ranks by AUC and pAUC, never by catches at low false alarms, so gains for our measure are unproven.
    Its dev sets have 100 normal test clips per machine, so "0 false alarms" there only shows a rate under about 3 %.
  - These experiments are added to the BEATs test.
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
