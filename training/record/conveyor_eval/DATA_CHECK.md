# Data check: data/fake/conveyor-eval

Quality control of the generation. Reads only `.npz`/`.json`; nothing re-simulated.
**Status: FINAL (13 Nov, 05:30). Generation 3 (started 23:57, finished 05:14, 0 failed): all 33 runs have
`labels.json` and are checked. 9.6 GB.** Generation 1 (20:05) was stopped for S1; generation 2 (20:27) died at 23:54 in
a low-battery hibernation and was replaced, after S4, by generation 3: all 33 runs from scratch, with healthy
vibration drift, full-minute nuisance labels and the Thursday start for slow faults. Tables headed "generation 2"
are kept for reference only.

## Verdict: YES, good to share
The data is complete, intact and realistic enough for the planned minute-level evaluation, with the known issues
below documented rather than blocking (agreed with the main session).

- **Integrity, all 33 runs:** one row a second from the scenario start, no gaps (`diff(t0) == 1` throughout), whole
  seconds, 3600 rows per hour file, no partial files left; minute summaries every 60 s matching the rows; `minute_t0`
  equal to the labels; no inf values. Vibration, sound, thermal and `imu_temp_c` present (NaN only in scheduled
  sensor dropouts); the five `env_*` all-NaN (no room sensor) and long trends NaN only until enough history exists.
  `trend_since_shock_s` stays NaN in 6 fault runs where no jolt happened in 2 days. Every hourly raw clip present
  (48/48, 168/168); 5 clips are shorter because they fall in scheduled sensor dropouts (each matched to the second);
  none unreadable.
- **Labels:** no fault label before any onset; sudden faults switch at the onset minute; slow faults switch 3.5-7 h
  (obvious) and 8-16 h (subtle) after the onset as designed (N1); normal weeks all `normal`; `running` matches the
  vibration exactly; nuisances labelled for every minute they overlap (dropped objects 60-100 min a day, slams 10-22,
  dropouts 6-10, forklifts 45-67, people 70-113, neighbour machine 8-11 h, door open 3-6 h).
- **Healthy machine** (both training weeks): velocity 1.14/1.76/2.53 mm/s (p1/p50/p99), robust spread 19.6 %
  (REALISM 1.7 mm/s, target about 17 %); 3.5 mg per axis and 1.04 g when stopped; sound 74-77 dB in shifts, 50 dB at
  night; motor frame 33-49 C running; two shifts, breaks, Saturday half day, Sunday off; IMU temperature follows the
  housing. heldout: +4 C by design plus weather and load (N6), 10 % busier, Wednesday 10:00-12:00 stop visible.
  Pre-onset levels of every fault run sit inside the training range (percentiles 5-97).
- **Faults:** all 30 runs move the readings REALISM.md lists for their kind, in the right direction and about the
  stated size, subtle at about 0.3 of obvious; slow faults grow and reach full severity at the run's end (about 0.81
  of it at the last running minute, the belt stopping at 22:00); sudden ones switch on at the onset. Subtle idler
  bearing, motor bearing, gear wear and lubrication loss are nearly invisible at the drive end, as expected. Tables
  below.

**Before reading the evaluation, know:** splice damage and jam-subtle are invisible in minute medians (N7: use
minute max or per-second features for them); misalignment doubles the speed tracker's reading, so its diagnosis is
unreliable (S3, a device weakness); the held-out week sits up to about 9 C warmer in the camera than training (N6).
**Fix in the next generation:** S5 (stop transient), N5 (gyro drift), N6 (camera-only weather).

## Serious

### S5. At every stop the motor speeds up for about 1 s; in jam-obvious a 4 g burst (bug, found 04:50; KNOWN, fix next generation; per-second level)
`simulate_conveyor._timeline` lines 224-225: `tail = np.arange(i1, i1 + 15); ramp[tail] = np.exp(-(tail - e) / tau)` with
`i1 = int(e)`. The tail starts at the second before the stop time `e`, so `ramp[int(e)] = exp(+(e - int(e)) / tau)`
is above 1: up to 1.95x speed at ordinary stops (tau 1.5 s), and 8.2x at the jam (tau 0.3 s, e 0.63 s past the
second). Vibration scales with speed squared.
Evidence: normal-w1, every 22:00 and Saturday 14:00 stop, in the last running second: `vib_speed_hz` 29.3 -> 37-42 Hz,
velocity 1.8 -> 4.5-9.4 mm/s, `vib_peak_g` 0.07 -> 0.17-0.28 g, sound +6-7 dB, then the coast-down. jam-obvious raw
clip: a 1.5 g rms buzz around 124 Hz from 1.1 s before to 0.5 s after the jam, peak 4.0 g on x (REALISM: a 1.6 g
jolt; the jolt term alone peaks at 1.27 g). Generation 2 had the same.
Impact: 1-2 s per stop, so minute medians (what `evaluate.py` reads) barely notice; per-second rows, recorder events
and raw clips carry a physically impossible speed-up at every stop, and the jam jolt is about 3x too big.
Fix: start the tail at `ceil(e)` (or cap the ramp at 1). Fixing the data needs a regeneration; proposed as "known,
fix next generation".

### S4. Healthy vibration was almost perfectly constant: subtle vibration faults looked huge (generation 2; FIX in generation 3, to verify)
normal-w1, 6 days, running minutes after the first hour (per-minute medians):

