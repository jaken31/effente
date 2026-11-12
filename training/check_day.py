#!/usr/bin/env python3
"""Check a folder of effente recordings and plot what is in it.

Reads every hour_*.npz / clip_*.npz in the folder, checks timestamps,
sample rates, gaps and clipping, then draws:
  overview.png   the whole span, minute by minute, against truth.csv
  snapshot.png   vibration and sound spectra plus a thermal frame at --at
and writes per_minute.csv.

Usage:
  python3 training/check_day.py fake-dataset/normal-day-01 --at 10:30
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

FULL_SCALE_RMS = 32767 / math.sqrt(2)


def db_spl(rms_counts, gain_db):
    return 20 * np.log10(np.maximum(rms_counts, 1e-3) / FULL_SCALE_RMS) + 120 - gain_db


def parse_hhmm(text):
    p = [int(x) for x in text.split(":")] + [0, 0]
    return p[0] * 3600 + p[1] * 60 + p[2]


def minute_stats(rec, labels):
    out = {}
    acc = rec["accel"].astype(np.float32) / float(rec["accel_lsb_per_g"])
    ta = rec["accel_t_us"] / 1e6
    minute = np.floor(ta / 60).astype(int)
    for m in np.unique(minute):
        sel = minute == m
        a = acc[sel]
        dyn = a - a.mean(0)
        row = out.setdefault(m, {})
        row["vib_rms_g"] = float(np.sqrt((dyn ** 2).sum(1).mean()))
        seg = dyn[:8192, 0]
        if len(seg) == 8192:
            spec = np.abs(np.fft.rfft(seg * np.hanning(8192))) * 2 / (8192 * 0.5)
            f = np.fft.rfftfreq(8192, 1 / 1000)
            band = (f > 24) & (f < 31)
            row["one_x_g"] = float(spec[band].max())
            row["one_x_hz"] = float(f[band][spec[band].argmax()])
    aud = rec["audio"].astype(np.float32)
    bl = int(rec["audio_block_len"])
    tb = rec["audio_t_us"] / 1e6
    gain = float(rec["audio_gain_db"])
    blocks = aud[: len(tb) * bl].reshape(len(tb), bl)
    block_ms = (blocks ** 2).mean(1)
    sec = np.floor(tb).astype(int)
    sec_ids, inv = np.unique(sec, return_inverse=True)
    sec_ms = np.bincount(inv, block_ms) / np.bincount(inv)
    sec_db = db_spl(np.sqrt(sec_ms), gain)
    for s, L, ms in zip(sec_ids, sec_db, sec_ms):
        row = out.setdefault(s // 60, {})
        row.setdefault("_ms", []).append(ms)
        row["sound_max_db"] = max(row.get("sound_max_db", -99), float(L))
    for m, row in out.items():
        if "_ms" in row:
            row["sound_leq_db"] = float(db_spl(math.sqrt(np.mean(row.pop("_ms"))), gain))
    th = rec["thermal"]
    tt = rec["thermal_t_us"] / 1e6
    motor = labels == 4
    bearing = labels == 5
    bg = labels == 0
    minute = np.floor(tt / 60).astype(int)
    for m in np.unique(minute):
        fr = th[minute == m]
        row = out.setdefault(m, {})
        row["motor_max_c"] = float(fr[:, motor].max(1).mean())
        row["bearing_c"] = float(fr[:, bearing].mean())
        row["background_c"] = float(np.median(fr[:, bg]))
    return out


def checks(files):
    print("file checks")
    prev = None
    for p in files:
        r = np.load(p)
        ta = r["accel_t_us"]
        tb = r["audio_t_us"]
        tt = r["thermal_t_us"]
        acc_rate = (len(ta) - 1) / ((ta[-1] - ta[0]) / 1e6)
        aud_rate = (len(tb) - 1) * int(r["audio_block_len"]) / ((tb[-1] - tb[0]) / 1e6)
        th_rate = (len(tt) - 1) / ((tt[-1] - tt[0]) / 1e6)
        gaps = int((np.diff(ta) > 1500).sum())
        clip_a = int((np.abs(r["accel"].astype(np.int32)) >= 32767).sum())
        clip_s = int((np.abs(r["audio"].astype(np.int32)) >= 32767).sum())
        gyro_bias = (r["gyro"].astype(np.float32) / float(r["gyro_lsb_per_dps"])).mean(0)
        join = ""
        if prev is not None:
            join = f"  join gap {(ta[0] - prev) / 1000:.3f} ms"
        prev = ta[-1]
        print(f"  {p.name}: accel {len(ta):,} @ {acc_rate:.2f} Hz, audio {len(r['audio']):,} @ {aud_rate:.1f} Hz, "
              f"thermal {len(tt):,} @ {th_rate:.3f} Hz, gaps {gaps}, clipped accel/audio {clip_a}/{clip_s}, "
              f"gyro bias {np.round(gyro_bias, 2)} deg/s{join}")


def overview(folder, stats, truth, events, out):
    mins = np.array(sorted(stats))
    hours = mins / 60 + 1 / 120
    get = lambda k: np.array([stats[m].get(k, np.nan) for m in mins])
    th = np.array([int(r["second"]) for r in truth]) / 3600
    lo, hi = hours.min() - 1 / 60, hours.max() + 1 / 60
    keep = (th >= lo) & (th <= hi)
    th = th[keep]
    tr = [r for r, k in zip(truth, keep) if k]

    fig, ax = plt.subplots(5, 1, figsize=(14, 13), sharex=True)
    load = np.array([float(r["load"]) for r in tr])
    rpm = np.array([float(r["rpm"]) for r in tr])
    ax[0].fill_between(th, load, step="mid", alpha=0.35, label="load (truth)")
    ax[0].set_ylabel("load")
    ax0b = ax[0].twinx()
    ax0b.plot(th, rpm, lw=0.6, color="k", label="speed (truth)")
    ax0b.set_ylabel("RPM")
    ax[0].set_title(f"{folder.name}: pump state from truth.csv")

    ax[1].plot(hours, get("vib_rms_g") * 1000, label="vibration rms, all axes (from IMU)")
    ax[1].plot(hours, get("one_x_g") * 1000, label="1x peak, x axis (from IMU)")
    ax[1].set_ylabel("milli-g")
    ax[1].legend(loc="upper left")

    ax[2].plot(hours, get("sound_leq_db"), label="sound level, minute average (from mic)")
    ax[2].plot(hours, get("sound_max_db"), lw=0.6, alpha=0.7, label="loudest second in the minute")
    ax[2].set_ylabel("dB SPL")
    ax[2].legend(loc="upper left")

    amb = np.array([float(r["ambient_c"]) for r in tr])
    ax[3].plot(hours, get("motor_max_c"), label="motor hottest pixel (thermal)")
    ax[3].plot(hours, get("bearing_c"), label="bearing housing (thermal)")
    ax[3].plot(hours, get("background_c"), label="background median (thermal)")
    ax[3].plot(th, amb, "k--", lw=0.8, label="room temperature (truth)")
    ax[3].set_ylabel("deg C")
    ax[3].legend(loc="upper left")

    kinds = ["compressor_on", "forklift", "clang", "voices", "person_passing", "technician_check"]
    for i, kind in enumerate(kinds):
        spans = [(float(e["start_s"]) / 3600, float(e["end_s"]) / 3600) for e in events if e["type"] == kind]
        spans = [(s, en) for s, en in spans if en >= lo and s <= hi]
        for s, en in spans:
            if en - s > 0.02:  # long enough to see as a bar
                ax[4].plot([s, en], [i, i], lw=8, color=f"C{i}", solid_capstyle="butt")
        ax[4].plot([s for s, _ in spans], [i] * len(spans), "|", ms=14, mew=1.2, color=f"C{i}")
        ax[4].text(hi, i, f" {len(spans)}", va="center", fontsize=8)
    ax[4].set_yticks(range(len(kinds)), kinds)
    ax[4].set_xlabel("hour of day")
    ax[4].set_xlim(lo, hi)
    ax[4].set_title("background events (events.csv)")
    for a in ax:
        a.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out, dpi=110)
    plt.close(fig)


def snapshot(files, at_s, meta, out):
    for p in files:
        r = np.load(p)
        ta = r["accel_t_us"] / 1e6
        if ta[0] <= at_s < ta[-1] - 8:
            break
    else:
        raise SystemExit(f"no file covers {at_s} s")
    fr = meta["machine"]["frequencies_hz_at_nominal"]
    i0 = np.searchsorted(ta, at_s)
    acc = r["accel"][i0:i0 + 8192].astype(np.float32) / float(r["accel_lsb_per_g"])
    acc -= acc.mean(0)
    f = np.fft.rfftfreq(8192, 1 / 1000)
    win = np.hanning(8192)
    tb = r["audio_t_us"] / 1e6
    b0 = np.searchsorted(tb, at_s)
    bl = int(r["audio_block_len"])
    aud = r["audio"][b0 * bl: b0 * bl + 65536].astype(np.float32)
    fa = np.fft.rfftfreq(len(aud), 1 / 16000)
    aspec = np.abs(np.fft.rfft(aud * np.hanning(len(aud)))) * 2 / (len(aud) * 0.5)
    tt = r["thermal_t_us"] / 1e6
    frame = r["thermal"][np.searchsorted(tt, at_s)]

    fig = plt.figure(figsize=(14, 10))
    ax1 = fig.add_subplot(2, 2, (1, 2))
    for k, name in enumerate("xyz"):
        spec = np.abs(np.fft.rfft(acc[:, k] * win)) * 2 / (8192 * 0.5)
        ax1.semilogy(f, spec * 1000, lw=0.8, label=f"{name} axis")
    for key, lab in [("1x_rotation", "1x"), ("2x", "2x"), ("3x", "3x"), ("vane_pass_6x", "vane 6x"),
                     ("line_2x_mains", "120 Hz line"), ("compressor_floor", "compressor"),
                     ("base_resonance", "base resonance")]:
        ax1.axvline(fr[key], color="grey", lw=0.6, ls=":")
        ax1.text(fr[key], ax1.get_ylim()[1] if False else 40, lab, rotation=90, fontsize=8, va="top")
    ax1.set_xlim(0, 300)
    ax1.set_ylim(1e-3, 50)
    ax1.set_xlabel("Hz")
    ax1.set_ylabel("milli-g peak")
    ax1.set_title(f"vibration spectrum at {at_s // 3600:02.0f}:{at_s % 3600 // 60:02.0f} (8.2 s)")
    ax1.legend()

    ax2 = fig.add_subplot(2, 2, 3)
    ax2.loglog(fa[1:], aspec[1:], lw=0.5)
    for key, lab in [("line_2x_mains", "120"), ("vane_pass_6x", "vane"), ("fan_blade_pass_10x", "fan"),
                     ("rotor_slot_28x", "slot")]:
        ax2.axvline(fr[key], color="grey", lw=0.6, ls=":")
        ax2.text(fr[key], aspec[1:].max(), lab, rotation=90, fontsize=8, va="top")
    ax2.set_xlim(20, 8000)
    ax2.set_xlabel("Hz")
    ax2.set_ylabel("counts peak")
    ax2.set_title("sound spectrum (4.1 s)")

    ax3 = fig.add_subplot(2, 2, 4)
    im = ax3.imshow(frame, cmap="inferno", interpolation="nearest")
    fig.colorbar(im, ax=ax3, label="deg C")
    ax3.set_title("thermal frame")
    fig.tight_layout()
    fig.savefig(out, dpi=110)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path)
    ap.add_argument("--at", default=None, help="time for snapshot.png, HH:MM[:SS]")
    args = ap.parse_args()
    folder = args.folder
    files = sorted(folder.glob("hour_*.npz")) + sorted(folder.glob("clip_*.npz"))
    meta = json.loads((folder / "meta.json").read_text())
    labels = np.array(meta["sensors"]["thermal"]["label_map"])
    with open(folder / "truth.csv") as f:
        truth = list(csv.DictReader(f))
    with open(folder / "events.csv") as f:
        events = list(csv.DictReader(f))

    checks(files)
    stats = {}
    for p in files:
        print(f"  summarising {p.name}", flush=True)
        stats.update(minute_stats(np.load(p), labels))
    keys = ["vib_rms_g", "one_x_g", "one_x_hz", "sound_leq_db", "sound_max_db", "motor_max_c", "bearing_c", "background_c"]
    with open(folder / "per_minute.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["minute", "time"] + keys)
        for m in sorted(stats):
            w.writerow([m, f"{m // 60:02d}:{m % 60:02d}"] + [f"{stats[m].get(k, float('nan')):.4f}" for k in keys])
    overview(folder, stats, truth, events, folder / "overview.png")
    if args.at:
        snapshot(files, parse_hhmm(args.at), meta, folder / "snapshot.png")
    print(f"wrote {folder / 'overview.png'}" + (f" and {folder / 'snapshot.png'}" if args.at else ""))


if __name__ == "__main__":
    main()
