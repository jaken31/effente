#!/usr/bin/env python3
"""Try candidate vibration methods on the simulated conveyor, against today's pipeline. Simulator only.

Three candidates, each a possible change to effente/features.py (see RESULTS.md for the gaps and the bars):

  A  speed       running speed from the family of shaft harmonics, so a strong 2x (misalignment) no longer
                 reads as double speed. Also the simpler fix REALISM.md suggests: halve when there is a peak at half.
  B  whitened    cepstrum pre-whitening (keep each frequency's phase, set every magnitude to 1) before the
                 envelope, so steady tones stop hiding the bearing's pulses.
  C  averaged    the envelope spectrum averaged over 60 one-second windows, against the median of sixty 1 s
                 readings.

Today's readings come from effente.features.extract, unchanged. The candidates reuse its helpers (fast_len,
_fit, strongest_peaks, envelope_peak). The envelope spectrum in `envelope_spectrum` repeats the few lines of
features.envelope_peak that C needs the whole spectrum from: a prototype, not used by the device.

The simulator does not model the MPU-6500's 218 Hz low-pass on vibration (its noise is flat to 500 Hz), so
B's effect above 218 Hz is optimistic here. The Ottawa conversion does apply that filter.

    python3 -m training.record.noise_rejection.lab            # all three, prints JSON
    python3 -m training.record.noise_rejection.lab --only A
"""

from __future__ import annotations

import argparse
import json
import math

import numpy as np

from effente import features as F
from effente.simulate_conveyor import ACCEL_HZ, IDLER_D, SLOW, ConveyorSim, Fault
from effente.site_noise import SiteNoise

FAULT_START_S, FAULT_END_S = 7 * 3600, 21 * 3600  # Monday: the belt runs 06:00-22:00
LATE_SHARE = 0.25  # slow faults grow over their window: judge the last quarter, where the wear is close to full
SPEED_TOL = 0.03
HIT_TOL = 0.03
HARMONICS = 5


# ----------------------------------------------------------------- the simulated machine

class Lab:
    """One simulated conveyor (realistic mode, factory site noise) with at most one fault."""

    def __init__(self, kind: str | None = None, severity: float = 1.0, seed: int = 1, site: bool = True):
        faults = [] if kind is None else [Fault(kind, FAULT_START_S, FAULT_END_S, "lab", severity)]
        self.kind, self.severity = kind, severity
        self.sim = ConveyorSim(days=1, seed=seed, realistic=True, faults=faults)
        self.src = SiteNoise(self.sim, profile="factory", seed=1000 + seed) if site else self.sim

    def window(self, s: int) -> np.ndarray:
        """The 1 s of vibration (n, 3) in g starting at second s."""
        self.sim.start_s, self.sim.end_s = float(s), float(s) + 1.0
        return np.concatenate([p.values for p in self.src.packets() if p.sensor == "accel"]).astype(float)

    def seconds(self, n: int, rng: np.random.Generator, consecutive: int = 1) -> list[int]:
        """n start seconds while running; for slow faults in the last quarter of the fault window."""
        lo = FAULT_START_S + 600
        if self.kind in SLOW:
            lo = int(FAULT_END_S - LATE_SHARE * (FAULT_END_S - FAULT_START_S))
        hi = FAULT_END_S - consecutive - 600
        out: list[int] = []
        while len(out) < n:
            s = int(rng.integers(lo, hi))
            if self.sim.running[s:s + consecutive + 1].all():
                out.append(s)
        return out

    def speed(self, s: int) -> float:
        return float(self.sim.speed[s])

    def defect_hz(self, s: int) -> float:
        """The fault's true pulse rate at second s (motor bearing: 3.58x motor; idler bearing: 3.6x idler 2)."""
        if self.kind == "motor_bearing":
            return 3.58 * self.speed(s)
        if self.kind == "idler_bearing":
            return 3.6 * float(self.sim.belt_v[s]) / (math.pi * IDLER_D[1])
        raise ValueError(self.kind)


