"""The OLED dashboard: what the 128x64 screen shows in each mode, as a layout for the sketch.

The sketch (device/app/effente/sketch/oled_ui.h) draws the layout: one widget a line, stacked in
8-pixel rows, 8 rows in all. This module only decides what goes where; it draws nothing. The live
numbers ({vib}, {peak}, {temp}) and the vibration trace ({spark}, the last 12.8 s) are filled in by the
microcontroller from every sample, so they move at the screen's own rate.

Every screen answers the technician's first question in the biggest text on it (NORMAL, the fault
type, INSPECT, the learning clock), then shows the evidence: in an alarm, the readings that moved
most, each in spreads of its own normal variation (PRD P3, P4). Fault type names are shown as
"possible" (PRD P5): none has been checked on real faults yet.

Preview every screen on a laptop: python -m device.app.preview
"""
from __future__ import annotations

import math

SIGMA = "\x7f"  # drawn as a sigma by the sketch's font
WIDTH = 21  # characters a row
BIG = 10  # characters a row in big text
ROWS = 8
MAX_BYTES = 220  # a layout must fit the Bridge's RPC buffer (256 bytes) with room to spare
SPREAD_FULL = 12.0  # a moved reading's meter is full at this many spreads
SPREAD_COUNTS = 6.0  # a reading this many spreads from usual counts as evidence on its own (TRD); the meter's tick

LIVE_VIB = "vib {vib} mg|pk {peak}g"
LIVE_TEMP = "{temp} C"

# The fault type, in big letters (10 at most). The full name is in effente/fault_types.py.
FAULT_BIG = {
    "vibration_too_high": "HIGH VIB", "unbalance_like": "UNBALANCE", "misalignment_like": "MISALIGNED",
    "looseness_or_rattle": "LOOSENESS", "repeating_knock": "KNOCKING", "impacts": "IMPACTS",
    "bearing_defect_like": "BEARING", "speed_change": "SPEED", "electrical_motor": "ELECTRICAL",
    "new_tone_whine_squeal": "WHINE", "grinding_or_hiss": "GRINDING", "alarm_or_beeping": "BEEPING",
    "overheating": "OVERHEAT", "heating_up_over_time": "HEATING UP", "local_hot_spot": "HOT SPOT",
    "area_running_cold": "COLD AREA", "excess_temperature_fluctuation": "TEMP SWING",
    "unsteady_vibration_or_sound": "UNSTEADY", "room_too_hot_or_humid": "ROOM",
    "device_moved": "MOVED", "camera_moved_or_blocked": "CAMERA",
}
# What follows "possible" above the big name, when the big name leaves something out.
FAULT_MORE = {"looseness_or_rattle": "or rattle", "bearing_defect_like": "bearing wear",
              "speed_change": "speed change", "electrical_motor": "motor fault",
              "room_too_hot_or_humid": "hot or humid", "device_moved": "device moved",
              "camera_moved_or_blocked": "camera view"}
FAULT_GROUP = {  # TRD: "sensing" for the device's own view, otherwise the first sensor the type relies on
    "device_moved": "sensing", "camera_moved_or_blocked": "sensing",
    "new_tone_whine_squeal": "sound", "grinding_or_hiss": "sound", "alarm_or_beeping": "sound",
    "overheating": "heat", "heating_up_over_time": "heat", "local_hot_spot": "heat", "area_running_cold": "heat",
    "excess_temperature_fluctuation": "heat", "room_too_hot_or_humid": "room",
}

# Readings, in 8 characters at most, for the meters. Anything missing falls back to its feature name.
READING = {
    "vib_1x": "1x turn", "vib_2x": "2x turn", "vib_3x": "3x turn", "vib_half": "0.5x trn",
    "vib_harm_3_10": "harmonic", "vib_velocity_mm_s": "velocity", "vib_rms_x": "vib X", "vib_rms_y": "vib Y",
    "vib_rms_z": "vib Z", "vib_crest": "crest", "vib_kurtosis": "spiky", "vib_peak_g": "peak g",
    "vib_jerk": "jerk", "vib_env_peak": "bearing", "vib_speed_hz": "speed", "vib_line_2x": "mains2x",
    "vib_peak2": "2nd peak", "gyr_rms": "rocking", "gyr_peak": "rock pk", "knock_repeat": "knocks",
    "imu_temp_c": "temp", "th_max_c": "hottest", "snd_level_db": "loudness", "snd_tone_db": "tone",
    "trend_vib_10m": "vib 10m", "trend_shocks_10m": "jolt 10m", "vib_gravity_x": "tilt X",
    "vib_gravity_y": "tilt Y", "vib_gravity_z": "tilt Z",
}


