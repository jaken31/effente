"""Was the sensor head on the machine? So that what it read while off is not learned as this machine's normal.

The head is held by a magnet and can come off: pulled by its cable, knocked, or shaken loose. What it reads
afterwards (a fall, the floor, a head rocking on its magnet) is not the machine, and a recording that keeps it
gives a wrong normal. This looks only at the accelerometer's own readings in the feature rows (COLUMNS: the share
of each window in free fall, and gravity on x, y and z) and says, row by row:

  on      nothing against it
  fell    free fall: over FELL_FRAC of a window near 0 g. It stays "fell" until the head is seen lifted (tilted
          over MOVED_DEG, SETTLE_S or more after the fall) and then back as it was: a head that lands flat on the
          floor reads the same tilt as before, and only being picked up and put back says it is on again
  moved   gravity's direction over MOVED_DEG (effente.safety's, 10 degrees) from where the recording started
  loose   the head rocking or creeping: its tilt WOBBLE_DEG or more from a second earlier, in WOBBLE_SHARE of
          the last WOBBLE_WINDOW_S
  sensor  gravity's size outside effente.safety.GRAVITY_G: tumbling, or samples that make no sense

Where the recording started is the head's tilt over its first REF_S. A press comes before the head is on the
machine as often as after, so for the first PLACING_S a change of tilt (with no fall) is the head still being
put on: its place is taken again from there, and attached_rows cuts what came before ("placing").

One implementation, used two ways: Attachment judges a row at a time (the device, while it records), and
attached_rows runs the same over a whole recording and gives the rows to learn from, with BEFORE_S cut before
each time off (a head rattles before it lets go) and AFTER_S after (a hand is still on it).

What it cannot see:
  - a sensor that doesn't measure gravity (piezo): nothing is checked, every row counts as on ("checked" false)
  - a head rattling on its magnet with no change of tilt: its gravity average doesn't move. That looks like the
    machine's own vibration, and nothing here tells them apart
  - a head put back more than MOVED_DEG off where it was: it stays "moved" to the end of the recording
  - a machine that itself shakes through 0 g or rocks: if the recording's first REF_S already has free fall, the
    fall check is off; if "loose" is over MOSTLY of the rows, that is how this machine is and none is cut for it
  - a recording whose head is off for its first PLACING_S and put on later: where it lay is taken as its place

Every number here is a starting value. Tested on simulated rows only: unverified on the real head or a machine.
"""

from __future__ import annotations

import math
from collections import deque

import numpy as np

from effente import features as F
from effente import safety as SF

COLUMNS = ("vib_freefall_frac", "vib_gravity_x", "vib_gravity_y", "vib_gravity_z")
STATES = ("on", "fell", "moved", "loose", "sensor")  # and "placing", from attached_rows only
FELL_FRAC = 0.05  # as device.intake.recorder's device_dropped: 50 ms of a 1 s window, a fall of about 1 cm
MOVED_DEG = SF.MOVED_DEG
REF_S, REF_MIN_ROWS = 10.0, 5  # where the head was put: its tilt over the recording's first seconds
PLACING_S = 60.0  # a change of tilt this soon is the head still being put on (under the device's 2 minute least)
SETTLE_S = 3.0  # after a fall, a tilt this much later is someone lifting it, not the tumble
WOBBLE_DEG, WOBBLE_STEP_S, WOBBLE_WINDOW_S, WOBBLE_SHARE = 2.0, 1.0, 10.0, 0.3
BEFORE_S, AFTER_S = 5.0, 5.0
MOSTLY = 0.5
SCREEN = {"fell": "HEAD FELL OFF", "moved": "DEVICE MOVED", "loose": "HEAD LOOSE"}  # "sensor" is safety's to say
WHAT = {"fell": "the head fell", "moved": "the head was not where it started", "loose": "the head was rocking",
        "sensor": "the accelerometer's readings made no sense", "placing": "the head was still being put on"}


def _angle(a, b) -> float:
    """Degrees between two unit vectors."""
    return math.degrees(math.acos(max(-1.0, min(1.0, a[0] * b[0] + a[1] * b[1] + a[2] * b[2]))))


