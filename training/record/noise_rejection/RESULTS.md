# Signal methods that need no new hardware: simulator check

Everything here is simulated (`ConveyorSim(realistic=True)` with `SiteNoise`, factory profile) unless a row says otherwise. Nothing was run on a device recording. Levels in the simulator are partly unverified (`training/record/conveyor_eval/REALISM.md`), so these results say which method is worth trying on real data. They do not say how much it helps on a real machine.

This follows section 6 of `docs/research/research-grade-claim.md`. Each method is a candidate change to `effente/features.py`, so none goes in without the five-person agreement (AGENTS.md, Shared code). The prototypes in `lab.py` work on the raw windows and leave `effente/` unchanged.

## Which gaps these methods target

The plan's section 6 listed methods without tying them to a measured gap. What the repo already measured:

| Gap | Where it is measured | Method tried here |
|---|---|---|
| Speed reads double under misalignment, so every per-turn reading is off by 2 | `conveyor_eval/REALISM.md`, "Known weakness" | A. Speed from the harmonic family, not the single strongest peak |
| Subtle motor bearing and idler bearing are missed in every rotation | `conveyor_eval/SYSTEM_SEARCH.md`, "What A still misses" | B. Tone removal before the envelope (cepstrum pre-whitening). C. Envelope spectra averaged over 60 s |
| Bearing faults named `bearing_defect_like` in 1 of 40 Ottawa files | `datasets/OTTAWA_RESULTS.md` | B, on the Ottawa files |

Dropped, with the reason:

- **Neighbouring machine.** The simulated neighbour moves the 32-40 Hz band by 4.4 healthy spreads (section 2 of the plan). But the full system is trained with `SiteNoise` and gives 0 false alerts in every rotation (`SYSTEM_SEARCH.md`). In the simulator, the neighbour has no measured cost. Revisit once step 1 of the plan (recording the surroundings) shows a real neighbour.
- **Speed locking onto the neighbour while stopped.** Nothing is judged while the machine is stopped (`effente/model_runtime.py`, `trend_running`). No cost.
- **Machine-off baseline.** Its target is the neighbour, which has no measured cost in the simulator.

## Bars, written before any run (17 Nov 2026)

**A. Speed.** Error means more than 3 % away from the simulator's true motor speed, on running windows.

- Cases: healthy, healthy with the site's neighbour running, misalignment (obvious and subtle), looseness, unbalance (obvious), and a stress neighbour 0.3 Hz from the shaft speed with two harmonics.
- Passes if: misalignment error falls to 5 % of windows or fewer at both severities, and no other case gets more than 1 point worse than today.

**B. Tone removal before the envelope.** A hit means the envelope's strongest rate is within 3 % of the true defect rate: 3.58 times motor speed for the motor bearing, 3.6 times idler 2's turning rate for the idler bearing.

- Cases: motor bearing and idler bearing at severity 0.3 and 1. Healthy windows for the false side.
- Passes if: the hit rate rises by 20 points or more at severity 0.3 for at least one of the two faults, and the healthy windows' envelope prominence does not rise (median within 10 %). Prominence must still separate fault from healthy, with an AUC no lower than today's.

**C. Envelope spectra averaged over 60 s.** Same hits and AUC as B, from one averaged envelope spectrum per minute against the median of sixty 1 s readings (the system already smooths over 10 minutes, so the median is the fair comparison).

- Passes if: AUC at severity 0.3 rises by 0.1 or more for at least one bearing fault.

**For all three:** a method that passes still needs a run on a real recording before it is proposed for `effente/`.

