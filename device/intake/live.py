#!/usr/bin/env python3
"""Watch mode: feed live features to a model, 4 times a second.

Two parts with a queue between them:

  intake thread     reads sensor packets, cuts windows, computes features,
                    (optionally) records them, puts them on the queue
  inference loop    takes windows off the queue, gives them to the model,
                    writes the results

Sensor reading must never wait for the model. If the model falls behind and
the queue is full, the OLDEST waiting window is dropped (and counted), so the
model always works on the freshest data and no sensor data is lost.

There is no model in this repo. The model slot loads one the trainer
provides, as a Python file with this shape:

    # my_model.py
    FEATURES_VERSION = "3dd8b3863791"   # effente features it was trained on
    def load(path):                     # read the model file, return an object
        ...                             # whose .predict(features) returns a dict
    # features: one row in effente.features order, NaN = sensor missing or history too short;
    # prepare it with effente.model_input.Prep, as in training

The slot refuses a model trained on a different feature list, and reloads
by itself when the model file changes. With no model, features still flow
(and can be recorded) and every result says "no model".

  python3 -m device.intake.live --data data/fake/normal-day-01 --from 20:00 --to 21:00
  python3 -m device.intake.live --data data/fake/normal-day-01 --from 20:00 --to 21:00 \\
      --loader my_model.py --model my_model.bin --record recordings/watch-01 --log results.jsonl --speed 1
  python3 -m device.intake.live --sim conveyor --from 0:10:00 --to 0:11:00 --speed 1 --serve 8765
      # live stream for a dashboard: curl -N http://DEVICE:8765/stream  (see stream.py)
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import queue
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from effente import features as F
from effente.history import History
from device.intake.extract import add_source_args, feature_stream, features_version, make_source
from device.intake.recorder import HISTORY_SAVE_EVERY_S, Recorder
from device.intake.source import FileSource
from device.intake.stream import Stream
from device.intake.windowing import HOP_S, WINDOW_S

_END = object()


class ModelSlot:
    """Holds the trainer's model, or nothing. Never crashes the device."""

    def __init__(self, loader=None, model=None, check_every_s: float = 5.0):
        self.loader = Path(loader) if loader else None
        self.model_path = Path(model) if model else None
        self.check_every_s = check_every_s
        self.model = None
        self.status = "no model"
        self.error: str | None = None
        self.loads = 0
        self._stamp = None
        self._retries = 3
        self._next_check = 0.0
        self.reload()

    def _stamps(self):
        return tuple(p.stat().st_mtime_ns if p and p.exists() else 0 for p in (self.loader, self.model_path))

    def reload(self) -> None:
        """Load the model files. If the new ones fail, a model already running stays in use."""
        stamp = self._stamps()
        if stamp != self._stamp:
            self._retries = 3  # a file caught half-copied gets a few more tries
        self._stamp = stamp
        if not self.loader:
            self.model, self.status = None, "no model"
            return
        try:
            spec = importlib.util.spec_from_file_location(f"effente_model_{self.loads}", self.loader)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            trained_on = getattr(mod, "FEATURES_VERSION", None)
            if trained_on != features_version():
                self._failed("refused", f"model was trained on features {trained_on}, "
                                        f"this device computes {features_version()}", retry=False)
                return
            model = mod.load(str(self.model_path) if self.model_path else None)
            self.model, self.status, self.error = model, "ready", None
            self.loads += 1
        except Exception as e:  # noqa: BLE001  a bad model file must not stop the device
            self._failed("error", f"{type(e).__name__}: {e}", retry=True)

    def _failed(self, status: str, error: str, retry: bool) -> None:
        if self.model is None:
            self.status, self.error = status, error
        else:  # keep scoring with the model that works
            self.status, self.error = "ready", f"new model not loaded ({status}): {error}; still using the previous one"
        if retry and self._retries > 0:
            self._retries -= 1
            self._stamp = None  # try again at the next check

    def maybe_reload(self) -> bool:
        now = time.monotonic()
        if now < self._next_check:
            return False
        self._next_check = now + self.check_every_s
        if self._stamps() != self._stamp:
            self.reload()
            return True
        return False

    def predict(self, x, t0: float | None = None):
        """The model's result for one row. A model with predict_at(t0, x) also gets the row's time."""
        if self.model is None:
            return None
        if t0 is not None and hasattr(self.model, "predict_at"):
            return self.model.predict_at(t0, x)
        return self.model.predict(x)


