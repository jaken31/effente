"""Real-site disturbances for any simulated source: what a device beside a real machine also sees.

Simulated machines are too clean: in a real factory or data center people talk, drop tools,
walk in front of the camera, open doors and drive forklifts; neighbouring machines run; the
weather changes the room; sensors drift and drop out. A model trained on clean fake data
learns a "normal" no real site has, and its false-alarm rate looks far better than it will
be. `SiteNoise` wraps any source (the conveyor or server simulators, or a recording replay)
and adds these, scheduled at random but reproducibly from a seed:

  sound      dropped objects and door slams (80-100 dB bangs), people talking, forklifts and
             carts passing (engine rumble; some with a reverse beeper, which sounds like an alarm)
  vibration  small floor jolts from slams and drops, a neighbouring machine running on its own
             schedule (a faint extra tone), and now and then someone knocking the device (a
             jolt and a lasting tilt of a degree or two)
  heat       a person standing in front of the camera for minutes, a hot drink or tool left in
             view (cools over half an hour), sun through a window on sunny days (factory only),
             the camera's own artefacts (checkerboard between its two half-frames, slow offset drift)
  room       doors opening (a cool, damp gust), weather (the room and everything in view drift a
             few degrees over days)
  data       short sensor dropouts (a sensor's packets missing for a few seconds)

None of these is a machine fault: `nuisance_at(t)` names what was going on at time t, so false
alarms can be counted by cause. Levels and rates are plausible starting values, not measured
on a real site (unverified).

  src = SiteNoise(ConveyorSim(days=7), profile="factory", seed=3)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

DAY = 86400.0

PROFILES = {
    # events per hour at full activity (weekday working hours), unless noted
    "factory": dict(drops=6.0, slams=1.0, voices=3.0, forklifts=2.0, beeper_share=0.3, stands=0.4,
                    hot_objects=0.1, doors=1.0, bumps_per_day=0.3, outages=0.3, sun=True, neighbour=True),
    "datacenter": dict(drops=0.5, slams=0.3, voices=0.6, forklifts=0.3, beeper_share=0.0, stands=0.2,
                       hot_objects=0.03, doors=0.5, bumps_per_day=0.1, outages=0.3, sun=False, neighbour=True),
}
# How busy the site is: people-driven events scale with this.
WORK_HOURS = (6.0, 22.0)
NIGHT_ACTIVITY, WEEKEND_ACTIVITY = 0.15, 0.3


@dataclass
class Event:
    kind: str
    start: float
    dur: float
    p: dict = field(default_factory=dict)

    @property
    def end(self) -> float:
        return self.start + self.dur


class SiteNoise:
    def __init__(self, source, profile: str = "factory", seed: int = 0, intensity: float = 1.0):
        if profile not in PROFILES:
            raise ValueError(f"profile must be one of {sorted(PROFILES)}")
        self.src, self.profile, self.seed, self.intensity = source, profile, seed, intensity
        self.cfg = PROFILES[profile]
        start = float(getattr(source, "start_s", 0.0))
        end = float(getattr(source, "end_s", start + DAY))
        days = getattr(source, "days", None)
        self.span = (0.0, max(end, (days or 0) * DAY))  # schedule the whole run, so any slice sees the same site
        self.events: list[Event] = []
        self._schedule()
        self._cache: dict[int, np.ndarray] = {}
        self._tilt = [(e.start, e.p["tilt"]) for e in self.events if e.kind == "device_knocked"]

    # ------------------------------------------------------------ passthrough
    def __getattr__(self, name):  # info, sensors, label_at, truth_at, faults, ... of the wrapped source
        return getattr(self.src, name)

    def nuisance_at(self, t: float) -> list[str]:
        return sorted({e.kind for e in self.events if e.start <= t < e.end})

    # ------------------------------------------------------------ schedule
    def _week_t(self, t):
        off = getattr(self.src.info, "week_offset_s", None) or 0.0
        return t + off

    def activity(self, t: float) -> float:
        w = self._week_t(t)
        day, hour = int(w // DAY) % 7, (w % DAY) / 3600
        if day >= 5:
            return WEEKEND_ACTIVITY
        return 1.0 if WORK_HOURS[0] <= hour < WORK_HOURS[1] else NIGHT_ACTIVITY

    def _poisson(self, rng, per_hour: float, people: bool = True) -> list[float]:
        lo, hi = self.span
        rate = per_hour * self.intensity / 3600.0
        if rate <= 0:
            return []
        n = rng.poisson(rate * (hi - lo))
        times = np.sort(rng.uniform(lo, hi, n))
        if people:
            times = [t for t in times if rng.random() < self.activity(t)]
        return [float(t) for t in times]

    def _schedule(self):
        rng = np.random.default_rng([self.seed, 77])
        c, ev = self.cfg, self.events
        for t in self._poisson(rng, c["drops"]):
            ev.append(Event("dropped_object", t, 0.6, dict(level=rng.uniform(80, 100), tau=rng.uniform(0.03, 0.15),
                                                            ring=rng.uniform(600, 4000, 3), jolt=rng.uniform(0.005, 0.04))))
        for t in self._poisson(rng, c["slams"]):
            ev.append(Event("door_slam", t, 0.8, dict(level=rng.uniform(88, 100), tau=rng.uniform(0.06, 0.12),
                                                       ring=rng.uniform(80, 400, 3), jolt=rng.uniform(0.02, 0.08))))
        for t in self._poisson(rng, c["voices"]):
            ev.append(Event("people_talking", t, rng.uniform(10, 120), dict(level=rng.uniform(56, 70),
                                                                            f0=rng.uniform(95, 230), ph=rng.uniform(0, 6.3))))
        for t in self._poisson(rng, c["forklifts"]):
            beeper = rng.random() < c["beeper_share"]
            ev.append(Event("forklift_passing", t, rng.uniform(15, 60),
                            dict(level=rng.uniform(70, 84), fe=rng.uniform(24, 40), beeper=beeper,
                                 beep_hz=rng.uniform(900, 1250), beep_level=rng.uniform(78, 90))))
        for t in self._poisson(rng, c["stands"]):
            ev.append(Event("person_in_front_of_camera", t, rng.uniform(60, 300),
                            dict(c0=int(rng.integers(0, 20)), w=int(rng.integers(8, 16)), r0=int(rng.integers(0, 8)),
                                 temp=rng.uniform(30.5, 34.0))))
        for t in self._poisson(rng, c["hot_objects"]):
            ev.append(Event("hot_object_in_view", t, rng.uniform(600, 2400),
                            dict(r=int(rng.integers(1, 22)), c=int(rng.integers(1, 30)), t0=rng.uniform(50, 66),
                                 tau=rng.uniform(600, 1500))))
        for t in self._poisson(rng, c["doors"]):
            ev.append(Event("door_open", t, rng.uniform(900, 1800), dict(drop=rng.uniform(0.5, 2.0),
                                                                         rh=rng.uniform(-6, 6))))
        for t in self._poisson(rng, c["bumps_per_day"] / 24.0):
            v = rng.normal(0, 1, 3)
            ev.append(Event("device_knocked", t, 1.0, dict(peak=rng.uniform(0.2, 1.0), tilt=v / np.linalg.norm(v)
                                                           * math.radians(rng.uniform(1, 3)))))
        for t in self._poisson(rng, c["outages"], people=False):
            ev.append(Event("sensor_dropout", t, rng.uniform(2, 8),
                            dict(sensor=str(rng.choice(["accel", "audio", "thermal", "room"])))))
        lo, hi = self.span
        if c["neighbour"]:  # a neighbouring machine runs in blocks during working hours
            fn = rng.uniform(18, 47)
            t = lo
            while t < hi:
                gap, run = rng.uniform(600, 5400), rng.uniform(1200, 7200)
                t += gap
                if rng.random() < self.activity(t):
                    ev.append(Event("neighbour_machine", t, run, dict(f=fn * rng.uniform(0.98, 1.02),
                                                                      g=rng.uniform(0.002, 0.006), level=rng.uniform(55, 64))))
                t += run
        if c["sun"]:
            for d in range(int(hi // DAY) + 1):
                if rng.random() < 0.5:
                    ev.append(Event("sun_through_window", d * DAY + 9 * 3600, 7 * 3600,
                                    dict(peak=rng.uniform(1.5, 4.0), r0=rng.uniform(4, 20), c0=rng.uniform(0, 10))))
        # weather and the camera's slow offset: smooth random walks over the whole run
        n = int((hi - lo) // 600) + 3
        self._weather = np.cumsum(rng.normal(0, 0.12, n))  # every 10 minutes; about +-2 degC over a week
        self._weather -= np.linspace(self._weather[0], self._weather[-1], n) * 0.5
        self._cam_drift = np.convolve(rng.normal(0, 0.5, n), np.ones(6) / 6, mode="same") * 0.4
        self.events.sort(key=lambda e: e.start)

    def _slow(self, arr, t):
        x = (np.asarray(t) - self.span[0]) / 600.0
        i = np.clip(np.floor(x).astype(int), 0, len(arr) - 2)
        f = np.clip(x - i, 0, 1)
        return arr[i] * (1 - f) + arr[i + 1] * f

    # ------------------------------------------------------------ packets
    def packets(self):
        for p in self.src.packets():
            if len(p.t) == 0:
                yield p
                continue
            t0, t1 = float(p.t[0]), float(p.t[-1])
            if any(e.kind == "sensor_dropout" and e.p["sensor"] == p.sensor and e.start <= t0 < e.end
                   for e in self._active(t0, t1)):
                continue  # this sensor's data is missing for a few seconds
            yield type(p)(p.sensor, p.t, self._apply(p.sensor, p.t, p.values, t0, t1))

    def _active(self, t0, t1):
        return [e for e in self.events if e.start <= t1 and e.end >= t0]

    def _apply(self, sensor, t, v, t0, t1):
        act = self._active(t0, t1)
        if sensor == "audio":
            return self._audio(t, v, act)
        if sensor == "accel":
            return self._accel(t, v, act)
        if sensor == "gyro":
            return self._gyro(t, v, act)
        if sensor == "thermal":
            return self._thermal(t, v, act)
        if sensor == "room":
            return self._room(t, v, act)
        return v

    def _counts(self, db: float) -> float:
        """Microphone rms counts for a level in dB SPL (0 dBFS = 120 dB SPL less the gain)."""
        info = self.src.info
        full = 2 ** (getattr(info, "audio_bits", 16) - 1) - 1
        return full / math.sqrt(2) * 10 ** ((db - 120.0 + info.audio_gain_db) / 20.0)

    def _noise(self, e: Event, d: np.ndarray, rate: float) -> np.ndarray:
        """The event's own broadband noise at offsets d (seconds into it), the same whichever packet asks."""
        key = id(e)
        if key not in self._cache:
            if len(self._cache) > 64:
                self._cache.clear()
            rng = np.random.default_rng([self.seed, 78, int(e.start * 1000) % (2 ** 31)])
            self._cache[key] = rng.standard_normal(int(e.dur * rate) + 2)
        buf = self._cache[key]
        return buf[np.minimum((d * rate).astype(int), len(buf) - 1)]

    def _audio(self, t, v, act):
        if not any(e.kind in ("dropped_object", "door_slam", "people_talking", "forklift_passing", "neighbour_machine")
                   for e in act):
            return v
        rate = self.src.info.audio_rate_hz
        x = v.astype(np.float64)
        for e in act:
            dt = t - e.start
            m = (dt >= 0) & (dt < e.dur)
            if not m.any():
                continue
            d = dt[m]
            if e.kind in ("dropped_object", "door_slam"):
                env = np.exp(-d / e.p["tau"])
                nz = self._noise(e, d, rate)
                ring = sum(np.sin(2 * np.pi * f * d) for f in e.p["ring"]) / 1.7
                x[m] += self._counts(e.p["level"]) * 2.5 * env * (0.6 * nz + 0.8 * ring)
            elif e.kind == "people_talking":
                f0 = e.p["f0"] * (1 + 0.04 * np.sin(2 * np.pi * 0.3 * d + e.p["ph"]))
                ph = 2 * np.pi * np.cumsum(f0) / rate
                voice = sum(np.sin(k * ph) / k ** 0.8 for k in range(1, int(3500 / e.p["f0"])))
                syll = np.clip(np.sin(2 * np.pi * 4.1 * d + e.p["ph"]) + 0.3 * np.sin(2 * np.pi * 0.7 * d), 0, None) ** 2
                fade = np.clip(np.minimum(d, e.dur - d) / 2.0, 0, 1)
                x[m] += self._counts(e.p["level"]) * 0.5 * voice * syll * fade
            elif e.kind == "forklift_passing":
                shape = np.sin(np.pi * d / e.dur) ** 2
                eng = sum(np.sin(2 * np.pi * k * e.p["fe"] * d) / k for k in range(1, 7))
                nz = self._noise(e, d, rate)
                x[m] += self._counts(e.p["level"]) * shape * (0.5 * eng + 0.7 * nz)
                if e.p["beeper"]:
                    on = (np.mod(d, 1.0) < 0.5) & (d > 0.35 * e.dur) & (d < 0.65 * e.dur)
                    x[m] += self._counts(e.p["beep_level"]) * math.sqrt(2) * on * np.sin(2 * np.pi * e.p["beep_hz"] * d)
            elif e.kind == "neighbour_machine":
                fade = np.clip(np.minimum(d, e.dur - d) / 20.0, 0, 1)
                x[m] += self._counts(e.p["level"]) * fade * (np.sin(2 * np.pi * 4 * e.p["f"] * d)
                                                             + 0.5 * np.sin(2 * np.pi * 8 * e.p["f"] * d))
        top = 2 ** (getattr(self.src.info, "audio_bits", 16) - 1) - 1
        return np.clip(np.round(x), -top - 1, top).astype(v.dtype)

    def _tilt_at(self, t):
        g = np.zeros((len(t), 3))
        for ts, tilt in self._tilt:
            g[t >= ts] += tilt  # small-angle: gravity rotates by about the tilt in radians
        return g

    def _accel(self, t, v, act):
        a = v.astype(np.float64)
        if self._tilt and t[-1] >= self._tilt[0][0]:
            gdir = a.mean(0)
            norm = np.linalg.norm(gdir) or 1.0
            a += np.cross(self._tilt_at(t), gdir / norm) * norm
        for e in act:
            dt = t - e.start
            m = (dt >= 0) & (dt < min(e.dur, 1.0))
            if e.kind in ("dropped_object", "door_slam", "device_knocked") and m.any():
                d = dt[m]
                rng = np.random.default_rng([self.seed, 79, int(e.start * 1000) % (2 ** 31)])
                axis = rng.normal(0, 1, 3)
                axis /= np.linalg.norm(axis)
                peak = e.p["peak"] if e.kind == "device_knocked" else e.p["jolt"]
                f = rng.uniform(25, 70)
                a[m] += (peak * np.exp(-d / 0.04) * np.sin(2 * np.pi * f * d))[:, None] * axis[None]
            elif e.kind == "neighbour_machine":
                dt_ = t - e.start
                mm = (dt_ >= 0) & (dt_ < e.dur)
                if mm.any():
                    fade = np.clip(np.minimum(dt_[mm], e.dur - dt_[mm]) / 20.0, 0, 1)
                    a[mm, 2] += e.p["g"] * fade * np.sin(2 * np.pi * e.p["f"] * dt_[mm])
        return a.astype(v.dtype)

    def _gyro(self, t, v, act):
        g = v.astype(np.float64)
        for e in act:
            if e.kind == "device_knocked":
                dt = t - e.start
                m = (dt >= 0) & (dt < 1.0)
                if m.any():
                    g[m] += (60 * e.p["peak"] * np.exp(-dt[m] / 0.08) * np.sin(2 * np.pi * 9 * dt[m]))[:, None]
        return g.astype(v.dtype)

    def _thermal(self, t, v, act):
        fr = v.astype(np.float64).copy()
        rows, cols = fr.shape[1], fr.shape[2]
        fr += (self._slow(self._weather, t) + self._slow(self._cam_drift, t))[:, None, None]
        rng = np.random.default_rng([self.seed, 80, int(t[0] * 100) % (2 ** 31)])
        checker = (np.add.outer(np.arange(rows), np.arange(cols)) % 2) * 2 - 1
        fr += rng.normal(0, 0.12, len(t))[:, None, None] * checker[None]  # the two half-frames disagree slightly
        rr, cc = np.mgrid[0:rows, 0:cols]
        for e in act:
            dt = t - e.start
            on = (dt >= 0) & (dt < e.dur)
            if not on.any():
                continue
            if e.kind == "person_in_front_of_camera":
                c0, w, r0 = e.p["c0"], e.p["w"], e.p["r0"]
                mask = ((cc >= c0) & (cc < c0 + w) & (rr >= r0)).astype(float)
                mask = np.clip(mask * 0.9 + 0.1 * np.roll(mask, 1, 1), 0, 0.95)
                fr[on] = fr[on] * (1 - mask) + e.p["temp"] * mask
            elif e.kind == "hot_object_in_view":
                bg = np.median(fr[on], axis=(1, 2))
                temp = bg + (e.p["t0"] - bg) * np.exp(-dt[on] / e.p["tau"])
                w = np.exp(-((rr - e.p["r"]) ** 2 + (cc - e.p["c"]) ** 2) / 1.2) * 0.85
                fr[on] = fr[on] * (1 - w[None]) + temp[:, None, None] * w[None]
            elif e.kind == "sun_through_window":
                prog = dt[on] / e.dur
                strength = e.p["peak"] * np.sin(np.pi * prog)
                cpos = e.p["c0"] + 20 * prog
                patch = np.exp(-((rr - e.p["r0"]) ** 2) / 40.0)[None] * np.exp(
                    -((cc[None] - cpos[:, None, None]) ** 2) / 30.0)
                fr[on] += strength[:, None, None] * patch
        return fr.astype(v.dtype)

    def _room(self, t, v, act):
        r = v.astype(np.float64).copy()
        w = self._slow(self._weather, t)
        r[:, 0] += w
        r[:, 1] -= 1.5 * w
        for e in act:
            if e.kind == "door_open":
                dt = t - e.start
                on = (dt >= 0) & (dt < e.dur)
                if on.any():
                    shape = (1 - np.exp(-dt[on] / 60.0)) * np.exp(-dt[on] / 600.0)
                    r[on, 0] -= e.p["drop"] * shape
                    r[on, 1] += e.p["rh"] * shape
        return r.astype(v.dtype)
