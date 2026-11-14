# The effente device app (Arduino UNO Q)

One Arduino App Lab app: the sketch runs on the microcontroller, and the Python side runs our intake, model and stream on the board's Linux side.

```
plug in ──> DISPLAY ──button──> RECORDING ──button──> TRAINING ──done──> WATCHING ──button──> RECORDING ...
            readings              learning              on the board      checking for faults
            ↑ the whole time: readings and status on the OLED, and a live stream on port 8765 ↑
```

| Mode | Screen | What happens |
|------|--------|--------------|
| Idle (`display`) | `IDLE` and the time, vibration, peak, speed, temperature | Sensors are read and features computed; nothing is kept |
| Recording | `RECORDING` and the readings | Learn mode: feature rows, per-minute summaries, raw snippets and events go to `data/recordings/rec-…` |
| Training | `TRAINING`, then the percentage and stage | The model is trained on the board ([effente/trainer.py](../../effente/trainer.py), NumPy only). A press in the first 2 minutes of a recording doesn't stop it: learning carries on and the screen still says `RECORDING` (the reason is in `/latest` status as `message`, and a `stop_refused` event). A short recording (1 s periods) needs 100 rows; if fewer than that count as running (the device on something still), it learns from every row and the training report says so (`learned_from_all_rows`). A training that fails goes back to `RECORDING` with the reason in status and a `training_failed` event, never on the screen. A day or more trains from the per-minute summaries, so a week fits in memory |
| Detecting (`watching`) | `DETECTING`, or what is wrong (`UNBALANCE LIKE`, `OUTSIDE NORMAL`, …) and the reading that moved most | Judges every summary period: 1 s after a short recording, up to a minute after a long one |

Pressing the button while in inference starts a new recording, to relearn. After a power cut, the app resumes what it was doing. If it was in inference, it carries on with the saved model. If it was recording, it carries on recording into the same folder.

## What counts as wrong

The device answers two things only: is something wrong, and what. There are no severity levels. "What" is a plain description of what is going on (VIBRATION TOO HIGH, UNUSUAL VIBRATION, KNOCKING OR JOLTS, RAPID TEMP CHANGES, …), not a guess at the cause. The description comes straight from which groups of readings left normal (vibration level, vibration shape, spikiness, speed, temperature, sound, …). On 20 real bearings, the cause guesses were mostly wrong, but the descriptions were accurate for all 35 faults caught ([OTTAWA_RESULTS.md](../../training/record/datasets/OTTAWA_RESULTS.md)). The specific fault type still goes out in the data (`details`, `diagnosis`) for the team. Problems show on the screen as `! …` lines in every mode, go out on the stream as `problem` and `problem_cleared` events, and appear in `/latest` status as `wrong`, `what` and `problems`. The rules are in [effente/safety.py](../../effente/safety.py).

- **The sensor itself, first:** `SENSOR PROBLEM` when the readings make no sense. That means gravity far from 1 g (outside 0.85–1.15 g), no noise at all on any axis (stuck or disconnected), or an IMU temperature outside its rated −40 to 125 °C. While it's on, the vibration checks and the model's diagnosis are set aside, because they would only be judging bad data. On 12 Nov, corrupted samples made the device say VIBRATION TOO HIGH while it sat still; now that case says SENSOR PROBLEM. Learning on such data gives a training note. Sensors that remove gravity by design (industrial piezo sensors) skip the gravity test: their data source sets `accel_measures_gravity = False`.
- **From the moment it's plugged in (no setup, any machine):**
  - vibration over 11 mm/s
  - knocking: impulsive vibration with peaks of 0.5 g or more
  - over 100 °C
  - heating more than 8 °C a minute
  - 5 or more sudden jolts in 10 minutes
- **After training, against this machine's own normal** (the highest while learning): vibration at 2× normal, 15 °C or 6 dB over normal, and the learned model's line. No one has to say what kind of machine it is.
  - Each reading is judged on its median over the period the normal was learned from (a minute after a long recording), so like is compared with like. A passing forklift's reversing beeper (15 s, far over normal) no longer counts; a lasting change shows after about 40 seconds. Models trained before this get their own period.
