"""The optional dials: each sets one group on its own, and with none set the device does what the main
sensitivity alone did.

  model_sensitivity          the model's slow (median) alarm
  vibration_ / heat_ / sound_sensitivity
                             the outside-normal checks for that sensor
  early_warning_sensitivity  the early warning; early_warning false switches it off
  spike_sensitivity          single spikes (its own default, 0.5; it never follows the main one)

Run with:  python3 -m device.intake.tests.test_dials
"""

import json
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from effente import safety as SF
from effente.model_runtime import Model
from device.intake.device_app import OVERRIDES, DeviceApp
from device.intake.stream import Stream
from device.intake.tests.test_board_flow import FakeBoard
from device.intake.tests.test_model_spike import trained
from device.intake.tests.test_safety import feed

GROUP = {"model_sensitivity": "model", "vibration_sensitivity": "vibration", "heat_sensitivity": "temperature",
         "sound_sensitivity": "sound"}


def app_with_model(tmp):
    app = DeviceApp(tmp, FakeBoard(1, lambda s: 30.0))
    app.watcher = app._watcher(Model(trained()), None)  # as training or a resume sets it
    return app


def where(app) -> dict:
    """Every place a margin is used, as it is now."""
    return {"safety": dict(app.safety.margins), "spike": app.safety.spike_margin, "model": app.watcher.margin,
            "model_spike": app.watcher.spike_margin, "early": app.early.sensitivity,
            "shown": app.settings()["counts_as_wrong"]}


def test_with_no_override_everything_follows_the_main_dial():
    with tempfile.TemporaryDirectory() as tmp:
        app = app_with_model(tmp)
        for main in (0.25, 0.5, 0.9):
            app.settings({"sensitivity": main})
            m = SF.margins(main)
            assert app.safety.margins == m and app.watcher.margin == m["model"] and app.early.sensitivity == main
            assert app.safety.spike_margin == 2.0 and app.watcher.spike_margin == 1.0  # the spike dial stays at 0.5
            shown = app.settings()["counts_as_wrong"]
            assert shown["vibration_times_normal"] == round(m["vibration"], 2) and shown["model_score_over"] == round(m["model"], 2)
        s = SF.Safety({"levels": {}})
        s.set_group_sensitivity({"vibration": None, "temperature": None, "sound": None})
        assert s.margins == SF.margins(0.5)


def test_each_override_moves_only_its_own_group():
    with tempfile.TemporaryDirectory() as tmp:
        app = app_with_model(tmp)
        before = where(app)
        for name in OVERRIDES:
            app.settings({name: 1.0})
            now = where(app)
            moved = {k for k in before if now[k] != before[k]}
            if name in GROUP:
                g = GROUP[name]
                assert (now["safety"][g] == SF.margins(1.0)[g]) == (g != "model"), (name, now["safety"])
                assert {k for k in before["safety"] if now["safety"][k] != before["safety"][k]} <= {g}, name
                expect = {"model", "shown"} if g == "model" else {"safety", "shown"}
                assert moved == expect, (name, moved)
            else:  # the early warning
                assert moved == {"early"} and now["early"] == 1.0, (name, moved)
            app.settings({name: None})  # null: follow the main dial again
            assert where(app) == before, name
        app.settings({"spike_sensitivity": 1.0})
        moved = {k for k in before if where(app)[k] != before[k]}
        assert moved == {"spike", "model_spike", "shown"}, moved


def test_an_override_changes_what_counts_as_wrong_for_its_sensor_only():
    """A warm site: heat turned down to 0.25 (+30 C needed), vibration left on the main 0.5 (2x needed)."""
    base = {"levels": {"vib_velocity_mm_s": 1.0, "vib_total_g": 0.03, "imu_temp_c": 45.0, "snd_level_db": 60.0}}
    s = SF.Safety(base)
    s.set_group_sensitivity({"temperature": 0.25})
    feed(s, 15, vel=2.5, imu=65.0)  # 2.5x and +20 C
    assert [p["name"] for p in s.problems()] == ["vibration_above_normal"], s.problems()
    s.set_group_sensitivity({"temperature": None})  # following the main 0.5 again: +15 C counts
    feed(s, 15, start=15, vel=2.5, imu=65.0)
    assert {p["name"] for p in s.problems()} == {"vibration_above_normal", "hotter_than_normal"}


