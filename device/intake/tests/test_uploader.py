"""Tests for the uploader to the laptop platform (device.intake.uploader, platform/CONTRACT.md 3.1).

Run with:  python3 -m device.intake.tests.test_uploader
"""

import json
import math
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

import numpy as np

from effente import fault_types as FT
from effente import features as F
from effente import safety as SF
from device.intake.tests.fake_platform import FakePlatform, record_errors
from device.intake.uploader import NO_TYPE_FITS, Uploader

START = 6 * 3600 + 10 * 60  # Monday 06:10 in the simulated conveyor's week: it is running


def row(vel=1.0, total_g=0.03, imu=40.0):
    """A feature row with the readings the safety checks use, and a full vibration spectrum."""
    x = np.full(F.N_FEATURES, np.nan)
    for a in "xyz":
        x[F.INDEX[f"vib_rms_{a}"]] = math.log10(total_g / math.sqrt(3))
    x[F.INDEX["vib_velocity_mm_s"]] = vel
    x[F.INDEX["imu_temp_c"]] = imu
    x[F.INDEX["vib_kurtosis"]] = 0.0
    x[F.INDEX["vib_peak_g"]] = math.log10(0.1)
    x[F.INDEX["trend_shocks_10m"]] = 0.0
    for i in range(len(F.VIB_EDGES_HZ) - 1):
        x[F.INDEX[f"vib_band_{i:02d}"]] = -4.5
    return x


def model_file(tmp, name="rec-test"):
    """A model file with the parts message 1 is made from, as effente.trainer writes them."""
    d = {"format": "effente-model-1", "features_version": "test", "threshold": 1.0,
         "fault_types": [{"name": ft.name, "text": ft.text, "sensing": ft.sensing} for ft in FT.TYPES],
         "feature_table": [{"name": n, "text": F.FEATURE_TEXT.get(n, ""), "unit": F.FEATURE_KIND.get(n, "")}
                           for n in ("vib_1x", "vib_velocity_mm_s")]}
    p = Path(tmp) / f"{name}.json"
    p.write_text(json.dumps(d))
    return p, d


def until(cond, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.02)
    return False


def limit_problems(safety, t_start, seconds, vel=14.0):
    """Feed the real safety checks over-the-limit vibration; returns (last t0, problems)."""
    t = t_start
    for k in range(int(seconds * 4)):
        t = t_start + k * 0.25
        safety.update(t, row(vel=vel))
    return t, safety.problems()


def test_unset_url_means_no_uploader():
    assert Uploader.from_env("effente-01", "m", env={}) is None
    assert Uploader.from_env("effente-01", "m", env={"EFFENTE_PLATFORM_URL": "  "}) is None
    u = Uploader.from_env("effente-01", "m", env={"EFFENTE_PLATFORM_URL": "http://10.0.0.2:8000/"})
    assert u is not None and u.base == "http://10.0.0.2:8000"


def test_model_message_and_record_shape_follow_the_contract():
    with tempfile.TemporaryDirectory() as tmp, FakePlatform() as fp:
        path, d = model_file(tmp)
        up = Uploader(fp.url, "effente-01", "conveyor-07", simulated=True).start()
        try:
            up.announce(path, d, 0.5)
            t0 = 1_763_136_131.0  # Unix time, as the board's clock gives it
            results = [
                {"diagnosis": "unbalance_like", "diagnosis_this_minute": "unbalance_like", "confidence": 0.7,
                 "unknown_score": 2.4, "features": {"vib_1x": 7.2}, "fault_types": {"unbalance_like": 9.1},
                 "abnormal_minutes": 3, "out_of_range": 0, "note": ""},
                {"diagnosis": "unrecognised_pattern", "confidence": 0.3, "unknown_score": 1.5, "features": {},
                 "abnormal_minutes": 1, "note": "abnormal, but no fault type fits well"},
                {"diagnosis": "warming_up", "confidence": 0.0, "unknown_score": None, "note": "first minute"},
            ]
            for k, res in enumerate(results):
                up.on_row(t0 + k, row(), "watching", res, [], 0.5, 1.0)
            assert up.wait_idle(5) and len(fp.records) == 3, (fp.records, up.stats)
        finally:
            up.stop()
        assert not fp.errors, fp.errors
        m = fp.models[0]
        assert m["model_version"].startswith("conveyor-07-rec-test-") and m["sensitivity"] == 0.5
        assert len(m["fault_types"]) == len(FT.TYPES) and m["simulated"] is True
        moved = next(t for t in m["fault_types"] if t["name"] == "device_moved")
        assert moved["group"] == "sensing" and moved["validated"] is False
        assert "library_entry" not in m and "source" not in m  # the model file gives neither
        a, b, c = fp.records
        assert a["model_version"] == m["model_version"] and a["at"] == "2025-11-14T16:02:11Z"
        assert a["reason"] == FT.TYPES[FT.NAMES.index("unbalance_like")].text and a["wrong"] is True
        assert a["sensitivity"] == 0.5 and a["alarm_line"] == 1.0 and len(a["spectrum"]) == 20
        assert a["fault_types"] == {"unbalance_like": 9.1} and a["problems"] == []
        assert b["reason"] == NO_TYPE_FITS and b["wrong"] is True and "fault_types" not in b
        assert c["reason"] == "" and c["wrong"] is False and c["unknown_score"] is None


