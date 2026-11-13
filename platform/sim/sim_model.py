"""Simulated device model: announcement, records, check-ins and problems.

Everything here is MADE UP. The spectra are illustrative shapes chosen to look
plausible on the dashboard, not measurements of any fan, and the "model" is a
hand-written table of cases, not the device runtime (effente/model_runtime.py)
or the signal pipeline from the TRD. It must never be presented as either.
Every message built here carries "simulated": true.

Messages follow platform/CONTRACT.md version 3: message 1 (model), message 2
(record) and message 3 (check-in). Names of fault types, readings and
problems are the contract's; their texts are copied from effente/ so the
dashboard shows the words the real device would send.

The case decides the diagnosis. Problems are separate, as on the real
device: the safety checks run apart from the model, so any problem can be on
with any case and in any mode (`ProblemChecks`).
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

MODEL_VERSION = "sim-3"
FEATURES_VERSION = "sim-features-3"
DEVICE_ID = "effente-sim-01"
MACHINE_ID = "demo-fan-1"
LIBRARY_ENTRY = "generic_rotating"
SENSITIVITY = 0.5  # the default; the dashboard can change it (contract section 7)
ALARM_LINE = 1.0  # alarm_line(SENSITIVITY)

# How far beyond normal an outside-normal problem must be at sensitivity 0.5, as
# effente/safety.py OUTSIDE_NORMAL: times normal for vibration, degC, dB.
OUTSIDE_NORMAL = {"vibration_above_normal": 2.0, "hotter_than_normal": 15.0, "louder_than_normal": 6.0}

# 20 vibration bands, 5 to 500 Hz, spaced evenly on a log scale (as effente/features.py VIB_EDGES_HZ).
SPECTRUM_EDGES_HZ = [round(5.0 * 100.0 ** (i / 20), 2) for i in range(21)]
TURN_HZ = 30.0  # demo fan taken as about 1,800 RPM (assumed, not measured)
UNKNOWN_HZ = 300.0  # the made-up "never seen" tone

WATCHING = "watching"
MODES = [WATCHING, "display", "calibrating", "recording", "training"]  # all but watching send check-ins

CASES = ["normal", "unbalance", "looseness", "unrecognised", "stopped", "warming_up"]
# Each judged case's typical smoothed unknown_score. The diagnosis compares this
# with the alarm line, so a case does not flip each second near the line, as the
# device's smoothing keeps it from flipping. Made up, like everything here.
TYPICAL_SCORE = {"normal": 0.4, "unbalance": 2.4, "looseness": 2.0, "unrecognised": 1.65}
DIAGNOSIS = {
    "normal": "normal",
    "unbalance": "unbalance_like",
    "looseness": "looseness_or_rattle",
    "unrecognised": "unrecognised_pattern",
    "stopped": "stopped",
    "warming_up": "warming_up",
}
ABNORMAL_CASES = ("unbalance", "looseness", "unrecognised")

# The fault types this stand-in can name, with effente/fault_types.py's texts.
FAULT_TYPES = [
    {"name": "vibration_too_high", "text": "overall vibration well above this machine's normal", "group": "vibration"},
    {"name": "unbalance_like", "text": "once-per-turn vibration grown (unbalance, a bent shaft, a lost blade)",
     "group": "vibration"},
    {"name": "looseness_or_rattle",
     "text": "many harmonics, half-order and rattling (looseness, a soft foot, a cracked mount)", "group": "vibration"},
]
FAULT_TEXT = {t["name"]: t["text"] for t in FAULT_TYPES}
UNRECOGNISED_REASON = "abnormal, no known fault type fits"

# Part of a model's feature table, with effente/features.py's texts.
FEATURES = [
    {"name": "vib_1x", "text": "vibration once per turn", "unit": "log"},
    {"name": "vib_2x", "text": "vibration twice per turn", "unit": "log"},
    {"name": "vib_harm_3_10", "text": "vibration at 3-10x running speed (rattle)", "unit": "log"},
    {"name": "vib_half", "text": "vibration at half running speed", "unit": "log"},
    {"name": "vib_crest", "text": "vibration knocks (peak over rms)", "unit": "ratio"},
    {"name": "vib_kurtosis", "text": "vibration impulsiveness", "unit": "ratio"},
    {"name": "vib_band_17", "text": "vibration 251-315 Hz", "unit": "log"},
    {"name": "vib_velocity_mm_s", "text": "vibration velocity, 10-250 Hz rms (ISO 10816-like, partial band)",
     "unit": "mm_s"},
    {"name": "snd_level_db", "text": "sound level", "unit": "db"},
    {"name": "th_max_c", "text": "hottest point in view", "unit": "c"},
]
FEATURE_NAMES = [f["name"] for f in FEATURES]

# Problems, in the order of the contract's names table (limits first).
# name: (kind, text from effente/safety.py, typical value, jitter)
PROBLEMS = {
    "vibration_over_limit": ("limit", "vibration over 11 mm/s, too high for practically any machine", 12.4, 0.4),
    "knocking": ("limit", "knocking: repeated impacts in the vibration", 9.1, 0.5),
    "too_hot": ("limit", "over 100 C", 104.5, 0.6),
    "heating_fast": ("limit", "heating up more than 8 C a minute", 9.6, 0.4),
    "repeated_jolts": ("limit", "5 or more sudden jolts in 10 minutes", 6, 0),
    "vibration_above_normal": ("outside_normal", "vibrating well beyond its normal", 2.3, 0.1),
    "hotter_than_normal": ("outside_normal", "hotter than its normal", 18.0, 0.5),
    "louder_than_normal": ("outside_normal", "louder than its normal", 7.0, 0.4),
}
PROBLEM_NAMES = list(PROBLEMS)
HOLD_S = 10.0  # a problem shows once it has held this long (as effente/safety.py)
CLEAR_SHARE = 0.8  # and clears once it has been gone for this share of HOLD_S


def alarm_line(sensitivity: float) -> float:
    """The contract's alarm_line: 4 at 0, 1 at the default 0.5, 0.25 at 1."""
    return round(4.0 ** (1.0 - 2.0 * sensitivity), 4)


