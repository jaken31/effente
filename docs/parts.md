# Parts list

Status is what the team has said so far. "Confirm" means nobody has said whether you have it. No prices are listed because none were checked.

## Electronics

| Part | Qty | Purpose | Status |
|------|-----|---------|--------|
| Arduino UNO Q, 4 GB | 1 | The device: sampling, detection, screen, agent | Have |
| Accelerometer breakout, MPU6050 (GY-521) | 1 | Vibration sensing in the sensor head | Have |
| BNO085 motion sensor | 1 | Spare only. Do not swap mid-project | Have |
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

## Prices

Checked 2026-11-11 from web search summaries. There is no single exact price: these parts are sold by many sellers at different prices and pack sizes. Check the live listing before buying.

| Part | Price found (USD) | Where | Have? |
|------|-------------------|-------|-------|
| Arduino UNO Q, 4 GB | Likely $79 list. Also seen: $74.19, $65.99, $59.00 (possibly outdated) | [Arduino US store](https://store-usa.arduino.cc/products/uno-q-4gb) (no price shown in search), [CNX Software](https://www.cnx-software.com/2026/01/21/arduino-uno-q-4gb-board-with-4gb-ram-32gb-storage-available-59/?amp=1&noamp=mobile), [RobotShop](https://www.robotshop.com/products/arduino-uno-q-4gb), [Walmart](https://www.walmart.com/ip/20001402363) | |
| MPU6050 breakout (GY-521) | $2.89 to $6.99 each. 3-pack $13.99 | [Walmart](https://www.walmart.com/ip/MPU6050-Digital-Motion-Processor-Module-3Shaft-Gyro-Accelerometer-Module-for-Robotics-Projects-IIC-Communication/18580807787) and other retail listings | |
| 2.4" OLED module (SSD1309, 128x64) | $15 to $35. Seen: $14.75, $15.48, $25.95, $29.90 | [Fruugo](https://www.fruugo.us/242-ssd1309-128x64-oled-display-module-242-inch-4-pin-oled-lcd-display-module-iic-white/p-403800753), [Walmart](https://www.walmart.com/ip/6118914408), [DisplayModule](https://www.displaymodule.com/products/2-42-inch-oled-graphic-display-128x64-monochrome-with-spi) | |
| Microphone module (INMP441) | About $3.35 each in a 4-pack at $13.41. Singles seen at about $11 | Walmart listings | |
| Neodymium magnets, 12 x 3 mm | 2-pack $4.95. 6-pack $8.44. 30-pack $23.99 | [Home Science Tools](https://homesciencetools.com/product/neodymium-disc-magnets-half-inch-6-pack), [Newark](https://www.newark.com/c/tools-production-supplies/tools-hand-workholding/magnets/disc-round-magnets?height=3mm), [Total Element](https://totalelement.com/products/12mm-x-3mm-neodymium-rare-earth-permanent-disc-magnets-n52-30-pack) | |

Not priced (search returned nothing usable, or the item is generic): power bank, USB-C cable, sensor cable, jumper wires, breadboard, steel washers, instant glue, M3 screws, cable ties, filament, fan power supply, calipers, soldering iron, tape, weights.

Fill in the "Have?" column as you check.

## Open choices

1. **Accelerometer: settled.** MPU6050. Changing sensors after recording invalidates the data.
2. **Power bank.** Its size sets the size of the handheld body.
3. **Fan speed control.** Recordings need one fixed speed, so the fan needs a steady supply.
