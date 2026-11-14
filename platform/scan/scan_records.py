"""The scan store: takes a scan a phone uploads, keeps it, has it converted, and answers for it
(docs/scan-and-log-plan.md, 5.1, 5.3, 5.4 and C1). Standard library only.

Kept here, in platform/scan, so the receiver's stubs (platform/server/scan_store.py and scan_api.py,
made by step 0) only have to call it. It knows nothing about HTTP: the receiver hands it a way to read
the body, and gets back a scan or a ScanError whose message is the 400's "error".

On disk, under the receiver's data folder (never committed):
    scans.jsonl              one scan per line, as in 5.4; the newest line for a scan_id wins
    scans/<scan_id>/         the file as the phone sent it (scan.usdz or scan.glb), and model.glb
    scans/incoming/          uploads still arriving; emptied at start
"""

import datetime as dt
import json
import os
import re
import secrets
import shutil
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from scan_convert import Converter, describe  # noqa: E402
from scan_glb import BadGlb, file_format  # noqa: E402

CONTRACT = "3.4"
MAX_SCAN_BYTES = 268435456  # 256 MB
DEFAULT_LIMIT = 20
MAX_LIMIT = 200
CHUNK = 65536
MACHINE_ID = re.compile(r"^[a-z0-9-]{1,40}$")
CLIENT_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")
SCAN_ID = re.compile(r"^scan-[A-Za-z0-9-]{1,60}$")


class ScanError(Exception):
    """A bad request. The message is a plain sentence: the receiver sends it as the 400's "error"."""


