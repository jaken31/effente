"""History features: what one window cannot show. Shared by training and the device.

One `History` per sensor stream. Feed it every window in time order through
`FeatureEngine.update(window)`, which returns the full feature row (per-window
features from effente.features plus these). It keeps:

  raw vibration, last 8.2 s           -> slow vibration bands and peaks (0.4-25 Hz:
                                         pulleys, idler rollers, gearbox shafts)
  one value per second, last 10 min   -> 1 and 10 minute averages, heat rise per
                                         minute (fast escalation), repeating knocks,
                                         sudden jolts and bangs, when the machine started
  one average per minute, last 7 days -> 1 h, 6 h, 24 h and 7 day averages, rise per
                                         hour and per day, running speed and room
                                         trends, which image cell or band is rising
                                         against its own past, which image area went
                                         cold (about 9 MB)
  one average per running hour, 4 weeks -> 4 week averages, rise per week, and which
                                         cell or band changed most over the last 7 days
                                         against 4 weeks (about 1 MB)

Sudden events: a jolt is a second whose peak acceleration is 4x the median of
the last 10 minutes (and at least 0.05 g); a bang is a second whose loudest
10 ms is 12 dB above the 10-minute median. Both are relative, so they adapt
to any machine.

Day and week averages count only time when the machine was running, so a long
stop does not look like cooling. Running versus stopped is decided afresh each
minute from everything seen so far: once the machine has been seen both on and
off, "running" means vibration within x2 of the usual running level. A machine
only ever seen in one state (always on, like a server) counts as running.

Each second's value is taken from the window that starts on that second, so a
stream with 4 windows a second (live) and one with 1 a second (recording)
give exactly the same history features.

Every feature stays NaN until enough history exists: 1 h needs 48 minutes,
24 h needs 20 hours, 7 days needs 2 days, 4 weeks needs 2 weeks. Save the state with `save()` and
reload it with `History.load()` so days-long features survive a restart.
Thresholds are starting values, unverified on real machines.
"""

from __future__ import annotations

import hashlib
import math
import warnings
from collections import deque
from pathlib import Path

import numpy as np

from effente import features as F

SLOW_WINDOW_S = 8.192
KNOCK_SPAN_S = 300
KNOCK_LAGS_S = (5, 120)
START_JUMP = 0.3  # log10 vibration: a x2 jump within a minute counts as a start (a drop as a stop)
RUNNING_MARGIN = 0.3  # running = within x2 of the usual running level
SHOCK_RATIO_LOG = math.log10(4.0)  # a jolt: peak 4x the 10-minute median ...
SHOCK_MIN_LOG = math.log10(0.05)  # ... and at least 0.05 g
SHOCK_OVER_THIRD_LOG = math.log10(2.0)  # ... and 2x the 3rd biggest of the last 10 minutes
BANG_DB = 12.0  # a bang: loudest 10 ms 12 dB above the 10-minute median ...
BANG_OVER_THIRD_DB = 6.0  # ... 6 dB above the 3rd loudest of the last 10 minutes ...
BANG_CREST_DB = 9.0  # ... and 9 dB above its own second's level (normal running stays under 8)
# The "3rd biggest" rule: something that keeps happening (a knock every belt lap) stops being
# sudden after a few times; the knock features describe it from then on. The level rule keeps a
# machine starting up from counting as a bang.
RUN_REF_S = 60  # once the machine has run this long, bangs must also beat its usual running peaks
# After a quiet night the last 10 minutes are silence, so the first noises of a start would count
# as bangs; the last 10 minutes of running are kept for that (every second until a stop is seen,
# saved across restarts). A device installed on a stopped machine has no reference at its first start.
WEEK_MINUTES = 7 * 24 * 60
MONTH_HOURS = 28 * 24  # one average per hour of running, for 4 weeks
MONTH_SPAN_H = 14 * 24  # 4 week figures need 2 weeks of history ...
MONTH_RUN_MIN = 3 * 1440  # ... holding at least 3 days of running
RESUME_GAP_S = 120  # after a restart, the saved per-minute results are reused at once if no more was lost
STATE_VERSION = 7

