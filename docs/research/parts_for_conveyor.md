# Parts for the demo conveyor

The demo machine is a small belt conveyor. The device itself is unchanged by it: the UNO Q handheld with a magnetic sensor head. Build steps: [conveyor-build-guide.md](conveyor-build-guide.md). Tick-list: [conveyor-rig-checklist.md](conveyor-rig-checklist.md).

Three ways to get a conveyor:

- **Printed kit (what the team is building).** Printed frame, rollers and motor bracket sized for the team's TT gear motor. CAD and print-ready STLs: [device/cad/](../../device/cad/README.md).
- **Bought kit.** Search "mini conveyor belt kit DIY". Check that its DC motor can run at a steady voltage and that the motor mount screws and rollers are reachable.
- **Wood build.** Wood frame, PVC rollers, 608 bearings and 8 mm rods, described in the build guide.

## 1. The device

Listed in [parts.md](../parts.md): UNO Q (4 GB), MPU-6500 accelerometer (MPU-9250/6500 breakout), 2.42" OLED, push button, USB power bank, cables, and the sensor head parts (magnet, steel discs, glue, screws, cable ties, printed head).

## 2. The printed conveyor

| Part | Qty | Notes | Status |
|------|-----|-------|--------|
| Printed frame | 1 | 200 x 107 x 39 mm. Open side walls and base, a pad for the motor bracket, a pocket for the probe disc | To print |
| Printed drive roller | 1 | Pushes straight onto the TT motor's output shaft (double-D bore). Holes in its end face take M3 screws as imbalance weights | To print. Test the shaft fit first |
| Printed idle roller | 1 | Printed pins run in slots in the frame. No bearings | To print |
| Printed motor bracket | 1 | Cradle for the TT gearbox | To print |
| Belt | 1 | TPU tube printed in vase mode, or duct tape folded sticky-side in, 40 mm wide, joined into a loop about 409 mm long | Have TPU |
| TT gear motor (yellow, 1:48) | 1, plus a spare | Drives the belt | Have |
| M3 bolts, 8 to 10 mm, and nuts | 2 | Bolt the motor bracket to the frame. These are the loose-mount fault | Need |
| Zip ties, up to about 3.5 mm wide | 2 | Hold the motor in the bracket | Need |
| Steel disc or washer, about 20 mm, magnetic | 1 | The probe point, glued into the frame's pocket. Test it with the magnet: 304 and 316 stainless are not magnetic | Need |
| Grease or petroleum jelly | A dab | On the roller pins | Need |

TT motor shaft and gearbox sizes in the CAD are listing values, not measurements. Measure them before printing (see the CAD README).

## 3. Power (fixed speed)

Every recording needs the same speed.

| Option | Parts | Notes |
|--------|-------|-------|
| Simplest | USB phone charger and a USB cable cut open (or a USB breakout) | Red and black to the motor. 5 V is inside the TT motor's usual 3 to 6 V range |
| With speed control | TB6612FNG driver + Arduino Nano | Nano sets one fixed PWM value. Power the motor side (VM) from its own 5 to 6 V supply and join the grounds |

Keep the conveyor separate from the UNO Q. The device is a probe that visits a machine; it does not power or control it.

## 4. Fault kit

| Fault | Parts | How |
|-------|-------|-----|
| Imbalance (material buildup) | M3 screws, nuts or washers, or modelling clay | A screw in one of the drive roller's weight holes, or clay on one side of the roller |
| Loose mount | Screwdriver | Loosen the 2 motor-bracket bolts one turn |
| Sensitivity sweep | Several sizes of weight | Smaller and smaller weights |
| Unknown (held out) | Washer | Under one side of the motor bracket |
| Belt damage (stretch only) | Duct tape | A lump on the belt. Not one of the current fault classes |

## 5. Where the sensor head goes

- The printed frame has a pocket for the steel disc on the outside of the wall opposite the motor, beside the drive roller. On a wood build, glue the disc on the side rail next to the drive-roller bearing.
- Never on the motor bracket. The demo loosens it, and the sensor must stay rigid.
- Same spot for every recording.

## 6. Tools

- 3D printer, PETG or PLA, and TPU for the belt.
- Screwdriver, instant glue, calipers.
- Phone slow-motion video or a tachometer, to measure the roller's turns per second for the model team.
- Wood build only: drill with 22 mm and 8 mm bits, saw.

No prices are listed, since none were checked.
