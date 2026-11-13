"""Band maths for the Spectrum page and the band chart. Plain Python, no Qt.

A record's `spectrum` is 20 vibration band levels, log10, on the 21 edges the
model announced in message 1 (`spectrum_edges_hz`, CONTRACT.md sections 1 and 2).
Nothing here judges a record: the only threshold, RISE_RED, decides which bars
are drawn red, and the page says what it means.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from itertools import pairwise

N_BANDS = 20

# Fixed level scale (log10), so a change shows as a change. Widened only when a
# level falls outside it. From the mockup's BAND_LO and BAND_HI.
LEVEL_LO = -5.6
LEVEL_HI = -2.4

# The accelerometer's own low-pass filter (MPU-6500, device/app/README.md).
# Bands above it are attenuated by the sensor, so the chart shades them.
SENSOR_FILTER_HZ = 218.0

# A band this far above its baseline (log10, so about 3.2 times) is drawn red.
RISE_RED = 0.5

# How many periods the waterfall shows, and how many rows the bands table lists.
WATERFALL_PERIODS = 120
TABLE_ROWS = 10

# Hz labels on the axis, where they fall inside the edges.
HZ_TICKS = (5, 10, 20, 50, 100, 200, 500, 1000, 2000)


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def spectrum_of(record: dict | None) -> list[float] | None:
    """The record's 20 band levels, or None if it has no usable spectrum."""
    s = record.get("spectrum") if isinstance(record, dict) else None
    if not isinstance(s, list) or len(s) != N_BANDS or not all(_num(v) for v in s):
        return None
    return [float(v) for v in s]


def edges_of(model: dict | None) -> list[float] | None:
    """The model's 21 band edges in Hz, or None until a usable model is announced."""
    e = model.get("spectrum_edges_hz") if isinstance(model, dict) else None
    if not isinstance(e, list) or len(e) != N_BANDS + 1 or not all(_num(v) and v > 0 for v in e):
        return None
    if any(b <= a for a, b in pairwise(e)):
        return None
    return [float(v) for v in e]


def log_fraction(hz: float, lo_hz: float, hi_hz: float) -> float:
    """Where `hz` sits between lo_hz (0) and hi_hz (1) on a log axis."""
    return (math.log(hz) - math.log(lo_hz)) / (math.log(hi_hz) - math.log(lo_hz))


def band_spans(edges: Sequence[float] | None, x0: float, width: float) -> list[tuple[float, float]]:
    """Left and right pixel of each band: log positions of the edges, or equal
    widths when the edges are not known."""
    if edges is None:
        w = width / N_BANDS
        return [(x0 + i * w, x0 + (i + 1) * w) for i in range(N_BANDS)]
    lo, hi = edges[0], edges[-1]
    xs = [x0 + log_fraction(e, lo, hi) * width for e in edges]
    return list(pairwise(xs))


def level_range(*series: Iterable[float] | None) -> tuple[float, float]:
    """The level axis: LEVEL_LO to LEVEL_HI, widened to whole steps if any value
    falls outside, so no bar is ever clipped."""
    lo, hi = LEVEL_LO, LEVEL_HI
    for s in series:
        for v in s or ():
            if v < lo:
                lo = math.floor(v * 2) / 2 - 0.1
            if v > hi:
                hi = math.ceil(v * 2) / 2 + 0.1
    return lo, hi


def level_ticks(lo: float, hi: float) -> list[float]:
    """Grid lines at -5.5, -4.5, ... inside the range."""
    out, v = [], math.floor(lo) + 0.5
    while v <= hi:
        if v >= lo:
            out.append(v)
        v += 1.0
    return out


def average_spectrum(records: Iterable[dict]) -> list[float] | None:
    """Band by band mean of the records' spectra, or None if none has one."""
    total, n = [0.0] * N_BANDS, 0
    for r in records:
        s = spectrum_of(r)
        if s is None:
            continue
        total = [a + b for a, b in zip(total, s)]
        n += 1
    return [v / n for v in total] if n else None


def baseline_from(records: Sequence[dict]) -> tuple[list[float] | None, int]:
    """The default baseline: the mean spectrum of the records the device called
    normal, or of every record when none was normal. Returns (baseline, periods)."""
    normal = [r for r in records if r.get("diagnosis") == "normal" and spectrum_of(r)]
    pool = normal or [r for r in records if spectrum_of(r)]
    return average_spectrum(pool), len(pool)


def differences(spectrum: Sequence[float], baseline: Sequence[float]) -> list[float]:
    """Level minus baseline per band, in log10."""
    return [v - b for v, b in zip(spectrum, baseline)]


def times_baseline(diff: float) -> float:
    """A log10 difference as a ratio: +1.0 is 10 times the baseline."""
    return 10.0**diff


def is_red(diff: float | None) -> bool:
    """Whether a band's bar is drawn red: well above its baseline."""
    return diff is not None and diff > RISE_RED


def signed(v: float, digits: int = 1) -> str:
    """+1.6, -0.3, +0.0."""
    text = f"{abs(v):.{digits}f}"
    return ("-" if v < 0 and float(text) != 0 else "+") + text


def range_text(edges: Sequence[float] | None, i: int) -> str:
    if edges is None:
        return "unknown"
    return f"{edges[i]:.1f} to {edges[i + 1]:.1f}"


def band_rows(
    spectrum: Sequence[float], baseline: Sequence[float] | None, edges: Sequence[float] | None, top: int = TABLE_ROWS
) -> list[dict]:
    """The bands table: strongest change from the baseline first."""
    rows = []
    for i, v in enumerate(spectrum):
        d = v - baseline[i] if baseline else None
        rows.append(
            {
                "band": i + 1,
                "range": range_text(edges, i),
                "level": v,
                "baseline": baseline[i] if baseline else None,
                "diff": d,
                "times": times_baseline(d) if d is not None else None,
            }
        )
    if baseline:
        rows.sort(key=lambda r: -r["diff"])
    return rows[:top]


def hover_text(
    i: int, spectrum: Sequence[float], baseline: Sequence[float] | None, edges: Sequence[float] | None
) -> str:
    """The readout under the chart for band index i."""
    hz = f"{range_text(edges, i)} Hz" if edges else "range unknown"
    parts = [f"Band {i + 1}", hz, f"level {spectrum[i]:.2f}"]
    if baseline:
        d = spectrum[i] - baseline[i]
        parts += [f"baseline {baseline[i]:.2f}", f"{times_baseline(d):.1f}× baseline"]
    return " · ".join(parts)


def waterfall_alpha(v: float, lo: float = LEVEL_LO, hi: float = LEVEL_HI) -> float:
    """Opacity of a waterfall cell: faint at the bottom of the scale, solid at the top."""
    t = min(1.0, max(0.0, (v - lo) / (hi - lo)))
    return 0.03 + t * 0.97
