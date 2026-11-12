# Physical design: research report

*Generated: 2026-11-11 | Sources: 9 | Confidence: medium*

Mock-up: [mockup.svg](../mockup.svg)

## Summary

The mounting method is not the risk for this project. Stud, adhesive and magnet mounts all pass vibration cleanly up to about 2,000 Hz or more, and the signals effente needs sit below 500 Hz. The real risks are a mount that flexes or works loose, and a heavy device changing how a small fan vibrates. So the design is a handheld body for the UNO Q, screen and battery, plus a small, solid sensor head holding only the accelerometer. The head snaps onto the machine by magnet and the body never loads the machine.

## 1. Mounting method

- Vendors agree on the ranking: stud mounting is best, adhesive next, magnet worst ([PCB Piezotronics](https://www.pcb.com/Resources/TechnicalInformation/Mounting), [Kistler](https://www.kistler.com/AT/en/mounting-installing-an-accelerometer/C00000130)).
- Rough upper limits: magnet about 2,000 Hz, adhesive 2,500 to 5,000 Hz, stud above 6,000 Hz ([NI](https://www.ni.com/en/shop/data-acquisition/sensor-fundamentals/measuring-vibration-with-accelerometers.html)). These depend on the sensor and vendors' loss figures disagree, so treat them as guides.
- Every extra joint between sensor and machine loses more signal, so fewer joints is better ([Endevco](https://www.endevco.com/contentStore/mktgContent/endevco/dlm_uploads/2019/02/TP319.pdf)).
- Hard instant glue (cyanoacrylate) performs better than wax ([Vibration Research](https://vibrationresearch.com/?p=272)).

Inference: a fan at 1,000 to 3,000 RPM has its once-per-turn peak at 17 to 50 Hz. All three methods are far above that, so choose on repeatability and convenience, not frequency response.

## 2. The mount itself as an error source

- Resonance of the mounting fixture is a common source of error, and a heavier mount lowers the resonant frequency of the whole system ([All About Circuits](https://www.allaboutcircuits.com/technical-articles/accelerometer-mounting-methods-types-effects-and-solutions/)).
- A 2025 Fraunhofer paper studies 3D-printed accelerometer fixtures in four materials and trains a classifier to detect a loose sensor mount from the vibration itself ([Fraunhofer](https://publica.fraunhofer.de/handle/publica/496560)). Unverified which materials. Relevant because a loose sensor looks like a fault.
- PLA softens at roughly 50 to 70 C (single source, from a search excerpt). A warm motor could soften a PLA mount.

Gap: no source quantified the error a printed PLA mount adds. Insufficient data found.

## 3. How commercial sensors do it

- Tractian Smart Trac: one sealed unit, 40 x 40 mm footprint, 155 g plus a 20 g base, rated IP69K, fixed with epoxy or an M8 screw ([Tractian specs](https://faq.tractian.com/en/articles/9145027-smart-trac-technical-specifications), [Tractian install guide](https://faq.tractian.com/en/articles/9125770-smart-trac-fixing-and-installation-methods)). Height is listed as both 71 and 78.5 mm, so unconfirmed.
- Not researched in depth: Augury, SKF, ABB housings.

Inference: commercial units put everything in one body because they sit on large industrial motors. On a small demo fan, 155 g would change the fan's behaviour, which is why effente splits sensor and electronics.

## 4. Part dimensions

| Part | Dimension | Confidence |
|------|-----------|------------|
| Arduino UNO Q | 53.34 mm wide, UNO form factor. Length listed as both 68.58 and 68.85 mm ([DigiKey](https://www.digikey.fr/en/products/detail/arduino/ABX00162/27526619), [Macnica](https://www.macnica.co.jp/en/business/semiconductor/manufacturers/arduino/products/149785/)) | Medium |
| UNO Q mounting holes | Not found. Assumed standard UNO pattern | Unverified |
| GY-521 (MPU6050) | About 21 x 16 mm, two M3 holes, hole spacing possibly 15.5 mm ([Flux Workshop](https://fluxworkshop.com/collections/abd-sensors/products/bdaa100044-gy-521-mpu-6050)) | Medium, spacing unverified |
| GY-291 (ADXL345) | Not found | None |
| 2.4" OLED module | Not researched | None |

Measure every part with calipers before printing. Listings disagree.

## Design decisions

1. **Handheld body plus magnetic sensor head.** The head goes on the machine, the body stays in the hand or parks nearby, joined by a short flexible cable. The head docks on the body for carrying.
2. **Puck is small, solid and low.** Board screwed flat to a thick base with no standoffs. Print at 100% infill. PETG if the motor runs warm.
3. **Fix the puck to the fan body, not to the fan's mounting screws.** The demo loosens those screws, and the sensor must stay rigid while the fan is loose.
4. **Attachment: magnet.** Magnet mounts are usable to about 2,000 Hz, well above what effente needs. On non-steel surfaces, glue a small steel disc at a marked probe point. The magnet gives the same contact force every time; a head pressed on by hand would not (inference, not from the sources).
5. **Same mount for every recording.** Changing it after recording training data invalidates that data.
6. **Cable strain relief on the puck** so a tug on the cable does not shake the sensor. General practice, not from the sources above.
7. **Handheld body:** board on standoffs, screen window in the lid, one button to start a reading, a bay for a USB power bank, a dock for the head, vents.

## Methodology

Five web searches covering mounting methods, commercial sensor housings, UNO Q dimensions, breakout dimensions, and printed mounts. Mostly vendor sources. Search snippets only; no source was read in full.
