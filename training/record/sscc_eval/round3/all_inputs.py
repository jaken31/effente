"""hyb60 over every sensor the device has: the round-3 model (round3_model.Round3Model: one PCA per sensor group,
groups joined by the highest) fed with the device's own per-second readings (effente.features, 285 names).

Groups, by reading name (the device's four sensors):
  imu          vib_* (without vib_gravity_*), slow_*, knock_*, gyr_*   the accelerometer and the gyro
  temperature  imu_temp_c, trend_heat_rise_per_min, trend_heat_swing_10m: only when the learning recording is
               LONG_S or longer (a short recording has seen one temperature only, and the room moving by a degree
               an hour later alarms: seen on the board, 14 Nov; tried again here with the rise alone, 28 % false
               alarms). Until then heat is effente/safety.py's sudden_heat
  sound        snd_*   (the 46 readings the ESP32 sends today; the 181 of hyb60's own layout when it sends them)
  thermal      th_*    (the thermal camera; never run with a camera: untested)
Left out (they follow the hour, the device's position and the room, not the machine): time_*, vib_gravity_*,
env_*, the other trend_*. Sudden heat and a moved device stay with effente/safety.py as well.

Each group is its own Round3Model with its own line, and the device alarms when any group's median of the last 3
seconds is over that group's line. (One line over the highest group, as in the SSCC runs, was tried on the board's
recordings first: a restless group lifted the common line and hid a fault that only the IMU showed.) With one
group, as on SSCC, this is exactly hyb60. Each line is set a little higher the more groups there are
(q = 1 - (1 - Q) / groups), so the false alarms of all groups together stay near one group's.

A group is used when at least MIN_PRESENT of the learning rows have it. A reading with no value (a sensor away for
a moment) takes its learning median, so a missing sensor never alarms here; the status says it is missing."""
import json

import numpy as np

import round3_model as RM

GROUPS = (("imu", ("vib_", "slow_", "knock_", "gyr_")),
          ("temperature", ("trend_heat_rise_per_min", "trend_heat_swing_10m", "imu_temp_c")),
          ("sound", ("snd_",)), ("thermal", ("th_",)))
LEFT_OUT = ("time_", "vib_gravity_", "env_")
LONG_S = 6 * 3600.0  # from this much learning on, the temperature is a group
Q = 0.995  # hyb60's line: this share of normal seconds under it, all groups together
MIN_PRESENT = 0.9
FORMAT = "effente-hyb60-all-inputs-2"


class AllInputs:
    def __init__(self, names, **settings):
        self.names, self.settings = list(names), settings

    def learn(self, rows, seconds=None):
        X = np.asarray(rows, float)
        short = (len(X) if seconds is None else seconds) < LONG_S
        present = np.isfinite(X).mean(0)
        self.group_names, self.cols = [], []
        for g, prefixes in GROUPS:
            c = [i for i, n in enumerate(self.names) if n.startswith(prefixes) and not n.startswith(LEFT_OUT)
                 and present[i] >= MIN_PRESENT]
            if c and not (short and g == "temperature"):
                self.group_names.append(g)
                self.cols.append(np.array(c))
        q = 1.0 - (1.0 - self.settings.get("q", Q)) / max(len(self.cols), 1)
        self.fill = [np.nanmedian(X[:, c], 0) for c in self.cols]
        self.models = [RM.Round3Model(**{**self.settings, "q": q, "groups": [np.arange(len(c))]}).learn(self._clean(X, k))
                       for k, c in enumerate(self.cols)]
        self.reset()
        return self

    def _clean(self, X, k):
        S = np.atleast_2d(np.asarray(X, float))[:, self.cols[k]]
        return np.where(np.isfinite(S), S, self.fill[k])

    @property
    def lines(self):
        return {g: m.line for g, m in zip(self.group_names, self.models)}

    def reset(self):
        for m in self.models:
            m.reset()

    def levels(self, rows):
        """(n, groups): each group's score over its own line, second by second (no median of 3)."""
        return np.stack([m.scores(self._clean(rows, k)) / m.line for k, m in enumerate(self.models)], 1)

    def judge(self, row, margin=1.0):
        """alarm: any group's median of its last 3 scores over its line x margin. level: the highest group's.
        which: that group. groups: every group's level. away: groups with no reading this second."""
        r = np.asarray(row, float)
        lv, away = {}, []
        for k, (g, m) in enumerate(zip(self.group_names, self.models)):
            if not np.isfinite(r[self.cols[k]]).any():
                away.append(g)
            lv[g] = m.judge(self._clean(r, k)[0], margin)["level"]
        which = max(lv, key=lv.get)
        return {"alarm": bool(lv[which] > margin), "level": lv[which], "which": which, "groups": lv, "away": away}

    def save(self, path):
        for g, m in zip(self.group_names, self.models):
            m.save(f"{path}.{g}.npz")
        with open(str(path) + ".json", "w", encoding="utf-8") as f:
            json.dump({"format": FORMAT, "groups": self.group_names, "fill": [x.tolist() for x in self.fill],
                       "names": [[self.names[i] for i in c] for c in self.cols]}, f)

    @classmethod
    def load(cls, path, names):
        with open(str(path) + ".json", encoding="utf-8") as f:
            d = json.load(f)
        if d["format"] != FORMAT:
            raise ValueError(d["format"])
        a = cls(names)
        a.group_names = d["groups"]
        a.cols = [np.array([a.names.index(n) for n in c]) for c in d["names"]]
        a.fill = [np.array(x) for x in d["fill"]]
        a.models = [RM.Round3Model.load(f"{path}.{g}.npz") for g in a.group_names]
        return a