def margins(sensitivity: float) -> dict[str, float]:
    """How far beyond normal each outside-normal problem must be, as effente/safety.py
    margins(): 0.5 gives OUTSIDE_NORMAL, each 0.25 up halves the departure."""
    k = 4.0 ** (2.0 * sensitivity - 1.0)
    vib = OUTSIDE_NORMAL["vibration_above_normal"]
    return {
        "vibration_above_normal": 1.0 + (vib - 1.0) / k,
        "hotter_than_normal": OUTSIDE_NORMAL["hotter_than_normal"] / k,
        "louder_than_normal": OUTSIDE_NORMAL["louder_than_normal"] / k,
    }


def judged_abnormal(case: str, sensitivity: float = SENSITIVITY) -> bool:
    """Whether the model calls this case abnormal at this sensitivity."""
    score = TYPICAL_SCORE.get(case)
    return score is not None and score > alarm_line(sensitivity)


def now_iso() -> str:
    return iso(datetime.now(timezone.utc))


def iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_iso(text) -> datetime | None:
    if not isinstance(text, str):
        return None
    try:
        t = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


# --------------------------------------------------------------- spectrum

def _band_of(hz: float) -> int:
    for i in range(20):
        if SPECTRUM_EDGES_HZ[i] <= hz < SPECTRUM_EDGES_HZ[i + 1]:
            return i
    return 19


def make_spectrum(case: str, rng: random.Random) -> list[float]:
    """20 vibration band levels, log10 band power. Illustrative shapes only."""
    if case == "stopped":
        return [round(-6.6 + rng.uniform(-0.1, 0.1), 3) for _ in range(20)]
    s = [-5.0 - 0.02 * i + rng.uniform(-0.08, 0.08) for i in range(20)]
    s[_band_of(TURN_HZ)] += 0.6  # the fan's own once-per-turn line
    if case == "unbalance":
        s[_band_of(TURN_HZ)] += rng.uniform(1.4, 1.7)
        s[_band_of(2 * TURN_HZ)] += 0.3
    elif case == "looseness":
        for i in range(20):
            s[i] += rng.uniform(0.3, 0.5)  # raised floor
        for k in (2, 3, 4, 5, 6):  # harmonics that come and go
            if rng.random() < 0.7:
                s[_band_of(k * TURN_HZ)] += rng.uniform(0.4, 1.0)
        s[_band_of(TURN_HZ / 2)] += 0.6
    elif case == "unrecognised":
        s[_band_of(UNKNOWN_HZ)] += rng.uniform(1.6, 1.9)
    return [round(x, 3) for x in s]


# --------------------------------------------------------------- messages

