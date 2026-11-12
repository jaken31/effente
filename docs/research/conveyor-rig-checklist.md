# Conveyor rig checklist

Tick items as you get parts and finish steps. On GitHub, the boxes become clickable when this file is pasted into an issue or pull request description.

| Marker | Route |
|---|---|
| 🟫 | Wood route |
| 🟦 | 3D-printed route |
| ⬜ | Both routes |

Pick **one** conveyor option: A (kit), B (wood) or C (3D-printed).

## Device (already on the repo's parts list)

- [ ] ⬜ Arduino UNO Q (4 GB)
- [ ] ⬜ MPU6050 accelerometer (GY-521)
- [ ] ⬜ 2.4" OLED screen
- [ ] ⬜ Push button
- [ ] ⬜ USB power bank + short USB-C cable
- [ ] ⬜ Sensor cable (30–50 cm), jumper wires, breadboard
- [ ] ⬜ Sensor head: magnet, steel discs, instant glue, M3 screws, cable ties, printed head

## Conveyor: option A (kit)

- [ ] ⬜ Mini conveyor belt kit with a DC motor (check the motor mount screws and rollers are reachable)

## Conveyor: option B (wood)

- [ ] 🟫 Wood base board, about 45 × 15 cm
- [ ] 🟫 2 wood side rails, about 45 × 6 cm
- [ ] 🟫 2 × PVC pipe (about 32 mm wide, 10 cm long) + 4 end caps
- [ ] 🟫 Metal L-bracket + 2 screws (motor mount; these screws become the loose-mount fault)
- [ ] 🟫 About 10 wood screws for the frame

## Conveyor: option C (3D-printed parts + solid base)

A fully printed frame flexes and adds its own vibration, so print the detailed parts and keep one solid base.

- [ ] 🟦 Solid base: wood board or aluminium extrusion (e.g. 2020 profile), about 45 cm
- [ ] 🟦 2 printed rollers with 608 bearing pockets (or solid on an 8 mm rod), printed true and centred
- [ ] 🟦 Printed side plates / bearing blocks, with a tension slot at the tail end
- [ ] 🟦 Printed motor mount bracket, held down by 2 screws (the loose-mount fault)
- [ ] 🟦 Printed belt guides (optional)
- [ ] 🟦 Screws to fix printed parts to the base

## Shared conveyor parts (options B and C)

- [ ] ⬜ 4 × 608 skateboard bearings
- [ ] ⬜ 2 × 8 mm steel rods, about 20 cm (or M8 bolts)
- [ ] ⬜ Rubber strip or resistance band, about 9 cm wide (the belt)
- [ ] ⬜ DC gear motor, 6–12 V, about 100–300 rpm (+ 1 spare)
- [ ] ⬜ Shaft coupler (motor shaft size → 8 mm). Measure the motor shaft first
- [ ] ⬜ 2–4 shaft collars (8 mm) or tape

## Conveyor power (fixed speed)

- [ ] ⬜ Regulated DC supply matching the motor (e.g. 9 or 12 V, 1 A+), or an adjustable buck converter + wall adapter
- [ ] ⬜ On/off switch + wires
- [ ] ⬜ Conveyor motor kept separate from the UNO Q

## Fault kit

- [ ] ⬜ Modelling clay or coins + tape (imbalance: buildup on the drive roller)
- [ ] ⬜ Screwdriver (loose mount: loosen the 2 mount screws)
- [ ] ⬜ Several sizes of weight (sensitivity sweep)
- [ ] ⬜ Washers (misalignment: held out for the "unknown" test)
- [ ] ⬜ Duct tape (belt damage: stretch only, not in the current 3 classes)
- [ ] ⬜ Rubber mat for the table

## Sensor placement

- [ ] ⬜ Steel disc glued on the side next to the drive-roller bearing (probe point)
- [ ] ⬜ Probe point marked; same spot for every recording
- [ ] ⬜ Sensor NOT on the motor mount (it gets loosened in the demo)

## Tools

- [ ] 🟫 Drill + 22 mm bit (bearings) + 8 mm bit (end caps)
- [ ] 🟫 Saw
- [ ] 🟦 3D printer + PETG filament (PLA softens at about 50–70 °C)
- [ ] ⬜ Screwdriver
- [ ] ⬜ Hot glue or epoxy
- [ ] ⬜ Calipers (measure every part)
- [ ] ⬜ Phone slow-motion video or tachometer (roller speed)

## Before recording

- [ ] ⬜ Motor shaft measured and coupler fits
- [ ] ⬜ Power supply matches motor voltage and current
- [ ] 🟦 Bearing-pocket fit checked on a test print (22 mm bearings)
- [ ] 🟦 Rollers, bearing blocks, motor mount and sensor puck printed at 100% infill
- [ ] ⬜ Rollers spin with no visible wobble
- [ ] ⬜ Belt runs straight and doesn't slip
- [ ] ⬜ Healthy run is smooth
- [ ] ⬜ Roller speed measured and shared with the model team
- [ ] ⬜ All training data recorded on this same finished rig
- [ ] ⬜ Prices checked on live listings (none have been checked yet)