def reading_label(name: str) -> str:
    if name in READING:
        return READING[name]
    for prefix in ("vib_", "trend_", "snd_", "th_", "env_"):
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    return name.replace("_", " ")[:8]


def lr(left: str, right: str = "") -> str:
    """A text row: a left-aligned and a right-aligned part."""
    return f"{left}|{right}" if right else left


def clock(seconds: float) -> str:
    s = int(max(seconds, 0))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60:02d}:{s % 60:02d}"


def spectrum_row(bands) -> str | None:
    """The vibration bands (log10 power, 5-500 Hz) as one spectrum row, scaled to the loudest band: the
    top is the loudest band, the bottom 40 dB below it. It shows the shape, not the level."""
    vals = [float(v) for v in (bands if bands is not None else [])]
    if not vals or not all(math.isfinite(v) for v in vals):
        return None
    top = max(vals)
    return "~" + "".join(chr(ord("a") + int(round(min(max((v - (top - 4.0)) / 4.0, 0.0), 1.0) * 23))) for v in vals[:21])


def meter(label: str, value: str, fill: float, mark: float | None = None) -> str:
    f = int(round(min(max(fill, 0.0), 1.0) * 100))
    m = "" if mark is None else f"|{int(round(min(max(mark, 0.0), 1.0) * 100))}"
    return f"={label[:8]}|{value[:6]}|{f}{m}"


def moved_meters(features: dict | None, n: int = 3) -> list[str]:
    """The readings that moved most, each as a meter in spreads of its own normal variation."""
    top = sorted((features or {}).items(), key=lambda kv: -abs(kv[1]))[:max(n, 0)]
    return [meter(reading_label(k), f"{v:+.1f}{SIGMA}", abs(v) / SPREAD_FULL, SPREAD_COUNTS / SPREAD_FULL)
            for k, v in top]


def drift_meter(unknown_score: float | None, margin: float = 1.0) -> str:
    """How far the machine is from its normal. The tick, at half the bar, is the alarm line."""
    if unknown_score is None:
        return meter("drift", "--", 0.0, 0.5)
    return meter("drift", f"{unknown_score / margin:.2f}x", unknown_score / margin / 2.0, 0.5)


def problem_rows(problems, room: int) -> list[str]:
    """The no-setup checks that fired (effente.safety screen texts), as blinking bars."""
    return [f"! {p}"[:WIDTH] for p in list(problems)[:max(room, 0)]]


def height(row: str) -> int:
    if row == "{spark}":
        return 2
    return {"*": 2, "~": 3}.get(row[:1], 1)


def fit(rows: list[str]) -> list[str]:
    """Keep the layout inside the 8 screen rows and the Bridge's buffer, dropping rows from the bottom."""
    out, used = [], 0
    for r in rows:
        if used + height(r) > ROWS:
            break
        out.append(r)
        used += height(r)
    while out and len("\n".join(out).encode()) > MAX_BYTES:
        out.pop()
    return out


def _wrap(text: str, width: int = WIDTH) -> list[str]:
    words, lines, cur = (text or "").split(), [], ""
    for w in words:
        if len(cur) + len(w) + (1 if cur else 0) > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}" if cur else w
    return lines + [cur] if cur else lines


def _speed(speed_hz) -> str:
    return f"speed {speed_hz:.1f} Hz" if speed_hz else "speed --"


# ------------------------------------------------------------------ one function a screen

def streaming(now: str, speed_hz: float | None = None, bands=None, problems=()) -> list[str]:
    """Display mode: live readings, the vibration trace and the spectrum."""
    spec = spectrum_row(bands)
    bang = problem_rows(problems, 3)
    middle = bang + [""] * (3 - len(bang)) if bang else [spec] if spec else ["", "", ""]
    return fit([f"#LIVE|{now}", LIVE_VIB, "{spark}"] + middle + [lr(_speed(speed_hz), LIVE_TEMP)])