def with_tone(a: np.ndarray, rate: float, t0: float, f: float, g: float = 0.006) -> np.ndarray:
    """A stress neighbour: a tone at f on z with two harmonics (a real neighbour is rarely a pure sine)."""
    t = t0 + np.arange(len(a)) / rate
    b = a.copy()
    b[:, 2] += g * (np.sin(2 * np.pi * f * t) + 0.5 * np.sin(4 * np.pi * f * t + 0.4) + 0.25 * np.sin(6 * np.pi * f * t))
    return b


# ----------------------------------------------------------------- today's readings

def today(a: np.ndarray, rate: float = ACCEL_HZ) -> np.ndarray:
    return F.extract(F.Window(t0=0.0, accel_g=a, accel_rate_hz=rate, accel_range_g=8.0))


def spectrum(a: np.ndarray, rate: float) -> tuple[np.ndarray, np.ndarray]:
    """The per-window amplitude spectrum features._vibration takes its speed from (Hann, all axes)."""
    dyn = a - a.mean(0)
    nf = F.fast_len(len(dyn))
    seg = F._fit(dyn, nf)
    w = np.hanning(len(seg))
    amp = np.abs(np.fft.rfft(seg * w[:, None], nf, axis=0)) * (2.0 / w.sum())
    return np.sqrt((amp ** 2).sum(1)), np.fft.rfftfreq(nf, 1.0 / rate)


# ----------------------------------------------------------------- A: speed

def _level_at(total, f, hz, df):
    lo, hi = np.searchsorted(f, [hz - 1.5 * df, hz + 1.5 * df])
    return float(total[lo:hi + 1].max()) if hi > lo else 0.0


def speed_halving(a: np.ndarray, rate: float = ACCEL_HZ) -> float:
    """REALISM.md's suggested fix: take half the strongest peak when there is a clear peak at half."""
    total, f = spectrum(a, rate)
    (sp, _), = F.strongest_peaks(total, f, *F.SPEED_SEARCH_HZ, n=1)
    if sp <= 0 or sp / 2 < F.SPEED_SEARCH_HZ[0]:
        return sp
    sel = (f >= F.SPEED_SEARCH_HZ[0]) & (f <= F.SPEED_SEARCH_HZ[1])
    med = float(np.median(total[sel]))
    return sp / 2 if _level_at(total, f, sp / 2, f[1] - f[0]) > F.MIN_PROMINENCE * med else sp


def speed_family(a: np.ndarray, rate: float = ACCEL_HZ) -> float:
    """The candidate whose first HARMONICS multiples hold the most vibration on average.

    Candidates are the clear peaks and their halves and thirds. A candidate must itself be a clear peak, and
    the score is the mean over its multiples, so half the true speed (whose odd multiples are empty) loses to
    the true speed, and the true speed wins over its own 2x when 1x and 3x are present at all."""
    total, f = spectrum(a, rate)
    df = f[1] - f[0]
    lo_hz, hi_hz = F.SPEED_SEARCH_HZ
    sel = (f >= lo_hz) & (f <= hi_hz)
    med = float(np.median(total[sel]))
    peaks = [p for p, _ in F.strongest_peaks(total, f, lo_hz, hi_hz, n=4) if p > 0]
    if not peaks:
        return 0.0
    cands = {round(c, 3) for p in peaks for c in (p, p / 2, p / 3) if lo_hz <= c <= hi_hz}
    best, best_score = peaks[0], -1.0
    for c in sorted(cands):
        if _level_at(total, f, c, df) <= F.MIN_PROMINENCE * med:
            continue
        ks = [k for k in range(1, HARMONICS + 1) if k * c < f[-1]]
        score = float(np.mean([_level_at(total, f, k * c, df) for k in ks]))
        if score > best_score:
            best, best_score = c, score
    return best