def model_announcement(device_id: str = DEVICE_ID, machine_id: str = MACHINE_ID,
                       sensitivity: float = SENSITIVITY) -> dict:
    """Contract message 1."""
    return {
        "device_id": device_id,
        "machine_id": machine_id,
        "model_version": MODEL_VERSION,
        "features_version": FEATURES_VERSION,
        "library_entry": LIBRARY_ENTRY,
        "source": "calibrated",
        "threshold": 1.0,
        "sensitivity": sensitivity,
        "fault_types": [dict(t, validated=False) for t in FAULT_TYPES],
        "features": [dict(f) for f in FEATURES],
        "spectrum_edges_hz": list(SPECTRUM_EDGES_HZ),
        "simulated": True,
    }


def model_file() -> dict:
    """Model file for the agent (device/agent reads `features` as {name, sentence}).

    Not a contract message: the TRD's feature table shape, with no weights and
    no hidden units (`unit_indices` is empty).
    """
    return {
        "model_version": MODEL_VERSION,
        "features": [{"name": f["name"], "unit_indices": [], "sentence": f["text"]} for f in FEATURES],
        "fault_types": [dict(t) for t in FAULT_TYPES],
        "simulated": True,
    }


def _r(rng: random.Random, lo: float, hi: float) -> float:
    return round(rng.uniform(lo, hi), 2)


def diagnose(case: str, rng: random.Random, abnormal_minutes: int = 1, sensitivity: float = SENSITIVITY) -> dict:
    """The model's side of a record for a case, judged at a sensitivity.

    The case sets the readings and the score; the sensitivity only moves the
    line. An abnormal case under the line reads as normal, and a normal case
    over it as an unrecognised pattern.
    """
    out = _case(case, rng, abnormal_minutes)
    abnormal = judged_abnormal(case, sensitivity)
    if case in ABNORMAL_CASES and not abnormal:
        return {"diagnosis": "normal", "confidence": out["confidence"], "unknown_score": out["unknown_score"],
                "reason": "", "features": out["features"], "abnormal_minutes": 0,
                "out_of_range": out["out_of_range"], "note": out["note"]}
    if case == "normal" and abnormal:
        diagnosis = DIAGNOSIS["unrecognised"]
        out.update(diagnosis=diagnosis, diagnosis_this_minute=diagnosis, reason=UNRECOGNISED_REASON,
                   confidence=0.3, fault_types={}, abnormal_minutes=max(1, abnormal_minutes))
    return out


def _case(case: str, rng: random.Random, abnormal_minutes: int) -> dict:
    """The case's own readings and diagnosis, at the default sensitivity."""
    if case not in DIAGNOSIS:
        raise ValueError(f"unknown case {case!r}")
    diagnosis = DIAGNOSIS[case]
    if case == "warming_up":
        return {"diagnosis": diagnosis, "confidence": 0.0, "unknown_score": None, "reason": "", "features": {},
                "abnormal_minutes": 0, "out_of_range": 0, "note": "the first minute is still being summarised"}
    if case == "stopped":
        return {"diagnosis": diagnosis, "confidence": 1.0, "unknown_score": None, "reason": "", "features": {},
                "abnormal_minutes": 0, "out_of_range": 0,
                "note": "the machine is not running; nothing is judged while it is stopped"}
    if case == "normal":
        score = _r(rng, 0.25, 0.55)
        features = {"vib_1x": _r(rng, 0.4, 1.1), "snd_level_db": _r(rng, -0.9, -0.3), "th_max_c": _r(rng, 0.2, 0.7)}
        return {"diagnosis": diagnosis, "confidence": round(1.0 - score, 2), "unknown_score": score, "reason": "",
                "features": features, "abnormal_minutes": 0, "out_of_range": 0, "note": ""}
    if case == "unbalance":
        out = {"confidence": _r(rng, 0.66, 0.76), "unknown_score": _r(rng, 2.2, 2.6),
               "features": {"vib_1x": _r(rng, 6.8, 7.6), "vib_velocity_mm_s": _r(rng, 2.8, 3.4),
                            "vib_2x": _r(rng, 1.1, 1.6)},
               "fault_types": {"unbalance_like": _r(rng, 8.6, 9.6), "vibration_too_high": _r(rng, 3.9, 4.5)},
               "out_of_range": 0, "reason": FAULT_TEXT[diagnosis]}
    elif case == "looseness":
        out = {"confidence": _r(rng, 0.58, 0.68), "unknown_score": _r(rng, 1.8, 2.2),
               "features": {"vib_harm_3_10": _r(rng, 5.6, 6.6), "vib_half": _r(rng, 4.0, 4.8),
                            "vib_crest": _r(rng, 3.0, 3.6)},
               "fault_types": {"looseness_or_rattle": _r(rng, 7.0, 7.8), "vibration_too_high": _r(rng, 2.8, 3.4)},
               "out_of_range": 0, "reason": FAULT_TEXT[diagnosis]}
    else:  # unrecognised
        out = {"confidence": 0.3, "unknown_score": _r(rng, 1.5, 1.8),
               "features": {"vib_band_17": _r(rng, 6.4, 7.2), "vib_kurtosis": _r(rng, 1.6, 2.2),
                            "snd_level_db": _r(rng, 1.0, 1.4)},
               "fault_types": {"vibration_too_high": _r(rng, 1.8, 2.4)},
               "out_of_range": 1, "reason": UNRECOGNISED_REASON}
    out.update(diagnosis=diagnosis, diagnosis_this_minute=diagnosis, abnormal_minutes=max(1, abnormal_minutes),
               note="1 reading far outside anything seen while learning" if out["out_of_range"] else "")
    return out


