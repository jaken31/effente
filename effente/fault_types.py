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
