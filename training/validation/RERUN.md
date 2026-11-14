# What to rerun, and what to record for it

Nothing in [REPORT.md](REPORT.md) comes from a device recording. Checks 1 to 6 use the simulators. Check 7 uses public data converted to look like an MPU6050, not the MPU-6500 the device has.

## When track 3's code changes (no new data)

The numbers were made with track 3's library entries (`training/library/entries`, merged in PR 33; versions in each manifest) and `effente.calibration` as it stands on `track3/calibration` (PR 42). `training/validation/track3.py` is the only module that imports them.

PR 42 is still a draft and its threshold rule is not decided. When it changes or merges, rerun everything.

Rerun `python3 -m training.validation.run_all` when any of these changes:

- an entry is rebuilt (for example `conveyor` with real recordings of the demo conveyor)
- calibration changes what it refits, or its threshold rule (`THRESHOLD_MEDIAN_MULTIPLE`, provisional)
- the fit check rule changes (`effente.calibration.FIT_MAX_RATIO`, a starting value set on the simulators). Check 6's pass and fail shares are marked as resting on it until the TRD sets a rule
- calibration starts applying the manifests' `widen` factors or `hold` list (it reads only the model file today)

## When device recordings exist (for track 2)

The harness reads rows through `common.session()`. A loader for learn-mode folders (`effente.model_input.load_recording`) goes there; the checks do not change.

Record every session as a learn-mode folder (`device/intake/recorder.py`), one row a second, MPU-6500 only. Write down for each: machine, mount (which steel disc, and which attachment of the head), start time, and for a fault its kind, its size and the second it was applied.

| Check | Rerun on | Sessions | Faults | Mounts |
|---|---|---|---|---|
| 1. Held-out normal | Demo conveyor, and one other rotating machine for `generic_rotating` | Per machine: 1 calibration session of 15 minutes running (the first 5 are the calibration, the rest lets check 5 cut it). Then held-out sessions of 30 minutes, never calibrated on: 1 continuing on the same attachment, 3 after taking the head off and putting it back, 1 on another day | None | Same disc, head re-attached between sessions. Note each attachment |
| 2. Faults | Demo conveyor | Per fault and mount: 1 recording with 5 minutes calibration, 5 minutes normal, then 10 minutes with the fault, without stopping the recording. 3 repeats each | Weight on the drive roller (unbalance), loosened motor-mount screws (looseness), and any other fault the demo shows. Note the weight or the turns loosened | Same attachment as the calibration, and a fresh attachment |
| 3. A fault no type covers | Demo conveyor | As check 2, 3 repeats | The demo's "something unusual": a fault that is none of the 21 types. Write down what was done | As check 2 |
| 4. int8 vs trained network | A model trained on each machine's own recording (`effente.trainer.train_model`), and the library entries once the builder reports the number | The 15-minute sessions of check 1 | None | None |
| 5. Calibration length | The recordings of checks 1 and 2 | No extra, if the calibration sessions are continuous and at least 5 minutes | Those of check 2 | Those of checks 1 and 2 |
| 6. Fit check | Demo conveyor (right for `conveyor`) and at least 2 machines that are not conveyors (wrong for `conveyor`), for example a fan and a drill | Per machine: 5 minutes running, 3 times | None | A new attachment each time |
| 7. NASA IMS | The same public data, converted again for the MPU-6500 (the model lifecycle doc gives this to track 2) | No device session | None | None |

## Not in the harness, needs the physical rig

- Sensitivity sweep (TRD Validation table): imbalance with smaller and smaller weights, to find the smallest one detected. Record as check 2, one recording per weight.
- The running-speed search range (TRD signal pipeline, open question).
- Slow wear on our own sensor. Check 2 leaves out the simulators' wear faults (they grow over days), and check 7 is the only slow fault measured.
