"""Looking for boards with no address typed: which addresses are tried, what counts as a board,
a board on USB, and the state's search."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from app.data import find_boards as finder

STATUS = {"device_id": "effente-01", "machine_id": "fan-1", "at": "2026-11-14T10:00:00Z", "mode": "display"}
IP_OUT = """1: lo    inet 127.0.0.1/8 scope host lo\\       valid_lft forever preferred_lft forever
5: docker0    inet 172.17.0.1/16 brd 172.17.255.255 scope global docker0\\       valid_lft forever
6: wlan0    inet 10.110.213.214/16 brd 10.110.255.255 scope global dynamic noprefixroute wlan0\\       valid_lft 7062sec
"""


def test_only_the_addresses_near_the_laptop_and_the_last_board_are_tried():
    tried = finder.nearby(["192.168.43.10"], "10.110.213.214:8765")
    assert len(tried) == 253 + 254
    assert tried[0] == "192.168.43.1" and "192.168.43.10" not in tried  # never the laptop itself
    assert "10.110.213.214" in tried and "10.110.214.1" not in tried
    assert finder.nearby(["192.168.43.10"], "192.168.43.20") == finder.nearby(["192.168.43.10"])  # once each
    assert finder.nearby([], "board.local") == []  # a name gives no numbers to search near
    assert finder.nearby(["8.8.8.8", "169.254.3.4", "192.0.0.2", "127.0.0.1", "not one"]) == []  # not a local network


def test_a_boards_wifi_address_is_read_from_its_own_listing():
    assert finder.wifi_addresses(IP_OUT) == ["10.110.213.214"]
    assert finder.wifi_addresses("") == []

    def run(command):
        return "List of devices attached\n1601234490\tdevice\nABC\toffline\n" if command[1] == "devices" else IP_OUT

    assert finder.usb_boards(run, "adb") == [("1601234490", "10.110.213.214")]
    assert finder.usb_boards(lambda command: None, "adb") == []  # adb did not answer


@pytest.fixture
def port_of():
    """Start a stand-in for something on a port; `port_of(latest)` answers /latest with it."""
    servers = []

    def start(latest):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = json.dumps(latest).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return server.server_address[1]

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


def test_only_what_names_itself_a_device_counts_as_a_board(port_of):
    assert finder.probe("127.0.0.1", port_of({"status": STATUS})) == STATUS
    assert finder.probe("127.0.0.1", port_of({"status": {"mode": "display"}})) is None  # no device_id
    assert finder.probe("127.0.0.1", port_of(["something else"])) is None
    assert finder.probe("127.0.0.1", port_of({})) is None  # no status at all


def test_a_search_lists_the_usb_board_and_what_answers_nearby():
    answers = {"192.168.43.20": STATUS, "192.168.43.77": dict(STATUS, device_id="effente-02", mode="watching")}
    found = []
    tried = finder.find("", found.append, own=["192.168.43.10"],
                        usb=[("S1", "192.168.43.20"), ("S2", "10.110.213.214")],
                        look=lambda address, port: answers.get(address))
    assert tried == 252  # the 254 nearby, less the laptop and the board already asked over USB
    by = {b["address"]: b for b in found}
    assert len(found) == 3
    assert by["192.168.43.20"] == {"address": "192.168.43.20", "how": "usb", "serial": "S1", "answers": True,
                                   "device_id": "effente-01", "machine_id": "fan-1", "mode": "display"}
    assert by["10.110.213.214"]["answers"] is False and by["10.110.213.214"]["device_id"] == ""  # another network
    assert by["192.168.43.77"]["how"] == "network" and by["192.168.43.77"]["device_id"] == "effente-02"


def test_a_stopped_search_tries_nothing_more():
    stop = threading.Event()
    stop.set()
    asked = []
    finder.find("", own=["192.168.43.10"], usb=[], stop=stop, look=lambda a, p: asked.append(a))
    assert asked == []


def test_the_state_fills_as_boards_turn_up_and_drops_a_stopped_search(make_host, make_state, qtbot):
    state = make_state(make_host())
    assert state.found_boards() == {"searching": False, "boards": [], "tried": None}
    go = threading.Event()
    lasts = []

    def find(last, found, stop):
        lasts.append(last)
        found(finder.board("192.168.43.20", finder.NETWORK, STATUS))
        go.wait(5)
        found(finder.board("192.168.43.21", finder.NETWORK, STATUS))
        return 253

    state._find = find
    state.connect_device("192.168.43.99")
    state.disconnect_device()
    state.find_boards()
    assert state.found_boards()["searching"] is True
    qtbot.waitUntil(lambda: len(state.found_boards()["boards"]) == 1, timeout=3000)
    assert lasts == ["192.168.43.99"]  # the address connected to last is searched near
    go.set()
    qtbot.waitUntil(lambda: state.found_boards()["searching"] is False, timeout=3000)
    assert state.found_boards()["tried"] == 253
    assert [b["address"] for b in state.found_boards()["boards"]] == ["192.168.43.20", "192.168.43.21"]

    go.clear()
    state.find_boards()
    qtbot.waitUntil(lambda: len(state.found_boards()["boards"]) == 1, timeout=3000)
    state.stop_finding()
    go.set()
    qtbot.wait(300)
    found = state.found_boards()
    assert found["searching"] is False and found["tried"] is None
    assert len(found["boards"]) == 1  # what came after the stop is not added
