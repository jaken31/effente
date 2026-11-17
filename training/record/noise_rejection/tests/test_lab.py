"""Tests for the candidate methods in lab.py, on hand-made signals (seconds, no simulator).

Run with:  python3 -m pytest training/record/noise_rejection/tests
"""

import numpy as np

from training.record.noise_rejection import lab as L

RATE = 1000.0


def _tones(parts, n=1000, noise=0.0005, seed=0):
    """(n, 3) in g: gravity on z plus tones [(Hz, g, axis)] and a little white noise."""
    rng = np.random.default_rng(seed)
    t = np.arange(n) / RATE
    a = rng.normal(0, noise, (n, 3))
    a[:, 2] += 1.0
    for hz, g, axis in parts:
        a[:, axis] += g * np.sin(2 * np.pi * hz * t + 0.3 * axis)
    return a


def test_family_keeps_true_speed_when_2x_dominates():
    a = _tones([(29.5, 0.02, 0), (59.0, 0.13, 2), (88.5, 0.01, 2)])  # misalignment: 2x far above 1x
    assert L.today(a)[L.F.INDEX["vib_speed_hz"]] > 55  # today's tracker reads double
    assert abs(L.speed_family(a) - 29.5) < 1.0


def test_family_ignores_half_order_of_looseness():
    parts = [(29.5, 0.06, 0), (14.75, 0.02, 2)] + [(29.5 * k, 0.05 / k ** 0.7, k % 3) for k in range(2, 8)]
    a = _tones(parts)
    assert abs(L.speed_family(a) - 29.5) < 1.0
    assert abs(L.speed_halving(a) - 14.75) < 1.0  # the simple halving fix gets this wrong


def test_family_is_zero_with_no_clear_peak():
    assert L.speed_family(_tones([])) == 0.0


def test_whiten_flattens_magnitude_and_keeps_length():
    a = _tones([(50.0, 0.2, 0), (120.0, 0.05, 1)])
    w = L.whiten(a)
    assert w.shape == a.shape
    mag = np.abs(np.fft.rfft(w[:, 0]))[1:-1]
    assert np.allclose(mag, 1.0, atol=1e-6)


def test_whitening_finds_pulses_under_a_strong_tone():
    """Noise pulsing at 21 Hz, under a mesh tone with 7.4 Hz sidebands 30 times stronger."""
    rng = np.random.default_rng(1)
    t = np.arange(4000) / RATE
    pulse = (0.5 + 0.5 * np.cos(2 * np.pi * 21.0 * t)) ** 8
    a = np.zeros((len(t), 3))
    a[:, 0] = 0.003 * rng.standard_normal(len(t)) * pulse
    a[:, 0] += 0.09 * np.sin(2 * np.pi * 170 * t) * (1 + 0.8 * np.sin(2 * np.pi * 7.4 * t))
    spec, fe = L.envelope_spectrum(a, RATE)
    plain_hz, _ = L.env_peak(spec, fe)
    spec_w, fe_w = L.envelope_spectrum(L.whiten(a), RATE)
    white_hz, _ = L.env_peak(spec_w, fe_w)
    assert abs(plain_hz - 7.4) < 0.5
    assert abs(white_hz - 21.0) < 0.5


def test_auc():
    assert L.auc([0, 1], [2, 3]) == 1.0
    assert L.auc([2, 3], [0, 1]) == 0.0
    assert L.auc([1, 1], [1, 1]) == 0.5
