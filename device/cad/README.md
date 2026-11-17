# CAD: handheld enclosure, sensor head, demo conveyor

| Folder | What is in it |
|--------|---------------|
| `print/` | STL files, already in print orientation |
| `files/` | Fusion archives (`.f3d`), STEP exports, and the parametric Fusion build scripts |

`effente-cad.stl` is an earlier enclosure export, kept for reference. Print the files in `print/` instead.

## Rebuilding

Run `files/build_enclosure.py` or `files/build_conveyor.py` in Fusion (Utilities > Scripts and Add-Ins). Every size is a parameter at the top of the script. Each run builds a new design and prints clash checks. Values marked UNVERIFIED in the scripts are typical or listing sizes, not measurements: measure the real parts before printing.

## What to print

One of each. Material PETG or PLA, except the belt.

| Plate | File | Infill | Supports |
|-------|------|--------|----------|
| 1 | `conveyor_frame.stl` | 15-20% gyroid, 4 walls | Off |
| 1 | `conveyor_drive_roller.stl` | 4 walls + 20% gyroid, upright, brim | On, build plate only |
| 1 | `conveyor_idle_roller.stl` | 4 walls + 20% gyroid, upright, brim | On, build plate only |
| 1 | `conveyor_motor_bracket.stl` | 100% | Off |
| 1 | `device_sensor_head.stl` | 100% | Off |
| 1 | `device_head_lid.stl` | 100% | Off |
| 1 | `device_imu_cradle.stl` | 100% | Off |
| 1 | `device_screen_tray.stl` | 15-20%, 4 walls | Off |
| 2 | `device_back_shell.stl` | 15-20%, 4 walls | Off |
| 2 | `device_front_lid.stl` | 15-20%, 4 walls | Off |
| 2 | `device_bank_holder.stl` | 15-20%, 4 walls, floor down | Off |
| 3 | `conveyor_belt_TPU.stl` | TPU, vase mode | Off |

Print the drive roller first and check it fits the TT motor shaft.

### L-slots (rollers lock in)

The roller pins sit in L-shaped slots, so a driven roller cannot climb out. The long leg is a little nearer the other roller; the short leg runs toward the frame's end and ends in a pocket 1.5 mm deep (2 mm on the 2x conveyor). Drop the pin down the long leg, push the roller outward along the short leg (this tightens the belt), and it drops into the pocket. Belt tension pulls sideways, and the pin would have to lift to get out. To remove a roller, lift it slightly, slide it inward, and lift it out.

- Idle roller: both ends in L-slots.
- Drive roller (1x): only its wall-B pin has an L-slot; the motor shaft holds the wall-A end. Fit it with the motor bracket unbolted: push the roller onto the motor shaft, drop the hub into the wall-A slot and the pin into the wall-B long leg (the motor turns slightly), swing the pin end outward into the pocket so the motor straightens, then bolt the bracket down.
- A frame printed before the L-slots has straight slots; reprint `conveyor_frame.stl` to get them.

## Measured parts

