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

## Measured parts

- **MPU board:** 25 x 15 mm, 10 mm thick with its header. The sensor head is now 29 x 33.5 x 21.5 mm, the smallest that holds the board upright plus its jumper plugs. The top 3.5 mm of the IMU cradle is open front and back, because the 10-pin header runs almost the full length of the board.
- **Head lid clamp:** two snap clips on the head lid's long sides sit flush in recesses in the head's walls, so the head still fits its dock. Their wedge hooks click into small windows in the walls. To open, push each clip's foot out with a fingernail (there is a gap below it) and lift the lid. PETG is better than PLA for the clips.
- **OLED:** 72 x 43 mm, 13 mm thick with its header pins. The screen tray's pocket fits it; the tray's outside size and the lid hooks are unchanged. The glass-to-PCB thickness (4.5 mm) is estimated from the 13 mm, not measured.
- **SCAN button:** the snap lips that hold the switch are 1.8 mm (3x the old 0.6 mm). The button moved 2 mm down the lid so the snap fingers can bend out past the screen tray's hook. The bigger lips bend the fingers further, so print the front lid in PETG if you can; PLA fingers may crack.

## External power bank holder

`device_bank_holder.stl` is a tray that holds an Anker Prime 12,000 mAh (A1335) against the right-hand wall of the handheld, outside the case. The bank inside the case (76 x 48 x 13 mm, 5 V / 1.5 A) is rated below the UNO Q's 5 V / 3 A; the Anker's USB-C gives 5 V / 3 A. Bank size is from Anker's spec page (134.4 x 55 x 34.53 mm), not measured.

The bank lies flat, display up, USB-C end at the top of the device, so its battery screen sits beside the device's OLED. Covers flush with the device's front face hide the rest of the bank, including the Anker logo; a window shows only the screen. The window position is estimated from a photo (screen about 13 to 61 mm from the USB-C end): check it against your bank and adjust `BANK['win']` if needed.

1. Stick the tray's back plate to the right-hand wall with command strips (the design leaves 2 mm for them). Keep the strips off the two windows in the plate (cable slot and vents).
2. Slide the bank in from the top end, display up, far end first, until it hits the end wall. Two latch tongues in the floor click up behind it. To take it out, press both tongues down from underneath and slide the bank out.
3. Run a short, thin USB-C cable from the bank's port, down past the end of the tray, back along under the tray floor, through the window in the back plate and the slot in the wall, to the UNO Q's USB-C.

Print it floor down, as exported, with no supports. The latch tongues must flex, so PETG is better than PLA here.

With the tray on, the handheld is about 146 x 145 mm and the bank adds 360 g on the right.

## Not yet measured

TT motor shaft and gearbox, button cap height, the OLED's glass-to-PCB thickness, and the height of the sockets under the UNO Q. See the UNVERIFIED notes in each script.
