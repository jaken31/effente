# What can go wrong, and how the intake sees it

This maps realistic failures in industrial machines and data-center equipment to the four sensors (MPU6050 accelerometer and gyroscope, INMP441 microphone, MLX90640 thermal camera, BME280 room sensor) and to the features that carry each one to the model. It covers the intake only; deciding what is a fault is the model's job.

It is based on a literature and datasheet review (sources at the end) and on two simulations run through the full pipeline: a conveyor week and a data-center server rack. Simulation results are in the last section.

## What the sensors can and cannot see

| Sensor | Sees | Cannot see |
|--------|------|------------|
| Accelerometer, 1 kHz | Vibration up to about 250 Hz: running speed, imbalance, misalignment, looseness, belts, slow bearings and gears, shocks, tilt | Anything above about 250 Hz with confidence: early bearing wear, most gear mesh and fan blade-pass. Its noise (about 6.5 mg) hides very small vibration such as a server fan through a rack |
| Gyroscope | Rocking, twisting, bumps, the device moving | Fine spectral detail |
| Microphone, 16 kHz | Sound from 60 Hz to about 7 kHz: whine, squeal, grinding, clicks, hum, beeps, fan pitch, bearing noise pulsing at defect rates | Ultrasound (early bearing stages, air leaks, corona), and running speed itself (below 60 Hz) |
| Thermal camera, 32x24 | Where things are hot and how fast they heat: bearings, motors, connectors, exhausts, hot spots, cold spots. Each of the 48 grid cells gives its average (broad areas: exhausts, airflow) and its hottest point (small parts: a connector covering 2 of a cell's 16 pixels raises the average by an eighth of what it raises the hottest point) | Absolute temperature to better than ±1-2 °C, shiny metal (it reflects), anything smaller than a pixel (about 3 cm at 1 m) |
| Room sensor | Room temperature and humidity, dew point | It reads the air at the device, which the machine warms |

## Recommended settings

- **Accelerometer ±8 g** (±16 g on presses, crushers and conveyors with heavy impacts). At ±2 g a hard jolt clips; the sensor's own noise is far above its step size at any range, so nothing is lost. Digital low-pass 260 Hz, 1 kHz, read in batches.
- **Gyroscope ±250 °/s**, the most sensitive range for rocking.
- **Microphone 16 kHz**, or **32 kHz** if the Bridge can carry it: that doubles the audible range to about 13.5 kHz (more bearing, coil-whine and squeal detail). Keep 24-bit samples; point the port away from airflow.
- **Thermal camera** refresh 8 Hz (4 full frames a second); ignore the first 4 minutes after power-up; put matte tape on shiny targets.
- **Room sensor** away from the hot machine surface.

## A. Sudden events (milliseconds to seconds)

The recorder logs these as events and keeps raw sensor data from 4 s before to 6 s after (at most one clip per kind a minute, 300 a day). "Sudden" is judged against the last 10 minutes of the same machine:

- `sudden_vibration`: a jolt 4x the usual peak, at least 0.05 g, and 2x the 3rd biggest of the last 10 minutes.
- `sudden_sound`: a bang 12 dB over the usual loudest instant, 6 dB over the 3rd loudest of the last 10 minutes, and 9 dB over its own second's level. Once the machine has been seen running, "usual" is also its loudest instants over its last 10 minutes of running, so the first noises of a morning start, after a silent night, do not count. The very first start a device ever sees has nothing to compare with and can still be logged.
- `beeping`: a tone switching on and off by 30 dB or more for 3 of 5 seconds. Normal machines stay under about 25 dB.
- `sudden_heat`: the hottest point jumps 5 °C above its median of the minute before, to above 45 °C (hotter than a person walking past), and stays there for 3 s.
- `device_dropped`: the accelerometer reads free fall.

Something that keeps happening (a knock every belt lap) stops being sudden after 3 times in 10 minutes. From then on the knock features describe it.

| Failure | How it shows up | Features |
|---------|-----------------|----------|
| Impact, collision, dropped load | Big jolt and ringing; a bang | `vib_peak_g`, `vib_jerk`, `vib_shock_axis`, `gyr_peak`, `snd_peak_db`, `snd_click`; history `trend_shocks_10m`, `trend_since_shock_s`, `trend_bangs_10m`; raw clip saved from 4 s before |
| Jam, stall, overload | Jolt; speed collapses; motor strains (hum, squeal); then heat | `vib_peak_g`, `vib_speed_hz`, `vib_line_2x`, `snd_tone_hz`, `snd_mains_db`, `trend_run_minutes`; heat later in `trend_heat_rise_per_min` |
| Trip, stop, restart | All running tones disappear or ramp | `vib_speed_hz`, `vib_rms_*`, `snd_level_db`, `trend_run_minutes` |
| Belt snap, thrown belt, coupling break | Jolt, then the driven parts' frequencies vanish | `vib_peak_g`, `slow_peak1_hz`, `slow_peak2_hz`, `vib_speed_hz` |
| Blade loss, debris strike | A step up in once-per-turn vibration | `vib_1x`, `vib_peak_g`, `snd_peak_db` |
| Rub (rotor, seal, belt on frame) | Squeal or scrape; half-order vibration; local heat | `snd_tone_hz`, `snd_tone_db`, `vib_half`, `vib_kurtosis`, `snd_kurt_hf`, `th_spot_*`, `th_cell_*` |
| Bearing seizure (end of life) | Broadband jump, grinding, heat | `vib_band_*`, `snd_band_*`, `snd_kurt_hf`, `th_hotspot_c`, `th_spot_*`, `trend_heat_rise_per_min` |
| Water hammer, valve slam | Low-frequency jolt and bang | `vib_peak_g`, `snd_peak_db` |
| Electrical arcing | Crackle bursts; buzz; sometimes a flash of heat | `snd_kurt_hf`, `snd_click`, `snd_mains_db`, `th_change_c`, `th_max_c`; event `sudden_heat` |
| Fire, smouldering, thermal runaway | Fast temperature rise | `th_max_c`, `th_change_c` (within a second), `trend_heat_rise_per_min` (fire detectors alarm near 8 °C/min), `env_room_c`; event `sudden_heat` |
| Fan stops (server, power supply, cooling) | Its tone vanishes; other fans speed up; its exhaust area changes temperature while the rest runs hotter | `snd_tone_hz`, `snd_level_db`, `snd_band_*`, `th_cell_*` (average of the exhaust area), `trend_cell_fall_1h_vs_24h` (which area went cold, against the whole image), `trend_cell_rise_1h_vs_24h` |
| Cooling or air-conditioning failure | Room heats; fans speed up | `env_room_c`, `trend_room_rise_per_h`, `trend_room_1h`, `env_humidity_pct`, `snd_level_db` |
| UPS on battery, any alarm or warning beeper | Periodic beeps; relay clunk; hum change | `snd_tone_mod_db`, `snd_tone_hz`, `snd_peak_db`, `snd_mains_db`; event `beeping` |
| Device dropped or knocked off | Free fall then impact; tilt changes | `vib_freefall_frac`, `vib_gravity_*`, `gyr_peak`; events `device_dropped`, `device_moved` |

## B. Slow and subtle degradation (minutes to weeks)

The history features look back 1 minute, 10 minutes, 1 hour, 6 hours, 24 hours, 7 days and 4 weeks, counting running time only, so a drift over weeks shows as a rise per week and as the part that changed most over the last 7 days against 4 weeks. Beyond 4 weeks, the model's comparison with what it learned as normal still sees a reading that has left its usual range.

| Failure | How it shows up | Features |
|---------|-----------------|----------|
| Imbalance growth | Once-per-turn vibration rises | `vib_1x`, `vib_velocity_mm_s`, `trend_vib_*`, `trend_vib_band_rise_*` |
| Misalignment | Twice-per-turn rises (often along the shaft); coupling warms | `vib_2x`, per-axis `vib_rms_*`, `th_spot_*` |
| Looseness, soft foot | Many harmonics, half-order, knocks; rocking | `vib_harm_3_10`, `vib_half`, `vib_crest`, `vib_kurtosis`, `gyr_rms` |
| Rolling-bearing wear | Late stages: defect-rate pulses in vibration and in high-frequency sound, then heat | `vib_env_peak_hz`/`vib_env_peak`/`vib_env_order`, `snd_env_peak_hz`/`snd_env_peak_db`/`snd_env_order`, `snd_kurt_hf`, `snd_band_*`, `th_spot_*`, `trend_*_band_rise_24h_vs_7d` and `_7d_vs_4w`, `trend_cell_rise_24h_vs_7d` and `_7d_vs_4w` |
| Lubrication loss | Rising high-frequency hiss; bearing warms | `snd_band_*` (2-7 kHz), `snd_flatness`, `th_spot_*`, `trend_heat_*` |
| Gear wear, cracked tooth | Gear-mesh tone and sidebands (mostly in sound); once-per-turn knocks | `snd_tone_hz`, `snd_tone_db`, `vib_peak2_hz`, `knock_period_s`, `knock_repeat`, `snd_env_*` |
| Belt wear, slip | Driven speeds fall against the motor; squeal; flapping | `slow_peak1_hz`, `slow_peak2_hz`, `vib_speed_hz`, `trend_speed_*`, `snd_tone_hz` |
| Belt splice damage | A knock once per belt lap | `knock_period_s`, `knock_repeat` |
| Pump cavitation | Crackling broadband noise in 2-3 kHz and up | `snd_kurt_hf`, `snd_band_*`, `snd_flatness`, `snd_click` |
| Clogged filter, airflow loss | Fans speed up at the same room temperature; exhaust hotter | `snd_level_db`, `snd_tone_hz`, `th_cell_*`, `env_hot_above_room_c`, `trend_heat_*` (over weeks: `trend_heat_rise_per_week`, `trend_snd_4w`) |
| Motor electrical faults | Twice-mains (100/120 Hz) vibration and hum rise | `vib_line_2x`, `snd_mains_db`, `th_cell_*` |
| Loose or corroded connection, overheating cable, failing breaker | One small spot heats slowly, with load. Usually smaller than a grid cell, so its cell's hottest point carries it, not the cell's average | `th_spot_*`, `trend_cell_rise_24h_vs_7d` and `trend_cell_rise_7d_vs_4w` (and which cell), `trend_heat_rise_per_day`, `trend_heat_rise_per_week` |
| Small-fan bearing wear | New harmonics and grinding; the fan slows; its zone warms | `snd_tone_*`, `snd_band_*`, `snd_env_*`, `snd_kurt_hf`, `th_spot_*`, `trend_snd_band_rise_*` |
| Coil whine, power-supply ageing | High tones that shift with load | `snd_tone_hz`, `snd_band_*` (above 7 kHz needs 32 kHz sampling) |
| Cooling unit drift, short-cycling | Room temperature cycle and humidity change | `env_room_c`, `env_humidity_pct`, `trend_room_*`, `trend_humidity_1h` |
| Area running colder than it used to: blocked radiator fins or heat-exchanger tubes, a closed valve, a failed heater or heat trace, a blocked steam trap, a stopped pump or motor | A broad area cools while the rest of the image does not. Measured against the whole image's change, so the machine cooling as a whole (less load, a stop) does not count | `th_cell_*`, `th_cold_spot_c`, `trend_cell_fall_1h_vs_24h`, `trend_cell_fall_24h_vs_7d` (and which cell) |
| Condensation risk | A surface below the dew point | `env_dew_point_c`, `env_condensation_margin_c` |
| Slow drift of anything | A feature creeping over days or weeks | the 24 h, 7 day and 4 week averages, rise per hour, per day and per week, the biggest-riser features that name which image cell (by its hottest point), vibration band or sound band is changing, and which image area went cold (by its average) |

## C. Problems with the sensing itself

| Problem | How it is caught |
|---------|------------------|
| Clipping (range exceeded) | `vib_clip_frac`, `snd_clip_frac`; event `sensor_clipping` |
| Sensor dead or stuck | No noise at all, silence, or identical thermal frames: event `sensor_stuck`; missing data: `sensor_lost` |
| Device moved, dropped, re-mounted | `vib_gravity_*`, `vib_freefall_frac`, `trend_scene_match`; events `device_moved`, `device_dropped` |
| Thermal camera blocked or dirty | `th_spread_c` collapses; `trend_scene_match` drops |
| Mount loosening | Looks like machine looseness; tell them apart by gravity drift and gyro rocking |
| Gaps and dropped data | Event `gap`; sequence numbers in the UNO Q source (to be written) |
| People and other machines nearby | Learned as normal on site. A person walking past is not sudden heat (under 45 °C), and a repeating knock stops being logged after 3 in 10 minutes |

## What these four sensors cannot do

Being honest about the limits matters for the pitch:

- **Early bearing wear** (the first stages) is ultrasonic or above 500 Hz in vibration. We see bearings from the later stages, through the microphone's envelope and the accelerometer on slow machines.
- **Ultrasonic air leaks and corona** are invisible.
- **Hard-drive health.** Drives are the most replaced part in data centers (78 % of hardware replacements in one large study), but their decline shows in SMART counters, not in sound or heat. We can hear clicking drives and protect against shocks and loud noise, not predict quiet failures.
- **UPS battery swelling** is invisible unless the battery is in the camera's view.
- **Server fan vibration through a rack** is below the accelerometer's noise. The microphone and camera do that job.
- **Absolute temperatures** are good to about ±1-2 °C; trends and differences are far better than that.

## Simulation results

To be filled in from the conveyor week and server rack runs (`python3 -m device.intake.coverage_check`).

## Sources

- Vibration severity zones (ISO 10816-3 / 20816-3): https://www.fabrico.io/blog/iso-20816-vibration-severity-zones/
- Bearing defect frequencies and the four stages of bearing failure: https://www.fabrico.io/blog/bearing-defect-frequencies/ ; https://www.acoem.com/en/blog/do-you-know-the-4-stages-of-bearing-failure/
- Motor electrical faults in vibration: https://irispower.com/wp-content/uploads/2023/07/Induction-Motor-Vibration-Electrical-Problems-Doyle-IRMC-2023.pdf
- Microphone envelope analysis for bearings: https://arxiv.org/pdf/2304.07307
- Spectral kurtosis for impulsive faults: Randall and Antoni, Mechanical Systems and Signal Processing 25 (2011) 485-520
- Machine sound anomaly detection datasets (MIMII, DCASE): https://arxiv.org/pdf/1909.09347 ; https://arxiv.org/pdf/2206.05876
- INMP441 datasheet: https://microcontroller-course.web.cern.ch/Project_Material/resources/INMP441.pdf
- MLX90640 datasheet: https://cdn.sparkfun.com/assets/3/1/c/6/f/MLX90640-Datasheet.pdf
- Thermography reflections and emissivity: https://www.fluke.com/en-us/learn/blog/thermal-imaging/fixing-thermography-reflectivity
- Heat detector rate of rise: https://fmapprovals.com/products-we-certify/understanding-the-benefits/fm-approved-heat-detectors
- Data-center hardware failures: https://www.microsoft.com/en-us/research/wp-content/uploads/2010/06/socc088-vishwanath.pdf ; https://static.usenix.org/event/fast07/tech/schroeder.html
- Data-center outage causes: https://intelligence.uptimeinstitute.com/resource/annual-outage-analysis-2026
- Cooling-loss temperature rise: https://www.achrnews.com/articles/157407-the-heat-is-on-but-the-cracs-are-off-now-what