def test_a_problem_while_the_diagnosis_is_normal():
    safety = SF.Safety()
    t, problems = limit_problems(safety, 1000.0, 12)
    assert [p["name"] for p in problems] == [SF.UNIVERSAL[0]]  # the vibration limit, whatever it is called
    with tempfile.TemporaryDirectory() as tmp, FakePlatform() as fp:
        path, d = model_file(tmp)
        up = Uploader(fp.url, "effente-01", "conveyor-07").start()
        try:
            up.announce(path, d, 0.75)
            line = SF.margins(0.75)["model"]
            up.on_row(t, row(vel=14.0), "watching", {"diagnosis": "normal", "confidence": 0.9,
                                                     "unknown_score": 0.2, "features": {"vib_1x": 0.4}}, problems,
                      0.75, line)
            assert up.wait_idle(5) and fp.records
        finally:
            up.stop()
        r = fp.records[0]
        assert not record_errors(r), record_errors(r)
        assert r["diagnosis"] == "normal" and r["wrong"] is True and r["reason"] == ""
        p = r["problems"][0]
        assert p["name"] == SF.UNIVERSAL[0] and p["kind"] == "limit" and p["value"] == 14.0
        assert p["text"] == SF.WHAT[SF.UNIVERSAL[0]][0] and r["alarm_line"] == 0.5
        # device time 1000 s is not Unix time: since is placed by its distance from the row's t0
        from datetime import datetime
        at, since = (datetime.fromisoformat(r["at"].replace("Z", "+00:00")),
                     datetime.fromisoformat(p["since"].replace("Z", "+00:00")))
        held = (at - since).total_seconds()
        assert 0 <= t - problems[0]["since"] <= 12 and abs(held - (t - problems[0]["since"])) <= 1.0, held


def test_check_in_with_problems_while_recording():
    from device.intake.device_app import DeviceApp

    class Clock:
        t = 1000.0

        def __call__(self):
            return self.t

    with tempfile.TemporaryDirectory() as tmp, FakePlatform() as fp:
        clock = Clock()
        up = Uploader(fp.url, "effente-01", "conveyor-07", checkin_every_s=0.0).start()
        try:
            app = DeviceApp(tmp, _NoSource(), clock=clock, machine_id="conveyor-07", uploader=up)
            app.on_button()
            assert app.mode == "recording"
            for k in range(60):
                clock.t = 1000.0 + k * 0.25
                app._row(clock.t, row(vel=14.0))
            app._close_recorder()
            assert until(lambda: fp.checkins and fp.checkins[-1][1]["problems"]), fp.checkins[-3:]
        finally:
            up.stop()
        assert not fp.records and not fp.errors, fp.errors
        dev, c = fp.checkins[-1]
        assert dev == "effente-01" and c["mode"] == "recording" and c["machine_id"] == "conveyor-07"
        assert c["wrong"] is True and [p["name"] for p in c["problems"]] == [SF.UNIVERSAL[0]]
        assert c["problems"][0]["kind"] == "limit" and c["sensitivity"] == 0.5


def test_need_model_repeats_message_1():
    with tempfile.TemporaryDirectory() as tmp, FakePlatform() as fp:
        path, d = model_file(tmp)
        up = Uploader(fp.url, "effente-01", "conveyor-07").start()
        try:
            up.announce(path, d, 0.5)
            assert until(lambda: len(fp.models) == 1)
            fp.need_model_once = True  # e.g. the platform restarted with no models
            up.on_row(2000.0, row(), "watching", {"diagnosis": "normal"}, [], 0.5, 1.0)
            assert until(lambda: len(fp.models) == 2), up.stats
            up.on_row(2001.0, row(), "watching", {"diagnosis": "normal"}, [], 0.5, 1.0)
            assert up.wait_idle(5) and len(fp.records) == 2
            time.sleep(0.3)
            assert len(fp.models) == 2 and fp.models[1] == fp.models[0]
        finally:
            up.stop()


