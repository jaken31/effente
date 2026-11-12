# The effente device app (Arduino UNO Q)

One Arduino App Lab app: the sketch runs on the microcontroller, and the Python side runs our intake, model and stream on the board's Linux side.

```
plug in ──> DISPLAY ──button──> RECORDING ──button──> TRAINING ──done──> WATCHING ──button──> RECORDING ...
            readings              learning              on the board      checking for faults
            ↑ the whole time: readings and status on the OLED, and a live stream on port 8765 ↑
```

| Mode | Screen | What happens |
|------|--------|--------------|
| Streaming | `STREAMING` and the time, vibration, peak, speed, temperature | Sensors are read and features computed; nothing is kept |
| Recording | `RECORDING` and the readings | Learn mode: feature rows, per-minute summaries, raw snippets and events go to `data/recordings/rec-…` |
| Training | `TRAINING`, then the percentage and stage | The model is trained on the board ([effente/trainer.py](../../effente/trainer.py), NumPy only). A recording shorter than 5 minutes is refused, and learning carries on. A day or more trains from the per-minute summaries, so a week fits in memory |
| Inference | `NOTHING WRONG`, or what is wrong (`UNBALANCE LIKE`, `OUTSIDE NORMAL`, …) and the reading that moved most | Judges every summary period: 1 s after a short recording, up to a minute after a long one |

Pressing the button while in inference starts a new recording, to relearn. After a power cut, the app resumes what it was doing. If it was in inference, it carries on with the saved model. If it was recording, it carries on recording into the same folder.

## What counts as wrong

The device answers two things only: is something wrong, and what. There are no severity levels. Problems show on the screen as `! …` lines in every mode, go out on the stream as `problem` and `problem_cleared` events, and appear in `/latest` status as `wrong`, `what` and `problems`. The rules are in [effente/safety.py](../../effente/safety.py).

- **From the moment it's plugged in (no setup, any machine):**
  - vibration over 11 mm/s
  - knocking: impulsive vibration with peaks of 0.5 g or more
  - over 100 °C
  - heating more than 8 °C a minute
  - 5 or more sudden jolts in 10 minutes
- **After training, against this machine's own normal** (the highest while learning): vibration at 2× normal, 15 °C or 6 dB over normal, and the learned model's line. No one has to say what kind of machine it is.
- Each check must hold for 10 seconds before it shows, so a bump doesn't count.
- **Notes after training:** the screen shows notes for 2 minutes after training (status keeps them) if the machine was mostly stopped, hardly vibrated, or was still warming up or changing. Notes also flag a universal limit the machine was already over while learning. That limit is then dropped for this machine, so it doesn't show for ever.

**Sensitivity:** how far outside normal counts as wrong hasn't been decided. Sensitivity goes from 0 (least sensitive) to 1 (most), and the numbers above are the starting values at the default, 0.5. Each 0.25 step halves or doubles how far outside normal counts:

| Sensitivity | Vibration | Temperature | Sound | Model's line |
|---|---|---|---|---|
| 0 | 5× normal | +60 °C | +24 dB | 4× |
| 0.25 | 3× | +30 °C | +12 dB | 2× |
| 0.5 (default) | 2× | +15 °C | +6 dB | 1× |
| 0.75 | 1.5× | +7.5 °C | +3 dB | 0.5× |
| 1 | 1.25× | +3.75 °C | +1.5 dB | 0.25× |

It changes the outside-normal checks and the model, never the universal limits. Change it while the device runs:

```bash
curl -X POST -d '{"sensitivity": 0.7}' http://localhost:8765/settings
```

`curl http://localhost:8765/settings` shows the current value and what it means in numbers. Editing `data/settings.json` on the board (`{"sensitivity": 0.7}`) works too, and is picked up within 2 seconds.

## Wiring

| Part | Where | Notes |
|------|-------|-------|
| MPU6050 (on this board's module: an MPU-6500, WHO_AM_I 0x70) | Third I2C bus (`Wire2`), address 0x68 | Accelerometer ±8 g, gyro ±250 °/s, 1 kHz. Accelerometer low-pass 218 Hz (6500) or 184 Hz (6050) |
| OLED 128×64 | `Wire2`, address 0x3C | SSD1306/SSD1309. For an SH1106, set `OLED_COL_OFFSET` to 2 in the sketch |
| Button | D2 to GND | Internal pull-up, debounced. Until it's wired, `adb shell touch /home/arduino/ArduinoApps/effente/data/press` counts as a press |

## Deploy, and connect to the stream

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

`/settings` is described above. `/latest`, `/features` and `/health` are described in [device/intake/README.md](../intake/README.md). On a network that lets devices see each other, use the board's address instead of `localhost`. University WiFi usually doesn't allow that, so use the USB forward or a phone hotspot.

Data stays on the board in `~/ArduinoApps/effente/data`: `recordings/`, `models/`, `state.json`, `settings.json` and `history.npz`. The deploy script never touches it.

## Measured on this board (12 Nov 2026)

| | |
|--|--|
| Sample rate | 1,000 a second (998–1,002 by the microcontroller's clock), 0 lost, 0 FIFO overflows |
| Bridge line | 115200 baud, about 11.5 kB/s. The sensor stream uses 7.5 kB/s (65 %). At 1,445 samples a second (the MPU-6500's rate with the old settings) the line was full, and the router logged corrupted packets |
| At rest | 1.018 g; noise 4–9 mg rms (the MPU's own noise) |
| IMU temperature | 42–43 °C, from the IMU's own temperature sensor, sent with every batch, and used as the heat reading when there's no thermal camera |
| Features per window | 29 ms with every sensor (12 % of one core at 4 windows a second); less with only the IMU |
| Model | Loads in 8 ms, judges in 6 ms |
| OLED | A full frame is 27 ms of I2C time (about 35 frames a second at most). With sensor reads in between, a frame takes 45–55 ms. Text from the Linux side costs about 180 bytes of Bridge line per update, so about 10 updates a second fit; the app sends 2 |

## Limits and known issues

- **What the Bridge can carry:** the accelerometer at 1 kHz plus the gyro at 100 Hz fits. The microphone (16 kHz) and the thermal camera won't fit raw on the 115200-baud line. Options:
  - process them on the microcontroller
  - connect the mic to the Linux side
  - raise the router's baud rate (a system service change that needs `sudo`; untested)
- **Startup:** now and then, after the app restarts, the sketch doesn't start: the screen stays on `starting...` and no samples arrive. Stopping the app and starting it again (or starting another app first) has fixed it every time so far. The cause hasn't been found.
- **Ownership:** the sketch belongs in `firmware/` and the app in `device/`, which is track 1's area under AGENTS.md. It's here as track 2's sensor app, until track 1's work replaces it.
