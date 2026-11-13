#!/usr/bin/env python3
"""Watch the effente app on the board from the laptop, over adb. Presses nothing, changes nothing.

  python3 device/app/board_monitor.py                      # until Ctrl-C
  python3 device/app/board_monitor.py --until recording    # stop when the mode is reached
  python3 device/app/board_monitor.py --log run.jsonl      # also keep every status and event

It sets up `adb forward tcp:8765 tcp:8765` itself, then reads /latest once a second and the events
from /stream. Every --every seconds it prints one line:

  the mode (and how long it has been learning, or the training stage and share)
  rows: feature rows a second on the stream, and how far behind the sensors the newest row is
  accel / gyro / temp: samples a second from each of the IMU's three streams, over the last interval
      (accelerometer and batches are counted; the gyroscope is every 10th accelerometer sample)
  lost / gaps: samples the sample numbers say went missing, and how many times
  FIFO restarts: the sketch's count, the change, and the rate per 10 s since the monitor started
  dropped: packets the Linux side threw away because it was behind
  the readings (vibration, peak, speed, IMU temperature) and anything the checks say is wrong
  while recording: the recording folder on the board, its size and files

Events (the button, mode changes, problems, training) are printed as they arrive, and after training
the training report: rows, period, patterns kept, threshold, line, warnings.

Exit codes: 0 the --until mode was reached (or Ctrl-C); 3 --max-minutes passed; 4 something needs a
look (the app unreachable or the samples stopped for --stall seconds, a refused stop, a failed
training, a problem the checks raised); --stay keeps going instead of exiting with 4.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
import urllib.request

BOARD_DATA = "/home/arduino/ArduinoApps/effente/data"
WAKE_EVENTS = ("stop_refused", "training_failed", "problem", "early_warning_off", "spike_levels_off")


def say(text: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {text}", flush=True)


def num(v, digits: int = 1):
    return round(v, digits) if isinstance(v, (int, float)) else v


def adb(args, timeout=20) -> str | None:
    try:
        out = subprocess.run(["adb", *args], capture_output=True, text=True, timeout=timeout)
        return out.stdout if out.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def get(url: str, timeout: float = 4.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


class Events(threading.Thread):
    """The stream's events topic, kept in a list; reconnects by itself (adb forwards drop)."""

    def __init__(self, url: str):
        super().__init__(daemon=True)
        self.url, self.items, self.last_id, self.lock = url + "/stream?topics=events", [], None, threading.Lock()

    def run(self):
        while True:
            try:
                req = urllib.request.Request(self.url, headers={"Last-Event-ID": self.last_id} if self.last_id else {})
                with urllib.request.urlopen(req, timeout=40) as r:
                    for raw in r:
                        line = raw.decode().rstrip("\n")
                        if line.startswith("id: "):
                            self.last_id = line[4:]
                        elif line.startswith("data: "):
                            try:
                                with self.lock:
                                    self.items.append(json.loads(line[6:]))
                            except ValueError:
                                pass
            except Exception:  # noqa: BLE001
                time.sleep(2)

    def take(self) -> list[dict]:
        with self.lock:
            out, self.items = self.items, []
        return out


def folder_line(name: str) -> str:
    out = adb(["shell", f"cd {BOARD_DATA}/recordings/{name} 2>/dev/null && du -sk . | cut -f1 && ls features raw | tr '\\n' ' '"])
    if not out:
        return f"{name}: can't be read"
    kb, _, files = out.strip().partition("\n")
    return f"{name} {kb} kB: {files.strip()}"


def report_text(rep: dict) -> str:
    keys = ("rows_learned", "rows_calibration", "period_s", "patterns_kept", "threshold", "line", "inputs",
            "features_kept", "fit_seconds", "learned_from_all_rows", "dropped_limits", "warnings")
    return "; ".join(f"{k} {rep[k]}" for k in keys if k in rep)


