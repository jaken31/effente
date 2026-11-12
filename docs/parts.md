# Parts list

Status is what the team has said so far. "Confirm" means nobody has said whether you have it. No prices are listed because none were checked.

## Electronics

| Part | Qty | Purpose | Status |
|------|-----|---------|--------|
| Arduino UNO Q, 4 GB | 1 | The device: sampling, detection, screen, agent | Have |
| Accelerometer breakout, MPU6050 (GY-521) or ADXL345 (GY-291) | 1, plus a spare | Vibration sensing in the sensor head | Have. Decide which one before recording |
| 2.4" OLED screen module | 1 | Shows diagnosis and reason | Have |
| Push button | 1 | Starts a reading (SCAN) | Have |
| USB power bank | 1 | Powers the handheld | Confirm it can power the UNO Q |
| USB-C cable, short | 1 | Power bank to UNO Q | Confirm |
| Microphone module | 1 | Second input (after vibration works) | Confirm. Check it works with the UNO Q |
| Flexible multi-core cable, about 30 to 50 cm | 1 | Sensor head to body | Confirm |
| Jumper wires and header pins | Assorted | Wiring | Confirm |
| Small breadboard or protoboard | 1 | Wiring during bring-up | Confirm |

## Sensor head and mounting

| Part | Qty | Purpose | Status |
|------|-----|---------|--------|
| Neodymium magnet, disc, about 10 to 15 mm | 1, plus spares | Holds the head on the machine | Need |
| Steel discs or washers, about 20 mm | 4 or more | Probe point glued to each machine | Need |
| Instant glue (cyanoacrylate) | 1 | Fixes steel discs and the magnet | Need |
| M3 screws and nuts, short | 4 to 6 | Fix the breakout flat in the head | Need. Measure the board's holes first |
| Cable tie or clip | 2 | Strain relief on the head | Need |

## Printed parts

| Part | Qty | Notes | Status |
|------|-----|-------|--------|
| Sensor head | 1, plus a spare | Solid (100% infill). PETG if the motor runs warm | To design and print before recording data |
| Handheld body, tray and lid | 1 | Screen window, button hole, power bank bay, head dock | To design. Print last |
| Filament, PLA or PETG | | | Confirm printer access during the event |

## Demo rig

| Part | Qty | Purpose | Status |
|------|-----|---------|--------|
| Small fan or motor | 1, plus a spare | The machine under test | Have |
| Power supply for the fan | 1 | Fixed speed for every recording | Confirm |
| Coins or small weights, several sizes | Assorted | Imbalance and the sensitivity sweep | Need |
| Tape | 1 | Fixing weights to the blade | Need |
| Screwdriver | 1 | Loosening the fan mount | Need |
| Rigid base or board | 1 | Something to screw the fan to, so "loose mount" is repeatable | Need |

## Tools and other

| Item | Purpose | Status |
|------|---------|--------|
| Calipers | Measure every part before designing printed parts | Need |
| Soldering iron and solder | Header pins, cable to the breakout | Confirm |
| Phone with hotspot | The device's WiFi link to the laptop | Have |
| Laptop | Platform, training, recording | Have |

## Open choices

1. **MPU6050 or ADXL345.** Pick one and keep it. Changing sensors after recording invalidates the data.
2. **Power bank.** Its size sets the size of the handheld body.
3. **Fan speed control.** Recordings need one fixed speed, so the fan needs a steady supply.
