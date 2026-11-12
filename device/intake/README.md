# Data intake

Takes sensor data in, lines the sensors up, and turns every window into a row of features for the model to train on and, later, to run on. There is no model here.

```
sensors / recordings / simulator -> source -> windows (1 s, every 0.25 s) -> features + history -> rows
                                    (UNO Q later)  (aligned by timestamp)   (effente.features,      |
                                                                             effente.history)       v
                                                          learn mode: kept on disk   watch mode: to the model
```

## Two modes

**Learn mode** ([recorder.py](recorder.py)): leave the device running for days to a month. Sensors run at full rate; what is kept:

| Kept | How often | About per week | About per month |
|------|-----------|----------------|-----------------|
| Feature rows (`features/features_NNNNN.npz`, one file per hour) | 1 per second | 0.5 GB | 2.1 GB |
| Per-minute min, mean and max of every feature (in the same files) | 1 per minute | 27 MB | 0.1 GB |
| Raw sensor snippets (`raw/clip_*.npz`), replayable into features later | 10 s every 10 min | 0.4 GB | 1.7 GB |
| History state (`history.npz`), so trend features survive a restart | saved every 5 min | 10 MB (fixed size once full) | 10 MB |
| Events (`events.jsonl`): start, stop, sensor lost or back, device moved or dropped, gaps, cleanup, sensor clipping or stuck | when they happen | tiny | tiny |
| Sudden events (jolt, bang, beeping, sudden heat) with raw data from 4 s before to 6 s after (`raw/clip_*.npz`) | when they happen, at most 1 a minute per kind and 300 a day | small | small |

Sizes are measured on the simulated server rack (278 features scaled from 263); a machine that is often stopped compresses smaller. A month is about 4 GB in all; if the device cannot spare that, set `--max-gb` and the cleanup below keeps the most useful data. The current hour is saved every minute, so a power cut loses at most a minute.

```bash
python3 -m device.intake.recorder --sim conveyor --from 0:00:00 --to 4:00:00 --out data/fake/conveyor-week --hop 1
python3 -m device.intake.recorder --data data/fake/normal-day-01 --out data/fake/learn-01
```

**Watch mode** ([live.py](live.py)): features go to the model 4 times a second. Intake and the model run separately with a queue between them; if the model falls behind, the oldest waiting window is dropped and counted, so sensor reading never waits. With `--record` it keeps recording while it watches, and it picks up the saved history in that folder.

```bash
python3 -m device.intake.live --sim conveyor --from 4:00:00 --to 7:00:00 --record data/fake/conveyor-week \
    --loader my_model.py --model my_model.bin --log results.jsonl --machine-id conveyor-07
```

`--from` and `--to` take `HH:MM`, or `D:HH:MM` for day D of a simulation (day 0 is Monday). `--speed 1` replays at real time; the default runs as fast as possible. `--hop 1` makes one window a second, which gives exactly the same rows as the default at whole seconds, 4x faster.

There is no model in this repo. The trainer provides a Python file:

```python
FEATURES_VERSION = "..."            # effente features it was trained on (see features_version())
def load(path):                     # returns an object with .predict(features) -> dict
    ...
# features: (278,) floats in effente.features order, NaN where a sensor is missing
#           or a history feature has not got enough history yet. Prepare them with
#           effente.model_input.Prep (below) exactly as in training.
```

The device refuses a model trained on a different feature list, reloads by itself when the model file changes, and marks the first 60 s after a start as `warming_up`. If a new model file fails to load (for example caught half-copied), the model already running stays in use and the load is retried. The model gets its own copy of each row, so it cannot change what is recorded. Without a model, features still flow and every result says `no model`.

The results log (`--log`, appended, never overwritten) has one JSON line per window in the TRD's log-record shape: `device_id`, `machine_id`, `at` (UTC), and `diagnosis`, `confidence`, `unknown_score`, `features`, `mask` and `spectrum` copied from the model's result when it gives them (left out, never null, when it does not). `spectrum` defaults to the 20 vibration bands for the dashboard. The intake adds `t`, `status`, the full `result`, `missing` sensors and `queue_wait_ms`. A result that cannot be written as JSON (NaN, an odd object) is logged as an error instead of stopping the device.

## Live stream

`--serve PORT` makes watch mode serve its data over HTTP ([stream.py](stream.py), standard library only), so a dashboard, the laptop platform or curl can connect. There is no UI here. It binds `0.0.0.0` (every network) by default; `--serve-host 127.0.0.1` keeps it on the device. There is no authentication.

