# Model lifecycle: setup, calibration and learning over time

Status: the library and calibration are part of the hackathon demo (see the TRD). The rest is a proposal for after the hackathon. Owner of this doc: track 5. Model work it describes belongs to track 3, recordings to track 2, device changes to track 1.

The point of the library is convenience: a technician snaps the device on and gets a judgement in minutes, with no days-long learning period. Leaving the device on one machine for days is optional; it only sharpens the model. The device runs one small neural network per machine. Everything bigger, including a fleet of agents, runs on the laptop platform.

## Summary

Without a library, a device must learn each machine from scratch before it protects anything. That takes days to a month of recording, which makes a handheld impractical, and the fault names it gives are guesses that have never been checked against real faults.

This spec replaces that with a guided lifecycle:

1. A setup chat on the dashboard collects facts about the machine.
2. A fixed lookup picks a starting model from a curated library and sets general safety rules.
3. The device protects the machine from minute one with those rules, then calibrates the library network to this machine in minutes.
4. Every alarm asks the customer "real problem or false alarm?"
5. An agent on the dashboard watches the data over time and recommends model changes, backed by replays of past data, for the customer to approve.
6. Confirmed real faults from every customer flow back into the library and the fault namer.

## Why

What the current model does (branch `track2/data-intake`, `effente/trainer.py` and `effente/model_runtime.py`):

- One small autoencoder per machine, trained on the device on that machine's normal running. It scores each minute by how badly it can rebuild it.
- An alarm when the median score of the last few minutes crosses a line set for about one false alarm a week.
- 21 general fault types, written by hand as "these readings rise or fall", plus a small classifier trained on fake faults added to the machine's own normal data.

Its gaps, which this spec addresses:

| Gap | Effect on the customer |
|-----|------------------------|
| No protection until learning finishes | Days to a month with nothing watching the machine |
| Learning must cover every normal way the machine runs | Customers must know and run every mode during setup |
| Fault names never checked on real faults | Specific names may be wrong, which costs trust |
| A fixed model per machine | False alarms or blind spots when the workload changes |
| No feedback from the customer | No way to learn which alarms were right |

## Goals

- Useful protection within minutes of mounting the device.
- A calibrated judgement within minutes of snapping the device on.
- A long stay on one machine stays optional, never required.
- Model changes that are measured, explained, approved by the customer, and reversible.
- A steady flow of confirmed, labelled real faults recorded with our own device.
- Never claim more than has been measured.

## Non-goals

