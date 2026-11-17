"""Bring a real device's data to the dashboard from the laptop side.

The contract (platform/CONTRACT.md) has the device send to the platform, and
device/intake/uploader.py does that once EFFENTE_PLATFORM_URL is set on the
board. Until it is, the device only serves its own data on port 8765
(device/intake/stream.py). This link reads that (`GET /latest`, twice a second)
and sends the platform the contract's messages on the device's behalf:

  not watching   a check-in (message 3) each time the device's status changes
  watching       the model (message 1), then a record (message 2) a second, each with the
                 vibration velocity (contract 9.2) and the heat of the device's newest feature row

When the thermal camera gave the heat, its newest images (all 24 x 32 pixels, at most 2)
are read from the device's `GET /heat` and go out as `heat.frames` (contract section 2).
A device without that address sends the summary alone.

Those images reach a dashboard a message at a time, so up to a second after they were taken. A
program that runs this link itself (the desktop app) can also be handed each image as soon as the
device serves it: `DeviceLink(..., on_heat=...)` asks `GET /heat` four times a second on a thread of
its own. Nothing about what the platform is sent changes.

A sensitivity set on the dashboard comes back in the platform's reply and is
passed to the device's `POST /settings`, as the uploader does on the board.

What the device's port does not carry, so this link cannot send:
  - the model's fault type texts and its threshold: message 1 goes out with
    `fault_types` empty and no `threshold`, and a record's `reason` is only what
    the device's own results line gives
The report agent (platform/report.py) reads the receiver's stored records and the
feature table of message 1, so it can report on what this link sent. UNVERIFIED on
the board.

The watching path is UNVERIFIED on the board. On 13 Nov 2026 it was run against
the device app itself (device/intake/device_app.py) on a laptop with simulated
conveyor sensors: recording, training, then watching, with a fault added. The
model was announced, and records with their diagnoses reached the receiver and
opened alarms. The check-in path was run against the board.

    python3 platform/link/device_link.py --device http://10.110.213.214:8765

Standard library only.
"""

from __future__ import annotations

import argparse
import http.client
import json
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime

POLL_S = 0.5  # the device's status changes once a second; the contract wants a message every 2 s
TIMEOUT_S = 2.0
LOG_REPEAT_S = 30.0
NO_HEAT_ADDRESS_S = 30.0  # a device whose port has no /heat is asked again this often
MAX_FRAMES = 2  # images in one message (contract section 2)
HEAT_POLL_S = 0.25  # on_heat: how often the device is asked for a new image (the camera gives 2 a second)
HEAT_RETRY_S = 5.0  # on_heat: after no answer, asked again this much later
# on_heat: the device answered with no image under 3 s old. Asked again this soon, because a real board's
# images come unevenly (16 Nov 2026: 1 to 13 s apart, a third of the answers empty) and 5 s lost the next ones.
HEAT_EMPTY_RETRY_S = 1.0
WALL_CLOCK_AFTER_S = 1e9  # as device/intake: above this, a device time is Unix time
CHECK_IN_MODES = ("display", "calibrating", "recording", "training")
QUIET = ("normal", "warming_up", "stopped")  # diagnoses that are not "something is wrong"
NO_TYPE_FITS = "abnormal, no known fault type fits"
# The limits of platform/CONTRACT.md's problems table. Every other problem is "outside_normal".
LIMITS = ("vibration_over_limit", "knocking", "too_hot", "heating_fast", "repeated_jolts")


