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
    server, port = serve(json.dumps({"device_id": "uno-q-1", "machine_id": "conveyor-07", "mode": "watching"}).encode())
    yield port
    server.shutdown()
    server.server_close()


def test_a_device_is_what_answers_latest_with_its_ids(device_port):
    assert devices.probe("127.0.0.1", device_port) == {
        "url": f"http://127.0.0.1:{device_port}", "host": "127.0.0.1", "mode": "watching",
        "machine_id": "conveyor-07", "device_id": "uno-q-1"}


@pytest.mark.parametrize("body", [b"not json", b"[1, 2]", b'{"mode": "watching"}', b'{"machine_id": ""}'])
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