_CELLS = [F.INDEX[f"th_cell_{r}{c}"] for r in range(F.TH_GRID[0]) for c in range(F.TH_GRID[1])]
_SPOTS = [F.INDEX[f"th_spot_{r}{c}"] for r in range(F.TH_GRID[0]) for c in range(F.TH_GRID[1])]
_VBANDS = [F.INDEX[f"vib_band_{i:02d}"] for i in range(len(F.VIB_EDGES_HZ) - 1)]
_SBANDS = [F.INDEX[f"snd_band_{i:02d}"] for i in range(len(F.SND_EDGES_HZ) - 1)]
_VRMS = [F.INDEX[f"vib_rms_{a}"] for a in "xyz"]
# Layout of the per-minute vector.
_HEAT, _VIB, _SND, _SPEED, _ROOM, _HUMID = range(6)
_C0 = 6
_V0 = _C0 + len(_CELLS)
_S0 = _V0 + len(_VBANDS)
_P0 = _S0 + len(_SBANDS)
_M = _P0 + len(_SPOTS)
H = {n: i for i, n in enumerate(F.HISTORY_NAMES)}
# Per-second record: second, heat, vibration, sound, kurtosis, clicks, peak vibration, peak sound.
_SEC = dict(t=0, heat=1, vib=2, snd=3, kurt=4, click=5, vpeak=6, speak=7)
_SEC_W = len(_SEC)


def _nanmean(a, axis=0):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return np.nanmean(a, axis=axis)


def _nanmedian(a):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return float(np.nanmedian(a)) if len(a) else float("nan")


def _version() -> str:
    return hashlib.sha1("|".join(F.FEATURE_NAMES).encode()).hexdigest()[:12]


def _vib_level(x: np.ndarray) -> float:
    rms = x[_VRMS]
    return float("nan") if np.isnan(rms).any() else math.log10(math.sqrt(float((10 ** (2 * rms)).sum())))


def _vector(x: np.ndarray) -> np.ndarray:
    v = np.full(_M, np.nan)
    v[_HEAT] = x[F.INDEX["env_hot_above_room_c"]]
    v[_VIB] = _vib_level(x)
    v[_SND] = x[F.INDEX["snd_level_db"]]
    v[_SPEED] = x[F.INDEX["vib_speed_hz"]]
    v[_ROOM] = x[F.INDEX["env_room_c"]]
    v[_HUMID] = x[F.INDEX["env_humidity_pct"]]
    v[_C0:_V0] = x[_CELLS]
    v[_V0:_S0] = x[_VBANDS]
    v[_S0:_P0] = x[_SBANDS]
    v[_P0:_M] = x[_SPOTS]
    return v


def _third_biggest(v: np.ndarray) -> float:
    v = v[~np.isnan(v)]
    return float(np.partition(v, -3)[-3]) if len(v) >= 3 else float("nan")


def _slope(t: np.ndarray, y: np.ndarray, need: int = 3) -> float:
    ok = ~np.isnan(y)
    if ok.sum() < need:
        return float("nan")
    return float(np.polyfit(t[ok], y[ok], 1)[0])


def running_mask(vib: np.ndarray) -> np.ndarray:
    """Which minutes count as running, judged from all the minutes given."""
    ok = ~np.isnan(vib)
    if not both_states_seen(vib):
        return ok  # only one state seen: count it as running
    return ok & (vib >= np.percentile(vib[ok], 90) - RUNNING_MARGIN)


def both_states_seen(vib: np.ndarray) -> bool:
    ok = ~np.isnan(vib)
    if ok.sum() < 10:
        return False
    p10, p90 = np.percentile(vib[ok], [10, 90])
    return bool(p90 - p10 >= RUNNING_MARGIN)


