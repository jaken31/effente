"""The model server module on a fake llama-server: a script that takes the real
one's flags and answers GET /v1/models. No Qt, no real model, nothing outside 127.0.0.1."""

import json
import os
import socket
import stat
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from app.data import model_server as ms

FAKE = '''#!{python}
"""Fake llama-server. FAKE_LLAMA says how it behaves: ready, loading or broken."""
import argparse, json, os, sys
from http.server import BaseHTTPRequestHandler, HTTPServer

p = argparse.ArgumentParser()
p.add_argument("-m", required=True)
p.add_argument("--host", required=True)
p.add_argument("--port", type=int, required=True)
p.add_argument("--offline", action="store_true")
a = p.parse_args()
with open(os.environ["FAKE_LLAMA_ARGS"], "w") as f:
    json.dump(sys.argv[1:], f)
mode = os.environ.get("FAKE_LLAMA", "ready")
if mode == "broken":
    print("0.00.100.000 I srv    load_model: loading")
    print("0.00.625.026 E srv  llama_server: exiting due to model loading error", flush=True)
    sys.exit(1)


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        ready = mode == "ready" and self.path == "/v1/models"
        body = json.dumps({{"data": [{{"id": os.path.basename(a.m)}}]}} if ready else {{"error": {{"message": "Loading model"}}}})
        self.send_response(200 if ready else 503)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *args):
        pass


HTTPServer((a.host, a.port), H).serve_forever()
'''


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for(check, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = check()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("timed out")


@pytest.fixture
def fake(tmp_path, monkeypatch):
    """(path of the fake binary, file its arguments are written to)."""
    binary = tmp_path / "llama-server"
    binary.write_text(FAKE.format(python=sys.executable))
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    args = tmp_path / "args.json"
    monkeypatch.setenv("FAKE_LLAMA_ARGS", str(args))
    monkeypatch.setenv("FAKE_LLAMA", "ready")
    return binary, args


@pytest.fixture
def model(tmp_path):
    path = tmp_path / "models" / "small.gguf"
    path.parent.mkdir()
    path.write_bytes(b"not a real model")
    return path


@pytest.fixture
def server(fake):
    s = ms.ModelServer(binary=str(fake[0]), port=free_port())
    yield s
    s.stop()


def test_model_file_is_the_size_in_the_folder(tmp_path):
    assert ms.model_file(tmp_path, "large") == tmp_path / "large.gguf"
    assert ms.model_file(tmp_path, "huge") == tmp_path / "small.gguf"
    assert ms.model_file("", "small") == ms.model_file(ms.DEFAULT_FOLDER, "small")
    assert str(ms.model_file("~/effente-models", "small")).startswith(os.path.expanduser("~"))


def test_starts_the_binary_with_the_real_flags_and_stops_it(server, fake, model):
    assert server.status() == ms.Status(ms.STOPPED, "Not running.")
    started = server.start(model)
    assert started.state == ms.LOADING and started.ours
    ready = wait_for(lambda: (s := server.status()).state == ms.READY and s)
    assert ready == ms.Status(ms.READY, "Running with small.gguf.", ours=True)
    assert server.owns_server()
    assert json.loads(fake[1].read_text()) == [
        "-m", str(model), "--host", "127.0.0.1", "--port", str(server.port), "--offline"]
    assert ms.probe(server.url)[0] == ms.READY

    child = server._child
    server.stop()
    assert child.poll() is not None
    assert not server.owns_server()
    assert ms.probe(server.url) == (ms.STOPPED, f"No model server is running at {server.url}.")
    assert server.status() == ms.Status(ms.STOPPED, "Not running.")
    server.stop()  # twice is fine


def test_start_twice_keeps_the_one_child(server, model):
    server.start(model)
    child = server._child
    wait_for(lambda: server.status().state == ms.READY)
    assert server.start(model).state == ms.READY
    assert server._child is child


@pytest.fixture
def already_running():
    """Some other model server on a port, as if started outside the app."""

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b'{"data": [{"id": "other.gguf"}]}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


def test_no_second_server_when_the_port_already_answers(fake, model, already_running):
    server = ms.ModelServer(binary=str(fake[0]), port=already_running)
    status = server.start(model)
    assert status.state == ms.READY and not status.ours
    assert "started outside this app" in status.message
    assert not fake[1].exists()  # the binary was never run
    assert not server.owns_server()
    server.stop()
    assert ms.probe(server.url)[0] == ms.READY  # and stop leaves the other one alone


def test_not_installed_says_so(model):
    server = ms.ModelServer(binary="llama-server-that-is-not-here", port=free_port())
    status = server.start(model)
    assert status.state == ms.NOT_INSTALLED
    assert status.message == ("llama-server-that-is-not-here is not installed on this laptop. "
                              "Install llama.cpp, then start again.")
    assert server.status() == status  # the reason stays on show


def test_missing_model_file_says_which(server, fake, tmp_path):
    missing = tmp_path / "models" / "large.gguf"
    status = server.start(missing)
    assert status == ms.Status(ms.MODEL_MISSING, f"The model file {missing} is missing.")
    assert server.status() == status
    assert not fake[1].exists()


def test_failed_to_start_gives_the_servers_last_line(server, model, monkeypatch):
    monkeypatch.setenv("FAKE_LLAMA", "broken")
    server.start(model)
    failed = wait_for(lambda: (s := server.status()).state == ms.FAILED and s)
    assert failed.message == (f"{server.binary} failed to start (exit code 1): "
                              "srv  llama_server: exiting due to model loading error")
    assert not failed.ours and not server.owns_server()
    monkeypatch.setenv("FAKE_LLAMA", "ready")  # starting again clears the failure
    server.start(model)
    wait_for(lambda: server.status().state == ms.READY)


def test_still_loading_is_not_ready(server, model, monkeypatch):
    monkeypatch.setenv("FAKE_LLAMA", "loading")
    server.start(model)
    state, why = wait_for(lambda: (r := ms.probe(server.url))[0] == ms.LOADING and r)
    assert why == f"The model server at {server.url} is still loading its model. Try again in a moment."
    assert server.status() == ms.Status(
        ms.LOADING, "Still loading small.gguf. A large model takes a while.", ours=True)


def test_a_server_that_dies_later_is_said_to_have_stopped(server, model):
    server.start(model)
    wait_for(lambda: server.status().state == ms.READY)
    server._child.kill()
    server._child.wait()
    assert server.status().message.startswith(f"{server.binary} stopped (exit code -9)")


def test_probe_tells_a_program_that_is_not_a_model_server():
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{httpd.server_address[1]}"
        assert ms.probe(url) == (ms.STOPPED, f"The program at {url} is not a model server (it answered HTTP 404).")
    finally:
        httpd.shutdown()
        httpd.server_close()
