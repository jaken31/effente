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
    stopped = dl.check_in(dict(STATUS, readings={"vib_mg": None}), ROW, ROW_TABLE)  # no samples: the row is old
    assert "spectrum" not in stopped and "heat" not in stopped


def camera_row(**changed):
    """A feature table and row with the thermal camera's readings: block RC averages 0.RC above the
    background and its hottest point 1.RC."""
    row = {"imu_temp_c": 26.604, "th_max_c": 61.234, "th_hot_row": 9.0, "th_hot_col": 21.0, "th_background_c": 25.0,
           "th_frozen": 0.0, "trend_scene_match": 0.931}
    for b in dl.HEAT_BLOCKS:
        row["th_cell_" + b], row["th_spot_" + b] = int(b) / 100, 1 + int(b) / 100
    row.update(changed)
    table = {"features_version": "v1", "features": [{"index": i, "name": n} for i, n in enumerate(row)]}
    return {"features_version": "v1", "values": list(row.values())}, table


def test_a_row_with_the_thermal_camera_gives_the_contracts_camera_heat():
    heat = dl.row_extras(*camera_row())["heat"]
    assert {k: v for k, v in heat.items() if k not in ("cells", "spots")} == {
        "source": "camera", "imu_temp_c": 26.6, "max_c": 61.23, "hot_row": 9, "hot_col": 21, "background_c": 25.0,
        "frozen": False, "scene_match": 0.93}
    assert len(heat["cells"]) == len(heat["spots"]) == 48
    assert heat["cells"][:9] == [0.0, 0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.1]  # row 0, then row 1
    assert heat["spots"][-1] == 1.57
    assert "frames" not in heat  # the device's port serves no images


def test_camera_heat_says_null_for_what_the_row_lacks():
    heat = dl.row_extras(*camera_row(th_cell_23=None, th_hot_row=24.0, trend_scene_match=None, th_frozen=1.0))["heat"]
    assert heat["cells"] is None and len(heat["spots"]) == 48  # 47 blocks are not a grid
    assert heat["hot_row"] is None and heat["hot_col"] == 21  # there is no row 24
    assert heat["scene_match"] is None and heat["frozen"] is True
    assert dl.row_extras(*camera_row(th_max_c=None))["heat"] == {"source": "imu", "imu_temp_c": 26.6}
    assert "heat" not in dl.row_extras(*camera_row(th_max_c=None, imu_temp_c=None))


def test_a_record_carries_the_heat_of_the_newest_row():
    line = {"device_id": "effente-01", "machine_id": "machine-1", "model_version": "m1", "at": "2026-11-14T16:02:11Z"}
    status = dict(STATUS, readings={"vib_mg": 4.5})
    row, table = camera_row()
    assert dl.record(line, status, row, table)["heat"]["source"] == "camera"
    assert dl.record(line, status, ROW, ROW_TABLE)["heat"] == {"source": "imu", "imu_temp_c": 26.6}
    assert "heat" not in dl.record(line, status)  # no row
    assert "heat" not in dl.record(line, dict(STATUS, readings={"vib_mg": None}), row, table)  # an old row


def test_the_platform_keeps_an_idle_devices_spectrum_and_heat(link, device, receiver):
    device.latest["status"]["readings"] = {"vib_mg": 4.5}
    device.latest["features"] = dict(ROW, features_version=TABLE["features_version"])
    link._table = dict(ROW_TABLE, features_version=TABLE["features_version"])
    link.step()
    kept = state(receiver)["devices"]["effente-01"]["readings"]
    assert kept["spectrum"] == [-5.123, -4.9] and kept["heat"]["source"] == "imu"
    assert kept["readings"]["vib_velocity_mm_s"] == 0.41


