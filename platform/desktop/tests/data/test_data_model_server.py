"""The model server module on a fake llama-server: a script that takes the real
one's flags and answers GET /v1/models. No Qt, no real model, nothing outside 127.0.0.1."""

import json
import os

import pytest
from app.data import model_server as ms
from fake_llama import answering, free_port, wait_for, write_fake


@pytest.fixture
def fake(tmp_path, monkeypatch):
    """(path of the fake binary, file its arguments are written to)."""
    return write_fake(tmp_path, monkeypatch)


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
    assert ready == ms.Status(ms.READY, "Running with small.gguf.", True, "small.gguf")
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
    with answering() as port:
        yield port


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
                              "srv llama_server: exiting due to model loading error")
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
        ms.LOADING, "Still loading small.gguf. A large model takes a while.", True, "small.gguf")


def test_a_server_that_dies_later_is_said_to_have_stopped(server, model):
    server.start(model)
    wait_for(lambda: server.status().state == ms.READY)
    server._child.kill()
    server._child.wait()
    assert server.status().message.startswith(f"{server.binary} stopped (exit code -9)")


def test_probe_tells_a_program_that_is_not_a_model_server():
    with answering(status=404, body=b"") as port:
        url = f"http://127.0.0.1:{port}"
        assert ms.probe(url) == (ms.STOPPED, f"The program at {url} is not a model server (it answered HTTP 404).")
    with answering(body=b"<html>hello</html>") as port:
        url = f"http://127.0.0.1:{port}"
        assert ms.probe(url) == (ms.STOPPED, f"The program at {url} is not a model server (its answer was not JSON).")
    with answering(body=b'{"data": []}') as port:
        url = f"http://127.0.0.1:{port}"
        assert ms.probe(url) == (ms.STOPPED, f"The model server at {url} has no model loaded.")