def picking(entry: str, index: int, count: int, about: str = "", problems=()) -> list[str]:
    """Picking a library entry (TRD; not in the device app yet): a press for the next, a hold to pick."""
    rows = [f"#PICK MACHINE|{index + 1}/{count}", f"*{entry.upper()[:BIG]}", about[:WIDTH],
            lr("tap: next", "hold: pick")]
    rows += problem_rows(problems, 2) or ["{spark}"]
    return fit(rows + [LIVE_VIB])


def calibrating(entry: str, done_s: float, need_s: float, fit_ok: bool | None = None) -> list[str]:
    """Calibrating a library entry to this machine (TRD; not in the device app yet)."""
    if fit_ok is False:  # TRD fit check: this machine does not look like the entry's type
        return fit([f"!NO MATCH|{entry[:10]}", "doesn't look like a", f"*{entry.upper()[:BIG]}",
                    "try generic_rotating", "or a long stay", "{spark}"])
    share = min(done_s / need_s, 1.0) if need_s > 0 else 1.0
    return fit([f"#CALIBRATING|{entry[:8]}", f"*{clock(need_s - done_s)}", f"%{int(share * 100)}|{int(share * 100)}%",
                "keep machine running", "{spark}", LIVE_VIB])


def recording(now: str, learning_s: float, min_s: float, speed_hz: float | None = None,
              message: str = "", problems=()) -> list[str]:
    """Learn mode: how long it has been learning, and whether that is enough to train yet."""
    share = min(learning_s / min_s, 1.0) if min_s > 0 else 1.0
    bar = f"%{int(share * 100)}|{clock(min_s - learning_s)} to go" if share < 1 else "%100|press: train"
    extra = (problem_rows(problems, 2) + _wrap(message))[:2]
    middle = extra + [""] * (2 - len(extra)) if extra else ["{spark}"]
    return fit([f"#LEARNING|{now}", f"*{clock(learning_s)}", bar] + middle + [LIVE_VIB, lr(_speed(speed_hz), LIVE_TEMP)])


def training(now: str, share: float, stage: str) -> list[str]:
    """Training a model on this machine alone, on the board."""
    p = int(round(min(max(share, 0.0), 1.0) * 100))
    return fit([f"#TRAINING|{now}", f"*{p}%", f"%{p}", stage[:WIDTH], "on this machine only", "{spark}"])


def watching(now: str, result: dict | None, margin: float = 1.0, problems=(), notes=(),
             speed_hz: float | None = None) -> list[str]:
    """Watching: the judgement, big, and its evidence."""
    res = result or {}
    diag = res.get("diagnosis", "warming_up")
    score = res.get("unknown_score")
    probs = list(problems)
    if diag == "unrecognised_pattern" or diag in FAULT_BIG:
        drift = f"drift {score / margin:.1f}x" if score is not None else ""
        if diag == "unrecognised_pattern":  # PRD: "unrecognised pattern, inspect"
            rows = [f"!! ALARM|{drift}", "unrecognised pattern", "*INSPECT"]
        else:
            more = FAULT_MORE.get(diag, "")
            rows = [f"!! ALARM|{drift}", lr(f"possible {more}".strip(), "" if more else FAULT_GROUP.get(diag, "vibration")),
                    f"*{FAULT_BIG[diag]}"]
        bang = problem_rows(probs, 1)
        meters = moved_meters(res.get("features"), 3 - len(bang))
        rows += meters + bang + [""] * (3 - len(meters) - len(bang))
        return fit(rows + [lr(LIVE_VIB.split("|")[0], LIVE_TEMP)])
    head = f"#WATCHING|{now}"
    tail = [LIVE_VIB, lr(_speed(speed_hz), LIVE_TEMP)]
    if probs:  # a no-setup check fired; say what the model makes of it too
        bang = problem_rows(probs, 2)
        model = {"normal": "model: normal", "stopped": "model: stopped"}.get(diag, "model: settling in")
        return fit([head] + bang + ([model] if len(bang) < 2 else []) + [drift_meter(score, margin), "{spark}"] + tail)
    big = {"normal": "NORMAL", "stopped": "STOPPED", "warming_up": "WARMING UP"}.get(diag, diag.upper()[:BIG])
    sub = {"normal": drift_meter(score, margin), "stopped": "machine not running"}.get(diag, "settling in")
    note = [f"note: {n}"[:WIDTH] for n in notes][:1]
    return fit([head, f"*{big}", sub, "{spark}"] + note + tail)
