"""End to end: run main.py as a process against a tiny fake receiver."""

import json
import os
import subprocess
import sys
import time

import pytest

from sim_fake_platform import FakePlatform, free_port, record_problems

MAIN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")
SAMPLE = os.path.join(os.path.dirname(MAIN), "sample_replay.jsonl")


def run_sim(*args, timeout=30, url=None):
    cmd = [sys.executable, MAIN, "--seed", "1", *args]
    env = dict(os.environ)
    if url:
        env["EFFENTE_PLATFORM_URL"] = url
    return subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout, env=env)


def test_announces_then_sends_contract_records(tmp_path):
    with FakePlatform() as fp:
        log = tmp_path / "dev.jsonl"
        model_file = tmp_path / "model.json"
        p = run_sim("--case", "unbalance", "--interval", "0.1", "--count", "4",
                    "--log", str(log), "--model-file", str(model_file), url=fp.url)
        assert p.returncode == 0, p.stderr
        assert fp.log[0] == ("model",), fp.log
        assert len(fp.models) == 1
        announced = fp.models[0]
        assert announced["simulated"] is True and announced["device_id"] == "effente-sim-01"
        assert announced["model_version"] == "sim-3" and len(announced["spectrum_edges_hz"]) == 21
        recs = fp.records
        assert len(recs) == 4 and fp.check_ins == []
        assert all(record_problems(r) == [] for r in recs)
        assert all(r["diagnosis"] == "unbalance_like" and r["wrong"] is True and r["problems"] == [] for r in recs)
        logged = [json.loads(line) for line in log.read_text().splitlines()]
        assert logged == recs
        mf = json.loads(model_file.read_text())
        assert mf["simulated"] is True and mf["features"][0]["sentence"] == "vibration once per turn"


def test_records_carry_heat_by_source(tmp_path):
    with FakePlatform() as fp:
        p = run_sim("--interval", "0.1", "--count", "2", url=fp.url)
        assert p.returncode == 0, p.stderr
        cam = [r["heat"] for r in fp.records]
    assert all(h["source"] == "camera" and len(h["frames"]) == 2 for h in cam)
    assert all(record_problems(r) == [] for r in fp.records)
    assert record_problems({"heat": {"source": "camera", "imu_temp_c": 40, "cells": [0] * 47}})  # the check can fail

    with FakePlatform() as fp:
        assert run_sim("--interval", "0.1", "--count", "2", "--heat", "imu", url=fp.url).returncode == 0
        assert [set(r["heat"]) for r in fp.records] == [{"source", "imu_temp_c"}] * 2
    with FakePlatform() as fp:
        assert run_sim("--interval", "0.1", "--count", "2", "--heat", "none", url=fp.url).returncode == 0
        assert all("heat" not in r for r in fp.records)


def test_problem_while_the_model_says_normal():
    with FakePlatform() as fp:
        p = run_sim("--problem", "too_hot", "--hold", "0", "--interval", "0.1", "--count", "3", url=fp.url)
        assert p.returncode == 0, p.stderr
        assert len(fp.records) == 3
        for r in fp.records:
            assert record_problems(r) == []
            assert r["diagnosis"] == "normal" and r["wrong"] is True
            assert [x["name"] for x in r["problems"]] == ["too_hot"] and r["problems"][0]["kind"] == "limit"
        assert len({r["problems"][0]["since"] for r in fp.records}) == 1  # one problem, one start


def test_problem_waits_for_the_hold():
    with FakePlatform() as fp:
        p = run_sim("--problem", "too_hot", "--hold", "0.5", "--interval", "0.1", "--count", "12", url=fp.url)
        assert p.returncode == 0, p.stderr
        on = [bool(r["problems"]) for r in fp.records]
        assert on[0] is False and on[-1] is True
        assert on == sorted(on)  # off until it has held, then on


def test_checks_in_while_not_watching_with_limits_only():
    with FakePlatform() as fp:
        p = run_sim("--mode", "recording", "--problem", "too_hot", "--problem", "vibration_above_normal",
                    "--hold", "0", "--interval", "0.1", "--count", "3", url=fp.url)
        assert p.returncode == 0, p.stderr
        assert fp.records == []
        assert fp.log[0] == ("model",) and fp.log[1:] == [("check_in", "recording")] * 3
        for device_id, body in fp.check_ins:
            assert device_id == "effente-sim-01"
            assert body["mode"] == "recording" and body["machine_id"] == "demo-fan-1"
            assert body["wrong"] is True and [x["name"] for x in body["problems"]] == ["too_hot"]
        assert "check-in recording + too_hot" in p.stdout
        assert "vibration_above_normal (watching only)" in p.stdout


def test_interval_over_two_seconds_refused():
    p = run_sim("--interval", "2.5", "--count", "1", url="http://127.0.0.1:9")
    assert p.returncode == 2 and "at most 2" in p.stderr


def test_need_model_triggers_new_announcement():
    with FakePlatform() as fp:
        fp.force_need_model_once = True
        p = run_sim("--interval", "0.1", "--count", "3", url=fp.url)
        assert p.returncode == 0
        assert len(fp.models) == 2
        assert fp.log[:3] == [("model",), ("records", 1), ("model",)]


def test_survives_platform_absent(tmp_path):
    log = tmp_path / "dev.jsonl"
    url = f"http://127.0.0.1:{free_port()}"  # nothing listening
    t0 = time.time()
    p = run_sim("--interval", "0.1", "--count", "5", "--log", str(log), url=url)
    assert p.returncode == 0, p.stderr
    assert "Traceback" not in p.stderr
    assert "platform unreachable" in p.stdout
    assert len(log.read_text().splitlines()) == 5
    assert time.time() - t0 < 15