**B on real bearings (Ottawa), added 17 Nov after the simulator runs, before this one.** Each of the 20 bearings' files goes through `datasets/ottawa.py`'s conversion to what our device would record (1 kHz, the MPU's 218 Hz low-pass, its noise). Windows of 1 s every 0.25 s. The dataset gives no bearing geometry, so the true defect rate is unknown. Two measures instead:

- Separation: AUC of envelope prominence, the bearing's healthy file against each of its two fault files. The median over the 40 fault files.
- Off the shaft orders: the share of files whose most common envelope rate is more than 0.1 of an order from any whole multiple of the running speed. Bearing defects pulse at rates that are not whole multiples; whole multiples read as unbalance or misalignment, which is the naming failure in `OTTAWA_RESULTS.md`.

Passes if: the median AUC rises by 0.1 or more, the off-order share of fault files rises by 20 points or more, and the off-order share of healthy files rises by no more than 10 points (otherwise whitening only turns noise into random peaks).

**A on real bearings (Ottawa), added after B's Ottawa run, before this one.** Unlike the others, this bar went into the same commit as its results; it was written before the run, but the git history cannot show that. The files log the shaft speed, so the speed reading can be checked against it. Same conversion and windows as above, all 60 files.

Passes if: the harmonic-family reading is more than 3 % off in no more windows than today's, in each of the five groups (healthy, inner race, outer race, ball, cage), and in 5 % of windows or fewer overall.

## Results

Run 17 Nov 2026. Simulator seeds 1 and 2 (site noise seeds 1001 and 1002): `results/sim_seed1.json`, `results/sim_seed2.json`. Ottawa: `results/ottawa_check.json`.

**In short:** none of the three methods is ready to propose for `effente/`.

- **A** fixes the doubled speed in the simulator. On the Ottawa rig, neither today's speed reading nor the candidate finds the shaft speed: a 120 Hz hum and a 92 Hz peak beat the shaft's own 1x.
- **B and C** look large in the simulator. B failed its Ottawa bar, but controls run afterwards show that bar cannot tell a fault from a different recording, so Ottawa does not judge B either way. C could not be run on 10 s files.

What is firm: on this rig, the device's speed reading never found the shaft. A once-per-turn tachometer (plan step 6) is the direct fix. That B and C need the kilohertz band (plan step 2) is plausible but not shown here.

### A. Speed

Simulator, % of running windows more than 3 % off (seed 1 / seed 2):

| Case | Today | Halve if a peak at half | Harmonic family |
|---|---|---|---|
| healthy (neighbour on in 60 % of windows) | 0 / 0 | 0 / 0 | 0 / 0 |
| misalignment | 100 / 100 | 0 / 0 | 0 / 0 |
| misalignment, subtle | 9 / 19 | 0 / 0 | 0 / 0 |
| looseness | 3 / 8.5 | 97 / 91.5 | 0 / 0 |
| looseness, subtle | 0 / 0 | 96 / 92 | 0 / 0 |
| unbalance | 0 / 0 | 0 / 0 | 0.5 / 0 |
| tone 0.3 Hz above 1x, two harmonics | 0 / 0 | 0 / 0 | 0 / 0 |

- **In the simulator, the family passes its bar.** The simple halving fix suggested in REALISM.md fails: looseness has a real half-order peak, so it halves the speed in over 90 % of windows.
- **On Ottawa it fails.** It is more than 3 % off in 92 % of windows. Today's reading is off in 100 %.
- **Why today's reading fails on Ottawa:** a 120 Hz hum is the strongest peak in every file. That is twice the mains frequency, the motor's magnetic vibration. So today's reading is 4 times the shaft speed throughout.
- **A second version, written after seeing Ottawa (post hoc):** it ignores the twice-mains bins and also tries a quarter of each peak. It brings the error to 81 %, with the simulator unchanged.
- **What is left after that:** a peak near 92 Hz on most bearings. It is not a whole multiple of the speed (3.2 times it on bearing 11), and the shaft's own 1x is only 3 to 6 times the median level. That is barely a clear peak at this sensor's noise.
- **Consequence for `OTTAWA_RESULTS.md` (my inference, not checked):** every per-turn reading there (1x, 2x, envelope order) was taken against 120 Hz, not the shaft. That may explain part of the "misalignment" and "unbalance" names.

**Verdict:** fails the bar. Do not propose it yet. On this rig (one lab bearing rig, one real axis, unit assumed), vibration alone never gave the shaft speed, which is the case for a once-per-turn tachometer (plan step 6). Check the twice-mains guard on its own on a recording from our own rig before proposing it: today's reading has the same weakness on any motor with a strong hum.

### B. Tone removal before the envelope

Simulator, per 1 s window (seed 1 / seed 2):

| Case | Hits, today | Hits, whitened | AUC, today | AUC, whitened |
|---|---|---|---|---|
| motor bearing 0.3 | 0 / 0 | 60 / 48.5 | 0.41 / 0.25 | 0.73 / 0.69 |
| motor bearing 1 | 71 / 75 | 100 / 100 | 0.12 / 0.16 | 1.00 / 1.00 |
| idler bearing 0.3 | 0 / 0 | 4.5 / 6.5 | 0.65 / 0.46 | 0.54 / 0.57 |
| idler bearing 1 | 47.5 / 59 | 88 / 86 | 0.57 / 0.57 | 0.98 / 0.98 |

The healthy windows' median prominence falls from 28 to 4.7. The gear mesh's sidebands dominate today's envelope, so today's prominence falls when a bearing fault appears (AUC under 0.5).

In the simulator, the motor bearing passes the bar. The idler bearing at 0.3 does not: its AUC drops on seed 1 and rises on seed 2.

Ottawa:

| | Today | Whitened |
|---|---|---|
| Median AUC, 40 fault files | 0.74 | 0.50 |
| Fault files off the shaft orders (of 40) | 18 | 27 |
| Healthy files off the shaft orders (of 20) | 10 | 17 |

**Verdict on the bar as written:** fails all three parts. But the controls below show the measure itself does not work on this set, so this is not evidence against whitening.

**Controls, all post hoc** (`ottawa_controls.py`, `results/ottawa_controls.json`). Two problems with the run above. `as_device` puts the real signal on axis 0 only, and axes 1 and 2 are added noise, which whitening weights equally. And nothing checked whether separation comes from the fault or from simply being another recording. So, on axis 0 only, median separation |2 AUC - 1|:

| Signal | Envelope | Healthy vs fault | Healthy, half vs half | Healthy vs another healthy bearing |
|---|---|---|---|---|
| 1 kHz, MPU noise | today | 1.00 | 0.11 | 0.99 |
| 1 kHz, MPU noise | whitened | 0.28 | 0.17 | 0.13 |
| 1 kHz, no noise | today | 1.00 | 0.18 | 1.00 |
| 1 kHz, no noise | whitened | 0.45 | 0.18 | 0.46 |
| 42 kHz raw, 2-10 kHz | today | 0.99 | 0.25 | 0.82 |
| 42 kHz raw, 2-10 kHz | whitened | 1.00 | 0.15 | 0.93 |

- **Today's envelope tells two healthy bearings apart as well as healthy from faulty.** Its "separation" on Ottawa is mostly the recording (each bearing mounted and run separately), not the fault.
- **Whitening at 1 kHz** separates fault files a little more than another healthy bearing with the MPU's noise (0.28 against 0.13), and no more without it (0.45 against 0.46). There is no clear fault signal either way.
- **At 42 kHz** both reach 1.0 against faults and 0.8-0.9 against another healthy bearing. The set cannot settle it there either.
- **So Ottawa, as converted, can't judge B.** A fair test needs the same machine before and after a fault, with no remount: our own rig (plan test 8) or a run-to-failure set such as NASA IMS.
- **Possible knock-on for `OTTAWA_RESULTS.md` (inference, not checked):** its 35 of 40 caught compared each bearing's healthy file with its fault files. If the recording alone moves the readings this much, part of that catch rate may be the remount, not the fault. Track 3 may want a healthy-against-another-healthy check there.

### C. Envelope spectra averaged over 60 s

Simulator, AUC per minute, median of sixty 1 s readings against the averaged spectrum (seed 1 / seed 2):

| Case | Plain, median | Plain, averaged | Whitened, median | Whitened, averaged |
|---|---|---|---|---|
| motor bearing 0.3 | 0.33 / 0.06 | 0.35 / 0.12 | 0.78 / 0.78 | 1.00 / 0.98 |
| idler bearing 0.3 | 0.57 / 0.32 | 0.60 / 0.33 | 0.87 / 0.71 | 0.90 / 0.78 |

Averaging alone moves AUC by under 0.1. On whitened windows it adds 0.2 for the motor bearing and lifts per-minute hits from about 50 % to 97-100 %.

**Verdict:** passes in the simulator only together with whitening, which failed on real data. Not supported for the current sensor. The Ottawa files are only 10 s long, so C could not be run on them.

## What this changes in the plan

- **Section 6 of `docs/research/research-grade-claim.md`** lists tone removal as needing "nothing new". It is still untested on real data: the simulator favours it, and Ottawa cannot judge it. Test it on the rig's own before-and-after recordings.
- **The tachometer (step 6)** has a measured reason now: on this rig, the device never found the shaft speed.
- **Rerun B and C** on the first IIS3DWB recordings, and on Ottawa converted to the new sensor's band (plan test 10). The code here takes any rate.
- **A twice-mains guard on today's speed reading** is the one small change worth discussing now. It needs a recording from our own rig first.

## Limits

- Two simulator seeds, and 200 windows or 30 minutes per case.
- Ottawa is 10 s per file, one rig, constant speed. Its unit is assumed to be m/s² (as in `datasets/ottawa.py`).
- AUC here is for one reading against healthy, not the full system's catch rate. No end-to-end run (`conveyor_eval/system_search.py`) was done, because no method passed.

## How to rerun

```
python3 -m pytest training/record/noise_rejection/tests                                     # seconds
python3 -m training.record.noise_rejection.lab --seed 1                                     # about 3 minutes
python3 -m training.record.noise_rejection.ottawa_check /path/to/ottawa-uods                # about 30 s
python3 -m training.record.noise_rejection.ottawa_controls /path/to/ottawa-uods             # a few minutes
```

The Ottawa folder must hold one subfolder of the `.mat` files from [Mendeley Data, y2px5tg92h](https://data.mendeley.com/datasets/y2px5tg92h) (about 290 MB; not committed).

## NASA IMS run-to-failure: bars, written 17 Nov 2026 before the run

Why this set: Ottawa could not judge B, because each bearing's healthy and fault files are separate recordings. In IMS, one shaft runs from healthy to failure without a remount, with healthy bearings on the same shaft (Qiu et al. 2006). Raw snapshots: 1 s at 20,480 Hz every 10 minutes (every 5 minutes for the first part of test 1).

Bearings, and the defect rate each failure should pulse at (2,000 rpm; Rexnord ZA-2115 rates as commonly quoted from the dataset's documentation, secondary, not checked against the PDF):

| Bearing | Failure | Rate checked |
|---|---|---|
| test 1, bearing 3 | inner race | BPFI 296.9 Hz |
| test 1, bearing 4 | roller | BSF 139.9 Hz or 2 x BSF 279.8 Hz |
| test 2, bearing 1 | outer race | BPFO 236.4 Hz |
| test 3, bearing 3 | outer race | BPFO 236.4 Hz |
| control: test 2, bearing 3 | none reported | its own BPFO, BPFI and BSF, for false hits |

Windows: early = the first 20 % of snapshots, late = the last 10 %. At most 150 snapshots each, evenly spaced. Signals: the device conversion of `datasets/ims.py` (1 kHz, its 260 Hz low-pass, its noise; real signal on one axis), envelope band 100-450 Hz as today; and the raw 20 kHz signal with a 2-9 kHz envelope band, to test whether the kilohertz band is what B needs.

A hit is an envelope peak within 3 % of the rate checked.

**B passes at 1 kHz if,** on at least 2 of the 4 failing bearings:
- the late hit rate rises 20 points or more over today's, and
- the early hit rate (false hits before the fault) stays at 10 % or less.

On the control bearing, the late hit rate must also stay at 10 % or less.

**Separation, with a control:** |2 AUC - 1| of envelope prominence, early against late. It must be at least 0.3 higher on the failing bearings (median) than on the control bearing, for whichever envelope is judged. This tells fault from slow drift in the rig.

**C passes if** an envelope spectrum averaged over 6 consecutive snapshots (an hour, as sixty 1 s windows is not possible here) raises the late hit rate by 20 points or more over single snapshots, with the same envelope, on at least 2 failing bearings, and the false-hit limits above hold.

**The kilohertz question:** the same B bar on the 20 kHz signal. If B passes at 20 kHz and not at 1 kHz, the plan's section 6 should say tone removal needs the wide band.

## NASA IMS results

Run 17 Nov 2026 with `ims_check.py` (`results/ims_check.json`). The check marked post hoc was added after the main run (`--posthoc`, `results/ims_check_posthoc.json`).

**In short:** whitening (B) and averaging (C) fail at both sample rates. What works is today's plain envelope moved to a kilohertz band. On the two outer-race failures it finds the defect rate late in the run and almost never early. At 1 kHz it finds nothing on any bearing. This is the first measured support for the wide-band sensor (plan step 2). It does not support tone removal.

Hit % of single snapshots, early / late (hour averages in brackets, late only):

| Bearing | 1 kHz today | 1 kHz whitened | 20 kHz today | 20 kHz whitened |
|---|---|---|---|---|
| test 1, b3, inner race | 0 / 0 | 0 / 0 | 0 / 4 (7) | 0 / 3 (7) |
| test 1, b4, roller | 0 / 0 | 0.7 / 2 | 0 / 0 | 0 / 0 |
| test 2, b1, outer race | 0 / 0 | 0 / 0 | **2 / 100 (100)** | 76 / 98 (100) |
| test 3, b3, outer race | 0 / 0 | 0 / 0 | **4.7 / 40 (40)** | 7.3 / 32 (38) |
| control: test 2, b3 | 0 / 0 | 3.3 / 1.1 | 38 / 54 (36) | 18 / 19 (2) |

Separation |2 AUC - 1|, early against late:

| | 1 kHz today | 1 kHz whitened | 20 kHz today | 20 kHz whitened |
|---|---|---|---|---|
| failing bearings, median | 0.39 | 0.07 | 0.71 | 0.56 |
| control | 0.17 | 0.00 | 0.05 | 0.25 |

**Verdicts against the bars:**

- **B at 1 kHz:** fails. Neither envelope finds a defect rate on any bearing.
- **Separation:** fails at 1 kHz for both envelopes (failing minus control: 0.23 today, 0.07 whitened; the bar is 0.3). At 20 kHz, today's passes (0.71 against 0.05).
- **C:** fails. Hour averages add at most 6 points on a failing bearing (test 3, whitened, 32 to 38).
- **The kilohertz question:** B does not pass at 20 kHz either. It adds no late hits over today's envelope, and it raises early false hits on test 2, bearing 1 to 76 %. So the plan should not say tone removal needs the wide band. It should say the envelope needs it.

**Post hoc, IIS3DWB-like:** the raw signal plus the IIS3DWB's datasheet noise (75 ug/rtHz), envelope band 2-6 kHz, inside its 6.3 kHz band. Today's envelope, early / late hit %:

- test 2, bearing 1: 0.7 / 100
- test 3, bearing 3: 3.3 / 40
- inner race and roller: still about 0

This is a rough stand-in. It does not model the chip's own response, its 26.7 kHz rate or its mount.

**Checks on the hits:**

- **The late peak is the outer race, not a shaft harmonic.** BPFO (236.4 Hz) is within 3 % of 7x shaft speed (233.3 Hz). But the late peaks sit at a median of 236 Hz (test 2) and 237 Hz (test 3), three 1 Hz bins from 7x.
- **The control's false hits come from the rate list, not a fault.** Four rates were checked, and 9x shaft (300 Hz) falls inside BPFI's 3 % window. Its early peaks sit at 15, 30, 280 and 301 Hz. So the 3 % window is too wide for the inner-race rate on this rig. A rerun should use 1 % or two bins.
- **Inner race and roller are not found at any rate or band.** Test 1, bearing 4 still separates at 20 kHz (0.98): something changes, but not at the rate checked.

## What the IMS run changes in the plan

- **Section 6 of `docs/research/research-grade-claim.md`**, two rows:
  - "Envelope analysis in a kilohertz band" now has measured support on two real outer-race failures. It is still unshown for inner race and roller.
  - "Tone removal before the envelope (cepstrum pre-whitening) | Nothing new" is not supported on any real set tried (Ottawa could not judge it; IMS shows no gain at 1 or 20 kHz).
- That doc belongs to track 5. This branch doesn't edit it.
- **No change to `effente/` is proposed.** The kilohertz envelope needs the new sensor first; at 1 kHz it finds nothing.

How to rerun:

```
python3 -m training.record.noise_rejection.ims_check /path/to/nasa-ims                # about 10 minutes
python3 -m training.record.noise_rejection.ims_check /path/to/nasa-ims --posthoc      # about 5 minutes
```

The folder layout is the one track 4 keeps (`effente-track4-validation/training/validation/data/nasa-ims`): `extract/test1/1st_test`, `test2/2nd_test`, `extract/test3/4th_test/txt`.
