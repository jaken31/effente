# Signal methods that need no new hardware: simulator check

Everything here is simulated (`ConveyorSim(realistic=True)` with `SiteNoise`, factory profile) unless a row says otherwise. Nothing was run on a device recording. Levels in the simulator are partly unverified (`training/record/conveyor_eval/REALISM.md`), so these results say which method is worth trying on real data. They do not say how much it helps on a real machine.

This follows section 6 of `docs/research/research-grade-claim.md`. Each method is a candidate change to `effente/features.py`, so none goes in without the five-person agreement (AGENTS.md, Shared code). The prototypes in `lab.py` work on the raw windows and leave `effente/` unchanged.

## Which gaps these methods target

The plan's section 6 listed methods without tying them to a measured gap. What the repo already measured:

| Gap | Where it is measured | Method tried here |
|---|---|---|
| Speed reads double under misalignment, so every per-turn reading is off by 2 | `conveyor_eval/REALISM.md`, "Known weakness" | A. Speed from the harmonic family, not the single strongest peak |
| Subtle motor bearing and idler bearing are missed in every rotation | `conveyor_eval/SYSTEM_SEARCH.md`, "What A still misses" | B. Tone removal before the envelope (cepstrum pre-whitening). C. Envelope spectra averaged over 60 s |
| Bearing faults named `bearing_defect_like` in 1 of 40 Ottawa files | `datasets/OTTAWA_RESULTS.md` | B, on the Ottawa files (not run yet: the data is not on this machine) |

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

**A on real bearings (Ottawa), added after B's Ottawa run, before this one.** The files log the shaft speed, so the speed reading can be checked against it. Same conversion and windows as above, all 60 files.

Passes if: the harmonic-family reading is more than 3 % off in no more windows than today's, in each of the five groups (healthy, inner race, outer race, ball, cage), and in 5 % of windows or fewer overall.

## Results

Run 17 Nov 2026. Simulator seeds 1 and 2 (site noise seeds 1001 and 1002): `results/sim_seed1.json`, `results/sim_seed2.json`. Ottawa: `results/ottawa_check.json`.

**In short:** none of the three methods is ready to propose for `effente/`. The simulator flattered all of them, and the real bearings showed why.

- **A** fixes the doubled speed in the simulator. On real bearings, neither today's speed reading nor the candidate finds the shaft speed.
- **B and C** look large in the simulator and fail on real bearings.

The one firm finding is about the hardware: speed needs a tachometer, and the envelope methods need the kilohertz band. That matches steps 2 and 6 of the plan's order of work.

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

**Verdict:** fails the bar. Do not propose it yet. The case for a once-per-turn tachometer (plan step 6) is now measured, not argued. Check the twice-mains guard on its own on a recording from our own rig before proposing it: today's reading has the same weakness on any motor with a strong hum.

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

**Verdict:** fails all three parts of the bar. Separation falls to chance, and the healthy files go off-order as often as the faulty ones, so the extra off-order files are noise, not bearing rates.

Checked post hoc: the cause is not the MPU's 218 Hz low-pass. Inside the passband, separation |2 AUC - 1| is:

| Band | Today | Whitened |
|---|---|---|
| 100-450 Hz | 1.00 | 0.12 |
| 100-210 Hz | 0.89 | 0.14 |
| 30-210 Hz | 0.93 | 0.17 |

The likely reason: at 1 kHz, what changes with a bearing fault is mostly the shaft-locked tones, and whitening removes exactly those. This is the same mechanism `OTTAWA_RESULTS.md` proposed for the naming failure.

The simulator's bearing is pulse-modulated noise, the textbook case envelope analysis is built for. Real bearings at 1 kHz don't look like that.

### C. Envelope spectra averaged over 60 s

Simulator, AUC per minute, median of sixty 1 s readings against the averaged spectrum (seed 1 / seed 2):

| Case | Plain, median | Plain, averaged | Whitened, median | Whitened, averaged |
|---|---|---|---|---|
| motor bearing 0.3 | 0.33 / 0.06 | 0.35 / 0.12 | 0.78 / 0.78 | 1.00 / 0.98 |
| idler bearing 0.3 | 0.57 / 0.32 | 0.60 / 0.33 | 0.87 / 0.71 | 0.90 / 0.78 |

Averaging alone moves AUC by under 0.1. On whitened windows it adds 0.2 for the motor bearing and lifts per-minute hits from about 50 % to 97-100 %.

**Verdict:** passes in the simulator only together with whitening, which failed on real data. Not supported for the current sensor. The Ottawa files are only 10 s long, so C could not be run on them.

## What this changes in the plan

- **Section 6 of `docs/research/research-grade-claim.md`** lists tone removal as needing "nothing new". On this evidence, it needs the kilohertz band (plan step 2) before it is worth trying again.
- **The tachometer (step 6)** has a measured reason now: on the only real set, the device never found the shaft speed.
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
```

The Ottawa folder must hold one subfolder of the `.mat` files from [Mendeley Data, y2px5tg92h](https://data.mendeley.com/datasets/y2px5tg92h) (about 290 MB; not committed).