```bash
python3 -m device.intake.live --sim conveyor --from 0:10:00 --to 0:11:00 --speed 1 --serve 8765 --machine-id conveyor-07
curl -N "http://DEVICE:8765/stream"                          # everything
curl -N "http://DEVICE:8765/stream?topics=results,events"    # only some topics
curl "http://DEVICE:8765/latest"
```

```js
const es = new EventSource("http://DEVICE:8765/stream?topics=results,events,status");
es.addEventListener("results", e => show(JSON.parse(e.data)));   // named events: onmessage does not fire
es.addEventListener("events", e => alert(JSON.parse(e.data)));
```

| Endpoint | What |
|----------|------|
| `GET /stream` | Server-Sent Events. `?topics=results,features,events,status` (default all). `?features_every=S` for fewer feature rows, `?replay=S` (0 = none) |
| `GET /latest` | `{"results": ..., "features": ..., "events": ..., "status": ...}`: the last item of each topic (null if none yet); status is fresh |
| `GET /features` | Every feature in row order: `index`, `name`, `text`, `unit`, `sensor`, `from_history`; plus `units`, the `spectrum` bands and their edges in Hz, and `features_version` |
| `GET /health` | `ok`, `uptime_s`, `last_result_age_s`, `clients`, counts published |

| Topic (SSE `event:`) | How often | Shape |
|-------|-----------|-------|
| `results` | every window, 4 a second | the results-log record, the same line as `--log` writes |
| `features` | one row per second of data (`--serve-features-every`) | `values`: all 278 features in `/features` order, 6 significant digits, `null` where NaN |
| `events` | when they happen: jolt, bang, beeping, heat jump, device moved or dropped, sensor lost or back, gaps, sensor clipping or stuck, storage cleanup, recording paused, stopped (needs `--record`) | `event`, `t`, `details` |
| `status` | every 5 s, and once at the end with `running: false` | model, window counts, missing sensors, recording, stream clients |

One message of each, from a 30 s run of the simulated conveyor with no model (only the feature values are cut short):

```
id: 6af60669-154
event: results
data: {"device_id": "effente-01", "machine_id": "conveyor-07", "at": "2026-11-12T18:33:21+00:00", "t": 36029.0, "status": "no model", "spectrum": [-4.702, -4.62, -4.627, -5.241, -4.892, -5.018, -5.037, -3.415, -4.819, -4.382, -4.075, -4.289, -4.182, -4.197, -4.095, -3.759, -3.934, -4.103, -4.582, -5.972], "result": null, "missing": [], "queue_wait_ms": 0.1}

id: 6af6065b-1
event: features
data: {"device_id":"effente-01","machine_id":"conveyor-07","at":"2026-11-12T18:33:00+00:00","t":36000.0,"features_version":"c985a0908a96","missing":[],"values":[-1.87222,-1.98179,-1.91626,-5.00315,-4.68125, ... 278 values, null where NaN]}

id: 6af6065b-155
event: events
data: {"device_id":"effente-01","machine_id":"conveyor-07","at":"2026-11-12T18:33:07+00:00","t":36029.0,"event":"recording_stopped","details":{"wall_time":"2026-11-12T18:33:07+00:00","windows":117,"rows":30}}

id: 6af60669-30
event: status
data: {"device_id":"effente-01","machine_id":"conveyor-07","at":"2026-11-12T18:33:15+00:00","uptime_s":2.0,"running":true,"t":36005.25,"status":"no model","missing":[],"model":{"status":"no model","error":null,"loads":0},"windows":{"made":22,"scored":22,"dropped":0,"model_errors":0},"model_ms_avg":0.0,"queue_wait_ms_avg":0.1,"recording":true,"recording_paused":false,"intake_error":null,"stream":{"clients":1,"client_drops":0,"stalled_disconnects":0,"published":{"results":22,"features":6,"events":0,"status":1}}}
```

With a model, `results` also carries `diagnosis`, `confidence`, `unknown_score`, `features` and `mask` when the model gives them, and `result` is the model's full result. `at` is UTC: from the device clock when it is Unix time, otherwise (a replay or simulation) the time the line was made. NaN never appears in any message.

