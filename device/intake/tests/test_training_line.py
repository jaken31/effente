"""Tests for how the trainer sets the alarm line (effente.trainer): system C's crossfit line for recordings with
1-minute periods, today's tail line for shorter ones.

Run with:  python3 -m device.intake.tests.test_training_line
"""

import sys

import numpy as np

from effente import features as F
from effente import trainer as TR


def rows(n, period, seed=0):
    rng = np.random.default_rng(seed)
    X = 10 + rng.normal(0, 1, (n, F.N_FEATURES))
    X[:, F.INDEX["trend_running"]] = 1
    if "vib_shock_axis" in F.INDEX:
        X[:, F.INDEX["vib_shock_axis"]] = rng.integers(0, 3, n)
    return np.arange(n, dtype=float) * period, X


def test_long_recordings_use_the_crossfit_line_and_system_cs_settings():
    t, X = rows(2 * TR.MIN_ROWS + 50, 60)
    d, rep = TR.train_model(t, X, list(F.FEATURE_NAMES), period=60, time_limit_s=3)
    assert rep["line"].startswith("crossfit") and rep["rows_calibration"] == len(t), rep
    assert d["smooth_minutes"] == TR.SMOOTH_LONG == 10
    assert rep["patterns_kept"] > TR.PCA_MAX_K  # no 40-pattern cap
    # the stored threshold is the out-of-sample line x the factor, so sensitivity 0.5 is system C's operating point
    assert abs(TR.THRESHOLD_FACTOR - 4.0 ** -0.3) < 1e-12


def test_short_recordings_keep_the_tail_line():
    t, X = rows(1800, 1)
    d, rep = TR.train_model(t, X, list(F.FEATURE_NAMES), period=1, time_limit_s=3)
    assert rep["line"].startswith("tail") and "crossfit needs" in rep["line"], rep["line"]
    assert d["smooth_minutes"] == TR.SMOOTH_SHORT == 5 and rep["patterns_kept"] <= TR.PCA_MAX_K
    t, X = rows(TR.MIN_ROWS + 50, 60)  # 1-minute periods but too few to split
    _, rep = TR.train_model(t, X, list(F.FEATURE_NAMES), period=60, time_limit_s=3)
    assert rep["line"].startswith("tail"), rep["line"]


if __name__ == "__main__":
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok    {name}")
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(f"FAIL  {name}: {type(e).__name__}: {e}")
    print("all passed" if not failed else f"{failed} failed")
    sys.exit(1 if failed else 0)
