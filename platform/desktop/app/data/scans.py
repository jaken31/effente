"""`state.scans`: the scans of the current machine (platform/desktop/INTERFACES.md
addition 18, platform/CONTRACT.md sections 8.2 to 8.4).

The state makes one of these and passes it every snapshot and every stream
event. `state` is the DashboardState or, in tests, FakeState: it gives
`current_machine()` and `action_failed(str)`, and the live one also `api` (the
ApiClient), `host` (the ReceiverHost, when this app runs the receiver) and
`_background(work, done)`.
tests/fake_scans.py `FakeScans` is this interface on fixed data.

The snapshot holds each machine's newest scan and the `scan` event every change,
so `current()` is right the moment the machine shown changes. Older scans are
not in the snapshot: the first `all()` for a machine fetches its list, and
`scans_changed` says when it is there.

The converted file is fetched over the API into a cache folder, so the app also
works when the receiver is on another machine. Nothing removes a file from the
cache yet.
"""

from __future__ import annotations

import http.client
import ipaddress
import math
import os
import re
import socket
import urllib.parse
import uuid
from pathlib import Path

from PySide6.QtCore import QObject, QStandardPaths, Signal
from PySide6.QtNetwork import QAbstractSocket, QNetworkAddressEntry, QNetworkInterface

from . import logic
from .api import ApiError, quote

SCAN_LIMIT = 100  # the most scans of one machine that are listed
MAX_SCAN_BYTES = 268_435_456  # the largest file the receiver takes (contract section 8.3)
APP_NAME = "effente desktop"  # `app` of a scan imported here
MODEL_FILE = "model.glb"
# What a file is, from its first four bytes, as the receiver reads it (contract section 8.3).
CONTENT_TYPES = {b"PK\x03\x04": "model/vnd.usdz+zip", b"glTF": "model/gltf-binary"}
SAFE_ID = re.compile(r"[A-Za-z0-9-]+")  # an id made by the platform. Anything else is not used as a folder name.

