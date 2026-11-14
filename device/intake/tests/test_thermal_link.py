"""The thermal camera's frames on the Linux side: the WiFi link to the ESP32 (device/intake/thermal_link.py),
what UnoQSource keeps of them, and how a row gets its heat readings.

A fake ESP32 (a TCP server on this computer) stands in for the real one; the camera itself is not here.
Run with pytest, or without it:  python3 -m device.intake.tests.test_thermal_link
"""

import socket
import threading
import time

import numpy as np

from effente import features as F
from device.intake.extract import feature_stream
from device.intake.source import UnoQSource
from device.intake.tests.test_mic import Clock, imu_batch
from device.intake.thermal_link import ThermalLink, pack_frame


def scene(hot=45.0, at=(10, 20)):
    """A 24 x 32 frame at 22 deg C with one warm patch."""
    f = np.full((24, 32), 22.0)
    f[at[0]:at[0] + 3, at[1]:at[1] + 3] = hot
    return f


class FakeEsp32(threading.Thread):
    """Accepts one client at a time and sends it whatever is put in `outbox`."""

    def __init__(self):
        super().__init__(daemon=True)
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.sock.settimeout(0.2)
        self.port = self.sock.getsockname()[1]
        self.outbox, self.stop, self.clients = [], False, 0

    def run(self):
        while not self.stop:
            try:
                c, _ = self.sock.accept()
            except socket.timeout:
                continue
            self.clients += 1
            try:
                while not self.stop:
                    while self.outbox:
                        c.sendall(self.outbox.pop(0))
                    time.sleep(0.01)
                    if self.drop:
                        self.drop = False
                        break
            except OSError:
                pass
            finally:
                c.close()

    drop = False


def wait(cond, seconds=5.0):
    end = time.time() + seconds
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_frames_arrive_whole_and_in_degrees():
    esp, got = FakeEsp32(), []
    esp.start()
    link = ThermalLink(got.append, host="127.0.0.1", port=esp.port).start()
    try:
        a, b = pack_frame(1, 500, scene(45.0)), pack_frame(2, 1000, scene(46.5))
        esp.outbox += [b"boot noise", a[:700]]  # other bytes first, and a frame cut in two
        time.sleep(0.1)
        esp.outbox += [a[700:] + b]
        assert wait(lambda: len(got) == 2)
        assert got[0].shape == (24, 32) and got[0].dtype == np.float32
        assert abs(got[0].max() - 45.0) < 0.01 and abs(got[1].max() - 46.5) < 0.01 and abs(got[0].min() - 22.0) < 0.01
        assert link.stats["frames"] == 2 and link.stats["skipped_bytes"] == len(b"boot noise")
    finally:
        link.close()
        esp.stop = True


def test_the_link_comes_back_after_the_esp32_goes_away():
    esp, got = FakeEsp32(), []
    esp.start()
    link = ThermalLink(got.append, host=lambda: "127.0.0.1", port=esp.port).start()
    try:
        esp.outbox.append(pack_frame(1, 0, scene()))
        assert wait(lambda: len(got) == 1)
        esp.drop = True  # the ESP32 hangs up
        assert wait(lambda: not link.stats["connected"])
        assert wait(lambda: esp.clients == 2, 8.0)  # the link connected again by itself
        esp.outbox.append(pack_frame(1, 0, scene()))
        assert wait(lambda: len(got) == 2)
    finally:
        link.close()
        esp.stop = True


def test_no_address_yet_means_no_connection():
    link = ThermalLink(lambda f: None, host=lambda: None).start()
    time.sleep(0.3)
    assert link.stats["connections"] == 0 and not link.stats["connected"]
    link.close()


def rows_with_frames(seconds, frames_until=1e9, every_s=0.5, bad_pixel=False):
    clock = Clock()
    src = UnoQSource(clock=clock)
    next_frame = 0.5
    for first in range(0, int(seconds * 1000), 25):
        imu_batch(src, clock, first)
        now_s = (first + 25) / 1000.0
        if now_s >= next_frame and now_s < frames_until:
            f = scene(45.0 + now_s)  # the patch warms a degree a second
            if bad_pixel:
                f[0, 0] = -273.15
            src.on_thermal_frame(f)
            next_frame += every_s
    src.close()
    return src, list(feature_stream(src))


def test_a_row_carries_the_cameras_heat_readings():
    src, rows = rows_with_frames(5.0)
    assert src.stats["heat_frames"] >= 8
    t0, x = rows[-1]
    assert "heat" not in F.missing_sensors(x)
    assert 46.0 < x[F.INDEX["th_max_c"]] < 51.0 and abs(x[F.INDEX["th_background_c"]] - 22.0) < 0.01
    assert x[F.INDEX["th_hot_row"]] in (10.0, 11.0, 12.0) and x[F.INDEX["th_hot_col"]] in (20.0, 21.0, 22.0)
    assert x[F.INDEX["th_hot_pixels"]] == 9.0
    assert 0.2 < x[F.INDEX["th_change_c"]] < 1.5  # two frames in the row's second, half a degree apart
    assert x[F.INDEX["th_frozen"]] == 0.0


def test_one_dead_pixel_is_not_a_cold_spot():
    src, rows = rows_with_frames(4.0, bad_pixel=True)
    _, x = rows[-1]
    assert src.stats["heat_bad_pixels"] >= 1
    assert abs(x[F.INDEX["th_cold_spot_c"]]) < 0.01  # the dead pixel took the frame's median


def test_heat_goes_missing_when_the_camera_stops_and_rows_never_wait():
    _, with_camera = rows_with_frames(8.0, frames_until=2.0)
    _, without = rows_with_frames(8.0, frames_until=0.0)
    assert len(with_camera) == len(without)
    late = [x for t0, x in with_camera if t0 - 1_700_000_000.0 > 6.0]
    assert late and all("heat" in F.missing_sensors(x) or np.isnan(x[F.INDEX["th_max_c"]]) for x in late)
    assert all(np.isnan(x[F.INDEX["th_max_c"]]) for _, x in without)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