def throttle(packets, speed: float):
    """Release packets no faster than `speed` x real time (1 = live speed)."""
    start_wall = start_t = None
    for p in packets:
        if start_wall is None:
            start_wall, start_t = time.monotonic(), float(p.t[0])
        delay = start_wall + (float(p.t[-1]) - start_t) / speed - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        yield p


@dataclass
class Stats:
    produced: int = 0
    scored: int = 0
    dropped: int = 0
    model_errors: int = 0
    reloads: int = 0
    model_s: float = 0.0
    model_max_s: float = 0.0
    wait_s: float = 0.0
    wait_max_s: float = 0.0
    feature: dict = field(default_factory=dict)
    intake_error: str | None = None

    def summary(self) -> str:
        n = max(1, self.scored)
        fw = self.feature.get("windows", 0) or 1
        return (f"windows {self.produced:,} made, {self.scored:,} given to the model, {self.dropped:,} dropped; "
                f"features {1000 * self.feature.get('seconds', 0) / fw:.2f} ms/window; "
                f"model {1000 * self.model_s / n:.2f} ms avg, {1000 * self.model_max_s:.1f} ms max; "
                f"queue wait {1000 * self.wait_s / n:.1f} ms avg, {1000 * self.wait_max_s:.0f} ms max"
                + (f"; model errors {self.model_errors}" if self.model_errors else ""))


def _put_dropping_oldest(q: queue.Queue, item, stats: Stats) -> None:
    while True:
        try:
            q.put_nowait(item)
            return
        except queue.Full:
            try:
                q.get_nowait()
                stats.dropped += 1
            except queue.Empty:
                pass


TRD_FIELDS = ("diagnosis", "confidence", "unknown_score", "features", "mask", "spectrum")
_SPECTRUM = [F.INDEX[f"vib_band_{i:02d}"] for i in range(len(F.VIB_EDGES_HZ) - 1)]
WALL_CLOCK_AFTER_S = 1e9  # timestamps above this are Unix time (from 2001 on); below, a replay or simulation


def _jsonable(o):
    return o.tolist() if hasattr(o, "tolist") else str(o)  # NumPy arrays and scalars, anything else as text


def log_record(t0, x, result, status, missing, wait_s, device_id, machine_id) -> str:
    """One results line in the TRD's log-record shape, plus intake fields.

    The TRD fields (diagnosis, confidence, unknown_score, features, mask, spectrum) are copied from
    the model's result when it gives them; fields it does not give are left out, never null. The
    spectrum defaults to the 20 vibration bands (log10 power, 5-500 Hz) for the dashboard."""
    at = (datetime.fromtimestamp(t0, timezone.utc) if t0 > WALL_CLOCK_AFTER_S else datetime.now(timezone.utc))
    rec = {"device_id": device_id, "machine_id": machine_id, "at": at.isoformat(timespec="seconds"),
           "t": round(float(t0), 3), "status": status}
    if isinstance(result, dict):
        rec.update({k: result[k] for k in TRD_FIELDS if k in result})
    if "spectrum" not in rec and not np.isnan(x[_SPECTRUM]).any():
        rec["spectrum"] = [round(float(v), 3) for v in x[_SPECTRUM]]
    rec.update({"result": result, "missing": missing, "queue_wait_ms": round(1000 * wait_s, 1)})
    try:
        return json.dumps(rec, default=_jsonable, allow_nan=False)
    except (TypeError, ValueError) as e:  # NaN or something odd in the result: keep the line, not the crash
        rec["result"] = {"error": f"result could not be written: {type(e).__name__}: {e}"}
        for k in TRD_FIELDS:  # a spectrum from the model may be what failed; the default one never has NaN
            rec.pop(k, None)
        if not np.isnan(x[_SPECTRUM]).any():
            rec["spectrum"] = [round(float(v), 3) for v in x[_SPECTRUM]]
        return json.dumps(rec, default=_jsonable, allow_nan=False)


