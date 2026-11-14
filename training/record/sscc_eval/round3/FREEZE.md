# Round 3 final candidate: FROZEN 2026-11-14 12:02 EST (machine clock), BEFORE any round-3 scoring of med load

No round-3 script has scored a med-load condition (run.py, drift.py, multispeed.py assert/loop over heavy + light only;
make_spec.py / make_frames.py computed features for every folder, med included, but nothing was fitted or scored on med).

Configs tried on the selection set (heavy + light) before this freeze: 149 distinct round-3 configurations
(res/*.json minus the two BASE rows), plus normal-only shift tests (drift.py, 36 runs) and 4 several-speeds runs.

## The candidate ("hyb60" in the report)
Harness name: `hyb:30:600@s+st@s|maxk=60`
- sound row per second (16 kHz, 1 s Hann FFT, same scaling as band_model.band_features): 30 log bands on
  geomspace(50, 600, 31) Hz, then 148 linear 50 Hz bands 600-8000 Hz (edges 600, 650, ..., 8000), then log10 rms,
  kurtosis, crest = 181 values;
- one sound group; robust scaling (median / IQR/1.349, clip +-8); per group PCA (var 0.95, max_k 60); score = mean
  squared residual / learning mean + mean T2; 5 contiguous blocks in time, out-of-sample block scores, group level =
  median of out-of-sample scores; judging = mean of the 5 block models; line = 99.5th percentile of the running
  median-of-3 of out-of-sample scores; alarm when the median of the last 3 seconds is over the line.
- groups fused by MAX (only one group on SSCC; the vibration group would join under lin50's 6 dB / 25 % gate).

Selection set (heavy + light) as frozen: 0/350 FA; without screwdrop 98.6 % (clean 98.9, noise 98.3);
heavy 0/176 98.2 % (clean 99.8, noise 96.5); light 0/174 99.0 % (clean 98.0, noise 100); other-load FA 3 %,
reverse split 24 %.

## Planned med run (ONCE): `python final_med.py` -> logs/final_med.log, res/final_med.json
Scores on med load: BASE log64, BASE lin50 (presets as shipped) and the frozen candidate via round3_model.py.
