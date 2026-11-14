"""Where sensor data comes from.

A source yields `Packet`s in roughly time order, the way data will arrive
from the UNO Q's microcontroller: each packet is one sensor's samples with a
timestamp for every sample, in seconds.

  FileSource   reads recording files (.npz, the TRD format plus extra sensor
               fields), such as the fake day from effente.simulate_day or raw
               snippets saved by the recorder
  UnoQSource   placeholder for the real thing, to be written when the
               hardware arrives (Bridge packets from the microcontroller)
  The simulators in effente.simulate_conveyor and effente.simulate_server are
  sources too.

Recommended sensor settings (starting values):
  MPU6050   1 kHz, digital low-pass 260 Hz, accelerometer +-8 g (at +-2 g a hard
            jolt clips; at +-8 g the sensor's own noise is still far above its
            step size, so nothing is lost), gyroscope +-500 deg/s
  INMP441   16 kHz (hears up to 8 kHz). 32 kHz would hear to 16 kHz (more
            bearing, leak and coil-whine detail) at twice the data rate, if the
            Bridge can carry it
  MLX90640  refresh setting 8 Hz, which gives 4 full frames a second
  BME280    1 reading a second
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterator

import numpy as np

from effente import features as F


@dataclass
class Packet:
    sensor: str  # "accel", "gyro", "audio", "thermal" or "room"
    t: np.ndarray  # (n,) seconds, one per sample or frame
    values: np.ndarray  # accel (n, 3) g; gyro (n, 3) deg/s; audio (n,) counts;
    #                     thermal (n, 24, 32) deg C; room (n, 2) [deg C, humidity %]


@dataclass
class SourceInfo:
    audio_rate_hz: float = 16000.0
    audio_gain_db: float = 0.0
    accel_lsb_per_g: float = 16384.0  # MPU6050 at +-2 g
    accel_range_g: float = 2.0
    gyro_lsb_per_dps: float = 131.0  # MPU6050 at +-250 deg/s
    audio_block_len: int = 256
    audio_bits: int = 16  # microphone sample depth (the INMP441 gives 24 bits)
    week_offset_s: float | None = None  # add to t to get local seconds since a Monday 00:00
    accel_measures_gravity: bool = True  # an MPU does; industrial piezo sensors read 0 g (effente.safety)


class FileSource:
    """Replays recording files as packets of `chunk_s` seconds per sensor, taking
    turns between sensors the way live data arrives."""

    def __init__(self, folder, start_s: float = 0.0, end_s: float = 86400.0, chunk_s: float = 1.0):
        self.folder = Path(folder)
        self.start_s, self.end_s, self.chunk_s = start_s, end_s, chunk_s
        self.files = sorted(self.folder.glob("hour_*.npz")) + sorted(self.folder.glob("clip_*.npz"))
        if not self.files:
            raise FileNotFoundError(f"no hour_*.npz or clip_*.npz files in {self.folder}")
        self.info = SourceInfo()
        self.labels: list[tuple[float, float, str]] = []  # (start, end, label) of each file read
        with np.load(self.files[0]) as first:
            has = lambda k: k in first.files and np.asarray(first[k]).size > 0  # noqa: E731
            self.sensors = (("accel",) + (("gyro",) if "gyro" in first.files else ())
                            + (("audio",) if has("audio") else ()) + (("thermal",) if has("thermal") else ())
                            + (("room",) if "room_t_us" in first.files else ())
                            + (("imu_temp",) if "imu_temp_t_us" in first.files else ()))

    def label_at(self, t: float) -> str:
        for a, b, lab in self.labels:
            if a <= t < b:
                return lab
        return ""

    def _covering(self):
        for p in self.files:
            if p.name.startswith("hour_"):
                h = int(p.stem.split("_")[1])
                if (h + 1) * 3600 <= self.start_s or h * 3600 >= self.end_s:
                    continue
            yield p

    @staticmethod
    def _week_offset(r) -> float | None:
        """Seconds to add to t for local seconds since a Monday 00:00, if known."""
        if "week_offset_s" in r.files:
            v = float(r["week_offset_s"])
            return None if math.isnan(v) else v
        try:  # fake files count seconds from midnight of their date
            d = date.fromisoformat(str(r["recorded_at"])[:10])
        except (KeyError, ValueError):
            return None
        return d.weekday() * 86400.0

    def packets(self) -> Iterator[Packet]:
        for path in self._covering():
            r = np.load(path)
            acc_t = r["accel_t_us"] / 1e6
            lsb = float(r["accel_lsb_per_g"])
            acc = r["samples"].astype(np.float32) / lsb
            gyro = (r["gyro"].astype(np.float32) / float(r["gyro_lsb_per_dps"])) if "gyro" in r.files else None
            bl = int(r["audio_block_len"])
            rate = float(r["audio_rate_hz"])
            bt = r["audio_t_us"] / 1e6
            if len(bt) > 1:  # real rate from the block timestamps, not the nominal one
                rate = (len(bt) - 1) * bl / (bt[-1] - bt[0])
            self.info = SourceInfo(
                audio_rate_hz=rate, audio_gain_db=float(r["audio_gain_db"]), accel_lsb_per_g=lsb,
                accel_range_g=32768.0 / lsb, audio_block_len=bl, week_offset_s=self._week_offset(r),
                gyro_lsb_per_dps=float(r["gyro_lsb_per_dps"]) if "gyro_lsb_per_dps" in r.files else 131.0,
                audio_bits=int(r["audio_bits"]) if "audio_bits" in r.files else 16)
            audio = r["audio"]
            th_t = r["thermal_t_us"] / 1e6
            thermal = r["thermal"]
            has_room = "room_t_us" in r.files
            if has_room:
                room_t = r["room_t_us"] / 1e6
                room = np.stack([r["room_c"], r["humidity_pct"]], 1)
            has_imu_temp = "imu_temp_t_us" in r.files
            if has_imu_temp:
                it_t, it_v = r["imu_temp_t_us"] / 1e6, np.asarray(r["imu_temp_c"], np.float32)[:, None]

            lo = max(self.start_s, acc_t[0] if len(acc_t) else self.start_s)
            hi = min(self.end_s, acc_t[-1] + 1e-3 if len(acc_t) else self.end_s)
            self.labels.append((float(acc_t[0]) if len(acc_t) else lo, hi + 1.0, str(r["label"])))
            c = math.floor(lo / self.chunk_s) * self.chunk_s
            while c < hi:
                e = min(c + self.chunk_s, self.end_s)
                a0, a1 = np.searchsorted(acc_t, [max(c, self.start_s), e])
                if a1 > a0:
                    yield Packet("accel", acc_t[a0:a1], acc[a0:a1])
                    if gyro is not None and len(gyro) == len(acc):
                        yield Packet("gyro", acc_t[a0:a1], gyro[a0:a1])
                b0, b1 = np.searchsorted(bt, [max(c, self.start_s), e])
                if b1 > b0:
                    t = (bt[b0:b1, None] + np.arange(bl)[None, :] / rate).ravel()
                    yield Packet("audio", t, audio[b0 * bl:b1 * bl])
                f0, f1 = np.searchsorted(th_t, [max(c, self.start_s), e])
                if f1 > f0:
                    yield Packet("thermal", th_t[f0:f1], thermal[f0:f1])
                if has_room:
                    r0, r1 = np.searchsorted(room_t, [max(c, self.start_s), e])
                    if r1 > r0:
                        yield Packet("room", room_t[r0:r1], room[r0:r1])
                if has_imu_temp:
                    i0, i1 = np.searchsorted(it_t, [max(c, self.start_s), e])
                    if i1 > i0:
                        yield Packet("imu_temp", it_t[i0:i1], it_v[i0:i1])
                c = e


def imu_temp_c(raw, who_am_i: int = 0x70) -> float:
    """The IMU's temperature from its TEMP_OUT counts. The two chips differ: the MPU-6500 (WHO_AM_I 0x70,
    and 0x71/0x73 for the 9250/9255) gives raw / 333.87 + 21 (its datasheet: 333.87 per degC, 0 at 21 degC);
    the MPU-6050 (0x68) gives raw / 340 + 36.53. Until 13 Nov the 6050 formula was used for our 6500, which
    read about 15 degC too high (43 degC for a board at about 28)."""
    if who_am_i == 0x68:
        return float(raw) / 340.0 + 36.53
    return float(raw) / 333.87 + 21.0


class UnoQSource:
    """Live sensor packets from the UNO Q's microcontroller over the Bridge.

    The sketch (device/app/effente/sketch) sends a notification "imu" about 40 times a second:
    (first sample number, micros() when read, samples, accelerometer bytes, gyroscope bytes,
    FIFO overflows, IMU temperature raw). The app wires it here with
    Bridge.provide("imu", source.on_imu); `packets()` then yields, per batch:

      accel      (n, 3) g at 1 kHz          (big-endian int16 counts / 4096, the +-8 g range)
      gyro       (n/10, 3) deg/s at 100 Hz  (every 10th sample; the Bridge line cannot carry more)
      imu_temp   (1, 1) deg C               (the IMU's temperature sensor, imu_temp_c(): the MPU-6500's
                                             raw / 333.87 + 21, or the MPU-6050's raw / 340 + 36.53)

    Times are the microcontroller's micros() (unwrapped past its 71-minute wrap), mapped onto
    wall-clock time by the offset seen when batches arrive, tracked slowly, and re-anchored if the
    two drift more than a second apart (for example when NTP corrects the board's clock). Lost
    samples are counted from the sample numbers; the timestamps simply skip them.

    The microphone as fitted (13 Nov): an INMP441 on an ESP32 (firmware/esp32_mic), wired to the header's
    UART. Raw sound can't pass the Bridge, so the ESP32 works the sound readings out itself and the sketch
    passes each set on as a notification "snd" (on_sound). They are not packets and nothing waits for
    them: feature_stream asks sound_features_at() for each row.

    Slots for sensors not fitted yet (unverified: no sketch sends these yet). Each is off unless
    switched on, because the pipeline waits for every sensor it is told about:
      audio=True    INMP441 microphone, notification "audio": (micros() at the last sample, samples as
                    big-endian int32, already shifted down to 24 bits). 16 kHz by default. Raw 16 kHz audio
                    is about 64 kB/s, far more than the Bridge line carries (about 11.5 kB/s, most of it
                    already used by the IMU), so the microphone needs another route or processing on the
                    microcontroller first (device/app/README.md, "Limits and known issues")
      thermal=True  MLX90640-D55 thermal camera (55 degree view, 24 x 32 pixels, I2C address 0x33),
                    notification "thermal": (micros() when read, 768 big-endian int16 in hundredths of
                    a deg C, row by row). One frame is 1.5 kB, so 4 frames a second also needs more
                    room on the line than is left
    """

    sensors = ("accel", "gyro", "imu_temp")

    def __init__(self, rate_hz: float = 1000.0, gyro_every: int = 10, accel_lsb_per_g: float = 4096.0,
                 gyro_lsb_per_dps: float = 131.0, max_packets: int = 3000, clock=None,
                 audio: bool = False, thermal: bool = False, audio_rate_hz: float = 16000.0, audio_bits: int = 24):
        import queue
        import time as _time
        self.rate, self.nominal, self.gyro_every = rate_hz, rate_hz, gyro_every
        self.lsb_g, self.lsb_dps = accel_lsb_per_g, gyro_lsb_per_dps
        self.clock = clock or _time.time
        self.q = queue.Queue(maxsize=max_packets)
        lt = _time.localtime(self.clock())
        now = self.clock()
        week_t = lt.tm_wday * 86400 + lt.tm_hour * 3600 + lt.tm_min * 60 + lt.tm_sec + (now % 1)
        self.sensors = UnoQSource.sensors + (("audio",) if audio else ()) + (("thermal",) if thermal else ())
        self.info = SourceInfo(accel_lsb_per_g=accel_lsb_per_g, accel_range_g=32768.0 / accel_lsb_per_g,
                               gyro_lsb_per_dps=gyro_lsb_per_dps, week_offset_s=week_t - now,
                               audio_rate_hz=audio_rate_hz, audio_bits=audio_bits)
        self.offset: float | None = None  # wall time minus microcontroller time
        self.wraps, self.last_us = 0, None
        self.anchor: tuple[int, float] | None = None  # (sample number, microcontroller seconds) for the rate
        self.expected: int | None = None
        self.last_t = {s: -math.inf for s in self.sensors}
        self.imu_who = 0x70  # the chip's WHO_AM_I, for the temperature formula; main.py sets it from the heartbeat
        self.stats = {"batches": 0, "samples": 0, "lost": 0, "gaps": 0, "overflows": 0, "dropped_packets": 0,
                      "reanchors": 0, "rate_hz": rate_hz, "imu_temp_c": None, "audio_blocks": 0, "thermal_frames": 0}
        self._closed = False
        # the microphone's readings (an ESP32 on the header's UART): the last few sets, as (window end, readings)
        self._sound: list[tuple[float, np.ndarray]] = []
        self.stats.update(sound_frames=0, sound_wifi_frames=0, sound_bad=0, sound_age_s=None)
        # the thermal camera's frames, fetched over WiFi from the ESP32 (device/intake/thermal_link.py): the last
        # few, as (when it arrived, frame in deg C)
        self._heat: list[tuple[float, np.ndarray]] = []
        self.stats.update(heat_frames=0, heat_bad_pixels=0, heat_age_s=None)

    # ------------------------------------------------------------ the microphone's readings (an ESP32, by UART)
    SOUND_NAMES = tuple(n for n in F.FEATURE_NAMES if n.startswith("snd_"))
    SOUND_SENT = tuple(n for n in SOUND_NAMES if n != "snd_env_order")  # what the ESP32 sends, in order
    SOUND_WIFI_S = 0.01  # a set's time on WiFi, about
    SOUND_FRAME_S = 0.095  # the frame's time on the UART line (sent 8 bytes every 4 ms): stamped at its last byte
    # How far a set's window may end before or after the row's. Sets come one a second, and each takes about
    # 0.4 s to get here, so a row's set ended up to about 1.4 s before the row does.
    SOUND_EARLY_S, SOUND_LATE_S = 1.6, 0.25

    def on_sound(self, t_us, age_ms, seq, payload) -> None:
        """One set of sound readings (the sketch's "snd" notification, one a second): `t_us` is the microcontroller's micros()
        when the frame's last byte arrived from the ESP32, `age_ms` how long before the ESP32 started sending
        the window ended, `payload` the 45 readings (SOUND_SENT) as half-precision floats (or float32). The ESP32 worked them out from its
        own 1.024 s window (firmware/esp32_mic); here they only get a time on our clock."""
        raw = bytes(payload)
        if len(raw) == 2 * len(self.SOUND_SENT):
            v = np.frombuffer(raw, "<e").astype(np.float32)
        elif len(raw) == 4 * len(self.SOUND_SENT):
            v = np.frombuffer(raw, "<f4")
        else:
            v = np.empty(0, np.float32)
        if len(v) != len(self.SOUND_SENT):
            self.stats["sound_bad"] += 1
            return
        t_us, seq = int(t_us), int(seq) & 0xFFFF
        if self.offset is None or self.last_us is None:
            arrived = self.clock()  # no IMU batch yet to map the microcontroller's clock: now is close enough
        else:
            wraps = self.wraps  # the same micros() as the IMU's batches, so the same wraps, give or take one
            if t_us < self.last_us and self.last_us - t_us > 2 ** 31:
                wraps += 1
            elif t_us > self.last_us and t_us - self.last_us > 2 ** 31:
                wraps -= 1
            arrived = self.offset + (t_us + wraps * 2 ** 32) / 1e6
        self._sound_set(arrived - self.SOUND_FRAME_S - min(int(age_ms), 60000) / 1000.0, v)

    def on_sound_wifi(self, age_ms, seq, payload) -> None:
        """One set of sound readings straight from the ESP32 over WiFi (device/intake/thermal_link.py SoundLink):
        every set, about 4 a second, at no cost to the microcontroller. `payload` is the 45 float32 readings,
        `age_ms` as in on_sound. Timed by its arrival: WiFi adds a few ms, which SOUND_WIFI_S allows for."""
        raw = bytes(payload)
        if len(raw) != 4 * len(self.SOUND_SENT):
            self.stats["sound_bad"] += 1
            return
        self.stats["sound_wifi_frames"] += 1
        self._sound_set(self.clock() - self.SOUND_WIFI_S - min(int(age_ms), 60000) / 1000.0, np.frombuffer(raw, "<f4"))

    def _sound_set(self, end: float, v) -> None:
        """Keep a set whose window ended at `end` (our clock)."""
        row = np.full(len(self.SOUND_NAMES), np.nan)
        row[[self.SOUND_NAMES.index(n) for n in self.SOUND_SENT]] = v
        self._sound = (self._sound + [(end, row)])[-8:]  # replaced, not changed: the pipeline thread reads it
        self.stats["sound_frames"] += 1
        self.stats["sound_age_s"] = round(self.clock() - end, 3)

    HEAT_KEEP = 8  # frames kept: 4 seconds at the camera's 2 a second
    HEAT_MAX_AGE_S = 3.0  # a frame older than this is not this row's

    def on_thermal_frame(self, frame_c) -> None:
        """One frame from the ESP32's camera (a (24, 32) array, deg C), timed by its arrival: it is about 0.5 s
        old by then (the camera fills a frame in two halves), which is nothing to how fast heat changes.
        A pixel the camera could not give (the ESP32 writes absolute zero) takes the frame's median, so one
        dead pixel doesn't read as a cold spot; a frame that is mostly such pixels is dropped."""
        f = np.array(frame_c, np.float32)
        if f.shape != (24, 32):
            return
        bad = ~np.isfinite(f) | (f < -100.0)
        if bad.any():
            self.stats["heat_bad_pixels"] += int(bad.sum())
            if bad.mean() > 0.5:
                return
            f[bad] = np.median(f[~bad])
        self._heat = (self._heat + [(self.clock(), f)])[-self.HEAT_KEEP:]  # replaced, not changed
        self.stats["heat_frames"] += 1

    def thermal_frames_at(self, window_start: float, window_end: float):
        """The camera's frames for a row: those that arrived during its window, or failing that the newest one
        if it is under HEAT_MAX_AGE_S old; None when there is none (heat from the camera is then missing).
        Never waits."""
        frames = self._heat
        if not frames:
            return None
        self.stats["heat_age_s"] = round(self.clock() - frames[-1][0], 2)
        inside = [f for t, f in frames if window_start <= t < window_end]
        if not inside and window_end - self.HEAT_MAX_AGE_S <= frames[-1][0] <= window_end + 1.0:
            inside = [frames[-1][1]]
        return np.stack(inside) if inside else None

    def sound_features_at(self, window_end: float):
        """The sound readings for a row whose window ends at `window_end`: the set whose own window ends nearest,
        if within SOUND_EARLY_S before to SOUND_LATE_S after; else None (sound is then missing from the row).
        A row is made as soon as its vibration is in, and the set for the same moment is still on its way
        (the ESP32's maths, then 0.1 s on the UART line), and only one set a second is passed on, so a row
        usually gets a set from 0.4 to 1.4 s before it. Never waits: without the ESP32 the rows come just as fast, with no sound."""
        best = None
        for end, row in self._sound:
            d = end - window_end
            if -self.SOUND_EARLY_S <= d <= self.SOUND_LATE_S and (best is None or abs(d) < abs(best[0])):
                best = (d, row)
        return None if best is None else best[1]

    # ------------------------------------------------------------ from the Bridge thread
    def on_imu(self, first, t_us, n, acc, gyr, overflows=0, temp_raw=None) -> None:
        first, t_us, n = int(first), int(t_us), int(n)
        if self.last_us is not None and t_us < self.last_us and self.last_us - t_us > 2 ** 31:
            self.wraps += 1  # micros() wrapped round (every 71.6 minutes)
        self.last_us = t_us
        mcu_s = (t_us + self.wraps * 2 ** 32) / 1e6
        seen = self.clock() - mcu_s
        if self.offset is None or abs(seen - self.offset) > 1.0:
            if self.offset is not None:
                self.stats["reanchors"] += 1
            self.offset = seen
        else:
            self.offset += 0.01 * (seen - self.offset)  # follows slow drift, not each batch's delay
        if self.anchor is None or first < self.anchor[0]:
            self.anchor = (first, mcu_s)
        elif mcu_s - self.anchor[1] > 10:
            self.rate = float(np.clip((first + n - self.anchor[0]) / (mcu_s - self.anchor[1]),
                                      0.9 * self.nominal, 1.1 * self.nominal))
        if self.expected is not None and first != self.expected:
            self.stats["gaps"] += 1
            self.stats["lost"] += max(0, first - self.expected)
        self.expected = first + n
        a = np.frombuffer(bytes(acc), ">i2").reshape(-1, 3).astype(np.float32) / self.lsb_g
        n = len(a)
        t_last = self.offset + mcu_s  # the FIFO was read just after its newest sample
        t = t_last - (n - 1 - np.arange(n)) / self.rate
        self._put("accel", t, a)
        if gyr is not None and len(gyr):
            g = np.frombuffer(bytes(gyr), ">i2").reshape(-1, 3).astype(np.float32) / self.lsb_dps
            idx = np.nonzero((first + np.arange(n)) % self.gyro_every == 0)[0][:len(g)]
            if len(idx) == len(g):
                self._put("gyro", t[idx], g)
        if temp_raw is not None:
            temp = imu_temp_c(temp_raw, self.imu_who)
            self.stats["imu_temp_c"] = round(temp, 2)
            self._put("imu_temp", np.array([t_last]), np.array([[temp]], np.float32))
        self.stats["batches"] += 1
        self.stats["samples"] += n
        self.stats["overflows"] = int(overflows)
        self.stats["rate_hz"] = round(self.rate, 2)

    def _wall_s(self, t_us) -> float | None:
        """A microcontroller micros() as wall-clock seconds, by the offset the IMU batches keep; None until the
        first IMU batch has arrived."""
        if self.offset is None:
            return None
        return self.offset + (int(t_us) + self.wraps * 2 ** 32) / 1e6

    def on_audio(self, t_us, samples) -> None:
        """INMP441 samples (see the class notes). Ignored unless the source was made with audio=True."""
        t_last = self._wall_s(t_us)
        if "audio" not in self.sensors or t_last is None:
            return
        raw = bytes(samples)
        a = np.frombuffer(raw[:len(raw) - len(raw) % 4], ">i4").astype(np.float32)  # whole samples only
        if not len(a):
            return
        t = t_last - (len(a) - 1 - np.arange(len(a))) / self.info.audio_rate_hz
        self._put("audio", t, a)
        self.stats["audio_blocks"] += 1

    def on_thermal(self, t_us, frame) -> None:
        """One MLX90640-D55 frame (see the class notes). Ignored unless the source was made with thermal=True."""
        t = self._wall_s(t_us)
        if "thermal" not in self.sensors or t is None:
            return
        raw = bytes(frame)
        if len(raw) != 24 * 32 * 2:
            return  # a cut-off frame: never guess the missing pixels
        v = np.frombuffer(raw, ">i2")
        self._put("thermal", np.array([t]), (v.astype(np.float32) / 100.0).reshape(1, 24, 32))
        self.stats["thermal_frames"] += 1

    def _put(self, sensor, t, v) -> None:
        import queue
        lo = self.last_t[sensor]
        if t[0] <= lo:  # tracking the clock offset must never move a sensor's time backwards
            t = t + (lo - t[0]) + 1e-6
        self.last_t[sensor] = float(t[-1])
        p = Packet(sensor, t, v)
        while True:
            try:
                self.q.put_nowait(p)
                return
            except queue.Full:  # the pipeline is behind: drop the oldest data, never block the Bridge
                try:
                    self.q.get_nowait()
                    self.stats["dropped_packets"] += 1
                except queue.Empty:
                    pass

    # ------------------------------------------------------------ for the pipeline
    def packets(self) -> Iterator[Packet]:
        import queue
        while True:
            try:
                p = self.q.get(timeout=1.0)
            except queue.Empty:
                if self._closed:
                    return
                continue
            if p is None:  # close(): everything queued before it has been delivered
                return
            yield p

    def close(self) -> None:
        self._closed = True
        try:
            self.q.put_nowait(None)
        except Exception:  # noqa: BLE001
            pass

    def label_at(self, t: float) -> str:
        return ""
