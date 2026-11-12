# effente

A permanent sensor that feels, listens to and watches the heat of a motor, detects mechanical faults, and explains each diagnosis in terms a technician can check. Detection runs on the device with no cloud.

Built for the 300 AI Hackathon, Nov 12-15, 2026.

- [Product requirements](docs/PRD.md)
- [Technical requirements](docs/TRD.md)
- [Fake dataset](fake-dataset/README.md): one simulated day of normal IMU, microphone and thermal data from a factory pump (no faults)

## The problem

Factories lose a lot of money when machines break without warning. Siemens estimates $1.4 trillion a year across the 500 biggest companies, and about $2.3M per hour for a car plant (TODO: confirm both figures against the 2024 "True Cost of Downtime" report before the pitch).

Machines usually give early warning signs well before they fail: small vibration changes, a new noise, or a part running hotter than usual. Most motors are not monitored, and existing AI alerts are black boxes that technicians do not trust.

## What it does

- **What is wrong:** imbalance, loose mount, overheating, or normal.
- **Why:** for example, "strong vibration once per turn", and which sensor saw it.
- **Unknown, inspect:** when it sees a fault it was never trained on.
- **What to do about it:** an agent on the device turns the log into a maintenance report, a repair priority, and a parts lookup when a connection is available.

## How it works

The device is an Arduino UNO Q with three sensors: an accelerometer for vibration, a microphone for sound, and a thermal camera for heat. Its microcontroller samples all three on one clock and drives a 2.4" screen. Its Linux side turns each signal into features (vibration and sound spectra, a heat map relative to the background), runs a small neural network over all three, and uploads the full log over WiFi to a platform on a laptop. If one sensor fails, it keeps working on the other two.

## Where mech interp comes in

1. **Find the features.** We look inside the network and find what it uses: a "once-per-turn" feature for imbalance, a "rattling" feature for looseness, a "hot spot" feature for overheating.
2. **Prove they are real.** Switching off the imbalance feature makes the model stop detecting imbalance. We do this live from the platform.
3. **Only verified explanations.** The device shows a reason only if a switch-off test backs it. The agent may cite only those features.
4. **Unknown-fault alarm.** It triggers when the network's internal activity looks unfamiliar.

## Demo

1. Healthy fan: "Normal".
2. Coin taped to a blade: "Imbalance: once-per-turn vibration".
3. Loosened mounting screws: "Loose mount: rattling in vibration and sound".
4. Housing warmed with a hair dryer (simulated heat, said openly): "Overheating: hot spot on the motor housing".
5. Something unusual: "Unknown, inspect".
6. Switch off the imbalance feature: the fan with the coin no longer reads "Imbalance".
7. The agent's maintenance report.

## Hardware

- Arduino UNO Q (4 GB)
- Accelerometer (MPU6050 or ADXL345)
- Microphone (I2S MEMS mic such as INMP441, or a USB mic)
- Thermal camera (MLX90640, or AMG8833 as a fallback)
- 2.4" OLED screen
- Small fan or motor as the demo machine
- Tang Nano 20K FPGA (stretch: the trimmed model as a circuit)

## Competitors

Tractian, Augury, SKF and ABB sell predictive maintenance sensors. effente explains each diagnosis with a verified reason, flags faults it has not seen before, and needs no cloud to detect.

## Tracks

1. **Device:** sampling all three sensors, screen, on-device inference, WiFi upload.
2. **Data and signal:** recordings, sensitivity sweep, public run-to-failure dataset.
3. **Model and FPGA:** training, unknown alarm, circuit version.
4. **Mech interp:** feature analysis, switch-off tests, explanation table.
5. **Platform, agent and business:** dashboard, agent, validation, pitch.

Rule: vibration is built end to end first, then sound, then heat. The five-case demo runs end to end by Saturday 1pm. Anything not working then is cut.