def _iso(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def problems_of(problems, at: str) -> list[dict]:
    """The device's safety problems ({name, what, value, since}) in the contract's shape, limits first.

    `since` is device time: shown as its own time when that is Unix time, else as `at`."""
    out = []
    for p in problems or []:
        if not isinstance(p, dict) or not p.get("name"):
            continue
        since = p.get("since")
        if isinstance(since, str):
            when = since
        elif isinstance(since, (int, float)) and since > WALL_CLOCK_AFTER_S:
            when = _iso(since)
        else:
            when = at
        out.append({"name": p["name"], "kind": "limit" if p["name"] in LIMITS else "outside_normal",
                    "text": p.get("text") or p.get("what") or "", "value": p.get("value"), "since": when})
    out.sort(key=lambda p: p["kind"] != "limit")  # stable: the device's own order inside each kind
    return out


# Features copied from the device's newest row into a check-in's `readings`, under their own names.
ROW_READINGS = ("vib_velocity_mm_s", "snd_tone_hz", "snd_tone_db", "snd_centroid_hz")
# The thermal image's 48 blocks, row by row: th_cell_RC and th_spot_RC in effente/features.py.
HEAT_BLOCKS = tuple(f"{r}{c}" for r in range(6) for c in range(8))


def _number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def new_frames(got, after: str) -> list[dict]:
    """The usable images of a `GET /heat` reply taken after `after`, oldest first: each {at, min_c, step_c, data}.

    A device whose newest image is older than `after` has started its clock again (a restart with no
    network time): all its images count as new, or none would until this link is started again."""
    if not isinstance(got, dict) or not isinstance(got.get("frames"), list):
        return []
    usable = sorted((f for f in got["frames"] if isinstance(f, dict) and isinstance(f.get("at"), str)
                     and isinstance(f.get("data"), str)
                     and _number(f.get("min_c")) and _number(f.get("step_c"))), key=lambda f: f["at"])
    if usable and usable[-1]["at"] >= after:
        usable = [f for f in usable if f["at"] > after]
    return [{k: f[k] for k in ("at", "min_c", "step_c", "data")} for f in usable]


def heat_of(value):
    """The contract's `heat` (section 2, "Heat") from a feature row, or None when the row has no
    temperature at all. `value(name)` gives a feature's number or None.

    With `th_max_c` in the row the thermal camera gave this reading: `source` is "camera" and the
    summary is the row's th_* features, each null when the row lacks it (`cells` and `spots` are
    null unless all 48 blocks are there). Without it, the IMU's temperature alone.
    No `frames` here: DeviceLink adds the images it reads from the device's `GET /heat`.
    The camera branch is UNVERIFIED on the board: on 14 Nov 2026 its rows had no th_* values."""
    imu = value("imu_temp_c")
    imu = None if imu is None else round(imu, 2)
    if value("th_max_c") is None:
        return None if imu is None else {"source": "imu", "imu_temp_c": imu}

    def rounded(name):
        return None if value(name) is None else round(value(name), 2)

    def whole(name, below):
        v = value(name)
        return int(v) if v is not None and v == int(v) and 0 <= v < below else None

    def blocks(prefix):
        got = [value(prefix + b) for b in HEAT_BLOCKS]
        return None if any(g is None for g in got) else [round(g, 2) for g in got]

    frozen = value("th_frozen")
    return {"source": "camera", "imu_temp_c": imu, "max_c": rounded("th_max_c"),
            "hot_row": whole("th_hot_row", 24), "hot_col": whole("th_hot_col", 32),
            "background_c": rounded("th_background_c"), "cells": blocks("th_cell_"), "spots": blocks("th_spot_"),
            "frozen": frozen is not None and frozen >= 1.0, "scene_match": rounded("trend_scene_match")}


def row_extras(row, table) -> dict:
    """What the device's newest feature row adds to a check-in (contract section 3, "Readings"):
    `spectrum` (the vibration bands, with `spectrum_edges_hz`), `heat` (heat_of: the thermal camera's
    summary, or the IMU's temperature without one) and `readings` (ROW_READINGS). Each only when the
    row has the numbers. `table` is the device's `GET /features`, which names the row's values."""
    values = row.get("values") if isinstance(row, dict) else None
    if not isinstance(values, list) or not isinstance(table, dict):
        return {}
    if row.get("features_version") != table.get("features_version"):
        return {}  # the names would not match the values
    index = {f["name"]: f["index"] for f in table.get("features") or []
             if isinstance(f, dict) and isinstance(f.get("name"), str) and isinstance(f.get("index"), int)}

    def value(name):
        i = index.get(name)
        return values[i] if i is not None and 0 <= i < len(values) and _number(values[i]) else None

    out = {}
    spec = table.get("spectrum") if isinstance(table.get("spectrum"), dict) else {}
    bands = [value(n) for n in spec.get("features") or []]
    if bands and all(b is not None for b in bands):
        out["spectrum"] = [round(b, 3) for b in bands]
        if isinstance(spec.get("edges_hz"), list):
            out["spectrum_edges_hz"] = spec["edges_hz"]
    heat = heat_of(value)
    if heat is not None:
        out["heat"] = heat
    more = {name: round(value(name), 3) for name in ROW_READINGS if value(name) is not None}
    if more:
        out["readings"] = more
    return out


def _samples_coming(status: dict) -> bool:
    """Whether the device's screen shows vibration now, so its newest feature row is current."""
    readings = status.get("readings")
    return isinstance(readings, dict) and _number(readings.get("vib_mg"))


def velocity(status: dict, row=None, table=None):
    """`vib_velocity_mm_s` of the device's newest feature row, in mm/s (contract 9.2), or None when the
    row is missing, is for another feature table, has no such value, or is old (no samples coming in)."""
    if not _samples_coming(status):
        return None
    return row_extras(row, table).get("readings", {}).get("vib_velocity_mm_s")


def check_in(status: dict, row=None, table=None) -> dict:
    """Message 3 from the device's status while it is not watching. With the device's newest
    feature row and its feature table, the check-in also carries row_extras."""
    problems = problems_of(status.get("problems"), status["at"])
    mode = status.get("mode")
    c = {"machine_id": status["machine_id"], "mode": mode if mode in CHECK_IN_MODES else "display",
         "at": status["at"], "wrong": bool(problems), "problems": problems}
    if isinstance(status.get("sensitivity"), (int, float)):
        c["sensitivity"] = status["sensitivity"]
    if isinstance(status.get("readings"), dict):
        c["readings"] = dict(status["readings"])  # the numbers on the device's screen (contract section 3)
        if _samples_coming(status):
            extras = row_extras(row, table)
            c["readings"].update(extras.pop("readings", {}))
            c.update(extras)
    return c


def model_message(status: dict, table: dict) -> dict:
    """Message 1 from the device's status and its `GET /features`. The fault types are not on the
    device's port, so none are announced."""
    msg = {"device_id": status["device_id"], "machine_id": status["machine_id"],
           "model_version": status["model_version"], "features_version": table.get("features_version"),
           "fault_types": [],
           "features": [{"name": f["name"], "text": f.get("text", ""), "unit": f.get("unit", "")}
                        for f in table.get("features") or [] if isinstance(f, dict) and f.get("name")],
           "simulated": bool(status.get("simulated", False))}
    edges = (table.get("spectrum") or {}).get("edges_hz")
    if isinstance(edges, list):
        msg["spectrum_edges_hz"] = edges
    if isinstance(status.get("sensitivity"), (int, float)):
        msg["sensitivity"] = status["sensitivity"]
    return msg


def record(line: dict, status: dict, row=None, table=None) -> dict:
    """Message 2 from one of the device's results lines and its status, as uploader.py builds it.

    `readings` is the device's screen numbers plus `vib_velocity_mm_s` (contract 9.2), taken from
    `row`, the device's newest feature row, named by `table`. It is always there, null when unknown.
    `heat` (heat_of) comes from the same row, and is left out when the row is missing or old.

    `line["result"]` is the model's own result. The line's top-level diagnosis is an older
    contract's (four names), so it is not used."""
    result = line.get("result") if isinstance(line.get("result"), dict) else {}
    diagnosis = result.get("diagnosis") or "warming_up"
    problems = problems_of(status.get("problems"), line["at"])
    if isinstance(result.get("reason"), str):
        reason = result["reason"]
    elif diagnosis == "unrecognised_pattern":
        reason = NO_TYPE_FITS
    elif diagnosis in QUIET:
        reason = ""
    else:
        reason = line.get("reason") if isinstance(line.get("reason"), str) else ""
    rec = {"device_id": line["device_id"], "machine_id": line["machine_id"],
           "model_version": line["model_version"], "at": line["at"], "diagnosis": diagnosis,
           "confidence": result.get("confidence", 0.0), "unknown_score": result.get("unknown_score")}
    sensitivity = status.get("sensitivity")
    if isinstance(sensitivity, (int, float)):
        rec["sensitivity"] = sensitivity
        rec["alarm_line"] = round(4 ** (1 - 2 * sensitivity), 4)  # the contract's formula
    rec.update(reason=reason, features=dict(result.get("features") or {}))
    for k in ("diagnosis_this_minute", "fault_types"):
        if k in result:
            rec[k] = result[k]
    rec.update(abnormal_minutes=result.get("abnormal_minutes", 0), out_of_range=result.get("out_of_range", 0),
               note=result.get("note", ""), problems=problems, wrong=diagnosis not in QUIET or bool(problems))
    if isinstance(line.get("spectrum"), list):
        rec["spectrum"] = line["spectrum"]
    extras = row_extras(row, table) if _samples_coming(status) else {}
    if "heat" in extras:
        rec["heat"] = extras["heat"]
    readings = dict(status["readings"]) if isinstance(status.get("readings"), dict) else {}
    readings["vib_velocity_mm_s"] = extras.get("readings", {}).get("vib_velocity_mm_s")
    rec["readings"] = readings
    rec["simulated"] = bool(line.get("simulated", False))
    return rec


class DeviceLink:
    """Reads one device's port, sends the platform its messages. start() runs it on a thread.

    `on_heat(machine_id, images)`, when given, is called from a second thread with each of the thermal
    camera's images as soon as the device serves it (heat_step), oldest first, each as in `heat.frames`."""

    def __init__(self, device_url: str, platform_url: str, poll_s: float = POLL_S, timeout: float = TIMEOUT_S,
                 log=None, on_heat=None, heat_poll_s: float = HEAT_POLL_S):
        self.device = device_url.rstrip("/")
        self.platform = platform_url.rstrip("/")
        self.poll_s, self.timeout = poll_s, timeout
        self.log = log or (lambda m: print(f"device link: {m}", flush=True))
        self.stats = {"check_ins": 0, "records": 0, "models": 0, "refused": 0, "settings": 0}
        self.on_heat, self.heat_poll_s = on_heat, heat_poll_s
        self.machine_id: str | None = None  # of the device's newest status
        self._heat_at = ""  # `at` of the newest image handed to on_heat
        self._heat_wait_until = 0.0  # on_heat: no answer or no image last time, not asked again before this
        self._heat_thread: threading.Thread | None = None
        self.device_reachable: bool | None = None
        self.last_message = ""  # the newest thing said to the operator
        self._status_at = None
        self._line_at = None
        self._announced = None  # model_version the platform has from us
        self._table = None  # the device's GET /features, read once
        self._frame_at = ""  # `at` of the newest camera image the platform has from us
        self._no_heat_until = 0.0  # the device's port had no /heat: not asked again before this
        self._logged: dict[str, float] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ---- running ------------------------------------------------------------
    def start(self) -> DeviceLink:
        if self._thread is None:
            self._thread = threading.Thread(target=self.run, name="effente-device-link", daemon=True)
            self._thread.start()
        if self.on_heat is not None and self._heat_thread is None:
            self._heat_thread = threading.Thread(target=self.run_heat, name="effente-device-heat", daemon=True)
            self._heat_thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(self.timeout * 2 + 1.0)
            self._thread = None
        if self._heat_thread is not None:
            self._heat_thread.join(self.timeout + 1.0)
            self._heat_thread = None

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                self.step()
            except Exception as e:  # noqa: BLE001  one odd reply must not end the link
                self._say("step", f"{type(e).__name__}: {e}")
            self._stop.wait(self.poll_s)

    def run_heat(self) -> None:
        while not self._stop.wait(self.heat_poll_s):
            try:
                self.heat_step()
            except Exception as e:  # noqa: BLE001  one odd reply or a failing listener must not end the thread
                self._say("heat", f"{type(e).__name__}: {e}")

    def heat_step(self) -> None:
        """Hand on_heat the device's images it has not had yet. Asked only once the device's status has
        named its machine; a device that does not answer `/heat` is asked again HEAT_RETRY_S later,
        one that answers with no image HEAT_EMPTY_RETRY_S later."""
        machine = self.machine_id
        if self.on_heat is None or machine is None or time.monotonic() < self._heat_wait_until:
            return
        got = self._get(self.device + "/heat")
        new = new_frames(got, self._heat_at)
        if not new:
            if not (isinstance(got, dict) and got.get("frames")):  # no answer, no camera, or its images are old
                answered = isinstance(got, dict) and isinstance(got.get("frames"), list)
                self._heat_wait_until = time.monotonic() + (HEAT_EMPTY_RETRY_S if answered else HEAT_RETRY_S)
            return
        self._heat_at = new[-1]["at"]
        self.on_heat(machine, new)

    def step(self) -> None:
        """Read the device once and send what is new."""
        latest = self._get(self.device + "/latest")
        if not isinstance(latest, dict):
            if self.device_reachable is not False:
                self._tell(f"device {self.device} not reachable; trying again")
            self.device_reachable = False
            return
        if self.device_reachable is not True:
            self._tell(f"reading device {self.device}")
        self.device_reachable = True
        status = latest.get("status")
        if not isinstance(status, dict) or not all(
                isinstance(status.get(k), str) and status[k] for k in ("device_id", "machine_id", "at")):
            return self._say("status", "the device's status has no device_id, machine_id or at yet")
        self.machine_id = status["machine_id"]
        line = latest.get("results")
        if status.get("mode") == "watching":
            if isinstance(line, dict) and line.get("at") and line["at"] != self._line_at:
                self._send_record(line, status, latest.get("features"))
        elif status["at"] != self._status_at:  # a status that stops changing means a device that stopped
            message = check_in(status, latest.get("features"), self._feature_table())
            newest = self._add_frames(message)
            reply = self._post(f"/api/devices/{urllib.parse.quote(status['device_id'], safe='')}", message)
            if reply is not None:
                self._frame_at = newest
                self._status_at = status["at"]
                self.stats["check_ins"] += 1
                self._settings(reply, status)

    def _send_record(self, line: dict, status: dict, row=None) -> None:
        version = line.get("model_version")
        if not (isinstance(version, str) and version and isinstance(line.get("device_id"), str)
                and isinstance(line.get("machine_id"), str)):
            return self._say("line", "the device's results line has no device_id, machine_id or model_version")
        if self._announced != version and not self._announce(dict(status, model_version=version)):
            return
        message = record(line, status, row, self._feature_table())
        newest = self._add_frames(message)
        reply = self._post("/api/records", message)
        if reply is None:
            return
        self._frame_at = newest
        self._line_at = line["at"]
        self.stats["records"] += 1
        if reply.get("need_model") is True:
            self._announced = None  # announced again with the next record
        self._settings(reply, status)

    def _add_frames(self, message: dict) -> str:
        """Give a message whose heat is the camera's the device's newest images (`GET /heat`): those the
        platform does not have from us yet, oldest first, at most MAX_FRAMES. Returns the `at` of the newest
        image the platform will then have (to keep once it took the message)."""
        heat = message.get("heat")
        if not isinstance(heat, dict) or heat.get("source") != "camera" or time.monotonic() < self._no_heat_until:
            return self._frame_at
        got = self._get(self.device + "/heat")
        if not isinstance(got, dict) or not isinstance(got.get("frames"), list):
            self._no_heat_until = time.monotonic() + NO_HEAT_ADDRESS_S  # an older device: the summary alone
            return self._frame_at
        new = new_frames(got, self._frame_at)[-MAX_FRAMES:]
        if not new:
            return self._frame_at
        heat["frames"] = new
        return new[-1]["at"]

    def _feature_table(self):
        """The device's feature table, read once (it changes only with the device's code)."""
        if self._table is None:
            table = self._get(self.device + "/features")
            self._table = table if isinstance(table, dict) else None
        return self._table

    def _announce(self, status: dict) -> bool:
        table = self._feature_table()
        if not isinstance(table, dict):
            self._say("features", "the device's feature table could not be read; no model announced")
            return False
        if self._post("/api/model", model_message(status, table)) is None:
            return False
        self._announced = status["model_version"]
        self.stats["models"] += 1
        return True

    def _settings(self, reply: dict, status: dict) -> None:
        """A sensitivity set on the dashboard goes to the device when it differs from the device's."""
        wanted = (reply.get("settings") or {}).get("sensitivity") if isinstance(reply.get("settings"), dict) else None
        if not isinstance(wanted, (int, float)) or isinstance(wanted, bool):
            return
        have = status.get("sensitivity")
        if isinstance(have, (int, float)) and abs(have - wanted) < 1e-9:
            return
        answer = self._request(self.device + "/settings", {"sensitivity": wanted})
        if isinstance(answer, dict) and answer.get("ok") is True:
            self.stats["settings"] += 1
            self._tell(f"sensitivity {wanted} from the dashboard set on the device")
        else:
            self._say("settings", f"the device did not take sensitivity {wanted}")

    # ---- HTTP ---------------------------------------------------------------
    def _get(self, url: str):
        return self._request(url)

    def _post(self, path: str, payload):
        """The platform's reply when it took the message, else None (said once in a while)."""
        reply = self._request(self.platform + path, payload, errors=True)
        if isinstance(reply, dict) and reply.get("ok") is True:
            return reply
        self.stats["refused"] += 1
        why = reply.get("error") if isinstance(reply, dict) else "no answer"
        self._say("platform " + path.split("/")[2], f"the platform did not take {path}: {why}")
        return None

    def _request(self, url: str, payload=None, errors: bool = False):
        """JSON from a GET, or from a POST of `payload`. None when there is no usable answer;
        with `errors`, an HTTP error's JSON body is returned too."""
        data = None if payload is None else json.dumps(payload, allow_nan=False, separators=(",", ":")).encode()
        req = urllib.request.Request(url, data=data, method="GET" if data is None else "POST",
                                     headers={"Content-Type": "application/json"} if data else {})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            with e:
                try:
                    return json.loads(e.read()) if errors else None
                except ValueError:
                    return None
        except (OSError, ValueError, http.client.HTTPException):
            return None

    # ---- telling the operator -------------------------------------------------
    def _tell(self, message: str) -> None:
        self.last_message = message
        self.log(message)

    def _say(self, key: str, message: str) -> None:
        """As _tell, but the same kind of trouble at most once every LOG_REPEAT_S."""
        now = time.monotonic()
        if now - self._logged.get(key, -float("inf")) >= LOG_REPEAT_S:
            self._logged[key] = now
            self._tell(message)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--device", required=True, help="the device's own port, for example http://10.110.213.214:8765")
    parser.add_argument("--url", default="http://localhost:8000", help="the platform (default http://localhost:8000)")
    args = parser.parse_args(argv)
    link = DeviceLink(args.device, args.url)
    try:
        link.run()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