def test_carries_on_when_platform_comes_back():
    port = free_port()
    proc = subprocess.Popen([sys.executable, MAIN, "--url", f"http://127.0.0.1:{port}", "--case", "unbalance",
                             "--interval", "0.1", "--count", "40"],
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        time.sleep(1.0)
        with FakePlatform(port) as fp:
            out, err = proc.communicate(timeout=30)
        assert proc.returncode == 0, err
        assert "platform unreachable" in out and "platform ok" in out
        assert len(fp.models) >= 1  # announced once it was back
        assert fp.log[0] == ("model",)
        # Windows made while it was down were queued and sent as one array.
        assert any(entry[0] == "records" and entry[1] > 1 for entry in fp.log)
        assert len(fp.records) == 40
    finally:
        if proc.poll() is None:
            proc.kill()


def test_replay_sends_sample_file():
    with FakePlatform() as fp:
        p = run_sim("--replay", SAMPLE, "--interval", "0.05", url=fp.url)
        assert p.returncode == 0, p.stderr
        assert len(fp.records) == 10
        diags = [r["diagnosis"] for r in fp.records]
        assert diags == ["normal"] * 4 + ["unbalance_like"] * 2 + ["stopped"] * 2 + ["unrecognised_pattern"] * 2
        assert [bool(r["problems"]) for r in fp.records] == [False, False] + [True] * 6 + [False, False]
        assert all(r["simulated"] is True and r["device_id"] == "effente-sim-01" for r in fp.records)
        assert all(record_problems(r) == [] for r in fp.records)


needs_pty = pytest.mark.skipif(not sys.platform.startswith(("linux", "darwin")), reason="needs a pty")


def _spawn_on_pty(*args):
    """Start main.py with its stdin/stdout on a fresh pty. Returns (proc, master_fd, slave_fd)."""
    import fcntl
    import pty
    import termios

    def make_controlling_tty():  # so Ctrl-C on the pty sends SIGINT, as in a real terminal
        os.setsid()
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)

    master, slave = pty.openpty()
    proc = subprocess.Popen([sys.executable, MAIN, *args], stdin=slave, stdout=slave, stderr=subprocess.PIPE,
                            preexec_fn=make_controlling_tty)
    return proc, master, slave


def _pump(fd, seconds):
    import select

    out = b""
    end = time.time() + seconds
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.05)
        if r:
            try:
                out += os.read(fd, 4096)
            except OSError:
                break
    return out


@needs_pty
def test_keyboard_switches_case_and_quits_restoring_terminal():
    import termios

    with FakePlatform() as fp:
        proc, master, slave = _spawn_on_pty("--url", fp.url, "--interval", "0.1")
        try:
            assert termios.tcgetattr(slave)[3] & termios.ICANON
            out = _pump(master, 0.8)
            assert not termios.tcgetattr(slave)[3] & termios.ICANON  # keys without Enter
            os.write(master, b"4")
            out += _pump(master, 0.8)
            os.write(master, b"2")
            out += _pump(master, 0.8)
            os.write(master, b"t")
            out += _pump(master, 0.8)
            os.write(master, b"m")  # watching -> display: check-ins instead of records
            out += _pump(master, 0.8)
            os.write(master, b"q")
            proc.wait(timeout=5)
            out += _pump(master, 0.2)
            assert proc.returncode == 0
            assert termios.tcgetattr(slave)[3] & termios.ICANON  # restored
            assert termios.tcgetattr(slave)[3] & termios.ECHO
        finally:
            if proc.poll() is None:
                proc.kill()
            os.close(master)
            os.close(slave)
        text = out.decode(errors="ignore")
        assert "case unrecognised" in text and "case unbalance" in text and "[SIM] display" in text
        assert "heat camera" in text and "heat imu" in text
        recs = fp.records
        assert recs[0]["heat"]["source"] == "camera" and recs[-1]["heat"]["source"] == "imu"
        diags = [r["diagnosis"] for r in fp.records]
        assert "unrecognised_pattern" in diags and "unbalance_like" in diags
        assert fp.check_ins and fp.check_ins[-1][1]["mode"] == "display"


@needs_pty
def test_ctrl_c_restores_terminal():
    import termios

    proc, master, slave = _spawn_on_pty("--url", f"http://127.0.0.1:{free_port()}", "--interval", "0.1")
    try:
        _pump(master, 0.8)
        assert not termios.tcgetattr(slave)[3] & termios.ICANON
        os.write(master, b"\x03")  # Ctrl-C through the terminal
        proc.wait(timeout=5)
        assert proc.returncode == 130
        assert b"Traceback" not in proc.stderr.read()
        assert termios.tcgetattr(slave)[3] & termios.ICANON
    finally:
        if proc.poll() is None:
            proc.kill()
        os.close(master)
        os.close(slave)


def test_keys_restore_after_exception():
    import pty
    import termios

    import sim_device

    master, slave = pty.openpty()
    try:
        stream = os.fdopen(slave, "rb", buffering=0, closefd=False)
        with pytest.raises(RuntimeError):
            with sim_device.Keys(stream) as keys:
                assert keys.active
                assert not termios.tcgetattr(slave)[3] & termios.ICANON
                raise RuntimeError("boom")
        assert termios.tcgetattr(slave)[3] & termios.ICANON
    finally:
        os.close(master)
        os.close(slave)
