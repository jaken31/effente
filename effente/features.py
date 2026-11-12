"""Features: the one shared implementation for training and inference.

Two kinds of feature, in one fixed list (`FEATURE_NAMES`):

  Per-window  computed from one window of sensor data by `extract(window)`
  History     computed from the past by effente.history.History: slow
              vibration over 8 s, repeating knocks, sudden-event counts, and
              trends from one minute out to seven days. `extract()` leaves
              these NaN; use effente.history.FeatureEngine for full rows.

A sensor missing from a window gives NaN for its features, so training can
tell "missing" apart from "zero". History features stay NaN until there is
enough history behind them.

Per-window families, and what they are for:
  vib_*   accelerometer: level per axis, 20 bands (5-500 Hz), running speed and
          1x/2x/3x/3-10x/0.5x levels (imbalance, misalignment, looseness), the
          second strongest peak (gears, fans), knockiness, peak g and jerk
          (shocks, jams, collisions) and which axis, envelope analysis of the
          100-450 Hz band (bearing and gear defects that modulate a carrier),
          clipping (the sensor's range was exceeded), gravity (device moved)
  gyr_*   gyroscope: rocking and twisting (rack bumps, machine tipping, loose base)
  snd_*   microphone: level, 32 bands (50-8,000 Hz), pitch, clicks, strongest tone
          and how tonal the sound is (whine, squeal), loudest instant (bangs),
          mains hum (electrical load and faults), beeping (alarms, intermittent
          tones), envelope analysis of 2-7 kHz (bearing defects heard as
          modulated noise), clipping
  th_*    thermal camera: hottest point, mean, background, hot spot and where it
          is, hot pixel count, coldest spot (leaks, blocked flow), fastest change
          within the window (fire, arcing, sudden heat), frozen image (camera
          stuck), and an 8x6 grid of heat above background: each cell's average
          (broad areas) and its hottest point (a small part such as a
          connector, which the average dilutes 16 times)
  env_*   room sensor: temperature, humidity, hottest point above room
  time_*  hour of day and weekday, so "normal for this time" can be learned

Spectrum-like features are log10 values, so "x4 the usual" is a difference
of 0.6 whatever the absolute level. Plain NumPy only, so it runs on the UNO Q.
All thresholds are starting values, unverified on real machines.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

WINDOW_S = 1.0
VIB_EDGES_HZ = np.geomspace(5.0, 500.0, 21)  # 20 bands; every band holds a frequency at 1 s windows
SND_EDGES_HZ = np.geomspace(50.0, 8000.0, 33)  # 32 bands
TH_GRID = (6, 8)  # rows, columns of the coarse heat map (4x4 pixels each)
TH_HOT_ABOVE_C = 5.0  # a pixel counts as hot this far above background
SPEED_SEARCH_HZ = (8.0, 120.0)
PEAK2_SEARCH_HZ = (3.0, 200.0)
TONE_SEARCH_HZ = (100.0, 7900.0)
VIB_ENV_BAND_HZ = (100.0, 450.0)
SND_ENV_BAND_HZ = (2000.0, 7000.0)
ENV_SEARCH_HZ = (2.0, 400.0)
MIN_PROMINENCE = 4.0  # a peak must be this many times the median level
CLIP_FRACTION = 0.98  # a sample within 2 % of full scale counts as clipped
LOG_EPS = 1e-6

SLOW_EDGES_HZ = np.geomspace(0.4, 25.0, 7)  # 6 bands for the 8 s slow-vibration view
TREND_HORIZONS = ("1m", "10m", "1h", "6h", "24h", "7d")


@dataclass
class Window:
    """One window of aligned sensor data. Any sensor may be None (missing)."""

    t0: float  # start time, seconds
    accel_g: np.ndarray | None = None  # (n, 3) in g, including gravity
    accel_t: np.ndarray | None = None  # (n,) sample times, used for history
    accel_rate_hz: float = 1000.0  # measured from timestamps where possible
    accel_range_g: float = 2.0  # the accelerometer's full scale, for clipping
    gyro_dps: np.ndarray | None = None  # (n, 3) in degrees per second
    audio: np.ndarray | None = None  # (m,) microphone counts
    audio_full_scale: float = 32767.0  # largest count: 32767 for 16-bit samples, 8388607 for 24-bit
    audio_rate_hz: float = 16000.0
    audio_gain_db: float = 0.0  # firmware gain, for dB SPL
    thermal_c: np.ndarray | None = None  # (f, 24, 32) frames in deg C
    room: np.ndarray | None = None  # (k, 2) readings of [temperature deg C, humidity %]
    week_t: float | None = None  # local seconds since a Monday 00:00, if the clock is known


def _band_label(prefix: str, edges: np.ndarray, i: int) -> str:
    lo, hi = edges[i], edges[i + 1]
    fmt = (lambda v: f"{v:.0f}") if hi >= 20 else (lambda v: f"{v:.1f}")
    return f"{prefix} {fmt(lo)}-{fmt(hi)} Hz"


def _build_names():
    names, desc, kind, sensor = [], {}, {}, {}

    def add(name, text, k, s):
        names.append(name)
        desc[name], kind[name], sensor[name] = text, k, s

    V, G, S, H, R, T = "vibration", "rotation", "sound", "heat", "room", "clock"
    for ax in "xyz":
        add(f"vib_rms_{ax}", f"vibration level, {ax} axis", "log", V)
    for i in range(len(VIB_EDGES_HZ) - 1):
        add(f"vib_band_{i:02d}", _band_label("vibration", VIB_EDGES_HZ, i), "log", V)
    add("vib_speed_hz", "running speed (strongest steady peak, 8-120 Hz)", "hz", V)
    add("vib_1x", "vibration once per turn", "log", V)
    add("vib_2x", "vibration twice per turn", "log", V)
    add("vib_3x", "vibration three times per turn", "log", V)
    add("vib_harm_3_10", "vibration at 3-10x running speed (rattle)", "log", V)
    add("vib_half", "vibration at half running speed", "log", V)
    add("vib_peak2_hz", "second strongest vibration peak, 3-200 Hz", "hz", V)
    add("vib_peak2", "level of the second strongest vibration peak", "log", V)
    add("vib_crest", "vibration knocks (peak over rms)", "ratio", V)
    add("vib_kurtosis", "vibration impulsiveness", "ratio", V)
    add("vib_peak_g", "biggest jolt in the window", "log", V)
    add("vib_jerk", "fastest change of acceleration (sudden shock)", "log", V)
    add("vib_shock_axis", "axis of the biggest jolt (0 x, 1 y, 2 z)", "index", V)
    add("vib_env_peak_hz", "envelope of 100-450 Hz vibration: strongest repeating rate (bearings, gears)", "hz", V)
    add("vib_env_peak", "how far that envelope rate stands above the rest", "log", V)
    add("vib_env_order", "that envelope rate as a multiple of running speed (0 if no speed)", "ratio", V)
    add("vib_velocity_mm_s", "vibration velocity, 10-250 Hz rms (ISO 10816-like, partial band)", "mm_s", V)
    add("vib_line_2x", "vibration at 100/120 Hz (twice mains: electrical, magnetic)", "log", V)
    add("vib_clip_frac", "share of samples at the accelerometer's limit (range exceeded)", "ratio", V)
    add("vib_freefall_frac", "share of samples near 0 g (device falling)", "ratio", V)
    for ax in "xyz":
        add(f"vib_gravity_{ax}", f"device tilt (gravity on {ax})", "g", V)
    add("gyr_rms", "rocking and twisting, average", "log", G)
    add("gyr_peak", "rocking and twisting, biggest", "log", G)

    add("snd_level_db", "sound level", "db", S)
    for i in range(len(SND_EDGES_HZ) - 1):
        add(f"snd_band_{i:02d}", _band_label("sound", SND_EDGES_HZ, i), "log", S)
    add("snd_centroid_hz", "sound pitch (spectral centroid)", "hz", S)
    add("snd_click", "clicks in the sound", "log", S)
    add("snd_tone_hz", "strongest tone, 100-7,900 Hz", "hz", S)
    add("snd_tone_db", "how far the strongest tone stands above the rest", "db", S)
    add("snd_flatness", "how noisy (1) versus tonal (0) the sound is", "ratio", S)
    add("snd_peak_db", "loudest 10 ms in the window (bangs)", "db", S)
    add("snd_mains_db", "mains hum (100/120 Hz and harmonics) above nearby sound", "db", S)
    add("snd_tone_mod_db", "how much the strongest tone switches on and off (beeping)", "db", S)
    add("snd_env_peak_hz", "envelope of 2-7 kHz sound: strongest repeating rate (bearings)", "hz", S)
    add("snd_env_peak_db", "how far that envelope rate stands above the rest", "db", S)
    add("snd_env_order", "that envelope rate as a multiple of running speed (0 if no speed)", "ratio", S)
    add("snd_kurt_hf", "impulsiveness of 2-7 kHz sound (cavitation, rubs, arcing, bearing impacts)", "ratio", S)
    add("snd_clip_frac", "share of samples at the microphone's limit", "ratio", S)

    add("th_max_c", "hottest point in view", "c", H)
    add("th_mean_c", "average temperature in view", "c", H)
    add("th_background_c", "background temperature", "c", H)
    add("th_hotspot_c", "hot spot above background", "c", H)
    add("th_hot_row", "row of the hottest pixel (0-23)", "index", H)
    add("th_hot_col", "column of the hottest pixel (0-31)", "index", H)
    add("th_hot_pixels", f"pixels more than {TH_HOT_ABOVE_C:.0f} degC above background", "count", H)
    add("th_cold_spot_c", "coldest spot below background (leaks, blocked flow)", "c", H)
    add("th_change_c", "fastest change of any pixel within the window (sudden heat)", "c", H)
    add("th_frozen", "1 if every frame in the window is identical (camera stuck)", "ratio", H)
    add("th_spread_c", "temperature spread across the image (collapses if the camera is blocked)", "c", H)
    rows, cols = TH_GRID
    for r in range(rows):
        for c in range(cols):
            add(f"th_cell_{r}{c}", f"heat at row {r + 1}, column {c + 1} of the {rows}x{cols} image grid", "c", H)
    for r in range(rows):
        for c in range(cols):
            add(f"th_spot_{r}{c}", f"hottest point at row {r + 1}, column {c + 1} of the image grid, above "
                f"background (a small part heating: connector, terminal, bearing)", "c", H)

    add("env_room_c", "room temperature", "c", R)
    add("env_humidity_pct", "room humidity", "pct", R)
    add("env_hot_above_room_c", "hottest point in view above room temperature", "c", R)
    add("env_dew_point_c", "dew point of the room air", "c", R)
    add("env_condensation_margin_c", "coldest surface in view above the dew point (below 0: condensation)", "c", R)

    add("time_hour_sin", "time of day (sine)", "ratio", T)
    add("time_hour_cos", "time of day (cosine)", "ratio", T)
    add("time_weekday_sin", "day of the week (sine; Sunday night runs smoothly into Monday)", "ratio", T)
    add("time_weekday_cos", "day of the week (cosine)", "ratio", T)
    n_base = len(names)

    # ---- history features (effente.history)
    for i in range(len(SLOW_EDGES_HZ) - 1):
        add(f"slow_band_{i:02d}", _band_label("slow vibration (8 s)", SLOW_EDGES_HZ, i), "log", V)
    add("slow_peak1_hz", "strongest slow vibration peak, 0.4-20 Hz (pulleys, idlers)", "hz", V)
    add("slow_peak1", "level of the strongest slow vibration peak", "log", V)
    add("slow_peak2_hz", "second strongest slow vibration peak", "hz", V)
    add("slow_peak2", "level of the second strongest slow vibration peak", "log", V)
    add("knock_period_s", "time between repeating knocks or clicks over the last 5 min", "s", V)
    add("knock_repeat", "how regularly the knocks repeat (0 none, 1 perfectly)", "ratio", V)
    add("trend_run_minutes", "minutes since the machine last started", "min", V)
    add("trend_shocks_10m", "sudden jolts in the last 10 minutes", "count", V)
    add("trend_since_shock_s", "seconds since the last sudden jolt", "s", V)
    add("trend_bangs_10m", "sudden loud sounds in the last 10 minutes", "count", S)
    add("trend_since_bang_s", "seconds since the last sudden loud sound", "s", S)
    for h in TREND_HORIZONS:
        add(f"trend_heat_{h}", f"hottest point above room, average over {h}"
            + (" of running time" if h in ("24h", "7d") else ""), "c", H)
    add("trend_heat_rise_per_min", "hottest point above room: rise per minute over the last 2 minutes", "c_per_min", H)
    add("trend_heat_rise_per_h", "hottest point above room: rise per hour over the last hour", "c_per_h", H)
    add("trend_heat_rise_per_day", "hottest point above room: rise per day over the last 7 days of running", "c_per_day", H)
    for h in ("10m", "1h", "24h"):
        add(f"trend_vib_{h}", f"vibration level, average over {h}" + (" of running time" if h == "24h" else ""), "log", V)
    for h in ("10m", "1h", "24h"):
        add(f"trend_snd_{h}", f"sound level, average over {h}" + (" of running time" if h == "24h" else ""), "db", S)
    add("trend_speed_1h", "running speed, average over 1 h", "hz", V)
    add("trend_speed_24h", "running speed, average over 24 h of running time", "hz", V)
    add("trend_room_1h", "room temperature, average over 1 h", "c", R)
    add("trend_room_rise_per_h", "room temperature: rise per hour over the last hour (cooling failure)", "c_per_h", R)
    add("trend_humidity_1h", "room humidity, average over 1 h", "pct", R)
    add("trend_scene_match", "how closely the thermal image matches its usual layout (-1 to 1; low if moved or blocked)",
        "ratio", H)
    for what, src, unit, n in (("cell", H, "c", 48), ("vib_band", V, "log", 20), ("snd_band", S, "log", 32)):
        label = {"cell": "image cell's hottest point", "vib_band": "vibration band", "snd_band": "sound band"}[what]
        for pair in ("1h_vs_24h", "24h_vs_7d"):
            older = "24 h" if pair == "1h_vs_24h" else "7 days"
            newer = "last hour" if pair == "1h_vs_24h" else "last 24 h"
            add(f"trend_{what}_rise_{pair}", f"biggest rise of any {label}: {newer} vs {older} of running", unit, src)
            add(f"trend_{what}_rise_{pair}_at", f"which {label} (0-{n - 1}) rose most, {newer} vs {older}", "index", src)
    for pair in ("1h_vs_24h", "24h_vs_7d"):
        older = "24 h" if pair == "1h_vs_24h" else "7 days"
        newer = "last hour" if pair == "1h_vs_24h" else "last 24 h"
        add(f"trend_cell_fall_{pair}", f"biggest drop of any image cell's average below the whole image's change: "
            f"{newer} vs {older} of running (fan stopped, blocked fins or tubes, heater failed)", "c", H)
        add(f"trend_cell_fall_{pair}_at", f"which image cell (0-47) dropped most, {newer} vs {older}", "index", H)
    # Weeks: from one average per hour of running, kept for 4 weeks.
    add("trend_heat_4w", "hottest point above room, average over the last 4 weeks of running time", "c", H)
    add("trend_heat_rise_per_week", "hottest point above room: rise per week over the last 4 weeks of running",
        "c_per_week", H)
    add("trend_vib_4w", "vibration level, average over the last 4 weeks of running time", "log", V)
    add("trend_snd_4w", "sound level, average over the last 4 weeks of running time", "db", S)
    add("trend_speed_4w", "running speed, average over the last 4 weeks of running time", "hz", V)
    for what, src, unit, n in (("cell", H, "c", 48), ("vib_band", V, "log", 20), ("snd_band", S, "log", 32)):
        label = {"cell": "image cell's hottest point", "vib_band": "vibration band", "snd_band": "sound band"}[what]
        add(f"trend_{what}_rise_7d_vs_4w", f"biggest rise of any {label}: last 7 days vs 4 weeks of running", unit, src)
        add(f"trend_{what}_rise_7d_vs_4w_at", f"which {label} (0-{n - 1}) rose most, last 7 days vs 4 weeks", "index",
            src)
    add("trend_cell_fall_7d_vs_4w", "biggest drop of any image cell's average below the whole image's change: "
        "last 7 days vs 4 weeks of running (fan stopped, blocked fins or tubes, heater failed)", "c", H)
    add("trend_cell_fall_7d_vs_4w_at", "which image cell (0-47) dropped most, last 7 days vs 4 weeks", "index", H)
    add("trend_running", "1 while the machine counts as running, 0 while stopped (judged each minute from "
        "vibration; a machine never seen stopping counts as running)", "ratio", V)
    return names, desc, kind, sensor, n_base


FEATURE_NAMES, FEATURE_TEXT, FEATURE_KIND, FEATURE_SENSOR, N_BASE = _build_names()
N_FEATURES = len(FEATURE_NAMES)
INDEX = {n: i for i, n in enumerate(FEATURE_NAMES)}
BASE_SLICE = slice(0, N_BASE)
HISTORY_SLICE = slice(N_BASE, N_FEATURES)
HISTORY_NAMES = FEATURE_NAMES[N_BASE:]


def sensor_of(name: str) -> str:
    return FEATURE_SENSOR[name]


def _slice(prefix: str) -> slice:
    idx = [i for i, n in enumerate(FEATURE_NAMES[:N_BASE]) if n.startswith(prefix + "_")]
    return slice(idx[0], idx[-1] + 1)


# Per-window features of each sensor (history and clock features are not in these).
SLICES = {s: _slice(p) for p, s in (("vib", "vibration"), ("gyr", "rotation"), ("snd", "sound"),
                                    ("th", "heat"), ("env", "room"))}


# ----------------------------------------------------------------- helpers

def band_powers(power: np.ndarray, f: np.ndarray, edges: np.ndarray) -> list[float]:
    """log10 power per band. A band too narrow to hold a frequency bin takes the nearest bin."""
    out = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        sel = (f >= lo) & (f < hi)
        p = power[sel].sum() if sel.any() else power[int(np.argmin(np.abs(f - math.sqrt(lo * hi))))]
        out.append(math.log10(p + LOG_EPS ** 2))
    return out


def strongest_peaks(level: np.ndarray, f: np.ndarray, lo: float, hi: float, n: int = 2,
                    guard_bins: int = 2) -> list[tuple[float, float]]:
    """Up to n clear peaks as (frequency, level), strongest first. A missing peak is (0, median)."""
    sel = np.nonzero((f >= lo) & (f <= hi))[0]
    if len(sel) < 4:
        return [(0.0, 0.0)] * n
    med = float(np.median(level[sel])) + 1e-12
    work = level.copy()
    out = []
    for _ in range(n):
        k = sel[np.argmax(work[sel])]
        if work[k] <= MIN_PROMINENCE * med:
            out.append((0.0, med))
            continue
        freq = float(f[k])
        if 0 < k < len(level) - 1:  # refine between bins
            y0, y1, y2 = np.log(level[k - 1:k + 2] + 1e-12)
            den = y0 - 2 * y1 + y2
            if den != 0:
                freq = float((k + np.clip(0.5 * (y0 - y2) / den, -0.5, 0.5)) * (f[1] - f[0]))
        out.append((freq, float(level[k])))
        work[max(0, k - guard_bins):k + guard_bins + 1] = 0.0
    return out


def envelope_peak(x: np.ndarray, rate: float, band: tuple[float, float], search: tuple[float, float],
                  axis: int = 0) -> tuple[float, float]:
    """Envelope analysis: band-pass, take the envelope, and find its strongest repeating rate.

    Returns (frequency, prominence as a ratio over the median). A bearing or gear
    defect shows up as a carrier band whose loudness pulses at the defect rate.
    """
    n = x.shape[axis]
    X = np.fft.fft(x, axis=axis)
    f = np.fft.fftfreq(n, 1.0 / rate)
    keep = (f >= band[0]) & (f <= band[1])  # positive band only: the inverse is the analytic signal
    shape = [1] * x.ndim
    shape[axis] = n
    env = np.abs(np.fft.ifft(X * (2.0 * keep).reshape(shape), axis=axis))
    if env.ndim > 1:
        env = env.sum(axis=1 - axis)
    env = env - env.mean()
    spec = np.abs(np.fft.rfft(env * np.hanning(len(env))))
    fe = np.fft.rfftfreq(len(env), 1.0 / rate)
    sel = (fe >= search[0]) & (fe <= search[1])
    if sel.sum() < 4:
        return 0.0, 1.0
    k = int(np.argmax(np.where(sel, spec, 0)))
    return float(fe[k]), float(spec[k] / (np.median(spec[sel]) + 1e-12))


# ----------------------------------------------------------------- vibration

def _vibration(a_g: np.ndarray, rate: float, range_g: float) -> list[float]:
    n = len(a_g)
    gravity = a_g.mean(0)
    dyn = a_g - gravity
    rms = dyn.std(0)
    w = np.hanning(n)
    amp = np.abs(np.fft.rfft(dyn * w[:, None], axis=0)) * (2.0 / w.sum())  # peak g per axis
    power = (amp ** 2).sum(1)
    total = np.sqrt(power)
    f = np.fft.rfftfreq(n, 1.0 / rate)
    df = f[1] - f[0]
    bands = band_powers(power, f, VIB_EDGES_HZ)

    (speed, _), = strongest_peaks(total, f, *SPEED_SEARCH_HZ, n=1)
    sel = (f >= SPEED_SEARCH_HZ[0]) & (f <= SPEED_SEARCH_HZ[1])
    floor = float(np.median(total[sel])) if sel.any() else 0.0

    def order(mult):
        if speed <= 0:
            return floor
        target = mult * speed
        lo, hi = np.searchsorted(f, [target - 1.5 * df, target + 1.5 * df])
        return float(total[lo:hi + 1].max()) if hi > lo else floor

    one, two, three, half = order(1), order(2), order(3), order(0.5)
    harm = math.sqrt(sum(order(m) ** 2 for m in range(3, 11)))
    p2_hz, p2 = strongest_peaks(total, f, *PEAK2_SEARCH_HZ, n=2)[1]
    mag = np.sqrt((dyn ** 2).sum(1))
    mag_rms = math.sqrt(float((mag ** 2).mean())) + 1e-12
    crest = float(mag.max() / mag_rms)
    centred = mag - mag.mean()
    kurt = float((centred ** 4).mean() / ((centred ** 2).mean() ** 2 + 1e-24) - 3.0)
    peak = float(mag.max())
    axis = int(np.argmax(np.abs(dyn).max(0)))
    jerk = float(np.abs(np.diff(a_g, axis=0)).max() * rate) if n > 1 else 0.0
    env_hz, env_prom = envelope_peak(dyn, rate, VIB_ENV_BAND_HZ, ENV_SEARCH_HZ)
    env_order = env_hz / speed if speed > 0 else 0.0
    vb = (f >= 10) & (f <= 250)  # velocity = acceleration / (2 pi f), rms over 10-250 Hz
    vel = math.sqrt(float(((amp[vb] * 9.81 / (2 * np.pi * f[vb, None])) ** 2).sum() / 2)) * 1000
    line = 0.0
    for lf in (100.0, 120.0):
        lo, hi = np.searchsorted(f, [lf - 1.5 * df, lf + 1.5 * df])
        line = max(line, float(total[lo:hi + 1].max()) if hi > lo else 0.0)
    clip = float((np.abs(a_g) >= CLIP_FRACTION * range_g).any(1).mean())
    freefall = float((np.sqrt((a_g ** 2).sum(1)) < 0.3).mean())

    lg = lambda v: math.log10(v + LOG_EPS)
    return ([lg(v) for v in rms] + bands
            + [speed, lg(one), lg(two), lg(three), lg(harm), lg(half), p2_hz, lg(p2), crest, kurt,
               lg(peak), lg(jerk), float(axis), env_hz, math.log10(env_prom), env_order, vel, lg(line),
               clip, freefall]
            + [float(v) for v in gravity])


def _gyro(g: np.ndarray) -> list[float]:
    dyn = g - g.mean(0)
    mag = np.sqrt((dyn ** 2).sum(1))
    return [math.log10(math.sqrt(float((mag ** 2).mean())) + LOG_EPS), math.log10(float(mag.max()) + LOG_EPS)]


# ----------------------------------------------------------------- sound

def _sound(x: np.ndarray, rate: float, gain_db: float, full_scale: float = 32767.0) -> list[float]:
    raw = np.asarray(x)
    x = raw.astype(np.float64)
    x = x - x.mean()
    rms = math.sqrt(float((x ** 2).mean())) + 1e-9
    full_rms = full_scale / math.sqrt(2)  # a full-scale sine: the same dB for any bit depth
    to_db = lambda r: 20 * math.log10(r / full_rms + 1e-12) + 120.0 - gain_db
    level = to_db(rms)
    n = len(x)
    w = np.hanning(n)
    Xw = np.fft.rfft(x * w)
    p = np.abs(Xw) ** 2
    f = np.fft.rfftfreq(n, 1.0 / rate)
    bands = [math.log10(10 ** b - LOG_EPS ** 2 + 1.0) for b in band_powers(p, f, SND_EDGES_HZ)]
    sel = (f >= SND_EDGES_HZ[0]) & (f < SND_EDGES_HZ[-1])
    centroid = float((f[sel] * p[sel]).sum() / (p[sel].sum() + 1e-12))
    frame = max(1, int(rate * 0.01))
    m = (n // frame) * frame
    e = (x[:m].reshape(-1, frame) ** 2).mean(1)
    click = math.log10((e.max() + 1.0) / (np.median(e) + 1.0))
    peak_db = to_db(math.sqrt(float(e.max())) + 1e-9)
    ts = (f >= TONE_SEARCH_HZ[0]) & (f <= TONE_SEARCH_HZ[1])
    pt, ft = p[ts], f[ts]
    k = int(np.argmax(pt))
    tone_hz = float(ft[k])
    tone_db = float(10 * math.log10((pt[k] + 1.0) / (np.median(pt) + 1.0)))
    flatness = float(np.exp(np.mean(np.log(pt + 1e-12))) / (pt.mean() + 1e-12))

    # Mains hum: 100/150/200 Hz (50 Hz grid) or 120/180/240 Hz (60 Hz grid), whichever is stronger.
    df = f[1] - f[0]
    near = p[(f >= 80) & (f <= 300)]
    ref = float(np.median(near)) + 1.0 if len(near) else 1.0
    hums = []
    for base in (50.0, 60.0):
        tot = 0.0
        for mult in (2, 3, 4):
            lo, hi = np.searchsorted(f, [base * mult - 2 * df, base * mult + 2 * df])
            tot += float(p[lo:hi + 1].max()) if hi > lo else 0.0
        hums.append(10 * math.log10(tot / 3 / ref + 1e-12))
    mains_db = max(hums)

    # Beeping: how much the strongest tone's loudness changes over 50 ms frames.
    fl = max(64, int(rate * 0.05))
    nf = n // fl
    if nf >= 4:
        frames = x[:nf * fl].reshape(nf, fl) * np.hanning(fl)[None]
        fp = np.abs(np.fft.rfft(frames, axis=1)) ** 2
        ff = np.fft.rfftfreq(fl, 1.0 / rate)
        kb = int(np.argmin(np.abs(ff - tone_hz)))
        tone_track = fp[:, max(0, kb - 1):kb + 2].sum(1) + 1.0
        tone_mod = float(min(40.0, 10 * math.log10(tone_track.max() / tone_track.min())))
    else:
        tone_mod = 0.0
    # Envelope of the 2-7 kHz band, cheaply: band-pass the (unwindowed) spectrum, rectify,
    # and average down to 1 kHz. Its spectrum shows the rate the band pulses at. Unwindowed,
    # because a window's own rise and fall would show up as a slow pulse.
    band = np.fft.rfft(x) * ((f >= SND_ENV_BAND_HZ[0]) & (f <= SND_ENV_BAND_HZ[1]))
    sig = np.fft.irfft(band, n)
    s2 = float((sig ** 2).mean()) + 1e-24
    kurt_hf = float((sig ** 4).mean() / s2 ** 2 - 3.0)
    xb = np.abs(sig)
    step = max(1, int(round(rate / 1000)))
    env = xb[:(n // step) * step].reshape(-1, step).mean(1)
    env = env - env.mean()
    es = np.abs(np.fft.rfft(env * np.hanning(len(env))))
    fe = np.fft.rfftfreq(len(env), step / rate)
    es_sel = (fe >= ENV_SEARCH_HZ[0]) & (fe <= ENV_SEARCH_HZ[1])
    if es_sel.sum() >= 4:
        ke = int(np.argmax(np.where(es_sel, es, 0)))
        env_hz, env_prom = float(fe[ke]), float(es[ke] / (np.median(es[es_sel]) + 1e-12))
    else:
        env_hz, env_prom = 0.0, 1.0
    clip = float((np.abs(raw.astype(np.float64)) >= CLIP_FRACTION * full_scale).mean())
    # snd_env_order needs the running speed from vibration; extract() fills it in.
    return [level] + bands + [centroid, click, tone_hz, tone_db, flatness, peak_db, mains_db, tone_mod,
                              env_hz, 10 * math.log10(env_prom), np.nan, kurt_hf, clip]


# ----------------------------------------------------------------- heat

def _heat(frames: np.ndarray) -> list[float]:
    img = frames.mean(0)
    bg = float(np.median(img))
    rows, cols = TH_GRID
    h, w = img.shape
    r, c = np.unravel_index(int(np.argmax(img)), img.shape)
    hot = int((img > bg + TH_HOT_ABOVE_C).sum())
    cold = float(bg - img.min())
    change = float(np.abs(frames[-1] - frames[0]).max()) if len(frames) > 1 else 0.0
    frozen = float(len(frames) > 1 and bool(np.all(frames == frames[0])))
    blocks = (img - bg).reshape(rows, h // rows, cols, w // cols)
    cells, spots = blocks.mean((1, 3)), blocks.max((1, 3))
    return ([float(img.max()), float(img.mean()), bg, float(img.max() - bg), float(r), float(c), float(hot),
             cold, change, frozen, float(img.std())] + [float(v) for v in cells.ravel()]
            + [float(v) for v in spots.ravel()])


def dew_point(temp_c: float, rh_pct: float) -> float:
    """Magnus formula, good to about 0.4 degC between -40 and 50 degC."""
    a, b = 17.62, 243.12
    g = math.log(max(rh_pct, 0.1) / 100.0) + a * temp_c / (b + temp_c)
    return b * g / (a - g)


MIN_ACCEL_SAMPLES = 64
MIN_AUDIO_SAMPLES = 1024


def extract(win: Window) -> np.ndarray:
    """Per-window features in FEATURE_NAMES order. Missing sensors and history give NaN."""
    out = np.full(N_FEATURES, np.nan)
    if win.accel_g is not None and len(win.accel_g) >= MIN_ACCEL_SAMPLES:
        out[SLICES["vibration"]] = _vibration(np.asarray(win.accel_g, float), win.accel_rate_hz, win.accel_range_g)
    if win.gyro_dps is not None and len(win.gyro_dps) >= MIN_ACCEL_SAMPLES:
        out[SLICES["rotation"]] = _gyro(np.asarray(win.gyro_dps, float))
    if win.audio is not None and len(win.audio) >= MIN_AUDIO_SAMPLES:
        out[SLICES["sound"]] = _sound(win.audio, win.audio_rate_hz, win.audio_gain_db, win.audio_full_scale)
    if win.thermal_c is not None and len(win.thermal_c) > 0:
        out[SLICES["heat"]] = _heat(np.asarray(win.thermal_c, float))
    if not np.isnan(out[INDEX["snd_env_peak_hz"]]) and not np.isnan(out[INDEX["vib_speed_hz"]]):
        speed = out[INDEX["vib_speed_hz"]]
        out[INDEX["snd_env_order"]] = out[INDEX["snd_env_peak_hz"]] / speed if speed > 0 else 0.0
    if win.room is not None and len(win.room) > 0:
        room = np.asarray(win.room, float).mean(0)
        out[INDEX["env_room_c"]] = room[0]
        out[INDEX["env_humidity_pct"]] = room[1]
        out[INDEX["env_hot_above_room_c"]] = out[INDEX["th_max_c"]] - room[0]  # NaN if no thermal
        dp = dew_point(room[0], room[1])
        out[INDEX["env_dew_point_c"]] = dp
        coldest = out[INDEX["th_background_c"]] - out[INDEX["th_cold_spot_c"]]
        out[INDEX["env_condensation_margin_c"]] = coldest - dp  # NaN if no thermal
    if win.week_t is not None:
        hour = (win.week_t % 86400) / 3600
        out[INDEX["time_hour_sin"]] = math.sin(2 * math.pi * hour / 24)
        out[INDEX["time_hour_cos"]] = math.cos(2 * math.pi * hour / 24)
        day = (win.week_t / 86400.0) % 7.0
        out[INDEX["time_weekday_sin"]] = math.sin(2 * math.pi * day / 7)
        out[INDEX["time_weekday_cos"]] = math.cos(2 * math.pi * day / 7)
    return out


def missing_sensors(x: np.ndarray) -> list[str]:
    return [s for s, sl in SLICES.items() if np.isnan(x[sl]).all()]
