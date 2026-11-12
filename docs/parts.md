# Parts list

Status is what the team has said so far. "Confirm" means nobody has said whether you have it. No prices are listed because none were checked.

## Electronics

| Part | Qty | Purpose | Status |
|------|-----|---------|--------|
| Arduino UNO Q, 4 GB | 1 | The device: sampling, detection, screen, agent | Have |
| Accelerometer breakout, MPU-9250/6500 (MPU-6500 used) | 1 | Vibration sensing in the sensor head. Replaces the MPU6050 (GY-521) | Have |
| BNO085 motion sensor | 1 | Spare only. Do not swap mid-project | Have |
| 2.42" OLED screen module (SSD1309, I2C) | 1 | Shows diagnosis and reason | Have |
| Push button, 12 x 12 mm tactile, round cap | 1 | Starts a reading (SCAN) | Have |
| USB power bank | 1 | Powers the handheld | Have, but too weak: its label says 5 V / 1.5 A and the UNO Q needs 5 V / 3 A. Need a 5 V / 3 A (15 W or more) USB-C bank, then update the bay size in the CAD |
| USB-C cable, short | 1 | Power bank to UNO Q | Confirm |
| Microphone module (INMP441) | 1 | Second input (after vibration works) | Have. UNO Q I2S support not confirmed |
| Thermal camera (MLX90640-D55) | 1 | Optional input | Have |
| Flexible multi-core cable, about 30 to 50 cm | 1 | Sensor head to body | Confirm |
| Jumper wires and header pins | Assorted | Wiring | Confirm |
| Small breadboard or protoboard | 1 | Wiring during bring-up | Confirm |

## Sensor head and mounting

| Part | Qty | Purpose | Status |
|------|-----|---------|--------|
| Neodymium magnet, disc, about 10 to 15 mm | 1, plus spares | Holds the head on the machine | Need |
| Steel discs or washers, about 20 mm | 4 or more | Probe point glued to each machine | Need |
| Instant glue (cyanoacrylate) | 1 | Fixes steel discs and the magnet | Need |
| M3 screws and nuts, short | 4 to 6 | Case screws, conveyor motor-bracket bolts | Need |
| Cable tie or clip | 2 | Strain relief on the head | Need |

## Printed parts

| Part | Qty | Notes | Status |
|------|-----|-------|--------|
| Sensor head, head lid, IMU cradle | 1, plus a spare | Solid (100% infill). PETG if the motor runs warm | Designed: `device/cad/`. Print before recording data |
| Handheld back shell, front lid, screen tray | 1 | Screen window, button sleeve, power bank bay, head dock | Designed: `device/cad/`. Some sizes unmeasured, see its README |
| Filament, PLA or PETG | | | Confirm printer access during the event |

## Demo rig

| Part | Qty | Purpose | Status |
|------|-----|---------|--------|
| Printed mini conveyor (frame, 2 rollers, motor bracket) | 1 | The machine under test. Files in `device/cad/` | To print |
| TT gear motor (yellow, 1:48) | 1, plus a spare | Drives the belt | Have |
| TB6612FNG motor driver + Arduino Nano | 1 | Fixed motor speed. Or skip both and run the motor from 5 V USB | Have |
| Belt: TPU print, or duct tape folded sticky-side in, 40 mm wide | 1 | Conveyor belt | Have TPU |
| M3 screws, nuts, washers, or clay | Assorted | Imbalance and the sensitivity sweep (screws go in the drive roller's holes) | Need |
| Zip ties | 2 | Hold the motor in its bracket | Need |
| Screwdriver | 1 | Loosening the motor-bracket bolts | Need |
| Rubber mat | 1 | Under the conveyor, so only the fault shakes | Need |

## Tools and other

| Item | Purpose | Status |
|------|---------|--------|
| Calipers | Measure every part before designing printed parts | Need |
| Soldering iron and solder | Header pins, cable to the breakout | Confirm |
| Phone with hotspot | The device's WiFi link to the laptop | Have |
| Laptop | Platform, training, recording | Have |

## Prices

Checked 2026-11-11 from web search summaries. There is no single exact price: these parts are sold by many sellers at different prices and pack sizes. Check the live listing before buying.

| Part | Price found (USD) | Where | Have? |
|------|-------------------|-------|-------|
| Arduino UNO Q, 4 GB | Likely $79 list. Also seen: $74.19, $65.99, $59.00 (possibly outdated) | [Arduino US store](https://store-usa.arduino.cc/products/uno-q-4gb) (no price shown in search), [CNX Software](https://www.cnx-software.com/2026/01/21/arduino-uno-q-4gb-board-with-4gb-ram-32gb-storage-available-59/?amp=1&noamp=mobile), [RobotShop](https://www.robotshop.com/products/arduino-uno-q-4gb), [Walmart](https://www.walmart.com/ip/20001402363) | |
| MPU6050 breakout (GY-521) | $2.89 to $6.99 each. 3-pack $13.99 | [Walmart](https://www.walmart.com/ip/MPU6050-Digital-Motion-Processor-Module-3Shaft-Gyro-Accelerometer-Module-for-Robotics-Projects-IIC-Communication/18580807787) and other retail listings | |
| 2.4" OLED module (SSD1309, 128x64) | $15 to $35. Seen: $14.75, $15.48, $25.95, $29.90 | [Fruugo](https://www.fruugo.us/242-ssd1309-128x64-oled-display-module-242-inch-4-pin-oled-lcd-display-module-iic-white/p-403800753), [Walmart](https://www.walmart.com/ip/6118914408), [DisplayModule](https://www.displaymodule.com/products/2-42-inch-oled-graphic-display-128x64-monochrome-with-spi) | |
| Microphone module (INMP441) | About $3.35 each in a 4-pack at $13.41. Singles seen at about $11 | Walmart listings | |
| Neodymium magnets, 12 x 3 mm | 2-pack $4.95. 6-pack $8.44. 30-pack $23.99 | [Home Science Tools](https://homesciencetools.com/product/neodymium-disc-magnets-half-inch-6-pack), [Newark](https://www.newark.com/c/tools-production-supplies/tools-hand-workholding/magnets/disc-round-magnets?height=3mm), [Total Element](https://totalelement.com/products/12mm-x-3mm-neodymium-rare-earth-permanent-disc-magnets-n52-30-pack) | |

Not priced (search returned nothing usable, or the item is generic): power bank, USB-C cable, sensor cable, jumper wires, breadboard, steel washers, instant glue, M3 screws, cable ties, filament, calipers, soldering iron, tape, weights.

Fill in the "Have?" column as you check.

## Open choices

1. **Accelerometer: settled.** MPU-6500 (on an MPU-9250/6500 breakout), replacing the MPU6050. Record all training data with it. Changing sensors after recording invalidates the data.
2. **Power bank.** The current bank is too weak for the UNO Q (1.5 A against 3 A). Its replacement's size sets the bay in the handheld body.
3. **Conveyor speed control.** Recordings need one fixed speed: run the TT motor from 5 V USB, or from the TB6612 + Nano at one PWM setting that never changes.
