"""Simulated heat readings for contract 3.3 records (the optional `heat` field).

Everything here is MADE UP. The thermal scene is a hand-drawn layout (a warm
motor body, a smaller hot bearing, a cool room) with noise, not a picture of
any machine, and the IMU temperature is a plausible-looking number, not a
measurement. Records carrying it are "simulated": true like every other part
of the simulator.

The summary values follow the definitions of the th_* features in
effente/features.py so the dashboard sees the same shapes it will get from the
device. They are computed here in plain Python only because the simulator is
standard library only. The device sends effente's own values; this file is not
part of the signal pipeline and must not be used as one.

Modes:
  camera  a thermal camera is fitted: summary, IMU temperature and 2 images
  imu     no camera: the IMU temperature only
  none    no `heat` field at all
"""

from __future__ import annotations

import base64
import math
import random
from datetime import datetime, timedelta, timezone

W, H = 32, 24  # MLX90640 pixels
ROWS, COLS = 6, 8  # contract grid: 6 rows of 8 blocks
BLOCK = 4  # pixels per block side
FRAMES_PER_RECORD = 2
MIN_STEP_C = 0.05
MODES = ["camera", "imu", "none"]


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def scene(t_s: float, rng: random.Random) -> list[list[float]]:
    """One 24 x 32 image in degC at time t_s seconds. Illustrative only."""
    room = 23.5 + 0.3 * math.sin(t_s / 90.0)
    motor = 17.0 + 1.5 * math.sin(t_s / 40.0)  # body, above room
    bearing = 11.0 + 2.0 * math.sin(t_s / 25.0 + 1.0)  # small spot, above room
    img = []
    for r in range(H):
        row = []
        for c in range(W):
            v = room
            v += motor * math.exp(-(((c - 20.5) / 5.5) ** 2 + ((r - 11.5) / 4.5) ** 2))
            v += bearing * math.exp(-(((c - 9.0) / 1.4) ** 2 + ((r - 15.0) / 1.4) ** 2))
            v += 1.5 * (r / (H - 1))  # warmer floor at the bottom of the view
            v += rng.gauss(0.0, 0.15)  # sensor noise
            row.append(v)
        img.append(row)
    return img


def encode_frame(img: list[list[float]], at: str) -> dict:
    """Contract 3.3 image: min_c + byte x step_c, every pixel fitting 0..255."""
    flat = [v for row in img for v in row]
    lo = math.floor(min(flat) * 100) / 100  # rounded down, so no pixel is below min_c
    span = max(flat) - lo
    step = max(MIN_STEP_C, math.ceil(span / 255 * 1000) / 1000)  # rounded up, so the hottest fits
    data = bytes(max(0, min(255, round((v - lo) / step))) for v in flat)
    return {"at": at, "min_c": lo, "step_c": step, "data": base64.b64encode(data).decode("ascii")}


def decode_frame(frame: dict) -> list[list[float]]:
    """Inverse of encode_frame, for tests and readers: 24 rows of 32 degC values."""
    raw = base64.b64decode(frame["data"])
    if len(raw) != W * H:
        raise ValueError(f"image must be {W * H} bytes, got {len(raw)}")
    vals = [frame["min_c"] + b * frame["step_c"] for b in raw]
    return [vals[r * W:(r + 1) * W] for r in range(H)]


def _median(xs: list[float]) -> float:
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def summarise(frames: list[list[list[float]]]) -> dict:
    """The summary part of `heat`, over the mean of the window's frames."""
    n = len(frames)
    img = [[sum(f[r][c] for f in frames) / n for c in range(W)] for r in range(H)]
    flat = [v for row in img for v in row]
    bg = _median(flat)
    hot = max(range(len(flat)), key=lambda i: (flat[i], -i))  # first hottest, like numpy argmax
    cells, spots = [], []
    for br in range(ROWS):
        for bc in range(COLS):
            block = [img[br * BLOCK + i][bc * BLOCK + j] - bg for i in range(BLOCK) for j in range(BLOCK)]
            cells.append(round(sum(block) / len(block), 2))
            spots.append(round(max(block), 2))
    return {
        "max_c": round(flat[hot], 2),
        "hot_row": hot // W,
        "hot_col": hot % W,
        "background_c": round(bg, 2),
        "cells": cells,
        "spots": spots,
        "frozen": n > 1 and all(f == frames[0] for f in frames[1:]),
    }


class HeatSim:
    """Heat readings for one simulated device, one call per record."""

    def __init__(self, mode: str = "camera", seed: int | None = None):
        if mode not in MODES:
            raise ValueError(f"heat mode must be one of {MODES}, got {mode!r}")
        self.mode = mode
        # Own generator, so heat never changes the vibration records for a seed.
        self.rng = random.Random(None if seed is None else seed + 1)
        self.t0 = None

    def imu_temp(self, t_s: float) -> float:
        return round(42.5 + 0.4 * math.sin(t_s / 120.0) + self.rng.gauss(0.0, 0.05), 2)

    def heat(self, at: str, interval_s: float = 1.0) -> dict | None:
        """The `heat` field for a record stamped `at`, or None in mode none."""
        if self.mode == "none":
            return None
        end = datetime.fromisoformat(at.replace("Z", "+00:00"))
        if self.t0 is None:
            self.t0 = end
        t_s = (end - self.t0).total_seconds()
        out = {"source": self.mode, "imu_temp_c": self.imu_temp(t_s)}
        if self.mode == "imu":
            return out
        gap = interval_s / FRAMES_PER_RECORD
        times = [end - timedelta(seconds=gap * (FRAMES_PER_RECORD - 1 - k)) for k in range(FRAMES_PER_RECORD)]
        imgs = [scene((t - self.t0).total_seconds(), self.rng) for t in times]
        out.update(summarise(imgs))
        out["scene_match"] = round(self.rng.uniform(0.9, 0.97), 3)  # made up: the simulator learns no view
        out["frames"] = [encode_frame(img, _iso(t)) for img, t in zip(imgs, times)]
        return out
