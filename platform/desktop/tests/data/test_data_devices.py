"""Finding devices on the laptop's network (app/data/devices.py), against a stand-in for a device's port."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from app.data import devices
from app.data import scans as scans_module


def serve(body: bytes):
    """A server on this laptop that answers every GET with `body`. Returns (server, port)."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


@pytest.fixture
def device_port():
    # The shape device/intake/stream.py `latest_json` serves: the status is under "status".
    status = {"device_id": "uno-q-1", "machine_id": "conveyor-07", "mode": "watching"}
    server, port = serve(json.dumps({"results": None, "features": None, "events": None, "status": status}).encode())
    yield port
    server.shutdown()
    server.server_close()


def test_a_device_is_what_answers_latest_with_its_ids(device_port):
    assert devices.probe("127.0.0.1", device_port) == {
        "url": f"http://127.0.0.1:{device_port}", "host": "127.0.0.1", "mode": "watching",
        "machine_id": "conveyor-07", "device_id": "uno-q-1"}


@pytest.mark.parametrize("body", [b"not json", b"[1, 2]", b'{"mode": "watching"}', b'{"machine_id": ""}',
                                  b'{"status": {"mode": "watching"}}', b'{"status": {"device_id": ""}}',
                                  b'{"device_id": "uno-q-1", "machine_id": "conveyor-07"}'])  # not under "status"
def test_another_server_on_the_port_is_not_a_device(body):
    server, port = serve(body)
    try:
        assert devices.probe("127.0.0.1", port) is None
    finally:
        server.shutdown()
        server.server_close()


def test_nothing_listening_is_not_a_device(device_port):
    server, port = serve(b"{}")
    server.shutdown()
    server.server_close()
    assert devices.probe("127.0.0.1", port) is None
    assert [d["machine_id"] for d in devices.find(["127.0.0.1"], device_port)] == ["conveyor-07"]
    assert devices.find([], device_port) == []


def test_the_addresses_asked_are_the_laptops_local_networks(monkeypatch):
    monkeypatch.setattr(scans_module, "_route_address", lambda: "192.168.43.10")
    monkeypatch.setattr(scans_module, "_interface_addresses", lambda: ["192.168.43.10", "10.0.0.7", "169.254.3.4"])
    assert [str(n) for n in devices.networks()] == ["192.168.43.0/24", "10.0.0.0/24"]  # never a link-local one
    hosts = devices.candidates()
    assert hosts[0] == devices.THIS_LAPTOP and len(hosts) == 1 + 253 + 253
    assert "192.168.43.1" in hosts and "10.0.0.254" in hosts
    assert "192.168.43.10" not in hosts and "10.0.0.7" not in hosts  # not the laptop's own addresses
    monkeypatch.setattr(scans_module, "_route_address", lambda: None)
    monkeypatch.setattr(scans_module, "_interface_addresses", list)
    assert devices.networks() == [] and devices.candidates() == [devices.THIS_LAPTOP]


def test_the_addresses_near_the_one_last_connected_to_are_asked_too(monkeypatch):
    """On a large network the device is often in another /24 than the laptop (seen 16 Nov 2026:
    laptop 10.110.173.89, board 10.110.213.214). The Device page's Find boards asks there too."""
    monkeypatch.setattr(scans_module, "_route_address", lambda: "10.110.173.89")
    monkeypatch.setattr(scans_module, "_interface_addresses", lambda: ["10.110.173.89"])
    hosts = devices.candidates("10.110.213.214")
    assert len(hosts) == 1 + 253 + 254 and len(set(hosts)) == len(hosts)
    assert "10.110.213.214" in hosts and "10.110.173.89" not in hosts
    assert devices.candidates("10.110.173.20") == devices.candidates()  # the same network: asked once
    assert devices.candidates("not an address !") == devices.candidates()


def test_find_asks_a_usb_boards_wifi_address_and_near_the_last(monkeypatch, device_port):
    asked = []
    monkeypatch.setattr(devices, "usb_addresses", lambda: ["10.9.8.7"])
    monkeypatch.setattr(devices, "candidates", lambda last="": ["127.0.0.1", "10.9.8.7", f"near {last}"])
    monkeypatch.setattr(devices, "probe", lambda host, port: asked.append(host))
    assert devices.find(port=device_port, last="10.1.2.3") == []
    assert asked == ["10.9.8.7", "127.0.0.1", "near 10.1.2.3"]


def test_the_address_in_a_devices_url():
    assert devices.host_of("http://10.110.213.214:8765") == "10.110.213.214"
    assert devices.host_of(None) == "" and devices.host_of("") == ""


def test_the_finder_answers_with_the_newest_search_only(qtbot):
    answers = iter([["old"], ["new"]])
    gate = threading.Event()

    def find():
        mine = next(answers)
        if mine == ["old"]:
            gate.wait(5)
        return mine

    finder = devices.Finder(find=find)
    got = []
    finder.found.connect(got.append)
    finder.start()
    with qtbot.waitSignal(finder.found, timeout=5000):
        finder.start()
    gate.set()
    qtbot.wait(100)
    assert got == [["new"]] and not finder.searching
    with qtbot.waitSignal(finder.found, timeout=5000):
        finder.start()  # a search that fails found nothing
    assert got[-1] == []