def stream_events(recorder: Recorder, stream: Stream) -> None:
    """Also publish the recorder's events (and its storage cleanup's) on the live stream.

    Wraps `recorder.event` instead of changing recorder.py. Events written before
    this is called (recording_started) are only in events.jsonl."""
    if getattr(recorder, "_stream", None) is stream:
        return
    write = recorder.event

    def event(t, name, **details):
        write(t, name, **details)
        try:
            stream.publish_event(t, name, details)
        except Exception:  # noqa: BLE001  the stream must never stop the recording
            pass

    recorder.event, recorder._stream = event, stream
    if recorder.storage is not None and recorder.storage.event == write:
        recorder.storage.event = event


def run_live(source, slot: ModelSlot, recorder: Recorder | None = None, log_path=None,
             speed: float = 0.0, queue_size: int = 40, warmup_s: float = 60.0,
             window_s: float = WINDOW_S, hop_s: float = HOP_S, console: bool = True,
             report_every_s: float = 600.0, history: History | None = None, history_path=None,
             device_id: str = "effente-01", machine_id: str = "machine-1", stream: Stream | None = None) -> Stats:
    """Run intake and the model together. `history` carries trend features across
    restarts: it is saved to `history_path` every 5 minutes (by the recorder, if
    one is attached) and at the end. `stream` (a started device.intake.stream.Stream)
    gets every result, a feature row a second, the recorder's events and a status."""
    stats = Stats()
    q: queue.Queue = queue.Queue(maxsize=queue_size)
    history = history if history is not None else (recorder.history if recorder else None) or History()
    if recorder is not None and recorder.history is None:
        recorder.history = history
    now = {"running": True, "t": None, "status": None, "missing": None}
    if stream is not None:
        if recorder is not None:
            stream_events(recorder, stream)

        def status():
            n = max(1, stats.scored)
            storage = recorder.storage if recorder is not None else None
            return {**now, "model": {"status": slot.status, "error": slot.error, "loads": slot.loads},
                    "windows": {"made": stats.produced, "scored": stats.scored, "dropped": stats.dropped,
                                "model_errors": stats.model_errors},
                    "model_ms_avg": round(1000 * stats.model_s / n, 2),
                    "queue_wait_ms_avg": round(1000 * stats.wait_s / n, 1),
                    "recording": recorder is not None, "recording_paused": bool(storage and storage.paused),
                    "intake_error": stats.intake_error}

        stream.status_provider = status

    def intake():
        last_save = None
        try:
            packets = throttle(source.packets(), speed) if speed > 0 else None
            tap = recorder.on_packet if recorder else None
            for t0, x in feature_stream(source, window_s, hop_s, timing=stats.feature, tap=tap, packets=packets,
                                        history=history):
                if recorder:
                    recorder.on_window(t0, x)
                elif history_path and (last_save is None or t0 - last_save >= HISTORY_SAVE_EVERY_S):
                    history.save(history_path)
                    last_save = t0
                stats.produced += 1
                _put_dropping_oldest(q, (t0, x.copy(), time.monotonic()), stats)  # the model's own copy
        except Exception as e:  # noqa: BLE001
            stats.intake_error = f"{type(e).__name__}: {e}"
        finally:
            if history_path and not recorder:
                history.save(history_path)
            _put_dropping_oldest(q, _END, stats)

    worker = threading.Thread(target=intake, name="intake", daemon=True)
    worker.start()
    log = open(log_path, "a") if log_path else None
    first_t0 = next_report = None
    if console and slot.error:
        print(f"model slot: {slot.status}: {slot.error}", flush=True)
    while True:
        item = q.get()
        if item is _END:
            break
        t0, x, queued_at = item
        if slot.maybe_reload():
            stats.reloads += 1
            if console:
                print(f"model slot: {slot.status}" + (f": {slot.error}" if slot.error else ""), flush=True)
        if first_t0 is None:
            first_t0, next_report = t0, t0 + report_every_s
        missing = F.missing_sensors(x)  # before the model sees the row
        if stream is not None:
            stream.publish_features(t0, x, missing)  # copies the row, so the model cannot change it
        t = time.perf_counter()
        try:
            result = slot.predict(x, t0)
        except Exception as e:  # noqa: BLE001
            result = {"error": f"{type(e).__name__}: {e}"}
            stats.model_errors += 1
        dt = time.perf_counter() - t
        wait = time.monotonic() - queued_at
        stats.model_s += dt
        stats.model_max_s = max(stats.model_max_s, dt)
        stats.wait_s += wait
        stats.wait_max_s = max(stats.wait_max_s, wait)
        stats.scored += 1
        status = "warming_up" if t0 - first_t0 < warmup_s else slot.status
        line = (log_record(t0, x, result, status, missing, wait, device_id, machine_id)
                if log or stream is not None else None)
        if log:
            log.write(line + "\n")
            log.flush()
        if stream is not None:
            now.update(t=round(float(t0), 3), status=status, missing=missing)
            stream.publish_result(line, t0)
        if console and t0 >= next_report:
            print(f"  t={t0:,.0f} s  {status}  {stats.summary()}", flush=True)
            next_report += report_every_s
    worker.join()
    if recorder:
        recorder.close()
    if log:
        log.close()
    if stream is not None:
        now["running"] = False
        stream.publish_status()
    return stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_source_args(ap)
    ap.add_argument("--hop", type=float, default=HOP_S, help="seconds between windows (default 0.25)")
    ap.add_argument("--loader", help="the trainer's model .py file")
    ap.add_argument("--model", help="the trainer's model file, passed to load()")
    ap.add_argument("--record", help="also record features, snippets and events here while watching")
    ap.add_argument("--log", help="JSON-lines results, one per window")
    ap.add_argument("--speed", type=float, default=0.0, help="replay speed: 1 = real time, 0 = as fast as possible")
    ap.add_argument("--queue", type=int, default=40, help="windows waiting for the model before the oldest drop")
    ap.add_argument("--warmup", type=float, default=60.0, help="seconds marked warming_up after start")
    ap.add_argument("--history", help="where trend history is saved between restarts "
                                      "(default: inside --record, otherwise not saved)")
    ap.add_argument("--max-gb", type=float, help="storage budget for --record (cleanup when crossed)")
    ap.add_argument("--device-id", default="effente-01", help="this device, for the results log")
    ap.add_argument("--machine-id", default="machine-1", help="the machine it watches, for the results log")
    ap.add_argument("--serve", type=int, metavar="PORT",
                    help="serve a live data stream on this port (8765 is the usual; see device/intake/stream.py)")
    ap.add_argument("--serve-host", default="0.0.0.0",
                    help="address to serve on (default 0.0.0.0 = every network; 127.0.0.1 = this machine only)")
    ap.add_argument("--serve-features-every", type=float, default=1.0,
                    help="seconds of data between full feature rows on the stream (default 1)")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    source = make_source(args)
    slot = ModelSlot(args.loader, args.model)
    stream = None
    if args.serve is not None:
        try:
            stream = Stream(args.serve_host, args.serve, device_id=args.device_id, machine_id=args.machine_id,
                            features_every_s=args.serve_features_every).start()
        except OSError as e:
            raise SystemExit(f"cannot serve the live stream on {args.serve_host}:{args.serve}: {e}")
        if not args.quiet:
            print(f"live stream on {args.serve_host}:{stream.port}: GET /stream, /latest, /features, /health",
                  flush=True)
    history_path = args.history or (str(Path(args.record) / "history.npz") if args.record else None)
    history = History.load(history_path) if history_path else History()
    rec = (Recorder(args.record, lambda: source.info, source.sensors, history=history, max_gb=args.max_gb,
                    hop_s=args.hop) if args.record else None)
    if rec and stream:
        stream_events(rec, stream)
    if rec and history.reset_reason:
        rec.event(None, "history_reset", reason=history.reset_reason)
    if rec and args.history:
        rec.history_path = Path(args.history)
    if history.reset_reason and not args.quiet:
        print(f"history: {history.reset_reason}")
    started = time.perf_counter()
    try:
        stats = run_live(source, slot, rec, args.log, args.speed, args.queue, args.warmup, hop_s=args.hop,
                         console=not args.quiet, history=history, history_path=history_path,
                         device_id=args.device_id, machine_id=args.machine_id, stream=stream)
    finally:
        if stream is not None:
            stream.stop(drain_s=1.0)  # let connected clients get the last items
    print(f"done in {time.perf_counter() - started:.0f} s; model slot: {slot.status}")
    print(stats.summary())
    if stats.intake_error:
        print(f"intake error: {stats.intake_error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