| Feature | p1 | median | p99 | robust SD / median |
|---|---|---|---|---|
| `vib_velocity_mm_s` | 1.664 | 1.676 | 1.695 | 0.45 % |
| `vib_rms_x` (linear g) | 0.0221 | 0.0229 | 0.0237 | 1.1 % |
| `vib_1x` (linear) | 0.0313 | 0.0339 | 0.0346 | 1.8 % |
| `gyr_rms` (linear) | 0.1984 | 0.1996 | 0.2010 | 0.3 % |
| `snd_level_db` | 74.0 | 76.1 | 77.3 | (dB) |
| `th_max_c` | 33.5 | 40.3 | 48.6 | 7.8 % |

Daily median velocity is 1.67-1.68 mm/s on every day of normal-w1 and heldout. The empty belt at lunch reads
1.684 vs 1.679 mm/s loaded mid-morning. Real constant-speed drives scatter by roughly 10-20 % or more in overall
vibration with load, oil temperature, belt tension and material; that is why alert levels are usually set at
2-2.5x baseline. Here a +23 % velocity change (looseness-subtle) is 84 robust spreads out and unbalance-subtle 270;
with realistic scatter they would be about 2 and 7. So every "subtle" vibration fault (looseness, misalignment,
unbalance, motor_bearing, gear_wear) is easy for any method, vibration false alarms will be unrealistically rare, and
the method ranking would reward leaning on tight vibration features. Heat (room-driven) and sound (load-driven,
74-77 dB) vary plausibly.
Cause: in `simulate_conveyor._second` every vibration term is a fixed amplitude x (speed/29.5)^2. Speed varies only
with slip (1-3 %), load scales only the mesh term (0.5 + 0.5 load) and the small material noise, and nothing drifts
slowly. Fix sketch: slow multiplicative drifts per component (lognormal, about 10-15 % SD, hours to days), load
dependence of 1x and mesh, maybe a gearbox-temperature (oil viscosity) term. Needs a full regeneration.
Fix in generation 3 (main session): each vibration source (motor, mesh, out/pulley, idlers, broadband) gets its own
lognormal drift (2-day, 2-hour and day-to-day parts, about 18 % in all), motor and mesh scale with load
(0.8 + 0.4 load), and the mesh is damped as the gearbox warms (1 - 0.006 t_gear). Their check: velocity median
1.77, robust spread 16.6 %, p1-p99 1.37-2.64. To verify on the new weeks. One doubt: warmer, thinner oil usually
First check on generation 3 (00:40, partial: about 1.2 days of each week, pre-onset of 3 fault runs, running minutes
after the first hour): velocity p1/median/p99 normal-w1 1.15/1.49/1.76 (robust SD 6.8 %), normal-w2 1.01/1.43/1.74
(9.8 %), heldout 1.44/1.98/2.33 (6.5 %), fault runs 1.43-2.27 median, 6-18 %. Within a day that is now realistic
(about +-25 % extremes), and machines differ from run to run. The 2-day drift needs the full weeks; the week-level
spread (target about 17 %) is to verify when they finish. `gyr_rms` stays constant (0.3 %), plausibly: it is the
gyro's own noise floor, not the machine.
damps a gear mesh less, not more, so the sign of the oil term is debatable (small: about -10 % at +16 C).

### S1. Slow faults reached only about a quarter of their stated size: FIXED in the restart
First generation: `generate.py` gave every non-jam fault the window `(onset, end + DAY)`, so the four slow faults
(`(exp(3u)-1)/(e^3-1)` wear growth) stopped at 0.23-0.29 of their severity (obvious idler +6 C, not +25).
Reported 20:25; the main session changed slow faults to end their window at the run's end and restarted.
From the new `scenarios.json`:

| Run | Level at run end (00:00, stopped) | At the last running minute | Per-minute label switches after onset |
|---|---|---|---|
| idler_bearing-obvious | 1.00 | 0.81 | 6.9 h |
| idler_bearing-subtle | 0.30 | 0.25 | 16.1 h |
| gear_wear-obvious | 1.00 | 0.83 | 3.7 h |
| gear_wear-subtle | 0.30 | 0.25 after the Thursday fix (0.12 in the superseded Friday version) | about 10 h |
| motor_bearing-obvious | 1.00 | 0.82 | 3.5 h |
| motor_bearing-subtle | 0.30 | 0.24 | 8.2 h |
| lubrication_loss-obvious | 1.00 | 0.83 | 3.8 h |
| lubrication_loss-subtle | 0.30 | 0.26 after the Thursday fix (0.13 in the superseded Friday version) | about 11 h |

To confirm in the data when they finish.

### S2. Two subtle slow-fault runs grew mostly while the belt was stopped: FIXED (rerun pending)
gear_wear-subtle and lubrication_loss-subtle started on Friday and ended Sunday 00:00, so their wear peaked over
a stopped weekend: about 0.12-0.13 at the last running minute instead of about 0.25. Reported 20:30. The main
session changed `generate.py` so that slow faults that would start on Friday start on Thursday. Verified with
`scenarios()`: only these two runs change; both now run Thu -> Sat 00:00 and reach 0.25-0.26 at the last running
minute. Their versions in the current pool use the old settings and are **superseded**; they will be deleted and
rerun with `--only` after the pool finishes. Check the reruns, not the first versions.
Heads-up sent with it: `--only` rewrites `scenarios.json` with only the rerun scenarios. `evaluate.py` reads
`labels.json`, so the evaluation is unaffected, but the plan file should be restored afterwards.