# The address ranges kept for private networks. A hotspot and an office network hand out
# addresses from these, and a phone on the same network can reach them.
LOCAL_NETWORKS = tuple(ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
CONTAINER_NETWORK = LOCAL_NETWORKS[1]  # where container bridges on this laptop usually sit
# An address kept for documentation, which no machine has. Only the routing table is asked about it.
ELSEWHERE = ("192.0.2.1", 9)


# ---- this laptop's address ------------------------------------------------------


def _local(address) -> bool:
    """True for an IPv4 address on a private network. Never 127.0.0.1, a link-local or a VPN address."""
    try:
        ip = ipaddress.IPv4Address(address)
    except ValueError:
        return False
    return any(ip in network for network in LOCAL_NETWORKS)


def _route_address():
    """The address this laptop sends from to reach another network, or None when it
    has no such route. Connecting a UDP socket only asks the routing table: no packet
    is sent and nothing outside the laptop is asked."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(ELSEWHERE)
            return s.getsockname()[0]
        except OSError:
            return None


def _interface_addresses() -> list:
    """The IPv4 addresses of this laptop's network interfaces that are up, as the system lists them."""
    flag = QNetworkInterface.InterfaceFlag
    out = []
    for interface in QNetworkInterface.allInterfaces():
        flags = interface.flags()
        if not (flags & flag.IsUp and flags & flag.IsRunning) or flags & (flag.IsLoopBack | flag.IsPointToPoint):
            continue
        out += [entry.ip().toString() for entry in interface.addressEntries()
                if entry.ip().protocol() == QAbstractSocket.NetworkLayerProtocol.IPv4Protocol]
    return out


def _interface_addresses6() -> list:
    """The IPv6 addresses of this laptop that another machine on its network can reach, the lasting
    ones before the temporary ones. Never a link-local one: a phone cannot use it without knowing
    which of its own interfaces to send from."""
    flag = QNetworkInterface.InterfaceFlag
    eligible = QNetworkAddressEntry.DnsEligibilityStatus.DnsEligible
    lasting, other = [], []
    for interface in QNetworkInterface.allInterfaces():
        flags = interface.flags()
        if not (flags & flag.IsUp and flags & flag.IsRunning) or flags & (flag.IsLoopBack | flag.IsPointToPoint):
            continue
        for entry in interface.addressEntries():
            ip = entry.ip()
            if ip.protocol() != QAbstractSocket.NetworkLayerProtocol.IPv6Protocol:
                continue
            if not (ip.isGlobal() or ip.isUniqueLocalUnicast()):
                continue
            (lasting if entry.dnsEligibility() == eligible else other).append(ip.toString())
    return lasting + other


def lan_address6() -> str:
    """This laptop's IPv6 address on the local network, or "" when it has none. For a network
    that gives it no IPv4 address a phone can reach: some phone hotspots are IPv6 only.
    Unverified: no phone has sent a scan to this address yet."""
    found = _interface_addresses6()
    return found[0] if found else ""


def lan_address() -> str:
    """This laptop's address on the local network, or "" when it has none.

    First the address it reaches other networks from: on a phone's hotspot or a
    shared WiFi that is the one the phone sees. With no such route (the laptop is
    the hotspot, say), the first private address of an interface that is up. A
    container bridge counts last. That order is a guess: with several networks
    up, the address may be one the phone cannot reach."""
    route = _route_address()
    if _local(route):
        return route
    found = [a for a in _interface_addresses() if _local(a)]
    found.sort(key=lambda a: ipaddress.IPv4Address(a) in CONTAINER_NETWORK)  # stable: otherwise the system's order
    return found[0] if found else ""


def _any_address() -> str:
    """The nearest thing to an address for this laptop when `lan_address()` has none or the receiver
    is not known to listen there: the private IPv4 one, else any other IPv4 one of an interface that
    is up, else the IPv6 one in brackets. "" when the laptop is on no network at all. A guess: the
    phone may not reach it."""
    address = lan_address()
    if address:
        return address
    found = _interface_addresses()
    if found:
        return found[0]
    address6 = lan_address6()
    return f"[{address6}]" if address6 else ""


def _loopback(host) -> bool:
    """True for `localhost` and 127.x: an address only this laptop can use."""
    if not isinstance(host, str):
        return False
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _this_laptop(host) -> bool:
    """True when `host` only means "this laptop" to the laptop itself: loopback, or 0.0.0.0."""
    if _loopback(host):
        return True
    try:
        return ipaddress.ip_address(host).is_unspecified
    except ValueError:
        return False


# ---- scans ----------------------------------------------------------------------


def _is_scan(scan) -> bool:
    return isinstance(scan, dict) and isinstance(scan.get("scan_id"), str) and isinstance(scan.get("machine_id"), str)


def _model(scan) -> dict:
    model = scan.get("model")
    return model if isinstance(model, dict) else {}


def _when(scan) -> float:
    """A scan's `received_at` in milliseconds. One that cannot be read sorts last."""
    ms = logic.parse_time(scan.get("received_at"))
    return 0.0 if math.isnan(ms) else ms


def default_cache_dir() -> Path:
    """Where converted files are kept between runs: the system's cache folder for this user."""
    return Path(QStandardPaths.writableLocation(QStandardPaths.GenericCacheLocation)) / "effente" / "scans"


class Scans(QObject):
    scans_changed = Signal()  # a scan arrived or its status moved
    model_ready = Signal(str)  # the converted file for this scan_id is on disk and can be shown

    def __init__(self, state):
        super().__init__(state)
        self.state = state
        self.cache_dir = None  # a folder, or None for default_cache_dir(). Tests set their own.
        self._machine_scans: dict = {}  # machine_id -> {scan_id: scan}
        self._listed: set = set()  # machines whose list has been fetched since the last snapshot
        self._listing: dict = {}  # machine_id being listed -> the scans that came meanwhile
        self._wanted: set = set()  # scan_ids model_path was asked for and has not given yet
        self._fetching: set = set()
        self._failed: set = set()  # scan_ids whose fetch failed: tried again when the scan or the snapshot comes

    # ---- fed by the state -------------------------------------------------

    def apply_snapshot(self, snap: dict) -> None:
        """GET /api/state, after every (re)connect."""
        scans = snap.get("scans")
        self._machine_scans = {}
        for scan in (scans.values() if isinstance(scans, dict) else ()):
            self._keep(scan)
        self._listed.clear()  # scans may have arrived while the stream was down
        self._failed.clear()
        self.scans_changed.emit()
        self._fetch_wanted()

    def apply_event(self, name: str, data: dict) -> None:
        """One stream event, of any name."""
        if name == "scan":
            self._take(data)

    def _keep(self, scan) -> bool:
        """Store a scan. True if it is new or differs from the one held."""
        if not _is_scan(scan):
            return False
        machine, scan_id = scan["machine_id"], scan["scan_id"]
        if machine in self._listing:
            self._listing[machine][scan_id] = scan  # newer than the list being read
        known = self._machine_scans.setdefault(machine, {})
        if known.get(scan_id) == scan:
            return False
        known[scan_id] = scan
        return True

    def _take(self, scan) -> None:
        """Keep a scan from an event or a reply. Either may come first, so the same one twice changes nothing."""
        if self._keep(scan):
            self._failed.discard(scan["scan_id"])
            self.scans_changed.emit()
            self._fetch_wanted()

    def _list(self, machine) -> None:
        """Fetch a machine's scans once, in the background."""
        api = getattr(self.state, "api", None)
        if api is None or not machine or machine in self._listed or machine in self._listing:
            return
        self._listing[machine] = {}

        def done(reply, error):
            late = self._listing.pop(machine, {})
            self._listed.add(machine)  # also after a failure: the next snapshot tries again, not every read
            if error:
                self.state.action_failed.emit(f"Scans not loaded: {error}")
                return
            listed = reply.get("scans")
            known = self._machine_scans.setdefault(machine, {})
            known.update({s["scan_id"]: s for s in (listed if isinstance(listed, list) else ()) if _is_scan(s)})
            known.update(late)  # an event that came while the list was read is newer than the list
            self.scans_changed.emit()
            self._fetch_wanted()

        self.state._background(lambda: api.get(f"/api/machines/{quote(machine)}/scans", limit=SCAN_LIMIT), done)

    def _newest_first(self, machine) -> list:
        scans = self._machine_scans.get(machine, {}).values()
        return sorted(scans, key=lambda s: (_when(s), s["scan_id"]), reverse=True)

    def _find(self, scan_id):
        for scans in self._machine_scans.values():
            if scan_id in scans:
                return scans[scan_id]
        return None

    # ---- reads ------------------------------------------------------------

    def current(self):
        """The current machine's newest scan, or None."""
        scans = self._newest_first(self.state.current_machine())
        return scans[0] if scans else None

    def all(self) -> list:
        """The current machine's scans, newest first."""
        machine = self.state.current_machine()
        self._list(machine)
        return self._newest_first(machine)

    def model_path(self, scan_id: str):
        """Path of the converted file in the app's cache, or None while it is being
        fetched. Asking starts the fetch, and `model_ready` says when it is there.
        For a scan that is not converted yet, the fetch starts once it is."""
        scan = self._find(scan_id) if isinstance(scan_id, str) else None
        if scan is None or not SAFE_ID.fullmatch(scan_id):
            return None
        if self._cached(scan):
            return str(self._cache_file(scan_id))
        self._wanted.add(scan_id)
        self._fetch(scan)
        return None

    def qr_text(self, machine_id: str, name: str = "", anyway: bool = False) -> str:
        """The line the QR code holds (contract section 8.2): the receiver's address as
        the phone reaches it. For a receiver on this laptop that is the laptop's
        address on the local network: IPv4, or IPv6 in brackets when it has no
        IPv4 one and this app's receiver listens on IPv6. "" when the phone could
        not reach it: the laptop has no such address, or the receiver this app
        runs listens on the laptop only.

        `anyway` gives a line even then, for the person who asks for the code all
        the same: it carries the nearest address there is (`_any_address`), else
        the receiver's address as this app uses it. The phone may not reach it."""
        api = getattr(self.state, "api", None)
        if api is None or not machine_id:
            return ""
        base = api.base
        if _this_laptop(api.host):
            own = getattr(self.state, "host", None)
            address = "" if own is not None and _loopback(getattr(own, "host", None)) else lan_address()
            if not address and getattr(getattr(own, "receiver", None), "httpd6", None) is not None:
                # No IPv4 address a phone can reach, and this app's receiver hears IPv6: the IPv6 one.
                address6 = lan_address6()
                address = f"[{address6}]" if address6 else ""
            if not address and anyway:
                address = _any_address()
            if address:
                base = f"http://{address}:{api.port}"
            elif not anyway:
                return ""
        q = urllib.parse.quote
        text = f"effente://scan?v=1&base={q(base, safe='')}&machine={q(machine_id, safe='')}"
        return text + (f"&name={q(name, safe='')}" if name else "")

    # ---- the converted file -----------------------------------------------

    def _cache_file(self, scan_id: str) -> Path:
        return Path(self.cache_dir or default_cache_dir()) / scan_id / MODEL_FILE

    def _cached(self, scan) -> bool:
        """True when the cache holds this scan's converted file, whole."""
        model = _model(scan)
        if model.get("status") != "ready":
            return False
        try:
            size = self._cache_file(scan["scan_id"]).stat().st_size
        except OSError:
            return False
        return not isinstance(model.get("bytes"), int) or size == model["bytes"]

    def _fetch_wanted(self) -> None:
        for scan_id in list(self._wanted):
            scan = self._find(scan_id)
            if scan is not None:
                self._fetch(scan)

    def _fetch(self, scan) -> None:
        """Fetch a ready scan's converted file into the cache, in the background, once at a time."""
        scan_id, model = scan["scan_id"], _model(scan)
        api = getattr(self.state, "api", None)
        if api is None or model.get("status") != "ready" or scan_id in self._fetching or scan_id in self._failed:
            return
        if self._cached(scan):  # it was asked for before it was ready, and another fetch brought it since
            self._arrived(scan_id)
            return
        self._fetching.add(scan_id)
        path, expected = self._cache_file(scan_id), model.get("bytes")
        part = path.with_name(f"{MODEL_FILE}.{uuid.uuid4().hex}.part")  # moved into place only when whole

        def work():
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                size = api.get_bytes(f"/api/scans/{quote(scan_id)}/{MODEL_FILE}", to=part)
                if isinstance(expected, int) and size != expected:
                    raise ApiError(f"{size} of {expected} bytes arrived")
                os.replace(part, path)
            except (OSError, http.client.HTTPException) as e:  # the cache could not be written, or the reply broke off
                raise ApiError(str(e) or type(e).__name__) from None
            finally:
                try:
                    part.unlink(missing_ok=True)
                except OSError:
                    pass

        def done(_, error):
            self._fetching.discard(scan_id)
            if error:
                self._failed.add(scan_id)
                self.state.action_failed.emit(f"Model not loaded: {error}")
            else:
                self._arrived(scan_id)

        self.state._background(work, done)

    def _arrived(self, scan_id: str) -> None:
        self._wanted.discard(scan_id)
        self.model_ready.emit(scan_id)

    # ---- actions ----------------------------------------------------------

    def import_file(self, path) -> None:
        """Send a USDZ or GLB file from this laptop's disk as a scan for the current machine."""
        machine = self.state.current_machine()
        if not machine:
            self.state.action_failed.emit("Scan not imported: no machine is shown.")
            return
        api, path = self.state.api, Path(path)
        client_id = str(uuid.uuid4())  # one per import, so the receiver stores a file sent twice only once

        def work():
            try:
                with open(path, "rb") as f:
                    start, size = f.read(4), os.fstat(f.fileno()).st_size
                if start not in CONTENT_TYPES:
                    raise ApiError(f"{path.name} is not a USDZ or GLB file.")
                if size > MAX_SCAN_BYTES:
                    raise ApiError(f"{path.name} is larger than {MAX_SCAN_BYTES // 2**20} MB.")
                return api.post(f"/api/machines/{quote(machine)}/scans", file=path, content_type=CONTENT_TYPES[start],
                                client_id=client_id, name=path.name, app=APP_NAME)
            except (OSError, http.client.HTTPException) as e:  # the file could not be read, or the reply broke off
                raise ApiError(str(e) or type(e).__name__) from None

        def done(reply, error):
            if error:
                self.state.action_failed.emit(f"Scan not imported: {error}")
            else:
                self._take(reply.get("scan"))

        self.state._background(work, done)
