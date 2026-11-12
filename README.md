# effente

A handheld AI machine stethoscope. A technician touches it to a motor for 10 seconds and the screen shows what is wrong and why. It runs offline on an ESP32.

## The problem

Factories lose a lot of money when machines break without warning. Siemens estimates $1.4 trillion a year across the 500 biggest companies, and about $2.3M per hour for a car plant (TODO: confirm both figures against the 2024 "True Cost of Downtime" report before the pitch).

Machines usually give early warning signs, such as small vibration changes, weeks before they fail. Most motors are not monitored because sensors are expensive, and existing AI alerts are black boxes that technicians do not trust.

## What the device shows

- **What is wrong:** imbalance, loose mount, or normal.
- **Why:** for example, "strong vibration once per turn".
- **Unknown pattern, inspect:** when it sees a fault it was never trained on.

No cloud, no subscription. Catching a problem weeks early means the repair happens during a planned break instead of a sudden shutdown.

## Where mech interp comes in

This is what makes the project different from existing predictive maintenance tools.

1. **Find the features.** We look inside the model and find what it uses: a "once-per-turn" feature for imbalance, a "rattling" feature for looseness.
2. **Prove they are real.** Turning off the imbalance feature makes the model stop detecting imbalance. That is causal evidence, not a guess.
3. **Unknown-fault alarm.** It triggers when the model's internal activity looks unfamiliar.
4. **Shrink the model.** We remove the parts we can show it does not use, so it fits on a $5 chip.

Pitch claim: "We opened up the AI and proved it learned real physics, so technicians can trust it."

## Hardware (about $20-40)

- ESP32 board
- Accelerometer (MPU6050 or ADXL345)
- Small OLED screen
- Button and battery
- Small fan or motor as the demo "machine"

## Demo

1. Healthy fan: "Normal".
2. Coin taped to a blade (simulates dirt buildup on a roller): "Imbalance: once-per-turn vibration".
3. Loosened mounting screws: "Loose mount: rattling pattern".
4. Something unusual: "Unknown, inspect".

## Competitors

Tractian, Augury, SKF and ABB sell predictive maintenance sensors. Our difference: the device explains why, flags unknown faults, works offline, and is cheap.

## Roles

1. **Hardware:** ESP32, sensor, screen.
2. **Data:** record vibration for each fault, convert to frequency charts.
3. **Model:** train it, shrink it, put it on the chip.
4. **Mech interp and pitch:** find features, run the switch-off test, build the unknown alarm.

## Build order

1. Sensor data
2. Model on a laptop
3. Explanations
4. Deployment on the chip

Backup: if chip deployment runs late, run the model on the laptop with the ESP32 as the sensor.
