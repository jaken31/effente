"""Send the device's data to the laptop platform (platform/CONTRACT.md, version 3.1).

The device only makes outgoing requests. Three messages go out:

  1. POST /api/model           the model: at start, after each training, and whenever a reply
                               says "need_model": true
  2. POST /api/records         while watching, one record a second of data, with the diagnosis,
                               the safety checks' problems, wrong, sensitivity and alarm_line
  3. POST /api/devices/{id}    while not watching, a check-in every second (the contract asks for
                               one at least every 2 s): mode, wrong and the problems

The sensor pipeline only calls `announce` and `on_row`, which build a small dict and put it on a
bounded queue; a thread of its own does the sending. When the platform cannot be reached, the
newest records are kept (the oldest drop, counted) and go out as JSON arrays when it comes back.
A check-in that could not be sent is dropped: the next one says the same, newer. Failures are
logged, never raised. A reply that carries "settings" is handed to `on_settings` (the device
applies a sensitivity that differs from its own).

The platform's address comes from EFFENTE_PLATFORM_URL. Unset or empty means nothing is sent.
Standard library only.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

from effente import fault_types as FT
from effente import features as F
from effente import safety as SF
from device.intake.stream import json_safe

ENV_URL = "EFFENTE_PLATFORM_URL"
TIMEOUT_S = 2.0
RECORD_EVERY_S = 1.0  # seconds of data between records while watching
CHECKIN_EVERY_S = 1.0  # seconds between check-ins while not watching (the contract's most is 2)
BACKLOG = 900  # records kept while the platform is down: the newest 15 minutes
BATCH = 120  # records per request when catching up
RETRY_S = (1.0, 2.0, 5.0)  # wait before trying an unreachable platform again; the last one repeats
LOG_REPEAT_S = 30.0  # the same failure is logged again at most this often
WALL_CLOCK_AFTER_S = 1e9  # same rule as live.log_record: above this, device time is Unix time
QUIET = ("normal", "warming_up", "stopped")  # diagnoses that are not "something is wrong"
NO_TYPE_FITS = "abnormal, no known fault type fits"
_SPECTRUM = [F.INDEX[f"vib_band_{i:02d}"] for i in range(len(F.VIB_EDGES_HZ) - 1)]
_SENSORS = {t.name: t for t in FT.TYPES}


def iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def model_version(path, machine_id: str) -> str:
    """Changes whenever the model file does: the machine, the file's name and a hash of its bytes."""
    p = Path(path)
    return f"{machine_id}-{p.stem}-{hashlib.sha1(p.read_bytes()).hexdigest()[:8]}"


def model_message(d: dict, version: str, device_id: str, machine_id: str, sensitivity: float | None,
                  simulated: bool) -> dict:
    """Message 1 from a model file's contents (effente.trainer's format)."""
    types = []
    for ft in d.get("fault_types", []):
        known = _SENSORS.get(ft["name"])
        group = ft.get("group") or ("sensing" if ft.get("sensing") else known.sensors[0] if known else None)
        t = {"name": ft["name"], "text": ft.get("text", ""), "validated": bool(ft.get("validated", False))}
        if group:
            t["group"] = group
        types.append(t)
    msg = {"device_id": device_id, "machine_id": machine_id, "model_version": version,
           "features_version": d.get("features_version"), "threshold": d.get("threshold"),
           "fault_types": types,
           "features": [{"name": f["name"], "text": f.get("text", ""), "unit": f.get("unit", "")}
                        for f in d.get("feature_table", [])],
           "spectrum_edges_hz": [round(float(e), 3) for e in F.VIB_EDGES_HZ], "simulated": simulated}
    for k in ("library_entry", "source"):  # only as the model file gives them
        if d.get(k) is not None:
            msg[k] = d[k]
    if sensitivity is not None:
        msg["sensitivity"] = sensitivity
    return msg