- Any agent or large model on the device. The device runs only the small per-machine network and the day-0 rules.
- The agents changing models on the device without the customer's approval.
- Any accuracy or lead-time target. Targets are set once the benchmark set exists (see [Validation](#validation)).

## The customer cycle

```
 mount ──> setup chat ──> day-0 rules live ──> calibration ──> daily use ──> (optional) own model
  5 min      5-10 min       from minute one      minutes             │          after a long stay
                                                   │                             │
                                         fit check fails?               alarm ──> "real or false?"
                                         ask, re-pick or                         │
                                         learn from scratch                      v
                                                                       review agent (weekly + on change)
                                                                         replay ──> recommend ──> approve
                                                                                                    │
                            library and fault namer <── confirmed real faults, from every customer
```

Times are starting values, not measured.

| Stage | Customer sees | System does |
|-------|---------------|-------------|
| 1. Mount | Attaches the device, powers it, it appears on the dashboard | Device connects to the platform |
| 2. Setup chat | Answers questions about the job and the machine | Setup agent fills in the [machine profile](#machine-profile) |
| 3. Day-0 rules | "Watching for serious problems now. Learning this machine's normal." | Lookup picks a library model and [day-0 rules](#day-0-rules); platform delivers both to the device |
| 4. Calibration | Progress, then "Calibrated" or a question | Device learns this machine's levels on top of the library model, then runs the [fit check](#fit-check) |
| 5. Optional long stay | Status change: more sensitive | If the device stays on, it trains a model on this machine alone and switches to it |
| 6. Daily use | Alarms with a fault group, evidence and one question: real or false? | [Feedback](#alarm-feedback) is stored with the alarm |
| 7. Workload change | "Did something change around Tuesday?" then a recommendation with replay numbers | [Review agent](#review-agent) |
| 8. Slow fault | An alarm, even though the drift was gradual | [Learning rules](#learning-over-time) keep the drift out of normal |
| 9. More machines | Faster setup | Site facts reused from the first profile |

## Machine profile

The setup agent's only output. A fixed form with a schema. A field the customer does not know stays empty: the agent asks, or leaves it blank, and never guesses a number.

| Field | Example | Used for |
|-------|---------|----------|
| `machine_type` | `conveyor`, `fan`, `pump`, `motor`, `other` | Library lookup |
| `drive` | `electric_motor_direct`, `belt`, `gearbox` | Library lookup, fault types that apply |
| `speed_rpm` | 1450, or a range | Where once-per-turn and twice-per-turn peaks should sit |
| `size_class` | ISO 10816/20816 machine group | Day-0 vibration zone |
| `mounting` | `rigid`, `flexible` | Day-0 vibration zone |
| `operating_modes` | `["idle", "full_load"]` | How many normal states to expect during calibration |
| `schedule` | shifts, planned stops | Stops are not faults |
| `sensor_location` | `motor_drive_end`, `gearbox`, `frame` | Which fault types the device can see from there |
| `room_noise` | `quiet`, `loud`, `very_loud` | How much to trust sound |
| `site_id` | `plant-a` | Reuse of site facts for the next machine |

The profile is stored on the platform and sent to the device with the model.

## Moving the device between machines

The same handheld can watch one machine for days, then move to the next. The network is per machine, so:

- The device keeps one model per `machine_id` (each about 50 kB) and loads the right one when it is attached. The technician picks the machine on the device, or the platform tells it.
- Re-attaching changes the readings a little, even on the same steel probe point (the TRD's `mount_id`). After each attachment the device runs a short re-calibration of the machine's levels before judging. Day-0 rules apply meanwhile.
- A machine seen for the first time goes through setup.

## Setup agent

- One of the agents in the laptop fleet.
- Asks about the work and the machine in plain words. Accepts "I don't know".
- Output is a profile checked against the schema. It does not pick the model. The [lookup](#model-selection) does.
- Shows the customer the filled profile to confirm before anything is sent to the device.
- Offline: the customer fills the same form by hand.

## Day-0 rules

General limits that hold for any machine, live from the first minute, before any learning. They only catch serious problems.

| Rule | Reading | Notes |
|------|---------|-------|
| Vibration severity zone | `vib_velocity_mm_s` against the ISO 10816/20816 zone for `size_class` and `mounting` | The feature covers 10-250 Hz only, while the ISO band is 10-1000 Hz, so zones are approximate. Treat as unverified until checked on the test rig |
| Temperature limit | `th_max_c`, `th_hotspot_c` | Absolute limits per machine type, from manufacturer guidance where the profile names one |
| Impacts | `trend_shocks_10m`, `trend_bangs_10m` | Already relative to the machine's own recent past |
| Alarm or beeping | `snd_tone_mod_db` | An existing fault type that needs no learning |

Day-0 rules stay on after calibration as a floor under the learned model.

## Model library

A curated set of starting models, each built on the device's exact feature list (`features_version` in the model file).

Each entry holds:

- the model file (current `effente-model-1` format)
- the profile fields it suits
- its fit check numbers (see below)
- a scorecard on the [benchmark set](#validation): false alarms per week on normal device data, and which rig faults it caught and how early
- the data it was trained on, by source

Rules:

- An entry ships to customers only with a scorecard measured on device data. The hackathon `conveyor` entry is a demo entry, trained on the conveyor it is demoed on.
- Starting entries, in order: `generic_rotating`, `conveyor`, `fan`, `pump`. Grow only as real recordings arrive.
- Entries are versioned. A device always records which entry and version it runs.

## Model selection

A fixed lookup from profile to library entry and day-0 rule settings. Not left to the agent's judgment, so it is predictable, testable, and a wrong choice can be traced.

- Match on `machine_type`, then `drive`.
- No match: `generic_rotating`.
- The lookup table lives with the library (track 3) and has its own tests.

## Delivery to the device

- The device only makes outgoing requests ([CONTRACT.md](../platform/CONTRACT.md)). So the device pulls: a new request, for example `GET /api/devices/{device_id}/model`, returns the chosen model file, the profile and the day-0 rule settings. An upload reply can flag `"new_model": true`, the same way it flags `need_model` today.
- The model file is about 50 kB.
- The device already refuses a model built on a different feature list (`features_version` check in `effente/model_runtime.py`). Keep that.
- The device keeps the previous model for rollback.

## Calibration

A new Calibrating mode in the device app (see the TRD's device modes).

- Calibration starts automatically once an entry is picked.
- Calibration learns this machine's averages and spreads (`Prep`) and its alarm line, keeping the library model's network. Starting value: 5 minutes of running (`MIN_RECORD_S`, the trainer's current minimum). Every mode in `operating_modes` must be seen, so a machine with several modes needs longer. Measure how short it can go.
- If a mode in the profile was not seen, the dashboard says which one and asks the customer to run it.

## Fit check

After calibration, the device checks whether this machine looks like what the library model expects.

- Score the calibration data with the library model. Compare it to the entry's own fit check numbers.
- Fits: "Calibrated. Your conveyor looks normal."
- Does not fit: the agent asks, for example "This doesn't behave like a typical conveyor. Is it under heavy load, or a different machine?" It then offers another entry, or falls back to learning from scratch, which takes longer.
- The pass/fail rule is open (see [Open questions](#open-questions)).

## Optional: own model after a long stay

- Only if the device is left on the machine. Once enough normal running is recorded, the device trains a model on this machine alone (`effente.trainer.train_model`), starting from the library entry's network instead of random weights.
- How much data that needs, with a library start, is not yet measured. Measure it on the rig.
- Day-0 rules stay on.

## What the dashboard shows

Protection level is always visible, so the customer sees it grow instead of waiting:

| Level | Shown as |
|-------|----------|
| Day-0 rules only | "Watching for serious problems. Learning this machine." |
| Calibrated | "Calibrated. Watching for changes from this machine's normal." |
| Own model (optional) | "Trained on this machine." |
| Model under review | "A model change is waiting for your approval." |

## Alarm feedback

The most valuable data in the loop. Build it before the review agent.

- Every alarm asks one question: **real problem** or **false alarm**. One tap, on the dashboard or the device.
- For a real problem, a follow-up some days later: "What did you find?" with common answers (loose bolt, worn bearing, belt, misalignment, overheating, other: free text).
- Stored with the alarm: the verdict, the cause, who answered, when, and the model entry and version that raised it.
- A reminder, not a nag, for alarms left unanswered.
- Contract version 2.1 (section 6 of [CONTRACT.md](../platform/CONTRACT.md)) defines the alarm and the answer. The platform works out alarms from the records, so the device needs no change. The finding is free text for now; the dashboard can offer the common answers as ready-made text. A fixed list of causes waits until there are enough answers to see which ones come up.

## Learning over time

The risk: a fault that grows slowly, like a bearing wearing over weeks, gets learned as the new normal, and the alarm never fires. Rules against it:

- Only minutes with no alarm and no unanswered alarm are used for learning.
- The original calibrated baseline is kept frozen. Every minute is also scored against it.
- A steady upward trend in heat or vibration (`trend_heat_rise_per_day`, `trend_heat_rise_per_week`, `trend_vib_10m` and similar) is never learned as normal.
- Larger changes to normal need the customer to confirm "this is normal now". Nothing large is absorbed silently.

## Review agent

One of the agents in the laptop fleet. Recommends; never acts on its own.

When it runs: weekly, and when the model's fit gets worse, or false alarms rise.

What it looks for:

| Pattern | Likely meaning |
|---------|----------------|
| A sudden step, many readings shifting together in a way that fits the load (speed and overall vibration rising together), then stable | Workload change |
| A slow drift in a few specific readings (one bearing frequency, one hot spot) that keeps growing | Developing fault |
| Unclear | Ask the customer: "Did anything change on the line around Tuesday?" |

What it recommends, with evidence:

- Replay stored history through each candidate: retrain on recent data, another library entry, or no change.
- For each, show two numbers: false alarms it would have raised, and whether it still catches every past alarm the customer confirmed real.
- The agent explains these numbers in plain words. The numbers decide, not the agent's judgment.
- The customer approves. The old model is kept for rollback. Every change is logged with who approved it and why.

Replay needs per-minute feature rows kept over time. The device keeps them while recording (`device/intake/recorder.py`) and keeps rolling history (`effente/history.py`), but the platform stores only log records today. Where replay data lives is open.

## Fault names

Report what has been measured, nothing more:

- **Fault group** first: vibration, heat, sound, or sensing (a problem with the device or its view). This part is reliable.
- **Specific type**, like `unbalance_like`, shown plainly only once it has caught that fault on the rig or in a pilot. Until then, "possible unbalance".
- A per-type scorecard, in the library, records which types are validated and on what data.

## Data plan

Everything above depends on real faults recorded with our own device.

| Source | What it gives | Owner |
|--------|---------------|-------|
| Fault test rig | Labelled faults on our own sensors, made on purpose: added weight (unbalance), offset coupling (misalignment), loosened bolt (looseness), a bearing with a known defect, a slack belt, blocked airflow (overheating). The demo conveyor can be the start. | Tracks 2 and 3 |
| Shadow-mode pilots | Real machines, real workloads, real faults with causes. A handful of sites: campus HVAC fans, a makerspace, a local shop's conveyor. Alarms are shown and answered, not relied on. | Track 5 to recruit, track 2 to record |
| Public datasets converted to our sensor | Variety for pretraining. NASA IMS (already converted for the MPU6050; redo for the MPU-6500), CWRU bearings, MIMII machine sounds. Fine-tune on rig and pilot data after. | Track 2 to convert, track 3 to train |

Record all training data with the MPU-6500 and never mix it with MPU6050 data (TRD).

## Validation

- One fixed benchmark set: rig recordings and pilot recordings, held back from training. Every model change, library entry and fault type is scored against it.
- `training/anomaly/bench.py` (on `track2/data-intake`) is the start: it already compares 8 detectors on simulated and public data. Extend it to device data.
- Measured per library entry: false alarms per week on normal data; per rig fault, whether it was caught and how early; how much calibration data it needed.
- Measured for the whole lifecycle: time from mount to calibrated; share of fit checks that pass on the right entry and fail on the wrong one.
- No targets until the first measurements exist.

## Changes needed

| Area | Change | Owner | Needs all-team agreement |
|------|--------|-------|--------------------------|
| Model file | Add library entry id and version, and the profile | Track 3 | Yes: shared format |
| Log record | Add model entry and version, protection level. An alarm id from the device is optional: contract 2.1 has the platform work it out | Track 3 and 5 | Yes: shared format |
| Contract | Model pull request, `new_model` flag (version 2 already has the new diagnosis names; version 2.1 has the feedback endpoint) | Track 5 | Yes |
| Device | Auto-start recording after setup, model pull and rollback, one model per machine with re-calibration on attach, day-0 rules, protection level on the screen | Track 1 | No |
| Trainer | Calibrate on top of a library network; start full training from it | Track 3 | No |
| Platform | Profile store, feedback store, replay data store, review agent | Track 5 | No |
| Dashboard | Setup chat, protection level, feedback question, approval of model changes | Track 5 | No |
| Library | Entries, lookup table, scorecards | Track 3 | No |

## Rollout order

0. Hackathon: library entries `conveyor` and `generic_rotating` on the device, picked with the button, calibration and the fit check.
1. Fault test rig and benchmark set. Without these nothing else can be measured.
2. Alarm feedback, with the honest fault names. Useful from the first customer.
3. Day-0 rules, checked on the rig.
4. Machine profile, setup chat, lookup, delivery of entries to the device.
6. Shadow-mode pilots, in parallel with steps 2 to 5.
7. More library entries as their scorecards are measured.
8. Learning over time, then the review agent with replay.

## Decisions

Agreed on 2026-11-12. The PRD, TRD, architecture doc and contract version 2 are updated to match, pending the team's sign-off:

1. **Diagnosis names and data.** Finn's runtime names (`warming_up`, `stopped`, `normal`, the 21 fault types in `effente/fault_types.py`, `unrecognised_pattern`) and his data formats replace the contract's four diagnoses. The dashboard labels and the agent rules move to this list, grouped by fault group.
2. **No switch-off tests.** Explanations are the input readings that moved, with how far they moved (for example `vib_1x` up 7 spreads), as `effente/model_runtime.py` produces them. The switch-off toggle, the feature mask and the explanation table are dropped.
3. **Agents on the laptop.** The device runs only the small per-machine network and the day-0 rules. All agents run as a fleet on the laptop platform and read the platform's stored data directly. The agent code moves from `device/agent/` to the laptop.

## Open questions

1. **Fit check rule.** What score difference counts as "does not fit". Set from rig data.
2. **Replay data.** Device, platform, or both, and how long it is kept.
3. **Pooling customer data.** Confirmed faults from every customer train the shared library. Needs customer consent and a written data policy before pilots start.
4. **Day-0 vibration zones.** How far the 10-250 Hz partial-band velocity can stand in for ISO zones on the MPU-6500. Check the sensor's filter setting and test on the rig.
5. **Pricing.** Feedback, review and the library are ongoing services. Ties to the PRD's open pricing question.
