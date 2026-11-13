"""Pure dashboard logic: a Python port of platform/web/dashboard_logic.js.

Function for function, with the same names in snake_case, and the same
answers (tests/data/test_data_logic_parity.py runs both on the same inputs).
No Qt, no network. Shapes follow platform/CONTRACT.md section 5.

The state is a plain dict, as in the JavaScript, with snake_case keys. Objects
the functions return also use snake_case keys (`at_ms`, not `atMs`).

The second half of the file, from "Laptop only", has no JavaScript source:
alarms (contract section 6) and sensitivity (section 7). Pages use those
helpers; no other module carries its own labelling or judging rules.
"""

from __future__ import annotations

import base64
import binascii
import datetime as _dt
import math
import re
from array import array
from decimal import ROUND_HALF_UP, Decimal

# Diagnoses that are not fault types (contract "Names").
NOT_ABNORMAL = ("normal", "stopped", "warming_up")
WATCHING = "watching"

# Contract: connected means heard from in the last 5 seconds. The brief adds
# "no record for 5 seconds" as its own stale condition while watching.
STALE_MS = 5000
HISTORY_CAP = 300

# Contract section 5, "Showing problems next to the diagnosis".
KIND_LABELS = {
    "limit": "limit for any machine",
    "outside_normal": "beyond this machine's normal",
}

MODE_NAMES = {
    "watching": "Watching",
    "display": "Display",
    "calibrating": "Calibrating",
    "recording": "Recording",
    "training": "Training",
}


def _is_num(v) -> bool:
    """JavaScript `typeof v === "number" && isFinite(v)`."""
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _is_number_type(v) -> bool:
    """JavaScript `typeof v === "number"` (NaN and infinities included)."""
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def fixed(v, digits: int) -> str:
    """JavaScript `Number(v).toFixed(digits)`: halves round away from zero, on the exact binary value."""
    v = float(v)
    if math.isnan(v):
        return "NaN"
    if math.isinf(v):
        return "Infinity" if v > 0 else "-Infinity"
    q = Decimal(v).quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)
    text = f"{q:f}"
    if v == 0 and text.startswith("-"):
        text = text[1:]  # (-0).toFixed(1) is "0.0"; (-0.01).toFixed(1) stays "-0.0"
    return text


def _js_round(v: float) -> float:
    """JavaScript Math.round: halves go up."""
    return math.floor(v + 0.5)


def _js_number_text(v: float) -> str:
    """JavaScript String(v) for ordinary numbers: no trailing ".0"."""
    if float(v).is_integer():
        return str(int(v))
    return repr(float(v))


# How the value of each known problem reads. A name not listed here is
# still shown, with its value as sent: names are the device's, not ours.
PROBLEM_VALUES = {
    "vibration_over_limit": lambda v: fixed(v, 1) + " mm/s",
    "knocking": lambda v: "kurtosis " + fixed(v, 1),
    "too_hot": lambda v: fixed(v, 1) + " °C",
    "heating_fast": lambda v: fixed(v, 1) + " °C a minute",
    "repeated_jolts": lambda v: fixed(v, 0) + " jolts in 10 min",
    "vibration_above_normal": lambda v: fixed(v, 1) + "× its normal",
    "hotter_than_normal": lambda v: "+" + fixed(v, 0) + " °C over its normal",
    "louder_than_normal": lambda v: "+" + fixed(v, 0) + " dB over its normal",
}


def capitalise(text) -> str:
    return text[0].upper() + text[1:] if text else ""


def display_name(diagnosis) -> str:
    """"unbalance_like" -> "Unbalance-like", "looseness_or_rattle" -> "Looseness or rattle"."""
    if diagnosis is None:
        return ""
    text = _js_string(diagnosis)
    if text == "normal":
        return "Normal"
    if text == "warming_up":
        return "Warming up"
    if text == "stopped":
        return "Stopped"
    if text == "unrecognised_pattern":
        return "Unrecognised pattern"
    return capitalise(re.sub(r"_like$", "-like", text).replace("_", " "))


