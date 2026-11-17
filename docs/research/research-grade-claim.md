# Research-grade claim: what it takes

Written 17 Nov 2026. Research and a plan only. Nothing here was built or measured for this doc, apart from one simulator check in section 2.

How to read the labels:

- **(repo)**: a number already in this repo, with the file it comes from.
- **(datasheet)**: a maker's figure, found through a web search of the datasheet. Not measured by us.
- **(secondary)**: from a vendor page or blog, not the standard or datasheet itself. Check before relying on it.
- **(my arithmetic)**: worked out here from the figures above.
- **Unverified**: not checked at all. All are listed again in section 11.

No prices are listed because none were checked.

## 1. What the claim can say, and when

"Cutting-edge" and "research-grade" are earned by the measurement chain and by evidence, not by a parts list. Three levels, each with the wording it supports:

| Level | What exists | Wording it supports |
|---|---|---|
| 0. Today | MPU-6500 at 1 kHz, simulated validation, one public bearing set | "Prototype. Detects large changes from a machine's own normal." |
| 1. Instrument | Wide-band low-noise sensor, lossless timed sampling, stiff mount, calibrator check, side-by-side run against a lab accelerometer | "Wide-band, calibrated, checked against a reference instrument." |
| 2. Diagnostics | Level 1, plus a speed reference, a reference channel, real faults from our own rig, and public benchmarks rerun on the new sensor | "Research-grade diagnostics, with noise rejection measured on real machines." |

Do not use level 2 wording before the level 2 evidence exists (AGENTS.md, Truthfulness).

## 2. Where the device stands today

| Item | Today | Source |
|---|---|---|
| Accelerometer | MPU-6500, +-8 g, 1 kHz, 218 Hz low-pass | `device/app/effente/sketch/sketch.ino` (repo) |
| Sensor noise | 300 ug/rtHz, 2 to 4 mg rms per axis measured at rest | `training/record/conveyor_eval/REALISM.md`, `device/app/README.md` (repo) |
| Sampling | I2C at 400 kHz. About one FIFO restart every 5 s, about 0.7 % of samples lost | `device/app/README.md` (repo) |
| Link, microcontroller to Linux | 460800 baud on this board (115200 by default) | `device/app/README.md` (repo) |
| Velocity reading | 10 to 250 Hz only; the ISO band is 10 to 1000 Hz | `docs/model-lifecycle.md` (repo) |
| Running speed | The strongest peak in each 1 s window, 8 to 120 Hz | `effente/features.py` (repo) |
| Mount | Printed puck on a magnet. Resonance never measured | `docs/parts.md` (repo) |
| Sound | INMP441 through an ESP32. Only worked-out readings reach the device, not raw sound | `device/app/README.md` (repo) |
| Real bearing faults | Caught 35 of 40. Named `bearing_defect_like` in 1 of 40 files | `training/record/datasets/OTTAWA_RESULTS.md` (repo) |
| Validation | No row comes from a device recording | `training/validation/REPORT.md` (repo) |
| Calibration against a known vibration | None | |

One check run for this doc, on master's simulator (`ConveyorSim` realistic mode, with and without `SiteNoise`, same seconds, 120 running and 104 stopped windows):

- Conveyor running: the neighbouring machine moves one reading, the 32 to 40 Hz band, by 4.4 healthy spreads. Speed, once-per-turn and velocity do not move.
- Conveyor stopped: the speed reading locks onto the neighbour's 34 Hz tone in 80 % of windows.
- The simulated neighbour is 2 to 6 mg on one axis, which REALISM.md marks as unverified. So this shows where the pipeline is fragile, not how bad a real site is.

## 3. Target specification

The measurement chain, end to end:

```
 machine
    |
 stiff metal head on a magnet or glued pad      resonance measured, well above the band used
    |
 wide-band accelerometer  ----+                 5 kHz or more, under 100 ug/rtHz
 reference accelerometer  ----+-- one clock     on the frame or floor, same sample clock
 tachometer pulse         ----+                 once per shaft turn
    |
 timed, lossless sampling                       20 kHz or more, 0 lost samples
    |
 full-rate path to the Linux side               about 160 kB/s for three axes
    |
 features, model, raw clips around alarms       on the device
    |
 laptop: training, review, reports              on site, no outside service
```

