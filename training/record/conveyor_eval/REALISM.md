# How realistic the simulated conveyor is, and where each number comes from

`effente/simulate_conveyor.py` in realistic mode (`ConveyorSim(realistic=True)`) plus `effente/site_noise.py` (factory profile), as used by [generate.py](generate.py). Everything is simulated. A number with a source is anchored to published figures. **Unverified** means no source was found and the value is a plausible guess.

## The machine
- **Drive:** an industrial belt conveyor. A 4-pole motor turns at about 1,770 rpm (60 Hz mains, 1–3 % slip under load) and drives a two-stage gearbox (4:1, then 5:1) and a 0.5 m drive pulley.
- **Belt:** 60 m long, running at about 2.3 m/s, so it laps every 26 s. It has one mechanical splice and idlers of about 125 mm. Bulk conveyors typically run at about 2–2.5 m/s with 4–6 inch idler rolls ([CEMA guidance](https://pdhonline.com/courses/m344/m344content.pdf), [CKIT](https://www.ckit.co.za/secure/tech-focus/idlers/idlers.htm)).
- **Week:** two shifts on weekdays (06:00–22:00) with a lunch and an evening break running empty, Saturday 06:00–14:00, Sunday off. Load varies with the shift, with surges.
- **Mounting:** the device is fixed to the drive-end bearing housing, between the gearbox and the head pulley. The thermal camera and microphone point at the drive from close by.

## Healthy levels against sources

| Reading | Simulated, healthy at full load | Source |
|---|---|---|
| Drive vibration velocity | 1.7 mm/s | Healthy conveyor drives sit around 2 mm/s, and faults go to tens of mm/s ([Reliable Plant case study](https://reliableplant.com/Read/32715/case-study-efficient-maintenance-saves-thousands-in-gearbox-replacement-costs)) |
| Accelerometer noise | 3.5 mg rms per axis | MPU-6500: 300 µg/√Hz ([TDK](https://invensense.tdk.com/)), about 5 mg over its 218 Hz band. Our own board measured 2–4 mg at rest |
| Gravity | 1.04 g | MPU offsets up to ±60 mg (TDK). Our board read 1.018 g |
| Motor frame | about +22–26 °C above the room (42 °C at a 20 °C room) | A standard TEFC frame reads 40–50 °C, and the frame runs 15–25 °C under the windings ([IEEE 841 and NEMA MG 1 summaries](https://www.baldor.com/Shared/pdf/tefcspec.pdf)) |
| Gearbox | up to +16 °C | Mineral gear oil should stay under 75–80 °C ([Silmid](https://www.silmid.com:443/Documents/Brochures/B023001002_Gears_EN_1115_compressed%20(1)%20(2).pdf)) |
| Idlers | +3 °C | A few °C above ambient is normal, and ambient + 20 °C is the allowed limit ([Pytlik and Trela 2016](https://yadda.icm.edu.pl/baztech/element/bwmeta1.element.baztech-d7f1cd4f-f149-43f2-90bc-6ec4f5da2a99/c/JSM_2016_2_Pytlik_Trela.pdf)) |
| Sound at the drive | 76 dB (shift), 50 dB (night) | Conveyor areas run 70–85 dBA, with conveyors often setting the background ([AIHA](https://publications.aiha.org/202210-mitigate-conveyor-noise), [Commercial Acoustics](https://commercial-acoustics.com/soundproofing/factory/)) |
| Factory background | 70 dB in shifts; pink noise falling above 2 kHz | Broadband, strongest at roughly 63–500 Hz ([NASA SP-5108](https://ntrs.nasa.gov/api/citations/19820003985/downloads/19820003985.pdf)). The exact shape is **unverified** |
| Motor hum | 120 Hz and 240 Hz | Twice-line-frequency magnetic forces ([motor noise papers](https://www.intechopen.com/chapters/40901)). The level is **unverified** |
| Gear mesh, belt and rolling noise | 200 Hz – 4 kHz | Drive noise is strongest at 200–1000 Hz; belt and idler noise at 1–2 kHz ([MDPI 2021 lab study](https://www.mdpi.com/2071-1050/13/4/2233)). Levels are **unverified** |
| Splice knock | once per 26 s lap, 74 dB | Belt joints are the main impulsive confounder in idler monitoring ([Shiri et al. 2021](https://eprints.soton.ac.uk/502431/1/energies-14-07646.pdf)). The level is **unverified** |
| IMU temperature | follows the housing with a 10-minute lag, plus 9 °C of device warmth | **Unverified.** Our board on a desk reads about 28 °C, a few °C above the room. An earlier reading of about 20 °C above the room was a conversion error, fixed on 13 Nov: the MPU-6050's formula had been used for the MPU-6500. So 9 °C may be on the high side. The models learn this machine's own normal, so a constant offset doesn't change what they catch |

## Healthy vibration varies, as real drives do
- **Before 13 Nov:** the first generation's healthy vibration was almost constant: velocity 1.675–1.695 mm/s across a week, about 1 %. Every fault then looked enormous, and false alarms were unrealistically rare (found by the data check).
- **Real constant-speed drives** wander roughly 10–20 % with load, oil temperature, belt tension and material. That's why alarm levels sit at 2–2.5× baseline in SKF and ISO 20816 practice.
- **Now each vibration source** (motor, gear mesh, pulley, idlers, broadband) has its own slow lognormal drift over two days and over two hours, plus day to day. The motor and the gears also work harder under load, and warm oil damps the mesh slightly.
- **Checked on 75 snapshots over 5 days:** healthy velocity has a median of 1.77 mm/s and a typical (robust) spread of 16.6 %. The 1–99 % range is 1.37–2.64 mm/s. The overall level's spread is 13.9 %.
- **The size of the wander is unverified.** It's chosen to match that 10–20 % range, not measured on a particular conveyor.

## Known weakness of the device's speed reading (not of the data)
The device reads running speed as the strongest peak at 8–120 Hz (`effente/features.py`). When misalignment makes the twice-per-turn vibration bigger than the once-per-turn, the reading jumps to double the true speed, and every per-turn reading (1×, 2×, half order) is then off by a factor of 2. A real device would do the same. So misalignment descriptions are unreliable with this tracker, and detection gets a boost from a "speed doubled" jump no real belt makes. The fix (prefer half the frequency when there's a real peak there) changes the shared signal pipeline, so it needs the team's agreement first.

## The sensors as they behave
- **MPU-6500:** 1 kHz, ±8 g (clips beyond), its 218 Hz low-pass, 3.5 mg noise. The temperature reading comes in steps of 1/340 °C, as the chip reports it.
- **MLX90640-D55:** 32×24 pixels, 55°×35°, 4 frames a second. Its noise is about 0.2 K at that rate (the datasheet gives 0.1 K at 1 Hz), it has a fixed pattern, and the absolute error is a fixed offset of up to ±1.5 °C per device ([Melexis](https://media.melexis.com/-/media/files/documents/datasheets/MLX90640-datasheet-melexis.pdf), [Waveshare](https://www.waveshare.com/mlx90640-d55-thermal-camera.htm)). Site noise adds the checkerboard between its two half-frames and a slow drift.
- **INMP441:** −26 dBFS at 94 dB SPL, 33 dBA input noise (61 dB SNR), recorded at 16 kHz ([TDK](https://invensense.tdk.com/digital/inmp441)). The 60 Hz low-frequency roll-off isn't modelled.
- **No room sensor:** the device doesn't have one.

## Disturbances that are not faults (site_noise, factory)
- **Sound:** dropped tools and door slams (80–100 dB), voices, and forklifts (70–84 dB). About 30 % of forklifts have a reversing alarm at 1–2.8 kHz; real alarms are 1–2.8 kHz at 95–110 dB at 1 m ([Hella](https://www.hella.com/forvia-us/assets/documents/BI_Backup_alarms_2026.pdf), [Brigade](https://brigade-electronics.com/en-ca/warning-systems/reversing-and-warning-alarms/)).
- **Machines and people:** a neighbouring machine, people walking past or standing in the camera's view, hot objects left in view, and sun through a window.
- **The device:** now and then someone knocks it, leaving a lasting tilt of a degree or two. Short sensor dropouts happen too.
- **The held-out week:** 4 °C warmer, about 10 % busier, and a 2-hour maintenance stop.

Rates are **unverified** starting values.

## Faults (FAULT_KINDS)
Severity 1 is obvious and 0.3 is subtle.
- **Slow faults** (idler bearing, gear wear, motor bearing, lubrication loss) grow over their window slowly, then faster. The shape follows the NASA IMS run-to-failure bearing ([NASA PCoE](https://www.nasa.gov/intelligent-systems-division/discovery-and-systems-health/pcoe/pcoe-data-set-repository/)).
- **The others** switch on within a minute.

Patterns follow the SKF vibration diagnostic guide ([CM5003](https://forums.ni.com/attachments/ni/170/107329/1/CM5003%20Vibration%20Guide1.pdf)) and conveyor sources. Exact sizes are **unverified**.

| Fault | What changes | Obvious (subtle is about 0.3 of it) |
|---|---|---|
| unbalance | 1× motor speed, radial, the two axes 90° apart | velocity 1.7 → 8 mm/s (subtle: 3.4) |
| looseness | harmonics 2–10×, 0.5×, a rattle once a turn, rocking (gyro), a rattle sound | spiky vibration (kurtosis +2.6), rattle |
| misalignment | 2× above 1×, strong axially; the coupling warms | 2× about 3 times higher |
| gear_wear | mesh and its sidebands grow, 2× mesh; mesh whine; the gearbox warms | sidebands 6 times higher, whine +14 dB, +10 °C |
| motor_bearing | pulses at about 3.58× motor speed, high-frequency hiss; the motor warms | +6 °C, rough hiss |
| idler_bearing | pulses at 3.6× idler speed at the far idler; hiss; the idler warms | +25 °C at the idler |
| idler_seized | the idler stops; squeal near 1.6 kHz (MDPI 2021: idler tone near 1.58 kHz); hot | +35 °C, +12 dB tone |
| belt_rub | squeal near 2.8 kHz, a hot spot at the belt edge | +18 °C, 84 dB squeal |
| belt_slip | the belt runs up to 10 % slower than the pulley; stick-slip squeal near 2.5 kHz in bursts; the pulley warms | +15 °C. Slip noise jumps about 13 dB at onset (belt patents) |
| splice_damage | the splice knock grows | 5× (subtle 2.2×) |
| jam | obvious: the belt stops dead with a jolt, restarts after 3 min. Subtle: five 5-second bog-downs | 1.6 g jolt (subtle 0.35 g) |
| overload | load up to 1.6× | motor and gearbox hotter, louder material noise |
| lubrication_loss | gearbox heats, broadband friction, hiss | +20 °C |
| cooling_blocked | heat only: the motor fan cover is blocked | motor +25 °C |
| electrical | twice-mains (120 Hz) vibration and hum grow; the motor warms | hum +12 dB, +8 °C |

## Checked against real data
- **Healthy levels:** matched to the targets in the table above.
- **Accelerometer:** noise and gravity match our own board at rest.
- **Fault shapes:** wear growth copies the shape of NASA's real run-to-failure bearing. Faults that ought to be invisible at the drive end are: a subtle idler bearing far down the belt hardly shows at the drive housing. That's realistic, and the evaluation will show it.
- **Not checked:** a real conveyor recording of this kind. None was found that's public with all three sensors.