### S3. Misalignment makes the speed tracker read double; every order feature is then mislabelled (feature pipeline, not data; ACCEPTED as a known device weakness, noted in REALISM.md)
`features.py:386` takes `vib_speed_hz` as the strongest steady peak in 8-120 Hz. Misalignment's axial 2x
(0.13 g obvious, 0.039 g subtle) outgrows the 1x, so after the onset the speed reads **58.6 Hz instead of 29.3 Hz**:
in 100 % of running minutes for misalignment-obvious and 60 % (flickering) for misalignment-subtle. All order
features follow the wrong speed: `vib_2x` falls (-0.85 log), `vib_half` rises (+1.6 log, really the 1x), `vib_1x`
is really the 2x, `vib_env_order` halves. The vibration data itself is right (a real device with this tracker
would do the same), but (a) `evaluate.py`'s diagnosis check (`said_fits`) for misalignment will judge diagnoses on
mislabelled orders: the device sees "huge 1x, small 2x", which reads as unbalance; (b) detection gets an
artefact boost from a "speed doubled" jump no real belt can make. Fixing the tracker means regenerating, since
features are computed during generation and raw data is only 10 s an hour. The other choice is to accept it and
say in the evaluation that misalignment diagnosis is unreliable with this tracker. The main session decides.

## Known, fix next generation (documented, not blocking this dataset)
Agreed with the main session (04:10, S5 at 04:55): accepted for tonight's data, to be fixed in the simulator after this
generation finishes.
- **N5. Gyro's healthy level has no drift:** `gyr_rms` varies 0.3 %, so looseness's rocking stands out (z +86
  subtle). Fix: apply the motor drift to the gyro's machine part in `simulate_conveyor._second`.
- **N6. site_noise weather reaches only the camera, and is bigger than documented:** `site_noise.py:300` adds
  `_weather` to the thermal frames (and :331 to the room sensor realistic mode lacks); `imu_temp` never gets it, and
  the walk at :160 spans up to about 10 C a week instead of +-2 C. Fix: add the lagged weather to `imu_temp` and
  scale the walk.