class History:
    def __init__(self):
        self.acc_t = np.empty(0)
        self.acc = np.empty((0, 3))
        self.cur_sec: int | None = None
        self.cur_rec: np.ndarray | None = None
        self.cur_vec: np.ndarray | None = None
        self.secs: deque = deque(maxlen=600)
        self.shocks: deque = deque(maxlen=10_000)
        self.bangs: deque = deque(maxlen=10_000)
        self.run_peaks: deque = deque(maxlen=600)  # loudest instant of each of the last 600 running seconds
        self.cur_min: int | None = None
        self.min_acc: list[np.ndarray] = []
        self.min_t = np.full(WEEK_MINUTES, np.nan)  # one row per minute for 7 days, a ring buffer
        self.min_v = np.full((WEEK_MINUTES, _M), np.nan, np.float32)  # float32 as saved, so a restart changes nothing
        self.min_i = 0
        self.cur_hour: int | None = None
        self.hour_t = np.full(MONTH_HOURS, np.nan)  # one row per hour for 4 weeks, a ring buffer
        self.hour_v = np.full((MONTH_HOURS, _M), np.nan, np.float32)  # average of the hour's running minutes
        self.hour_n = np.zeros(MONTH_HOURS, np.float32)  # how many running minutes that hour had
        self.hour_sure = np.zeros(MONTH_HOURS, bool)  # running vs stopped was known when it closed
        self.hour_i = 0
        self.last_start: int | None = None
        self.stopped = False  # True from a detected stop until the next start
        self.out = np.full(len(F.HISTORY_NAMES), np.nan)
        self.reset_reason: str | None = None
        self.loaded = False  # True from load() until the first window after it

    # ------------------------------------------------------------ input
    def update(self, win: F.Window, x: np.ndarray) -> np.ndarray:
        if win.accel_g is not None and win.accel_t is not None and len(win.accel_t):
            new = win.accel_t > (self.acc_t[-1] if len(self.acc_t) else -math.inf)
            if new.any():
                self.acc_t = np.concatenate([self.acc_t, win.accel_t[new]])
                self.acc = np.concatenate([self.acc, np.asarray(win.accel_g)[new]])
                keep = self.acc_t >= self.acc_t[-1] - SLOW_WINDOW_S - 0.2
                self.acc_t, self.acc = self.acc_t[keep], self.acc[keep]
        sec = math.floor(win.t0 + 1e-9)
        last = self.cur_sec if self.cur_sec is not None else (int(self.secs[-1][0]) if self.secs else None)
        if last is not None and sec < last - 60:
            self.__init__()
            self.reset_reason = "clock went backwards; history restarted"
        if self.loaded:
            self.loaded = False
            # Straight after a restart: carry on from the saved history if little time was lost,
            # so the first rows match an uninterrupted run. After a longer gap, start empty.
            if self.cur_sec is not None and 0 <= sec - self.cur_sec <= RESUME_GAP_S:
                self._per_minute_features()  # per-second results follow when the saved second closes
        if self.cur_sec is None or sec > self.cur_sec:
            if self.cur_sec is not None:
                minute_closed = self._close_second()
                self._per_second_features(self.cur_sec)
                if minute_closed:
                    self._per_minute_features()
            self.cur_sec, self.cur_vec = sec, _vector(x)
            self.cur_rec = np.array([sec, self.cur_vec[_HEAT], self.cur_vec[_VIB], self.cur_vec[_SND],
                                     x[F.INDEX["vib_kurtosis"]], x[F.INDEX["snd_click"]],
                                     x[F.INDEX["vib_peak_g"]], x[F.INDEX["snd_peak_db"]]])
        return self.out.copy()

    def _close_second(self) -> bool:
        s, v, rec = self.cur_sec, self.cur_vec, self.cur_rec
        self._sudden(s, rec)
        self.secs.append(tuple(rec))
        self._detect_start(s)
        if not self.stopped and not np.isnan(rec[_SEC["speak"]]):  # running, or never seen stopping
            self.run_peaks.append(float(rec[_SEC["speak"]]))
        m = s // 60
        closed = False
        if self.cur_min is not None and m != self.cur_min:
            self._close_minute()
            closed = True
        self.cur_min = m
        self.min_acc.append(v)
        return closed

    def _sudden(self, s: int, rec: np.ndarray):
        """Jolts and bangs, judged against the last 10 minutes of this machine."""
        if len(self.secs) < 60:
            return
        recent = np.array(self.secs, float)
        vp, sp, level = rec[_SEC["vpeak"]], rec[_SEC["speak"]], rec[_SEC["snd"]]
        if not np.isnan(vp):
            col = recent[:, _SEC["vpeak"]]
            if (vp - _nanmedian(col) > SHOCK_RATIO_LOG and vp > SHOCK_MIN_LOG
                    and not vp - _third_biggest(col) <= SHOCK_OVER_THIRD_LOG):
                self.shocks.append(s)
        if not np.isnan(sp):
            col = recent[:, _SEC["speak"]]
            usual, third = _nanmedian(col), _third_biggest(col)
            if len(self.run_peaks) >= RUN_REF_S:  # and louder than it usually is while running
                run = np.array(self.run_peaks, float)
                usual, third = float(np.fmax(usual, _nanmedian(run))), float(np.fmax(third, _third_biggest(run)))
            if (sp - usual > BANG_DB and not sp - third <= BANG_OVER_THIRD_DB
                    and not sp - level < BANG_CREST_DB):
                self.bangs.append(s)

    def _close_minute(self):
        if not self.min_acc:
            return
        self.min_t[self.min_i] = self.cur_min
        self.min_v[self.min_i] = _nanmean(np.array(self.min_acc))
        self.min_i = (self.min_i + 1) % WEEK_MINUTES
        self.min_acc = []
        hour = self.cur_min // 60
        if self.cur_hour is not None and hour != self.cur_hour:
            self._close_hour(self.cur_hour)
        self.cur_hour = hour

    def _close_hour(self, hour: int):
        """Average the hour's running minutes into the 4-week ring."""
        have = ~np.isnan(self.min_t)
        t, V = self.min_t[have], self.min_v[have].astype(float)
        sel = (t >= hour * 60) & (t < hour * 60 + 60)
        if not sel.any():
            return
        run = running_mask(V[:, _VIB]) & sel
        self.hour_t[self.hour_i] = hour
        self.hour_n[self.hour_i] = run.sum()
        self.hour_sure[self.hour_i] = both_states_seen(V[:, _VIB])
        self.hour_v[self.hour_i] = _nanmean(V[run]) if run.any() else np.nan
        self.hour_i = (self.hour_i + 1) % MONTH_HOURS

    def _month(self, now_min: float):
        """Average over the last 4 weeks of running time, from the hourly ring, and which hours it used.

        An hour closed before the machine had been seen both running and stopped counted all its
        minutes as running; once other hours are sure, it counts only if its vibration is within x2 of
        theirs. A machine never seen stopping (a server) keeps all its hours."""
        ht, now_h = self.hour_t, now_min / 60.0
        have = ~np.isnan(ht) & (ht > now_h - MONTH_HOURS)
        if not have.any() or now_h - float(ht[have].min()) < MONTH_SPAN_H:
            return None, None
        ok = have & (self.hour_n > 0)
        sure = ok & self.hour_sure
        if sure.any():
            vib = self.hour_v[:, _VIB].astype(float)
            level = float(np.percentile(vib[sure], 90))
            with np.errstate(invalid="ignore"):
                ok = sure | (ok & ~self.hour_sure & (vib >= level - RUNNING_MARGIN))
        if self.hour_n[ok].sum() < MONTH_RUN_MIN:
            return None, None
        V, n = self.hour_v[ok].astype(float), self.hour_n[ok].astype(float)[:, None]
        good = ~np.isnan(V)
        w = np.where(good, n, 0.0)
        tot = w.sum(0)
        return np.where(tot > 0, (np.where(good, V, 0.0) * w).sum(0) / np.where(tot > 0, tot, 1.0), np.nan), ok

    def _detect_start(self, s: int):
        if len(self.secs) < 70:
            return
        vib = np.array([e[_SEC["vib"]] for e in list(self.secs)[-70:]])
        recent, before = _nanmean(vib[-10:]), _nanmean(vib[:-10])
        if np.isnan(recent) or np.isnan(before):
            return
        if recent - before > START_JUMP and (self.last_start is None or s - self.last_start > 120):
            self.last_start = s - 10
            self.stopped = False
        elif before - recent > START_JUMP:
            self.last_start = None
            self.stopped = True

    # ------------------------------------------------------------ per second
    def _per_second_features(self, now: int):
        o = self.out
        o[H["trend_run_minutes"]] = (now - self.last_start) / 60.0 if self.last_start is not None else np.nan
        secs = np.array(self.secs, float).reshape(-1, _SEC_W)
        t = secs[:, 0]
        for key, name, span in (("heat", "trend_heat_1m", 60), ("heat", "trend_heat_10m", 600),
                                ("vib", "trend_vib_10m", 600), ("snd", "trend_snd_10m", 600)):
            sel = secs[t > now - span, _SEC[key]]
            o[H[name]] = float(np.nanmean(sel)) if np.count_nonzero(~np.isnan(sel)) >= 0.8 * span else np.nan
        last2 = t > now - 120
        o[H["trend_heat_rise_per_min"]] = _slope(t[last2], secs[last2, _SEC["heat"]], need=96) * 60
        for events, count, since in ((self.shocks, "trend_shocks_10m", "trend_since_shock_s"),
                                     (self.bangs, "trend_bangs_10m", "trend_since_bang_s")):
            o[H[count]] = float(sum(1 for e in events if e > now - 600)) if len(self.secs) >= 60 else np.nan
            o[H[since]] = float(now - events[-1]) if events else np.nan
        self._knocks(secs, now)
        self._slow()

    @staticmethod
    def _repeat(series):
        """(period in samples, how regular 0-1) of the strongest repeat in a 1 per second series."""
        if np.count_nonzero(~np.isnan(series)) < 0.8 * KNOCK_SPAN_S:
            return None
        c = np.where(np.isnan(series), np.nanmean(series), series)
        c = c - c.mean()
        n = len(c)
        r = np.fft.irfft(np.abs(np.fft.rfft(c, 2 * n)) ** 2)[:n]
        if r[0] <= 0:
            return None
        r = r / r[0]
        lo, hi = KNOCK_LAGS_S[0], min(KNOCK_LAGS_S[1], n // 2)
        lag = lo + int(np.argmax(r[lo:hi + 1]))
        return lag, float(np.clip(r[lag], 0.0, 1.0))

    def _knocks(self, secs, now):
        """Repeating knocks, from vibration impulsiveness and sound clicks; the clearer one wins."""
        o = self.out
        recent = secs[secs[:, 0] > now - KNOCK_SPAN_S]
        found = [r for r in (self._repeat(recent[:, _SEC["kurt"]]), self._repeat(recent[:, _SEC["click"]]))
                 if r is not None]
        if not found:
            o[H["knock_period_s"]] = o[H["knock_repeat"]] = np.nan
            return
        lag, rep = max(found, key=lambda r: r[1])
        o[H["knock_period_s"]], o[H["knock_repeat"]] = float(lag), rep

    def _slow(self):
        o = self.out
        names = [f"slow_band_{i:02d}" for i in range(len(F.SLOW_EDGES_HZ) - 1)]
        names += ["slow_peak1_hz", "slow_peak1", "slow_peak2_hz", "slow_peak2"]
        if len(self.acc_t) < 2 or self.acc_t[-1] - self.acc_t[0] < SLOW_WINDOW_S - 0.2:
            for n in names:
                o[H[n]] = np.nan
            return
        sel = self.acc_t >= self.acc_t[-1] - SLOW_WINDOW_S
        t, a = self.acc_t[sel], self.acc[sel]
        rate = (len(t) - 1) / (t[-1] - t[0])
        dyn = a - a.mean(0)
        w = np.hanning(len(dyn))
        amp = np.abs(np.fft.rfft(dyn * w[:, None], axis=0)) * (2.0 / w.sum())
        power = (amp ** 2).sum(1)
        f = np.fft.rfftfreq(len(dyn), 1.0 / rate)
        for n, v in zip(names, F.band_powers(power, f, F.SLOW_EDGES_HZ)):
            o[H[n]] = v
        (f1, l1), (f2, l2) = F.strongest_peaks(np.sqrt(power), f, 0.4, 20.0, n=2, guard_bins=3)
        o[H["slow_peak1_hz"]], o[H["slow_peak1"]] = f1, math.log10(l1 + F.LOG_EPS)
        o[H["slow_peak2_hz"]], o[H["slow_peak2"]] = f2, math.log10(l2 + F.LOG_EPS)

    # ------------------------------------------------------------ per minute
    def _per_minute_features(self):
        o = self.out
        have = ~np.isnan(self.min_t)
        if not have.any():
            return
        t = self.min_t[have]
        V = self.min_v[have]
        now = float(np.nanmax(t))
        oldest = float(t.min())
        run = running_mask(V[:, _VIB])
        rows_ok = ~np.isnan(V[:, :_C0]).all(1)
        o[H["trend_running"]] = float(run[int(np.argmax(t))])

        def window(span, need, running_only=False, need_span=0.0):
            sel = (t > now - span) & rows_ok
            if running_only:
                sel &= run
            if sel.sum() < need or now - oldest < need_span:
                return None
            return _nanmean(V[sel])

        def put(name, vec, col):
            o[H[name]] = vec[col] if vec is not None else np.nan

        h1, h6 = window(60, 48), window(360, 288)
        put("trend_heat_1h", h1, _HEAT)
        put("trend_heat_6h", h6, _HEAT)
        put("trend_vib_1h", h1, _VIB)
        put("trend_snd_1h", h1, _SND)
        put("trend_speed_1h", h1, _SPEED)
        put("trend_room_1h", h1, _ROOM)
        put("trend_humidity_1h", h1, _HUMID)
        last60 = t > now - 60
        o[H["trend_heat_rise_per_h"]] = _slope(t[last60], V[last60, _HEAT], need=48) * 60
        o[H["trend_room_rise_per_h"]] = _slope(t[last60], V[last60, _ROOM], need=48) * 60

        d24 = window(1440, 60, running_only=True, need_span=0.83 * 1440)
        for name, col in (("trend_heat_24h", _HEAT), ("trend_vib_24h", _VIB), ("trend_snd_24h", _SND),
                          ("trend_speed_24h", _SPEED)):
            put(name, d24, col)
        r1 = window(60, 30, running_only=True)
        week = window(WEEK_MINUTES, 24 * 60, running_only=True, need_span=2 * 1440)
        # Which part is heating: each cell's hottest point, since a part is usually smaller than a cell.
        for what, sl in (("cell", slice(_P0, _M)), ("vib_band", slice(_V0, _S0)), ("snd_band", slice(_S0, _P0))):
            self._biggest_rise(f"trend_{what}_rise_1h_vs_24h", r1, d24, sl)
            self._biggest_rise(f"trend_{what}_rise_24h_vs_7d", d24, week, sl)
        # Which area went cold (fan stopped, blocked fins or tubes, heater failed): each cell's average,
        # since a cold area is usually broad, measured against the whole image's change so the machine
        # cooling as a whole (less load) does not count. In the server simulation this showed the
        # stopped fan at 14 spreads, against 4 without subtracting the whole image's change.
        self._biggest_rise("trend_cell_fall_1h_vs_24h", r1, d24, slice(_C0, _V0), fall=True)
        self._biggest_rise("trend_cell_fall_24h_vs_7d", d24, week, slice(_C0, _V0), fall=True)
        # Does the thermal image still look like it usually does? Low if the camera moved or is blocked.
        ref = d24 if d24 is not None else h6
        latest = V[int(np.argmax(t)), _C0:_V0]
        o[H["trend_scene_match"]] = np.nan
        if ref is not None and not np.isnan(latest).any() and not np.isnan(ref[_C0:_V0]).any():
            a, b = latest - latest.mean(), ref[_C0:_V0] - ref[_C0:_V0].mean()
            den = float(np.sqrt((a ** 2).sum() * (b ** 2).sum()))
            if den > 1e-6:
                o[H["trend_scene_match"]] = float((a * b).sum() / den)
        # Weeks: the last 7 days against the last 4 weeks of running time.
        month, used = self._month(now)
        for name, col in (("trend_heat_4w", _HEAT), ("trend_vib_4w", _VIB), ("trend_snd_4w", _SND),
                          ("trend_speed_4w", _SPEED)):
            put(name, month, col)
        o[H["trend_heat_rise_per_week"]] = np.nan
        if month is not None:
            sel = used & (self.hour_n >= 30)  # running hours, mostly running
            o[H["trend_heat_rise_per_week"]] = _slope(self.hour_t[sel] / (24 * 7),
                                                      self.hour_v[sel, _HEAT].astype(float), need=72)
        for what, sl in (("cell", slice(_P0, _M)), ("vib_band", slice(_V0, _S0)), ("snd_band", slice(_S0, _P0))):
            self._biggest_rise(f"trend_{what}_rise_7d_vs_4w", week, month, sl)
        self._biggest_rise("trend_cell_fall_7d_vs_4w", week, month, slice(_C0, _V0), fall=True)
        put("trend_heat_7d", week, _HEAT)
        if week is not None:
            sel = (t > now - WEEK_MINUTES) & run
            o[H["trend_heat_rise_per_day"]] = _slope(t[sel] / 1440.0, V[sel, _HEAT])
        else:
            o[H["trend_heat_rise_per_day"]] = np.nan

    def _biggest_rise(self, name, newer, older, sl, fall=False):
        o = self.out
        o[H[name]] = o[H[name + "_at"]] = np.nan
        if newer is None or older is None:
            return
        d = newer[sl] - older[sl]
        if np.isnan(d).all():
            return
        if fall:  # drop below the typical change of all of them
            d = np.nanmedian(d) - d
        k = int(np.nanargmax(d))
        o[H[name]], o[H[name + "_at"]] = float(d[k]), float(k)

    # ------------------------------------------------------------ saving
    def save(self, path) -> None:
        state = dict(
            state_version=STATE_VERSION, features_version=_version(),
            secs=np.array(list(self.secs), float).reshape(-1, _SEC_W),
            shocks=np.array(list(self.shocks), float), bangs=np.array(list(self.bangs), float),
            run_peaks=np.array(list(self.run_peaks), float), stopped=np.array(self.stopped),
            min_t=self.min_t, min_v=self.min_v.astype(np.float32), min_i=self.min_i,
            min_acc=np.array(self.min_acc, float).reshape(-1, _M),
            hour_t=self.hour_t, hour_v=self.hour_v.astype(np.float32), hour_n=self.hour_n, hour_i=self.hour_i,
            hour_sure=self.hour_sure,
            cur_hour=np.array(np.nan if self.cur_hour is None else self.cur_hour, float),
            cur=np.array([np.nan if v is None else v for v in (self.cur_min, self.last_start, self.cur_sec)], float),
            cur_rec=np.full(_SEC_W, np.nan) if self.cur_rec is None else self.cur_rec,
            cur_vec=np.full(_M, np.nan) if self.cur_vec is None else self.cur_vec,
        )
        path = Path(path)
        tmp = path.with_name(path.name + ".tmp")
        with open(tmp, "wb") as f:
            np.savez_compressed(f, **state)
        tmp.replace(path)

    @classmethod
    def load(cls, path) -> "History":
        h = cls()
        path = Path(path) if path else None
        if path is None or not path.exists():
            return h
        s = np.load(path)
        if int(s["state_version"]) != STATE_VERSION or str(s["features_version"]) != _version():
            h.reset_reason = "saved history was made with different features; started fresh"
            return h
        h.secs.extend(tuple(r) for r in s["secs"])
        h.shocks.extend(int(v) for v in s["shocks"])
        h.bangs.extend(int(v) for v in s["bangs"])
        h.run_peaks.extend(float(v) for v in s["run_peaks"])
        h.stopped = bool(s["stopped"])
        h.min_t = s["min_t"].astype(float)
        h.min_v = s["min_v"].astype(np.float32)
        h.min_i = int(s["min_i"])
        h.min_acc = list(s["min_acc"])
        h.hour_t = s["hour_t"].astype(float)
        h.hour_v = s["hour_v"].astype(np.float32)
        h.hour_n = s["hour_n"].astype(np.float32)
        h.hour_i = int(s["hour_i"])
        h.hour_sure = s["hour_sure"].astype(bool)
        h.cur_hour = None if np.isnan(float(s["cur_hour"])) else int(s["cur_hour"])
        cm, ls, cs = s["cur"]
        h.cur_min = None if np.isnan(cm) else int(cm)
        h.last_start = None if np.isnan(ls) else int(ls)
        if not np.isnan(cs):  # the second that was still open when the state was saved
            h.cur_sec, h.cur_rec, h.cur_vec = int(cs), s["cur_rec"].astype(float), s["cur_vec"].astype(float)
        h.loaded = True
        return h


class FeatureEngine:
    """Full feature rows for one stream: per-window features plus history."""

    def __init__(self, history: History | None = None):
        self.history = history or History()

    def update(self, win: F.Window) -> np.ndarray:
        x = F.extract(win)
        x[F.HISTORY_SLICE] = self.history.update(win, x)
        return x