- **On connect** a client first gets the last 30 s of its topics, then live data. EventSource reconnects by itself after 3 s and sends `Last-Event-ID`; the stream then resumes after that item, with no repeats (as far back as it keeps: 248 results, 34 feature rows, 200 events). After a device restart, ids start again and the client gets the normal replay.
- **Never slows the device**: publishing only puts items in memory. Each client has its own queue of 256 items that drops its oldest when full (counted as `client_drops`), and a client that cannot be written to for 10 s is disconnected (`stalled_disconnects`). At most 16 clients; more get 503. Measured once on a laptop: 3 minutes of the simulated conveyor ran in 3.58 s without the stream and 3.73 s with it and a client that never reads, 717 windows made and 717 scored both times.
- **CORS** is open (`Access-Control-Allow-Origin: *`), so a dashboard page on the laptop can connect to the device.
- Events written before the stream attaches (`recording_started`) are only in `events.jsonl`. Without `--record` there are no events.
- Not yet tried: a browser `EventSource`, other machines on a real network, and the UNO Q itself. The tests use urllib on one laptop.

## Sending to the laptop platform

[uploader.py](uploader.py) sends the device's data to the laptop platform, following [platform/CONTRACT.md](../../platform/CONTRACT.md) version 3.1. The device app ([device_app.py](device_app.py)) uses it when it is given one. Standard library only, and only outgoing requests.

- **Where:** `EFFENTE_PLATFORM_URL`, for example `http://192.168.43.10:8000`. If it is unset or empty, nothing is sent.
- **The model** (`POST /api/model`): at start, after each training, and whenever a reply says `need_model`. It carries the fault types with their text and group, the feature table and the spectrum's band edges. `model_version` is the machine, the model file's name and a hash of the file, so it changes whenever the file does. `library_entry` and `source` are sent only if the model file has them. The device's trainer writes neither yet.
- **Records** (`POST /api/records`): while watching, one record a second of data. Each record has the diagnosis, `reason`, the safety checks' `problems` (with `kind` and `since`), `wrong`, `sensitivity` and `alarm_line`. A problem shows even while the diagnosis is `normal`.
- **Check-ins** (`POST /api/devices/{id}`): while not watching, one a second, with `mode`, `wrong` and the problems.
- **Settings:** when a reply carries a sensitivity that differs from the device's own, the device applies it the same way `POST /settings` does. It is saved in `data/settings.json`, so it survives a restart.
- **Never slows the device:** the pipeline only puts a small dict on a queue; a thread of its own sends. If the platform can't be reached, it keeps the newest 900 records (15 minutes) and drops the oldest, counted. When the platform is back, they go out as JSON arrays of up to 120. A check-in that could not be sent is dropped, since the next one is newer. Failures are logged at most every 30 s, never raised.
- **Check against a stand-in:** `python3 -m device.intake.tests.fake_platform 8000` prints what arrives and any contract errors.
- Not yet tried on the UNO Q itself or over the hotspot. It has run against the real receiver on one laptop.

## Feeding the model

Feature rows are for people and the agent to read; a model needs plain, bounded numbers. [effente/model_input.py](../../effente/model_input.py) prepares them the same way in training and on the device. It is a preparation step, not a model:

```python
from effente.model_input import Prep, load_recording, day_split, persistent
X, t0, names = load_recording("data/fake/conveyor-week")   # every hour file, checked for one feature list
train, test = day_split(t0, test_days=1)                    # the last day held out, with an hour's gap
prep = Prep.fit(X[train], names, t0[train])                 # statistics from training rows only
Z = prep.transform(X)                                       # no NaN, every value within +-8
prep.save("prep.json")                                      # ships with the model (the TRD's normalisation statistics)
```

| Step | What | Why |
|------|------|-----|
| Running rows only | Fits on rows where `trend_running` is 1 | Stopped and running are different machines to a model; mixing them hides faults |
| Drop "which" indices | The `_at` features (which cell or band) are left out; the jolt axis becomes three 0/1 columns | A plain number says cell 47 is "more" than cell 0, and when nothing is rising the index flips at random |
| Drop young history | Any feature NaN in more than half the training rows | A 7-day or 4-week trend never had a value in a week of learning, and would look abnormal the day it appears; check those trends with simple rules until there is enough history |
| Weekday | Only kept when training covers 3 weeks or more | Otherwise each weekday is seen too few times; held-out days would look new |
| Tame heavy tails | Seconds since the last jolt or bang: log, capped at an hour. Kurtosis: log10(kurtosis + 3) | Kurtosis reaches thousands on impacts |
| Scale | Median and interquartile range of training rows, then clipped to +-8 | Robust to spikes in training; a fixed range suits whole-number (quantised) weights. `out_of_range(X)` counts values beyond +-8 before clipping, a second alarm the clip cannot hide |
| Missing sensors | Missing becomes 0 (the training median) plus one 0/1 column per sensor | "No data" must never look like "normal" |