- **S5. Stop transient (the ramp's tail starts at `int(e)`, the second before the stop):** at every stop the motor
  briefly speeds up (up to 1.95x; 8.2x at the jam) and the inrush branch of `self.current` is skipped for that second
  (`ramp < 1` is false). Touches per-second rows, recorder events and raw clips at every stop (twice a weekday in
  every run), and jam-obvious's jolt (4.4 g, about 3x too big). Minute medians, and so `evaluate.py`, are barely
  affected. Fix: start the tail at `ceil(e)` or cap the ramp at 1. (The main session called this N7; it is S5 here
  because N7 was already taken by the minute-median note.)
Details of N5 and N6 under Notable below, S5 under Serious.

## Notable (not wrong data, but worth knowing before reading the evaluation)

- **N1. Per-minute labels lag the onset for slow faults.** `label_at` only names a slow fault once its level
  passes 0.02 (0.05 for idler wear). After the fix that is **3.5-7 h after onset for the obvious ones and
  8-16 h for the subtle ones** (table in S1). `evaluate.py` scores from `scenario.onset`, so
  the evaluation itself is unaffected; anyone scoring from `labels.json`'s per-minute `label` would count
  those hours as normal.
- **N2. Each fault run is effectively a different thermal camera.** The camera's fixed offset (±1.5 C) and
  fixed-pattern noise come from `[seed, 9]` / `[seed, 3]`, and every run has its own seed, so the offset
  differs from the training weeks by -1.5 to +1.4 C (heldout +1.2 C). The weather term (±2 C, day scale) is
  larger, so the effect looks like weather, but it is not "the same device on the same machine".
- **N3. Monday-start fault runs begin with heat carried over from the sim's end (confirmed 21:25, minor).**
  `_lag` runs twice and starts its second pass from the first pass's end state. Fault runs starting on Monday
  (d0 = 0: idler_bearing-subtle, belt_slip-obvious, looseness-subtle, gear_wear-obvious, jam-subtle,
  lubrication_loss-obvious) take that state from the sim's end, Thursday 00:00, two hours after a shift. So their
  Monday 00:00-06:00 looks like the night after a working day, not after a weekend. Seen in idler_bearing-subtle:
  gear cell `th_cell_33` 3.5 -> 0.6 C over 00:00-05:00 and `imu_temp_c` 30.4 C at 00:00, against normal-w1's cold
  Monday night (0.07 C flat, 26.9 C). For the two Monday runs with slow gearbox faults at full level at the sim's
  end, the start is warmer still (predicted about +16 C at 00:00, +2 C by 06:00 for lubrication_loss-obvious).
  `evaluate.py` skips the first 6 h and stopped minutes, so only a residual of about 0.6-2 C at the first
  running minutes reaches the pre-onset scoring. Unrealistic but small. Fix if wanted: start the lag from zero
  (or from the first pass's state at d0 x DAY).

- **N4. `labels.json` missed most short nuisances (found 22:35; FIXED: evaluate.py rebuilds them, and generation 3 writes full-minute labels).**
  `generate.py` writes `nuisance` as `src.nuisance_at(m + 30)`: what is active at the minute's 30th second. Events
  shorter than a minute are named only if they happen to cover that instant. belt_slip-subtle: the site_noise
  schedule (rebuilt from the seed, no simulation) has 197 dropped objects, 29 door slams, 2 device knocks and
  11 sensor dropouts in the run; `labels.json` names 3, 0, 1 and 1 of them. Forklifts (15-60 s): 57 events, 38
  minutes. `evaluate.py` attributes each false alarm to `lab["nuisance"]` at the minute it began, so most
  bang- and jolt-caused false alarms will show as "nothing in particular". The false-alarm counts themselves are
  unaffected. Fix without re-simulating: rebuild `nuisance` per minute from `SiteNoise(...)._schedule` (cheap and
  deterministic from `site_seed`) as every event overlapping `[m, m + 60)`, and in `generate.py` for later runs.
  Fixed by the main session: `generate.minute_nuisances()` (every event overlapping the minute) and
  `evaluate.full_nuisances()`, which rebuilds the labels from the scenario's seeds for existing runs (checked by
  them on belt_slip-subtle: door_slam 0 -> 29, dropped_object 3 -> 193). Caveat: the running pool uses `spawn`
  and its 8 workers imported the old `generate.py` at startup, so **every `labels.json` from this pool keeps the
  old 30th-second `nuisance` field**; only the two `--only` reruns get the new one. `evaluate.py` rebuilds them,
  so the evaluation is right; anything else reading `labels.json["nuisance"]` directly is not.

### Laptop outage, about 22:55-23:15 (generation 2, checked 23:25): no effect on the data
The Mac hibernated at 1 % battery and woke at 23:14:57; the generator (pid 64859) and its workers were frozen
and carried on. In all 8 runs that were in progress (the three weeks, looseness-*, misalignment-*,
gear_wear-subtle): one row a second from the start with no gap or jump in `t0` (all `diff == 1`, whole seconds),
every closed hour file 3600 rows, minutes contiguous. The hour files show a 19-20 min wall-clock pause ending
23:15-23:16, with no NaN rows around it; the only NaN stretches near it (looseness-subtle, 6 s of sound, 6 s of
heat) match scheduled site_noise sensor dropouts at those exact seconds. The sim and recorder run on simulated
time, so a pause can't show in the data.

- **N5. The gyro's healthy level got no drift (minor; found 03:25).** `gyr_rms` varies 0.3 % in normal running
  (the machine part `0.05 x s2` in `_second` isn't multiplied by the motor drift, and the gyro's own noise floor
  dominates). Looseness adds rocking, so `gyr_rms` jumps +28 % in looseness-subtle: z +86 against normal, against
  +19.5 for kurtosis. A constant noise floor is realistic, but the rocking part should wander with the machine; with
  `gm` on it the spread would be about 1-2 % and subtle looseness about 15-25 spreads in the gyro. Looseness stays
  clearly detectable through kurtosis either way, so not worth regenerating; a method that leans on the gyro will
  look a little better on looseness than it would on a real belt.

- **N6. The held-out week is warmer than designed in the camera, and site_noise's weather reaches only the camera
  (found 04:05; minor-moderate, accepted for this dataset unless the main session says otherwise).** Measured
  heldout vs normal-w1 (all minutes): camera background +9.1 C, `th_max_c` +8.7 C, `imu_temp_c` +4.6 C. Decomposed:
  the simulator's room is about 4 C warmer by design (3.9 C vs w1, 6.1 C vs w2 after each week's own weather; main
  session's rebuild), plus about 1.4 C at the motor from the 10 % higher load; and in the camera only, the per-run
  camera offset (+1.2 C, N2) and site_noise's weather walk (+4.0 C: mean over the run w1 -2.2, w2 -0.8, heldout
  +1.7 C). That adds up to the measured +9.1 C. Two issues with the walk: (a) `SiteNoise._thermal` adds it to the
  thermal frames only, while `imu_temp` passes through, so a warmer room warms what the camera sees but not the
  housing the IMU is fixed to; (b) it is bigger than documented ("about +-2 C over a week"): over a run it spans
  w1 -6.8..+1.0, w2 -7.8..+2.9, heldout -0.8..+4.8 C. Effect: the camera-vs-IMU relation shifts between weeks for no
  physical reason (background minus IMU while running: w1 -19.9, w2 -19.4, heldout -16.3 C; training p5-p95
  -24.7..-14.3), which costs a multivariate model some heldout false alarms; background-relative thermal features
  are unaffected. Fix later: lagged weather on `imu_temp` too, and scale the walk to the documented size.

- **N7. Minute medians hide impulsive faults (found 04:25; evaluation design, not data).** `evaluate.py` loads
  `minute_median` rows. A splice knock once per 26 s lap (or a 5 s stall in jam-subtle) never reaches a minute's
  median: in splice_damage-obvious the medians move only `knock_repeat` +1.8 and `vib_peak_g` +2.7 robust
  spreads, while `minute_max` shows sound peak 83 -> 96 dB and vibration kurtosis 0.2 -> 15.6. Every method will
  likely miss splice damage because of the aggregation, not the method. For the evaluation: add minute_max (or a
  high percentile) for impulsive features (`vib_peak_g`, `snd_peak_db`, `vib_kurtosis`, `snd_kurt_hf`, `knock_*`),
  or report splice and jam separately.

