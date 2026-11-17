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

## Results

Not run yet.
