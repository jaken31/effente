"""The ESP32's sound readings (firmware/esp32_mic/sound_features.h) against the Python they copy
(effente.features._sound): the C++ is compiled here, on this computer, and fed the same samples.

Needs a C++ compiler (c++, g++ or clang++); skipped without one.
Run with pytest, or without it:  python3 -m device.intake.tests.test_mic_features
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

from effente import features as F

HEADER_DIR = Path(__file__).resolve().parents[3] / "firmware" / "esp32_mic"
N = 16384  # SND_N
FULL = 8388607.0
NAMES = [n for n in F.FEATURE_NAMES if n.startswith("snd_")]
SENT = [n for n in NAMES if n != "snd_env_order"]

MAIN = r"""
#include <stdio.h>
#include <vector>
#include "sound_features.h"
// stdin: a ring length (uint32), a start index (uint32), then the ring (int16). stdout: SND_SENT floats.
int main() {
  uint32_t head[2];
  if (fread(head, 4, 2, stdin) != 2) return 2;
  std::vector<int16_t> ring(head[0]);
  if (fread(ring.data(), 2, head[0], stdin) != head[0]) return 3;
  static SoundFeatures sf;
  if (!sf.begin()) return 4;
  float out[SND_SENT];
  sf.compute(ring.data(), head[0], head[1], out);
  fwrite(out, 4, SND_SENT, stdout);
  return 0;
}
"""

_built = {}


def binary():
    """The compiled test program, or None without a compiler."""
    if "path" not in _built:
        cxx = next((c for c in ("c++", "g++", "clang++") if shutil.which(c)), None)
        if cxx is None:
            _built["path"] = None
        else:
            tmp = Path(tempfile.mkdtemp(prefix="effente-mic-"))
            (tmp / "main.cpp").write_text(MAIN)
            exe = tmp / ("snd.exe" if sys.platform == "win32" else "snd")
            subprocess.run([cxx, "-O2", "-std=c++11", "-I", str(HEADER_DIR), str(tmp / "main.cpp"), "-o", str(exe)],
                           check=True)
            _built["path"] = exe
    return _built["path"]


def need_compiler():
    if binary() is None:
        try:
            import pytest
        except ImportError:
            return False
        pytest.skip("no C++ compiler on this computer")
    return True


def esp32(samples16, ring_len=N + 4096, start=1234):
    """The C++ readings of 16-bit samples, laid into a ring the way the ESP32 holds them (wrapping)."""
    ring = np.zeros(ring_len, np.int16)
    ring[(start + np.arange(N)) % ring_len] = samples16
    data = np.array([ring_len, start], "<u4").tobytes() + ring.astype("<i2").tobytes()
    out = subprocess.run([str(binary())], input=data, capture_output=True, check=True).stdout
    return dict(zip(SENT, np.frombuffer(out, "<f4").astype(float)))


def python(samples16):
    """effente.features._sound on the same samples, as 24-bit counts."""
    return dict(zip(NAMES, F._sound(samples16.astype(np.int64) * 256, 16000.0, 0.0, FULL)))


def to16(x):
    return np.clip(np.round(x), -32768, 32767).astype(np.int16)


def signals():
    rng = np.random.default_rng(7)
    t = np.arange(N) / 16000.0
    hiss = rng.normal(0, 300, N)
    out = {"quiet hiss": hiss * 0.05 + 3,
           "a motor: hiss and a 1 kHz tone": hiss + 4000 * np.sin(2 * np.pi * 1000.3 * t) + 120,
           "mains hum": hiss * 0.3 + 2500 * np.sin(2 * np.pi * 120 * t) + 900 * np.sin(2 * np.pi * 180 * t)}
    beeps = hiss + 6000 * np.sin(2 * np.pi * 2500 * t) * (np.sin(2 * np.pi * 4 * t) > 0)
    out["a beeper: 2.5 kHz, on and off"] = beeps
    burst = np.zeros(N)
    carrier = np.sin(2 * np.pi * 4200 * t)
    for start in np.arange(200, N - 200, 16000 // 87):  # a bearing-like knock 87 times a second
        burst[start:start + 60] += 5000 * np.exp(-np.arange(60) / 12.0) * carrier[:60]
    out["knocks in the 2-7 kHz band, 87 a second"] = hiss * 0.4 + burst
    bang = hiss.copy()
    bang[9000:9040] += 30000 * np.hanning(40)
    out["one bang"] = bang
    # With hiss, as a real mic always has: a noiseless clipped sine has 160 dB between its tone and its floor,
    # more than single precision holds, and there the two differ (snd_tone_db by 0.4 dB; measured 13 Nov).
    out["clipping"] = np.clip(40000 * np.sin(2 * np.pi * 440 * t) + hiss, -32768, 32767)
    return {k: to16(v) for k, v in out.items()}


# How close the C++ must be. dB readings and the bands (log10 of power) are compared as they are; frequencies
# must land on the same bin; ratios within a small share of their range.
TOL = {"snd_level_db": 0.01, "snd_peak_db": 0.01, "snd_click": 0.002, "snd_centroid_hz": 2.0, "snd_tone_hz": 0.5,
       "snd_tone_db": 0.1, "snd_flatness": 0.005, "snd_mains_db": 0.2, "snd_tone_mod_db": 0.1,
       "snd_env_peak_hz": 0.5, "snd_env_peak_db": 0.1, "snd_kurt_hf": 0.02, "snd_clip_frac": 1e-6}
BAND_TOL = 0.01  # of log10(power): about 0.1 dB


def test_the_names_are_the_ones_the_sketch_sends():
    assert len(NAMES) == 46 and len(SENT) == 45
    assert SENT[0] == "snd_level_db" and SENT[1] == "snd_band_00" and SENT[33] == "snd_centroid_hz"
    assert SENT[-2:] == ["snd_kurt_hf", "snd_clip_frac"]


def test_cpp_matches_python_on_every_signal():
    if not need_compiler():
        return
    worst = {}
    for label, s in signals().items():
        got, want = esp32(s), python(s)
        for name in SENT:
            tol = BAND_TOL if name.startswith("snd_band_") else TOL[name]
            if name == "snd_kurt_hf":
                tol = max(tol, 0.002 * abs(want[name]))
            err = abs(got[name] - want[name])
            worst[name] = max(worst.get(name, 0.0), err / tol)
            assert err <= tol, f"{label}: {name} is {got[name]:.6g} on the ESP32's code, {want[name]:.6g} in Python"
    return worst


def test_where_the_window_sits_in_the_ring_does_not_matter():
    if not need_compiler():
        return
    s = signals()["a motor: hiss and a 1 kHz tone"]
    a, b = esp32(s, ring_len=N + 4096, start=0), esp32(s, ring_len=N + 4096, start=N + 4000)
    assert all(a[n] == b[n] for n in SENT)


if __name__ == "__main__":
    if binary() is None:
        print("no C++ compiler on this computer: nothing compared")
        raise SystemExit(0)
    test_the_names_are_the_ones_the_sketch_sends()
    worst = test_cpp_matches_python_on_every_signal()
    test_where_the_window_sits_in_the_ring_does_not_matter()
    name = max(worst, key=worst.get)
    print(f"the ESP32's sound readings match Python on {len(signals())} signals; closest to its limit: {name} "
          f"at {100 * worst[name]:.0f} % of it")