## Checks done so far (round 1, first generation, partial data)

### Process health
- 8 workers running (3 weeks + 5 fault runs at a time), no `FAILED` in the log. Disk: 1.4 TB free, 663 MB used.
- Progress 20:20: fault runs 0.31-0.35 after 15 min (about 45 min a run), weeks 0.09-0.10 (about 2.6 h).

### Integrity (normal-w1, heldout, belt_rub-subtle, idler_seized-obvious, partial)
- One row a second from the scenario start, no gaps (all `diff(t0) == 1`), all on whole seconds; 3600 rows per
  finished hour file, a growing `.partial.npz` for the current hour.
- Minute summaries every 60 s; `minute_median` matches the median of that minute's rows (worst relative
  difference 6e-7).
- All-NaN: the five `env_*` (no room sensor, as intended), plus `trend_room_*`/`trend_humidity_1h` (no room
  sensor) and the 24 h / 7 d / 4 w trends (not enough history yet). Vibration, sound, thermal and `imu_temp_c`
  present everywhere. `trend_running` is 1 on the first night, as documented (a machine never seen stopping
  counts as running).

### Weekly structure (generation 2: normal-w1 Mon-Sat, heldout Mon-Sun, partial; 00:00)
Two shifts 06:00-22:00 on weekdays (16.0 running hours, start 05:57-06:05, stop 21:55-22:07), Saturday
06:00-14:00 (8.1 h), Sunday off. heldout Wednesday: running stops 10:00-12:00 (maintenance), 14.1 h that day.
heldout is warmer: running `th_max_c` 48-56 C vs 35-44 C; background +4-6 C. Day/night room swing 7-16 C in
`th_background_c` (includes sun through the window on some days, up to 41.5 C on heldout Friday). Sound 76.1-76.7 dB
running, 50.0 dB at night, every day. `trend_running` reads 0 at 03:00 from day 2 on. Lunch/evening breaks run
empty: sound -2.2 dB and `th_max_c` -1.8 C, but vibration unchanged (S4).

### Healthy machine so far (normal-w1 Monday, heldout Monday, pre-onset of 5 fault runs)
| Reading | Target | Seen |
|---|---|---|
| vibration velocity, running | 1.7 mm/s | 1.67-1.69 |
| accelerometer noise, stopped | 3.5 mg/axis | 3.5 mg (`vib_rms_*` = -2.456 log10 g; raw clip 3.44/3.52/3.49 mg) |
| gravity | 1.04 g | 1.040 |
| motor frame `th_max_c`, running | 40-50 C | w1 Monday 37-44 C (warming from cold), heldout 45-51 C |
| sound | 70-80 shift / 50 night | 75.5-76.7 / 50.0 |
| raw spectrum (x, running) | | 29.1 Hz (1x), 167.5 Hz (2nd-stage mesh, 23 x 7.28 Hz), 58 Hz (2x), 7.3 Hz and 1.5 Hz shafts, mesh sidebands at ±7.3 Hz, 120 Hz line: consistent with the drive's kinematics |
| IMU temperature | follows housing with lag, +9 C device warmth | 26-27 C at night (room 17-18), 43-44 C running; rises over the morning with the housing |

Pre-onset levels of the fault runs sit inside normal-w1's running distribution (percentiles 11-98 for the main
vibration, sound and heat readings). Heldout Monday is warmer as intended (background 28.9 C vs 23.6 C).
A neighbouring machine ran in heldout Monday 03:00-05:00 (sound 60.6 dB, `vib_speed_hz` locks onto its 31.6 Hz
tone): a site_noise nuisance, plausible.

### Fault runs, generation 3 (current)
Checked the same way as generation 2 below (integrity, labels, before/after shifts). The idler and belt_rub runs
are identical in their fault signatures to generation 2 (same seeds; only the vibration drift is new), so the
generation-2 rows for them stand. Integrity OK for all; no non-normal labels before onset; `running` agrees with
vibration.