| Property | Target | Why |
|---|---|---|
| Usable band | 10 Hz to at least 5 kHz | Bearing impacts ring at kilohertz frequencies (OTTAWA_RESULTS.md's hypothesis for the naming failure). The ISO velocity band needs 10 to 1000 Hz |
| Noise density | Under 100 ug/rtHz | The line Analog Devices draws for "high performance" condition monitoring sensors (secondary) |
| Sample rate | At least 2.56 times the top of the band | The usual analyser rule: it leaves room for the anti-alias filter. From general practice, not found in a standard here (unverified) |
| Lost samples | 0 over a one-hour recording | A gap smears every spectrum line |
| Sample timing | Sensor's own clock or a hardware timer, never the Linux side | Already a TRD rule |
| Resolution | One count well under the noise in the band | So the sensor's noise, not the converter, sets the floor |
| Range | +-16 g or more without clipping | Jams and knocks; the device already counts clipping |
| Mount resonance | Measured, at least 3 times the top of the band used | Rule of thumb from a mounting guide (secondary). Magnets are usable to roughly 2 kHz; a glued pad or stud goes higher |
| Amplitude accuracy | Within the calibrator's own tolerance at 159.2 Hz | So mm/s readings can be compared with ISO zones |
| Speed reference | Once-per-turn pulse | Makes shaft-locked averaging exact, and ends the "speed doubled" error in REALISM.md |
| Reference channel | Second accelerometer, same clock | Lets the device tell machine vibration from the surroundings |

## 4. Parts

### Accelerometers

| | MPU-6500 (today) | IIS3DWB | ADXL1002 |
|---|---|---|---|
| Axes | 3 | 3 | 1 |
| Output | Digital, I2C or SPI | Digital, SPI, 16 bit | Analog, 40 mV/g |
| Band (3 dB) | 218 Hz as set | 6.3 kHz typical, 5 kHz minimum | 11 kHz (4.7 kHz within 5 %) |
| Sample rate | 1 kHz as set | 26.667 kHz | Set by the converter |
| Noise density | 300 ug/rtHz | 75 (x, y) and 110 (z) with three axes on; 60 and 80 with one | 25 ug/rtHz |
| Range | +-8 g as set | +-2, 4, 8 or 16 g | +-50 g |
| Sensor resonance | not looked up | about 7 kHz (secondary) | 21 kHz |
| Supply | not looked up | 1.1 to 1.3 mA | 1.0 mA, 3.3 to 5.25 V |
| Extra parts needed | none | none; has a 3 kB FIFO | A 16 bit or better converter and an anti-alias filter |

All IIS3DWB and ADXL1002 figures are (datasheet) unless marked.

Noise compared in the same band (my arithmetic, noise = density x square root of bandwidth):

| | Over today's 218 Hz band | Over its own full band |
|---|---|---|
| MPU-6500 | 4.4 mg rms | 4.4 mg (218 Hz) |
| IIS3DWB, x or y | 1.1 mg rms | 6.0 mg (6.3 kHz) |
| ADXL1002 | 0.37 mg rms | 2.6 mg (11 kHz) |

So in any one narrow band the IIS3DWB is 4 times quieter than today's sensor and the ADXL1002 is 12 times quieter. The full-band figure is larger only because the band is wider.

Converter check for the ADXL1002 (my arithmetic): its noise is 25 ug/rtHz x 40 mV/g = 1 uV/rtHz, about 105 uV rms over 11 kHz. A 16 bit converter on 3.3 V has 50 uV per count, which is enough. A 12 bit converter has 0.8 mV per count, about 20 mg, which is not. Whether the UNO Q's own converter is good enough is unverified; assume an external one.

**Recommendation:** IIS3DWB as the main sensor. It is three axes, digital, needs no converter or filter design, and covers the band. Add an ADXL1002 on the main axis later if the 6 kHz band or the noise proves to be the limit.

### The rest

| Part | What to get | Notes |
|---|---|---|
| Reference accelerometer | A second IIS3DWB on a cable, or the spare BNO085 as a first try | The BNO085 is band-limited like the MPU, so it only helps below a few hundred hertz (unverified) |
| Tachometer | A reflective optical sensor and reflective tape on the shaft, or a Hall sensor and a magnet | Read by a microcontroller timer input, stamped on the sample clock |
| Head | Machined aluminium or steel puck, flat base, two-pole magnet; a glued steel pad per probe point | The printed puck's resonance is unknown. Measure before deciding |
| Microphone | Raw sound to the main board, not readings worked out on an ESP32 | An ultrasonic MEMS microphone is optional, for early bearing wear (unverified: no part chosen) |
| Calibrator | A handheld shaker at 159.2 Hz, 10 m/s2 rms | Examples: Kistler 8921B01, B&K 4294, IMI 699A02 (+-3 %), Spektra CV-05 (secondary). Borrow one if possible |
| Lab reference | A piezo accelerometer with a calibration certificate, and its signal conditioner | For the side-by-side run in section 8. A university lab is the likely source |

## 5. Data path and compute

### The link is the first problem

| | Bytes a second | Source |
|---|---|---|
| Today's stream, 1 kHz | 7.5 kB/s | `device/app/README.md` (repo) |
| The link at 115200 baud | about 11.5 kB/s | same (repo) |
| The link at 460800 baud | about 46 kB/s | (my arithmetic, 4 times the above) |
| IIS3DWB, three axes, 16 bit, 26.667 kHz | 160 kB/s | (my arithmetic) |

The full-rate stream is about 3.5 times what the link carries. Three ways round it:

| Option | How | Cost |
|---|---|---|
| A. Bursts | Fill the microcontroller's memory with 1 s at full rate, send it over the next few seconds | No continuous record. Fine for spot checks, weak for the long stay |
| B. Readings on the microcontroller | Work out spectra on the STM32, send only the readings | Two copies of the signal pipeline, which AGENTS.md forbids |
| C. A sensor head with its own small microcontroller, on USB to the Linux side | The head samples over SPI and streams over USB, which carries about 1 MB/s at full speed | A new small board in the head. Not tried on the UNO Q (unverified) |

**Recommendation:** C. It keeps one signal pipeline, keeps timing in hardware, and frees the Bridge line. Try A first as the cheap proof that the sensor works.

### Storage

Raw data at 160 kB/s is 13.8 GB a day (my arithmetic). The 4 GB board has 32 GB of storage (secondary). So keep the feature rows as now, plus raw clips of a few seconds around each alarm and a short clip each hour.

### On the device

The UNO Q's Linux side is four Cortex-A53 cores at 2.0 GHz with 4 GB of memory; its microcontroller is an STM32U585, up to 160 MHz, 786 kB of memory (secondary). The per-window work at the higher rate is a few FFTs of 16,384 to 32,768 points a second, plus envelope and band readings. That is small for this processor, but nobody has timed it at 26.7 kHz (unverified). Time it before promising four windows a second.

### On the laptop

- Training the per-machine model and the library: any recent laptop. This is already the case.
- A pretrained sound encoder, if the DCASE survey's ideas are taken up: the survey's own guess is that the small one (CED-tiny, 5.5 M parameters) fits the board and the large one (BEATs, 90.7 M) takes seconds a clip. It also says no timing exists on the board.
- The local agent model: a graphics card with 8 to 12 GB of memory is my estimate, not measured (unverified).
- Everything stays on site. No part of this needs an outside service.

## 6. What the hardware unlocks in the signal pipeline

Each line is a change to `effente/`, so each needs the five-person agreement first.

| Method | Needs | What it gives |
|---|---|---|
| Velocity over 10 to 1000 Hz | Band to 1 kHz | Day-0 zones that match the ISO band, closing open question 4 in `docs/model-lifecycle.md` |
| Envelope analysis in a kilohertz band | Band to 5 kHz | Bearing faults seen where they ring, instead of at 100 to 450 Hz |
| Band chosen by spectral coherence | Band to 5 kHz, a few seconds of data | No fixed envelope band to tune per machine. Published comparisons call it more robust than the plain envelope spectrum (secondary) |
| Tone removal before the envelope (cepstrum pre-whitening) | Nothing new | Steady tones, the neighbour's included, taken out with no tuning (secondary) |
| Shaft-locked averaging and order tracking | Tachometer | Anything not locked to this shaft averages away. Exact speed, no doubling |
| Machine-off baseline | Nothing new | Bands present with the machine stopped are discounted |
| Reference subtraction and "moves with the floor" readings | Reference channel on the same clock | Surrounding vibration removed, or at least flagged to the model |
| 60 s spectra | Nothing new | A steady tone gets about 18 dB more headroom over sensor noise than in a 1 s window (my arithmetic, 10 log 60). Real bearing tones wander, so expect less |

Not recommended yet: diffusion denoisers, Noise2Noise denoisers, and vibration foundation models. Reasons: heavy compute, tested only on benchmarks or simulated systems, and a November 2026 preprint reports under 50 % zero-shot accuracy on unseen machines (secondary).

## 7. ISO zones, for the calibrated velocity reading

Velocity in mm/s rms, 10 to 1000 Hz, on the bearing housing (secondary: vendor summaries of ISO 20816-3, not the standard's text):

| Machine group | Support | A/B | B/C | C/D |
|---|---|---|---|---|
| 2: 15 to 300 kW | rigid | 1.4 | 2.8 | 4.5 |
| 2: 15 to 300 kW | flexible | 2.3 | 4.5 | 7.1 |
| 1: above 300 kW | rigid | 2.3 | 4.5 | 7.1 |
| 1: above 300 kW | flexible | 3.5 | 7.1 | 11.0 |

The demo conveyor's motor is far below 15 kW, so these zones do not apply to it. Buy or borrow the standard before putting zone names on the screen.

## 8. Evidence plan

Each bar is written down before the run, as contract 9.5 already requires. "Bar: to set" means the team has not agreed one.

| # | Test | How | Bar |
|---|---|---|---|
| 1 | Noise floor | Head on foam, 10 minutes, spectrum of each axis | Within 1.5 times the datasheet density across the band (proposed) |
| 2 | Lost samples | One hour at full rate, count gaps | 0 |
| 3 | Amplitude | Head on the calibrator, 159.2 Hz at 10 m/s2 | Within the calibrator's stated tolerance |
| 4 | Frequency response | Head and the lab reference on the same shaker or machine point, sweep or broadband | Bar: to set |
| 5 | Mount resonance | Tap test of the head on a steel block, each mount type | At least 3 times the top of the band used |
| 6 | Re-attachment | Take the head off and put it back 10 times, same disc | Bar: to set. RERUN.md already asks for this recording |
| 7 | Side by side on a machine | Our head and the lab reference on the same housing, healthy and with a rig fault | Bar: to set |
| 8 | Rig faults | The recordings RERUN.md lists: weights, loosened screws, a fault no type covers | Bar: to set |
| 9 | Surroundings | Second motor on the same table, on and off, with and without the reference channel | Bar: to set |
| 10 | Public sets | Ottawa and NASA IMS converted again for the new sensor's band and noise | Bar: to set. Ottawa naming is 1 of 40 today |
| 11 | False alarms | A week on one real machine | Bar: to set |

Tests 1 to 5 support level 1 wording. Tests 6 to 11 support level 2.

## 9. Order of work

1. Record the surroundings on today's device: head on the conveyor with it off, with it on, and on the table beside it. It costs nothing and says how much section 6's noise work matters.
2. IIS3DWB on SPI in burst mode (option A). Tests 1 and 2.
3. Stiff head. Test 5.
4. Calibrator. Test 3.
5. USB sensor head (option C), then one-hour and one-week recordings.
6. Tachometer and reference channel. Test 9.
7. Lab reference runs. Tests 4 and 7.
8. Rig faults and public sets. Tests 8, 10 and 11.

Steps 2 to 5 belong to track 1, the recordings to track 2, the pipeline changes in section 6 to all five.

## 10. What still cannot be claimed after all of this

- Any accuracy or lead-time figure for a machine type we have not recorded.
- Specific fault names, until each has been caught on the rig or in a pilot (`docs/model-lifecycle.md`, Fault names).
- A traceable calibration. A handheld calibrator is a field check; a certificate needs a calibration lab (secondary).
- Compliance with ISO 20816. The device can report the same quantity in the same band; compliance is a claim about a whole measurement procedure.

## 11. Unverified, in one place

- The 2.56 times sample-rate rule as a requirement of any standard.
- The "3 times the band" mount rule and the "about 2 kHz" magnet limit: one vendor guide each.
- The IIS3DWB's 7 kHz resonance: an ST forum answer, not the datasheet page.
- The UNO Q microcontroller's own converter, its SPI speed from a sketch, and whether a USB sensor head works with the board's Linux side.
- FFT timing on the board at 26.7 kHz.
- The BNO085's usable band as a reference sensor.
- The laptop graphics memory figure.
- ISO zone values: vendor summaries; two of them disagree on small details.
- The simulated neighbour machine's level, so also the size of the effect in section 2.
- Every bar marked "proposed" or "to set".

## 12. Sources

- [IIS3DWB datasheet, ST](https://www.st.com/resource/en/datasheet/iis3dwb.pdf)
- [IIS3DWB bandwidth and resonance, ST community](https://community.st.com/t5/mems-sensors/iis3dwb-sensitivity-and-resolution/m-p/171426)
- [ADXL1001/ADXL1002 datasheet, Analog Devices](https://www.analog.com/ADXL1002)
- [Why MEMS accelerometers suit condition monitoring, Analog Devices](https://www.analog.com/en/resources/technical-articles/why-memes-acceler-are-best-choice-for-cbm-apps.html)
- [MPU-6500 product highlight](https://www.digikey.com.au/en/product-highlight/i/invensense/mpu6500-sixaxis-gyro-accelerometer-mems-motiontracking-devices)
- [Arduino UNO Q, Qualcomm](https://www.qualcomm.com/developer/hardware/arduino-uno-q)
- [Arduino UNO Q tech specs](https://www.wevolver.com/specs/arduino-uno-q-4gb)
- [Sensor mounting methods, Wilcoxon](https://wilcoxon.com/blog/sensor-mounting-methods/)
- [How sensor mounting affects measurements, PCB](https://pcb.com/Resources/White-Papers/WPL_68_HowSensorMountingAffectsMeasurements)
- [ISO 20816 severity zones, vendor summary](https://www.fabrico.io/blog/iso-20816-vibration-severity-zones/)
- [Kistler 8921B01 portable calibrator](https://www.kistler.com/AT/en/p/portable-accelerometer-calibrator-8921b01/000000000018026138)
- [IMI 699A02 handheld shaker](https://pcb.com/ContentStore/mktg/IMI_Downloads/IMI_699A02_LowRes.pdf)
- [Accelerometer calibration guide](https://microprecision.com/blog/accelerometer-calibration-guide/)
- [ISO 13373-2:2016 contents](https://cdn.standards.iteh.ai/samples/68128/ba08a4f03c53403798aff4fdbfd4e72e/ISO-13373-2-2016.pdf)
- [IESFOgram and spectral coherence](https://lirias.kuleuven.be/bitstream/123456789/653802/2/MSSP%20IESFOgram.pdf)
- [Cepstrum pre-whitening under variable speed](https://pmu.edu.sa/Attachments/Research/PDF/2013/Application%20of%20cepstrum%20pre-whitening%20for%20the%20diagnosis%20of%20bearing%20faults%20under%20variable%20speed%20conditions.pdf)
- [Discrete component removal methods compared, PHM Society](https://papers.phmsociety.org/index.php/phme/article/view/1538)
- [DCASE 2026 Task 2, noise-aware detection](https://arxiv.org/abs/2606.01578)
- [Multimodal foundation model preprint, November 2026](https://arxiv.org/html/2611.15051)
- In this repo: `training/record/datasets/DCASE_TASK2_SURVEY.md`, `training/record/datasets/OTTAWA_RESULTS.md`, `training/record/conveyor_eval/REALISM.md`, `training/validation/REPORT.md`, `training/validation/RERUN.md`, `device/app/README.md`, `docs/model-lifecycle.md`.