def contract_problems(problems: list[dict], t0: float, at: datetime) -> list[dict]:
    """effente.safety's problems in the contract's shape: limits first, `since` as ISO UTC.

    safety.py keeps `since` in device time (the row's t0); it is placed on the record's clock by
    its distance from t0, so it also works when device time is not Unix time (a replay)."""
    out = []
    for p in problems:
        name, since = p["name"], p.get("since")
        when = at - timedelta(seconds=max(0.0, float(t0) - float(since))) if since is not None else at
        out.append({"name": name, "kind": "limit" if name in SF.UNIVERSAL else "outside_normal",
                    "text": p.get("what") or SF.WHAT.get(name, ("",))[0], "value": p.get("value"),
                    "since": iso(when)})
    out.sort(key=lambda p: p["kind"] != "limit")  # stable: keeps the checks' own order inside each kind
    return out


class _Reply:
    def __init__(self, ok: bool, reachable: bool, body: dict | None = None, error: str = ""):
        self.ok, self.reachable, self.body, self.error = ok, reachable, body or {}, error


class Uploader:
    """Call start(), then announce() and on_row() from the device. Nothing here raises into the caller."""

    def __init__(self, base_url: str, device_id: str = "effente-01", machine_id: str = "machine-1", *,
                 simulated: bool = False, on_settings=None, timeout: float = TIMEOUT_S, backlog: int = BACKLOG,
                 batch: int = BATCH, record_every_s: float = RECORD_EVERY_S,
                 checkin_every_s: float = CHECKIN_EVERY_S, retry_s=RETRY_S, log=None):
        self.base = base_url.rstrip("/")
        self.device_id, self.machine_id, self.simulated = device_id, machine_id, simulated
        self.on_settings = on_settings  # called with the reply's "settings" dict
        self.timeout, self.batch = timeout, batch
        self.record_every_s, self.checkin_every_s, self.retry_s = record_every_s, checkin_every_s, tuple(retry_s)
        self.log = log or (lambda m: print(f"uploader: {m}", flush=True))
        self.lock = threading.Lock()
        self.records: deque = deque(maxlen=backlog)
        self.checkin: dict | None = None
        self.model: dict | None = None
        self.need_model = False
        self.texts: dict[str, str] = {t.name: t.text for t in FT.TYPES}
        self.stats = {"records_sent": 0, "records_dropped": 0, "records_rejected": 0, "checkins_sent": 0,
                      "checkins_failed": 0, "models_sent": 0, "requests_failed": 0, "build_errors": 0}
        self.reachable: bool | None = None
        self._last_record_t: float | None = None
        self._last_checkin = -float("inf")
        self._fails = 0
        self._retry_at = 0.0
        self._logged: dict[str, float] = {}
        self._busy = False  # the sender is working: something taken off the queues may be in flight
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @classmethod
    def from_env(cls, device_id: str, machine_id: str, env=None, **kw) -> "Uploader | None":
        """An uploader for EFFENTE_PLATFORM_URL, or None when it is unset or empty."""
        url = (os.environ if env is None else env).get(ENV_URL, "").strip()
        return cls(url, device_id, machine_id, **kw) if url else None

    def start(self) -> "Uploader":
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="uploader", daemon=True)
            self._thread.start()
        return self

    def stop(self, drain_s: float = 0.0) -> None:
        """Stop sending. With drain_s, first wait that long for what is queued to go out."""
        if drain_s > 0:
            self.wait_idle(drain_s)
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(self.timeout + 1.0)

    def wait_idle(self, timeout: float) -> bool:
        """Wait until nothing is queued or in flight (True), or until timeout (False)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.lock:
                if (not self._busy and not self.records and self.checkin is None
                        and not (self.need_model and self.model)):
                    return True
            time.sleep(0.02)
        return False

    # ------------------------------------------------------------ called by the device
    def announce(self, model_path, d: dict, sensitivity: float | None = None) -> None:
        """A model is in use (at start, or just trained): send message 1 for it."""
        try:
            version = model_version(model_path, self.machine_id)
            msg = model_message(d, version, self.device_id, self.machine_id, sensitivity, self.simulated)
            with self.lock:
                self.model, self.need_model = msg, True
                self.texts.update({t["name"]: t["text"] for t in msg["fault_types"] if t.get("text")})
            self._wake.set()
        except Exception as e:  # noqa: BLE001  a model that cannot be described must not stop the device
            self.stats["build_errors"] += 1
            self._say("announce", f"model not announced: {type(e).__name__}: {e}")

    def on_row(self, t0: float, x, mode: str, result: dict | None, problems: list[dict],
               sensitivity: float | None = None, alarm_line: float | None = None) -> None:
        """Every feature row. Queues a record a second while watching, a check-in a second otherwise."""
        try:
            if mode == "watching":
                if result is None or (self._last_record_t is not None and
                                      0 <= t0 - self._last_record_t < self.record_every_s - 1e-6):
                    return
                with self.lock:
                    version = self.model["model_version"] if self.model else None
                if version is None:
                    self._say("no model", "watching without an announced model: no records sent")
                    return
                self._last_record_t = t0
                rec = self.record(t0, x, result, problems, version, sensitivity, alarm_line)
                with self.lock:
                    if len(self.records) == self.records.maxlen:
                        self.stats["records_dropped"] += 1  # the deque drops its oldest
                    self.records.append(rec)
            else:
                self._last_record_t = None
                now = time.monotonic()
                if now - self._last_checkin < self.checkin_every_s:
                    return
                self._last_checkin = now
                c = self.check_in(t0, mode, problems, sensitivity)
                with self.lock:
                    self.checkin = c
            self._wake.set()
        except Exception as e:  # noqa: BLE001
            self.stats["build_errors"] += 1
            self._say("build", f"could not build a message: {type(e).__name__}: {e}")

    # ------------------------------------------------------------ the messages
    @staticmethod
    def _at(t0: float) -> datetime:
        return (datetime.fromtimestamp(t0, timezone.utc) if t0 > WALL_CLOCK_AFTER_S
                else datetime.now(timezone.utc))

    def record(self, t0, x, result: dict, problems: list[dict], version: str, sensitivity=None,
               alarm_line=None) -> dict:
        """Message 2 from one watching row: the Watcher's result and the safety checks' problems."""
        at = self._at(t0)
        diag = result.get("diagnosis", "warming_up")
        probs = contract_problems(problems, t0, at)
        if diag == "unrecognised_pattern":
            reason = NO_TYPE_FITS
        elif diag in QUIET:
            reason = ""
        else:
            reason = self.texts.get(diag, "")
        rec = {"device_id": self.device_id, "machine_id": self.machine_id, "model_version": version,
               "at": iso(at), "diagnosis": diag, "confidence": result.get("confidence", 0.0),
               "unknown_score": result.get("unknown_score")}
        if sensitivity is not None:
            rec["sensitivity"] = sensitivity
            if alarm_line is not None:
                rec["alarm_line"] = round(float(alarm_line), 4)
        rec.update(reason=reason, features=dict(result.get("features") or {}))
        for k in ("diagnosis_this_minute", "fault_types"):
            if k in result:
                rec[k] = result[k]
        rec.update(abnormal_minutes=result.get("abnormal_minutes", 0), out_of_range=result.get("out_of_range", 0),
                   note=result.get("note", ""), problems=probs, wrong=diag not in QUIET or bool(probs))
        if x is not None:
            bands = [float(x[i]) for i in _SPECTRUM]
            if all(b == b for b in bands):  # no NaN: a band the device cannot compute leaves the spectrum out
                rec["spectrum"] = [round(b, 3) for b in bands]
        rec["simulated"] = self.simulated
        return json_safe(rec)

    def check_in(self, t0, mode: str, problems: list[dict], sensitivity=None) -> dict:
        """Message 3 while not watching."""
        at = self._at(t0)
        probs = contract_problems(problems, t0, at)
        c = {"machine_id": self.machine_id, "mode": mode, "at": iso(at), "wrong": bool(probs), "problems": probs}
        if sensitivity is not None:
            c["sensitivity"] = sensitivity
        return json_safe(c)

    # ------------------------------------------------------------ sending
    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(0.5)
            self._wake.clear()
            if self._stop.is_set():
                break
            if time.monotonic() < self._retry_at:
                continue
            with self.lock:
                self._busy = True
            try:
                self._send_pending()
            except Exception as e:  # noqa: BLE001  the sender must keep going whatever happens
                self._say("sender", f"{type(e).__name__}: {e}")
            finally:
                with self.lock:
                    self._busy = False

    def _send_pending(self) -> None:
        with self.lock:
            model = self.model if self.need_model else None
        if model is not None:
            r = self._post("/api/model", model)
            if not r.reachable:
                return self._down(r)
            self._up()
            with self.lock:
                if self.model is model:
                    self.need_model = False
            if r.ok:
                self.stats["models_sent"] += 1
            else:
                self._say("model rejected", f"model {model['model_version']} refused: {r.error}")
        while True:
            with self.lock:
                batch = [self.records.popleft() for _ in range(min(self.batch, len(self.records)))]
            if not batch:
                break
            r = self._post("/api/records", batch[0] if len(batch) == 1 else batch)
            if not r.reachable:
                self._requeue(batch)
                return self._down(r)
            self._up()
            if r.ok:
                self.stats["records_sent"] += len(batch)
            else:  # the platform is up but refused them: sending again would be refused again
                self.stats["records_rejected"] += len(batch)
                self._say("records rejected", f"{len(batch)} record(s) refused: {r.error}")
            self._handle(r)
            if self._stop.is_set():
                return
        with self.lock:
            c, self.checkin = self.checkin, None
        if c is not None:
            r = self._post(f"/api/devices/{urllib.parse.quote(self.device_id, safe='')}", c)
            if not r.reachable:
                self.stats["checkins_failed"] += 1
                return self._down(r)
            self._up()
            if r.ok:
                self.stats["checkins_sent"] += 1
            else:
                self.stats["checkins_failed"] += 1
                self._say("check-in rejected", f"check-in refused: {r.error}")
            self._handle(r)

    def _requeue(self, batch: list[dict]) -> None:
        """Put records that could not be sent back in front, keeping only the newest."""
        with self.lock:
            merged = batch + list(self.records)
            keep = merged[-self.records.maxlen:]
            self.stats["records_dropped"] += len(merged) - len(keep)
            self.records.clear()
            self.records.extend(keep)

    def _handle(self, r: _Reply) -> None:
        if r.body.get("need_model") is True:
            with self.lock:
                self.need_model = self.model is not None
            self._wake.set()
        settings = r.body.get("settings")
        if isinstance(settings, dict) and self.on_settings is not None:
            try:
                self.on_settings(settings)
            except Exception as e:  # noqa: BLE001
                self._say("settings", f"settings from the platform not applied: {e}")

    def _post(self, path: str, payload) -> _Reply:
        data = json.dumps(payload, allow_nan=False, separators=(",", ":")).encode()
        req = urllib.request.Request(self.base + path, data=data, method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:  # the platform answered, with an error
            try:
                body = json.loads(e.read() or b"{}")
            except (ValueError, OSError):
                body = {}
            body = body if isinstance(body, dict) else {}
            return _Reply(False, True, body, f"HTTP {e.code} {body.get('error', '')}".strip())
        except (OSError, ValueError, http.client.HTTPException) as e:  # refused, timed out, no route, bad URL, a broken reply
            self.stats["requests_failed"] += 1
            return _Reply(False, False, error=f"{type(e).__name__}: {e}")
        try:
            body = json.loads(raw)
        except ValueError:
            return _Reply(False, True, error="reply was not JSON")
        if not isinstance(body, dict):
            return _Reply(False, True, error="reply was not a JSON object")
        return _Reply(body.get("ok") is True, True, body, "" if body.get("ok") is True else str(body.get("error", "")))

    def _down(self, r: _Reply) -> None:
        if self.reachable is not False:
            self.log(f"platform {self.base} not reachable ({r.error}); keeping the newest records")
        self.reachable = False
        self._retry_at = time.monotonic() + self.retry_s[min(self._fails, len(self.retry_s) - 1)]
        self._fails += 1

    def _up(self) -> None:
        if self.reachable is False:
            self.log(f"platform {self.base} reachable again; sending the records held back")
        self.reachable, self._fails, self._retry_at = True, 0, 0.0

    def _say(self, key: str, msg: str) -> None:
        now = time.monotonic()
        if now - self._logged.get(key, -float("inf")) >= LOG_REPEAT_S:
            self._logged[key] = now
            self.log(msg)
