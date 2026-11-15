"""The thermal camera on the UNO Q's own bus (device/intake/mlx90640.py): the calibration maths, and the
parts the sketch sends put back together.

No camera and no board here. The calibration memory below is made up around typical values, and the
expected numbers are what this code gives for it: they guard against a change, they do not show the
maths is the maker's (see the module's note: unverified against the maker's code).
"""

import numpy as np

from device.intake import mlx90640 as M

CHESS, INTERLEAVED = 0x1901, 0x0901


def memory(pixel_word=0x08A0):
    ee = np.zeros(M.EE_WORDS, np.int64)
    for at, word in {16: 0x4210, 17: 0xFFBB, 32: 0x79A6, 33: 0x2363, 48: 6383, 49: 12273, 50: 0x5952, 51: 0x9D68,
                     52: 0x5454, 53: 0x0994, 54: 0x6956, 55: 0x5354, 56: 0x2363, 57: 0xE446, 58: 0xFBB5, 59: 0x044B,
                     60: 0xF020, 61: 0x9797, 62: 0x9797, 63: 0x2889}.items():
        ee[at] = word
    ee[64:] = pixel_word
    return ee


def aux(control=CHESS, **changed):
    return dict({"ta_vbe": 19442, "cp0": -55, "gain": 6383, "ta_ptat": 1711, "cp1": -56, "vdd": -13115,
                 "control": control}, **changed)


def words(values):
    return np.asarray(values).astype(">i2").tobytes()


def send_memory(camera, ee, epoch=1):
    for first in range(0, M.EE_WORDS, 96):
        camera.on_ee(epoch, first, np.asarray(ee[first:first + 96]).astype(">u2").tobytes())


def send_half_frame(camera, frame, subpage, raw, control=CHESS, skip=(), **changed):
    """As the sketch sends it: the subpage's 384 pixels in four parts, then the seven other words."""
    mine = np.asarray(raw)[M.subpage_pixels(subpage, bool(control & 0x1000))]
    a = aux(control, **changed)
    parts = [mine[i * 96:(i + 1) * 96] for i in range(4)]
    parts.append([a[k] if k != "control" else a[k] - 65536 * (a[k] > 32767)
                  for k in ("ta_vbe", "cp0", "gain", "ta_ptat", "cp1", "vdd", "control")])
    for i, part in enumerate(parts):
        if i not in skip:
            camera.on_part(frame, i, subpage, words(part))


def test_supply_and_camera_temperature_from_the_frames_own_readings():
    cal = M.Calibration(memory())
    assert (cal.k_vdd, cal.vdd25) == (-3168, -13056)
    assert abs(cal.supply_v(aux()) - 3.3186) < 1e-4
    assert abs(cal.camera_c(aux()) - 39.184) < 1e-3


def test_a_warmer_reading_is_a_warmer_pixel_and_each_half_covers_its_own_pixels():
    cal = M.Calibration(memory())
    cool, warm = cal.to_c(np.full(768, 300), aux(), 0), cal.to_c(np.full(768, 600), aux(), 0)
    have = ~np.isnan(cool)
    assert have.sum() == 384 and (have == ~np.isnan(warm)).all()
    assert (warm[have] > cool[have] + 10).all()
    row, col = np.divmod(np.arange(768), 32)
    assert (have == ((row + col) % 2 == 0)).all()  # chess: the sketch's rule for subpage 0
    other = ~np.isnan(cal.to_c(np.full(768, 300), aux(), 1))
    assert (other == ~have).all()
    lines = ~np.isnan(cal.to_c(np.full(768, 300), aux(INTERLEAVED), 1))
    assert (lines == (row % 2 == 1)).all()  # interleaved: whole lines
    assert list(M.subpage_pixels(0, True)[:3]) == [0, 2, 4] and list(M.subpage_pixels(1, False)[:2]) == [32, 33]


def test_a_reading_with_nothing_but_the_cameras_own_warmth_reads_near_the_camera():
    """A pixel whose reading is its own offset (no radiation difference seen) is at about the reflected
    temperature the maths assumes: the camera's, less 8 degrees C."""
    cal = M.Calibration(memory())
    a = aux(cp0=int(cal.cp_offset[0]), cp1=int(cal.cp_offset[1]))
    drift = (1 + cal.kta * (cal.camera_c(a) - 25)) * (1 + cal.kv * (cal.supply_v(a) - 3.3))
    t = cal.to_c(cal.offset * drift, a, 0)
    assert np.nanmax(np.abs(t - cal.camera_c(a))) < 3.0


def test_marked_pixels_are_known():
    ee = memory()
    ee[64 + 5], ee[64 + 700] = 0, 0x08A1
    cal = M.Calibration(ee)
    assert list(cal.broken) == [5] and list(cal.deviating) == [700] and cal.bad.sum() == 2


