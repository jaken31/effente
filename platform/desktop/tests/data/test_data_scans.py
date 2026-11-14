"""`state.scans` (INTERFACES.md addition 18) on a DashboardState against a stand-in receiver.

The receiver's own scan routes (contract sections 8.3 and 8.4) are built
elsewhere and may not be merged. `StandIn` below answers them the way the
contract says, with just enough of the rest for a DashboardState to connect. It
is written from the contract, not from the receiver's code, so these tests do
not show that the two agree.
"""

import json
import queue
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from app.data import scans as scans_module
from app.data.api import ApiClient
from app.data.scans import Scans
from app.data.state import DashboardState
from fake_scans import box_glb
from PySide6.QtCore import QObject, Signal

MACHINE = "conveyor-07"
OTHER = "press-01"
USDZ = b"PK\x03\x04" + b"a made-up file, not a scan of anything"
WAIT_MS = 5000


class StandIn:
    """A receiver that knows only scans: /api/state, /api/stream and the scan routes."""

    def __init__(self):
        self.scans: dict = {}  # scan_id -> scan, in the order they came
        self.models: dict = {}  # scan_id -> the converted file's bytes
        self.uploads: list = []  # (machine_id, query, Content-Type, body) of every upload
        self.model_gets = 0  # how often a converted file was asked for
        self.cut_models_short = False  # send half a converted file, then close
        self._streams: list = []
        self._lock = threading.Lock()
        stand_in = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_GET(self):
                stand_in.get(self)

            def do_POST(self):
                stand_in.post(self)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.drop_streams()
        self.server.shutdown()
        self.server.server_close()

    # ---- what a test does to it --------------------------------------------

    def add(self, machine=MACHINE, glb=None, status="ready", announce=True) -> dict:
        """Store a scan as if a phone had sent it. With `glb` it is ready."""
        n = len(self.scans) + 1
        scan = {"scan_id": f"scan-202611141815{n:02d}-test", "machine_id": machine, "client_id": f"test-{n:04d}",
                "at": f"2026-11-14T18:14:{n:02d}Z", "received_at": f"2026-11-14T18:15:{n:02d}Z",
                "format": "glb" if glb else "usdz", "file": {"name": "scan", "bytes": len(glb or USDZ)},
                "app": "stand-in", "model": {"status": status, "reason": ""}}
        self.scans[scan["scan_id"]] = scan
        if glb:
            self.convert(scan["scan_id"], glb, announce=False)
        if announce:
            self.send("scan", self.scans[scan["scan_id"]])
        return self.scans[scan["scan_id"]]

    def convert(self, scan_id, glb, announce=True) -> dict:
        """The conversion finished: the scan is ready and its converted file is `glb`."""
        self.models[scan_id] = glb
        return self.move(scan_id, "ready", announce=announce, bytes=len(glb))

    def move(self, scan_id, status, reason="", announce=True, **model) -> dict:
        scan = dict(self.scans[scan_id], model={"status": status, "reason": reason, **model})
        self.scans[scan_id] = scan
        if announce:
            self.send("scan", scan)
        return scan

    def send(self, name, data) -> None:
        with self._lock:
            for q in self._streams:
                q.put((name, data))

    def drop_streams(self) -> None:
        """Close every open stream, as a receiver that restarts does."""
        with self._lock:
            for q in self._streams:
                q.put(None)

    def of(self, machine) -> list:
        """A machine's scans, newest first."""
        return sorted((s for s in self.scans.values() if s["machine_id"] == machine),
                      key=lambda s: s["received_at"], reverse=True)

    # ---- the routes --------------------------------------------------------

    def get(self, request) -> None:
        url = urllib.parse.urlsplit(request.path)
        parts = [urllib.parse.unquote(p) for p in url.path.split("/")[2:]]
        if parts == ["state"]:
            machines = sorted({s["machine_id"] for s in self.scans.values()})
            self.json(request, 200, {"ok": True, "now": "2026-11-14T18:20:00Z", "machines": machines, "devices": {},
                                     "models": {}, "alarms": {}, "scans": {m: self.of(m)[0] for m in machines}})
        elif parts == ["stream"]:
            self.stream(request)
        elif len(parts) == 3 and parts[0] == "machines" and parts[2] == "scans":
            self.json(request, 200, {"ok": True, "scans": self.of(parts[1])})
        elif len(parts) == 3 and parts[0] == "machines" and parts[2] in ("records", "agent-runs"):
            self.json(request, 200, {"ok": True, "records": [], "runs": []})  # what the state reads for a machine
        elif len(parts) == 3 and parts[0] == "scans" and parts[2] == "model.glb" and parts[1] in self.scans:
            self.model(request, parts[1])
        else:
            self.json(request, 400, {"ok": False, "error": "unknown route"})

    def model(self, request, scan_id) -> None:
        self.model_gets += 1
        status = self.scans[scan_id]["model"]["status"]
        if status != "ready":
            self.json(request, 400, {"ok": False, "error": status})
            return
        body = self.models[scan_id]
        request.send_response(200)
        request.send_header("Content-Type", "model/gltf-binary")
        request.send_header("Content-Length", str(len(body)))
        request.end_headers()
        request.wfile.write(body[:len(body) // 2] if self.cut_models_short else body)
        request.close_connection = self.cut_models_short

    def post(self, request) -> None:
        url = urllib.parse.urlsplit(request.path)
        parts = [urllib.parse.unquote(p) for p in url.path.split("/")[2:]]
        query = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
        body = request.rfile.read(int(request.headers["Content-Length"]))
        if not (len(parts) == 3 and parts[0] == "machines" and parts[2] == "scans"):
            self.json(request, 400, {"ok": False, "error": "unknown route"})
            return
        self.uploads.append((parts[1], query, request.headers["Content-Type"], body))
        if body[:4] not in (b"PK\x03\x04", b"glTF"):
            self.json(request, 400, {"ok": False, "error": "not a USDZ or GLB file"})
            return
        first = next((s for s in self.scans.values() if s["client_id"] == query["client_id"]), None)
        if first:
            self.json(request, 200, {"ok": True, "duplicate": True, "scan": first})
            return
        is_glb = body[:4] == b"glTF"
        scan = self.add(parts[1], glb=body if is_glb else None, status="ready" if is_glb else "waiting", announce=False)
        scan = dict(scan, client_id=query["client_id"], file={"name": query.get("name", ""), "bytes": len(body)},
                    app=query.get("app", ""))
        self.scans[scan["scan_id"]] = scan
        self.send("scan", scan)
        self.json(request, 200, {"ok": True, "duplicate": False, "scan": scan})

    def json(self, request, status, obj) -> None:
        body = json.dumps(obj).encode()
        request.send_response(status)
        request.send_header("Content-Type", "application/json")
        request.send_header("Content-Length", str(len(body)))
        request.end_headers()
        request.wfile.write(body)

    def stream(self, request) -> None:
        q = queue.Queue()
        with self._lock:
            self._streams.append(q)
        try:
            request.send_response(200)
            request.send_header("Content-Type", "text/event-stream")
            request.send_header("Connection", "close")
            request.end_headers()
            request.wfile.write(b": open\n\n")
            request.wfile.flush()
            while True:
                try:
                    item = q.get(timeout=1)
                except queue.Empty:
                    request.wfile.write(b": keep-alive\n\n")
                    request.wfile.flush()
                    continue
                if item is None:
                    return
                request.wfile.write(f"event: {item[0]}\ndata: {json.dumps(item[1])}\n\n".encode())
                request.wfile.flush()
        except OSError:
            pass  # the app went away
        finally:
            request.close_connection = True
            with self._lock:
                self._streams.remove(q)


@pytest.fixture
def receiver():
    stand_in = StandIn()
    yield stand_in
    stand_in.stop()


@pytest.fixture
def make_scans(qtbot, receiver, tmp_path):
    """`state.scans` of a started DashboardState on the stand-in, with its own cache folder."""
    states = []

    def make():
        state = DashboardState(receiver.url)
        states.append(state)
        state.scans.cache_dir = tmp_path / f"cache-{len(states)}"
        state.failures = []
        state.action_failed.connect(state.failures.append)
        state.start()
        qtbot.waitUntil(lambda: state.view()["platform_up"] and state.view()["snapshot_loaded"], timeout=WAIT_MS)
        return state.scans

    yield make
    for state in states:
        state.stop()


def model_of(qtbot, scans, scan_id) -> str:
    """Ask for a converted file and wait until it is there."""
    with qtbot.waitSignal(scans.model_ready, timeout=WAIT_MS) as ready:
        assert scans.model_path(scan_id) is None
    assert ready.args == [scan_id]
    return scans.model_path(scan_id)


# ---- the scans kept -----------------------------------------------------------------


def test_scans_come_with_the_snapshot_the_list_and_the_stream(qtbot, receiver, make_scans):
    old, new = receiver.add(glb=box_glb()), receiver.add(status="converting")
    scans = make_scans()
    assert type(scans) is Scans and scans.state.current_machine() == MACHINE
    assert scans.current() == new  # the snapshot holds only the newest

    with qtbot.waitSignal(scans.scans_changed, timeout=WAIT_MS):  # the first all() reads the rest
        assert scans.all() == [new]
    assert scans.all() == [new, old]

    with qtbot.waitSignal(scans.scans_changed, timeout=WAIT_MS):  # its status moves
        failed = receiver.move(new["scan_id"], "failed", "Blender is not installed on this laptop")
    assert scans.current() == failed and scans.all() == [failed, old]

    with qtbot.waitSignal(scans.scans_changed, timeout=WAIT_MS):  # another arrives
        newest = receiver.add(status="waiting")
    assert scans.current() == newest and scans.all() == [newest, failed, old]


def test_the_same_scan_twice_changes_nothing(qtbot, receiver, make_scans):
    scan = receiver.add(glb=box_glb())
    scans = make_scans()
    with qtbot.assertNotEmitted(scans.scans_changed, wait=300):
        receiver.send("scan", scan)
        receiver.send("settings", {"device_id": "effente-01"})  # an event that is not about scans
    scans.apply_event("scan", {"scan_id": 7})  # not a scan: ignored
    assert scans.all() == [scan]


def test_scans_follow_the_machine_shown(qtbot, receiver, make_scans):
    ours, theirs = receiver.add(glb=box_glb()), receiver.add(machine=OTHER, status="waiting")
    scans = make_scans()
    assert scans.current() == ours
    scans.state.set_machine(OTHER)
    assert scans.current() == theirs  # there at once: the snapshot holds every machine's newest
    scans.state.set_machine("mill-03")
    assert scans.current() is None and scans.all() == []


def test_a_reconnect_reads_the_scans_again(qtbot, receiver, make_scans):
    first = receiver.add(glb=box_glb())
    scans = make_scans()
    with qtbot.waitSignal(scans.scans_changed, timeout=WAIT_MS):
        scans.all()
    receiver.drop_streams()
    missed = receiver.add(status="waiting", announce=False)  # stored while the stream was down
    qtbot.waitUntil(lambda: scans.current() == missed, timeout=WAIT_MS)
    qtbot.waitUntil(lambda: scans.all() == [missed, first], timeout=WAIT_MS)


def test_a_scan_that_arrives_while_the_list_is_read_is_kept(qtbot, receiver, make_scans):
    scan = receiver.add(status="converting")
    scans = make_scans()
    before = dict(receiver.scans)
    scans.all()  # the list is on its way, and holds the scan as converting
    ready = receiver.convert(scan["scan_id"], box_glb(), announce=False)
    receiver.scans = before  # the list read answers with what it held when asked
    scans.apply_event("scan", ready)
    qtbot.waitUntil(lambda: MACHINE in scans._listed, timeout=WAIT_MS)
    assert scans.all() == [ready]


def test_a_list_that_fails_says_so_once(qtbot, receiver, make_scans):
    receiver.add(status="waiting")
    scans = make_scans()
    receiver.of = lambda machine: 1 / 0  # every list read now breaks
    with qtbot.waitSignal(scans.state.action_failed, timeout=WAIT_MS) as said:
        scans.all()
    assert said.args[0].startswith("Scans not loaded: ")
    with qtbot.assertNotEmitted(scans.state.action_failed, wait=300):
        assert len(scans.all()) == 1  # what the snapshot gave is still shown, and it is not asked again


# ---- the converted file -------------------------------------------------------------


def test_the_converted_file_is_fetched_into_the_cache(qtbot, receiver, make_scans):
    scan = receiver.add(glb=box_glb())
    scans = make_scans()
    path = model_of(qtbot, scans, scan["scan_id"])
    assert path == str(scans.cache_dir / scan["scan_id"] / "model.glb")
    assert Path(path).read_bytes() == box_glb()
    assert [p.name for p in (scans.cache_dir / scan["scan_id"]).iterdir()] == ["model.glb"]  # no half file left

    with qtbot.assertNotEmitted(scans.model_ready, wait=200):  # now it is there, asking fetches nothing
        assert scans.model_path(scan["scan_id"]) == path
    assert receiver.model_gets == 1


def test_asking_twice_fetches_once(qtbot, receiver, make_scans):
    scan = receiver.add(glb=box_glb())
    scans = make_scans()
    with qtbot.waitSignal(scans.model_ready, timeout=WAIT_MS):
        assert scans.model_path(scan["scan_id"]) is None and scans.model_path(scan["scan_id"]) is None
    assert receiver.model_gets == 1


def test_a_cached_file_is_used_by_the_next_run(qtbot, receiver, make_scans):
    scan = receiver.add(glb=box_glb())
    first = make_scans()
    path = model_of(qtbot, first, scan["scan_id"])
    second = make_scans()
    second.cache_dir = first.cache_dir
    assert second.model_path(scan["scan_id"]) == path and receiver.model_gets == 1


def test_a_scan_asked_for_before_it_is_converted_arrives_when_it_is(qtbot, receiver, make_scans):
    scan = receiver.add(status="converting")
    scans = make_scans()
    with qtbot.assertNotEmitted(scans.state.action_failed, wait=200):
        assert scans.model_path(scan["scan_id"]) is None
    assert receiver.model_gets == 0  # nothing to fetch yet
    with qtbot.waitSignal(scans.model_ready, timeout=WAIT_MS) as ready:
        receiver.convert(scan["scan_id"], box_glb())
    assert ready.args == [scan["scan_id"]]
    assert Path(scans.model_path(scan["scan_id"])).read_bytes() == box_glb()


def test_a_scan_converted_again_is_fetched_again(qtbot, receiver, make_scans):
    scan = receiver.add(glb=box_glb())
    scans = make_scans()
    model_of(qtbot, scans, scan["scan_id"])
    bigger = box_glb(hi=(1.0, 1.0, 1.0)) + b"\x00" * 4
    with qtbot.waitSignal(scans.scans_changed, timeout=WAIT_MS):
        receiver.convert(scan["scan_id"], bigger)
    assert Path(model_of(qtbot, scans, scan["scan_id"])).read_bytes() == bigger


def test_a_file_cut_short_is_not_kept(qtbot, receiver, make_scans):
    scan = receiver.add(glb=box_glb())
    scans = make_scans()
    receiver.cut_models_short = True
    with qtbot.waitSignal(scans.state.action_failed, timeout=WAIT_MS) as said:
        assert scans.model_path(scan["scan_id"]) is None
    assert said.args[0].startswith("Model not loaded: ")
    assert not list((scans.cache_dir / scan["scan_id"]).iterdir())
    with qtbot.assertNotEmitted(scans.state.action_failed, wait=300):  # a page that asks on every draw is told once
        assert scans.model_path(scan["scan_id"]) is None
    assert receiver.model_gets == 1

    receiver.cut_models_short = False  # the next snapshot tries again
    with qtbot.waitSignal(scans.model_ready, timeout=WAIT_MS):
        receiver.drop_streams()
    assert Path(scans.model_path(scan["scan_id"])).read_bytes() == box_glb()


def test_a_cache_that_cannot_be_written_says_so(qtbot, receiver, make_scans, tmp_path):
    scan = receiver.add(glb=box_glb())
    scans = make_scans()
    (tmp_path / "a-file").write_text("in the way")
    scans.cache_dir = tmp_path / "a-file"
    with qtbot.waitSignal(scans.state.action_failed, timeout=WAIT_MS) as said:
        assert scans.model_path(scan["scan_id"]) is None
    assert said.args[0].startswith("Model not loaded: ")


def test_only_a_known_scan_with_a_plain_id_is_fetched(qtbot, receiver, make_scans):
    receiver.add(glb=box_glb())
    odd = receiver.add(glb=box_glb())
    odd = receiver.scans[odd["scan_id"]] = dict(odd, scan_id="../../outside")
    scans = make_scans()
    with qtbot.waitSignal(scans.scans_changed, timeout=WAIT_MS):
        scans.all()
    assert odd in scans.all()
    with qtbot.assertNotEmitted(scans.state.action_failed, wait=200):
        assert scans.model_path("../../outside") is None  # never used as a folder name
        assert scans.model_path("scan-nobody-has") is None
    assert receiver.model_gets == 0 and not scans.cache_dir.exists()


# ---- import -------------------------------------------------------------------------


def test_a_glb_file_is_imported_and_shown(qtbot, receiver, make_scans, tmp_path):
    receiver.add(status="failed")  # so a machine is shown
    scans = make_scans()
    source = tmp_path / "Förderband 7.glb"
    source.write_bytes(box_glb())
    with qtbot.waitSignal(scans.scans_changed, timeout=WAIT_MS):
        scans.import_file(source)
    qtbot.waitUntil(lambda: len(receiver.uploads) == 1, timeout=WAIT_MS)
    machine, query, content_type, body = receiver.uploads[0]
    assert (machine, content_type, body) == (MACHINE, "model/gltf-binary", box_glb())
    assert (query["name"], query["app"]) == ("Förderband 7.glb", "effente desktop") and "at" not in query
    assert 8 <= len(query["client_id"]) <= 64 and query["client_id"].replace("-", "").isalnum()

    scan = scans.current()
    assert (scan["format"], scan["model"]["status"], scan["file"]["name"]) == ("glb", "ready", "Förderband 7.glb")
    assert Path(model_of(qtbot, scans, scan["scan_id"])).read_bytes() == box_glb()
    assert scans.state.failures == []


def test_a_usdz_file_is_imported_and_waits_for_its_conversion(qtbot, receiver, make_scans, tmp_path):
    receiver.add(status="failed")
    scans = make_scans()
    source = tmp_path / "scan.usdz"
    source.write_bytes(USDZ)
    with qtbot.waitSignal(scans.scans_changed, timeout=WAIT_MS):
        scans.import_file(str(source))  # a path as text, as a file picker gives it
    assert receiver.uploads[0][2:] == ("model/vnd.usdz+zip", USDZ)
    scan = scans.current()
    assert (scan["format"], scan["model"]["status"]) == ("usdz", "waiting")
    assert scans.model_path(scan["scan_id"]) is None


def test_each_import_has_its_own_client_id(qtbot, receiver, make_scans, tmp_path):
    receiver.add(status="failed")
    scans = make_scans()
    source = tmp_path / "scan.usdz"
    source.write_bytes(USDZ)
    for _ in range(2):
        with qtbot.waitSignal(scans.scans_changed, timeout=WAIT_MS):
            scans.import_file(source)
    assert receiver.uploads[0][1]["client_id"] != receiver.uploads[1][1]["client_id"]
    qtbot.waitUntil(lambda: len(scans.all()) == 3, timeout=WAIT_MS)


@pytest.mark.parametrize("content,why", [(b"just some text", "notes.txt is not a USDZ or GLB file."),
                                         (b"", "notes.txt is not a USDZ or GLB file."),
                                         (None, "No such file")])
def test_a_file_that_is_not_a_scan_is_not_sent(qtbot, receiver, make_scans, tmp_path, content, why):
    receiver.add(status="failed")
    scans = make_scans()
    source = tmp_path / "notes.txt"
    if content is not None:
        source.write_bytes(content)
    with qtbot.waitSignal(scans.state.action_failed, timeout=WAIT_MS) as said:
        scans.import_file(source)
    assert said.args[0].startswith("Scan not imported: ") and why in said.args[0]
    assert receiver.uploads == []


def test_a_file_over_the_limit_is_not_sent(qtbot, receiver, make_scans, tmp_path, monkeypatch):
    receiver.add(status="failed")
    scans = make_scans()
    monkeypatch.setattr(scans_module, "MAX_SCAN_BYTES", 2**20)
    source = tmp_path / "big.glb"
    source.write_bytes(b"glTF" + b"\x00" * 2**20)
    with qtbot.waitSignal(scans.state.action_failed, timeout=WAIT_MS) as said:
        scans.import_file(source)
    assert said.args == ["Scan not imported: big.glb is larger than 1 MB."] and receiver.uploads == []


def test_the_receivers_refusal_is_passed_on(qtbot, receiver, make_scans, tmp_path, monkeypatch):
    receiver.add(status="failed")
    scans = make_scans()
    monkeypatch.setitem(scans_module.CONTENT_TYPES, b"OBJ\n", "model/obj")  # a type the app would send and the receiver refuses
    source = tmp_path / "scan.obj"
    source.write_bytes(b"OBJ\n...")
    with qtbot.waitSignal(scans.state.action_failed, timeout=WAIT_MS) as said:
        scans.import_file(source)
    assert said.args == ["Scan not imported: not a USDZ or GLB file"]


def test_an_import_with_no_machine_shown_says_so(qtbot, receiver, make_scans, tmp_path):
    scans = make_scans()
    with qtbot.waitSignal(scans.state.action_failed) as said:
        scans.import_file(tmp_path / "scan.usdz")
    assert said.args == ["Scan not imported: no machine is shown."] and receiver.uploads == []


# ---- the pairing line ---------------------------------------------------------------


def addresses(monkeypatch, route, interfaces=()):
    """Give this laptop the addresses a test wants: the one it routes from, and its interfaces'."""
    monkeypatch.setattr(scans_module, "_route_address", lambda: route)
    monkeypatch.setattr(scans_module, "_interface_addresses", lambda: list(interfaces))


def read_line(text) -> dict:
    """The pairing line as the phone reads it."""
    url = urllib.parse.urlsplit(text)
    assert (url.scheme, url.netloc, url.path) == ("effente", "scan", "")
    return {k: v[0] for k, v in urllib.parse.parse_qs(url.query, strict_parsing=True).items()}


class Bare(QObject):
    """A state that only knows its receiver."""

    action_failed = Signal(str)

    def __init__(self, base):
        super().__init__()
        self.api = ApiClient(base)


def test_the_pairing_line_is_the_contracts(monkeypatch, qapp):
    addresses(monkeypatch, "192.168.43.10")
    scans = Scans(Bare("http://127.0.0.1:8000"))
    assert scans.qr_text("conveyor-07", "Conveyor 7") == (
        "effente://scan?v=1&base=http%3A%2F%2F192.168.43.10%3A8000&machine=conveyor-07&name=Conveyor%207")
    assert scans.qr_text("conveyor-07") == "effente://scan?v=1&base=http%3A%2F%2F192.168.43.10%3A8000&machine=conveyor-07"
    line = read_line(scans.qr_text("conveyor-07", "Förderband & Motor = 7?"))
    assert line == {"v": "1", "base": "http://192.168.43.10:8000", "machine": "conveyor-07",
                    "name": "Förderband & Motor = 7?"}


@pytest.mark.parametrize("receiver_url", ["http://127.0.0.1:8123", "http://localhost:8123/", "http://[::1]:8123",
                                          "http://0.0.0.0:8123"])
def test_the_base_is_this_laptops_address_and_the_receivers_port(monkeypatch, qapp, receiver_url):
    addresses(monkeypatch, "10.0.0.7")
    assert read_line(Scans(Bare(receiver_url)).qr_text("conveyor-07"))["base"] == "http://10.0.0.7:8123"


def test_a_receiver_on_another_machine_is_the_base(monkeypatch, qapp):
    addresses(monkeypatch, "10.0.0.7")
    assert read_line(Scans(Bare("http://192.168.1.20:8000/")).qr_text("conveyor-07"))["base"] == "http://192.168.1.20:8000"


def test_a_receiver_this_app_runs_for_this_laptop_only_gives_no_line(monkeypatch, qapp):
    addresses(monkeypatch, "192.168.43.10")
    state = Bare("http://127.0.0.1:8000")
    state.host = Bare("http://127.0.0.1:8000")  # stands for the ReceiverHost: only its `host` is read
    state.host.host = "127.0.0.1"
    assert Scans(state).qr_text("conveyor-07") == ""  # no phone can reach it
    state.host.host = "0.0.0.0"
    assert read_line(Scans(state).qr_text("conveyor-07"))["base"] == "http://192.168.43.10:8000"


@pytest.mark.parametrize("route,interfaces,address", [
    ("192.168.43.10", ["10.0.0.7"], "192.168.43.10"),  # the address it reaches other networks from
    (None, ["10.42.0.1"], "10.42.0.1"),  # no route out: the laptop is the hotspot
    ("100.109.244.125", ["100.109.244.125", "172.20.10.4"], "172.20.10.4"),  # a VPN's address is not on the local network
    (None, ["172.18.0.1", "192.168.1.5"], "192.168.1.5"),  # a container bridge counts last
    (None, ["172.18.0.1"], "172.18.0.1"),  # but it may be a hotspot in that range
    ("8.8.4.4", ["127.0.0.1", "169.254.7.7", "100.64.0.9", "::1", "fe80::1", "not an address"], ""),
    ("127.0.0.1", [], ""),
    (None, [], ""),
])
def test_which_address_is_this_laptops(monkeypatch, route, interfaces, address):
    addresses(monkeypatch, route, interfaces)
    assert scans_module.lan_address() == address


def test_with_no_address_the_line_is_empty(monkeypatch, qapp):
    addresses(monkeypatch, "127.0.0.1", ["127.0.0.1", "169.254.7.7"])
    scans = Scans(Bare("http://localhost:8000"))
    assert scans.qr_text("conveyor-07", "Conveyor 7") == ""
    addresses(monkeypatch, "192.168.43.10")
    assert scans.qr_text("") == ""  # and with no machine to scan


def test_this_laptops_real_addresses_are_read_without_a_network(qapp):
    """The two real lookups, on whatever this machine has. Neither sends anything."""
    route = scans_module._route_address()
    assert route is None or isinstance(route, str)
    assert all(isinstance(a, str) and "." in a for a in scans_module._interface_addresses())
    found = scans_module.lan_address()
    assert found == "" or scans_module._local(found)
    line = Scans(Bare("http://127.0.0.1:8000")).qr_text("conveyor-07")
    assert "127.0.0.1" not in line and "localhost" not in line
