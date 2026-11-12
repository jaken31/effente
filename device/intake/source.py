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
            self.sensors = (("accel",) + (("gyro",) if "gyro" in first.files else ())
                            + ("audio", "thermal") + (("room",) if "room_t_us" in first.files else ()))

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
                c = e


class UnoQSource:
    """Real sensor data from the UNO Q's microcontroller. Not written yet.

    Plan: the microcontroller sends packets of [sensor id, sequence number,
    first-sample timestamp, samples] over the Arduino Bridge; this class
    checks sequence numbers for drops, converts microcontroller time to wall
    time (so history survives reboots), and yields `Packet`s like FileSource.
    """

    def __init__(self, *_, **__):
        raise NotImplementedError("UnoQSource needs the hardware; use FileSource for now")
