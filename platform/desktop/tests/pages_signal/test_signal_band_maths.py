"""Band maths: log positions, baseline and differences."""

import math
from itertools import pairwise

import pytest
from app.pages.spectrum import maths

# The simulated device's edges (platform/sim/sim_model.py), as message 1 sends them.
EDGES = [round(5.0 * 100.0 ** (i / 20), 2) for i in range(21)]


def test_log_fraction_ends_and_middle():
    assert maths.log_fraction(5, 5, 500) == pytest.approx(0)
    assert maths.log_fraction(50, 5, 500) == pytest.approx(0.5)
    assert maths.log_fraction(500, 5, 500) == pytest.approx(1)
    # 218 Hz sits at log10(218/5)/2 of the way across, as the mockup draws it.
    assert maths.log_fraction(218, 5, 500) == pytest.approx(math.log10(218 / 5) / 2)


def test_band_spans_follow_the_announced_edges():
    spans = maths.band_spans(EDGES, 44, 1000)
    assert len(spans) == 20
    assert spans[0][0] == pytest.approx(44)
    assert spans[-1][1] == pytest.approx(1044)
    for (_a, b), (c, _d) in pairwise(spans):
        assert b == pytest.approx(c)  # bands touch
    # Log-spaced edges give (nearly) equal widths; rounding to 0.01 Hz moves them under half a pixel.
    widths = [b - a for a, b in spans]
    assert max(widths) - min(widths) < 0.5


def test_band_spans_uneven_edges_are_log_placed():
    edges = [10.0 * 2**i for i in range(21)]
    spans = maths.band_spans(edges, 0, 200)
    assert [b - a for a, b in spans] == pytest.approx([10.0] * 20)


def test_band_spans_without_edges_are_equal():
    spans = maths.band_spans(None, 0, 200)
    assert [b - a for a, b in spans] == pytest.approx([10.0] * 20)


def test_edges_of_rejects_unusable_models():
    assert maths.edges_of({"spectrum_edges_hz": EDGES}) == EDGES
    assert maths.edges_of(None) is None
    assert maths.edges_of({"spectrum_edges_hz": EDGES[:5]}) is None
    assert maths.edges_of({"spectrum_edges_hz": [0.0] + EDGES[1:]}) is None
    assert maths.edges_of({"spectrum_edges_hz": list(reversed(EDGES))}) is None


def test_difference_and_times_baseline():
    spec = [-4.0] * 20
    base = [-5.0] * 20
    base[3] = -4.0
    d = maths.differences(spec, base)
    assert d[0] == pytest.approx(1.0) and d[3] == pytest.approx(0.0)
    assert maths.times_baseline(1.0) == pytest.approx(10.0)
    assert maths.times_baseline(0.0) == pytest.approx(1.0)
    assert maths.signed(1.6) == "+1.6" and maths.signed(-0.25) == "-0.2" and maths.signed(-0.01) == "+0.0"


def test_red_only_well_above_baseline():
    assert not maths.is_red(None)
    assert not maths.is_red(maths.RISE_RED)
    assert maths.is_red(maths.RISE_RED + 0.01)
    assert not maths.is_red(-2.0)


def test_band_rows_sorted_by_change():
    spec = [-5.0] * 20
    spec[6] = -3.0
    spec[9] = -4.5
    base = [-5.0] * 20
    rows = maths.band_rows(spec, base, EDGES)
    assert [r["band"] for r in rows[:2]] == [7, 10]
    assert len(rows) == maths.TABLE_ROWS
    assert rows[0]["range"] == f"{EDGES[6]:.1f} to {EDGES[7]:.1f}"
    assert rows[0]["times"] == pytest.approx(100.0)


def test_baseline_from_prefers_normal_periods():
    recs = [
        {"diagnosis": "normal", "spectrum": [-5.0] * 20},
        {"diagnosis": "normal", "spectrum": [-4.0] * 20},
        {"diagnosis": "unbalance_like", "spectrum": [-1.0] * 20},
        {"diagnosis": "normal"},  # no spectrum: skipped
    ]
    base, n = maths.baseline_from(recs)
    assert n == 2 and base == pytest.approx([-4.5] * 20)
    base, n = maths.baseline_from(recs[2:3])  # no normal period: everything
    assert n == 1 and base == pytest.approx([-1.0] * 20)
    assert maths.baseline_from([]) == (None, 0)


def test_level_range_widens_never_clips():
    assert maths.level_range([-5.0] * 20) == (maths.LEVEL_LO, maths.LEVEL_HI)
    lo, hi = maths.level_range([-7.2, -1.1])
    assert lo < -7.2 and hi > -1.1
    assert maths.level_ticks(-5.6, -2.4) == [-5.5, -4.5, -3.5, -2.5]


def test_hover_text_reads_band_range_level_and_baseline():
    spec = [-5.0] * 20
    spec[6] = -2.93
    base = [-5.0] * 20
    base[6] = -4.52
    text = maths.hover_text(6, spec, base, EDGES)
    assert text == f"Band 7 · {EDGES[6]:.1f} to {EDGES[7]:.1f} Hz · level -2.93 · baseline -4.52 · {10 ** 1.59:.1f}× baseline"
    assert maths.hover_text(6, spec, None, None) == "Band 7 · range unknown · level -2.93"
