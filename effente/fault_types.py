"""General fault types: the same names on every machine, judged against that machine's own normal.

When the normal model says a machine is no longer normal, it also says how far each prepared
reading is from what it expected (signed, in spreads of that reading's normal variation). Each
fault type below is a signature over named features: which readings move, and which way. The
type whose readings moved most is the diagnosis; if none moved enough the diagnosis is
"unrecognised pattern" with the readings that did move, never a forced label.

Signatures are starting values from the failure-mode review in device/intake/FAILURE_MODES.md,
not learned from real labelled faults (unverified). NumPy only: it runs on the device.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

MIN_SCORE = 4.0  # a type is named only if its evidence reaches 4 spreads
SOUND_ONLY_MINUTES = 10  # sound-only changes shorter than this may be the room, not the machine

_HIGH_SND = tuple(f"snd_band_{i:02d}" for i in range(23, 32))  # about 2-8 kHz


@dataclass(frozen=True)
class Member:
    names: tuple[str, ...]  # one feature, or a group (the strongest of the group counts)
    direction: int = 1  # +1 must rise, -1 must fall, 0 either way
    weight: float = 1.0


def M(*names, direction=1, weight=1.0):
    return Member(tuple(names), direction, weight)


def G(prefix, n, direction=1, weight=1.0):
    return Member(tuple(f"{prefix}{i:02d}" for i in range(n)) if prefix.endswith("_band_") else
                  tuple(f"{prefix}{r}{c}" for r in range(6) for c in range(8)), direction, weight)


@dataclass(frozen=True)
class FaultType:
    name: str
    text: str
    members: tuple[Member, ...]
    sensors: tuple[str, ...]
    sensing: bool = False  # a problem with the device or its view, not the machine


TYPES = (
    FaultType("vibration_too_high", "overall vibration well above this machine's normal",
              (M("vib_rms_x", "vib_rms_y", "vib_rms_z"), M("vib_velocity_mm_s"), M("trend_vib_10m", weight=0.5)),
              ("vibration",)),
    FaultType("unbalance_like", "once-per-turn vibration grown (unbalance, a bent shaft, a lost blade)",
              (M("vib_1x", weight=2.0), M("vib_velocity_mm_s", weight=0.5)), ("vibration",)),
    FaultType("misalignment_like", "twice-per-turn vibration grown (misalignment, a worn coupling)",
              (M("vib_2x", weight=2.0), M("vib_3x", weight=0.5)), ("vibration",)),
    FaultType("looseness_or_rattle", "many harmonics, half-order and rattling (looseness, a soft foot, a cracked mount)",
              (M("vib_harm_3_10"), M("vib_half"), M("vib_crest"), M("gyr_rms", weight=0.5)), ("vibration", "rotation")),
    FaultType("repeating_knock", "a knock or click that repeats (damaged belt splice, gear tooth, clicking drive)",
              (M("knock_repeat", weight=2.0), M("vib_kurtosis"), M("snd_click")), ("vibration", "sound")),
    FaultType("impacts", "jolts or bangs much more often than usual (collisions, a jam, loose parts hitting)",
              (M("trend_shocks_10m"), M("trend_bangs_10m"), M("vib_peak_g", weight=0.5)), ("vibration", "sound")),
    FaultType("bearing_defect_like", "pulses at bearing defect rates in vibration or high-pitched sound (rolling-bearing wear)",
              (M("vib_env_peak", weight=1.5), M("snd_env_peak_db", weight=1.5), M("snd_kurt_hf")), ("vibration", "sound")),
    FaultType("speed_change", "running speed away from its usual (belt slip, a failing drive, a different load)",
              (M("vib_speed_hz", direction=0, weight=2.0), M("slow_peak1_hz", direction=0)), ("vibration",)),
    FaultType("electrical_motor", "twice-mains vibration and hum grown (motor electrical fault, a failing supply)",
              (M("vib_line_2x", weight=1.5), M("snd_mains_db")), ("vibration", "sound")),
    FaultType("new_tone_whine_squeal", "a new or louder tone (whine, squeal, a belt or bearing singing)",
              (M("snd_tone_db", weight=1.5), M("snd_tone_hz", direction=0, weight=0.5)), ("sound",)),
    FaultType("grinding_or_hiss", "rough high-pitched noise (grinding, lubrication loss, cavitation, arcing)",
              (M("snd_kurt_hf"), M("snd_flatness"), Member(_HIGH_SND, 1, 1.0)), ("sound",)),
    FaultType("alarm_or_beeping", "a tone switching on and off (an alarm, a warning beeper, a UPS on battery)",
              (M("snd_tone_mod_db", weight=2.0),), ("sound",)),
    FaultType("overheating", "hotter than usual for this machine",
              (M("th_max_c"), M("env_hot_above_room_c"), M("th_hotspot_c"), M("imu_temp_c")), ("heat",)),
    FaultType("heating_up_over_time", "temperature climbing over hours, days or weeks",
              (M("trend_heat_rise_per_h"), M("trend_heat_rise_per_day", weight=1.5), M("trend_heat_rise_per_week", weight=1.5)),
              ("heat",)),
    FaultType("local_hot_spot", "one small part heating (a connection, a bearing, a cable, a contact)",
              (G("th_spot_", 48, weight=1.5), M("trend_cell_rise_1h_vs_24h"), M("trend_cell_rise_24h_vs_7d")), ("heat",)),
    FaultType("area_running_cold", "an area cooler than usual (a stopped fan, blocked fins or flow, a failed heater)",
              (G("th_cell_", 48, direction=-1, weight=1.5), M("trend_cell_fall_1h_vs_24h"), M("th_cold_spot_c")), ("heat",)),
    FaultType("excess_temperature_fluctuation", "temperature swinging more than usual (cycling, hunting, an intermittent fault)",
              (M("trend_heat_swing_10m", weight=1.5), M("trend_heat_swing_1h", weight=1.5)), ("heat",)),
    FaultType("unsteady_vibration_or_sound", "vibration or sound coming and going more than usual (intermittent fault)",
              (M("trend_vib_swing_10m"), M("trend_vib_swing_1h"), M("trend_snd_swing_10m"), M("trend_snd_swing_1h")),
              ("vibration", "sound")),
    FaultType("room_too_hot_or_humid", "the room warmer or damper than usual (cooling failure, condensation risk)",
              (M("env_room_c", weight=1.5), M("trend_room_rise_per_h"), M("env_condensation_margin_c", direction=-1)),
              ("room",)),
    FaultType("device_moved", "the device has been moved or knocked (its tilt changed)",
              (M("vib_gravity_x", "vib_gravity_y", "vib_gravity_z", direction=0, weight=2.0),), ("vibration",), sensing=True),
    FaultType("camera_moved_or_blocked", "the thermal image no longer looks like its usual view",
              (M("trend_scene_match", direction=-1, weight=2.0), M("th_spread_c", direction=-1)), ("heat",), sensing=True),
)
NAMES = tuple(t.name for t in TYPES)

# What people are told: what is going on, not a guess at the cause. On 20 real bearings (Ottawa,
# training/record/datasets/OTTAWA_RESULTS.md) the device caught 35 of 40 faults but named the cause
# right once; unbalance, misalignment and bearing wear look alike to a 1 kHz accelerometer. So types the
# sensors can't reliably tell apart share one description. The detailed type stays in the data.
SYMPTOM_TEXT = {  # symptom: (the screen's form, at most 19 characters; a sentence)
    "vibration_too_high": ("VIBRATION TOO HIGH", "vibrating much more than normal"),
    "unusual_vibration": ("UNUSUAL VIBRATION", "the vibration's pattern has changed from normal"),
    "knocking_or_jolts": ("KNOCKING OR JOLTS", "knocks, clicks or jolts that don't belong"),
    "speed_changed": ("SPEED CHANGED", "running at a different speed from normal"),
    "vibration_unsteady": ("VIBRATION UNSTEADY", "vibration or sound coming and going more than normal"),
    "new_tone": ("NEW TONE OR WHINE", "a new or louder tone: a whine, a squeal or singing"),
    "grinding_noise": ("GRINDING NOISE", "rough, high-pitched noise: grinding or hissing"),
    "beeping": ("BEEPING", "a tone switching on and off, like an alarm"),
    "too_hot": ("TOO HOT", "hotter than normal"),
    "heating_up": ("HEATING UP", "temperature climbing over hours or days"),
    "heating_fast": ("HEATING FAST", "temperature rising quickly"),
    "hot_spot": ("HOT SPOT", "one small part heating up"),
    "running_cold": ("RUNNING COLD", "an area cooler than normal"),
    "temperature_swings": ("RAPID TEMP CHANGES", "rapid temperature changes, more than normal"),
    "louder_than_normal": ("LOUDER THAN NORMAL", "louder than normal"),
    "room_too_hot_or_humid": ("ROOM HOT OR HUMID", "the room is warmer or damper than normal"),
    "device_moved": ("DEVICE MOVED", "the device has been moved or knocked"),
    "camera_moved": ("CAMERA MOVED", "the camera's view has changed or is blocked"),
    "sensor_problem": ("SENSOR PROBLEM", "the sensor's readings make no sense"),
    "outside_normal": ("OUTSIDE NORMAL", "readings outside normal, in a pattern that fits nothing known"),
}
SYMPTOM = {  # fault type -> symptom
    "vibration_too_high": "vibration_too_high", "unbalance_like": "unusual_vibration",
    "misalignment_like": "unusual_vibration", "looseness_or_rattle": "unusual_vibration",
    "bearing_defect_like": "unusual_vibration", "electrical_motor": "unusual_vibration",
    "repeating_knock": "knocking_or_jolts", "impacts": "knocking_or_jolts", "speed_change": "speed_changed",
    "unsteady_vibration_or_sound": "vibration_unsteady", "new_tone_whine_squeal": "new_tone",
    "grinding_or_hiss": "grinding_noise", "alarm_or_beeping": "beeping", "overheating": "too_hot",
    "heating_up_over_time": "heating_up", "local_hot_spot": "hot_spot", "area_running_cold": "running_cold",
    "excess_temperature_fluctuation": "temperature_swings", "room_too_hot_or_humid": "room_too_hot_or_humid",
    "device_moved": "device_moved", "camera_moved_or_blocked": "camera_moved",
    "unrecognised_pattern": "outside_normal",
}
# A broader symptom makes a narrower one redundant when both are on (said once, not twice).
COVERS = {"vibration_too_high": ("unusual_vibration",), "too_hot": ("heating_up",),
          "heating_fast": ("heating_up",)}


def symptom_of(diagnosis: str | None) -> str | None:
    """The symptom for a fault type (None for normal, warming_up, stopped or an unknown name)."""
    return SYMPTOM.get(diagnosis or "")


def scores(dev: dict[str, float]) -> dict[str, float]:
    """Evidence for each type from deviations {feature name: signed spreads}. Missing readings
    are skipped; a type with none of its readings available scores 0."""
    out = {}
    for ft in TYPES:
        vals, weights = [], []
        for m in ft.members:
            got = [dev[n] for n in m.names if n in dev and np.isfinite(dev[n])]
            if not got:
                continue
            g = np.asarray(got, float)
            v = float(np.max(np.abs(g)) if m.direction == 0 else np.max(m.direction * g))
            vals.append(max(v, 0.0))
            weights.append(m.weight)
        if not vals:
            out[ft.name] = 0.0
            continue
        v, w = np.asarray(vals), np.asarray(weights)
        out[ft.name] = float(0.7 * np.max(v * w / w.max()) + 0.3 * (v * w).sum() / w.sum())
    return out


@dataclass
class Diagnosis:
    diagnosis: str
    confidence: float
    types: dict[str, float] = field(default_factory=dict)  # every type with evidence, strongest first
    evidence: dict[str, float] = field(default_factory=dict)  # the readings that moved most
    note: str = ""


def diagnose(dev: dict[str, float], abnormal_minutes: int = 0, learned: dict[str, float] | None = None,
             top: int = 5) -> Diagnosis:
    """Name the fault type for an abnormal minute. `learned` (probabilities from a trained fault
    classifier, if there is one) is blended with the signature scores."""
    s = scores(dev)
    if learned:
        s = {k: 0.5 * v + 0.5 * MIN_SCORE * 2 * learned.get(k, 0.0) for k, v in s.items()}
    ranked = dict(sorted(((k, round(v, 2)) for k, v in s.items() if v >= 1.0), key=lambda kv: -kv[1]))
    moved = dict(sorted(((k, round(float(v), 2)) for k, v in dev.items() if np.isfinite(v)),
                        key=lambda kv: -abs(kv[1]))[:top])
    if not ranked or next(iter(ranked.values())) < MIN_SCORE:
        return Diagnosis("unrecognised_pattern", 0.3, ranked, moved, "abnormal, but no fault type fits well")
    name, best = next(iter(ranked.items()))
    second = list(ranked.values())[1] if len(ranked) > 1 else 0.0
    conf = float(np.clip(0.5 + 0.5 * (best - second) / best, 0.5, 1.0) * np.clip(best / (2 * MIN_SCORE), 0.4, 1.0))
    note = ""
    ft = TYPES[NAMES.index(name)]
    others = [n for n, v in dev.items() if np.isfinite(v) and abs(v) >= 3 and not n.startswith(("snd_", "trend_snd"))]
    if set(ft.sensors) == {"sound"} and not others and abnormal_minutes < SOUND_ONLY_MINUTES:
        conf *= 0.5
        note = "sound only so far: may be the room (people, other machines), not this machine"
    if ft.sensing:
        note = "a problem with the device or its view, not the machine"
    return Diagnosis(name, round(conf, 2), ranked, moved, note)


# ----------------------------------------------------------------- describing straight from what moved
# What people are told comes from which GROUPS of readings left normal, not through a guess at the
# cause: fewer ways to be confidently wrong. Each group: (symptom, readings or prefixes, direction).
# Direction +1: only a rise counts, -1: only a fall, 0: either way.
_VIB_BANDS = tuple(f"vib_band_{i:02d}" for i in range(20))
GROUPS = (
    ("vibration_too_high", ("vib_rms_x", "vib_rms_y", "vib_rms_z", "vib_velocity_mm_s", "trend_vib_10m"), 1),
    ("unusual_vibration", _VIB_BANDS + ("vib_1x", "vib_2x", "vib_3x", "vib_harm_3_10", "vib_half", "vib_peak2",
                                        "vib_env_peak", "vib_line_2x", "gyr_rms") + tuple(f"slow_band_{i:02d}" for i in range(6)), 0),
    ("knocking_or_jolts", ("vib_kurtosis", "vib_crest", "vib_peak_g", "vib_jerk", "trend_shocks_10m", "knock_repeat",
                           "snd_click", "trend_bangs_10m", "snd_peak_db"), 1),
    ("speed_changed", ("vib_speed_hz", "trend_speed_1h"), 0),
    ("vibration_unsteady", ("trend_vib_swing_10m", "trend_vib_swing_1h", "trend_snd_swing_10m", "trend_snd_swing_1h"), 1),
    ("louder_than_normal", ("snd_level_db", "trend_snd_10m"), 1),
    ("new_tone", ("snd_tone_db",), 1),
    ("grinding_noise", ("snd_kurt_hf", "snd_flatness", "snd_env_peak_db") + _HIGH_SND, 1),
    ("beeping", ("snd_tone_mod_db",), 1),
    ("too_hot", ("imu_temp_c", "th_max_c", "th_mean_c", "env_hot_above_room_c", "trend_heat_10m"), 1),
    ("heating_up", ("trend_heat_rise_per_h", "trend_heat_rise_per_day", "trend_heat_rise_per_week"), 1),
    ("heating_fast", ("trend_heat_rise_per_min",), 1),
    ("hot_spot", ("th_hotspot_c", "th_hot_pixels", "trend_cell_rise_1h_vs_24h", "trend_cell_rise_24h_vs_7d"), 1),
    ("running_cold", ("th_cold_spot_c", "trend_cell_fall_1h_vs_24h", "trend_cell_fall_24h_vs_7d"), 1),
    ("temperature_swings", ("trend_heat_swing_10m", "trend_heat_swing_1h", "th_change_c"), 1),
    ("room_too_hot_or_humid", ("env_room_c", "env_humidity_pct", "trend_room_rise_per_h"), 1),
    ("device_moved", ("vib_gravity_x", "vib_gravity_y", "vib_gravity_z"), 0),
    ("camera_moved", ("trend_scene_match", "th_spread_c"), -1),
)
MOVED = 3.0  # a reading counts as moved at 3 spreads from its normal
SAY_AT_MOST = 2  # symptoms said at once


def describe(dev: dict[str, float]) -> list[tuple[str, float]]:
    """What is going on, from the readings that left normal: [(symptom, strength)], strongest first.

    A group's strength is the mean of its three most-moved readings (in its direction), so a group
    where many readings moved beats one where a single reading did (they often top out at the same
    value). Two guards, from real bearing faults (training/record/datasets/OTTAWA_RESULTS.md):
      - speed_changed only when the vibration's shape did not change broadly: the speed is read as
        the strongest steady peak, and a fault's new peaks move that reading without the speed moving.
      - device_moved only when the vibration level did not rise and nothing clipped: heavy vibration
        and clipping shift the gravity reading without the device moving.
    Nothing moved enough: outside_normal (readings outside normal, in no known pattern)."""
    def moved(names, direction):
        v = [dev[n] for n in names if n in dev and np.isfinite(dev[n])]
        v = np.asarray(v, float) * (direction if direction else 1.0)
        return np.abs(v) if direction == 0 else np.maximum(v, 0.0)

    strength, broad = {}, {}
    for sym, names, direction in GROUPS:
        m = moved(names, direction)
        if not m.size:
            continue
        top = np.sort(m)[::-1][:3]
        strength[sym] = float(top.mean()) if top[0] >= MOVED else 0.0
        broad[sym] = int((m >= MOVED).sum())
    if broad.get("unusual_vibration", 0) >= 3:
        strength.pop("speed_changed", None)
    clipped = dev.get("vib_clip_frac", 0.0)
    if strength.get("vibration_too_high", 0.0) >= MOVED or (np.isfinite(clipped) and clipped >= MOVED):
        strength.pop("device_moved", None)
    ranked = sorted(((s, v) for s, v in strength.items() if v >= MOVED), key=lambda kv: -kv[1])
    said = [s for s, _ in ranked]
    covered = {c for s in said for c in COVERS.get(s, ())}
    ranked = [(s, round(v, 2)) for s, v in ranked if s not in covered]
    return ranked[:SAY_AT_MOST] or [("outside_normal", 0.0)]
