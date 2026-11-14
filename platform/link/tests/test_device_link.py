"""The device link between a stand-in for the device's port 8765 and a real receiver.

The stand-in answers /latest, /features and /settings with the shapes the board
gave on 13 Nov 2026 (status, in display mode) and the shapes device/intake
writes (results lines). No real device is used here.
"""

import json
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import device_link as dl
import pytest
from receiver_server import Receiver

STATUS = {"device_id": "effente-01", "machine_id": "machine-1", "at": "2026-11-14T03:16:42Z", "mode": "display",
          "model_version": "none", "simulated": False, "wrong": False, "problems": [], "sensitivity": 0.5}
LINE = {"device_id": "effente-01", "machine_id": "machine-1", "model_version": "rec-1-3f2a9c1e",
        "at": "2026-11-14T03:20:00Z", "diagnosis": "unknown", "diagnosis_detail": "unbalance_like", "reason": "",
        "spectrum": [-4.7, -4.6], "simulated": False,
        "result": {"diagnosis": "unbalance_like", "confidence": 0.71, "unknown_score": 2.4,
                   "features": {"vib_1x": 7.2}, "fault_types": {"unbalance_like": 9.1}, "abnormal_minutes": 3}}
TABLE = {"features_version": "89613129193f", "spectrum": {"edges_hz": [5.0, 6.3, 7.9]},
         "features": [{"index": 0, "name": "vib_1x", "text": "vibration once per turn", "unit": "log"}]}


