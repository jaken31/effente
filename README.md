# effente

A handheld probe with a magnetic sensor head. Snap it onto a machine, pick the machine type, and it is judging within minutes, using a pretrained network from its library calibrated to that machine. Leaving it on for days is optional and sharpens the model. It raises an alarm when the machine drifts from its normal, names the likely fault, and shows which readings moved. Detection runs on the device and the agents run on a model on your own laptop, so nothing leaves the site.

Built for the 300 AI Hackathon, Nov 12-15, 2026.

- [Product requirements](docs/PRD.md)
- [Technical requirements](docs/TRD.md)
- [Architecture diagrams](docs/architecture.md)
- [Model lifecycle: setup, calibration and learning over time](docs/model-lifecycle.md)

## The problem

Factories lose a lot of money when machines break without warning. Siemens estimates $1.4 trillion a year across the 500 biggest companies, and about $2.3M per hour for a large automotive plant ([True Cost of Downtime 2024](https://assets.new.siemens.com/siemens/assets/api/uuid:1b43afb5-2d07-47f7-9eb7-893fe7d0bc59/TCOD-2024_original.pdf)).

Machines usually give early warning signs, such as small vibration changes, well before they fail. Most motors are not monitored: smaller manufacturers tend to fix things when they break, because monitoring costs too much. The people who could judge a machine by feel are leaving too. US manufacturing needs to replace about 2.8 million retirees by 2033 (Deloitte and the Manufacturing Institute). And existing AI alerts are black boxes that technicians do not trust.

Built for small and mid-size manufacturers: one handheld covers a whole floor of machines that will never get a sensor each.

## What it does

- **Quick to use:** picks a pretrained network from its library and calibrates it to the machine in minutes. No fault data and no days-long learning needed.
- **What is wrong:** normal, or a likely fault such as "possible unbalance" or "possible looseness", from 21 general fault types.
- **Why:** the readings that moved and by how much, for example "once-per-turn vibration 7 times its usual spread".
- **Unrecognised pattern, inspect:** when the machine is abnormal but no known fault type fits.
- **What to do about it:** a fleet of agents on the laptop turns the log into a maintenance report and a repair priority, using a language model that runs on that same laptop.

## How it works

The device is a handheld built on an Arduino UNO Q, with a small magnetic sensor head on a short cable. The magnet holds the head on with the same force every time, so a reading does not depend on how hard someone presses. The microcontroller samples the accelerometer in the head and drives a 2.4" screen. See the [mock-up](docs/mockup.svg).

Its Linux side turns each second of signal into 285 readings and calibrates a small pretrained neural network from its library to the machine. Left on longer, it can train a network on that machine alone. While watching, the network scores how far the machine is from its normal. When it stays far off, the device names the likely fault and the readings behind it, and uploads the full log over WiFi to a platform on a laptop.

## Demo

1. Snap onto the healthy conveyor, pick "conveyor", calibrate: "Normal".
2. Weight on the drive roller: "Possible unbalance", once-per-turn vibration up.
3. Loosened motor-mount screws: "Possible looseness or rattle".
4. Something unusual: "Unrecognised pattern, inspect".
5. The dashboard: live spectrum, readings that moved, history.
6. The agents' maintenance report.

## Hardware

- Arduino UNO Q (4 GB)
- Accelerometer: MPU-6500 (MPU-9250/6500 breakout)
- 2.4" OLED screen and a button
- Magnet for the sensor head, steel discs as probe points
- USB power bank
- Optional: microphone, thermal camera, room sensor
- Mini belt conveyor as the demo machine

## Competitors

Tractian, Augury, SKF and ABB sell permanent predictive maintenance sensors. Fluke and SKF sell handheld vibration analyzers, which show a spectrum for a trained analyst to read. effente gives a diagnosis instead: it names the readings behind each alarm in plain words, flags faults it has not seen before, and sends nothing off site: detection runs on the device and the agents on your own laptop.

## Tracks

1. **Device:** sampling, screen, device app, WiFi upload.
2. **Data and signal:** recordings, sensitivity sweep, public run-to-failure dataset.
3. **Model:** model library, calibration, trainer, runtime, fault types, validation.
5. **Platform, agent and business:** dashboard, agent fleet, validation, pitch.

Rule: the four-case demo runs end to end by Saturday 1pm. Anything not working then is cut.
