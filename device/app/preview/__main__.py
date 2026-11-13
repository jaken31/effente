"""Preview every OLED screen on a laptop, drawn by the sketch's own renderer.

    python -m device.app.preview [OUT_DIR]

Compiles oled_preview.cpp (which includes device/app/effente/sketch/oled_ui.h, the code the board
runs), renders each screen in device/intake/screen.py with made-up example values, and writes one
PNG per screen and gallery.html to OUT_DIR (default: ./oled_preview). Needs a C++ compiler (c++).

The values are examples to show the layouts, not measurements.
"""
from __future__ import annotations

import base64
import html
import math
import os
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

from device.intake import screen as S

HERE = Path(__file__).resolve().parent
SCALE = 4
ON, OFF, BEZEL = (235, 245, 255), (6, 8, 12), (28, 30, 34)

# A made-up spectrum (log10 power in 20 bands): a peak low down and a smaller one higher up.
BANDS = [-4.6, -4.1, -2.9, -2.2, -3.0, -3.6, -3.4, -2.8, -3.3, -3.9, -4.2, -4.0, -3.7, -4.3, -4.8, -5.0,
         -5.1, -5.3, -5.6, -5.8]
UNBALANCE = {"diagnosis": "unbalance_like", "unknown_score": 2.4,
             "features": {"vib_1x": 7.2, "vib_velocity_mm_s": 3.1, "vib_rms_y": 2.2}}

# (file name, caption, layout rows, live: mg, peak g, temp C, ms for the blink phase[, mg before a change])
CASES = [
    ("01_live", "Display: live readings, trace, spectrum",
     S.streaming("14:02:11", 24.8, BANDS), (18.4, 0.061, 42.3, 0)),
    ("02_pick", "Pick a library entry (TRD, not in the app yet)",
     S.picking("conveyor", 0, 2, "belt conveyor"), (18.4, 0.061, 42.3, 0)),
    ("03_calibrating", "Calibrating the entry (TRD, not in the app yet)",
     S.calibrating("conveyor", 108, 300), (18.4, 0.061, 42.3, 0)),
    ("04_no_match", "Calibration fit check failed (TRD, not in the app yet)",
     S.calibrating("conveyor", 300, 300, fit_ok=False), (18.4, 0.061, 42.3, 0)),
    ("05_learning", "Long stay: learning this machine",
     S.recording("14:02:11", 161, 300, 24.8), (18.4, 0.061, 42.3, 0)),
    ("06_training", "Training on the board",
     S.training("14:07:40", 0.67, "fitting the network"), (18.4, 0.061, 42.3, 0)),
    ("07_normal", "Watching: normal",
     S.watching("14:12:03", {"diagnosis": "normal", "unknown_score": 0.42}, speed_hz=24.8), (18.4, 0.061, 42.3, 0)),
    ("08_unbalance", "Alarm: possible unbalance (demo case 2)",
     S.watching("14:15:30", UNBALANCE), (41.7, 0.142, 42.6, 0, 18.4)),
    ("09_looseness", "Alarm: possible looseness (demo case 3)",
     S.watching("14:21:10", {"diagnosis": "looseness_or_rattle", "unknown_score": 3.3,
                             "features": {"vib_harm_3_10": 9.8, "vib_half": 6.4, "vib_crest": 4.1}}),
     (55.0, 0.390, 42.7, 0, 18.4)),
    ("10_unrecognised", "Alarm: unrecognised pattern (demo case 4)",
     S.watching("14:26:45", {"diagnosis": "unrecognised_pattern", "unknown_score": 3.1,
                             "features": {"vib_peak2": 8.7, "vib_jerk": -5.2, "gyr_rms": 4.4}}),
     (33.0, 0.210, 42.5, 0, 18.4)),
    ("11_alarm_blink", "Same alarm, other half of the blink",
     S.watching("14:15:30", UNBALANCE), (41.7, 0.142, 42.6, 400, 18.4)),
    ("12_limit", "No-setup check fired while the model is content",
     S.watching("14:30:00", {"diagnosis": "normal", "unknown_score": 0.8}, problems=["VIB OVER LIMIT"], speed_hz=24.8),
     (380.0, 1.100, 43.0, 0, 18.4)),
    ("13_stopped", "Watching: machine stopped",
     S.watching("14:40:00", {"diagnosis": "stopped"}), (2.1, 0.008, 41.9, 0, 18.4)),
]