class FakeDevice:
    def __init__(self):
        self.latest = {"results": None, "status": dict(STATUS)}
        self.settings = []
        fake = self

        class H(BaseHTTPRequestHandler):
            def _json(self, obj, status=200):
                body = json.dumps(obj).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/latest":
                    self._json(fake.latest)
                elif self.path == "/features":
                    self._json(TABLE)
                else:
                    self._json({"ok": False}, 404)

            def do_POST(self):
                fake.settings.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                self._json({"ok": True})

            def log_message(self, *args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def device():
    d = FakeDevice()
    yield d
    d.close()


@pytest.fixture
def receiver(tmp_path):
    r = Receiver("127.0.0.1", 0, str(tmp_path / "data"), str(tmp_path / "web"), log=lambda m: None)
    r.start()
    yield r
    r.stop()


@pytest.fixture
def link(device, receiver):
    return dl.DeviceLink(device.url, f"http://127.0.0.1:{receiver.port}", log=lambda m: None)


def state(receiver):
    with urllib.request.urlopen(f"http://127.0.0.1:{receiver.port}/api/state", timeout=5) as resp:
        return json.loads(resp.read())


def test_an_idle_device_checks_in_once_per_status(link, device, receiver):
    link.step()
    link.step()  # the same status again: nothing new to say
    assert link.stats["check_ins"] == 1 and link.stats["refused"] == 0
    seen = state(receiver)["devices"]["effente-01"]
    assert seen["connected"] is True and seen["mode"] == "display"
    device.latest["status"]["at"] = "2026-11-14T03:16:43Z"
    link.step()
    assert link.stats["check_ins"] == 2


def test_problems_take_the_contracts_shape_limits_first():
    got = dl.problems_of([
        {"name": "vibration_above_normal", "what": "vibrating well beyond its normal", "value": 2.3, "since": 12.5},
        {"name": "too_hot", "what": "over 100 C", "value": 104.5, "since": 1794626200.0},
    ], "2026-11-14T03:16:42Z")
    assert got == [
        {"name": "too_hot", "kind": "limit", "text": "over 100 C", "value": 104.5, "since": "2026-11-14T03:16:40Z"},
        {"name": "vibration_above_normal", "kind": "outside_normal", "text": "vibrating well beyond its normal",
         "value": 2.3, "since": "2026-11-14T03:16:42Z"},
    ]
    c = dl.check_in(dict(STATUS, problems=[{"name": "too_hot", "what": "over 100 C", "value": 104.5}]))
    assert c["wrong"] is True and c["problems"][0]["kind"] == "limit" and c["sensitivity"] == 0.5


def test_a_watching_device_announces_its_model_then_sends_records(link, device, receiver):
    device.latest = {"results": dict(LINE), "status": dict(STATUS, mode="watching", model_version="rec-1-3f2a9c1e")}
    link.step()
    link.step()  # the same line again is not sent twice
    assert link.stats == {"check_ins": 0, "records": 1, "models": 1, "refused": 0, "settings": 0}
    model = receiver.store.models["rec-1-3f2a9c1e"]
    assert model["fault_types"] == [] and "threshold" not in model  # not on the device's port
    assert model["features"] == [{"name": "vib_1x", "text": "vibration once per turn", "unit": "log"}]
    assert model["spectrum_edges_hz"] == [5.0, 6.3, 7.9]
    rec = dl.record(LINE, device.latest["status"])
    assert rec["diagnosis"] == "unbalance_like"  # the model's own name, not the line's older "unknown"
    assert rec["wrong"] is True and rec["problems"] == [] and rec["alarm_line"] == 1.0
    assert rec["features"] == {"vib_1x": 7.2} and rec["spectrum"] == [-4.7, -4.6]
    assert state(receiver)["devices"]["effente-01"]["connected"] is True


def test_a_quiet_diagnosis_is_not_wrong():
    line = dict(LINE, result={"diagnosis": "normal", "confidence": 0.9, "unknown_score": 0.4})
    rec = dl.record(line, STATUS)
    assert rec["wrong"] is False and rec["reason"] == ""
    assert dl.record(dict(LINE, result=None), STATUS)["diagnosis"] == "warming_up"


def test_the_dashboards_sensitivity_goes_to_the_device(link, device, receiver):
    receiver.store.put_settings("effente-01", {"sensitivity": 0.7})
    link.step()
    assert device.settings == [{"sensitivity": 0.7}] and link.stats["settings"] == 1
    device.latest["status"].update(at="2026-11-14T03:16:43Z", sensitivity=0.7)
    link.step()
    assert device.settings == [{"sensitivity": 0.7}]  # the device has it now: not sent again


def test_a_device_that_does_not_answer_sends_nothing(receiver):
    link = dl.DeviceLink("http://127.0.0.1:9", f"http://127.0.0.1:{receiver.port}", timeout=0.5, log=lambda m: None)
    link.step()
    assert link.device_reachable is False and link.stats["check_ins"] == 0
    assert state(receiver)["devices"] == {}


def test_the_devices_own_readings_reach_the_dashboard_while_idle(link, device, receiver):
    device.latest["status"]["readings"] = {"vib_mg": 4.5, "sound_db": 82.6, "imu_temp_c": 26.6, "thermal_max_c": None}
    link.step()
    seen = state(receiver)["devices"]["effente-01"]
    assert seen["readings"] == {"at": "2026-11-14T03:16:42Z",
                                "readings": {"vib_mg": 4.5, "sound_db": 82.6, "imu_temp_c": 26.6, "thermal_max_c": None}}
    assert "readings" not in dl.check_in(STATUS)  # a status without them sends none


ROW_TABLE = {"features_version": "v1", "spectrum": {"features": ["vib_band_00", "vib_band_01"], "edges_hz": [5.0, 6.3, 7.9]},
             "features": [{"index": 0, "name": "vib_band_00"}, {"index": 1, "name": "vib_band_01"},
                          {"index": 2, "name": "vib_velocity_mm_s"}, {"index": 3, "name": "imu_temp_c"},
                          {"index": 4, "name": "th_max_c"}, {"index": 5, "name": "snd_tone_hz"}]}
ROW = {"features_version": "v1", "values": [-5.1234, -4.9, 0.41, 26.604, None, 186.5]}


def test_an_idle_check_in_carries_the_spectrum_heat_and_velocity_of_the_newest_row():
    status = dict(STATUS, readings={"vib_mg": 4.5, "sound_db": 82.6})
    c = dl.check_in(status, ROW, ROW_TABLE)
    assert c["spectrum"] == [-5.123, -4.9] and c["spectrum_edges_hz"] == [5.0, 6.3, 7.9]
    assert c["heat"] == {"source": "imu", "imu_temp_c": 26.6}
    assert c["readings"] == {"vib_mg": 4.5, "sound_db": 82.6, "vib_velocity_mm_s": 0.41, "snd_tone_hz": 186.5}
    assert status["readings"] == {"vib_mg": 4.5, "sound_db": 82.6}  # the device's status is not changed


def test_a_row_that_cannot_be_trusted_adds_nothing():
    status = dict(STATUS, readings={"vib_mg": 4.5})
    assert "spectrum" not in dl.check_in(status, dict(ROW, values=[None, -4.9, 0.41, 26.6, None, 186.5]), ROW_TABLE)  # a band missing
    assert dl.row_extras(dict(ROW, features_version="v2"), ROW_TABLE) == {}  # names for another version
    assert dl.row_extras(None, ROW_TABLE) == {} and dl.row_extras(ROW, None) == {}
    assert "heat" not in dl.row_extras(dict(ROW, values=[-5.1, -4.9, 0.41, 26.6, 61.2, 186.5]), ROW_TABLE)  # a camera reading: not ours to shape
    stopped = dl.check_in(dict(STATUS, readings={"vib_mg": None}), ROW, ROW_TABLE)  # no samples: the row is old
    assert "spectrum" not in stopped and "heat" not in stopped


def test_the_platform_keeps_an_idle_devices_spectrum_and_heat(link, device, receiver):
    device.latest["status"]["readings"] = {"vib_mg": 4.5}
    device.latest["features"] = dict(ROW, features_version=TABLE["features_version"])
    link._table = dict(ROW_TABLE, features_version=TABLE["features_version"])
    link.step()
    kept = state(receiver)["devices"]["effente-01"]["readings"]
    assert kept["spectrum"] == [-5.123, -4.9] and kept["heat"]["source"] == "imu"
    assert kept["readings"]["vib_velocity_mm_s"] == 0.41
