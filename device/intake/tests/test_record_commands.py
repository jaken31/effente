"""Recording in several goes, from a dashboard: the "record", "stop", "idle" and "detect" commands
(device/intake/device_app.py), on the made-up board of test_board_flow.

Run with pytest, or without it:  python3 -m device.intake.tests.test_record_commands
"""

import json
import tempfile

from device.intake.tests.test_board_flow import flow


def test_fixing_a_machine_idle_then_more_recording_and_the_model_is_made_again():
    """Record 3 minutes and train; then idle (someone works on the machine), record 1 more minute keeping the
    first, stop: the model is made again from both recordings, and the device is detecting again."""
    said = {}

    def do(name, **body):
        def at(app):
            try:
                said[name + str(len(said))] = (app.command(name, {"command": name, **body}), app.mode)
            except ValueError as e:
                said[name + str(len(said))] = (str(e), app.mode)
        return at

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        steps = {10: do("record", keep=False), 190: do("stop"),        # 3 minutes, then TRAINING -> DETECTING
                 230: do("idle"), 240: do("detect"), 250: do("idle"),  # idle and back, then idle for the repair
                 260: do("record"), 265: do("stop"),                   # 5 s: too little to add
                 330: do("stop"),                                      # 70 s: added, trained again
                 340: do("record", keep=False), 350: do("idle"),       # a new start, given up after 10 s
                 355: do("detect")}                                    # the model from before is still there
        app, board, headers = flow(tmp, 370, lambda s: 40.0, presses=(), at=steps)
        replies = list(said.values())
        modes = [m for _, m in replies]
        assert modes == ["recording", "training", "display", "watching", "display", "recording", "recording",
                         "training", "recording", "display", "watching"], modes
        assert "record 30 s or more" in (replies[6][0].get("note") or ""), replies[6]
        assert replies[5][0]["note"].startswith("added to 1 earlier"), replies[5]
        assert "too short to keep" in replies[9][0]["note"], replies[9]
        d = json.loads(app.model_path.read_text())
        rep = d["training_report"]
        assert len(rep["recordings"]) == 2, rep["recordings"]  # the first 3 minutes and the 70 s after the repair
        assert 230 <= rep["rows_learned"] + rep["rows_calibration"] <= 255, rep  # about 180 + 70 one-a-second rows
        assert [p.split("/")[-1].split("\\")[-1] for p in app.sessions] == rep["recordings"]
        seen = [h.split()[0] for h in headers]
        for want in ("@IDLE", "@RECORDING", "@TRAINING", "@DETECTING"):
            assert want in seen, (want, sorted(set(seen)))
        assert seen.count("@TRAINING") >= 2  # trained twice
        info = app.model_info()  # what a dashboard is told about the model
        assert info["recordings"] == rep["recordings"] and info["learned"]["model_line"] == d["threshold"]
        assert info["learned"]["normal_levels"] == d["baseline"]["levels"] and "vib_rms_x" in info["readings_in_the_model"]
        assert "k = 4 **" in info["how_to_compute"]["k"] and "model_file" not in info
        assert app.model_info(full=True)["model_file"]["autoencoder"] == d["autoencoder"]
        k = 4 ** (2 * 0.5 - 1)  # a dashboard's own sum for sudden_impact at the default, against the device's
        mine = max((1 + 1 / k) * 10 ** info["learned"]["highest_while_learning"]["vib_peak_g_log10"], 0.05)
        assert abs(mine - app.fires_at()["one_moment"]["sudden_impact"]["fires_at"]) < 1e-3
        # what was recorded and learned can be read, and only read
        listing = app.data_listing()
        assert [r["name"] for r in listing["recordings"] if r["in_the_model"]] == rep["recordings"]
        assert any(m["in_use"] and m["file"] == app.model_path.name for m in listing["models"])
        files = app.recording_files(rep["recordings"][0])["files"]
        assert any(f["path"].startswith("features/") for f in files) and any(f["path"] == "events.jsonl" for f in files)
        rows = app.recording_rows(rep["recordings"][0], start=10, limit=5, every=2)
        assert rows["returned"] == 5 and len(rows["rows"][0]) == len(rows["names"]) and rows["t"][1] - rows["t"][0] == 2.0
        assert app.data_file("models", app.model_path.name) == app.model_path.resolve()
        for kind, bad in (("models", "../state.json"), ("recordings", "../../secrets"), ("data", "state.json"), ("models", "nothing.json")):
            try:
                app.data_file(kind, bad)
                raise AssertionError((kind, bad))
            except ValueError:
                pass
        assert not hasattr(app, "set_model") and not hasattr(app, "patch_model")  # a model is never changed from outside
        c = app.controls()
        assert {"record", "stop", "idle", "detect", "press", "clear_history"} <= set(c["commands"])
        assert c["now"]["state"] == "DETECTING" and len(c["now"]["recordings_in_model"]) == 2 and "TRAINING" in c["states"]


def test_commands_that_make_no_sense_are_refused_and_change_nothing():
    said = []

    def do(name, **body):
        def at(app):
            try:
                app.command(name, {"command": name, **body})
                said.append((name, "ok", app.mode))
            except ValueError as e:
                said.append((name, str(e), app.mode))
        return at

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        steps = {5: do("stop"), 6: do("detect"), 7: do("record", keep="yes"), 8: do("record"), 9: do("detect")}
        app, board, headers = flow(tmp, 12, lambda s: 40.0, presses=(), at=steps)
        assert [m for _, _, m in said] == ["display", "display", "display", "recording", "recording"]
        assert "not recording" in said[0][1] and "no model yet" in said[1][1] and "true or false" in said[2][1]
        assert said[3][1] == "ok" and "recording" in said[4][1]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
