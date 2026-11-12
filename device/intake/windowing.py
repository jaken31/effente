"""Cut incoming packets into aligned, overlapping windows.

Each window is `window_s` long (default 1 s) and a new one starts every
`hop_s` (default 0.25 s), so features update 4 times a second while every
window still holds a full second of data. A full second is what gives 1 Hz
frequency detail, enough to tell once-per-turn from twice-per-turn on slow
machines; shorter windows would blur them.

Sensors run on their own clocks and rates, so windows are cut by timestamp,
never by sample count. A window is emitted once every sensor has delivered
data past its end. A sensor that falls `timeout_s` behind the others is
treated as missing, so one dead sensor costs a single pause instead of
stalling the device. Pass only the sensors a source actually has.
"""

from __future__ import annotations

import math
from typing import Iterator

import numpy as np

from device.intake.source import Packet
from effente.features import WINDOW_S

SENSORS = ("accel", "gyro", "audio", "thermal", "room")
HOP_S = 0.25


class Windower:
    def __init__(self, window_s: float = WINDOW_S, hop_s: float = HOP_S, sensors=SENSORS,
                 timeout_s: float = 15.0):
        if not 0 < hop_s <= window_s:
            raise ValueError("hop_s must be above 0 and no longer than window_s")
        self.window_s, self.hop_s = window_s, hop_s
        self.sensors, self.timeout_s = tuple(sensors), timeout_s
        self.t = {s: np.empty(0) for s in self.sensors}
        self.v: dict[str, np.ndarray | None] = {s: None for s in self.sensors}
        self.latest = {s: -math.inf for s in self.sensors}
        self.base: float | None = None  # window k starts at base + k * hop_s
        self.k = 0
        self.start_t = 0.0
        self.finished = False

    @property
    def next_t0(self) -> float | None:
        return None if self.base is None else self.base + self.k * self.hop_s

    def push(self, p: Packet) -> None:
        if p.sensor not in self.t or len(p.t) == 0:
            return
        if self.base is None:
            self.base = math.floor(p.t[0] / self.hop_s) * self.hop_s
            self.start_t = float(p.t[0])
        self.t[p.sensor] = np.concatenate([self.t[p.sensor], p.t])
        old = self.v[p.sensor]
        self.v[p.sensor] = p.values if old is None or len(old) == 0 else np.concatenate([old, p.values])
        self.latest[p.sensor] = max(self.latest[p.sensor], float(p.t[-1]))

    def finish(self) -> None:
        """No more data is coming: let the last windows out."""
        self.finished = True

    def ready(self) -> Iterator[tuple[float, dict[str, tuple[np.ndarray, np.ndarray] | None]]]:
        while self.base is not None:
            t0 = self.next_t0
            end = t0 + self.window_s
            ahead = max(self.latest.values())
            firsts = [float(self.t[s][0]) for s in self.sensors if len(self.t[s])]
            if firsts and min(firsts) > end + self.timeout_s:
                # A long gap (outage, or the clock set forward): jump to the first window that
                # holds data again, rather than stepping through every empty window (years, at worst).
                self.k = max(self.k + 1, math.ceil((min(firsts) - self.window_s - self.base) / self.hop_s))
                continue
            if self.finished and ahead < end - 0.01 * self.window_s:
                return  # only full windows at the very end, no half-filled stubs
            out = {}
            for s in self.sensors:
                complete = self.latest[s] >= end or (self.finished and self.latest[s] >= t0)
                # A sensor is missing only when it has fallen `timeout_s` behind the
                # others overall, not just behind this window: data arrives in bursts.
                seen = self.latest[s] if self.latest[s] > -math.inf else self.start_t
                missing = not complete and (seen < ahead - self.timeout_s or self.finished)
                if not complete and not missing:
                    return  # wait for more data
                # A sensor that timed out still contributes whatever it delivered
                # inside this window; it is only None when it delivered nothing.
                i0, i1 = np.searchsorted(self.t[s], [t0, end])
                out[s] = (self.t[s][i0:i1], self.v[s][i0:i1]) if i1 > i0 else None
            self.k += 1
            keep_from = self.next_t0  # the next window still needs everything after this
            for s in self.sensors:
                i = int(np.searchsorted(self.t[s], keep_from))
                if i:
                    self.t[s] = self.t[s][i:]
                    self.v[s] = self.v[s][i:]
            spans = [float(v[0][-1] - v[0][0]) for v in out.values() if v is not None and len(v[0]) > 1]
            if not spans or max(spans) < 0.5 * self.window_s:
                continue  # the edge of an outage or gap: no sensor covers half the window
            yield t0, out