def test_every_record_carries_the_velocity_of_the_newest_row():
    """Contract 9.2: readings.vib_velocity_mm_s on every record, null when the link cannot know it."""
    status = dict(STATUS, mode="watching", readings={"vib_mg": 4.5, "sound_db": 82.6})
    rec = dl.record(LINE, status, ROW, ROW_TABLE)
    assert rec["readings"] == {"vib_mg": 4.5, "sound_db": 82.6, "vib_velocity_mm_s": 0.41}  # only the asked-for key added
    assert status["readings"] == {"vib_mg": 4.5, "sound_db": 82.6}  # the device's status is not changed
    for result in ({"diagnosis": "normal"}, {"diagnosis": "stopped"}, None):  # whatever the diagnosis
        assert dl.record(dict(LINE, result=result), status, ROW, ROW_TABLE)["readings"]["vib_velocity_mm_s"] == 0.41
    unknown = [dl.record(LINE, STATUS),  # no screen readings, no row
               dl.record(LINE, dict(status, readings={"vib_mg": None}), ROW, ROW_TABLE),  # no samples: the row is old
               dl.record(LINE, status, dict(ROW, features_version="v2"), ROW_TABLE),  # names for another version
               dl.record(LINE, status, dict(ROW, values=[-5.1, -4.9, None, 26.6, None, 186.5]), ROW_TABLE)]  # sensor gave none
    assert [r["readings"]["vib_velocity_mm_s"] for r in unknown] == [None] * 4


def test_a_watching_devices_records_reach_the_platform_with_their_velocity(link, device, receiver):
    link._table = dict(ROW_TABLE, features_version=TABLE["features_version"])
    device.latest = {"results": dict(LINE), "features": dict(ROW, features_version=TABLE["features_version"]),
                     "status": dict(STATUS, mode="watching", model_version="rec-1-3f2a9c1e", readings={"vib_mg": 4.5})}
    link.step()
    assert link.stats["records"] == 1
    assert receiver.store.records[-1]["readings"]["vib_velocity_mm_s"] == 0.41


def frame(at):
    return {"at": at, "min_c": 20.0, "step_c": 0.015, "data": "AAAA", "more": "not sent on"}


def test_the_cameras_images_go_out_once_each_at_most_two_a_message():
    link = dl.DeviceLink("http://device", "http://platform", log=lambda m: None)
    served = {"frames": [frame("2026-11-15T00:03:02.584Z"), frame("2026-11-15T00:03:02.858Z")]}
    asked = []
    link._get = lambda url: asked.append(url) or served
    message = {"heat": {"source": "camera", "max_c": 23.4}}
    newest = link._add_frames(message)
    assert asked == ["http://device/heat"] and newest == "2026-11-15T00:03:02.858Z"
    assert message["heat"]["frames"] == [{"at": f["at"], "min_c": 20.0, "step_c": 0.015, "data": "AAAA"} for f in served["frames"]]
    again = {"heat": {"source": "camera"}}
    assert link._add_frames(again) == newest and len(again["heat"]["frames"]) == 2  # not taken yet: sent again
    link._frame_at = newest  # it took it
    served["frames"] = [served["frames"][1], frame("2026-11-15T00:03:03.120Z"), frame("2026-11-15T00:03:03.400Z"),
                        frame("2026-11-15T00:03:03.700Z"), {"at": "2026-11-15T00:03:04Z"}]  # the last has no image
    third = {"heat": {"source": "camera"}}
    assert link._add_frames(third) == "2026-11-15T00:03:03.700Z"
    assert [f["at"] for f in third["heat"]["frames"]] == ["2026-11-15T00:03:03.400Z", "2026-11-15T00:03:03.700Z"]
    link._frame_at = "2026-11-15T00:03:03.700Z"
    nothing_new = {"heat": {"source": "camera"}}
    link._add_frames(nothing_new)
    assert "frames" not in nothing_new["heat"]


def test_images_are_asked_for_only_when_the_camera_gave_the_heat_and_the_device_has_them():
    link = dl.DeviceLink("http://device", "http://platform", log=lambda m: None)
    asked = []
    link._get = lambda url: asked.append(url)  # None: the device's port has no /heat
    for message in ({}, {"heat": None}, {"heat": {"source": "imu", "imu_temp_c": 26.6}}):
        link._add_frames(message)
    assert asked == []
    message = {"heat": {"source": "camera"}}
    link._add_frames(message)
    link._add_frames(message)
    assert asked == ["http://device/heat"] and "frames" not in message["heat"]  # an older device is not asked every time


