"""The guided fan test (device/app/fan_test.py), run against a local DeviceApp instead of the board.

A DeviceApp with a live Stream is fed simulated conveyor rows about 100 times faster than real time
(its clock is the data's time, so "12 minutes of learning" takes about 12 seconds). The button press is
a `touch` of the press file instead of adb. When the script asks for the coin, the rows get the
once-per-turn vibration grown, as in test_device_app.

Run with:  python3 -m device.intake.tests.test_fan_test
"""

import importlib.util
import json
import sys
import tempfile
import threading
import time
from pathlib import Path

from effente import features as F
from effente.simulate_conveyor import ConveyorSim
from device.intake import contract as C
from device.intake.device_app import DeviceApp
from device.intake.extract import feature_stream
from device.intake.stream import Stream

ROOT = Path(__file__).resolve().parents[3]
START = 6 * 3600 + 10 * 60  # Monday 06:10, the conveyor is running
ROW_S = 0.01  # real seconds per row of 1 s of data: about 100x real time


def load_fan_test():
    spec = importlib.util.spec_from_file_location("fan_test", ROOT / "device" / "app" / "fan_test.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Clock:
    t = float(START)

    def __call__(self):
        return self.t


class Feeder(threading.Thread):
    """Feeds the app rows as its pipeline would, at ROW_S real seconds a row; `coin` grows vib_1x.
    It waits while the app trains: after a 12-minute learning the simulated conveyor stays inside its
    learned normal for only about 3 minutes (sound and heat drift), so no data is spent on training."""

    def __init__(self, app, clock, minutes=120):
        super().__init__(daemon=True)
        self.app, self.clock, self.minutes = app, clock, minutes
        self.coin = threading.Event()
        self.stop = threading.Event()
        self.rows = 0
        self.error = None

    def run(self):
        try:
            sim = ConveyorSim(days=1, start_s=START, end_s=START + self.minutes * 60, faults=[])
            for t0, x in feature_stream(sim, hop_s=1.0):
                if self.stop.is_set():
                    return
                if self.coin.is_set():
                    x = x.copy()
                    x[F.INDEX["vib_1x"]] += 0.6  # once-per-turn vibration four times larger
                self.clock.t = t0
                self.app._row(t0, x)
                self.rows += 1
                time.sleep(ROW_S)
                while self.app.mode == "training" and not self.stop.is_set():
                    time.sleep(0.02)  # as test_device_app does: the short simulated normal must not run out
        except Exception as e:  # noqa: BLE001
            self.error = f"{type(e).__name__}: {e}"


def test_fan_test_walks_through_learning_training_and_the_coin():
    fan_test = load_fan_test()
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp) / "device-data"
        clock = Clock()
        stream = Stream("127.0.0.1", 0, status_every_s=1.0, client_queue=5000).start()
        app = DeviceApp(data, ConveyorSim(days=1), stream=stream, clock=clock)
        feeder = Feeder(app, clock)
        feeder.start()
        prompts, said = [], []

        def ask(text):
            prompts.append(text)
            if "coin" in text:
                feeder.coin.set()
            return ""

        try:
            code = fan_test.main(
                ["--url", f"http://127.0.0.1:{stream.port}", "--no-forward",
                 "--press-cmd", f"touch '{data / 'press'}'",
                 "--minutes", "12", "--normal-minutes", "0.02", "--fault-minutes", "0.5",
                 "--try-sensitivity", "0.9", "--sensitivity-minutes", "0.05", "--poll", "0.2", "--settle", "1",
                 "--report-dir", str(Path(tmp) / "reports")],
                ask=ask, say=said.append)
        finally:
            feeder.stop.set()
            feeder.join(5)
            stream.stop()
        if "-v" in sys.argv:
            print("\n".join(said))
        assert feeder.error is None, feeder.error
        reports = sorted((Path(tmp) / "reports").glob("fan-test-*.json"))
        assert len(reports) == 1, (reports, said[-5:])
        report = json.loads(reports[0].read_text())
        assert "error" not in report, (report.get("error"), said[-10:])
        steps = report["steps"]
        assert steps["check"]["settings"]["sensitivity"] == 0.5 and steps["check"]["mode"] == "display"
        assert steps["record"]["learned_s"] >= 12 * 60 and "pressed_at" in steps["record"]
        assert steps["train"]["model"] and steps["train"]["model_version"].startswith("rec-")
        normal = steps["normal"]
        assert normal["results"] > 0 and normal["status_polls"] > 0
        assert normal["wrong_episodes"] == 0 and normal["what_named"] == {}, normal  # no coin: nothing said
        assert set(normal["diagnosis_detail_seen"]) <= {"normal", "warming_up"}, normal
        fault = steps["fault"]
        assert fault["said_wrong"] and fault["named_unbalance"], fault
        assert fault["first_wrong"]["after_s"] >= 0 and fault["described_vibration"], fault["what_named"]
        assert fault["diagnosis_seen"].get("imbalance"), fault["diagnosis_seen"]  # the contract's name
        trial = steps["sensitivity"][0]
        assert trial["sensitivity"] == 0.9 and trial["reply_ok"] and trial["restored"] == 0.5
        assert app.sensitivity == 0.5  # put back
        assert report["summary"]["named_unbalance"] is True and report["summary"]["false_alarms"] == 0
        assert report["passed"] is True and code == 0
        assert any("coin" in p for p in prompts) and any("Fix the device" in p for p in prompts)


def test_fan_test_stops_with_a_reason_when_the_app_is_not_there():
    fan_test = load_fan_test()
    with tempfile.TemporaryDirectory() as tmp:
        said = []
        code = fan_test.main(["--url", "http://127.0.0.1:9", "--no-forward", "--press-cmd", "true", "--yes",
                              "--report-dir", tmp], say=said.append)
        report = json.loads(next(Path(tmp).glob("fan-test-*.json")).read_text())
        assert code == 1 and report["passed"] is False and "cannot reach the app" in report["error"]


def test_fan_test_reads_old_and_new_results_lines():
    fan_test = load_fan_test()
    new = {"diagnosis": "imbalance", "diagnosis_detail": "unbalance_like", "wrong": True, "what": ["unbalance_like"]}
    old = {"diagnosis": "unbalance_like", "result": {"diagnosis": "unbalance_like", "wrong": True,
                                                     "what": ["unbalance_like"]}}
    for line in (new, old):
        assert fan_test.is_wrong(line) and fan_test.names_unbalance(line)
        assert fan_test.detail_of(line) == "unbalance_like" and fan_test.what_of(line) == ["unbalance_like"]
    calm = {"diagnosis": "normal", "diagnosis_detail": "normal", "wrong": False, "what": []}
    assert not fan_test.is_wrong(calm) and not fan_test.names_unbalance(calm)
    assert C.check_record({"device_id": "d", "machine_id": "m", "model_version": "v", "at": "2026-11-14T16:02:11Z",
                           **new, "reason": "x"}) == []


if __name__ == "__main__":
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            started = time.perf_counter()
            try:
                fn()
                print(f"ok    {name} ({time.perf_counter() - started:.0f} s)")
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(f"FAIL  {name}: {type(e).__name__}: {e}")
    print("all passed" if not failed else f"{failed} failed")
    sys.exit(1 if failed else 0)
