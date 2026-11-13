"""The OLED layouts: each fits the screen and the Bridge, and says what the PRD asks of it."""
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from device.intake import screen as S
from effente import fault_types as FT

BANDS = [-3.0 - 0.1 * i for i in range(20)]


def rows_used(rows):
    return sum(S.height(r) for r in rows)


def every_layout():
    yield S.streaming("12:00:00", 24.8, BANDS)
    yield S.streaming("12:00:00", None, None, ["VIB OVER LIMIT", "KNOCKING"])
    yield S.picking("generic_rotating", 1, 2, "any rotating machine")
    yield S.calibrating("conveyor", 10, 300)
    yield S.calibrating("conveyor", 300, 300, fit_ok=False)
    yield S.recording("12:00:00", 10, 300, 24.8)
    yield S.recording("12:00:00", 400, 300, None, "recording too short: learning carries on for a while", ["TOO HOT"])
    yield S.training("12:00:00", 0.5, "fitting the network on the summaries")
    for d in ("normal", "stopped", "warming_up"):
        yield S.watching("12:00:00", {"diagnosis": d, "unknown_score": 0.3}, notes=["MOSTLY STOPPED"])
    yield S.watching("12:00:00", {"diagnosis": "normal", "unknown_score": 0.3}, problems=["A", "B", "C"])
    for name in FT.NAMES + ("unrecognised_pattern",):
        yield S.watching("12:00:00", {"diagnosis": name, "unknown_score": 2.0,
                                      "features": {"vib_1x": 7.0, "some_very_long_feature_name": -9.5, "vib_2x": 5.0,
                                                   "vib_3x": 1.0}}, problems=["HEATING FAST"])


def test_every_layout_fits_the_screen_and_the_bridge():
    for rows in every_layout():
        assert rows_used(rows) <= 8, rows
        assert len("\n".join(rows).encode()) <= S.MAX_BYTES, rows
        for r in rows:
            if r[:1] == "*":
                assert len(r) - 1 <= S.BIG, r
            elif r[:1] not in ("=", "~", "%") and "{" not in r:
                assert len(r.lstrip("#!").replace("|", "")) <= S.WIDTH, r


def test_alarms_say_possible_and_show_the_readings_that_moved_in_spreads():
    rows = S.watching("t", {"diagnosis": "unbalance_like", "unknown_score": 2.4,
                            "features": {"vib_velocity_mm_s": 3.1, "vib_1x": 7.2}})
    assert rows[0].startswith("!") and rows[1].startswith("possible") and "*UNBALANCE" in rows
    first, second = "=1x turn|+7.2" + S.SIGMA + "|60|50", "=velocity|+3.1" + S.SIGMA + "|26|50"
    assert rows.index(first) < rows.index(second)  # the reading that moved most comes first
    for name in FT.NAMES:  # every fault type has a big name and none is shown without "possible"
        assert name in S.FAULT_BIG
        assert S.watching("t", {"diagnosis": name, "unknown_score": 2.0})[1].startswith("possible")


def test_unrecognised_pattern_says_inspect():
    rows = S.watching("t", {"diagnosis": "unrecognised_pattern", "unknown_score": 3.1, "features": {"vib_peak2": 8.7}})
    assert "unrecognised pattern" in rows and "*INSPECT" in rows


def test_spectrum_row_is_scaled_to_the_loudest_band():
    row = S.spectrum_row(BANDS)
    assert row[0] == "~" and len(row) == 21 and row[1] == "x" and min(row[1:]) >= "a"
    assert S.spectrum_row([float("nan")] * 20) is None and S.spectrum_row(None) is None


@pytest.mark.skipif(shutil.which("c++") is None, reason="no C++ compiler")
def test_the_sketch_renderer_draws_a_layout():
    """oled_ui.h, the code the board runs, compiled on the laptop: the header bar is lit, the rest drawn."""
    here = Path(__file__).resolve().parents[2] / "app" / "preview"
    with tempfile.TemporaryDirectory() as tmp:
        exe = Path(tmp) / "oled_preview"
        subprocess.run(["c++", "-std=c++11", "-o", str(exe), str(here / "oled_preview.cpp")], check=True,
                       capture_output=True)
        rows = S.watching("12:00:00", {"diagnosis": "normal", "unknown_score": 0.4})
        frame = subprocess.run([str(exe), "18", "0.05", "42", "0"], input="\n".join(rows).encode("latin-1"),
                               capture_output=True, check=True).stdout
    assert len(frame) == 1024
    assert sum(b == 0xFF for b in frame[:128]) > 40  # the inverted header
    assert any(frame[128:384]) and any(frame[512:768])  # the big NORMAL and the trace
