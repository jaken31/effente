# effente

A handheld probe a technician carries from machine to machine. Its magnetic sensor head snaps onto a motor or assembly-line module, detects mechanical faults from vibration, and explains each diagnosis in terms the technician can check. Detection runs on the device with no cloud.

Built for the 300 AI Hackathon, Nov 12-15, 2026.

- [Product requirements](docs/PRD.md)
- [Technical requirements](docs/TRD.md)
- [Architecture diagrams](docs/architecture.md)

## The problem

Factories lose a lot of money when machines break without warning. Siemens estimates $1.4 trillion a year across the 500 biggest companies, and about $2.3M per hour for a large automotive plant ([True Cost of Downtime 2024](https://assets.new.siemens.com/siemens/assets/api/uuid:1b43afb5-2d07-47f7-9eb7-893fe7d0bc59/TCOD-2024_original.pdf)).

Machines usually give early warning signs, such as small vibration changes, well before they fail. Most motors are not monitored: smaller manufacturers tend to fix things when they break, because monitoring costs too much. The people who could judge a machine by feel are leaving too. US manufacturing needs to replace about 2.8 million retirees by 2033 (Deloitte and the Manufacturing Institute). And existing AI alerts are black boxes that technicians do not trust.

Built for small and mid-size manufacturers: one handheld covers a whole floor of machines that will never get a sensor each.

## What it does

- **What is wrong:** imbalance, loose mount, or normal.
- **Why:** for example, "strong vibration once per turn".
- **Unknown, inspect:** when it sees a fault it was never trained on.
- **What to do about it:** an agent on the device turns the log into a maintenance report, a repair priority, and a parts lookup when a connection is available.

## How it works

The device is a handheld built on an Arduino UNO Q, with a small magnetic sensor head on a short cable. The magnet holds the head on with the same force every time, so a reading does not depend on how hard someone presses. The microcontroller samples the accelerometer in the head and drives a 2.4" screen. See the [mock-up](docs/mockup.svg). Its Linux side turns the signal into a frequency spectrum, runs a small neural network, and uploads the full log over WiFi to a platform on a laptop.

## Where mech interp comes in

1. **Find the features.** We look inside the network and find what it uses: a "once-per-turn" feature for imbalance, a "rattling" feature for looseness.
2. **Prove they are real.** Switching off the imbalance feature makes the model stop detecting imbalance. We do this live from the platform.
3. **Only verified explanations.** The device shows a reason only if a switch-off test backs it. The agent may cite only those features.
4. **Unknown-fault alarm.** It triggers when the network's internal activity looks unfamiliar.

## Demo

1. Healthy fan: "Normal".
2. Coin taped to a blade: "Imbalance: once-per-turn vibration".
3. Loosened mounting screws: "Loose mount: rattling pattern".
4. Something unusual: "Unknown, inspect".
5. Switch off the imbalance feature: the fan with the coin no longer reads "Imbalance".
6. The agent's maintenance report.

## Hardware

- Arduino UNO Q (4 GB)
- Accelerometer: MPU6050 (GY-521)
- 2.4" OLED screen
- Magnet for the sensor head, steel discs as probe points
- USB power bank
- Microphone (second input, added after vibration works)
- Small fan or motor as the demo machine

## Competitors

Tractian, Augury, SKF and ABB sell permanent predictive maintenance sensors. Fluke and SKF sell handheld vibration analyzers, which show a spectrum for a trained analyst to read. effente gives a diagnosis instead: it explains each diagnosis with a verified reason, flags faults it has not seen before, and needs no cloud to detect.

## Tracks

1. **Device:** sampling, screen, on-device inference, WiFi upload.
2. **Data and signal:** recordings, sensitivity sweep, public run-to-failure dataset.
3. **Model:** training, unknown alarm, model file.
4. **Mech interp:** feature analysis, switch-off tests, explanation table.
5. **Platform, agent and business:** dashboard, agent, validation, pitch.

Rule: the four-case demo runs end to end by Saturday 1pm. Anything not working then is cut.