class Attachment:
    """Judges rows in time order. update() returns the row's state; .state, .angle (degrees from the start,
    NaN until known), .checked (None until the first REF_S are in, then whether gravity is measured) and
    .placed_at (None, or the time the head's place was taken again: it was still being put on before)."""

    def __init__(self):
        self.state, self.angle, self.checked = "on", math.nan, None
        self.ref = None
        self.fall_check = True
        self._first: list[tuple[float, float, float, float]] = []  # (fall, gx, gy, gz) of the first REF_S
        self._t_first = self._t_start = self.placed_at = None
        self._down, self._lifted, self._fell_at = False, False, -math.inf
        self._dirs: deque = deque()  # (t, unit gravity), the last WOBBLE_STEP_S and a little
        self._steps: deque = deque()  # (t, tilt changed by WOBBLE_DEG in a step), the last WOBBLE_WINDOW_S
        self._steps_over = 0

    def update_row(self, t: float, x) -> str:
        """One feature row (the current feature list)."""
        return self.update(t, *(float(x[F.INDEX[c]]) for c in COLUMNS))

    def update(self, t: float, fall: float, gx: float, gy: float, gz: float) -> str:
        self.state = self._judge(float(t), fall, gx, gy, gz)
        return self.state

    def _judge(self, t, fall, gx, gy, gz) -> str:
        if self.checked is False:
            return "on"
        size = math.sqrt(gx * gx + gy * gy + gz * gz)
        if not math.isfinite(size):  # no accelerometer reading in this row: nothing to say
            return "on"
        if self.checked is None:  # still finding where the head was put
            if self._t_first is None:
                self._t_first = t
            if self._t_start is None:
                self._t_start = t
            self._first.append((fall, gx, gy, gz))
            if t - self._t_first >= REF_S and len(self._first) >= REF_MIN_ROWS:
                a = np.array(self._first, float)
                g = np.median(a[:, 1:], 0)
                n = float(np.linalg.norm(g))
                self.checked = n > 0.5  # an accelerometer that shows gravity (an MPU, not a piezo sensor)
                if self.checked:
                    self.ref = tuple(float(v) for v in g / n)
                    falls = a[:, 0][np.isfinite(a[:, 0])]
                    # in free fall while it was being put on: this machine shakes through 0 g
                    self.fall_check = not (len(falls) and float(np.median(falls)) > FELL_FRAC / 2)
                self._first = []
            return "on"
        falling = self.fall_check and math.isfinite(fall) and fall > FELL_FRAC
        sized = SF.GRAVITY_G[0] <= size <= SF.GRAVITY_G[1]
        unit = (gx / size, gy / size, gz / size) if size > 0 else self.ref
        self.angle = _angle(unit, self.ref) if size > 0 else math.nan
        away = not sized or self.angle > MOVED_DEG
        if falling:
            self._down, self._fell_at, self._lifted = True, t, False
            self._forget_wobble()
            return "fell"
        if self._down:
            if away:
                if t - self._fell_at >= SETTLE_S:
                    self._lifted = True
                return "fell"
            if not self._lifted:
                return "fell"
            self._down = False  # lifted and put back as it was
        elif sized and self.angle > MOVED_DEG and t - self._t_start <= PLACING_S:
            # still being put on: its place is where it comes to rest, from here
            self.checked, self.ref, self.angle = None, None, math.nan
            self._first, self._t_first, self.placed_at = [(fall, gx, gy, gz)], t, t
            self._forget_wobble()
            return "on"
        if not sized:
            self._forget_wobble()
            return "sensor"
        if self.angle > MOVED_DEG:
            self._forget_wobble()
            return "moved"
        return "loose" if self._wobbling(t, unit) else "on"

    def _forget_wobble(self):
        self._dirs.clear()
        self._steps.clear()
        self._steps_over = 0

    def _wobbling(self, t, unit) -> bool:
        d = self._dirs
        while len(d) >= 2 and d[1][0] <= t - WOBBLE_STEP_S + 1e-9:
            d.popleft()
        if d and d[0][0] <= t - WOBBLE_STEP_S + 1e-9 and t - d[0][0] <= 2 * WOBBLE_STEP_S:
            over = _angle(unit, d[0][1]) >= WOBBLE_DEG
            self._steps.append((t, over))
            self._steps_over += over
        d.append((t, unit))
        s = self._steps
        while s and t - s[0][0] > WOBBLE_WINDOW_S:
            self._steps_over -= s.popleft()[1]
        return bool(s) and t - s[0][0] >= 0.8 * WOBBLE_WINDOW_S and self._steps_over >= WOBBLE_SHARE * len(s)


