Here’s everything for a conveyor demo, matched to your repo’s design: the UNO Q handheld with a magnetic sensor head.

One thing to agree first: the PRD and parts list use a fan as the demo machine. Switching to a conveyor changes the demo rig only; the device itself stays the same. Since docs/ belongs to Track 5, tell the team and let them update the docs. Keep the fan as a backup.

1. The device (unchanged from the repo)

Already on the repo’s parts list, so nothing new to buy:

Arduino UNO Q (4 GB), MPU6050 (GY-521), 2.4" OLED, push button.
USB power bank, USB-C cable, sensor cable, jumper wires, breadboard.
Sensor head: neodymium magnet, steel discs, instant glue, M3 screws, cable ties, the printed head.
2. The conveyor (new)

Option A: buy a mini conveyor kit (fastest). Search “mini conveyor belt kit DIY”. Check that it has a DC motor you can run at a steady voltage, and that you can reach the motor mount screws and the rollers.

Option B: build it. Parts:

Part	Qty	Purpose
Wood base board, about 45 × 15 cm	1	Rigid base, so the loose-mount fault is repeatable
Wood side rails, about 45 × 6 cm	2	Hold the roller bearings
608 skateboard bearings	4	Two per roller
8 mm steel rod, about 20 cm	2	Roller axles
PVC pipe (about 32 mm wide, 10 cm long) + end caps	2 + 4	The rollers (3D-printed also works)
Rubber strip or resistance band, about 9 cm wide	1	The belt
DC gear motor, 6–12 V, about 100–300 rpm	1 + spare	Drives the belt
Shaft coupler (motor shaft → 8 mm)	1	Connects the motor to the drive roller
L-bracket + 2 wood screws	1	Motor mount. These screws become the “loose mount” fault
Shaft collars (8 mm) or tape	2–4	Stop the rollers sliding sideways
Wood screws	About 10	Frame
3. Power for the conveyor (fixed speed)

The TRD needs one fixed speed for every recording.

Part	Purpose
Regulated DC power supply matching the motor (e.g. 9 or 12 V, 1 A+)	Steady speed
Or a DC buck converter (adjustable step-down) plus a wall adapter	Set the voltage once, then leave it
On/off switch + wires	Start and stop the belt

Keep the conveyor’s motor separate from the UNO Q. The device is a probe that visits a machine; it shouldn’t power or control it. That’s realistic, and it means you don’t need a motor driver board.

4. Fault kit
Fault	Part	How
Imbalance (material buildup)	Modelling clay or coins + tape	Stick it on one side of the drive roller
Loose mount	Screwdriver	Loosen the 2 motor-bracket screws
Sensitivity sweep	Several sizes of weight	Smaller and smaller clay blobs
Extra fault (stretch only)	Duct tape	A lump on the belt for “belt damage”. Not in the current 3 classes
5. Where the sensor head goes
Glue a steel disc on the side rail, right next to the drive-roller bearing, as the probe point.
Don’t put it on the motor bracket. The demo loosens those screws, and the sensor must stay rigid (from the repo’s physical design notes).
Use the same spot for every recording, and mark it.
6. Tools
Drill: 22 mm bit for the bearings, 8 mm bit for the end caps.
Saw, screwdriver, hot glue or epoxy.
Calipers: the repo says measure every part before designing.
Phone slow-motion video or a tachometer, to measure the roller’s turns per second, which the model team needs.
7. Before buying
Measure the motor’s shaft size so the coupler fits.
Match the power supply to the motor’s voltage and current.
Do a test run before recording: the belt runs straight, doesn’t slip, and the healthy run is smooth.
No prices are listed, since none were checked. Look them up, as the repo’s parts list does.

The full step-by-step build (drawings, wiring, tips) is in the Mini Conveyor Build Guide page I made earlier. Use steps 1–7 and 10–11, and skip the ESP32 wiring, since your repo uses the UNO Q.

I tried the push again and GitHub still refused it (error 403). jaken31/effente isn’t connected to this session with write access, so I can’t push from here.

Your two Track 4 commits are safe on the track4/unit-analysis branch here. To get them onto GitHub, either connect the repo with write access in this session’s settings and then say “added”, or upload interp_track4.zip yourself on GitHub, onto a new branch called track4/unit-analysis.
