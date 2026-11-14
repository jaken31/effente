"""The microphone's readings on the Linux side: the sketch's "snd" notifications (device.intake.source
UnoQSource.on_sound) and how a row gets them (device.intake.extract.feature_stream).

The ESP32 and the UNO Q's sketch are not run here; the sound maths itself is in test_mic_features.py.
Run with pytest, or without it:  python3 -m device.intake.tests.test_mic
"""

import numpy as np

from effente import features as F
from device.intake.extract import feature_stream
from device.intake.source import UnoQSource

SENT = UnoQSource.SOUND_SENT
SND = F.SLICES["sound"]


class Clock:
    def __init__(self, t=1_700_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def readings(level=62.5):
    v = np.arange(len(SENT), dtype="<f4") + 100.0
    v[SENT.index("snd_level_db")] = level
    v[SENT.index("snd_env_peak_hz")] = 50.0
    return v


def imu_batch(src, clock, first, n=25, hz=25.0):
    """One batch as the sketch sends it: 1 g on z with a small wobble, stamped with the board's micros()."""
    i = first + np.arange(n)
    a = np.zeros((n, 3))
    a[:, 0] = 0.05 * np.sin(2 * np.pi * hz * i / 1000.0)
    a[:, 2] = 1.0
    acc = np.round(a * 4096).astype(">i2").tobytes()
    clock.t = 1_700_000_000.0 + (first + n) / 1000.0
    src.on_imu(first, int((first + n) * 1000), n, acc, b"", 0, 0)


def run(seconds, sound_every=None, sound_from=0.0, sound_until=1e9):
    """A source fed `seconds` of IMU batches, and a set of sound readings every `sound_every` seconds."""
    clock = Clock()
    src = UnoQSource(clock=clock)
    seq, next_sound = 0, sound_from
    for first in range(0, int(seconds * 1000), 25):
        imu_batch(src, clock, first)
        now_s = (first + 25) / 1000.0
        if sound_every and sound_from <= now_s < sound_until and now_s >= next_sound:
            # the window ended 150 ms before the ESP32 began to send; the frame then took its time on the line
            src.on_sound(int(now_s * 1e6), 150, seq, readings(60.0 + seq).tobytes())
            seq += 1
            next_sound += sound_every
    src.close()
    return src, list(feature_stream(src))


def test_a_row_carries_the_esp32s_readings():
    src, rows = run(4.0, sound_every=0.256)
    assert src.stats["sound_frames"] >= 14 and src.stats["sound_lost"] == 0 and src.stats["sound_bad"] == 0
    t0, x = rows[-1]
    assert "sound" not in F.missing_sensors(x) and "vibration" not in F.missing_sensors(x)
    snd = dict(zip(UnoQSource.SOUND_NAMES, x[SND]))
    assert snd["snd_band_00"] == 101.0 and snd["snd_clip_frac"] == 100.0 + len(SENT) - 1  # each in its own place
    # the set whose window ended nearest the row's: levels were 60, 61, 62, ... one every 0.256 s
    ends = 1_700_000_000.0 + 0.256 * np.arange(src.stats["sound_frames"]) + 0.275 - 0.150 - UnoQSource.SOUND_FRAME_S
    nearest = int(np.argmin(np.abs(ends - (t0 + 1.0))))
    assert abs(snd["snd_level_db"] - (60.0 + nearest)) <= 1.0
    # snd_env_order is the Linux side's: the ESP32's envelope rate over the speed from vibration
    speed = x[F.INDEX["vib_speed_hz"]]
    assert speed > 0 and abs(snd["snd_env_order"] - 50.0 / speed) < 1e-9


def test_without_the_esp32_rows_come_all_the_same_with_no_sound():
    _, with_mic = run(4.0, sound_every=0.256)
    src, rows = run(4.0)
    assert len(rows) == len(with_mic) > 8
    assert all("sound" in F.missing_sensors(x) for _, x in rows)
    assert src.stats["sound_frames"] == 0


def test_sound_goes_missing_when_the_esp32_stops():
    _, rows = run(6.0, sound_every=0.256, sound_until=2.0)
    late = [x for t0, x in rows if t0 - 1_700_000_000.0 > 3.5]
    early = [x for t0, x in rows if 1.0 < t0 - 1_700_000_000.0 < 1.4]
    assert early and all("sound" not in F.missing_sensors(x) for x in early)
    assert late and all("sound" in F.missing_sensors(x) for x in late)  # old readings are never reused


def test_bad_payloads_and_lost_frames_are_counted():
    clock = Clock()
    src = UnoQSource(clock=clock)
    src.on_sound(1_000_000, 100, 7, readings().tobytes())
    src.on_sound(1_256_000, 100, 8, readings().tobytes()[:-4])  # too short
    src.on_sound(1_512_000, 100, 11, readings().tobytes())      # 8 was bad, 9 and 10 never came
    assert src.stats["sound_frames"] == 2 and src.stats["sound_bad"] == 1 and src.stats["sound_lost"] == 3
    src.on_sound(1_768_000, 100, 0, readings().tobytes())       # the ESP32 started again: not a loss
    assert src.stats["sound_lost"] == 3
    assert src.sound_features_at(clock.t) is not None  # before any IMU batch, a set is timed by its arrival


def test_a_window_with_raw_audio_ignores_ready_made_readings():
    rng = np.random.default_rng(0)
    w = F.Window(t0=0.0, audio=rng.normal(0, 500, 16000).astype(np.int32), sound_features=np.zeros(46))
    assert F.extract(w)[F.INDEX["snd_level_db"]] > 10.0
    w = F.Window(t0=0.0, sound_features=np.zeros(45))  # the wrong length is not used at all
    assert np.isnan(F.extract(w)[SND]).all()


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