- **More impacts than normal:** after training on an hour or more, more seconds of impulsive sound (2–7 kHz) or impulsive vibration in the last 30 minutes than twice the most seen while learning. After a shorter recording (100 s or more of running), a simpler form: seconds with the vibration's impulsiveness or peak (or the impulsive sound) over a level set above anything seen while learning, more than 2 of them in the last minute, so the third knock; one bump doesn't count. Either shows as `KNOCKING OR JOLTS` as soon as the count is over, and clears within about a minute of the knocks stopping. It catches repeating knocks too short or too sparse for the model (simulated conveyor and test data only, unverified on a real machine).
- **A spike in the model's own score** (after a recording short enough for periods under a minute): the model alarms on the median of its last 5 scores, so one abnormal second never showed. Now one second scoring over a spike line alarms at once, named from that second's readings, and stays 30 s. The spike line is learned at training from the unsmoothed held-out scores (4× their one-a-week gamma tail, about 6–7× the model's line on the fake board) and kept in the model's baseline (`model_spike`). Its own dial, the spike sensitivity, scales it (below). After a long recording (1-minute periods) the model sees minute medians, which a 1-second spike can't move, so there the impact checks below are what catch it (simulated data only, unverified on a real machine).
- **One sudden impact** (after training, 100 s or more of running): a single second whose peak is over 2× the highest peak seen while learning, and at least 0.05 g. It shows at once as `KNOCKING OR JOLTS` and stays 30 s. The spike sensitivity sets how big: 5× at 0, 3× at 0.25, 2× at 0.5, 1.5× at 0.75, 1.25× at 1 (the vibration column of the table below). The `problem` event carries the peak in g (`peak_g`) and how many times the learning maximum it was (`value`). After a long recording (an hour or more) the level is still the biggest peak while learning, so the check only shows impacts bigger than the biggest knock the device took while learning; the spike sensitivity moves the multiple (on the simulated conveyor any lower level, such as a high percentile, gave 0.6–4.5 false alerts a week from people knocking the device; the maximum gave none). A bump of the table that big counts too (simulated and test data only, unverified on a real machine).
- Each of the other checks must hold for 10 seconds before it shows, so a bump doesn't count.
- **Notes after training:** `/latest` status has notes (`learning_notes`) if the machine was mostly stopped, hardly vibrated, or was still warming up or changing. They are not shown on the screen. Notes also flag a universal limit the machine was already over while learning. That limit is then dropped for this machine, so it doesn't show for ever.

**Sensitivity:** how far outside normal counts as wrong hasn't been decided. Sensitivity goes from 0 (least sensitive) to 1 (most), and the numbers above are the starting values at the default, 0.5. Each 0.25 step halves or doubles how far outside normal counts:

| Sensitivity | Vibration | Temperature | Sound | Model's line |
|---|---|---|---|---|
| 0 | 5× normal | +60 °C | +24 dB | 4× |
| 0.25 | 3× | +30 °C | +12 dB | 2× |
| 0.5 (default) | 2× | +15 °C | +6 dB | 1× |
| 0.75 | 1.5× | +7.5 °C | +3 dB | 0.5× |
| 1 | 1.25× | +3.75 °C | +1.5 dB | 0.25× |

It changes the outside-normal checks and the model, never the universal limits.

**Spike sensitivity** (`spike_sensitivity`, added 13 Nov): a second dial, with the same range, default and scale, for single spikes only: one sudden impact (the vibration column) and one second of the model over its spike line (the model column). It moves nothing else, and the main sensitivity doesn't move it. Turning the main one up to catch smaller spikes would also make the model alarm on ordinary running after a short recording (on the fake board: 24–58 % of the time at 0.75), which is why spikes have their own. It is set, saved and shown like the main one (`/settings`, `settings.json`, `/latest` status, `/view`). 

**Dials of their own** (optional, added 13 Nov): each sets one group on its own, with the same range and scale; unset (`null`) it follows `sensitivity`, so with none set nothing changes.

| Setting | What it sets |
|---|---|
| `model_sensitivity` | the model's slow (median) alarm |
| `vibration_sensitivity` | vibrating beyond its normal (the vibration column) |
| `heat_sensitivity` | hotter than its normal (the temperature column) |
| `sound_sensitivity` | louder than its normal (the sound column) |
| `early_warning_sensitivity` | the early warning |
| `early_warning` | `false` switches the early warning off |

They are set, saved and shown like the main one; `settings.json` keeps only those that are set. A warm or noisy site can turn heat or sound down without dulling vibration.

Change any of them while the device runs:

```bash
curl -X POST -d '{"sensitivity": 0.7}' http://localhost:8765/settings
```

```bash
curl -X POST -d '{"spike_sensitivity": 0.75}' http://localhost:8765/settings
```

`curl http://localhost:8765/settings` shows the current value and what it means in numbers. Editing `data/settings.json` on the board (`{"sensitivity": 0.7}`) works too, and is picked up within 2 seconds.

## Early warning

In inference, while nothing is wrong, the device also looks for a small change that has lasted or is creeping up, before it is big enough to count as wrong ([effente/early_warning.py](../../effente/early_warning.py)). It watches each group of readings (the same groups the descriptions come from) and the model's score, against this machine's normal given the other readings, in blocks of 60 summary periods: an hour after a long recording, a minute after a short one. Stopped time is skipped.

- **Screen:** while nothing is wrong, the header's left side reads like `EARLY: HEATING UP` instead of the mode.
- **Stream:** an `early_warning` event (`text`: `TOO HOT CREEPING UP` or `TOO HOT SLIGHTLY UP`, plus `symptom`, `rising` and a coarse `speed`: "rising over hours" or "over days"). An `early_warning_cleared` event follows when it goes away.
- **Status:** `/latest` has an `early_warning` field (`on`, `symptom`, `text`, `since`, …).

It never counts as wrong: `wrong` stays false. While something is wrong it is set aside, it starts again after relearning, and it clears when the change goes away. There's no countdown and no predicted failure time. Sensitivity scales it too. Its normal is saved beside the model (`models/<name>.early.json`). Models trained before it existed have none, so early warning stays off until the machine is relearned. How it compares with other approaches on the simulated conveyor: [EARLY_WARNING.md](../../training/record/conveyor_eval/EARLY_WARNING.md).

## Wiring

| Part | Where | Notes |
|------|-------|-------|
| MPU6050 (on this board's module: an MPU-6500, WHO_AM_I 0x70) | Third I2C bus (`Wire2`), address 0x68 | Accelerometer ±8 g, gyro ±250 °/s, 1 kHz. Accelerometer low-pass 218 Hz (6500) or 184 Hz (6050) |
| OLED 128×64 | `Wire2`, address 0x3C | SSD1306/SSD1309. For an SH1106, set `OLED_COL_OFFSET` to 2 in the sketch |
| Button | D9 to GND | Internal pull-up, debounced. Until it's wired, `adb shell touch /home/arduino/ArduinoApps/effente/data/press` counts as a press |

## Deploy, and connect to the stream

**Starting at power-up:** on 13 Nov effente was set as the board's default app, so it starts by itself when the board is plugged in:

```bash
adb shell arduino-app-cli properties set default user:effente
```

To stop that, set the default to `none`. Whether it starts reliably at every power-up is untested.

From the repo root, with the board on USB:

```bash
device/app/deploy.sh
```

```bash
adb forward tcp:8765 tcp:8765
```

```bash
curl -N http://localhost:8765/stream
```

`http://localhost:8765/view` (or `http://<the board's address>:8765/view`) is a small page for a browser: the state (IDLE, RECORDING, TRAINING or DETECTING), the four readings and what is wrong, refreshed every second. `/latest` status carries the same: `state` beside the contract's `mode` (`display`, `recording`, `training`, `watching`), and `readings`, in every mode; the stream sends status and a feature row every second.

`/settings` is described above. `/latest`, `/features` and `/health` are described in [device/intake/README.md](../intake/README.md). On a network that lets devices see each other, use the board's address instead of `localhost`. University WiFi usually doesn't allow that, so use the USB forward or a phone hotspot.

Data stays on the board in `~/ArduinoApps/effente/data`: `recordings/`, `models/`, `state.json`, `settings.json` and `history.npz`. The deploy script never touches it.

## Watching a test from the laptop, and pulling the recording

With the board on USB and the app deployed:

```bash
python3 device/app/board_monitor.py --until watching --log data/board-monitor.jsonl
```

[board_monitor.py](board_monitor.py) presses nothing and changes nothing. It prints the mode, feature rows a second, each IMU stream's rate (accelerometer, gyroscope, temperature), lost samples, FIFO restarts and their rate, the recording folder and its size, training progress and then the training report. It exits with 4 when something needs a look (the app unreachable, the samples stopped, a refused stop, a failed training, a problem the checks raised); `--stay` keeps it going.

```bash
device/app/pull_recording.sh
```

[pull_recording.sh](pull_recording.sh) copies the newest recording (or the one named) and its model into `data/real/board/<name>/`, which is never committed.

```bash
python3 device/app/recording_summary.py data/real/board/<name>
```

[recording_summary.py](recording_summary.py) prints what is in it: rows and gaps, empty sensors and columns, vibration and temperature levels, the raw clips' sample rates, the events and the training report. Raw clips are 10 s at every 10-minute mark of the clock (and around jolts), so a 5-minute recording has one or none; the gyroscope (100 a second) isn't kept in them.

Tried against a simulated board only (13 Nov), until the first run on the real one.

## The fan test

This tests the whole device. Fix it to a desk fan and let it learn the fan's normal. Then tape a coin to one blade and see whether it says something is wrong and describes it as a vibration problem. [fan_test.py](fan_test.py) runs on the laptop and guides you through it.

You need:

- the board on USB, with the app deployed (`device/app/deploy.sh`)
- a desk fan
- a coin and some tape
- about 45 minutes

```bash
python3 device/app/fan_test.py
```

The script sets up `adb forward tcp:8765 tcp:8765` itself, then:

1. checks the app is up (`/health`, `/latest`) and prints the sensitivity (`GET /settings`)
2. asks you to fix the device to the fan and switch it on, then presses the button (`adb shell touch …/data/press`) to start learning
3. waits `--minutes` (default 20), showing how long it has learned. Leave the fan running.
4. presses again to train and shows training progress until the device is in inference
5. watches `--normal-minutes` (default 10) with the fan as it is. Anything wrong now is a false alarm.
6. asks you to switch the fan off, tape the coin near the tip of one blade and switch it back on. It then watches up to `--fault-minutes` (default 10). It reports whether the device said something is wrong, what it named, and how long that took. It stops 30 s (`--settle`) after the vibration is described.
7. optionally tries another sensitivity with the coin still on: `--try-sensitivity 0.7`, or type a value when asked. It watches for `--sensitivity-minutes` (default 5), then puts the old sensitivity back.
8. writes a JSON report to `data/fan-test-<time>.json`. Like all of `data/`, it is never committed.

The test passes if there were no false alarms and the coin was described as UNUSUAL VIBRATION or VIBRATION TOO HIGH (`unusual_vibration` or `vibration_too_high` in `what`). The report also says whether the guessed cause was unbalance (`unbalance_like` in `details` or `diagnosis_detail`, which the platform contract calls `imbalance`), for information only. "Wrong" is the device's own `wrong`, in a results line or in status. It covers the model and the no-setup limits (vibration over 11 mm/s, …), so a false alarm from either counts. The exit code is 0 for a pass and 1 otherwise.

Other options:

- `--url` for a board reached over the network, with `--no-forward`
- `--press-cmd "…"` for another way to press the button
- `--poll` for how often status is checked
- `--yes` to skip the Enter prompts

It is tested without hardware, against a local `DeviceApp` fed simulated conveyor data (`python3 -m device.intake.tests.test_fan_test`). **It hasn't been run on the board or a real fan yet.**

## Send to the laptop platform

Set `EFFENTE_PLATFORM_URL` in the app's environment to the laptop's address on the hotspot, for example `http://192.168.43.10:8000` (`python3 platform/run.py --no-sim` prints it). The app then sends its model, a record a second while in inference, and a check-in otherwise, as in [platform/CONTRACT.md](../../platform/CONTRACT.md). Unset, nothing is sent. The start-up line in the app's log says which. Details are in [device/intake/README.md](../intake/README.md#sending-to-the-laptop-platform).

How App Lab passes environment variables to the Python side hasn't been tried yet, and neither has uploading from the board itself.

## Measured on this board (12 Nov 2026)

| | |
|--|--|
| Sample rate | 1,000 a second (998–1,002 by the microcontroller's clock), 0 lost, 0 FIFO overflows |
| Bridge line | 115200 baud, about 11.5 kB/s. The sensor stream uses 7.5 kB/s (65 %). At 1,445 samples a second (the MPU-6500's rate with the old settings) the line was full, and the router logged corrupted packets |
| At rest | 1.018 g; noise 4–9 mg rms (the MPU's own noise) |
| IMU temperature | About 28 °C on a desk (13 Nov), from the IMU's own temperature sensor, sent with every batch, and used as the heat reading when there's no thermal camera. Until 13 Nov it read about 43 °C, because the MPU-6050's conversion was used for our MPU-6500. The 6500's is raw / 333.87 + 21 |
| Features per window | 29 ms with every sensor (12 % of one core at 4 windows a second); less with only the IMU |
| Model | Loads in 8 ms, judges in 6 ms |
| OLED | 10 frames a second, drawn one eighth of the screen per pass at 400 kHz. Drawing the whole frame at once held the bus for about 27 ms; with the sensor read on top, that overflowed the MPU-6500's 512-byte FIFO, which corrupted every later sample (2 g of "vibration" at rest). Running the bus at 1 MHz didn't help. Text from the Linux side costs about 180 bytes of Bridge line per update |
| After the fix (12 Nov, after the reflash) | At rest: 1.018 g gravity, 2–4 mg noise per axis, 0.3–0.5 mm/s. About one FIFO restart every 5 s remains (about 0.7 % of samples, rate 993 a second); the cause isn't found yet |

## Limits and known issues

- **What the Bridge can carry:** the accelerometer at 1 kHz plus the gyro at 100 Hz fits. The microphone (16 kHz) and the thermal camera won't fit raw on the 115200-baud line. Options:
  - process them on the microcontroller
  - connect the mic to the Linux side
  - raise the router's baud rate (a system service change that needs `sudo`; untested)
- **After reflashing the board:** check `systemctl status arduino-burn-bootloader`. On 12 Nov its first-boot run timed out, so the sketch never started (0 samples). Running `arduino-cli burn-bootloader -b arduino:zephyr:unoq -P jlink` on the board fixed it. The newer toolchain also no longer links `log10f` (missing `__errno`), so the sketch uses its own.
- **Startup:** now and then, when the app starts, the sketch doesn't start on the microcontroller: no samples, and the screen frozen. Starting the app again has always fixed it. `deploy.sh` now waits for samples after each start and retries up to 3 times by itself; if all 3 fail, unplug the board for a few seconds. To tell what state it's in, the sketch sends a heartbeat each second: alive, sensor and screen found, WHO_AM_I, and the bus lines' levels. The app logs it every 10 s as `microcontroller: ...`, and "no heartbeat yet" means the sketch isn't running. At every start the sketch also frees the I2C bus (clock pulses on A5, then a STOP), in case a reset caught the sensor or screen mid-reply. It retries a missing sensor or screen every 2 s and shows SENSOR NOT FOUND meanwhile.
- **Ownership:** the sketch belongs in `firmware/` and the app in `device/`, which is track 1's area under AGENTS.md. It's here as track 2's sensor app, until track 1's work replaces it.