def is_wrong(diagnosis, problems: list[dict]) -> bool:
    """The contract's `wrong`: a fault type or unrecognised_pattern, or any problem."""
    return bool(problems) or diagnosis not in (None, "normal", "stopped", "warming_up")


def make_record(
    case: str,
    rng: random.Random,
    problems: list[dict] | None = None,
    device_id: str = DEVICE_ID,
    machine_id: str = MACHINE_ID,
    at: str | None = None,
    abnormal_minutes: int = 1,
    sensitivity: float = SENSITIVITY,
) -> dict:
    """Contract message 2 for one second of a case, with the problems on now, judged at `sensitivity`."""
    problems = [dict(p) for p in (problems or [])]
    model = diagnose(case, rng, abnormal_minutes, sensitivity)
    record = {
        "device_id": device_id,
        "machine_id": machine_id,
        "model_version": MODEL_VERSION,
        "at": at or now_iso(),
        "diagnosis": model["diagnosis"],
    }
    if "diagnosis_this_minute" in model:
        record["diagnosis_this_minute"] = model["diagnosis_this_minute"]
    record.update(
        confidence=model["confidence"],
        unknown_score=model["unknown_score"],
        sensitivity=sensitivity,
        alarm_line=alarm_line(sensitivity),
        reason=model["reason"],
        features=model["features"],
    )
    if "fault_types" in model:
        record["fault_types"] = model["fault_types"]
    record.update(
        abnormal_minutes=model["abnormal_minutes"],
        out_of_range=model["out_of_range"],
        note=model["note"],
        problems=problems,
        wrong=is_wrong(model["diagnosis"], problems),
        spectrum=make_spectrum(case, rng),
        simulated=True,
    )
    return record


def make_check_in(mode: str, problems: list[dict] | None = None, machine_id: str = MACHINE_ID,
                  at: str | None = None, sensitivity: float = SENSITIVITY) -> dict:
    """Contract message 3. Only limits can be on outside watching."""
    if mode not in MODES or mode == WATCHING:
        raise ValueError(f"a check-in is for a device that is not watching, not {mode!r}")
    problems = [dict(p) for p in (problems or []) if p.get("kind") == "limit"]
    return {
        "machine_id": machine_id,
        "mode": mode,
        "at": at or now_iso(),
        "wrong": bool(problems),
        "problems": problems,
        "sensitivity": sensitivity,
        "simulated": True,
    }


# --------------------------------------------------------------- problems

def make_problem(name: str, rng: random.Random, since: str) -> dict:
    kind, text, value, jitter = PROBLEMS[name]
    if jitter:
        value = round(value + rng.uniform(-jitter, jitter), 2)
    return {"name": name, "kind": kind, "text": text, "value": value, "since": since}