def test_platform_down_then_catch_up_with_the_newest_records():
    hold = socket.socket()  # bound, not listening: requests are refused and no other test can take the port
    hold.bind(("127.0.0.1", 0))
    port = hold.getsockname()[1]
    with tempfile.TemporaryDirectory() as tmp:
        path, d = model_file(tmp)
        logs = []
        up = Uploader(f"http://127.0.0.1:{port}", "effente-01", "conveyor-07", backlog=50, batch=20,
                      retry_s=(0.1,), log=logs.append).start()
        try:
            up.announce(path, d, 0.5)
            t = time.perf_counter()
            for k in range(80):  # 80 s of data while nothing listens
                up.on_row(5000.0 + k, row(), "watching", {"diagnosis": "normal"}, [], 0.5, 1.0)
            assert time.perf_counter() - t < 0.5  # the pipeline never waits for the network
            assert until(lambda: up.reachable is False)
            hold.close()
            with FakePlatform(port) as fp:
                assert up.wait_idle(10), up.stats
                assert len(fp.models) == 1
                ts = [r["at"] for r in fp.records]
                assert len(fp.records) == 50 and ts == sorted(ts), len(fp.records)
                assert fp.batches == [20, 20, 10], fp.batches  # JSON arrays, oldest first
                assert up.stats["records_dropped"] == 30 and up.stats["records_sent"] == 50
                up.on_row(5080.0, row(), "watching", {"diagnosis": "normal"}, [], 0.5, 1.0)
                assert until(lambda: len(fp.records) == 51) and fp.batches[-1] == 0  # live again: one object
                assert not fp.errors, fp.errors
        finally:
            up.stop()
            hold.close()
        assert any("not reachable" in m for m in logs) and any("reachable again" in m for m in logs), logs


def test_a_platform_that_never_answers_does_not_slow_the_pipeline():
    hole = socket.socket()
    hole.bind(("127.0.0.1", 0))
    hole.listen(64)  # accepts connections, never replies
    try:
        with tempfile.TemporaryDirectory() as tmp:
            path, d = model_file(tmp)
            up = Uploader(f"http://127.0.0.1:{hole.getsockname()[1]}", "effente-01", "m", timeout=1.0,
                          log=lambda m: None).start()
            try:
                up.announce(path, d)
                t = time.perf_counter()
                for k in range(4 * 300):  # 5 minutes of rows, 4 a second
                    up.on_row(100.0 + 0.25 * k, row(), "watching", {"diagnosis": "normal"}, [], 0.5, 1.0)
                assert time.perf_counter() - t < 1.0
                assert len(up.records) == 300
            finally:
                up.stop()
    finally:
        hole.close()


def test_a_sensitivity_set_on_the_platform_is_applied_and_survives_a_restart():
    from device.intake.device_app import DeviceApp

    with tempfile.TemporaryDirectory() as tmp, FakePlatform() as fp:
        fp.settings = {"sensitivity": 0.7}
        up = Uploader(fp.url, "effente-01", "conveyor-07", checkin_every_s=0.0).start()
        try:
            app = DeviceApp(tmp, _NoSource(), uploader=up)
            for k in range(8):
                app._row(1000.0 + 0.25 * k, row())
            assert until(lambda: app.sensitivity == 0.7), app.sensitivity
            assert app.safety.sensitivity == 0.7
            assert json.loads((Path(tmp) / "settings.json").read_text())["sensitivity"] == 0.7
            app._row(1005.0, row())  # the next check-in reports the new value
            assert until(lambda: any(c.get("sensitivity") == 0.7 for _, c in fp.checkins)), fp.checkins
            fp.settings = {"sensitivity": 2.0}  # not a valid value: ignored, the device keeps its own
            app._row(1010.0, row())
            assert until(lambda: "platform setting not used" in app.message), app.message
            assert app.sensitivity == 0.7
        finally:
            up.stop()
        again = DeviceApp(tmp, _NoSource())
        assert again.sensitivity == 0.7


