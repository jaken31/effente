"""Keep a recording folder within its storage budget.

Two limits: the recording folder may use at most `max_gb`, and the disk must
keep at least `min_free_gb` free. When either is crossed, the least useful
data goes first, and every step is written to the events log (the first
time it happens, then at most once an hour with the totals since):

  1. raw snippets from quiet periods, oldest first. Kept: snippets within
     10 minutes of an event (sensor lost, device moved, gap), and one per hour
  2. old feature rows thinned: hours older than `keep_recent_hours` go from
     1 row a second to 1 every `thin_every_s` seconds. Per-minute summaries
     stay, so every condition the machine went through is still covered
  3. the remaining raw snippets, oldest first
  4. last resort: recording pauses (watch mode keeps running) until there
     is room again

Never deleted: per-minute summaries, the events log, the history state.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Callable

import numpy as np

NOTABLE = {"sensor_lost", "sensor_back", "device_moved", "gap", "sensor_missing_at_start",
           "sudden_vibration", "sudden_sound", "sudden_heat", "sensor_clipping", "sensor_stuck", "device_dropped",
           "beeping"}


_HOUR_FILE = re.compile(r"features_(\d+)\.npz")  # finished hours only, not .partial


class StorageManager:
    def __init__(self, out_dir, max_gb: float = 10.0, min_free_gb: float = 2.0, keep_recent_hours: int = 24,
                 thin_every_s: float = 10.0, check_every_s: float = 300.0, log_every_s: float = 3600.0,
                 event: Callable | None = None, disk_free: Callable[[], int] | None = None):
        self.out = Path(out_dir)
        self.max_bytes = int(max_gb * 1e9)
        self.min_free = int(min_free_gb * 1e9)
        self.keep_recent_hours, self.thin_every_s = keep_recent_hours, thin_every_s
        self.check_every_s, self.log_every_s = check_every_s, log_every_s
        self.pending: dict[str, dict] = {}  # cleanup done but not yet logged, per step
        self.logged_at: dict[str, float] = {}
        self.event = event or (lambda t, name, **kw: None)
        self.disk_free = disk_free or (lambda: shutil.disk_usage(self.out).free)
        self.paused = False
        self.last_check: float | None = None

    # ------------------------------------------------------------ sizes
    def used(self) -> int:
        return sum(p.stat().st_size for p in self.out.rglob("*") if p.is_file())

    def _over(self, used: int, free: int) -> bool:
        return used > self.max_bytes or free < self.min_free

    def _notable_times(self) -> list[float]:
        path = self.out / "events.jsonl"
        if not path.exists():
            return []
        times = []
        for line in path.read_text().splitlines():
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("event") in NOTABLE and e.get("t") is not None:
                times.append(float(e["t"]))
        return times

    # ------------------------------------------------------------ policy
    def maybe_check(self, now_t: float) -> list[str]:
        if self.last_check is not None and now_t - self.last_check < self.check_every_s:
            return []
        self.last_check = now_t
        return self.check(now_t)

    def check(self, now_t: float) -> list[str]:
        actions: list[str] = []
        used, free = self.used(), self.disk_free()
        if not self._over(used, free):
            if self.paused and used < 0.9 * self.max_bytes and free > 1.2 * self.min_free:
                self.paused = False
                self.event(now_t, "recording_resumed", used_mb=round(used / 1e6, 1))
                actions.append("resumed")
            return actions

        def note(step: str, path: Path, freed: int):  # one log line per step, not one per file
            st = self.pending.setdefault(step, {"files": 0, "freed": 0, "first": path.name, "since": now_t})
            st["files"] += 1
            st["freed"] += freed
            st["last"] = path.name

        def drop(path: Path, why: str):
            nonlocal used, free
            size = path.stat().st_size
            path.unlink()
            used -= size
            free += size
            actions.append(f"deleted {path.name} ({why})")
            note(why, path, size)

        snippets = sorted((self.out / "raw").glob("clip_*.npz"))
        notable = self._notable_times()

        def protected(p: Path) -> bool:
            t = float(p.stem.split("_")[1])
            near_event = any(abs(t - e) <= 600 for e in notable)
            return near_event or t % 3600 < 600  # the first snippet of every hour stays

        for p in [p for p in snippets if not protected(p)]:  # 1. quiet snippets
            if not self._over(used, free):
                break
            drop(p, "quiet raw snippet")

        hours = sorted((int(m.group(1)), p) for p in (self.out / "features").glob("features_*.npz")
                       if (m := _HOUR_FILE.fullmatch(p.name)))
        newest = hours[-1][0] if hours else None
        if newest is not None:  # 2. thin old hours
            for hour, p in hours:
                if not self._over(used, free):
                    break
                if hour > newest - self.keep_recent_hours:
                    continue
                before = p.stat().st_size
                if self._thin(p):
                    after = p.stat().st_size
                    used -= before - after
                    free += before - after
                    actions.append(f"thinned {p.name} to 1 row per {self.thin_every_s:.0f} s")
                    note(f"old hour thinned to 1 row per {self.thin_every_s:.0f} s", p, before - after)

        for p in sorted((self.out / "raw").glob("clip_*.npz")):  # 3. remaining snippets
            if not self._over(used, free):
                break
            drop(p, "raw snippet, storage full")

        if self._over(used, free) and not self.paused:  # 4. pause
            self.paused = True
            actions.append("paused recording")
        self._log(now_t, everything="paused recording" in actions)
        if self.paused and "paused recording" in actions:
            self.event(now_t, "recording_paused", used_mb=round(used / 1e6, 1), free_mb=round(free / 1e6, 1))
        return actions

    def _log(self, now_t: float, everything: bool = False):
        for step in list(self.pending):
            if everything or step not in self.logged_at or now_t - self.logged_at[step] >= self.log_every_s:
                st = self.pending.pop(step)
                self.event(now_t, "storage_cleanup", step=step, files=st["files"],
                           freed_mb=round(st["freed"] / 1e6, 1), first=st["first"], last=st["last"],
                           since=st["since"])
                self.logged_at[step] = now_t

    def flush(self, now_t: float):
        """Log any cleanup not logged yet (call when recording stops)."""
        self._log(now_t, everything=True)

    def _thin(self, path: Path) -> bool:
        with np.load(path) as d:
            data = {k: d[k] for k in d.files}
        if float(data.get("thinned_every_s", 0)) >= self.thin_every_s:
            return False
        t0 = data["t0"]
        keep, last = [], -np.inf
        for i, t in enumerate(t0):
            if t - last >= self.thin_every_s - 1e-6:
                keep.append(i)
                last = t
        data["X"], data["t0"] = data["X"][keep], t0[keep]
        data["thinned_every_s"] = self.thin_every_s
        tmp = path.with_name(path.name + ".tmp")
        with open(tmp, "wb") as f:
            np.savez_compressed(f, **data)
        tmp.replace(path)
        return True