def model_report(name: str) -> dict | None:
    out = adb(["shell", f"cat {BOARD_DATA}/models/{name}"], timeout=60)
    try:
        return json.loads(out)["training_report"] if out else None
    except (ValueError, KeyError):
        return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://localhost:8765")
    ap.add_argument("--until", action="append", default=[], help="exit 0 when the mode is this (repeatable): "
                    "display, recording, training, watching")
    ap.add_argument("--every", type=float, default=10.0, help="seconds between printed lines")
    ap.add_argument("--max-minutes", type=float, default=0, help="exit 3 after this long (0: never)")
    ap.add_argument("--stall", type=float, default=15.0, help="seconds without the app or new samples before exit 4")
    ap.add_argument("--stay", action="store_true", help="never exit with 4; just say what was odd")
    ap.add_argument("--log", help="append every status and event to this file, one JSON object a line")
    ap.add_argument("--no-forward", action="store_true")
    args = ap.parse_args(argv)

    log = open(args.log, "a") if args.log else None

    def keep(kind, obj):
        if log:
            log.write(json.dumps({"at": time.time(), "kind": kind, **obj}) + "\n")
            log.flush()

    if not args.no_forward:
        adb(["forward", "tcp:8765", "tcp:8765"])
    events = Events(args.url)
    events.start()
    started = time.time()
    first = prev = None  # (uptime, sensors, published) at the start and at the last printed line
    last_ok = last_print = last_folder = 0.0
    last_samples, last_samples_at = None, time.time()
    mode = message = None
    wake: list[str] = []
    train_seen = None
    try:
        while True:
            now = time.time()
            try:
                latest = get(args.url + "/latest")
                last_ok = now
            except Exception as e:  # noqa: BLE001
                if now - (last_ok or started) > args.stall:
                    wake.append(f"the app can't be reached for {now - (last_ok or started):.0f} s ({type(e).__name__}); "
                                f"adb devices: {(adb(['devices']) or 'adb failed').strip().splitlines()[1:] or 'none'}")
                    last_ok = now  # say it once per --stall
                if not args.no_forward:
                    adb(["forward", "tcp:8765", "tcp:8765"])
                latest = None
            for ev in events.take():
                keep("event", ev)
                name, det = ev.get("event"), ev.get("details", {})
                if name == "model_trained":
                    say(f"EVENT model_trained {det.get('file')}: {report_text(det)}")
                else:
                    say(f"EVENT {name} {json.dumps(det)[:300]}")
                if name in WAKE_EVENTS:
                    wake.append(f"event {name}: {json.dumps(det)[:300]}")
            if latest is not None:
                st = latest.get("status") or {}
                keep("status", st)
                sn = st.get("sensors") or {}
                pub = (st.get("stream") or {}).get("published") or {}
                snap = (st.get("uptime_s") or 0.0, sn, pub)
                if first is None:
                    first = prev = snap
                    say(f"connected: mode {st.get('mode')} (state {st.get('state')}), up {st.get('uptime_s')} s, sensors {sn}, "
                        f"model {st.get('model')}, simulated {st.get('simulated')}")
                if st.get("mode") != mode:
                    say(f"MODE {mode} -> {st.get('mode')}  (state {st.get('state')})")
                    mode = st.get("mode")
                    last_print = 0.0
                if st.get("message") != message:
                    message = st.get("message")
                    if message:
                        say(f"MESSAGE (status only, not on the screen): {message}")
                if sn.get("samples") != last_samples:
                    last_samples, last_samples_at = sn.get("samples"), now
                elif now - last_samples_at > args.stall:
                    wake.append(f"no new samples for {now - last_samples_at:.0f} s (samples {last_samples})")
                    last_samples_at = now
                tr = st.get("training")
                if tr and tr != train_seen:
                    train_seen = tr
                    say(f"TRAINING {100 * tr.get('share', 0):.0f} %: {tr.get('stage')}")
                if now - last_print >= args.every:
                    last_print = now
                    dt = max(snap[0] - prev[0], 1e-6)
                    d = lambda k: (sn.get(k) or 0) - (prev[1].get(k) or 0)  # noqa: E731
                    total = max(snap[0] - first[0], 1e-6)
                    restarts = (sn.get("overflows") or 0) - (first[1].get("overflows") or 0)
                    feat = latest.get("features") or {}
                    lag = f", {now - feat['t']:.1f} s behind" if isinstance(feat.get("t"), (int, float)) else ""
                    r = st.get("readings") or {}
                    what = f" | WRONG: {st.get('what')}" if st.get("wrong") else ""
                    head = str(st.get("mode", "?")).upper()
                    if st.get("learning_s") is not None:
                        head += f" {int(st['learning_s'] // 60):02d}:{int(st['learning_s'] % 60):02d}"
                    say(f"{head} | rows {((pub.get('features') or 0) - (prev[2].get('features') or 0)) / dt:.2f}/s{lag}"
                        f" | accel {d('samples') / dt:.0f}/s gyro {d('samples') / dt / 10:.0f}/s temp {d('batches') / dt:.1f}/s"
                        f" (sketch says {sn.get('rate_hz')}) | lost {sn.get('lost')} (+{d('lost')}) gaps {sn.get('gaps')} (+{d('gaps')})"
                        f" | FIFO restarts {sn.get('overflows')} (+{d('overflows')}; {10 * restarts / total:.2f} per 10 s over {total:.0f} s)"
                        f" | dropped {sn.get('dropped_packets')} reanchors {sn.get('reanchors')}"
                        f" | vib {r.get('vib_mg')} mg peak {r.get('peak_g')} g speed {num(r.get('speed_hz'))} Hz {num(r.get('imu_temp_c'))} C"
                        f"{what}")
                    prev = snap
                if mode == "recording" and now - last_folder >= max(args.every, 15.0):
                    last_folder = now
                    state = adb(["shell", f"cat {BOARD_DATA}/state.json"])
                    try:
                        name = json.loads(state)["recording"].rstrip("/").split("/")[-1]
                        say(f"FOLDER {folder_line(name)}")
                    except (TypeError, ValueError, KeyError, AttributeError):
                        say("FOLDER state.json can't be read")
                if mode in args.until:
                    if mode == "watching" and st.get("model"):
                        rep = model_report(st["model"])
                        say(f"REPORT {st['model']}: {report_text(rep) if rep else 'not readable'}")
                        if st.get("learning_notes"):
                            say(f"NOTES (status only): {st['learning_notes']}")
                    say(f"reached {mode}")
                    return 0
            if wake:
                for w in wake:
                    say(f"LOOK: {w}")
                if not args.stay:
                    return 4
                wake = []
            if args.max_minutes and now - started > args.max_minutes * 60:
                say(f"stopping after {args.max_minutes:g} minutes, mode {mode}")
                return 3
            time.sleep(1.0)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
