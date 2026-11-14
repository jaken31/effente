"""What a connected program can tell the device: GET /controls, POST /settings and POST /command on the stream.

Run with pytest, or without it:  python3 -m device.intake.tests.test_controls
"""

import json
import tempfile
import urllib.error
import urllib.request

import numpy as np

from device.intake.device_app import COMMANDS, SETTINGS, DeviceApp
from device.intake.source import UnoQSource
from device.intake.stream import Stream
from device.intake.tests.test_mic import Clock, imu_batch


def get(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
        return json.loads(r.read())


def post(port, path, body):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def app_and_stream(folder):
    clock = Clock()
    src = UnoQSource(clock=clock)
    stream = Stream(port=0, device_id="d", machine_id="m").start()
    app = DeviceApp(folder, src, stream=stream, device_id="d", machine_id="m", clock=clock)
    return app, src, stream, clock


def test_controls_lists_every_setting_and_command():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        app, src, stream, _ = app_and_stream(d)
        try:
            for path in ("/controls", "/"):
                c = get(stream.port, path)["you_can_send"]
                assert set(SETTINGS) <= set(c["settings"]) and set(COMMANDS) <= set(c["commands"])
                assert c["settings"]["sensitivity"]["now"] == 0.5 and "does" in c["settings"]["sensitivity"]
                assert "send" in c["settings"] and "send" in c["commands"] and "password" in c["note"]
            assert set(get(stream.port, "/command")["commands"]) == set(COMMANDS)
            latest = get(stream.port, "/latest")  # the JSON a dashboard reads says what it may send back
            assert set(COMMANDS) <= set(latest["you_can_send"]["commands"]) and "does" in latest["you_can_send"]["settings"]["sensitivity"]
            short = latest["status"]["you_can_send"]
            assert short["commands"]["names"] == list(COMMANDS) and short["settings"]["names"] == list(SETTINGS)
            assert short["commands"]["useful_now"] == ["record", "detect", "train", "clear_history"]  # idle
            post(stream.port, "/settings", {"sensitivity": 0.75})
            assert get(stream.port, "/controls")["you_can_send"]["settings"]["sensitivity"]["now"] == 0.75
        finally:
            stream.stop()


def test_clear_history_empties_what_the_device_remembers():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        app, src, stream, clock = app_and_stream(d)
        try:
            for first in range(0, 3000, 25):
                imu_batch(src, clock, first)
            src.close()
            app.run()  # the pipeline, over what was queued
            assert len(app.history.secs) >= 2  # it has seconds of history now
            status, reply = post(stream.port, "/command", {"command": "clear_history"})
            assert status == 200 and reply["ok"] and reply["command"] == "clear_history"
            app._row(app.latest_t + 1.0, app.latest)  # the next row does it
            assert len(app.history.secs) <= 1 and np.isnan(app.history.min_t).all()  # only that row's own second
            assert (app.dir / "history.npz").exists()
        finally:
            stream.stop()


def test_press_is_the_button_and_unknown_commands_are_refused():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        app, src, stream, _ = app_and_stream(d)
        try:
            status, reply = post(stream.port, "/command", {"command": "press"})
            assert status == 200 and reply["mode_before"] == "display" and reply["mode"] == "recording" == app.mode
            status, reply = post(stream.port, "/command", {"command": "format_disk"})
            assert status == 400 and not reply["ok"] and "clear_history" in reply["error"]
            status, reply = post(stream.port, "/nothing", {})
            assert status == 404
        finally:
            app.stop() if hasattr(app, "stop") else None
            stream.stop()


def test_the_platforms_replies_can_carry_settings_and_commands():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        app, src, stream, _ = app_and_stream(d)
        try:
            app.platform_settings({"sensitivity": 0.6, "spike_sensitivity": 0.25, "no_such_setting": 1})
            assert app.sensitivity == 0.6 and app.spike_sensitivity == 0.25
            cmds = [{"id": "a1", "command": "clear_history"}, {"id": "a2", "command": "no_such_command"}, {"command": "press"}]
            app.platform_commands(cmds)
            assert app._clear_history is True and app.mode == "display"  # the one with no id is not done
            app._clear_history = False
            app.platform_commands(cmds)  # the platform repeats them: nothing is done twice
            assert app._clear_history is False
            assert json.loads((app.dir / "commands_done.json").read_text()) == ["a1", "a2"]
        finally:
            stream.stop()


def test_the_json_says_what_value_each_check_fires_at():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        app, src, stream, _ = app_and_stream(d)
        try:
            f = get(stream.port, "/latest")["fires_at"]
            assert f == {k: v for k, v in get(stream.port, "/fires_at").items() if k != "ok"}
            assert f["always"]["vibration_over_limit"]["fires_at"] == 11.0 and f["always"]["too_hot"]["fires_at"] == 100.0
            assert f["judging_now"] is False and f["model"] == "none"
            assert f["lasting"]["louder_than_normal"]["fires_at"] is None  # nothing learned yet
            assert f["one_moment"]["sudden_impact"]["default"] == "2x the highest" and f["one_moment"]["sudden_impact"]["fires_at"] is None
            # with something learned, and the dials moved, the numbers follow
            app.safety.set_baseline({"levels": {"vib_velocity_mm_s": 1.0, "vib_total_g": 0.02, "imu_temp_c": 30.0, "snd_level_db": 90.0},
                                     "spikes": {"features": {}, "peak_max": -1.0, "sound_max": 95.0,
                                                "moment_max": {"imu_temp_c": 31.0, "gyr_peak": 1.0}}, "period_s": 1})
            f = get(stream.port, "/fires_at")
            assert f["lasting"]["vibration_above_normal"]["fires_at"] == 2.0 and f["lasting"]["louder_than_normal"]["fires_at"] == 96.0
            assert f["lasting"]["hotter_than_normal"]["fires_at"] == 45.0
            assert f["one_moment"]["sudden_impact"]["fires_at"] == 0.2 and f["one_moment"]["sudden_sound"]["fires_at"] == 101.0
            assert f["one_moment"]["sudden_heat"]["fires_at"] == 36.0 and f["one_moment"]["sudden_twist"]["fires_at"] == 20.0
            post(stream.port, "/settings", {"sensitivity": 0.75, "spike_sensitivity": 0.25})
            f = get(stream.port, "/fires_at")
            assert f["lasting"]["louder_than_normal"]["fires_at"] == 93.0 and f["lasting"]["louder_than_normal"]["default"] == "+6 dB"
            assert f["one_moment"]["sudden_sound"]["fires_at"] == 107.0 and f["one_moment"]["sudden_impact"]["fires_at"] == 0.3
            assert f["always"]["vibration_over_limit"]["fires_at"] == 11.0  # no dial moves the fixed limits
        finally:
            stream.stop()


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