def attached_rows(t, columns: dict) -> tuple[np.ndarray, dict]:
    """The rows of one recording to learn from. t: the rows' times in seconds; columns: {name: values} with
    COLUMNS (a missing one counts as not measured). Returns (keep, report): keep is a bool for every row, in
    the order given; report says what was cut and why, with "warnings" and "warnings_screen" in the manner of
    effente.safety.learn_baseline. One recording at a time: each has its own place for the head."""
    t = np.asarray(t, float)
    n = len(t)
    cols = [np.asarray(columns[c], float) if c in columns else np.full(n, np.nan) for c in COLUMNS]
    order = np.argsort(t, kind="stable")
    ts = t[order]
    track = Attachment()
    states = np.empty(n, object)
    for i, (tt, f, gx, gy, gz) in enumerate(zip(ts.tolist(), *(c[order].tolist() for c in cols))):
        states[i] = track.update(tt, f, gx, gy, gz)
    report = {"checked": bool(track.checked), "rows": int(n), "rows_cut": 0, "seconds_off": {}, "times_off": [],
              "warnings": [], "warnings_screen": [],
              "note": "effente.attachment: starting values, unverified on the real head"}
    if not track.checked or not n:
        return np.ones(n, bool), report
    if not track.fall_check:
        report["warnings"].append("free fall was already read while the head was being put on: falls were not looked for")
    if track.placed_at is not None:
        states[ts < track.placed_at] = "placing"
    loose = states == "loose"
    if loose.mean() > MOSTLY:  # rocking most of the time: that is this machine, not the head
        states[loose] = "on"
        report["warnings"].append(f"the head's tilt wobbled for {100 * loose.mean():.0f} % of the learning: taken as "
                                  f"the machine's own movement and learned from; check the head sits firmly")
        report["warnings_screen"].append("HEAD WOBBLED")
    off = states != "on"
    keep = ~off
    if off.any():
        edge = np.flatnonzero(np.diff(np.r_[False, off, False].astype(int)))
        step = float(np.median(np.diff(ts))) if n > 1 else 1.0
        for a, b in zip(edge[::2], edge[1::2]):  # rows a..b-1 are off
            keep[np.searchsorted(ts, ts[a] - BEFORE_S, "left"):np.searchsorted(ts, ts[b - 1] + AFTER_S, "right")] = False
            what = next(s for s in ("fell", "moved", "sensor", "loose", "placing") if (states[a:b] == s).any())
            if len(report["times_off"]) < 20:
                report["times_off"].append({"what": what, "at_s": round(float(ts[a] - ts[0]), 1),
                                            "for_s": round(float(ts[b - 1] - ts[a]) + step, 1)})
            report["seconds_off"][what] = round(report["seconds_off"].get(what, 0.0) + float(ts[b - 1] - ts[a]) + step, 1)
        report["rows_cut"] = int((~keep).sum())
        why = ", ".join(f"{WHAT[k]} ({v:.0f} s)" for k, v in report["seconds_off"].items())
        report["warnings"].append(f"{report['rows_cut']} of {n} rows were not learned from: {why}. "
                                  f"Seat the head firmly and learn again if that was much of it")
        report["warnings_screen"].append("HEAD CAME OFF")
    out = np.empty(n, bool)
    out[order] = keep
    return out, report


def attached_rows_of(t, X, names) -> tuple[np.ndarray, dict]:
    """attached_rows for a matrix of feature rows with its column names."""
    names = [str(c) for c in names]
    X = np.asarray(X)
    return attached_rows(t, {c: X[:, names.index(c)] for c in COLUMNS if c in names})


def periods_kept(period_t, period_s: float, t, keep) -> np.ndarray:
    """For rows that each summarise a period (a minute's medians: effente.trainer.load_for_training), which to
    learn from: those with no cut one-a-second row in [period_t, period_t + period_s). t, keep: the
    one-a-second rows' times and attached_rows' answer for them."""
    cut = np.sort(np.asarray(t, float)[~np.asarray(keep, bool)])
    period_t = np.asarray(period_t, float)
    return np.searchsorted(cut, period_t, "left") == np.searchsorted(cut, period_t + period_s, "left")
