"""effente simulated device: main loop, keyboard, options. Started by main.py.

Every record and the model announcement carry "simulated": true. Nothing this
program sends is a measurement.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import select
import signal
import sys
import time

import sim_model as model
from sim_link import Link, mask_from

POLL_S = 1.0  # contact the platform at least this often when not uploading (contract: 2 s)
MAX_PENDING = 30  # records kept for catch-up while the platform is down
KEYS = {"1": "normal", "2": "imbalance", "3": "loose_mount", "4": "unknown"}


class Keys:
    """Single-key reads from a terminal, without Enter. Does nothing when stdin is not a terminal."""

    def __init__(self, stream=None):
        self.stream = stream if stream is not None else sys.stdin
        self.fd = None
        self.saved = None

    def __enter__(self):
        try:
            if self.stream is not None and self.stream.isatty():
                import termios
                import tty

                self.fd = self.stream.fileno()
                self.saved = termios.tcgetattr(self.fd)
                tty.setcbreak(self.fd)  # keeps Ctrl-C working
        except (ImportError, OSError, ValueError):
            self.fd = self.saved = None
        return self

    def __exit__(self, *exc):
        self.restore()
        return False

    def restore(self):
        if self.fd is not None and self.saved is not None:
            import termios

            termios.tcsetattr(self.fd, termios.TCSADRAIN, self.saved)
            self.saved = None

    @property
    def active(self) -> bool:
        return self.saved is not None

    def read(self, timeout: float) -> str:
        if not self.active:
            time.sleep(timeout)
            return ""
        ready, _, _ = select.select([self.fd], [], [], timeout)
        if not ready:
            return ""
        return os.read(self.fd, 1).decode(errors="ignore")


class Sim:
    def __init__(self, args, out=None, err=None):
        self.args = args
        self.out = out or sys.stdout
        self.err = err or sys.stderr
        self.link = Link(args.url, args.device)
        self.rng = random.Random(args.seed)
        self.case = args.case
        self.mask: list[str] = []
        self.mask_version = None
        self.reachable = None  # None until the first attempt
        self.need_announce = True
        self.paused = False
        self.pending: list[dict] = []
        self.produced = 0
        self.last_record = None
        self.last_error = ""
        self.last_contact = 0.0
        self.replay = load_replay(args.replay) if args.replay else None
        self.replay_index = 0

    # --- platform contact -------------------------------------------------
    def _note(self, result):
        self.reachable = result.reachable
        self.last_contact = time.monotonic()
        if result.reachable and not result.ok and result.error and result.error != self.last_error:
            self.err.write(f"\nplatform refused request: {result.error}\n")
        self.last_error = result.error if result.reachable and not result.ok else ""

    def _take_mask(self, body):
        got = mask_from(body)
        if got is not None:
            self.mask, self.mask_version = got

    def announce_if_needed(self) -> bool:
        """Send message 1 when due. Returns False if the platform is unreachable."""
        if not self.need_announce:
            return True
        r = self.link.announce(model.model_announcement(self.args.device))
        self._note(r)
        if r.ok:
            self.need_announce = False
        return r.reachable

    def upload(self, record):
        if not self.announce_if_needed():
            self._queue([record])
            return
        batch = self.pending + [record]
        r = self.link.send_records(batch)
        self._note(r)
        if not r.reachable:
            self._queue([record])
            return
        self.pending = []  # sent, or refused (resending would be refused again)
        if r.ok:
            self._take_mask(r.body)
            if r.body.get("need_model") is True:
                self.need_announce = True
                self.announce_if_needed()

    def _queue(self, records):
        self.pending = (self.pending + records)[-MAX_PENDING:]

    def poll(self):
        if not self.announce_if_needed():
            return
        r = self.link.get_mask()
        self._note(r)
        if r.ok:
            self._take_mask(r.body)

    # --- records ----------------------------------------------------------
    def next_record(self):
        if self.replay is not None:
            if self.replay_index >= len(self.replay):
                return None
            src = self.replay[self.replay_index]
            self.replay_index += 1
            return model.replay_record(src, self.mask, self.args.device, self.args.machine)
        return model.make_record(self.case, self.rng, self.mask, self.args.device, self.args.machine)

    def step(self) -> bool:
        """Diagnose and send one window. Returns False when there is nothing more to send."""
        record = self.next_record()
        if record is None:
            return False
        self.last_record = record
        self.produced += 1
        if self.args.log:
            with open(self.args.log, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
        self.upload(record)
        return True

    # --- status -----------------------------------------------------------
    def status(self) -> str:
        if self.replay is not None:
            source = f"replay {self.replay_index}/{len(self.replay)}"
        else:
            source = self.case
        if self.paused:
            source += " (paused)"
        r = self.last_record
        if r is None:
            sent = "nothing yet"
        else:
            sent = r["diagnosis"] + (f' "{r["reason"]}"' if r.get("reason") else "") + f' {r.get("confidence", 0):.2f}'
        mask = ",".join(self.mask) if self.mask else "none"
        if self.reachable is None:
            plat = "platform ..."
        elif not self.reachable:
            plat = "platform unreachable"
        elif self.last_error:
            plat = "platform error"
        else:
            plat = "platform ok"
        if self.pending:
            plat += f" ({len(self.pending)} queued)"
        return f"[SIM] case {source} | sent {sent} | mask {mask} | {plat}"

    # --- main loop --------------------------------------------------------
    def run(self, keys: Keys) -> int:
        if self.args.model_file:
            with open(self.args.model_file, "w", encoding="utf-8") as f:
                json.dump(model.model_file(), f, indent=2)
                f.write("\n")
        tty = keys.active
        if tty:
            self.out.write("keys: 1 normal  2 imbalance  3 loose mount  4 unknown  p pause  q quit\n")
        self.announce_if_needed()
        next_at = time.monotonic()
        while True:
            key = keys.read(0.05)
            if key in ("q", "Q"):
                break
            if key in KEYS:
                self.case = KEYS[key]
            elif key in ("p", "P", " "):
                self.paused = not self.paused
            now = time.monotonic()
            if not self.paused and now >= next_at:
                if not self.step():
                    break
                next_at = max(next_at + self.args.interval, now)
                if not tty:
                    self.out.write(self.status() + "\n")
                    self.out.flush()
                if self.args.count and self.produced >= self.args.count:
                    break
            elif now - self.last_contact >= POLL_S:
                self.poll()
            if tty:
                self.out.write("\r" + self.status() + "\x1b[K")
                self.out.flush()
        if tty:
            self.out.write("\n")
        return 0


def load_replay(path: str) -> list[dict]:
    records = []
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError as e:
                raise SystemExit(f"{path}:{n}: not JSON: {e}")
            if not isinstance(obj, dict):
                raise SystemExit(f"{path}:{n}: not a JSON object")
            records.append(obj)
    if not records:
        raise SystemExit(f"{path}: no records")
    return records


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="effente simulated device (all output is simulated)")
    p.add_argument("--url", default=os.environ.get("EFFENTE_PLATFORM_URL", "http://localhost:8000"))
    p.add_argument("--device", default=model.DEVICE_ID)
    p.add_argument("--machine", default=model.MACHINE_ID)
    p.add_argument("--case", default="normal", choices=model.CASES)
    p.add_argument("--interval", type=float, default=1.0, help="seconds between records")
    p.add_argument("--seed", type=int, default=None, help="repeatable output")
    p.add_argument("--log", metavar="FILE", help="append every record to FILE (JSON lines)")
    p.add_argument("--model-file", metavar="FILE", help="write a model file for the agent")
    p.add_argument("--replay", metavar="FILE", help="send records from a JSON lines file")
    p.add_argument("--count", type=int, default=0, help="stop after N records")
    args = p.parse_args(argv)
    if args.interval <= 0:
        p.error("--interval must be above 0")
    return args


def main(argv=None) -> int:
    args = parse_args(argv)
    sim = Sim(args)
    # SIGTERM should restore the terminal too.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    with Keys() as keys:
        try:
            return sim.run(keys)
        except KeyboardInterrupt:
            keys.restore()
            sys.stdout.write("\n")
            return 130
