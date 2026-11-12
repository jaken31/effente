# Physical design: research report

*Generated: 2026-11-11 | Sources: 9 | Confidence: medium*

Mock-up: [mockup.svg](../mockup.svg). As built: [device/cad/](../../device/cad/README.md) (CAD and print files). Sections 4 and the design decisions were updated on 2026-11-12 to match the parts the team has and the CAD.

## Summary

The mounting method is not the risk for this project. Stud, adhesive and magnet mounts all pass vibration cleanly up to about 2,000 Hz or more, and the signals effente needs sit below 500 Hz. The real risks are a mount that flexes or works loose, and a heavy device changing how a small machine vibrates (the demo machine is now a small printed conveyor, see [conveyor-build-guide.md](conveyor-build-guide.md)). So the design is a handheld body for the UNO Q, screen and battery, plus a small, solid sensor head holding only the accelerometer. The head snaps onto the machine by magnet and the body never loads the machine.

## 1. Mounting method

- Vendors agree on the ranking: stud mounting is best, adhesive next, magnet worst ([PCB Piezotronics](https://www.pcb.com/Resources/TechnicalInformation/Mounting), [Kistler](https://www.kistler.com/AT/en/mounting-installing-an-accelerometer/C00000130)).
- Rough upper limits: magnet about 2,000 Hz, adhesive 2,500 to 5,000 Hz, stud above 6,000 Hz ([NI](https://www.ni.com/en/shop/data-acquisition/sensor-fundamentals/measuring-vibration-with-accelerometers.html)). These depend on the sensor and vendors' loss figures disagree, so treat them as guides.
- Every extra joint between sensor and machine loses more signal, so fewer joints is better ([Endevco](https://www.endevco.com/contentStore/mktgContent/endevco/dlm_uploads/2019/02/TP319.pdf)).
- Hard instant glue (cyanoacrylate) performs better than wax ([Vibration Research](https://vibrationresearch.com/?p=272)).

Inference: a fan at 1,000 to 3,000 RPM has its once-per-turn peak at 17 to 50 Hz. The demo conveyor's drive roller turns only a few times a second and its motor shaft faster (neither measured yet). All are far below the mounting limits above, so choose on repeatability and convenience, not frequency response.

## 2. The mount itself as an error source

- Resonance of the mounting fixture is a common source of error, and a heavier mount lowers the resonant frequency of the whole system ([All About Circuits](https://www.allaboutcircuits.com/technical-articles/accelerometer-mounting-methods-types-effects-and-solutions/)).
- A 2025 Fraunhofer paper studies 3D-printed accelerometer fixtures in four materials and trains a classifier to detect a loose sensor mount from the vibration itself ([Fraunhofer](https://publica.fraunhofer.de/handle/publica/496560)). Unverified which materials. Relevant because a loose sensor looks like a fault.
- PLA softens at roughly 50 to 70 C (single source, from a search excerpt). A warm motor could soften a PLA mount.

Gap: no source quantified the error a printed PLA mount adds. Insufficient data found.

## 3. How commercial sensors do it

- Tractian Smart Trac: one sealed unit, 40 x 40 mm footprint, 155 g plus a 20 g base, rated IP69K, fixed with epoxy or an M8 screw ([Tractian specs](https://faq.tractian.com/en/articles/9145027-smart-trac-technical-specifications), [Tractian install guide](https://faq.tractian.com/en/articles/9125770-smart-trac-fixing-and-installation-methods)). Height is listed as both 71 and 78.5 mm, so unconfirmed.
- Not researched in depth: Augury, SKF, ABB housings.

Inference: commercial units put everything in one body because they sit on large industrial motors. On a small demo machine, 155 g would change the machine's behaviour, which is why effente splits sensor and electronics.

## 4. Part dimensions

| Part | Dimension | Confidence |
|------|-----------|------------|
| Arduino UNO Q | 53.34 mm wide, UNO form factor. Length listed as both 68.58 and 68.85 mm ([DigiKey](https://www.digikey.fr/en/products/detail/arduino/ABX00162/27526619), [Macnica](https://www.macnica.co.jp/en/business/semiconductor/manufacturers/arduino/products/149785/)). Team measured about 55 x 70 mm and 10 mm tall including the female headers. JMEDIA and JMISC sockets on the underside, USB-C on a short edge | Medium. Underside socket height not measured |
| UNO Q mounting holes | Standard UNO pattern, as used in the CAD | Medium |
| MPU-9250/6500 breakout (the team's accelerometer) | About 26 x 16 mm (listing 26.16 x 15.57 mm; the team's photo matches the shape). 10-pin header along a long edge, soldered with the plastic and long pins on the back. Two holes on the opposite edge | Unverified, not measured |
| GY-521 (MPU6050) | About 21 x 16 mm, two M3 holes, hole spacing possibly 15.5 mm ([Flux Workshop](https://fluxworkshop.com/collections/abd-sensors/products/bdaa100044-gy-521-mpu-6050)). No longer used | Medium, spacing unverified |
| GY-291 (ADXL345) | Not found | None |
| 2.42" OLED module (SSD1309, I2C) | About 70 x 42 mm, estimated from the team's photo. 5-pin header (GND, VCC, SCL, SDA, RES) on a short edge, pins out the back. 4 corner holes. Waveshare's version is 61.5 x 39.5 mm, so boards differ | Unverified, not measured |
| Push button | 12 x 12 mm tactile switch with a round cap (team's figure). Cap height not measured | Medium |
| USB power bank | 76.2 x 48.3 x 12.7 mm (3 x 1.9 x 0.5 in, team's figure). Label: 2000 mAh, 5 V / 1.5 A output. The UNO Q datasheet asks for 5 V / 3 A, so this bank is likely too weak | Medium. Replacement needed |
| MLX90640-D55 thermal camera (optional) | 28 x 16 mm PCB, 2 mm hole ([Waveshare wiki](https://www.waveshare.com/wiki/MLX90640-D55_Thermal_Camera)) | Medium |
| INMP441 microphone (optional) | Round board, about 14 mm, two rows of 3 pins. UNO Q I2S support not confirmed | Unverified |

Measure every part with calipers before printing. Listings disagree.

## Design decisions

1. **Handheld body plus magnetic sensor head.** The head goes on the machine, the body stays in the hand or parks nearby, joined by a short flexible cable. The head docks on the body for carrying.
2. **Puck is small and solid.** Print at 100% infill. PETG if the motor runs warm. As built, the head is 31 x 35 x 23 mm, taller than the "low" puck this report recommends: the MPU board keeps its soldered header and jumper plugs, so it stands upright in a printed cradle, clamped by a rib on the lid, with the plugs lying flat inside. Desoldering the header and wiring the cable straight to the pads would let the board lie flat and the head be low again.
3. **Fix the puck to the machine's frame, not to the motor's mounting screws.** The demo loosens those screws, and the sensor must stay rigid while the motor is loose. On the demo conveyor the probe point is a steel disc in a pocket on the frame's side wall, away from the motor bracket.
4. **Attachment: magnet.** Magnet mounts are usable to about 2,000 Hz, well above what effente needs. On non-steel surfaces, glue a small steel disc at a marked probe point. The magnet gives the same contact force every time; a head pressed on by hand would not (inference, not from the sources).
5. **Same mount for every recording.** Changing it after recording training data invalidates that data.
6. **Cable strain relief on the puck** so a tug on the cable does not shake the sensor. General practice, not from the sources above.
7. **Handheld body:** board on standoffs, screen window in the lid, one button to start a reading, a bay for a USB power bank, a dock for the head, vents. As built (84 x 145 x 48 mm): the UNO Q sits on pins and snap hooks with room for straight jumpers in its headers, the OLED and the MPU board sit in small printed adapters that can be reprinted alone, the button snaps into a sleeve, and the thermal camera and microphone sit in the top wall, not in the sensor head.

## Methodology

Five web searches covering mounting methods, commercial sensor housings, UNO Q dimensions, breakout dimensions, and printed mounts. Mostly vendor sources. Search snippets only; no source was read in full.
