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

## External power bank holder

`device_bank_holder.stl` is a tray that holds an Anker Prime 12,000 mAh (A1335) against the right-hand wall of the handheld, outside the case. The bank inside the case (76 x 48 x 13 mm, 5 V / 1.5 A) is rated below the UNO Q's 5 V / 3 A; the Anker gives 5 V / 3 A over USB-C PD. Bank size is from Anker's spec page (134.4 x 55 x 34.53 mm), not measured.

The bank lies flat with its display facing up, beside the device's own screen, and the tray's top is flush with the device's front face. The Anker's display is on its broad face, near the USB-C end.

1. Stick the tray's back plate to the right-hand wall with command strips (the design leaves 2 mm for them). Keep the strips off the two windows in the plate (cable slot and vents).
2. Slide the bank in from the top end, display up, USB-C end first, until it stops on the two corner stops at the bottom end. It is held by the lips over its long edges.
3. Run a short, thin USB-C cable from the bank's port, under the tray floor, through the window in the back plate and the slot in the wall, to the UNO Q's USB-C.

Print it floor down, as exported, with no supports.

With the tray on, the handheld is about 146 x 145 mm and the bank adds 360 g on the right.

## Not yet measured

TT motor shaft and gearbox, button cap height, OLED board size, MPU board size, and the height of the sockets under the UNO Q. See the UNVERIFIED notes in each script.