def build(tmp: Path) -> Path:
    exe = tmp / "oled_preview"
    cxx = os.environ.get("CXX", "c++")
    subprocess.run([cxx, "-std=c++11", "-O1", "-Wall", "-Wno-unused-function", "-o", str(exe),
                    str(HERE / "oled_preview.cpp")], check=True)
    return exe


def trace(mg: float, before: float | None = None) -> list[float]:
    """A made-up 12.8 s trace: a wobble around mg, or around `before` that steps up to mg 4 s ago."""
    out = []
    for i in range(128):
        level = mg if before is None or i >= 88 else before
        out.append(level * (1 + 0.18 * math.sin(i * 0.45) + 0.1 * math.sin(i * 1.9 + 1)))
    return out


def render(exe: Path, rows: list[str], live) -> bytes:
    args = [str(exe)] + [str(v) for v in live[:4]] + [f"{v:.2f}" for v in trace(live[0], *live[4:5])]
    return subprocess.run(args, input="\n".join(rows).encode("latin-1"), capture_output=True, check=True).stdout


def png(frame: bytes) -> bytes:
    """The frame as a PNG, each pixel SCALE x SCALE with a 1-pixel gap so it reads as an OLED."""
    pad = 6 * SCALE
    w, h = 128 * SCALE + 2 * pad, 64 * SCALE + 2 * pad
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        for x in range(w):
            px, py = x - pad, y - pad
            if 0 <= px < 128 * SCALE and 0 <= py < 64 * SCALE:
                cx, cy = px // SCALE, py // SCALE
                lit = frame[(cy >> 3) * 128 + cx] >> (cy & 7) & 1
                gap = px % SCALE == SCALE - 1 or py % SCALE == SCALE - 1
                raw += bytes(ON if lit and not gap else OFF)
            else:
                raw += bytes(BEZEL)

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


def main(out: Path):
    out.mkdir(parents=True, exist_ok=True)
    tiles = []
    with tempfile.TemporaryDirectory() as tmp:
        exe = build(Path(tmp))
        for name, caption, rows, live in CASES:
            data = png(render(exe, rows, live))
            (out / f"{name}.png").write_bytes(data)
            nbytes = len("\n".join(rows).encode())
            layout = html.escape("\n".join(rows).replace(S.SIGMA, "σ"))
            tiles.append(f'<figure><img src="data:image/png;base64,{base64.b64encode(data).decode()}" '
                         f'alt="{html.escape(caption)}"><figcaption><b>{html.escape(caption)}</b>'
                         f"<pre>{layout}</pre><small>{nbytes} bytes over the Bridge</small></figcaption></figure>")
    (out / "gallery.html").write_text(
        "<!doctype html><meta charset=utf-8><title>effente OLED screens</title><style>"
        "body{background:#111;color:#ddd;font:14px system-ui;margin:24px}"
        "main{display:grid;grid-template-columns:repeat(auto-fill,minmax(560px,1fr));gap:24px}"
        "figure{margin:0}img{width:100%;max-width:560px;image-rendering:pixelated;border-radius:8px}"
        "pre{color:#9ab;font-size:12px;margin:6px 0}small{color:#777}</style>"
        "<h1>effente OLED screens, 128x64</h1><p>Drawn by the sketch's renderer (oled_ui.h). "
        "Values are made-up examples, not measurements.</p><main>" + "".join(tiles) + "</main>")
    print(f"{len(CASES)} screens in {out}/ (gallery.html)")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path("oled_preview"))