def test_the_sketchs_parts_become_an_image_once_both_halves_are_in():
    got = []
    camera = M.Camera(got.append)
    raw = np.full(768, 300)
    raw[10 * 32 + 20] = raw[10 * 32 + 21] = 900  # two neighbours: one in each half
    send_half_frame(camera, 1, 0, raw)
    assert not got and camera.stats["half_frames"] == 1  # no calibration yet
    ee = memory()
    ee[64 + 3] = 0
    send_memory(camera, ee)
    assert camera.stats["calibrated"] and camera.stats["bad_pixels"] == 1
    send_half_frame(camera, 2, 0, raw)
    assert not got  # half the pixels only
    send_half_frame(camera, 3, 1, raw)
    assert len(got) == 1 and got[0].shape == (24, 32) and got[0].dtype == np.float32
    image = got[0]
    assert np.isnan(image[0, 3]) and np.isnan(image).sum() == 1  # the broken pixel, and no other
    assert image[10, 20] > image[0, 0] + 20 and image[10, 21] > image[0, 0] + 20
    assert camera.stats["images"] == 1 and abs(camera.stats["camera_c"] - 39.18) < 0.01
    send_half_frame(camera, 4, 0, np.full(768, 300))  # every half-frame after that gives an image
    assert len(got) == 2 and got[1][10, 20] < got[1][10, 21] - 20  # the newer half has cooled


def test_what_cannot_be_trusted_gives_no_image():
    got = []
    camera = M.Camera(got.append)
    send_memory(camera, memory())
    raw = np.full(768, 300)
    send_half_frame(camera, 1, 0, raw)
    send_half_frame(camera, 2, 1, raw, skip=(2,))  # a part lost on the way
    send_half_frame(camera, 3, 1, raw, vdd=0)  # a supply voltage no camera has
    assert not got and camera.stats["incomplete"] == 1 and camera.stats["refused"] == 1
    send_half_frame(camera, 4, 1, raw)
    assert len(got) == 1
    send_memory(camera, memory(), epoch=2)  # the sketch read the memory again: both halves are wanted anew
    assert camera.stats["ee_epoch"] == 2 and camera.stats["calibrated"]
    send_half_frame(camera, 5, 0, raw)
    assert len(got) == 1
    send_half_frame(camera, 6, 1, raw)
    assert len(got) == 2


def test_the_calibration_waits_for_every_word():
    camera = M.Camera(lambda image: None)
    ee = memory()
    for first in range(0, M.EE_WORDS - 96, 96):
        camera.on_ee(1, first, np.asarray(ee[first:first + 96]).astype(">u2").tobytes())
    assert not camera.stats["calibrated"]
    camera.on_ee(1, 900, b"\x00\x00")  # outside the memory: ignored
    camera.on_ee(1, 768, np.asarray(ee[768:]).astype(">u2").tobytes())
    assert camera.stats["calibrated"]


def test_the_devices_port_gives_the_newest_images_whole():
    """UnoQSource.heat_images and GET /heat: the contract's image form, every one of the 768 pixels."""
    import base64
    import json
    import urllib.request

    from device.intake.source import UnoQSource
    from device.intake.stream import Stream

    now = [1_794_700_000.25]
    source = UnoQSource(clock=lambda: now[0])
    assert source.heat_images() == []
    for k in range(3):
        image = np.full((24, 32), 22.0, np.float32)
        image[5, 7 + k] = 61.2
        source.on_thermal_frame(image)
        now[0] += 0.5
    frames = source.heat_images(2)
    assert [f["at"] for f in frames] == ["2026-11-14T23:46:40.750Z", "2026-11-14T23:46:41.250Z"]  # the newest two
    last = frames[-1]
    pixels = last["min_c"] + np.frombuffer(base64.b64decode(last["data"]), np.uint8).reshape(24, 32) * last["step_c"]
    assert pixels.shape == (24, 32) and abs(pixels[5, 9] - 61.2) <= last["step_c"] and abs(pixels[0, 0] - 22.0) <= last["step_c"]
    assert np.argmax(pixels) == 5 * 32 + 9

    stream = Stream(host="127.0.0.1", port=0).start()
    try:
        url = f"http://127.0.0.1:{stream.port}/heat"
        assert json.load(urllib.request.urlopen(url, timeout=3))["frames"] == []  # nothing gives images yet
        stream.heat_images = source.heat_images
        got = json.load(urllib.request.urlopen(url, timeout=3))
        assert got["ok"] and (got["rows"], got["cols"]) == (24, 32) and got["frames"] == frames
    finally:
        stream.stop()
    now[0] += 10
    assert source.heat_images() == []  # too old to be now