def _js_string(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if _is_number_type(v):
        return _js_number_text(v)
    return str(v)


def diagnosis_kind(diagnosis) -> str:
    """'none' (no diagnosis), 'normal', 'warming_up', 'stopped', 'unrecognised' or 'fault'."""
    if not isinstance(diagnosis, str) or diagnosis == "":
        return "none"
    if diagnosis == "unrecognised_pattern":
        return "unrecognised"
    if diagnosis in NOT_ABNORMAL:
        return diagnosis
    return "fault"


def mode_name(mode) -> str:
    if not mode:
        return ""
    return MODE_NAMES.get(mode) or capitalise(_js_string(mode).replace("_", " "))


def kind_label(kind) -> str:
    return KIND_LABELS.get(kind) or (_js_string(kind).replace("_", " ") if kind else "")


def problem_value_text(problem) -> str:
    v = problem.get("value") if isinstance(problem, dict) else None
    if not _is_num(v):
        return ""
    f = PROBLEM_VALUES.get(problem.get("name"))
    return f(v) if f else _js_number_text(_js_round(v * 100) / 100)


def as_problems(v) -> list:
    if not isinstance(v, list):
        return []
    return [p for p in v if isinstance(p, dict) and isinstance(p.get("name"), str) and p["name"]]


def parse_time(iso) -> float:
    """ISO time to milliseconds since 1970, or NaN (JavaScript Date.parse for the ISO forms the contract uses)."""
    if not isinstance(iso, str):
        return math.nan
    try:
        value = _dt.datetime.fromisoformat(iso)
    except ValueError:
        return math.nan
    if value.tzinfo is None:
        value = value.replace(tzinfo=_dt.UTC)
    return value.timestamp() * 1000.0


def _nan(v) -> bool:
    return isinstance(v, float) and math.isnan(v)


def create_state() -> dict:
    return {
        "devices": {},  # device_id -> device view (see ensure_device)
        "models": {},  # model_version -> message 1
        "runs": {},  # run_id -> {"run", "order"}
        "run_order": 0,
        "history": {},  # machine_id -> records without spectrum, oldest first
        "selected_device_id": None,  # None = auto (most recently heard)
        "platform_up": False,  # the event stream is open
        "platform_lost": False,  # the stream or /api/state failed and has not recovered
        "snapshot_loaded": False,
    }


def ensure_device(state: dict, device_id: str) -> dict:
    if device_id not in state["devices"]:
        state["devices"][device_id] = {
            "device_id": device_id,
            "connected": False,
            "last_seen": None,
            "model_version": None,
            "mode": None,  # "watching" or a check-in mode; None until the platform says
            "machine_id": None,
            "wrong": False,  # from the newest record or check-in; False while not connected
            "problems": [],  # likewise; [] while not connected
            "latest": None,  # newest record
            "last_record_local_ms": None,  # local clock time the newest record counts as received
            "last_heard_local_ms": None,
        }
    return state["devices"][device_id]


def apply_snapshot(state: dict, snap, now_ms: float) -> dict:
    """Load GET /api/state. `now_ms` is the local clock. The platform's own `now`
    converts its timestamps into "how long ago", so a clock skew between laptop
    clock and platform cannot make a fresh record look stale."""
    snap = snap if isinstance(snap, dict) else {}
    server_now = parse_time(snap.get("now"))

    def ago(iso):
        t = parse_time(iso)
        if _nan(t) or _nan(server_now):
            return None
        return now_ms - max(0, server_now - t)

    state["models"] = {**state["models"], **(snap.get("models") or {})}
    devs = snap.get("devices") or {}
    for device_id in list(state["devices"]):
        if device_id not in devs:
            del state["devices"][device_id]
    for device_id in devs:
        src = devs[device_id] or {}
        d = ensure_device(state, device_id)
        d["connected"] = bool(src.get("connected"))
        d["last_seen"] = src.get("last_seen") or None
        d["model_version"] = src.get("model_version") or None
        d["mode"] = src.get("mode") or None
        d["wrong"] = d["connected"] and src.get("wrong") is True
        d["problems"] = as_problems(src.get("problems")) if d["connected"] else []
        d["last_heard_local_ms"] = ago(src.get("last_seen"))
        latest = src.get("latest")
        if isinstance(latest, dict):
            d["latest"] = latest
            d["last_record_local_ms"] = ago(latest.get("received_at") or latest.get("at"))
            if not d["model_version"] and latest.get("model_version"):
                d["model_version"] = latest["model_version"]
        else:
            d["latest"] = None
            d["last_record_local_ms"] = None
        d["machine_id"] = src.get("machine_id") or (d["latest"] and d["latest"].get("machine_id")) or None
    state["snapshot_loaded"] = True
    return state


def _seq_key(r) -> float:
    seq = r.get("seq")
    return seq if _is_number_type(seq) else 0


def add_history_record(state: dict, rec, *, slim: bool = True) -> None:
    """`slim=False` (laptop only) keeps `spectrum` and `heat.frames`, for the
    desktop pages that draw them from history. The default matches the JS."""
    if not isinstance(rec, dict) or not rec.get("machine_id"):
        return
    lst = state["history"].get(rec["machine_id"])
    if lst is None:
        return  # only machines whose history was loaded are kept
    kept = dict(rec)
    if slim:
        kept.pop("spectrum", None)
        heat = kept.get("heat")
        if isinstance(heat, dict) and "frames" in heat:
            kept["heat"] = {k: v for k, v in heat.items() if k != "frames"}  # images only from the newest record
    seq = kept.get("seq")
    if _is_number_type(seq):
        for i in range(len(lst) - 1, -1, -1):
            if lst[i].get("seq") == seq:
                return  # duplicate after reconnect
            if _is_number_type(lst[i].get("seq")) and lst[i]["seq"] < seq:
                break
    lst.append(kept)
    if _is_number_type(seq):
        lst.sort(key=_seq_key)
    if len(lst) > HISTORY_CAP:
        del lst[: len(lst) - HISTORY_CAP]


def set_history(state: dict, machine_id: str, records, *, slim: bool = True) -> dict:
    """Load GET /api/machines/<id>/records. Records already received on the
    stream for this machine are kept and merged in (deduplicated by seq)."""
    prev = state["history"].get(machine_id) or []
    state["history"][machine_id] = []
    for r in records or []:
        add_history_record(state, r, slim=slim)
    for r in prev:
        add_history_record(state, r, slim=slim)
    return state


def add_run(state: dict, run) -> None:
    if not isinstance(run, dict) or not isinstance(run.get("run_id"), str):
        return
    state["run_order"] += 1
    state["runs"][run["run_id"]] = {"run": run, "order": state["run_order"]}


def add_runs(state: dict, runs) -> dict:
    # Contract lists runs newest first; add oldest first so arrival order
    # breaks ties in the same direction.
    lst = list(reversed(runs)) if isinstance(runs, list) else []
    for r in lst:
        if not isinstance(r, dict) or not isinstance(r.get("run_id"), str):
            continue
        have = state["runs"].get(r["run_id"])
        # A copy fetched after a reconnect may be newer than the streamed one,
        # or older if the stream got ahead of the fetch. Keep the further one.
        if have and run_progress(have["run"]) > run_progress(r):
            continue
        add_run(state, r)
    return state


def run_progress(run: dict) -> int:
    calls = len(run["tool_calls"]) if isinstance(run.get("tool_calls"), list) else 0
    return (1_000_000 if run.get("status") == "done" else 0) + calls


def apply_event(state: dict, name: str, data, now_ms: float, *, slim: bool = True) -> dict:
    """Apply one SSE event (contract section 5). Mutates and returns state.
    `slim` is passed to add_history_record (laptop only)."""
    if not isinstance(data, dict):
        return state
    if name == "record":
        if not data.get("device_id"):
            return state
        d = ensure_device(state, data["device_id"])
        d["latest"] = data
        d["last_record_local_ms"] = now_ms
        d["last_heard_local_ms"] = now_ms
        d["connected"] = True
        d["mode"] = WATCHING
        d["last_seen"] = data.get("received_at") or data.get("at") or d["last_seen"]
        if data.get("machine_id"):
            d["machine_id"] = data["machine_id"]
        if data.get("model_version"):
            d["model_version"] = data["model_version"]
        d["wrong"] = data.get("wrong") is True
        d["problems"] = as_problems(data.get("problems"))
        add_history_record(state, data, slim=slim)
    elif name == "model":
        if data.get("model_version"):
            state["models"][data["model_version"]] = data
        if data.get("device_id"):
            d = ensure_device(state, data["device_id"])
            d["model_version"] = data.get("model_version") or d["model_version"]
    elif name == "agent_run":
        add_run(state, data)
    elif name == "device":
        if not data.get("device_id"):
            return state
        d = ensure_device(state, data["device_id"])
        d["connected"] = bool(data.get("connected"))
        if data.get("last_seen"):
            d["last_seen"] = data["last_seen"]
        if data.get("mode"):
            d["mode"] = data["mode"]
        if data.get("connected"):
            d["last_heard_local_ms"] = now_ms
        else:
            d["wrong"] = False
            d["problems"] = []
    elif name == "problem":
        problem = data.get("problem")
        if not data.get("device_id") or not isinstance(problem, dict) or not isinstance(problem.get("name"), str):
            return state
        d = ensure_device(state, data["device_id"])
        if data.get("machine_id"):
            d["machine_id"] = data["machine_id"]
        d["problems"] = [p for p in d["problems"] if p["name"] != problem["name"]] + [problem]
        d["wrong"] = True
    elif name == "problem_cleared":
        if not data.get("device_id") or not isinstance(data.get("name"), str):
            return state
        d = ensure_device(state, data["device_id"])
        d["problems"] = [p for p in d["problems"] if p["name"] != data["name"]]
        # `wrong` also depends on the diagnosis: the next record or check-in says.
        # Without problems and without an abnormal diagnosis nothing is wrong.
        if not d["problems"] and not (
            d["mode"] == WATCHING and d["latest"] and is_abnormal(d["latest"].get("diagnosis"))
        ):
            d["wrong"] = False
    return state


def is_abnormal(diagnosis) -> bool:
    k = diagnosis_kind(diagnosis)
    return k in ("fault", "unrecognised")


def shown_device_id(state: dict):
    """The device shown: the one picked by hand if it still exists, else the
    one heard from most recently."""
    selected = state.get("selected_device_id")
    if selected and selected in state["devices"]:
        return selected
    best = None
    best_t = -math.inf
    for device_id in sorted(state["devices"]):
        d = state["devices"][device_id]
        t = d["last_heard_local_ms"]
        if t is None:
            t = parse_time(d["last_seen"])
        if t is None or _nan(t):
            t = -math.inf
        if best is None or t > best_t:
            best = device_id
            best_t = t
    return best


def device_status(state: dict, device_id, now_ms: float) -> str:
    """'none'          no device ever seen
    'disconnected'  platform says the device is not connected
    'checking_in'   connected and not watching: check-ins, no records
    'no_readings'   connected, but no record for 5 seconds (or never)
    'live'          a record arrived in the last 5 seconds"""
    d = state["devices"].get(device_id) if device_id else None
    if not d:
        return "none"
    if not d["connected"]:
        return "disconnected"
    if d["mode"] and d["mode"] != WATCHING:
        return "checking_in"  # no records outside watching, by design
    if d["last_record_local_ms"] is None or now_ms - d["last_record_local_ms"] > STALE_MS:
        return "no_readings"
    return "live"


def model_for(state: dict, device_id):
    d = state["devices"].get(device_id) if device_id else None
    if not d:
        return None
    v = d["model_version"] or (d["latest"] and d["latest"].get("model_version"))
    return (v and state["models"].get(v)) or None


def latest_run_for_machine(state: dict, machine_id):
    if not machine_id:
        return None
    best = None
    for entry in state["runs"].values():
        if entry["run"].get("machine_id") != machine_id:
            continue
        if not best:
            best = entry
            continue
        a = parse_time(entry["run"].get("at"))
        b = parse_time(best["run"].get("at"))
        if a > b or (a == b and entry["order"] > best["order"]) or (_nan(b) and not _nan(a)):
            best = entry
    return best["run"] if best else None


def headline(state: dict, device_id, now_ms: float) -> dict:
    """What the page's headline says (contract: `wrong` decides it, in every
    mode). Only a live device can say nothing is wrong: a device that is
    quiet, disconnected or behind a lost platform says nothing at all.
    {"state": 'none' | 'waiting' | 'not_live' | 'wrong' | 'ok', "text"}"""
    d = state["devices"].get(device_id) if device_id else None
    if not d:
        return {"state": "none", "text": "Waiting for a device"}
    status = device_status(state, device_id, now_ms)
    if not state["platform_up"] or status in ("disconnected", "no_readings"):
        return {"state": "not_live", "text": "Not live"}
    if not d["latest"] and (d["mode"] == WATCHING or not d["mode"]):
        return {"state": "waiting", "text": "Waiting for readings"}
    if d["wrong"]:
        return {"state": "wrong", "text": "Something is wrong"}
    return {"state": "ok", "text": "Nothing is wrong"}


def model_line(state: dict, device_id):
    """The model's panel, next to the problems. Never hidden by them.
    {"kind", "label", "detail", "note", "possible"}"""
    d = state["devices"].get(device_id) if device_id else None
    if not d:
        return None
    if d["mode"] and d["mode"] != WATCHING:
        return {
            "kind": "none",
            "label": "Model: not judging while " + mode_name(d["mode"]).lower(),
            "detail": "There is no diagnosis outside watching. The limits still run.",
            "note": "",
            "possible": False,
        }
    rec = d["latest"]
    if not rec:
        return None
    kind = diagnosis_kind(rec.get("diagnosis"))
    if kind == "normal":
        return {"kind": kind, "label": "Model: nothing unusual yet", "detail": "", "note": rec.get("note") or "",
                "possible": False}
    if kind in ("warming_up", "stopped"):
        return {
            "kind": kind,
            "label": "Model: " + ("machine stopped" if kind == "stopped" else "warming up"),
            "detail": rec.get("note") or "",
            "note": "",
            "possible": False,
        }
    possible = False
    if kind == "fault":
        model = model_for(state, device_id)
        types = model.get("fault_types") if model and isinstance(model.get("fault_types"), list) else []
        t = next((x for x in types if isinstance(x, dict) and x.get("name") == rec.get("diagnosis")), None)
        possible = not t or t.get("validated") is not True  # the contract: unvalidated types are "possible"
    return {"kind": kind, "label": "Model: " + display_name(rec.get("diagnosis")), "detail": rec.get("reason") or "",
            "note": rec.get("note") or "", "possible": possible}


def _announced_features(model) -> list:
    feats = model.get("features") if model else None
    return feats if isinstance(feats, list) else []


def moved_readings(state: dict, device_id) -> list:
    """The newest record's `features` (readings that moved, in spreads), with
    the text the model announced for each. Order as sent."""
    d = state["devices"].get(device_id) if device_id else None
    rec = d["latest"] if d else None
    if not rec or not isinstance(rec.get("features"), dict):
        return []
    texts = {}
    for f in _announced_features(model_for(state, device_id)):
        if isinstance(f, dict) and f.get("name"):
            texts[f["name"]] = f.get("text")
    return [{"name": n, "text": texts.get(n) or n, "value": v} for n, v in rec["features"].items() if _is_num(v)]


def feature_texts(state: dict, device_id) -> dict:
    """Feature name -> its announced text, for the agent's cited readings."""
    out = {}
    for f in _announced_features(model_for(state, device_id)):
        if isinstance(f, dict) and f.get("name"):
            out[f["name"]] = f.get("text") or f["name"]
    return out


def simulated_sources(state: dict, device_id, run) -> list:
    """Which parts of what is on screen are simulated."""
    out = []
    d = state["devices"].get(device_id) if device_id else None
    if d and d["latest"] and d["latest"].get("simulated") is True:
        out.append("readings")
    m = model_for(state, device_id)
    if m and m.get("simulated") is True:
        out.append("model")
    if run and run.get("simulated") is True:
        out.append("agent report")
    return out


# ---------------- heat (contract 3.3, optional) ----------------
HEAT_W = 32
HEAT_H = 24
HEAT_ROWS = 6
HEAT_COLS = 8
IMU_TREND_MS = 120000

_B64_CHARS = re.compile(r"^[A-Za-z0-9+/]*$")


def _b64_bytes(text):
    """The browser's atob: whitespace ignored, padding optional, anything else fails."""
    if not isinstance(text, str):
        return None
    s = re.sub(r"[\t\n\f\r ]", "", text)
    if len(s) % 4 == 0 and s.endswith("="):
        s = s[:-2] if s.endswith("==") else s[:-1]
    if len(s) % 4 == 1 or not _B64_CHARS.match(s):
        return None
    try:
        return base64.b64decode(s + "=" * (-len(s) % 4))
    except (binascii.Error, ValueError):
        return None


def decode_frame(frame):
    """One contract 3.3 image -> {"at_ms", "pixels": 768 floats in degC (float32, as the JS)},
    or None if it is not 768 bytes with a usable min_c and step_c."""
    if not isinstance(frame, dict) or not _is_num(frame.get("min_c")) or not _is_num(frame.get("step_c")):
        return None
    if frame["step_c"] <= 0:
        return None
    raw = _b64_bytes(frame.get("data"))
    if raw is None or len(raw) != HEAT_W * HEAT_H:
        return None
    min_c, step_c = frame["min_c"], frame["step_c"]
    pixels = array("f", (min_c + b * step_c for b in raw))
    return {"at_ms": parse_time(frame.get("at")), "pixels": pixels}


def _js_is_integer(v) -> bool:
    return _is_num(v) and float(v).is_integer()


def heat_view(rec) -> dict:
    """What the heat panel can draw from a record.
    kind "none"    the record has no heat field (or no record)
    kind "imu"     no camera: IMU temperature only
    kind "camera"  summary, and images when the record carried them
    kind "bad"     a heat field this page cannot read"""
    h = rec.get("heat") if isinstance(rec, dict) else None
    if h is None:
        return {"kind": "none"}
    if not isinstance(h, dict):
        return {"kind": "bad"}
    imu = h["imu_temp_c"] if _is_num(h.get("imu_temp_c")) else None
    if h.get("source") == "imu":
        return {"kind": "imu", "imu_temp_c": imu}
    if h.get("source") != "camera":
        return {"kind": "bad"}

    def grid(v):
        ok = isinstance(v, list) and len(v) == HEAT_ROWS * HEAT_COLS and all(_is_num(x) for x in v)
        return v if ok else None

    frames = [f for f in (decode_frame(x) for x in (h["frames"] if isinstance(h.get("frames"), list) else [])) if f]
    hot_row, hot_col = h.get("hot_row"), h.get("hot_col")
    view = {
        "kind": "camera",
        "imu_temp_c": imu,
        "max_c": h["max_c"] if _is_num(h.get("max_c")) else None,
        "background_c": h["background_c"] if _is_num(h.get("background_c")) else None,
        "hot_row": hot_row if _js_is_integer(hot_row) and 0 <= hot_row < HEAT_H else None,
        "hot_col": hot_col if _js_is_integer(hot_col) and 0 <= hot_col < HEAT_W else None,
        "cells": grid(h.get("cells")),
        "spots": grid(h.get("spots")),
        "frozen": h.get("frozen") is True,
        "scene_match": h["scene_match"] if _is_num(h.get("scene_match")) else None,
        "frames": frames,
    }
    if not frames and not (view["cells"] and view["background_c"] is not None):
        view["kind"] = "bad"
    return view


def frame_at(frames, elapsed_ms: float) -> dict:
    """Which of a record's images to show `elapsed_ms` after it arrived: the
    images are played back at the spacing they were taken. Returns
    {"index", "next_ms"} where next_ms is how long until the next image is due (None if none)."""
    if not frames:
        return {"index": -1, "next_ms": None}
    t0 = frames[0]["at_ms"]
    index = 0
    for i in range(1, len(frames)):
        ti = frames[i]["at_ms"]
        off = ti - t0 if _is_num(ti) and _is_num(t0) else math.nan
        if not (off >= 0 and off <= 2000):
            return {"index": len(frames) - 1, "next_ms": None}  # odd times: newest only
        if elapsed_ms >= off:
            index = i
        else:
            return {"index": index, "next_ms": off - elapsed_ms}
    return {"index": index, "next_ms": None}


def heat_range(view: dict):
    """Colour range for a camera view: whole degrees around the coldest and
    hottest pixel of its images (or grid), at least 4 degC wide."""
    lo, hi = math.inf, -math.inf
    for f in view.get("frames") or []:
        for v in f["pixels"]:
            lo = min(lo, v)
            hi = max(hi, v)
    cells, spots, bg = view.get("cells"), view.get("spots"), view.get("background_c")
    if not math.isfinite(lo) and cells and bg is not None:
        for i, c in enumerate(cells):
            lo = min(lo, bg + c)
            hi = max(hi, bg + (spots[i] if spots else c))
    if not math.isfinite(lo):
        return None
    lo = math.floor(lo)
    hi = math.ceil(hi)
    if hi - lo < 4:
        hi = lo + 4
    return {"lo": lo, "hi": hi}


# Thermal colour ramp (dark purple, red, orange, pale yellow), t in 0..1.
RAMP = [
    (0.0, (20, 11, 52)),
    (0.25, (101, 21, 110)),
    (0.5, (188, 55, 84)),
    (0.75, (249, 142, 9)),
    (1.0, (252, 255, 164)),
]


def heat_color(t) -> list:
    x = max(0.0, min(1.0, t if _is_num(t) else 0))
    for i in range(1, len(RAMP)):
        t1, c1 = RAMP[i]
        if x <= t1:
            t0, c0 = RAMP[i - 1]
            f = (x - t0) / (t1 - t0)
            return [int(_js_round(v + (c1[k] - v) * f)) for k, v in enumerate(c0)]
    return list(RAMP[-1][1])


def imu_trend(records):
    """Change of the IMU temperature over the last 2 minutes of a machine's
    history (records oldest first), ending at the newest record with one."""
    pts = []
    for r in records or []:
        heat = r.get("heat") if isinstance(r, dict) else None
        v = heat["imu_temp_c"] if isinstance(heat, dict) and _is_num(heat.get("imu_temp_c")) else None
        t = parse_time(r.get("at")) if isinstance(r, dict) else math.nan
        if v is not None and not _nan(t):
            pts.append((t, v))
    if len(pts) < 2:
        return None
    end = pts[-1]
    start = None
    start_i = -1
    for i, p in enumerate(pts):
        if end[0] - p[0] <= IMU_TREND_MS:
            start, start_i = p, i
            break
    if start is None or start_i == len(pts) - 1 or end[0] - start[0] < 10000:
        return None
    return {"delta_c": end[1] - start[1], "span_ms": end[0] - start[0]}


# =====================================================================
# Laptop only, no JS equivalent: alarms (contract section 6) and
# sensitivity (section 7), as platform/desktop/INTERFACES.md asks.
# =====================================================================

DEFAULT_SENSITIVITY = 0.5
# effente/safety.py OUTSIDE_NORMAL at the default sensitivity. Copied, not
# imported: safety.py needs numpy. tests/data checks the two agree.
OUTSIDE_NORMAL = {"vibration": 2.0, "temperature": 15.0, "sound": 6.0, "model": 1.0}
VERDICT_TEXT = {"real": "Real problem", "false": "False alarm"}


def _sensitivity_or_none(s):
    return float(s) if _is_num(s) and 0 <= s <= 1 else None


def alarm_line(sensitivity):
    """The contract's alarm line, `4 ** (1 - 2 * s)`: 4 at 0, 1 at 0.5, 0.25 at 1. None if not reported."""
    s = _sensitivity_or_none(sensitivity)
    return None if s is None else 4.0 ** (1.0 - 2.0 * s)


def sensitivity_name(sensitivity) -> str:
    """"Low" below 0.34, "High" above 0.66, else "Balanced". "Not reported" when there is none."""
    s = _sensitivity_or_none(sensitivity)
    if s is None:
        return "Not reported"
    return "Low" if s < 0.34 else "High" if s > 0.66 else "Balanced"


def margins(sensitivity) -> dict:
    """How far outside normal counts as wrong at this sensitivity, as effente/safety.py margins():
    vibration in times normal, temperature in degC, sound in dB, and the model line.
    The limits never change with sensitivity."""
    s = _sensitivity_or_none(sensitivity)
    s = DEFAULT_SENSITIVITY if s is None else s
    k = 4.0 ** (2.0 * s - 1.0)  # 0 -> 1/4, 0.5 -> 1, 1 -> 4
    base = OUTSIDE_NORMAL
    return {
        "vibration": 1.0 + (base["vibration"] - 1.0) / k,
        "temperature": base["temperature"] / k,
        "sound": base["sound"] / k,
        "model": base["model"] / k,
    }


# Contract "Names": problems are sent limits first, in the order of the names table.
PROBLEM_ORDER = ("vibration_over_limit", "knocking", "too_hot", "heating_fast", "repeated_jolts",
                 "vibration_above_normal", "hotter_than_normal", "louder_than_normal")


def problem_order(problems) -> list:
    """Problems in the contract's order: limits first, by the names table; names it
    does not list after those (limits first), otherwise as they came. A `problem` event appends to the end in
    apply_event (as the JS does), so a list built from events needs this."""
    def key(item):
        i, p = item
        name = p.get("name")
        known = name in PROBLEM_ORDER
        kind_rank = 0 if p.get("kind") == "limit" else 1
        return (0 if known else 1, PROBLEM_ORDER.index(name) if known else kind_rank, i)

    return [p for _, p in sorted(enumerate(problems or []), key=key)]


# Laptop only, no JS equivalent. docs/TRD.md: "A reading 6 or more spreads
# from usual counts"; device/agent/report.py EVIDENCE_SPREADS and moved() use the same.
EVIDENCE_SPREADS = 6.0


def is_evidence(spreads) -> bool:
    """Whether a reading, in spreads from its normal, moved enough to count, either way."""
    return _is_num(spreads) and abs(spreads) >= EVIDENCE_SPREADS


def fault_type_tag(fault_type) -> str:
    """The tag next to a fault type: "possible · not validated" unless the model
    announced it `validated: true` (contract section 1); "" for a validated one,
    as the web page shows no tag then. Laptop only: the JS has only model_line's
    `possible` flag and the bare word "possible" in index.html."""
    validated = isinstance(fault_type, dict) and fault_type.get("validated") is True
    return "" if validated else "possible · not validated"


def diagnosis_tag(model, diagnosis, model_version=None) -> str:
    """Laptop only, no JS equivalent. The tag for a diagnosis made by `model_version`:
    fault_type_tag of its fault type in `model` for a fault, "" for anything else.
    A fault made by another model than `model` is tagged, as its validation is not
    known here. No `model_version` means `model` made it."""
    if diagnosis_kind(diagnosis) != "fault":
        return ""
    same = isinstance(model, dict) and model_version in (None, model.get("model_version"))
    types = model.get("fault_types") if same and isinstance(model.get("fault_types"), list) else []
    return fault_type_tag(next((x for x in types if isinstance(x, dict) and x.get("name") == diagnosis), None))


def diagnosis_text(model, diagnosis, model_version=None) -> str:
    """Laptop only. display_name, with " · possible" after a fault diagnosis_tag tags."""
    name = display_name(diagnosis)
    return f"{name} · possible" if diagnosis_tag(model, diagnosis, model_version) else name


def is_unanswered(alarm) -> bool:
    return isinstance(alarm, dict) and alarm.get("answer") is None


def verdict_text(verdict) -> str:
    return VERDICT_TEXT.get(verdict, "")


def apply_laptop_snapshot(state: dict, snap) -> dict:
    """The parts of GET /api/state the JS does not read: `alarms`, each device's
    `sensitivity` and `settings`, and `machines`. Call after apply_snapshot."""
    snap = snap if isinstance(snap, dict) else {}
    alarms = snap.get("alarms")
    state["alarms"] = {k: v for k, v in alarms.items() if isinstance(v, dict)} if isinstance(alarms, dict) else {}
    machines = snap.get("machines")
    state["machines"] = [m for m in machines if isinstance(m, str)] if isinstance(machines, list) else []
    devs = snap.get("devices") or {}
    for device_id, d in state["devices"].items():
        src = devs.get(device_id) or {}
        d["sensitivity"] = _sensitivity_or_none(src.get("sensitivity"))
        d["settings"] = src.get("settings") if isinstance(src.get("settings"), dict) else None
    return state


def apply_laptop_event(state: dict, name: str, data) -> bool:
    """The events the JS does not read: `alarm` and `settings`, and the machine
    a `record` names. Returns True if it changed something."""
    if not isinstance(data, dict):
        return False
    if name == "alarm":
        if not isinstance(data.get("alarm_id"), str):
            return False
        state.setdefault("alarms", {})[data["alarm_id"]] = data
        return True
    if name == "settings":
        if not isinstance(data.get("device_id"), str):
            return False
        d = ensure_device(state, data["device_id"])
        d["sensitivity"] = _sensitivity_or_none(data.get("sensitivity"))
        d["settings"] = data.get("settings") if isinstance(data.get("settings"), dict) else None
        return True
    if name == "record":
        machine = data.get("machine_id")
        machines = state.setdefault("machines", [])
        if isinstance(machine, str) and machine and machine not in machines:
            machines.append(machine)
            machines.sort()
            return True
    return False


def known_machines(state: dict) -> list:
    """Every machine with stored records, or a device on it now. Sorted."""
    out = set(state.get("machines") or [])
    out.update(d["machine_id"] for d in state["devices"].values() if d.get("machine_id"))
    return sorted(out)


def device_for_machine(state: dict, machine_id):
    """The device shown for a machine: the one picked by hand if it is on that
    machine, else the one on it heard from most recently. None if none is."""
    if not machine_id:
        return None
    on = {k: d for k, d in state["devices"].items() if d.get("machine_id") == machine_id}
    if not on:
        return None
    return shown_device_id({"devices": on, "selected_device_id": state.get("selected_device_id")})


def _alarm_number(alarm) -> int:
    m = re.match(r"^alarm-(\d+)$", str(alarm.get("alarm_id", "")))
    return int(m.group(1)) if m else 0


def alarms_for_machine(state: dict, machine_id) -> list:
    """That machine's alarms, newest `start` first (ties: the later-opened first), as the platform lists them."""
    alarms = [a for a in (state.get("alarms") or {}).values() if a.get("machine_id") == machine_id]

    def key(a):
        t = parse_time(a.get("start"))
        return (-math.inf if _nan(t) else t, _alarm_number(a))

    return sorted(alarms, key=key, reverse=True)


def sensitivity_of(state: dict, device_id) -> dict:
    """{"reported": what the device last said or None, "wanted": what the dashboard set or None}."""
    d = state["devices"].get(device_id) if device_id else None
    if not d:
        return {"reported": None, "wanted": None}
    wanted = d.get("settings") or {}
    return {"reported": d.get("sensitivity"), "wanted": _sensitivity_or_none(wanted.get("sensitivity"))}