Training and testing: never split rows at random, because neighbouring rows are near-copies (4 a second overlap 75 %, and even 1 a second are strongly alike). Hold out whole days, set the alarm threshold on held-out normal days, and require persistence (`persistent(flags, 8, 12)`: 8 of the last 12 rows) before alarming: at 4 rows a second, a threshold wrong 0.1 % of the time still fires about 350 times a day. Train on learn mode's 1 row a second, not on watch mode's 4.

Sources: scikit-learn on [robust scaling](https://scikit-learn.org/stable/modules/generated/sklearn.preprocessing.RobustScaler.html), [fitting only on training data](https://scikit-learn.org/stable/common_pitfalls.html), [cyclical time features](https://scikit-learn.org/stable/auto_examples/applications/plot_cyclical_feature_engineering.html) and [missing values](https://scikit-learn.org/stable/modules/impute.html); missing-value indicators ([Lipton et al.](https://ar5iv.arxiv.org/html/1606.04130), [Josse et al.](https://arxiv.org/abs/1902.06931)); [DCASE anomalous sound detection](https://dcase.community/challenge2023/task-first-shot-unsupervised-anomalous-sound-detection-for-machine-condition-monitoring) (per-machine models, scores averaged over time); [integer quantisation ranges](https://ar5iv.arxiv.org/html/1712.05877); [Mahalanobis scores on hidden activity](https://arxiv.org/html/1807.03888v2) for the unknown alarm.

## Storage cleanup

`--max-gb` sets a budget for the recording folder (and the disk keeps `--min-free-gb` free, 2 GB by default). When either is crossed, [storage.py](storage.py) removes the least useful data first and logs every step (the first time, then at most once an hour with totals):

1. Raw snippets from quiet periods, oldest first. Snippets near an event, and the first of every hour, stay.
2. Feature rows in hours older than a day are thinned from 1 per second to 1 every 10 seconds. Per-minute summaries stay.
3. The remaining raw snippets, oldest first.
4. Last resort: recording pauses (watch mode keeps running) until there is room.

Per-minute summaries, the events log and the history state are never deleted.

## Make a feature file

```bash
python3 -m device.intake.extract --data data/fake/normal-day-01 --out data/fake/normal-day-01/features.npz
```

Each window is 1 second long and a new one starts every 0.25 seconds. A full second gives 1 Hz frequency detail; shorter windows would blur once-per-turn and twice-per-turn together. Neighbouring rows share 0.75 s of data, so for independent rows use `--hop 1`.

## What the trainer gets

| Field | Shape | Meaning |
|-------|-------|---------|
| `X` | (windows, 278) float32 | One row per window. NaN where a sensor was missing or history is too short. |
| `t0` | (windows,) | Window start, seconds |
| `feature_names`, `feature_sensor`, `feature_text`, `feature_kind` | (278,) | Column names, sensor, plain-English meaning, units (`log` = log10, `hz`, `ratio`, `g`, `db`, `c`, `pct`, `index`, `count`, `s`, `min`, `mm_s`, `c_per_min`, `c_per_h`, `c_per_day`, `c_per_week`) |
| `label` | (windows,) | Label of the recording or simulated fault |
| `features_version` | | Changes whenever the feature list changes. Check it before training. |

Learn-mode hour files hold the same columns, plus `minute_min`, `minute_mean` and `minute_max`.

## The 278 features

[FAILURE_MODES.md](FAILURE_MODES.md) says which failures each one is for.

**Per window (210)**, from one second of data ([effente/features.py](../../effente/features.py)):

| Sensor | Count | Features |
|--------|-------|----------|
| Vibration (MPU6050) | 44 | Level per axis, 20 bands from 5 to 500 Hz, running speed and the levels at 1x, 2x, 3x, 3-10x and 0.5x of it, second strongest peak, knockiness, biggest jolt and jerk and which axis, envelope analysis of 100-450 Hz (bearing and gear defect rates), velocity in mm/s (ISO-like), twice-mains vibration (electrical), clipping, free fall, gravity direction |
| Rotation (gyroscope) | 2 | Rocking and twisting, average and biggest |
| Sound (INMP441) | 43 | Level, 32 bands from 50 to 8,000 Hz, pitch, clicks, strongest tone, how tonal, loudest instant, mains hum, beeping, envelope analysis of 2-7 kHz (bearings), impulsiveness of 2-7 kHz (cavitation, rubs, arcing), clipping |
| Heat (MLX90640) | 107 | Hottest point, average, background, hot spot and where, hot pixel count, coldest spot, fastest change, frozen image, spread, and an 8x6 grid of heat above background: each cell's average (broad areas) and its hottest point (small parts such as a connector, which the average dilutes) |
| Room (BME280) | 5 | Temperature, humidity, hottest point above room, dew point, coldest surface above the dew point |
| Clock | 4 | Hour of day and day of the week, each as a sine and cosine so 23:59 runs smoothly into 00:00 and Sunday into Monday |

**From history (68)**, from the past of the same stream ([effente/history.py](../../effente/history.py)):

| Group | Features |
|-------|----------|
| Slow vibration (8 s view, 0.4-25 Hz) | 6 bands and the two strongest peaks: pulleys, idler rollers, gearbox shafts |
| Repeating knocks (last 5 min) | Time between knocks or clicks and how regular they are (belt splice, clicking drive) |
| Sudden events | Jolts and bangs in the last 10 minutes, seconds since the last of each |
| Machine | Running or stopped (`trend_running`, judged each minute from vibration; until the machine has been seen both running and stopped, everything counts as running), minutes since it last started |
| Heat trends | Hottest point above room over 1 min, 10 min, 1 h, 6 h, 24 h, 7 days and 4 weeks; rise per minute, per hour, per day and per week |
| Vibration, sound and speed trends | Levels over 10 min, 1 h, 24 h and 4 weeks; running speed over 1 h, 24 h and 4 weeks |
| Room trends | Room temperature over 1 h and its rise per hour (cooling failure), humidity over 1 h |
| Which part is changing | The image cell (by its hottest point), vibration band and sound band that rose most against their own past (last hour vs 24 h, 24 h vs 7 days, and 7 days vs 4 weeks), and which one; the image area (by its average) that went coldest against the whole image's change (a stopped fan, blocked fins, a failed heater) |
| Camera check | How closely the thermal image matches its usual layout (camera moved or blocked) |

Day, week and 4-week figures count running time only. The 4-week figures come from one average per running hour, kept for 4 weeks. History features stay empty until there is enough history (1 h needs 48 minutes, 24 h needs 20 hours, 7 days needs 2 days, 4 weeks needs 2 weeks), and they come out identical at 1 or 4 windows a second, so recorded training rows and live rows match.

Training and the device must compute features with the same code: import `effente.features` and `effente.history`, never copy them.

## Simulators

- [effente/simulate_conveyor.py](../../effente/simulate_conveyor.py): a belt conveyor week (motor, two-stage gearbox, drive pulley, idlers, a 60 m belt with a splice) on a shift schedule, with idler 2's bearing slowly wearing from Wednesday, a jam on Thursday, the belt rubbing the frame on Friday and a damaged splice from Saturday.
- [effente/simulate_server.py](../../effente/simulate_server.py): a data-center server rack over three days (six fans, power-supply fans, a hard drive, coil whine, the room's cooling cycling), with a UPS alarm, a rack bump, a slowly overheating power connector, a wearing fan bearing, a clicking hard drive, a fan failure and a cooling failure.

Check that every fault shows up in the recorded features:

```bash
python3 -m device.intake.coverage_check data/fake/conveyor-week --sim conveyor
python3 -m device.intake.coverage_check data/fake/server-rack --sim server
```

[FAILURE_MODES.md](FAILURE_MODES.md) maps realistic failures to the sensors and features, with the limits and the simulation results.

## Tests

```bash
python3 -m device.intake.tests.test_intake
python3 -m device.intake.tests.test_conveyor
python3 -m device.intake.tests.test_stream
python3 -m device.intake.tests.test_uploader
```

## Not done yet

- `UnoQSource` (real sensor packets from the microcontroller) needs the hardware. It must give timestamps on a clock that does not reset at reboot (wall-clock time), or history restarts after every reboot.
- Feature settings and thresholds are starting values, not tuned on real recordings.
- Microphone samples: set `audio_bits` (24 for the INMP441) in the UNO Q source, so levels and clipping use the right full scale; raw snippets then store audio as 32-bit.
