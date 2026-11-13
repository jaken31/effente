"""Numbers the Thermal page shows, from what logic.heat_view() decoded. Plain Python.

Frames are decoded only by app.data.logic (decode_frame, through heat_view);
this module only adds up pixels that were already decoded. It judges nothing.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

W, H = 32, 24  # MLX90640 pixels (CONTRACT.md, Heat)
ROWS, COLS = 6, 8  # the contract's grid of 4 x 4 pixel blocks

# Pixels at least this far above the background count as hot (the mockup's KPI).
HOT_ABOVE_ROOM_C = 15.0

# The fixed colour range the camera panel offers next to auto.
FIXED_RANGE = (20, 60)

# Heat trend window.
TREND_MS = 120_000


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


@dataclass
class FrameStats:
    hot_index: int
    hot_c: float
    mean_c: float
    spread_c: float  # 95th minus 5th percentile
    hot_pixels: int | None  # None without a background to compare with

    @property
    def hot_row(self) -> int:
        return self.hot_index // W

    @property
    def hot_col(self) -> int:
        return self.hot_index % W


def frame_stats(pixels: Sequence[float], background_c: float | None) -> FrameStats:
    hot_i = max(range(len(pixels)), key=lambda i: (pixels[i], -i))  # first hottest
    s = sorted(pixels)
    n = len(s)
    hot = None
    if background_c is not None:
        hot = sum(1 for v in pixels if v >= background_c + HOT_ABOVE_ROOM_C)
    return FrameStats(
        hot_index=hot_i,
        hot_c=pixels[hot_i],
        mean_c=sum(pixels) / n,
        spread_c=s[int(n * 0.95)] - s[int(n * 0.05)],
        hot_pixels=hot,
    )


def fastest_change(a, b) -> float | None:
    """Biggest change of any pixel between two decoded frames, in degC per second.
    None when their times are missing or not increasing."""
    if a is None or b is None:
        return None
    ta, tb = a["at_ms"], b["at_ms"]
    if not (_num(ta) and _num(tb)) or tb <= ta:
        return None
    dt = (tb - ta) / 1000.0
    return max(abs(x - y) for x, y in zip(a["pixels"], b["pixels"])) / dt


def cells_mean(cells: Sequence[float] | None) -> float | None:
    """Mean of the 48 block averages, which is the image mean above background."""
    return sum(cells) / len(cells) if cells else None


def trend_points(records: Sequence[dict], parse_time) -> list[tuple[float, float, float | None]]:
    """(time ms, hottest above room, average above room) for each record with a
    camera summary, the last TREND_MS of them. `parse_time` is logic's."""
    pts = []
    for r in records:
        h = r.get("heat") if isinstance(r, dict) else None
        if not isinstance(h, dict) or h.get("source") != "camera":
            continue
        mx, bg = h.get("max_c"), h.get("background_c")
        t = parse_time(r.get("at"))
        if not (_num(mx) and _num(bg) and _num(t)):
            continue
        cells = h.get("cells")
        avg = cells_mean(cells) if isinstance(cells, list) and cells and all(_num(c) for c in cells) else None
        pts.append((t, mx - bg, avg))
    if not pts:
        return []
    end = pts[-1][0]
    return [p for p in pts if end - p[0] <= TREND_MS]


def trend_top(values: Sequence[float]) -> int:
    """Top of the trend's degC axis: 40, or the next ten above the data."""
    m = max(values, default=0.0)
    return max(40, math.ceil(m / 10) * 10)


def color_index(v: float, lo: float, hi: float) -> int:
    """0..255 position of v in the colour range."""
    return max(0, min(255, round((v - lo) / (hi - lo) * 255)))


def luminance(rgb: Sequence[int]) -> float:
    r, g, b = rgb
    return 0.299 * r + 0.587 * g + 0.114 * b