def run_speed(n: int, seed: int) -> dict:
    rng = np.random.default_rng([seed, 1])
    cases = [("healthy", None, 1.0, False), ("misalignment", "misalignment", 1.0, False),
             ("misalignment subtle", "misalignment", 0.3, False), ("looseness", "looseness", 1.0, False),
             ("looseness subtle", "looseness", 0.3, False), ("unbalance", "unbalance", 1.0, False),
             ("healthy + tone 0.3 Hz above 1x", None, 1.0, True)]
    out = {}
    for name, kind, sev, tone in cases:
        lab = Lab(kind, sev, seed=seed)
        err = {"today": 0, "halving": 0, "family": 0}
        neighbour = 0
        secs = lab.seconds(n, rng)
        for s in secs:
            a, truth = lab.window(s), lab.speed(s)
            if tone:
                a = with_tone(a, ACCEL_HZ, s, truth + 0.3)
            if "neighbour_machine" in lab.src.nuisance_at(s + 0.5):
                neighbour += 1
            got = {"today": float(today(a)[F.INDEX["vib_speed_hz"]]), "halving": speed_halving(a),
                   "family": speed_family(a)}
            for k, v in got.items():
                err[k] += abs(v - truth) > SPEED_TOL * truth
        out[name] = {k: round(100 * v / len(secs), 1) for k, v in err.items()}
        out[name]["windows"] = len(secs)
        out[name]["with site neighbour"] = neighbour
    return out


# ----------------------------------------------------------------- B and C: envelope

def whiten(x: np.ndarray) -> np.ndarray:
    """Cepstrum pre-whitening: every frequency's magnitude set to 1, its phase kept. Per axis."""
    X = np.fft.rfft(x - x.mean(0), axis=0)
    return np.fft.irfft(X / (np.abs(X) + 1e-12), n=len(x), axis=0)


def envelope_spectrum(x: np.ndarray, rate: float, band=F.VIB_ENV_BAND_HZ) -> tuple[np.ndarray, np.ndarray]:
    """The summed-axis envelope spectrum of `band`, as features.envelope_peak computes it."""
    nf = F.fast_len(len(x))
    x = F._fit(x, nf)
    n = len(x)
    X = np.fft.fft(x, nf, axis=0)
    f = np.fft.fftfreq(nf, 1.0 / rate)
    keep = (f >= band[0]) & (f <= band[1])
    env = np.abs(np.fft.ifft(X * (2.0 * keep)[:, None], axis=0))[:n].sum(1)
    env = env - env.mean()
    ne = F.fast_len(len(env))
    return np.abs(np.fft.rfft(env * np.hanning(len(env)), ne)), np.fft.rfftfreq(ne, 1.0 / rate)


def env_peak(spec, fe, search=F.ENV_SEARCH_HZ) -> tuple[float, float]:
    sel = (fe >= search[0]) & (fe <= search[1])
    k = int(np.argmax(np.where(sel, spec, 0)))
    return float(fe[k]), float(spec[k] / (np.median(spec[sel]) + 1e-12))


def auc(neg, pos) -> float:
    """Chance that a fault score beats a healthy one (ties count half)."""
    neg, pos = np.asarray(neg), np.asarray(pos)
    return float(((pos[:, None] > neg[None]).sum() + 0.5 * (pos[:, None] == neg[None]).sum()) / (len(neg) * len(pos)))


def _hit(hz, truth):
    return abs(hz - truth) <= HIT_TOL * truth