- **MPU board:** measured about 25 x 15 mm (10 mm thick with its header), but a cradle sized for the listing's 26.2 x 15.6 mm held it better, so the IMU cradle is built for 26.2 x 15.6 again, with full-height front lips and only the back of the header band open. The sensor head stays 29 x 33.5 x 21.5 mm, so it still fits the dock in the front lid.
- **Head lid clamp:** a hidden friction fit. Four small crush ribs on the lid's locating lip press 0.15 mm into the head's long walls; nothing shows from outside. Push the lid down to close it. To open, put a fingernail in the notch at the top of the end wall opposite the cable and lift. The 0.15 mm is a starting value, not tested: if the lid is too tight, shave the ribs with a knife; if it falls off, set `HEAD['crush']` to 0.25 and reprint the head lid. These replace the snap clips, which broke off during printing.
- **Head dock magnet:** the dock in the front lid takes a 12 x 3 mm neodymium disc magnet (the same as the head's), not a steel disc, which did not hold the head. Glue it into the pocket from inside the lid. Before gluing, let it snap onto the head's magnet and mark the face that points at the head, so the two attract instead of push apart.
- **OLED:** 72 x 43 mm, 13 mm thick with its header pins. The screen tray's pocket fits it; the tray's outside size and the lid hooks are unchanged. The glass-to-PCB thickness (4.5 mm) is estimated from the 13 mm, not measured.
- **SCAN button:** the snap lips that hold the switch are 1.8 mm (3x the old 0.6 mm). The button moved 2 mm down the lid so the snap fingers can bend out past the screen tray's hook. The bigger lips bend the fingers further, so print the front lid in PETG if you can; PLA fingers may crack.

## External power bank holder

`device_bank_holder.stl` is a tray that holds an Anker Prime 12,000 mAh (A1335) against the right-hand wall of the handheld, outside the case. The bank inside the case (76 x 48 x 13 mm, 5 V / 1.5 A) is rated below the UNO Q's 5 V / 3 A; the Anker's USB-C gives 5 V / 3 A. Bank size is measured: 135 x 56 x 33 mm (Anker's spec page says 134.4 x 55 x 34.53 mm). The pocket leaves 0.4 mm around it.

The bank lies flat, display up, USB-C end at the top of the device, so its battery screen sits beside the device's OLED. Covers flush with the device's front face hide the rest of the bank, including the Anker logo; a window shows only the screen. The window position is estimated from a photo (screen about 13 to 61 mm from the USB-C end): check it against your bank and adjust `BANK['win']` if needed.

1. Stick the tray's back plate to the right-hand wall with command strips (the design leaves 2 mm for them). Keep the strips off the two windows in the plate (cable slot and vents).
2. Slide the bank in from the top end, display up, far end first, until it hits the end wall. Two latch tongues in the floor click up behind it. To take it out, press both tongues down from underneath and slide the bank out.
3. Run a short, thin USB-C cable from the bank's port, down past the end of the tray, back along under the tray floor, through the window in the back plate and the slot in the wall, to the UNO Q's USB-C.

Print it floor down, as exported, with no supports. The latch tongues must flex, so PETG is better than PLA here.

With the tray on, the handheld is about 147 x 145 mm and the bank adds 360 g on the right.

## 2x conveyor

`files/build_conveyor_2x.py` builds the same conveyor at twice the size (400 x 144 x 78 mm, 60 mm rollers, 80 mm belt), still driven by the TT motor. The 1x conveyor above is unchanged.

| File | Qty | Settings | Orientation |
|------|-----|----------|-------------|
| `conveyor2x_frame_half_A.stl` | 1 | 15-20% gyroid, 4 walls | As exported, base down (one printer) |
| `conveyor2x_frame_half_B.stl` | 1 | 15-20% gyroid, 4 walls | As exported, base down (the other printer) |
| `conveyor2x_drive_roller_gear.stl` | 1 | 4 walls, 15% gyroid, brim; supports on, build plate only | Standing, gear end down |
| `conveyor2x_idle_roller.stl` | 1 | 4 walls, 15% gyroid, brim; supports on, build plate only | Standing |
| `conveyor2x_pinion.stl` | 1 | 100% | Flat |
| `conveyor2x_motor_bracket.stl` | 1 | 100% | As exported, foot down |

Buy or use from stock: 5 M3 x 20 bolts and nuts (frame joint), 2 M3 x 8-10 bolts and nuts (motor bracket, the loose-mount fault), 2 zip ties, a 20 mm steel disc, and duct tape for the belt (about 816 mm loop, 80 mm wide, folded sticky-side in). The belt is too big to print as one TPU tube on a 250 x 210 bed.

Assembly: bolt the two frame halves together through the joint blocks (nuts in the hex pockets on half A, bolt heads in the counterbores on half B). Push the pinion onto the TT motor shaft, zip-tie the motor into the bracket, and bolt the bracket to the pad on half A. Drop both rollers into their slots, with the drive roller's gear outside wall A meshing with the pinion. Then fit the belt.

Rollers: all four pin slots are L-slots (see above). Fit the drive roller before the motor bracket, since the pinion is in the way while the roller is in the long leg; then bolt the bracket on so the pinion meshes.

Drive: the 30-tooth gear on the drive roller and the 15-tooth pinion give 2:1, so the belt gets the same pull and speed as the 1x direct drive. Power the motor from 9 V: two 9 V batteries **in parallel** (+ to +, - to -), not in series. 18 V is over the TB6612's limit and far over the TT motor's 3-6 V rating; 9 V is already above that rating, so expect the motor to wear faster.

Not checked: nobody has measured the TT motor's torque, and the 2x belt and rollers add friction. Before printing everything, run the motor at 9 V and press on the 1x belt: if it stalls easily, use a bigger 9-12 V gear motor instead. Gear teeth are module 1.5 with 0.1 mm backlash per gear; if they bind, scale the pinion to 99% in X and Y.

## Not yet measured

TT motor shaft and gearbox, button cap height, the OLED's glass-to-PCB thickness, and the height of the sockets under the UNO Q. See the UNVERIFIED notes in each script.
