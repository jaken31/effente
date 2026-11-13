# Real bearing faults: does the device catch them and name them?

Measured 12 Nov 2026 with [ottawa.py](ottawa.py) on the University of Ottawa bearing dataset (UODS-VAFDC). The dataset has 20 ball bearings at constant load and speed. Each was recorded healthy, then with a developing fault, then a full fault: inner race (bearings 1–5), outer race (6–10), ball (11–15) and cage (16–20).

## How
- **Data:** each recording was turned into what our device would see: resampled to 1 kHz, put through the MPU's low-pass, with its noise and gravity added, and clipped at ±8 g. Vibration only, because the device has no microphone yet.
- **Learning:** each bearing's normal was learned from the first 6 s of its healthy file, with the device's own trainer.
- **Judging:** the last 2 s of the healthy file (kept apart from the learning), the developing fault file and the full fault file. They were judged with the device's own `Watcher`, one row a "second".
- **Caught** means at least half the judged rows said something is wrong.
- **Named right** means `bearing_defect_like`, scored on the name given most often. Our fault types don't split bearing faults into inner race, outer race, ball and cage. "Related" means `vibration_too_high`, `impacts`, `repeating_knock`, `grinding_or_hiss` or `unsteady_vibration_or_sound`.

## Results

| Sensitivity | Developing faults caught | Full faults caught | Healthy rows called wrong | Healthy bearings flagged |
|---|---|---|---|---|
| 0.25 | 17/20 | 17/20 | 0 % | 0/20 |
| **0.5 (default)** | **18/20** | **17/20** | **1 %** | **0/20** |
| 0.75 | 19/20 | 19/20 | 24 % | 5/20 |

**Naming, at the default sensitivity (35 caught files):**

| Named | Files |
|---|---|
| `bearing_defect_like` (right) | 1 |
| Related (`vibration_too_high`, `impacts`, `repeating_knock`) | 17 |
| Wrong: `misalignment_like` 8, `unbalance_like` 4, `speed_change` 3, `device_moved` 1, `unrecognised_pattern` 1 | 17 |

`bearing_defect_like` came up at all, on any row, in only 1 of 40 fault files.

**Safety checks** (`effente/safety.py`, without the 10 s hold, since the files are only 10 s long): `vibration_above_normal` was over its line on 80 % or more of the rows in 9–10 of 20 fault files of each level, and `knocking` in 1. None of the healthy held-out parts went over a line.

**What the device says.** Since 12 Nov the screen describes what is going on instead of guessing a cause. The description comes straight from which groups of readings left normal (`describe` in `effente/fault_types.py`), with two guards: no "speed changed" when the vibration's shape changed broadly, and no "device moved" when the vibration rose or clipped. At the default sensitivity, of the 35 caught files:

| Says | From what moved (now) | Through the fault type (before) |
|---|---|---|
| KNOCKING OR JOLTS | 22 | 8 |
| UNUSUAL VIBRATION | 10 | 13 |
| VIBRATION TOO HIGH | 3 | 9 |
| OUTSIDE NORMAL (vague) | 0 | 1 |
| SPEED CHANGED (misleading) | 0 | 3 |
| DEVICE MOVED (misleading) | 0 | 1 |
| **Accurate** | **35 of 35** | 30 of 35 |

Every description of a vibration problem counts as accurate for a bearing fault. Knocking fits too: a damaged bearing sends a pulse each time a ball passes the damage. At the other sensitivities it's 34 of 34 and 38 of 38.

**Caution:** every fault here is a bearing fault. So this shows the descriptions aren't misleading, not that they tell different problems apart, such as an unbalanced blade from a loose mount. That needs a mixed set of faults: your own rig, or MaFaulDa.

**Also in the data:** the ball-fault files (bearings 11–15) record a load of 0, against 400 in their healthy files. So part of what changed there may be the load, not the fault. The speed stayed within 1 % on all bearings except the full cage faults on bearings 16 and 17, where it dropped by 3–4 %.

## What it means
- **Detection works.** At the default sensitivity, the device caught 35 of 40 faults and flagged none of the 20 healthy bearings.
- **Sensitivity:** 0.75 catches 2 more faults but flags 5 of the 20 healthy bearings. So 0.5 looks like the right default and 0.75 is too jumpy, at least on data this short.
- **Naming bearing faults doesn't work yet.** Half the names are related but vague, like "vibration too high", and half are wrong, mostly "misalignment" or "unbalance". The likely reason is that `bearing_defect_like` is recognised from the envelope of 100–450 Hz vibration and from high-pitched sound. Bearing impacts ring mostly at kilohertz frequencies, which a 1 kHz accelerometer can't see, and the device has no microphone. What the device does see is energy at whole multiples of the running speed, which the signatures read as unbalance (1×) or misalignment (2×). This explanation is a hypothesis, not tested yet.

## Limits
- Only 10 s per bearing, against the device's 5 minutes or more of learning, and only 2 s of held-out healthy data. The false-alarm rates are rough.
- The accelerometer's unit is assumed to be m/s². The files don't say, so this is unverified.
- Constant speed and load. A real machine varies more, which would raise the false alarms.

## Next steps worth trying
1. **Done:** describe what is going on, straight from what moved, with guards against the misleading "speed changed" and "device moved". Next, test that the descriptions tell different faults apart, on a mixed set (your rig: coin, loose screw, tapping, slowing it, heat; or MaFaulDa).
2. **Recognise bearing faults at 1 kHz** from energy at bearing defect orders: rates of about 3–6× running speed that aren't whole multiples.
3. **Fit a microphone.** The bearing and grinding signatures depend on high-pitched sound.
