#!/usr/bin/env python3
"""Learn mode: record days to a month of data for training. No model here.

Sensors run at full rate the whole time; what changes over a long recording
is what gets kept. In --out:

  features/features_NNNNN.npz   one file per hour of the device clock (NNNNN is
                                the hour number): one feature row per second
                                (X, t0), plus the min, mean and max of every
                                feature for each minute (minute_*)
  features/*.partial.npz        the current hour, rewritten every minute, so a
                                power cut loses at most a minute. `recover()`
                                (or the next start) turns leftovers into finished files.
  raw/clip_NNNNNNNNN.npz        10 s of raw sensor data every 10 minutes, and
                                10 s around every sudden event (from 4 s before
                                it, so the moment itself is kept), in the
                                recording format, so features can be recomputed
  events.jsonl                  start and stop, sensors dropping out or coming
                                back, the device being moved, gaps, sudden jolts,
                                bangs and heat jumps, and sensor problems
                                (clipping, stuck readings, a frozen camera)

About 0.9 GB a week, 4 GB a month, with the defaults (measured on the simulated server
rack); --max-gb caps it, and the cleanup in storage.py keeps the most useful data.

  python3 -m device.intake.recorder --data data/fake/normal-day-01 --out recordings/learn-01
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import warnings
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import numpy as np

from effente import features as F
from effente.history import History
from device.intake.extract import add_source_args, feature_stream, features_version, make_source
from device.intake.source import FileSource, Packet, SourceInfo
from device.intake.storage import StorageManager
from device.intake.windowing import HOP_S, WINDOW_S

HISTORY_SAVE_EVERY_S = 300.0

GRAVITY = [F.INDEX[f"vib_gravity_{a}"] for a in "xyz"]
MOVE_G = 0.05  # gravity direction change that counts as "device moved"
MOVE_WINDOWS = 8  # ... sustained for this many windows (2 s at 4 per second)

RING_S = 12.0  # raw data kept in memory, so an event clip can start before the event
EVENT_BEFORE_S, EVENT_AFTER_S = 4.0, 6.0
EVENT_MIN_GAP_S = 60.0  # at most one clip per kind of event per minute ...
EVENT_MAX_PER_DAY = 300  # ... and this many per day, so a storm of events cannot fill the disk
HEAT_JUMP_C = 5.0  # sudden heat: the hottest point jumps this much above its median of the minute before ...
HEAT_EVENT_MIN_C = 45.0  # ... to above this (hotter than a person walking past the camera) ...
HEAT_HOLD_S = 3  # ... and stays there this many seconds (arcing, a flame, a part suddenly overheating)
BEEP_DB = 30.0  # the strongest tone switching on and off by this much: an alarm, a warning beeper,
              # or a squeal that comes and goes (normal machines stay under about 25 dB)
BEEP_QUIET_S = 120.0  # a new beeping episode starts after this long without beeping
QUALITY_WINDOWS = 40  # a data problem must last this many windows (10 s) before it is logged


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _audio_ints(v: np.ndarray, bits: int) -> np.ndarray:
    """Microphone samples as stored: int16 up to 16 bits, int32 above, clipped to the range (never wrapped)."""
    top = 2 ** (bits - 1) - 1
    return np.clip(np.round(np.asarray(v, float)), -top - 1, top).astype(np.int16 if bits <= 16 else np.int32)


def _save_atomic(path: Path, **arrays) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as f:
        np.savez_compressed(f, **arrays)
    os.replace(tmp, path)


class Recorder:
    def __init__(self, out_dir, info: Callable[[], SourceInfo], sensors, label: str = "normal",
                 keep_every_s: float = 1.0, window_s: float = WINDOW_S, hop_s: float = HOP_S,
                 snippet_every_s: float = 600.0, snippet_len_s: float = 10.0, checkpoint_s: float = 60.0,
                 max_gb: float | None = None, min_free_gb: float = 2.0, history: History | None = None,
                 storage_kw: dict | None = None):
        self.out = Path(out_dir)
        (self.out / "features").mkdir(parents=True, exist_ok=True)
        (self.out / "raw").mkdir(parents=True, exist_ok=True)
        recovered = recover(self.out)
        self.info, self.sensors, self.label = info, tuple(sensors), label
        self.history = history
        self.history_path = self.out / "history.npz"
        self.last_history_save: float | None = None
        self.storage = (StorageManager(self.out, max_gb, min_free_gb, event=self.event, **(storage_kw or {}))
                        if max_gb is not None else None)
        self.keep_every_s, self.window_s, self.hop_s = keep_every_s, window_s, hop_s
        self.snippet_every_s, self.snippet_len_s, self.checkpoint_s = snippet_every_s, snippet_len_s, checkpoint_s
        self.events_f = open(self.out / "events.jsonl", "a")
        self.counts = defaultdict(int)

        self.hour: int | None = None
        self.rows: list[np.ndarray] = []
        self.t0s: list[float] = []
        self.minute: int | None = None
        self.minute_buf: list[np.ndarray] = []
        self.min_t0: list[float] = []
        self.min_stats: dict[str, list[np.ndarray]] = {"min": [], "mean": [], "max": []}
        self.last_kept = -math.inf
        self.last_checkpoint: float | None = None
        self.prev_t0: float | None = None
        self.prev_missing: set | None = None
        self.grav_ref: np.ndarray | None = None
        self.grav_first: list[np.ndarray] = []
        self.moved_run = 0
        self.snips: dict[float, dict] = {}
        self.latest = {s: -math.inf for s in self.sensors}
        self.ring: deque = deque()
        self.last_periodic = -math.inf
        self.last_capture: dict[str, float] = {}
        self.captures_today: dict[int, int] = {}
        self.last_seen_event: dict[str, float] = {}
        self.runs = defaultdict(int)  # consecutive windows of a data-quality problem
        self.flagged: set = set()
        self.recent_rows: deque = deque(maxlen=int(8 / hop_s) + 2)  # the last few seconds of feature rows
        self.beep_secs: deque = deque(maxlen=8)  # recent seconds with a beeping tone
        self.heat_secs: deque = deque(maxlen=90)  # (second, hottest point) for the last minute and a half
        self.heat_high = False
        self.beep_logged = False

        self.event(None, "recording_started", label=label, wall_time=_now(),
                   **({"recovered_partial_hours": recovered} if recovered else {}))

    # ------------------------------------------------------------ events
    def event(self, t, name, **details):
        self.counts["events"] += 1
        rec = {"t": None if t is None else round(float(t), 3), "event": name, **details}
        self.events_f.write(json.dumps(rec) + "\n")
        self.events_f.flush()

    # ------------------------------------------------------------ raw snippets and event clips
    def on_packet(self, p: Packet) -> None:
        if p.sensor not in self.latest or len(p.t) == 0:
            return
        self.ring.append(p)  # the last few seconds, so an event clip can start before the event
        while self.ring and self.ring[0].t[-1] < p.t[-1] - RING_S:
            self.ring.popleft()
        self.latest[p.sensor] = max(self.latest[p.sensor], float(p.t[-1]))
        if self.storage is not None and self.storage.paused:
            return
        if self.snippet_every_s > 0:
            for s in np.unique(np.floor(p.t / self.snippet_every_s) * self.snippet_every_s):
                if s > self.last_periodic:
                    self.last_periodic = float(s)
                    self.snips[float(s)] = {"end": float(s) + self.snippet_len_s, "parts": defaultdict(list)}
        for s, buf in self.snips.items():
            self._add(s, buf, p)
        done = min(self.latest.values())
        for s in [s for s, b in self.snips.items() if done >= b["end"]]:
            self._write_snippet(s)

    def _add(self, s: float, buf: dict, p: Packet) -> None:
        # Audio is stored in whole blocks, so take one extra block past the end;
        # trimming to whole blocks then still covers the full clip.
        info = self.info()
        extra = info.audio_block_len / info.audio_rate_hz if p.sensor == "audio" else 0.0
        sel = (p.t >= s) & (p.t < buf["end"] + extra)
        if sel.any():
            buf["parts"][p.sensor].append((p.t[sel], p.values[sel]))

    def capture(self, t_event: float, why: str, **details) -> None:
        """Save raw data from EVENT_BEFORE_S before an event to EVENT_AFTER_S after it."""
        last = self.last_capture.get(why, -math.inf)
        day = int(t_event // 86400)
        if t_event - last < EVENT_MIN_GAP_S or self.captures_today.get(day, 0) >= EVENT_MAX_PER_DAY:
            self.event(t_event, why, captured=False, **details)
            return
        self.last_capture[why] = t_event
        self.captures_today[day] = self.captures_today.get(day, 0) + 1
        start = math.floor(t_event - EVENT_BEFORE_S)
        clip = f"clip_{start:09d}.npz"
        covered = any(s <= t_event - 1 and b["end"] >= t_event + 2 for s, b in self.snips.items())
        if not covered and not (self.out / "raw" / clip).exists():
            buf = {"end": float(start + EVENT_BEFORE_S + EVENT_AFTER_S), "parts": defaultdict(list)}
            for p in self.ring:
                self._add(float(start), buf, p)
            self.snips[float(start)] = buf
            self.counts["event_clips"] += 1
        self.event(t_event, why, captured=True, clip=clip, **details)

    def _write_snippet(self, s: float) -> None:
        bufs = self.snips.pop(s)["parts"]
        info = self.info()

        def cat(sensor):
            parts = bufs.get(sensor, [])
            if not parts:
                return np.empty(0), None
            return np.concatenate([t for t, _ in parts]), np.concatenate([v for _, v in parts])

        ta, va = cat("accel")
        tg, vg = cat("gyro")
        tb, vb = cat("audio")
        tt, vt = cat("thermal")
        tr, vr = cat("room")
        if va is None or len(va) < 2:
            return  # nothing useful to keep
        bl = info.audio_block_len
        n = (len(tb) // bl) * bl
        arrays = dict(
            samples=np.clip(np.round((va if va is not None else np.empty((0, 3))) * info.accel_lsb_per_g),
                            -32768, 32767).astype(np.int16),
            sample_rate_hz=1000, accel_t_us=np.round(ta * 1e6).astype(np.int64),
            accel_lsb_per_g=info.accel_lsb_per_g,
            audio=_audio_ints(vb[:n] if vb is not None else np.empty(0), info.audio_bits), audio_bits=info.audio_bits,
            audio_t_us=np.round(tb[:n:bl] * 1e6).astype(np.int64), audio_block_len=bl,
            audio_rate_hz=int(round(info.audio_rate_hz)), audio_gain_db=info.audio_gain_db,
            thermal=(vt if vt is not None else np.empty((0, 24, 32))).astype(np.float32),
            thermal_t_us=np.round(tt * 1e6).astype(np.int64), thermal_rate_hz=4,
            label=self.label, recorded_at=_now(), session_id="learn", mount_id="m1",
            week_offset_s=np.nan if info.week_offset_s is None else info.week_offset_s,
        )
        if vg is not None and len(vg) == len(va):
            arrays.update(gyro=np.clip(np.round(vg * info.gyro_lsb_per_dps), -32768, 32767).astype(np.int16),
                          gyro_lsb_per_dps=info.gyro_lsb_per_dps)
        if vr is not None:
            arrays.update(room_c=vr[:, 0].astype(np.float32), humidity_pct=vr[:, 1].astype(np.float32),
                          room_t_us=np.round(tr * 1e6).astype(np.int64), room_rate_hz=1.0)
        _save_atomic(self.out / "raw" / f"clip_{int(s):09d}.npz", **arrays)
        self.counts["snippets"] += 1

    # ------------------------------------------------------------ features
    def on_window(self, t0: float, x: np.ndarray) -> None:
        self._watch_events(t0, x)
        m, hour = int(t0 // 60), int(t0 // 3600)
        if self.minute is not None and m != self.minute:
            self._close_minute()  # uses the old minute
        if self.hour is not None and hour != self.hour:
            self._finish_hour()  # uses the old hour
        if hour != self.hour:
            self._start_hour(hour, t0)
        self.minute, self.hour = m, hour
        self.minute_buf.append(x)
        if self.storage is not None:
            self.storage.maybe_check(t0)
        paused = self.storage is not None and self.storage.paused
        if not paused and t0 - self.last_kept >= self.keep_every_s - 1e-9:
            self.rows.append(x.astype(np.float32))
            self.t0s.append(t0)
            self.last_kept = t0
            self.counts["rows"] += 1
        self.counts["windows"] += 1
        if self.last_checkpoint is None:
            self.last_checkpoint = t0
        if t0 - self.last_checkpoint >= self.checkpoint_s:
            self._save(partial=True)
            self.last_checkpoint = t0
        if self.history is not None and (self.last_history_save is None
                                         or t0 - self.last_history_save >= HISTORY_SAVE_EVERY_S):
            self.history.save(self.history_path)
            self.last_history_save = t0

    def _watch_events(self, t0, x):
        if self.prev_t0 is not None and t0 - self.prev_t0 > 2 * self.hop_s + 1e-6:
            self.event(t0, "gap", seconds=round(t0 - self.prev_t0 - self.hop_s, 3))
        self.prev_t0 = t0
        missing = set(F.missing_sensors(x))
        if self.prev_missing is not None:
            for s in sorted(missing - self.prev_missing):
                self.event(t0, "sensor_lost", sensor=s)
            for s in sorted(self.prev_missing - missing):
                self.event(t0, "sensor_back", sensor=s)
        elif missing:
            self.event(t0, "sensor_missing_at_start", sensors=sorted(missing))
        self.prev_missing = missing
        self._sudden_events(t0, x)
        self._quality(t0, x)
        g = x[GRAVITY]
        if np.isnan(g).any():
            return
        if self.grav_ref is None:
            self.grav_first.append(g)
            if len(self.grav_first) >= 40:
                self.grav_ref = np.median(self.grav_first, 0)
            return
        if np.linalg.norm(g - self.grav_ref) > MOVE_G:
            self.moved_run += 1
            if self.moved_run >= MOVE_WINDOWS:
                self.event(t0, "device_moved", change_g=round(float(np.linalg.norm(g - self.grav_ref)), 3))
                self.grav_ref, self.moved_run = g, 0
        else:
            self.moved_run = 0

    def _sudden_events(self, t0, x):
        """Log jolts, bangs, beeping, heat jumps and drops, and keep the raw data around them."""
        self.recent_rows.append((t0, x))
        v = lambda n: x[F.INDEX[n]]
        for since_name, why in (("trend_since_shock_s", "sudden_vibration"), ("trend_since_bang_s", "sudden_sound")):
            since = v(since_name)
            if np.isnan(since) or since > 1:
                continue
            t_event = math.floor(t0) - 1 - since
            if self.last_seen_event.get(why) == t_event:
                continue  # the same event, seen again by an overlapping window
            self.last_seen_event[why] = t_event
            # Describe the event from its own second's row, not the current one.
            ev = next((r for t, r in self.recent_rows if abs(t - t_event) < 1e-6), x)
            e = lambda n: ev[F.INDEX[n]]
            if why == "sudden_vibration":
                details = {"peak_g": round(10 ** e("vib_peak_g"), 3),
                           "axis": "xyz"[int(e("vib_shock_axis"))] if not np.isnan(e("vib_shock_axis")) else None}
            else:
                details = {"peak_db": round(float(e("snd_peak_db")), 1)}
            self.capture(t_event, why, **details)
        fall = v("vib_freefall_frac")
        if not np.isnan(fall) and fall > 0.05 and t0 - self.last_seen_event.get("device_dropped", -math.inf) > 5:
            self.last_seen_event["device_dropped"] = t0
            self.capture(t0, "device_dropped", falling_s=round(float(fall) * self.window_s, 2))
        mod = v("snd_tone_mod_db")
        s = math.floor(t0 + 1e-9)
        if not np.isnan(mod) and mod >= BEEP_DB:
            if self.beep_secs and s - self.beep_secs[-1] > BEEP_QUIET_S:
                self.beep_logged = False
            if not self.beep_secs or self.beep_secs[-1] != s:
                self.beep_secs.append(s)
            if not self.beep_logged and sum(1 for b in self.beep_secs if b > s - 5) >= 3:
                self.beep_logged = True
                self.capture(t0, "beeping", tone_hz=round(float(v("snd_tone_hz"))),
                             level_db=round(float(v("snd_level_db")), 1))
        hot = v("th_max_c")
        if not np.isnan(hot) and (not self.heat_secs or self.heat_secs[-1][0] != s):
            self.heat_secs.append((s, hot))
            before = [h for t, h in self.heat_secs if s - 70 <= t < s - 10]
            last = [h for t, h in self.heat_secs if t > s - HEAT_HOLD_S]
            if len(before) >= 30 and len(last) == HEAT_HOLD_S:
                ref = float(np.median(before))
                high = min(last) >= max(ref + HEAT_JUMP_C, HEAT_EVENT_MIN_C)
                if high and not self.heat_high:
                    self.capture(s - HEAT_HOLD_S + 1, "sudden_heat", max_c=round(float(hot), 1),
                                 rise_c=round(float(min(last) - ref), 1),
                                 at=[int(v("th_hot_row")), int(v("th_hot_col"))])
                self.heat_high = high

    def _quality(self, t0, x):
        """Problems with the sensing itself: clipping, stuck readings, a frozen camera."""
        v = lambda n: x[F.INDEX[n]]
        rms = max(v("vib_rms_x"), v("vib_rms_y"), v("vib_rms_z"))
        checks = {
            ("sensor_clipping", "vibration"): v("vib_clip_frac") > 0.001,
            ("sensor_clipping", "sound"): v("snd_clip_frac") > 0.001,
            ("sensor_stuck", "vibration"): rms < -5.0,  # no noise at all: a real accelerometer always has some
            ("sensor_stuck", "sound"): v("snd_level_db") < 10.0,  # silence below the mic's own noise
            ("sensor_stuck", "heat"): v("th_frozen") >= 1.0,
        }
        for key, bad in checks.items():
            self.runs[key] = self.runs[key] + 1 if bad else 0
            if self.runs[key] >= QUALITY_WINDOWS and key not in self.flagged:
                self.flagged.add(key)
                self.event(t0, key[0], sensor=key[1])
            elif self.runs[key] == 0 and key in self.flagged:
                self.flagged.discard(key)
                self.event(t0, key[0] + "_cleared", sensor=key[1])

    def _close_minute(self):
        if not self.minute_buf:
            return
        a = np.vstack(self.minute_buf)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            self.min_stats["min"].append(np.nanmin(a, 0))
            self.min_stats["mean"].append(np.nanmean(a, 0))
            self.min_stats["max"].append(np.nanmax(a, 0))
        self.min_t0.append(self.minute * 60.0)
        self.minute_buf = []

    def _save(self, partial: bool):
        if self.hour is None:
            return
        n = F.N_FEATURES
        stack = lambda k: np.array(self.min_stats[k], np.float32).reshape(-1, n)
        name = f"features_{self.hour:05d}" + (".partial" if partial else "") + ".npz"
        _save_atomic(
            self.out / "features" / name,
            X=np.array(self.rows, np.float32).reshape(-1, n), t0=np.array(self.t0s),
            minute_t0=np.array(self.min_t0), minute_min=stack("min"), minute_mean=stack("mean"),
            minute_max=stack("max"), feature_names=np.array(F.FEATURE_NAMES),
            features_version=features_version(), window_s=self.window_s, hop_s=self.hop_s,
            keep_every_s=self.keep_every_s, hour_index=self.hour, label=self.label, saved_at=_now(),
        )

    def _start_hour(self, hour: int, t0: float) -> None:
        """Carry on with an hour already on disk (a restart within the hour), so it is not overwritten."""
        done = self.out / "features" / f"features_{hour:05d}.npz"
        if not done.exists():
            return
        with np.load(done) as d:
            same = str(d["features_version"]) == features_version() and d["X"].shape[1:] == (F.N_FEATURES,)
            if same:
                keep, mkeep = d["t0"] < t0, d["minute_t0"] < (t0 // 60) * 60
                self.rows, self.t0s = list(d["X"][keep]), list(d["t0"][keep])
                self.min_t0 = list(d["minute_t0"][mkeep])
                self.min_stats = {k: list(d[f"minute_{k}"][mkeep]) for k in ("min", "mean", "max")}
                if self.t0s:
                    self.last_kept = float(self.t0s[-1])
        if not same:  # made with other features: keep it aside rather than mix columns
            aside = self.out / "features" / "other_features"
            aside.mkdir(exist_ok=True)
            os.replace(done, aside / done.name)
            self.event(t0, "hour_set_aside", file=done.name, reason="made with different features")

    def _finish_hour(self):
        self._save(partial=False)
        partial = self.out / "features" / f"features_{self.hour:05d}.partial.npz"
        partial.unlink(missing_ok=True)
        self.counts["hours"] += 1
        self.rows, self.t0s, self.min_t0 = [], [], []
        self.min_stats = {"min": [], "mean": [], "max": []}
        self.last_checkpoint = None

    def _snippet_span(self, s: float) -> float:
        parts = self.snips[s]["parts"].get("accel") or self.snips[s]["parts"].get("audio") or []
        return float(parts[-1][0][-1] - parts[0][0][0]) if parts else 0.0

    def close(self):
        self._close_minute()
        for s in sorted(self.snips):  # unfinished snippets: keep them only if at least half full
            if self._snippet_span(s) >= 0.5 * self.snippet_len_s:
                self._write_snippet(s)
        self.snips = {}
        if self.hour is not None:
            self._finish_hour()
        if self.history is not None:
            self.history.save(self.history_path)
        if self.storage is not None:
            self.storage.flush(self.prev_t0)
        self.event(self.prev_t0, "recording_stopped", wall_time=_now(),
                   windows=self.counts["windows"], rows=self.counts["rows"])
        self.events_f.close()


def open_history(out_dir) -> History:
    """The saved history for a recording folder, or a fresh one."""
    return History.load(Path(out_dir) / "history.npz")


def recover(out_dir) -> list[str]:
    """Turn leftover partial hours (from a crash or power cut) into finished files."""
    done = []
    for p in sorted((Path(out_dir) / "features").glob("*.partial.npz")):
        final = p.with_name(p.name.replace(".partial", ""))
        if final.exists():
            p.unlink()
        else:
            os.replace(p, final)
            done.append(final.name)
    return done


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_source_args(ap)
    ap.add_argument("--out", required=True)
    ap.add_argument("--label", default="normal")
    ap.add_argument("--keep-every", type=float, default=1.0, help="seconds between kept feature rows")
    ap.add_argument("--snippet-every", type=float, default=600.0, help="seconds between raw snippets (0 = none)")
    ap.add_argument("--snippet-len", type=float, default=10.0)
    ap.add_argument("--hop", type=float, default=HOP_S, help="seconds between windows (1 = faster replay, same rows)")
    ap.add_argument("--max-gb", type=float, help="storage budget for the folder (cleanup when crossed)")
    ap.add_argument("--min-free-gb", type=float, default=2.0)
    args = ap.parse_args(argv)

    source = make_source(args)
    history = open_history(args.out)
    rec = Recorder(args.out, lambda: source.info, source.sensors, args.label, args.keep_every, hop_s=args.hop,
                   snippet_every_s=args.snippet_every, snippet_len_s=args.snippet_len,
                   max_gb=args.max_gb, min_free_gb=args.min_free_gb, history=history)
    if history.reset_reason:
        rec.event(None, "history_reset", reason=history.reset_reason)
    started = time.perf_counter()
    for t0, x in feature_stream(source, hop_s=args.hop, tap=rec.on_packet, history=history):
        rec.on_window(t0, x)
    rec.close()
    out = Path(args.out)
    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    c = rec.counts
    print(f"recorded {c['windows']:,} windows -> {c['rows']:,} feature rows in {c['hours']} hour files, "
          f"{c['snippets']} raw snippets, {c['events']} events; {size / 1e6:.1f} MB in "
          f"{time.perf_counter() - started:.0f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
