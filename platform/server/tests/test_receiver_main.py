"""The real entry point, started as a subprocess from another directory."""

import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request

MAIN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start(tmp_path, *extra):
    port = free_port()
    proc = subprocess.Popen(
        [sys.executable, MAIN, "--port", str(port), "--host", "127.0.0.1", "--data-dir", str(tmp_path / "d"), *extra],
        cwd=tmp_path,  # not the repo: must work from any directory
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    first = proc.stdout.readline()
    return proc, port, first


def get(port, path, timeout=5):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=timeout) as r:
        return r.status, r.read()


def test_main_starts_prints_url_and_stops_on_ctrl_c(tmp_path):
    proc, port, first = start(tmp_path, "--web-dir", str(tmp_path / "noweb"))
    try:
        assert first.strip() == f"effente receiver: open http://127.0.0.1:{port}/"
        status, body = get(port, "/api/state")
        assert status == 200 and json.loads(body)["ok"] is True
        # An open stream must not delay shutdown.
        stream = socket.create_connection(("127.0.0.1", port))
        stream.sendall(b"GET /api/stream HTTP/1.1\r\nHost: x\r\n\r\n")
        time.sleep(0.3)
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/records",
            data=json.dumps(
                {"device_id": "d1", "machine_id": "m1", "model_version": "v", "at": "2026-11-14T16:02:11Z", "diagnosis": "normal",
                 "problems": [], "wrong": False}
            ).encode(),
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5).read()
        started = time.monotonic()
        proc.send_signal(signal.SIGINT)
        out, err = proc.communicate(timeout=5)
        assert time.monotonic() - started < 3
        assert proc.returncode == 0, err
        assert "device d1 connected" in out
        assert "receiver stopped" in out
        assert "Traceback" not in err
        stream.close()
    finally:
        if proc.poll() is None:
            proc.kill()
    assert (tmp_path / "d" / "records.jsonl").exists()


def test_main_sigterm_and_default_web_dir(tmp_path):
    proc, port, _ = start(tmp_path)
    try:
        status, _ = get(port, "/")  # real platform/web or the notice page; both are 200
        assert status == 200
        proc.send_signal(signal.SIGTERM)
        proc.communicate(timeout=5)
        assert proc.returncode == 0
    finally:
        if proc.poll() is None:
            proc.kill()


def test_main_port_in_use(tmp_path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        port = s.getsockname()[1]
        proc = subprocess.run(
            [sys.executable, MAIN, "--port", str(port), "--host", "127.0.0.1", "--data-dir", str(tmp_path / "d")],
            capture_output=True,
            text=True,
            timeout=10,
        )
    assert proc.returncode == 1
    assert "cannot listen" in proc.stderr