def test_each_image_is_handed_on_as_soon_as_the_device_serves_it():
    got = []
    link = dl.DeviceLink("http://device", "http://platform", log=lambda m: None, on_heat=lambda m, f: got.append((m, f)))
    served = {"frames": [frame("2026-11-15T00:03:02.584Z"), frame("2026-11-15T00:03:02.858Z")]}
    asked = []
    link._get = lambda url: asked.append(url) or served
    link.heat_step()
    assert asked == [] and got == []  # not before the device's status has named its machine
    link.machine_id = "conveyor-07"
    link.heat_step()
    link.heat_step()  # nothing new: nothing handed on
    assert asked == ["http://device/heat"] * 2 and len(got) == 1
    assert got[0] == ("conveyor-07", [{"at": f["at"], "min_c": 20.0, "step_c": 0.015, "data": "AAAA"} for f in served["frames"]])
    served["frames"] = [served["frames"][1], frame("2026-11-15T00:03:03.120Z")]
    link.heat_step()
    assert [f["at"] for f in got[1][1]] == ["2026-11-15T00:03:03.120Z"]
    assert link._frame_at == ""  # what the platform is sent is counted apart


def test_images_come_again_after_the_device_starts_its_clock_again():
    """A restart with no network time: every image is then older than the newest one seen before it."""
    got = []
    link = dl.DeviceLink("http://device", "http://platform", log=lambda m: None, on_heat=lambda m, f: got.append(f))
    link.machine_id = "conveyor-07"
    served = {"frames": [frame("2026-11-15T00:03:02.584Z")]}
    link._get = lambda url: served
    link.heat_step()
    served["frames"] = [frame("1970-01-01T00:00:41.100Z"), frame("1970-01-01T00:00:41.600Z")]
    link.heat_step()
    link.heat_step()  # nothing new on the new clock: nothing handed on
    assert [[f["at"] for f in images] for images in got] == [
        ["2026-11-15T00:03:02.584Z"], ["1970-01-01T00:00:41.100Z", "1970-01-01T00:00:41.600Z"]]
    served["frames"].append(frame("1970-01-01T00:00:42.100Z"))
    link.heat_step()
    assert [f["at"] for f in got[-1]] == ["1970-01-01T00:00:42.100Z"]

    link._frame_at = "2026-11-15T00:03:02.584Z"  # the platform's copies, counted apart, start again too
    message = {"heat": {"source": "camera"}}
    assert link._add_frames(message) == "1970-01-01T00:00:42.100Z"
    assert [f["at"] for f in message["heat"]["frames"]] == ["1970-01-01T00:00:41.600Z", "1970-01-01T00:00:42.100Z"]


def test_a_device_with_no_image_is_not_asked_four_times_a_second():
    link = dl.DeviceLink("http://device", "http://platform", log=lambda m: None, on_heat=lambda m, f: None)
    link.machine_id = "conveyor-07"
    asked = []
    for answer in (None, {"ok": True, "frames": []}):  # no /heat on its port; no camera or only old images
        link._heat_wait_until = 0.0
        link._get = lambda url, answer=answer: asked.append(url) and answer
        link.heat_step()
        link.heat_step()
    assert asked == ["http://device/heat"] * 2


def test_an_answer_with_no_image_is_asked_again_sooner_than_no_answer(monkeypatch):
    """A real board's images come unevenly (16 Nov 2026: 1 to 13 s apart): the next one is not waited 5 s for."""
    link = dl.DeviceLink("http://device", "http://platform", log=lambda m: None, on_heat=lambda m, f: None)
    link.machine_id = "conveyor-07"
    monkeypatch.setattr(dl.time, "monotonic", lambda: 100.0)
    link._get = lambda url: {"ok": True, "frames": []}
    link.heat_step()
    assert link._heat_wait_until == 100.0 + dl.HEAT_EMPTY_RETRY_S
    link._heat_wait_until = 0.0
    link._get = lambda url: None
    link.heat_step()
    assert link._heat_wait_until == 100.0 + dl.HEAT_RETRY_S and dl.HEAT_EMPTY_RETRY_S < dl.HEAT_RETRY_S