def test_device_announces_its_model_and_sends_records_while_watching():
    """The whole device: learn on the simulated conveyor, train, watch. Takes about 10 s."""
    from device.intake.device_app import DeviceApp
    from device.intake.tests.test_device_app import Clock, rows, run
    from effente.simulate_conveyor import ConveyorSim

    with tempfile.TemporaryDirectory() as tmp, FakePlatform() as fp:
        clock = Clock()
        up = Uploader(fp.url, "effente-01", "conveyor-07", simulated=True, checkin_every_s=0.0).start()
        try:
            app = DeviceApp(tmp, ConveyorSim(days=1), clock=clock, machine_id="conveyor-07", uploader=up)
            run(app, clock, rows(START, 7), press_at=(START + 10, START + 6 * 60 + 30))
            assert app.mode == "watching", (app.mode, app.message)
            assert until(lambda: len(fp.models) == 1)
            run(app, clock, rows(START + 7 * 60, 1))
            assert up.wait_idle(10) and len(fp.records) >= 55, (len(fp.records), up.stats)
            modes = {c["mode"] for _, c in fp.checkins}
            assert {"display", "recording"} <= modes, modes
            version = fp.models[0]["model_version"]
            assert all(r["model_version"] == version for r in fp.records)
            assert all(r["sensitivity"] == 0.5 and r["alarm_line"] == 1.0 for r in fp.records)
            assert not fp.errors, fp.errors[:5]
            # a restart: the saved model is announced again, with the same version
            again = DeviceApp(tmp, ConveyorSim(days=1), clock=clock, machine_id="conveyor-07", uploader=up)
            assert again.mode == "watching" and until(lambda: len(fp.models) == 2)
            assert fp.models[1]["model_version"] == version
        finally:
            up.stop()


def test_wait_idle_waits_for_records_still_on_their_way():
    with tempfile.TemporaryDirectory() as tmp, FakePlatform() as fp:
        path, d = model_file(tmp)
        fp.delay_s = 0.5  # a slow platform: the batch is off the queue but not yet stored
        up = Uploader(fp.url, "effente-01", "conveyor-07", log=lambda m: None).start()
        try:
            up.announce(path, d, 0.5)
            for k in range(3):
                up.on_row(5000.0 + k, row(), "watching", {"diagnosis": "normal"}, [], 0.5, 1.0)
            assert up.wait_idle(10)
            assert len(fp.records) == 3 and up.stats["records_sent"] == 3, (len(fp.records), up.stats)
        finally:
            up.stop()


def test_a_broken_reply_keeps_the_records():
    junk = socket.socket()
    junk.bind(("127.0.0.1", 0))
    junk.listen(8)
    stop = threading.Event()

    def answer_junk():  # the model is accepted; every later request gets bytes that are not HTTP
        junk.settimeout(0.2)
        first = True
        while not stop.is_set():
            try:
                c, _ = junk.accept()
            except OSError:
                continue
            with c:
                c.recv(65536)
                body = b'{"ok": true}'
                c.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: %d\r\n"
                          b"Connection: close\r\n\r\n%s" % (len(body), body) if first else b"not http at all\r\n\r\n")
                first = False

    threading.Thread(target=answer_junk, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            path, d = model_file(tmp)
            up = Uploader(f"http://127.0.0.1:{junk.getsockname()[1]}", "effente-01", "m", retry_s=(0.1,),
                          log=lambda m: None).start()
            try:
                up.announce(path, d)
                assert until(lambda: up.stats["models_sent"] == 1), up.stats
                for k in range(5):
                    up.on_row(100.0 + k, row(), "watching", {"diagnosis": "normal"}, [], 0.5, 1.0)
                assert until(lambda: up.reachable is False), up.stats
                with up.lock:  # put back on the queue, not lost
                    assert len(up.records) == 5 and up.stats["records_sent"] == 0, (len(up.records), up.stats)
            finally:
                up.stop()
    finally:
        stop.set()
        junk.close()


class _NoSource:
    """A source that is never read: these tests feed rows to the app themselves."""
    sensors = ("accel",)

    class info:
        pass


if __name__ == "__main__":
    failed = 0
    only = sys.argv[1:]
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn) and (not only or any(o in name for o in only)):
            try:
                fn()
                print(f"ok    {name}")
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(f"FAIL  {name}: {type(e).__name__}: {e}")
    print("all passed" if not failed else f"{failed} failed")
    sys.exit(1 if failed else 0)
