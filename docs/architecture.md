# Architecture

Two diagrams: how the system fits together, and the data it keeps. Details are in the [TRD](TRD.md).

## High-level design

```
 MACHINE UNDER TEST
   conveyor, motor, fan
        |  vibration (+ sound, heat, room when fitted)
        v
 HANDHELD DEVICE (Arduino UNO Q)
 +---------------------------------------------------------------------+
 |  magnetic sensor head: MPU-6500 accelerometer                       |
 |        |                                                            |
 |  microcontroller: sampling at 1 kHz, screen, button                 |
 |        |                                                            |
 |  Linux side:                                                        |
 |    model library --pick entry--> calibration (minutes)              |
 |                                          |                          |
 |    features (278 per window) --> calibrated network --> alarm?      |
 |                                          |                          |
 |                                 readings that moved                 |
 |                                          |                          |
 |                                    fault types --> screen           |
 |    optional long stay: recording --> training --> own model         |
 |    device log                                                       |
 +---------------------------------------------------------------------+
        |  WiFi: log records
        v
 LAPTOP PLATFORM
 +---------------------------------------------------------------------+
 |  log receiver and store --> dashboard: spectrum, readings, history  |
 |        |                                                            |
 |  agent fleet: maintenance report agent                              |
 |  (later: setup agent, review agent)                                 |
 +---------------------------------------------------------------------+
        |  when online
        v
 ONLINE SERVICES: Claude API, Tavily search
```

Reading it top to bottom:

1. **Sense.** The magnetic head sits on the machine. The microcontroller samples it on a steady clock.
2. **Calibrate.** The technician picks a machine type. The device takes that pretrained network from its library and fits it to this machine in minutes. If the device stays on the machine, it can later train a model on that machine alone. None of this needs a network.
3. **Decide.** Each period the network scores how far the machine is from its normal. If it stays far for a few periods, the device names the likely fault type and the readings that moved, and shows them on the screen.
4. **Share.** Each record is logged and uploaded over WiFi to the laptop, where the dashboard shows it.
5. **Act.** The agent fleet on the laptop reads the stored records, calls Claude and Tavily, and produces a maintenance report. This is the only part that needs the internet.

The same feature code runs in recording, training and on the device.

Tracks map onto the boxes: track 1 is the handheld, track 2 is recordings and the signal pipeline, track 3 is training, the model and fault types, track 5 is the laptop platform and the agent fleet.

## Data model

```
 DEVICE 1--* RECORD *--1 MACHINE
                |
                *--1 MODEL 1--* FAULT TYPE
                |        1--* FEATURE
                |
 RECORDING *--* MODEL            (a model is trained on recordings)
 MACHINE 1--* AGENT RUN 1--0..1 REPORT *--* FEATURE (cited)
 MACHINE 1--* ALARM 1--* RECORD  (an alarm is a stretch of abnormal records)
                  1--* ANSWER
```

| Thing | Holds |
|-------|-------|
| Device | `device_id` |
| Machine | `machine_id` |
| Recording | Learn-mode folder: feature rows 1 a second, per-minute summaries, raw snippets, history state, events |
| Model | `model_version`, `features_version`, library entry, calibrated or trained, preparation statistics, int8 weights, threshold, fault classifier, fault types, feature table |
| Fault type | name, plain text, group |
| Feature | name, plain text, unit (278 of them) |
| Record | `device_id`, `machine_id`, `model_version`, `at`, `diagnosis`, `confidence`, `unknown_score`, `features` (readings that moved, in spreads), `fault_types`, `note`, `spectrum` |
| Agent run | `machine_id`, `at`, model, usage, `ok`, reason, problems, tool calls |
| Report | summary, priority, cited readings, recommended action, part reference |
| Alarm | `alarm_id`, machine, device, `model_version`, start, end, open, diagnosis, periods, peak `unknown_score`. Worked out by the platform from records, not sent by the device |
| Answer | verdict (real or false), who, when, finding |

Reading it:

- **Recording** is training data: a machine's normal running, kept by learn mode. **Record** is what the device produces while watching.
- **Feature** is the shared language. Features are what the device measures, what moves in an alarm, and the only things a report may cite.
- **Agent run** is one attempt to write a report. It has a report only if the checks passed.
- **Alarm** is one abnormal stretch of records on one machine. **Answer** is a person's verdict on it. Answers are only added, never changed; the newest counts.

In storage these are files, not database tables. Recordings are folders of `.npz` files. The model is one JSON file. Records and agent runs are one JSON object per line. Moved readings and citations live inside their parent records.

## Known gaps in the formats

1. **Machines and devices are only IDs.** There is nowhere to store a machine's type, speed or location. The machine profile in [model-lifecycle.md](model-lifecycle.md) fills this.
2. **Alarm verdicts are specified but not built.** Contract version 2.1 (section 6 of [CONTRACT.md](../platform/CONTRACT.md)) defines alarms and answers. The platform and dashboard do not have them yet. See alarm feedback in [model-lifecycle.md](model-lifecycle.md).
