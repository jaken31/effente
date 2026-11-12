"""effente simulated device: main loop, keyboard, options. Started by main.py.

Every message it sends carries "simulated": true. Nothing this program sends
is a measurement.
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
from sim_link import Link

MAX_INTERVAL_S = 2.0  # contract: a request at least every 2 s, or the device counts as gone
MAX_PENDING = 30  # records kept for catch-up while the platform is down
CASE_KEYS = {str(i + 1): case for i, case in enumerate(model.CASES)}
PROBLEM_KEYS = {"h": "too_hot", "v": "vibration_above_normal"}
KEY_HELP = (
    "keys: 1 normal  2 unbalance  3 looseness  4 unrecognised  5 stopped  6 warming up\n"
    "      h too hot (limit)  v vibration above normal  m next mode  p pause (send nothing)  q quit\n"
)


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
        self.mode = args.mode
        self.checks = model.ProblemChecks(hold_s=args.hold)
        now = time.monotonic()
        for name in args.problem or []:
            self.checks.start(name, now, model.now_iso())
        self.stretch_start = now if self.case in model.ABNORMAL_CASES else None
        self.reachable = None  # None until the first attempt
        self.need_announce = True
        self.paused = False
        self.pending: list[dict] = []
        self.produced = 0
        self.last_sent = ""
        self.last_error = ""
        self.replay = load_replay(args.replay) if args.replay else None
        self.replay_index = 0
        self.replayer = model.Replayer(args.device, args.machine)

    # --- controls ---------------------------------------------------------
    def set_case(self, case: str) -> None:
        if case != self.case:
            self.case = case
            self.stretch_start = time.monotonic() if case in model.ABNORMAL_CASES else None

    def next_mode(self) -> None:
        self.mode = model.MODES[(model.MODES.index(self.mode) + 1) % len(model.MODES)]

    def toggle_problem(self, name: str) -> None:
        self.checks.toggle(name, time.monotonic(), model.now_iso())

    def key(self, key: str) -> bool:
        """Act on one key. Returns False for quit."""
        k = key.lower()
        if k == "q":
            return False
        if key in CASE_KEYS:
            self.set_case(CASE_KEYS[key])
        elif k in PROBLEM_KEYS:
            self.toggle_problem(PROBLEM_KEYS[k])
        elif k == "m":
            self.next_mode()
        elif k in ("p", " "):
            self.paused = not self.paused
        return True

    # --- platform contact -------------------------------------------------
    def _note(self, result):
        self.reachable = result.reachable
        if result.reachable and not result.ok and result.error and result.error != self.last_error:
            self.err.write(f"\nplatform refused request: {result.error}\n")
        self.last_error = result.error if result.reachable and not result.ok else ""

    def announce_if_needed(self) -> bool:
        """Send message 1 when due. Returns False if the platform is unreachable."""
        if not self.need_announce:
            return True
        r = self.link.announce(model.model_announcement(self.args.device, self.args.machine))
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
        if r.ok and r.body.get("need_model") is True:
            self.need_announce = True
            self.announce_if_needed()

    def _queue(self, records):
        self.pending = (self.pending + records)[-MAX_PENDING:]

    def check_in(self, body):
        """Message 3. A check-in is not queued: only the newest one matters."""
        if not self.announce_if_needed():
            return
        self._note(self.link.check_in(body))

    # --- messages ---------------------------------------------------------
    def abnormal_minutes(self) -> int:
        if self.stretch_start is None:
            return 0
        return 1 + int((time.monotonic() - self.stretch_start) // 60)

    def next_record(self):
        problems = self.checks.current(model.WATCHING, self.rng, time.monotonic())
        if self.replay is not None:
            if self.replay_index >= len(self.replay):
                return None
            src = self.replay[self.replay_index]
            self.replay_index += 1
            return self.replayer.record(src)
        return model.make_record(self.case, self.rng, problems, self.args.device, self.args.machine,
                                 abnormal_minutes=self.abnormal_minutes())

    def step(self) -> bool:
        """Send one record (watching) or check-in (any other mode). False when there is nothing more to send."""
        if self.mode != model.WATCHING:
            problems = self.checks.current(self.mode, self.rng, time.monotonic())
            body = model.make_check_in(self.mode, problems, self.args.machine)
            self.last_sent = self._describe("check-in " + self.mode, body["problems"])
            self.produced += 1
            self.check_in(body)
            return True
        record = self.next_record()
        if record is None:
            return False
        self.last_sent = self._describe(record["diagnosis"], record["problems"])
        self.produced += 1
        if self.args.log:
            with open(self.args.log, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
        self.upload(record)
        return True

    @staticmethod
    def _describe(what, problems):
        return what + (" + " + ",".join(p["name"] for p in problems) if problems else "")

    # --- status -----------------------------------------------------------
    def status(self) -> str:
        if self.replay is not None:
            source = f"replay {self.replay_index}/{len(self.replay)}"
        else:
            source = self.case
        if self.paused:
            source += " (paused: sending nothing)"
        now = time.monotonic()
        checks = []
        for name in model.PROBLEM_NAMES:
            st = self.checks.status(name, now)
            if st == "starting":
                left = self.checks.hold_s - (now - self.checks.started[name][0])
                checks.append(f"{name} in {max(0, left):.0f}s")
            elif st == "on" and model.PROBLEMS[name][0] != "limit" and self.mode != model.WATCHING:
                checks.append(f"{name} (watching only)")
            elif st:
                checks.append(f"{name} {st}")
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
        return (f"[SIM] {self.mode} | case {source} | problems {', '.join(checks) or 'none'} | "
                f"sent {self.last_sent or 'nothing yet'} | {plat}")

    # --- main loop --------------------------------------------------------
    def run(self, keys: Keys) -> int:
        if self.args.model_file:
            with open(self.args.model_file, "w", encoding="utf-8") as f:
                json.dump(model.model_file(), f, indent=2)
                f.write("\n")
        tty = keys.active
        if tty:
            self.out.write(KEY_HELP)
        self.announce_if_needed()
        next_at = time.monotonic()
        while True:
            key = keys.read(0.05)
            if key and not self.key(key):
                break
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
    p.add_argument("--case", default="normal", choices=model.CASES, help="what the model says (default normal)")
    p.add_argument("--mode", default=model.WATCHING, choices=model.MODES,
                   help="watching sends records; any other mode sends check-ins (default watching)")
    p.add_argument("--problem", action="append", choices=model.PROBLEM_NAMES, metavar="NAME",
                   help="a problem on from the start (repeatable): " + ", ".join(model.PROBLEM_NAMES))
    p.add_argument("--hold", type=float, default=model.HOLD_S,
                   help=f"seconds a problem must hold before it shows (default {model.HOLD_S:g}, as on the device)")
    p.add_argument("--interval", type=float, default=1.0, help="seconds between records or check-ins (at most 2)")
    p.add_argument("--seed", type=int, default=None, help="repeatable output")
    p.add_argument("--log", metavar="FILE", help="append every record to FILE (JSON lines)")
    p.add_argument("--model-file", metavar="FILE", help="write a model file for the agent")
    p.add_argument("--replay", metavar="FILE", help="send records from a JSON lines file")
    p.add_argument("--count", type=int, default=0, help="stop after N records or check-ins")
    args = p.parse_args(argv)
    if not 0 < args.interval <= MAX_INTERVAL_S:
        p.error(f"--interval must be above 0 and at most {MAX_INTERVAL_S:g} (the platform counts a device "
                "silent for 5 s as gone, and the contract asks for a request every 2 s)")
    if args.hold < 0:
        p.error("--hold must be 0 or more")
    if args.replay and args.mode != model.WATCHING:
        p.error("--replay sends records, so it needs --mode watching")
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