def test_the_early_warning_can_be_switched_off():
    with tempfile.TemporaryDirectory() as tmp:
        app = app_with_model(tmp)
        app.mode = "watching"
        calls = []
        app.early.observe = lambda *a, **k: calls.append(a) or []
        row = FakeBoard(1, lambda s: 30.0)  # any row will do: the model judges it, the early warning is fed it
        from device.intake.extract import feature_stream
        x = next(iter(feature_stream(row, hop_s=1.0)))[1]
        app.watcher._judge(0.0, x)
        assert len(calls) == 1
        app.early.shown, app.early.symptom = True, "too_hot"  # something on show
        app.settings({"early_warning": False})
        assert app.early.screen() is None and app.status()["early_warning"] == {"on": False, "switched_off": True}
        app.watcher._judge(1.0, x)
        assert len(calls) == 1  # not fed while off
        app.settings({"early_warning": True})
        app.watcher._judge(2.0, x)
        assert len(calls) == 2 and "switched_off" not in app.status()["early_warning"]


def test_the_dials_round_trip_through_settings_and_persist():
    with tempfile.TemporaryDirectory() as tmp:
        stream = Stream("127.0.0.1", 0).start()
        try:
            app = DeviceApp(tmp, FakeBoard(1, lambda s: 30.0), stream=stream)
            url = f"http://127.0.0.1:{stream.port}/settings"

            def post(body):
                req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST")
                return json.loads(urllib.request.urlopen(req, timeout=5).read())

            got = json.loads(urllib.request.urlopen(url, timeout=5).read())
            assert all(got[k] is None for k in OVERRIDES) and got["early_warning"] is True, got
            got = post({"heat_sensitivity": 0.25, "sound_sensitivity": 0.1, "early_warning": False})
            assert got["heat_sensitivity"] == 0.25 and got["sound_sensitivity"] == 0.1 and got["early_warning"] is False
            assert got["sensitivity"] == 0.5 and got["vibration_sensitivity"] is None
            assert got["counts_as_wrong"]["degrees_over_normal"] == 30.0 and got["counts_as_wrong"]["vibration_times_normal"] == 2.0
            for bad in ({"heat_sensitivity": 3}, {"early_warning": "no"}, {"sound_sensitivity": None, "loudness": 1}):
                try:
                    post(bad)
                    raise AssertionError(f"{bad} was accepted")
                except urllib.error.HTTPError as e:
                    assert e.code == 400
            assert app.overrides["sound_sensitivity"] == 0.1  # a bad request changes nothing
            st = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{stream.port}/latest", timeout=5).read())["status"]
            assert st["heat_sensitivity"] == 0.25 and st["early_warning"] is False and st["model_sensitivity"] is None
            post({"sound_sensitivity": None})
            saved = json.loads((Path(tmp) / "settings.json").read_text())
            assert saved == {"sensitivity": 0.5, "spike_sensitivity": 0.5, "heat_sensitivity": 0.25,
                             "early_warning": False}, saved  # only what is set
            events = [e.payload for e in stream._rings["events"] if e.payload["event"] == "settings"]
            assert events[-1]["details"] == {"sound_sensitivity": None}, events[-1]
        finally:
            stream.stop()
        again = DeviceApp(tmp, FakeBoard(1, lambda s: 30.0))  # a restart
        assert again.overrides["heat_sensitivity"] == 0.25 and not again.early_on
        assert again.safety.margins["temperature"] == 30.0 and again.safety.margins["vibration"] == 2.0
        (Path(tmp) / "settings.json").write_text('{"sensitivity": 0.75}')  # by hand: the rest back to defaults
        again._settings_seen = None
        again._load_settings()
        assert again.early_on and all(v is None for v in again.overrides.values())
        assert again.safety.margins == SF.margins(0.75) and again.spike_sensitivity == 0.5


if __name__ == "__main__":
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                started = time.time()
                fn()
                print(f"ok    {name} ({time.time() - started:.0f} s)")
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(f"FAIL  {name}: {type(e).__name__}: {e}")
    print("all passed" if not failed else f"{failed} failed")
    sys.exit(1 if failed else 0)
