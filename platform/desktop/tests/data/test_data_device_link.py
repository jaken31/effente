"""A board picked by address while the app runs: the address, the host's link, the state's actions."""

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from app.data import device_address as addresses
from PySide6.QtCore import QSettings


@pytest.mark.parametrize(("typed", "url"), [
    ("192.168.43.20", "http://192.168.43.20:8765"),
    ("  192.168.43.20:9000 ", "http://192.168.43.20:9000"),
    ("http://10.110.213.214:8765/view", "http://10.110.213.214:8765"),
    ("board.local", "http://board.local:8765"),
    ("[fe80::1]", "http://[fe80::1]:8765"),
])
def test_an_address_becomes_the_url_of_the_boards_port(typed, url):
    assert addresses.device_url(typed) == (url, "")


@pytest.mark.parametrize("typed", ["", "   ", None, "192.168.43.20:port", "two words", "https://192.168.43.20",
                                   "ftp://board", "http://", "user@board", "board:0", "board:99999"])
def test_what_is_not_an_address_is_refused_with_a_reason(typed):
    url, why = addresses.device_url(typed)
    assert url == ""
    assert why


def closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def board():
    """A stand-in for the board's port: /latest answers with a status in display mode."""
    status = {"device_id": "effente-wifi", "machine_id": "fan-1", "at": "2026-11-14T10:00:00Z", "mode": "display"}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"status": status} if self.path == "/latest" else {}).encode()
            self.send_response(200 if self.path == "/latest" else 404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            """/command as the board answers it: "detect" is refused (no model), the others done."""
            sent = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            name = sent.get("command")
            if self.path != "/command" or name == "detect":
                code, reply = 400, {"ok": False, "error": "no model yet: record, then stop"}
            else:
                code, reply = 200, {"ok": True, "command": name, "mode": "recording", "state": "RECORDING"}
            body = json.dumps(reply).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_the_host_reads_a_board_picked_while_it_runs(make_host, board, qtbot):
    host = make_host()
    assert host.device_link() == addresses.NO_LINK
    host.connect_device(f"http://{board}")
    qtbot.waitUntil(lambda: host.device_link()["reachable"] is True, timeout=5000)
    assert host.device_link()["url"] == f"http://{board}"
    first = host.link
    host.connect_device(f"http://127.0.0.1:{closed_port()}")  # another address takes its place
    assert host.link is not first
    qtbot.waitUntil(lambda: host.device_link()["reachable"] is False, timeout=8000)
    host.disconnect_device()
    assert host.link is None
    assert host.device_link() == addresses.NO_LINK


def test_connecting_shows_the_board_and_remembers_the_address(make_host, make_state, board, qtbot, tmp_path):
    store = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    state = make_state(make_host(), settings=store)
    assert state.device_link()["url"] == ""
    assert state.device_address() == ""
    with qtbot.waitSignal(state.link_changed, timeout=2000):
        assert state.connect_device(f" {board} ") is True
    assert state.device_link()["url"] == f"http://{board}"
    assert state.device_address() == board
    assert store.value(addresses.ADDRESS_KEY) == board
    qtbot.waitUntil(lambda: state.device_link()["reachable"] is True, timeout=5000)
    qtbot.waitUntil(lambda: "effente-wifi" in (state.devices() or {}), timeout=5000)  # its check-in arrived
    with qtbot.waitSignal(state.link_changed, timeout=2000):
        state.disconnect_device()
    assert state.device_link()["url"] == ""
    assert state.device_address() == board  # still there to connect to again


def test_a_bad_address_or_no_host_is_refused_with_the_reason(make_host, make_state, qtbot):
    from app.data.state import NO_HOST, DashboardState

    host = make_host()
    state = make_state(host)
    with qtbot.waitSignal(state.action_failed, timeout=1000) as sig:
        assert state.connect_device("not an address") is False
    assert sig.args == [addresses.NOT_AN_ADDRESS]
    assert host.link is None
    elsewhere = DashboardState(host.url)  # a receiver this window did not start
    with qtbot.waitSignal(elsewhere.action_failed, timeout=1000) as sig:
        assert elsewhere.connect_device("192.168.43.20") is False
    assert sig.args == [NO_HOST]


def test_a_command_reaches_the_board_and_its_answer_comes_back(make_host, make_state, board, qtbot):
    state = make_state(make_host())
    assert state.command_url() == ""
    with qtbot.waitSignal(state.command_done, timeout=1000) as said:  # no board: nothing is sent
        assert state.send_command("record") is False
    assert said.args == [addresses.NO_BOARD]
    state.connect_device(board)
    assert state.command_url() == f"http://{board}"
    with qtbot.waitSignal(state.command_done, timeout=5000) as said:
        assert state.send_command("record") is True
    assert said.args == ['The board took "record": it is now RECORDING.']
    with qtbot.waitSignal(state.command_done, timeout=5000) as said:  # the board's own refusal
        state.send_command("detect")
    assert said.args == ["The board refused: no model yet: record, then stop."]
    state.disconnect_device()


def test_a_board_that_does_not_answer_a_command_is_said_so():
    url = f"http://127.0.0.1:{closed_port()}"
    ok, said = addresses.post_command(url, "idle", timeout=2)
    assert ok is False and said.startswith(f"The board at {url} did not answer:")


def test_commands_go_to_the_board_on_usb_when_none_is_picked_by_address(make_host):
    host = make_host()
    assert host.command_url() == ""

    class Usb:
        links, ports = {}, {"B": 5002, "A": 5001}

    host.usb_link = Usb()
    assert host.command_url() == "http://127.0.0.1:5001"
    host.device_url = "http://192.168.43.20:8765"
    assert host.command_url() == "http://192.168.43.20:8765"
    host.device_url, host.usb_link = None, None