| Run | Result |
|---|---|
| heldout | clean. Weekdays 16 h, Sat 8 h, Sun off; Wednesday 14.1 h (maintenance stop 10:00-12:00, `running` false in labels); velocity daily medians 1.82-2.08, robust SD 10.4 %; stopped 3.49 mg, 1.041 g; sound 76.4 / 50.0 dB; `th_max_c` 41-60 C running; all nuisances present. Warmer than training: see N6 |
| normal-w1 | clean. Same week structure; velocity p1/p50/p99 1.17/1.85/2.61 mm/s, robust SD **20.6 %** (S4 fixed), daily medians 1.50-2.25; `vib_rms_x` 17 %, `vib_1x` 22 %; sound 74.0-77.3 dB; `th_max_c` 33-49 C; IMU 35-49 C; `trend_running` 0 at night from day 2; `running` labels = vibration (100 %) |
| normal-w2 | clean; continuous with normal-w1 (last/first minutes match: background 10.2/9.9 C, IMU 25.9/25.5 C); velocity p1/p50/p99 1.06/1.67/2.22, robust SD 20.2 %; a cold start of the week (background 7-20 C Mon-Tue, `th_max_c` 34 C running) warming to 25-33 C. **Both training weeks together: velocity 1.14/1.76/2.53 mm/s, robust SD 19.6 %** (REALISM 1.7 mm/s, target about 17 %) |
| belt_rub-subtle | clean, same as gen 2 (`th_spot_37` +3.3 C, 2.8 kHz squeal, +2.4 dB) |
| idler_bearing-subtle | clean; idler heat `th_spot_36` +3.7 C only; with drift its vibration change (+2.5 robust spreads in velocity) is within normal wander, as REALISM.md expects |
| idler_bearing-obvious | clean; BPFO envelope at 20 Hz, `th_spot_36` +12 C (level 0.81 at the last running hour; full 1.0 at 00:00 with the belt stopped) |
| idler_seized-subtle | clean; `th_spot_37` +8 C, 1.6 kHz band +17 |
| idler_seized-obvious | clean; tone 1580 Hz, +26 C, +8.2 dB |
| belt_rub-obvious | clean; 2.8 kHz squeal 84 dB, `th_spot_37` +15 C, +8.5 dB |
| belt_slip-subtle | clean; lap 26 -> 27 s, pulley cells +4 C; a device knock in the window (tilt to 4.5 deg, same as gen 2) |
| belt_slip-obvious | clean; pulley +12-13 C, slip bursts +10 dB envelope, sound +4.1 dB; `knock_period_s` 13 s (M4) |
| unbalance-subtle | clean; velocity 1.79 -> 3.26 mm/s (x1.8). Against the normal weeks so far (velocity p1/p50/p99 1.16/1.84/2.56, robust SD 21 %): **3.7 normal spreads**, above every normal minute. Subtle but detectable, as it should be (gen 2: 270) |
| unbalance-obvious | clean; velocity 2.00 -> 8.30 mm/s (x4.2), 16.7 normal spreads; crest 2.1 -> 1.5 as the 1x dominates |
| gear_wear-subtle | clean (Thursday-start version: Thu 13:03 -> Sat 00:00, last running minute Fri 21:57 at 0.25). Genuinely subtle: mesh band (`vib_band_15`, 158-200 Hz) +0.4 log of which about 0.3 is a mesh-drift step between Thu and Fri; 2x mesh band (`vib_band_18`) +0.3; gear cell +1.3 C; whine +0.4 dB. Label at +9.6 h |
| gear_wear-obvious | clean; grows over the run: `vib_band_15` -3.5 -> -2.9 log (x4 in the last hours), 2x mesh `vib_band_18` -5.1 -> -4.2, whine: `snd_tone_hz` 497 -> 169 Hz (the 2nd-stage mesh becomes the loudest tone), `snd_tone_db` +3.2, gear cell +3.5 C (+8.3 true, blurred), kurtosis and envelope up. Label at +3.7 h |
| motor_bearing-subtle | clean; nearly invisible (level 0.25 at the end): `vib_band_19` (400-500 Hz) +5.5 robust spreads, nothing in heat or sound. Realistic: an early bearing defect lives at kHz, which a 1 kHz accelerometer cannot see |
| motor_bearing-obvious | clean; envelope 7 -> 105 Hz (order 3.58 = motor-bearing BPFO), `snd_kurt_hf` 0 -> 2.1 (pulsed hiss), `vib_band_19` +15, motor cell +3.8 C at the same hour of day (+4.9 true; before/after across hours is confounded by evening load) |
| splice_damage-subtle | clean; in minute **max**: sound peak 83.1 -> 89.1 dB, `knock_repeat` 0.54 -> 0.77. Invisible in minute medians (N7) |
| splice_damage-obvious | clean; in minute **max**: sound peak 83.1 -> 95.9 dB (+12.8; REALISM 74 -> 88 dB knock), vibration kurtosis 0.2 -> 15.6, peak g x2.3. In minute medians only `knock_repeat` +1.8 and `vib_peak_g` +2.7 spreads (N7). A device knock in the window |
| jam-subtle | clean; all five 5 s stalls (h 10.2, 12.7, 15.2, 34.2, 36.7 after start) present per second: shudder 0.33-0.39 g (REALISM 0.35 g; normal peak about 0.075 g), gyro peak about 13 dps, speed dip 29.3 -> 21.7 Hz, 1.9 kHz squeal; the belt keeps running; one `jam` minute labelled per stall. Invisible in minute medians (N7) |
| jam-obvious | clean apart from S5; the belt stops dead at the onset (running stops for 3 min, then a soft-start restart over about 8 s), 1.9 kHz slip squeal in the 3 s before, jolt peak 4.4 g (S5: should be about 1.3-1.6 g), gyro peak 62 dps; `jam` labelled in the onset minute |
| overload-subtle | clean; same hours of day, pre-onset day vs last day: motor cell +4.3 C, `th_max_c` +4.1, IMU +4.0, sound +0.8 dB; vibration +2.8 spreads (motor and mesh scale with load) |
| overload-obvious | clean; same hours: motor cell +13.3 C, gearbox +2.9, sound +1.9 dB, mesh band +0.34 log; `knock_period_s` 26 -> 53 s (louder material noise masks every other splice knock: a feature effect) |
| lubrication_loss-subtle | clean (Thursday-start version, label +10.8 h); genuinely subtle: gear cells +0.4-0.6 C at the same hours, about +5 C true at the end |
| lubrication_loss-obvious | clean; gear cells +3-4 C by mid-shift on day 2 and about +10 C in the last running hours (true +16.6 C at level 0.83, lagged and blurred), 3-6 kHz hiss: centroid +4.4 spreads. Vibration doesn't rise (the friction term is small; a real dry gearbox would get louder in vibration too: minor) |
| cooling_blocked-subtle | clean; motor cell +7.3 C at the same hours (0.3 x 25 C), nothing in vibration or sound, as it should be |
| cooling_blocked-obvious | clean; motor cell +24 C at the same hours (REALISM +25), `th_max_c` to 64.5 C, IMU +3.6 C (it sees 15 % of the motor), nothing in vibration or sound |
| electrical-subtle | clean; mains hum `snd_mains_db` +3.6 dB (0.3 x 12), `vib_line_2x` x1.7, motor +2.1 C (0.3 x 8); the 120 Hz hum becomes the loudest tone |
| electrical-obvious | clean; mains hum +12.1 dB (REALISM +12), `vib_line_2x` x4.5, motor cell +8.2 C (REALISM +8) at the same hours; the 120 Hz hum is the loudest tone |
| looseness-subtle | clean; kurtosis -0.18 -> 0.86 (z +19.5 vs normal), harmonics 3-10x z +9.9, axial level z +2.9, velocity z +0.7 (hidden in normal wander); `gyr_rms` z +86 (N5) |
| looseness-obvious | clean; kurtosis 2.66 (+2.8, REALISM +2.6), velocity 3.6 (z +5.3), rattle in sound (`snd_kurt_hf` 9.8), gyro rocking z +298 (N5) |
| misalignment-subtle | clean; velocity unchanged (z 0.0), axial `vib_rms_z` z +3.8, kurtosis down (more sinusoidal). Speed tracker doubles (S3, accepted) |
| misalignment-obvious | clean; velocity 1.76 -> 3.76 (z +5.7), `vib_rms_z` z +11.3, coupling cells warmer; speed tracker doubles (S3); a device knock in the window |