class ProblemChecks:
    """Stand-in for the device's safety checks (effente/safety.py), driven by hand.

    `start(name)` makes the condition true; the problem shows once it has held
    for `hold_s`. `stop(name)` makes it false; the problem clears once it has
    been gone for most of `hold_s`. Outside-normal checks run only while
    watching, so outside watching only limits are reported, and only when the
    problem's value is beyond the margin for the sensitivity (`margins`).
    Changing the sensitivity starts their hold again (`restart_outside_normal`).
    """

    def __init__(self, hold_s: float = HOLD_S):
        self.hold_s = hold_s
        self.started: dict[str, tuple[float, str]] = {}  # name -> (monotonic start, ISO start)
        self.stopped: dict[str, float] = {}  # name -> monotonic stop, while it is clearing

    def start(self, name: str, now: float, now_text: str) -> None:
        if name not in PROBLEMS:
            raise ValueError(f"unknown problem {name!r}")
        self.stopped.pop(name, None)
        self.started.setdefault(name, (now, now_text))

    def stop(self, name: str, now: float) -> None:
        if name in self.started and name not in self.stopped:
            self.stopped[name] = now

    def toggle(self, name: str, now: float, now_text: str) -> None:
        if self.wanted(name):
            self.stop(name, now)
        else:
            self.start(name, now, now_text)

    def restart_outside_normal(self, now: float, now_text: str) -> None:
        """A new sensitivity: outside-normal problems must hold again before they show."""
        for name in list(self.started):
            if PROBLEMS[name][0] != "limit" and self.wanted(name):
                self.started[name] = (now, now_text)

    def wanted(self, name: str) -> bool:
        """The condition is true now (it may not show yet)."""
        return name in self.started and name not in self.stopped

    def status(self, name: str, now: float) -> str:
        """'' (off), 'starting', 'on' or 'clearing'."""
        self._forget_cleared(now)
        if name not in self.started:
            return ""
        if name in self.stopped:
            return "clearing"
        return "on" if now - self.started[name][0] >= self.hold_s else "starting"

    def _forget_cleared(self, now: float) -> None:
        for name, stopped_at in list(self.stopped.items()):
            never_shown = stopped_at - self.started[name][0] < self.hold_s
            if never_shown or now - stopped_at >= self.hold_s * CLEAR_SHARE:
                del self.stopped[name]
                del self.started[name]

    def current(self, mode: str, rng: random.Random, now: float, sensitivity: float = SENSITIVITY) -> list[dict]:
        """The problems the device reports now, in the names table's order."""
        out = []
        margin = margins(sensitivity)
        for name in PROBLEM_NAMES:
            if self.status(name, now) not in ("on", "clearing"):
                continue
            if PROBLEMS[name][0] != "limit" and mode != WATCHING:
                continue
            problem = make_problem(name, rng, self.started[name][1])
            if name in margin and problem["value"] <= margin[name]:
                continue
            out.append(problem)
        return out


# --------------------------------------------------------------- replay

class Replayer:
    """Re-sends saved records now, keeping their spacing in time.

    Every time in a record (`at`, and each problem's `since`) is moved by the
    same amount, so the first record is sent at "now". `device_id` and
    `model_version` become the simulator's own, so they line up with its
    announcement; `machine_id` is kept from the file. A record is simulated
    unless it says `simulated: false` explicitly. `problems` and `wrong` are
    filled in if missing.
    """

    def __init__(self, device_id: str, machine_id: str):
        self.device_id = device_id
        self.machine_id = machine_id
        self.shift: timedelta | None = None

    def _move(self, text) -> str:
        t = parse_iso(text)
        if t is None or self.shift is None:
            return now_iso()
        return iso(t + self.shift)

    def record(self, src: dict) -> dict:
        first = parse_iso(src.get("at"))
        if self.shift is None and first is not None:
            self.shift = datetime.now(timezone.utc) - first
        problems = src.get("problems") if isinstance(src.get("problems"), list) else []
        out = dict(src)
        out.update(
            device_id=self.device_id,
            machine_id=src.get("machine_id") or self.machine_id,
            model_version=MODEL_VERSION,
            at=self._move(src.get("at")),
            problems=[dict(p, since=self._move(p.get("since"))) for p in problems if isinstance(p, dict)],
            simulated=src.get("simulated") is not False,
        )
        out.setdefault("diagnosis", "normal")
        if not isinstance(out.get("wrong"), bool):
            out["wrong"] = is_wrong(out["diagnosis"], out["problems"])
        return out


def sample_records() -> list[dict]:
    """What sample_replay.jsonl holds: one record a second, seed 7.

    Normal; then too_hot comes on while the model still says normal; then
    unbalance with too_hot still on; then too_hot alone while the machine is
    stopped; then an unrecognised pattern with no problem.
    """
    rng = random.Random(7)
    t0 = datetime(2026, 11, 14, 16, 0, 0, tzinfo=timezone.utc)
    hot_since = iso(t0 + timedelta(seconds=2))
    plan = [("normal", False)] * 2 + [("normal", True)] * 2 + [("unbalance", True)] * 2 + \
        [("stopped", True)] * 2 + [("unrecognised", False)] * 2
    out = []
    for i, (case, hot) in enumerate(plan):
        problems = [make_problem("too_hot", rng, hot_since)] if hot else []
        out.append(make_record(case, rng, problems, at=iso(t0 + timedelta(seconds=i))))
    return out