def run_envelope(n: int, seed: int) -> dict:
    rng = np.random.default_rng([seed, 2])
    healthy = Lab(None, seed=seed)
    hs = healthy.seconds(n, rng)
    base = {"today": [], "whitened": []}
    for s in hs:
        dyn = healthy.window(s)
        dyn = dyn - dyn.mean(0)
        base["today"].append(F.envelope_peak(dyn, ACCEL_HZ, F.VIB_ENV_BAND_HZ, F.ENV_SEARCH_HZ)[1])
        base["whitened"].append(F.envelope_peak(whiten(dyn), ACCEL_HZ, F.VIB_ENV_BAND_HZ, F.ENV_SEARCH_HZ)[1])
    out = {"healthy median prominence": {k: round(float(np.median(v)), 2) for k, v in base.items()}}
    for kind in ("motor_bearing", "idler_bearing"):
        for sev in (0.3, 1.0):
            lab = Lab(kind, sev, seed=seed)
            hits = {"today": 0, "whitened": 0}
            prom = {"today": [], "whitened": []}
            secs = lab.seconds(n, rng)
            for s in secs:
                dyn = lab.window(s)
                dyn = dyn - dyn.mean(0)
                truth = lab.defect_hz(s)
                for k, x in (("today", dyn), ("whitened", whiten(dyn))):
                    hz, p = F.envelope_peak(x, ACCEL_HZ, F.VIB_ENV_BAND_HZ, F.ENV_SEARCH_HZ)
                    hits[k] += _hit(hz, truth)
                    prom[k].append(p)
            out[f"{kind} {sev}"] = {
                **{f"hit % {k}": round(100 * v / len(secs), 1) for k, v in hits.items()},
                **{f"AUC {k}": round(auc(base[k], prom[k]), 3) for k in prom},
                "defect Hz (median)": round(float(np.median([lab.defect_hz(s) for s in secs])), 1)}
    return out


def _minute(lab: Lab, s: int, whitened: bool) -> tuple[float, float, list[float], list[float]]:
    """One minute from second s: (averaged peak Hz, averaged prominence, per-second Hz, per-second prominence)."""
    specs, hz1, pr1 = [], [], []
    fe = None
    for k in range(60):
        dyn = lab.window(s + k)
        dyn = dyn - dyn.mean(0)
        if whitened:
            dyn = whiten(dyn)
        spec, fe_k = envelope_spectrum(dyn, ACCEL_HZ)
        if fe is None:
            fe = fe_k
        spec = spec[:len(fe)]
        specs.append(spec)
        h, p = env_peak(spec, fe)
        hz1.append(h)
        pr1.append(p)
    avg_hz, avg_p = env_peak(np.mean(specs, 0), fe)
    return avg_hz, avg_p, hz1, pr1


def run_averaged(minutes: int, seed: int) -> dict:
    rng = np.random.default_rng([seed, 3])
    out = {}
    for whitened in (False, True):
        tag = "whitened" if whitened else "plain"
        healthy = Lab(None, seed=seed)
        h_avg, h_med = [], []
        for s in healthy.seconds(minutes, rng, consecutive=60):
            _, ap, _, pr1 = _minute(healthy, s, whitened)
            h_avg.append(ap)
            h_med.append(float(np.median(pr1)))
        for kind in ("motor_bearing", "idler_bearing"):
            for sev in (0.3, 1.0):
                lab = Lab(kind, sev, seed=seed)
                f_avg, f_med, hit_avg, hit_1s = [], [], 0, 0
                secs = lab.seconds(minutes, rng, consecutive=60)
                for s in secs:
                    ah, ap, hz1, pr1 = _minute(lab, s, whitened)
                    truth = lab.defect_hz(s + 30)
                    f_avg.append(ap)
                    f_med.append(float(np.median(pr1)))
                    hit_avg += _hit(ah, truth)
                    hit_1s += sum(_hit(h, truth) for h in hz1) / 60
                out[f"{kind} {sev} ({tag})"] = {
                    "AUC median of 1 s": round(auc(h_med, f_med), 3), "AUC averaged 60 s": round(auc(h_avg, f_avg), 3),
                    "hit % 1 s": round(100 * hit_1s / len(secs), 1), "hit % averaged": round(100 * hit_avg / len(secs), 1)}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--only", choices=["A", "B", "C"])
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--windows", type=int, default=200, help="windows per case for A and B")
    ap.add_argument("--minutes", type=int, default=30, help="minutes per case for C")
    args = ap.parse_args(argv)
    res = {"seed": args.seed}
    if args.only in (None, "A"):
        res["A speed: % of running windows more than 3 % off"] = run_speed(args.windows, args.seed)
    if args.only in (None, "B"):
        res["B envelope, per 1 s window"] = run_envelope(args.windows, args.seed)
    if args.only in (None, "C"):
        res["C envelope, per minute"] = run_averaged(args.minutes, args.seed)
    print(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