Slow faults reach full severity at the run's end, 00:00, when the belt has been stopped since about 22:00; the last
running minutes are at about 0.81 of full (obvious) and 0.25 (subtle). Agreed with the main session as fine.
The sleep from 00:47 to 01:37 fell inside batch 1; all five runs are gap-free (the sim runs on simulated time).

Nuisance labels are now whole-minute: per day dropped objects 62-103 minutes, door slams 10-22, sensor dropouts
6-10, forklifts 45-67, people talking 70-113 (generation 2: 0-2 for the short ones).

### Fault runs, generation 2 (superseded; finished runs; median before onset vs after, robust spreads of "before")
"After" is from 30 min past the onset for sudden faults, the last 3 running hours for slow faults, and the 5 minutes
around the event for jams. All runs below: integrity OK (172,800 rows, no gaps, whole seconds, minute summaries
match rows, `minute_t0` = labels), no non-normal labels before onset, `running` in labels matches the vibration.

| Run | Onset | Label switches | Top moves | Matches REALISM.md? |
|---|---|---|---|---|
| belt_rub-subtle | Fri 11:00 | at onset | `th_spot_37` 1.9 -> 5.2 C, `th_spot_27` 0.9 -> 4.7 C (+37), 2.8 kHz band +35, centroid 1310 -> 2090 Hz, `snd_level_db` +2.4 dB | yes (0.3 x 18 C, blurred; squeal 76 dB) |
| idler_bearing-obvious | Tue 16:58 | +6.9 h (slow, N1) | envelope peak 7 -> 20 Hz (= 3.6 x idler speed, BPFO), `th_spot_36` 1.9 -> 14.1 C, `snd_env_peak_db` +4.5, `snd_kurt_hf` +0.3, `vib_band_06` +12 | yes. Spot +12 C for a true +20 C (level 0.81 at the last running hour): a 3x2-pixel idler is smaller than the camera's spot size, so the cell max reads about 60 %; realistic for a 32x24 camera |
| idler_bearing-subtle | Mon 14:15 | +16.1 h (slow) | `th_spot_36` 1.9 -> 5.6 C (+27), nothing in vibration or sound | yes: as REALISM.md says, nearly invisible at the drive end, except the idler's heat in the camera |
| idler_seized-obvious | Thu 10:15 | at onset | tone 497 -> 1580 Hz, `th_spot_37` 1.7 -> 27.7 C, `snd_level_db` +8.2 dB, `snd_tone_db` +12 | yes (1.58 kHz squeal, +35 C blurred) |
| idler_seized-subtle | Wed 15:46 | at onset | `th_spot_37` 1.7 -> 9.7 C, 1.6 kHz band +17, `snd_level_db` +0.9 dB | yes (about 0.3 of obvious heat; a 71 dB tone under the 497 Hz mesh) |
| belt_rub-obvious | Mon 16:44 | at onset | tone 498 -> 2800 Hz, `th_spot_37` 1.8 -> 16.9 C, `th_spot_27` 0.6 -> 14 C, `snd_level_db` +8.5 dB, centroid 1270 -> 2700 Hz | yes (+18 C blurred, 84 dB squeal). Subtle/obvious heat 3.3/15 = 0.22 |
| belt_slip-subtle | Tue 08:03 | at onset | `knock_period_s` 26 -> 27 s (belt about 3 % slower), pulley cells +4 C (`th_cell_34` 2.8 -> 6.8), `snd_env_peak_db` +2.6 | yes (0.3 x 15 C; slip 0.3 x up to 10 %). Also a device knock (13:10 and 14:02, tilt 2.5 then 4.5 deg total): a nuisance inside the fault window, see note below |
| belt_slip-obvious | Wed 16:13 | at onset | pulley cells +12-13 C (`th_cell_34` 3.3 -> 15.8), `snd_env_peak_db` +10, `snd_level_db` +4.1 dB, `knock_period_s` 26 -> **13** s | heat and sound yes (+15 C; bursts at about 88 dB). `knock_period_s` locks onto the slip bursts, not the splice (M4) |
| unbalance-subtle | Thu 15:58 | at onset | `vib_velocity_mm_s` 1.68 -> 3.40, `vib_1x` +0.32 log (x2.1), `vib_rms_x`/`_y` +0.30/+0.33 log | yes, exactly REALISM.md's 3.4 mm/s (ISO 10816-3 zone C) |
| unbalance-obvious | Fri 12:41 | at onset | `vib_velocity_mm_s` 1.67 -> 8.03, `vib_1x` x5, `vib_rms_y` +0.76 log, crest 2.1 -> 1.45 (sinusoidal) | yes, exactly 8 mm/s (zone D). y axis 90 deg from x as SKF describes; crest falling as 1x dominates is right |
| looseness-subtle | Mon 11:02 | at onset | envelope 7 -> 88 Hz (3x), `vib_kurtosis` -0.13 -> 0.73, `vib_harm_3_10` +0.6 log, `vib_half` +0.7 log, `gyr_rms` +0.1 log, velocity 1.67 -> 2.06 | yes (about 0.3 of obvious kurtosis) |
| looseness-obvious | Tue 10:47 | at onset | `vib_kurtosis` -0.14 -> 2.67 (+2.8), `vib_harm_3_10` +1.1 log, `vib_half` +1.2 log, `gyr_rms` -0.70 -> -0.33 log (rocking x2.3), `snd_kurt_hf` 0 -> 9.8 (rattle), velocity 1.68 -> 3.6, crest 2.1 -> 2.9 | yes (REALISM: kurtosis +2.6, rattle). Harmonics to 10x, half order, z-axis shocks: textbook SKF looseness |
| misalignment-subtle | Wed 10:33 | at onset | `vib_rms_z` +0.28 log, velocity 1.68 -> 2.04, `vib_speed_hz` 29.3 -> 58.3 (60 % of minutes), coupling/gear cells +1-3 C | heat and axial level yes; orders garbled by S3 |
| misalignment-obvious | Thu 12:27 | at onset | `vib_rms_z` -1.78 -> -1.03 log (x5.6), velocity 1.68 -> 3.77, `vib_speed_hz` 29.3 -> 58.6 (always), gear cells +3-4 C, `th_spot_23/33` +4.4 C | axial 2x and warm coupling yes (REALISM "+5 C"); orders garbled by S3. Also a device knock (tilt about 3 deg) in the window |

Nuisances (minutes a day with each active, from `labels.json`): neighbour machine 470-690, door open 180-380,
sun 210, people talking 36-55, hot object 12-50, forklifts 17-26, person in front of the camera 8-27, drops,
slams and sensor dropouts about 1 each. All present; the neighbour machine (8-11 h a day) and open doors are the
most common. Device knocks tilt 1-3 deg each (as documented) but add up: two knocks 53 min apart in
belt_slip-subtle leave 4.5 deg; over the 14-day chain several can accumulate. Plausible, and they are nuisances inside
some fault windows, which `evaluate.py` will count toward detection if a method alarms on them.

## Minor notes
- **M1.** (Generation 3: the 2x-mesh band `vib_band_18`, 315-400 Hz, has almost no healthy content, so gear wear's 2x mesh shows strongly there even at small levels; through a real 218 Hz low-pass it would be weaker.) REALISM.md says the MPU-6500's 218 Hz low-pass is modelled; the simulator only band-limits the
  noise (260 Hz). Machine content above 218 Hz (looseness harmonics to 295 Hz, 2x mesh at 340 Hz with gear
  wear, the bearing band to 250 Hz) reaches the features unattenuated, so a real board would show those
  somewhat weaker.
- **M2.** Fault runs are 2 days with fresh history, so `trend_*_24h` is NaN for their first 20 h (all of
  pre-onset, part of post-onset) and the 7 d / 4 w trends are NaN throughout, while the training weeks have
  them from day 2 on. `evaluate.py` has to handle features that exist in training but not in the test runs.
- **M3.** `events.jsonl` logs `sensor_missing_at_start: room` in every run; harmless (there is no room sensor).
- **M4.** belt_slip's stick-slip squeal is gated by a fixed 0.23 Hz sine (bursts exactly every 4.35 s). Real
  stick-slip is irregular. Because it is so regular, `knock_period_s` in belt_slip-obvious locks onto 3 bursts
  (13.0 s) instead of the slower splice lap (expected about 28 s; the subtle run shows 27 s). Still a strong change
  either way, but a method could "detect" belt slip from an artefact of perfect periodicity.