def iso(timestamp):
    return dt.datetime.fromtimestamp(timestamp, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_time(text):
    """ISO 8601 to a UTC timestamp, or None. No zone means UTC (as in receiver_store.parse_time)."""
    if not isinstance(text, str) or not text:
        return None
    try:
        value = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return value.timestamp()


def _now():
    return dt.datetime.now(dt.timezone.utc).timestamp()


def _text(value, limit=200):
    """A short label from a query string: trimmed, no control characters."""
    value = "".join(ch for ch in str(value or "") if ch.isprintable()).strip()
    return value[:limit]


class ScanStore:
    """Every scan the receiver holds.

        store = ScanStore(data_dir, publish=receiver.publish)
        scan, duplicate = store.receive("conveyor-07", {"client_id": "..."}, length, body.read)

    `publish(name, data)` sends a stream event; the store sends "scan" when a scan is stored and each
    time its model.status changes. `converter` is a scan_convert.Converter, made here if left out."""

    def __init__(self, data_dir, publish=None, converter=None, now=_now, max_bytes=MAX_SCAN_BYTES):
        self.data_dir = str(data_dir)
        self.folder = os.path.join(self.data_dir, "scans")
        self.incoming = os.path.join(self.folder, "incoming")
        self.log_path = os.path.join(self.data_dir, "scans.jsonl")
        self.max_bytes = int(max_bytes)
        self._publish = publish or (lambda name, data: None)
        self._now = now
        self._lock = threading.RLock()
        self._scans = {}        # scan_id -> scan
        self._by_client = {}    # client_id -> scan_id, for the first scan with that client_id
        self._receiving = set()  # client_ids whose upload is arriving now
        self._converter = converter if converter is not None else Converter(self._on_convert)
        os.makedirs(self.folder, exist_ok=True)
        shutil.rmtree(self.incoming, ignore_errors=True)  # uploads cut off by the last stop
        os.makedirs(self.incoming, exist_ok=True)
        self._load()

    # ---- 5.1 hello ---------------------------------------------------------------------------------------

    def hello(self):
        return {"ok": True, "service": "effente", "contract": CONTRACT, "now": iso(self._now()),
                "max_scan_bytes": self.max_bytes}

    # ---- 5.3 upload --------------------------------------------------------------------------------------

    def check_upload(self, machine_id, query, content_length):
        """Everything that can be refused before a byte of the body is read. Returns the scan if this
        client_id was already stored (the body is then not wanted), else None. Raises ScanError."""
        if not isinstance(machine_id, str) or not MACHINE_ID.match(machine_id):
            raise ScanError("machine id must be 1 to 40 lowercase letters, digits or -")
        client_id = str(query.get("client_id") or "")
        if not CLIENT_ID.match(client_id):
            raise ScanError("client_id is required: 8 to 64 letters, digits or -")
        if query.get("at") and parse_time(query.get("at")) is None:
            raise ScanError("at is not a time: use ISO 8601, for example 2026-11-14T18:14:40Z")
        with self._lock:
            first = self._by_client.get(client_id)
            if first:
                return dict(self._scans[first])
        if content_length is None:
            raise ScanError("Content-Length is required; chunked uploads are not accepted")
        try:
            length = int(content_length)
        except (TypeError, ValueError):
            raise ScanError("Content-Length is not a number") from None
        if length < 1:
            raise ScanError("the scan file is empty")
        if length > self.max_bytes:
            raise ScanError(f"the scan file is too large: {length} bytes, and the limit is {self.max_bytes}")
        return None

    def receive(self, machine_id, query, content_length, read):
        """Store one uploaded scan. `read(n)` gives up to n bytes of the body. Returns (scan, duplicate).

        The body goes to a temporary file as it arrives and is never held in memory. It is checked by its
        first four bytes, not by its Content-Type. A ScanError means nothing was kept; the receiver must
        then close the connection, because the rest of the body was not read."""
        first = self.check_upload(machine_id, query, content_length)
        if first is not None:
            return first, True
        length = int(content_length)
        client_id = str(query["client_id"])
        with self._lock:
            if client_id in self._receiving:
                raise ScanError("this scan is already being uploaded")
            self._receiving.add(client_id)
        temp = os.path.join(self.incoming, f"{secrets.token_hex(8)}.part")
        try:
            kind = self._read_body(temp, length, read)
            scan = self._keep(machine_id, query, kind, temp, length)
        except BaseException:
            _remove(temp)
            raise
        finally:
            with self._lock:
                self._receiving.discard(client_id)
        self._publish("scan", dict(scan))
        self._start_model(scan["scan_id"])
        return self.scan(scan["scan_id"]), False

    def _read_body(self, temp, length, read):
        kind, got = None, 0
        with open(temp, "wb") as f:
            while got < length:
                chunk = read(min(CHUNK, length - got))
                if not chunk:
                    raise ScanError(f"the upload was cut off after {got} of {length} bytes")
                if kind is None:
                    head = chunk
                    while len(head) < 4 and got + len(head) < length:  # a first read shorter than 4 bytes
                        more = read(min(4 - len(head), length - got - len(head)))
                        if not more:
                            break
                        head += more
                    chunk = head
                    kind = file_format(chunk)
                    if kind is None:
                        raise ScanError("not a scan file: it must be a USDZ or a GLB file")
                f.write(chunk)
                got += len(chunk)
        return kind

    def _keep(self, machine_id, query, kind, temp, length):
        received = self._now()
        captured = parse_time(query.get("at"))
        with self._lock:
            scan_id = None
            while scan_id is None or scan_id in self._scans:
                stamp = dt.datetime.fromtimestamp(received, dt.timezone.utc).strftime("%Y%m%d%H%M%S")
                scan_id = f"scan-{stamp}-{secrets.token_hex(2)}"
            folder = os.path.join(self.folder, scan_id)
            os.makedirs(folder)
            os.replace(temp, os.path.join(folder, f"scan.{kind}"))
            scan = {
                "scan_id": scan_id,
                "machine_id": machine_id,
                "client_id": str(query["client_id"]),
                "at": iso(captured if captured is not None else received),
                "received_at": iso(received),
                "format": kind,
                "file": {"name": _text(query.get("name")) or f"scan.{kind}", "bytes": length},
                "app": _text(query.get("app")),
                "model": {"status": "waiting", "reason": ""},
            }
            self._scans[scan_id] = scan
            self._by_client.setdefault(scan["client_id"], scan_id)
            self._append(scan)
            return dict(scan)

    # ---- the model ---------------------------------------------------------------------------------------

    def _start_model(self, scan_id):
        """A GLB upload is its own model, ready at once. A USDZ goes to the converter."""
        with self._lock:
            scan = self._scans.get(scan_id)
            if scan is None:
                return
            kind = scan["format"]
        source = self.file_path(scan_id)
        target = os.path.join(self.folder, scan_id, "model.glb")
        if kind == "glb":
            try:
                if not os.path.exists(target):
                    try:
                        os.link(source, target)  # the same bytes, not a second copy
                    except OSError:
                        shutil.copyfile(source, target)
                self._set_model(scan_id, "ready", describe(target))
            except (BadGlb, OSError) as e:
                _remove(target)
                self._set_model(scan_id, "failed", {"reason": f"the GLB file cannot be read: {e}"})
        else:
            self._converter.submit(scan_id, source, target)

    def _on_convert(self, scan_id, status, details):
        self._set_model(scan_id, status, details)

    def _set_model(self, scan_id, status, details):
        with self._lock:
            scan = self._scans.get(scan_id)
            if scan is None:
                return
            model = {"status": status, "reason": str(details.get("reason", "")) if status == "failed" else ""}
            if status == "ready":
                model["bytes"] = details["bytes"]
                model["bounds"] = details["bounds"]
            if model == scan["model"]:
                return
            scan = dict(scan, model=model)
            self._scans[scan_id] = scan
            self._append(scan)
            event = dict(scan)
        self._publish("scan", event)

    def convert_again(self, scan_id):
        """Try a failed (or waiting) scan's conversion again, for example once Blender is installed."""
        scan = self.scan(scan_id)
        if scan["model"]["status"] in ("ready", "converting"):
            return scan
        self._set_model(scan_id, "waiting", {})
        self._start_model(scan_id)
        return self.scan(scan_id)

    # ---- 5.4 reading -------------------------------------------------------------------------------------

    def scan(self, scan_id):
        with self._lock:
            scan = self._scans.get(scan_id) if isinstance(scan_id, str) else None
            if scan is None:
                raise ScanError("no such scan")
            return dict(scan)

    def scans(self, machine_id, limit=None):
        """That machine's scans, newest received_at first."""
        if limit in (None, ""):
            limit = DEFAULT_LIMIT
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            raise ScanError("limit must be a number") from None
        if not 1 <= limit <= MAX_LIMIT:
            raise ScanError(f"limit must be from 1 to {MAX_LIMIT}")
        with self._lock:
            found = [dict(s) for s in self._scans.values() if s["machine_id"] == machine_id]
        found.sort(key=lambda s: (s["received_at"], s["scan_id"]), reverse=True)
        return found[:limit]

    def file_path(self, scan_id):
        """The file as the phone sent it."""
        scan = self.scan(scan_id)
        return os.path.join(self.folder, scan_id, f"scan.{scan['format']}")

    def model_path(self, scan_id):
        """The converted file. A ScanError with the status as its message while it is not ready."""
        scan = self.scan(scan_id)
        model = scan["model"]
        if model["status"] != "ready":
            raise ScanError(f"the model is {model['status']}" + (f": {model['reason']}" if model["reason"] else ""))
        return os.path.join(self.folder, scan_id, "model.glb")

    def state_part(self):
        """What GET /api/state gains: {"scans": {machine_id: that machine's newest scan}}."""
        newest = {}
        with self._lock:
            for scan in self._scans.values():
                held = newest.get(scan["machine_id"])
                if held is None or (scan["received_at"], scan["scan_id"]) > (held["received_at"], held["scan_id"]):
                    newest[scan["machine_id"]] = scan
            return {"scans": {machine: dict(scan) for machine, scan in newest.items()}}

    def machines(self):
        """Every machine that has a scan: it appears in the state's machines even with no probe."""
        with self._lock:
            return sorted({s["machine_id"] for s in self._scans.values()})

    def close(self):
        self._converter.stop()

    # ---- on disk -----------------------------------------------------------------------------------------

    def _append(self, scan):
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(scan, separators=(",", ":")) + "\n")

    def _load(self):
        """Scans survive a restart. One that was being converted when the receiver stopped waits again,
        and every waiting scan is given to the converter."""
        if os.path.exists(self.log_path):
            with open(self.log_path, encoding="utf-8") as f:
                for line in f:
                    try:
                        scan = json.loads(line)
                        scan_id = scan["scan_id"]
                        scan["machine_id"], scan["format"], scan["model"]["status"], scan["received_at"]
                    except (ValueError, KeyError, TypeError):
                        continue  # a line cut off by a power cut
                    if not isinstance(scan_id, str) or not SCAN_ID.match(scan_id):
                        continue
                    self._scans.pop(scan_id, None)  # the newest line wins, and keeps the newest's place
                    self._scans[scan_id] = scan
        waiting = []
        for scan_id, scan in list(self._scans.items()):
            if not os.path.isfile(os.path.join(self.folder, scan_id, f"scan.{scan['format']}")):
                del self._scans[scan_id]  # its file is gone: nothing to show or convert
                continue
            if scan.get("client_id"):
                self._by_client.setdefault(scan["client_id"], scan_id)
            if scan["model"]["status"] in ("converting", "waiting"):
                if scan["model"]["status"] == "converting":
                    scan = dict(scan, model={"status": "waiting", "reason": ""})
                    self._scans[scan_id] = scan
                    self._append(scan)
                waiting.append(scan_id)
        for scan_id in waiting:
            self._start_model(scan_id)


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass
