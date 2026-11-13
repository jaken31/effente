# Data check: data/fake/conveyor-eval

Quality control of the generation. Reads only `.npz`/`.json`; nothing re-simulated.
**Status: generation 3 started 23:57 (after S4); 0 of 33 finished.** Generation 1 (20:05) was stopped for S1;
generation 2 (20:27) died at 23:54 in a low-battery hibernation and was replaced, after S4, by generation 3: all 33
runs from scratch, with healthy vibration drift, full-minute nuisance labels and the Thursday start for slow
faults (so no reruns this time). Fault tables below headed "generation 2" are kept for reference; their
signatures should carry over except where the drift changes them, and every run is checked again.

## Serious

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
- **M1.** REALISM.md says the MPU-6500's 218 Hz low-pass is modelled; the simulator only band-limits the
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
