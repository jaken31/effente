#!/usr/bin/env python3
"""The fan test: does effente learn a desk fan's normal, then say an unbalanced blade is wrong?

Run on the laptop, with the board on USB and the app deployed (device/app/deploy.sh):

    python3 device/app/fan_test.py                       # 20 min learning, 10 min normal, 10 min with the coin
    python3 device/app/fan_test.py --minutes 30 --normal-minutes 15 --try-sensitivity 0.7

It walks a person through it, step by step:

  1. checks the app is up (/health, /latest) and prints the sensitivity (GET /settings)
  2. presses the button to start learning (adb shell touch .../data/press)
  3. waits --minutes while the device learns the fan's normal, showing progress
  4. presses again to train, and waits for inference, showing training progress
  5. watches --normal-minutes with the fan as it is, counting false alarms
  6. asks the person to stop the fan, tape a coin on one blade and start it again, then watches
     --fault-minutes: did it say something is wrong, what did it name, and how long did it take
  7. optionally tries another sensitivity (POST /settings) for --sensitivity-minutes, then puts the
     old one back
  8. writes a JSON report to data/fan-test-<time>.json (data/ is never committed)

"Wrong" is the device's own answer: `wrong` in a results line or in status. The coin should be
named as unbalance: our name `unbalance_like` (in `what` or `diagnosis_detail`), which the
platform contract calls `imbalance`.

Not run on real hardware yet (unverified): it is tested against a local DeviceApp on simulated
data (device/intake/tests/test_fan_test.py). Standard library only.
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BOARD_DATA = "/home/arduino/ArduinoApps/effente/data"
UNBALANCE = ("unbalance_like", "imbalance")  # our fault type's name, and the contract's


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def clock(seconds: float) -> str:
    s = int(max(seconds, 0))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60:02d}:{s % 60:02d}"


class TestFailed(Exception):
    """The test cannot go on (the app is not up, a press did nothing, training failed ...)."""


# ----------------------------------------------------------------- talking to the device

class Device:
    """The app's HTTP endpoints, and a way to press its button."""

    def __init__(self, url: str, press, timeout: float = 5.0):
        self.url, self.press_fn, self.timeout = url.rstrip("/"), press, timeout

    def get(self, path: str) -> dict:
        with urllib.request.urlopen(self.url + path, timeout=self.timeout) as r:
            return json.loads(r.read().decode())

    def post(self, path: str, body: dict) -> dict:
        req = urllib.request.Request(self.url + path, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:  # 400: the device says why
            try:
                return json.loads(e.read().decode())
            except ValueError:
                return {"ok": False, "error": f"HTTP {e.code}"}

    def status(self) -> dict:
        return self.get("/latest").get("status") or {}

    def press(self) -> None:
        self.press_fn()


def command_press(cmd: list[str]):
    """A press as a command, e.g. adb shell touch .../data/press."""
    def press():
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if out.returncode != 0:
            raise TestFailed(f"the press command failed ({' '.join(cmd)}): {out.stderr.strip() or out.returncode}")
    return press


def adb_forward(adb: str, port: int) -> None:
    out = subprocess.run([adb, "forward", f"tcp:{port}", f"tcp:{port}"], capture_output=True, text=True, timeout=30)
    if out.returncode != 0:
        raise TestFailed(f"adb forward failed: {out.stderr.strip() or out.returncode}. Is the board on USB?")


class Listener(threading.Thread):
    """Reads the live stream (results and events), keeping each item with the time it arrived.
    Reconnects with Last-Event-ID if the connection drops (adb forwards do)."""

    def __init__(self, url: str):
        super().__init__(daemon=True, name="stream")
        self.url = url.rstrip("/") + "/stream?topics=results,events&replay=0"
        self.items: list[tuple[float, str, dict]] = []  # (monotonic time, topic, data)
        self.lock = threading.Lock()
        self.stop_flag = threading.Event()
        self.last_id = None
        self.reconnects = 0
        self.error = None

    def run(self):
        while not self.stop_flag.is_set():
            try:
                headers = {"Last-Event-ID": self.last_id} if self.last_id else {}
                with urllib.request.urlopen(urllib.request.Request(self.url, headers=headers), timeout=30) as r:
                    msg = {}
                    for raw in r:
                        if self.stop_flag.is_set():
                            return
                        line = raw.decode().rstrip("\n")
                        if not line:
                            self._take(msg)
                            msg = {}
                        elif not line.startswith(":"):
                            k, _, v = line.partition(":")
                            msg[k] = v[1:] if v.startswith(" ") else v
            except Exception as e:  # noqa: BLE001  keep listening: the test reports what it got
                self.error = f"{type(e).__name__}: {e}"
            if not self.stop_flag.wait(1.0):
                self.reconnects += 1

    def _take(self, msg: dict):
        if "data" not in msg or msg.get("event") not in ("results", "events"):
            return
        try:
            data = json.loads(msg["data"])
        except ValueError:
            return
        if "id" in msg:
            self.last_id = msg["id"]
        with self.lock:
            self.items.append((time.monotonic(), msg["event"], data))

    def since(self, t: float, topic: str) -> list[tuple[float, dict]]:
        with self.lock:
            return [(at, d) for at, tp, d in self.items if at >= t and tp == topic]

    def stop(self):
        self.stop_flag.set()


# ----------------------------------------------------------------- what a results line or status says

def is_wrong(d: dict) -> bool:
    if "wrong" in d:
        return bool(d["wrong"])
    return bool((d.get("result") or {}).get("wrong"))  # an app deployed before the fields were lifted


VIBRATION = {"unusual_vibration", "vibration_too_high"}  # what an unbalanced blade should be described as


def what_of(d: dict) -> list[str]:
    w = d.get("what")
    if w is None:
        w = (d.get("result") or {}).get("what")
    return list(w or [])


def detail_of(d: dict) -> str | None:
    """Our own diagnosis name (unbalance_like ...), from a results line or status."""
    if d.get("diagnosis_detail"):
        return d["diagnosis_detail"]
    r = d.get("result")
    if isinstance(r, dict) and r.get("diagnosis"):
        return r["diagnosis"]
    return d.get("diagnosis")


def names_unbalance(d: dict) -> bool:
    return any(n in UNBALANCE for n in what_of(d)) or detail_of(d) == "unbalance_like" \
        or d.get("diagnosis") == "imbalance"


# ----------------------------------------------------------------- the test

class FanTest:
    def __init__(self, device: Device, listener: Listener | None, *, record_min: float = 20.0,
                 normal_min: float = 10.0, fault_min: float = 10.0, poll_s: float = 2.0,
                 try_sensitivity: float | None = None, sensitivity_min: float = 5.0, ask=input, say=print, report_dir: Path | None = None,
                 settle_s: float = 30.0, press_wait_s: float = 15.0, train_wait_min: float = 30.0):
        self.d, self.listener = device, listener
        self.record_s, self.normal_s, self.fault_s = 60 * record_min, 60 * normal_min, 60 * fault_min
        self.poll_s, self.try_sensitivity, self.settle_s = poll_s, try_sensitivity, settle_s
        self.sensitivity_s = 60 * sensitivity_min
        self.press_wait_s, self.train_wait_s = press_wait_s, 60 * train_wait_min
        self.ask, self.say = ask, say
        self.report_dir = Path(report_dir) if report_dir else REPO / "data"
        self.report: dict = {"test": "fan", "started_at": now_iso(), "url": device.url,
                             "planned": {"record_min": record_min, "normal_min": normal_min, "fault_min": fault_min,
                                         "try_sensitivity": try_sensitivity, "sensitivity_min": sensitivity_min},
                             "steps": {}, "passed": None}

    # ------------------------------------------------------------ helpers
    def _progress(self, text: str):
        self.say(f"  {text}")

    def _wait_mode(self, want, seconds: float, show=None) -> dict:
        """Poll status until its mode is in `want` (or `seconds` pass). Returns the last status."""
        end = time.monotonic() + seconds
        s = self.d.status()
        while s.get("mode") not in want and time.monotonic() < end:
            if show:
                show(s)
            time.sleep(self.poll_s)
            s = self.d.status()
        return s

    def _press(self, step: str):
        self.d.press()
        self.report["steps"].setdefault(step, {})["pressed_at"] = now_iso()

    def _confirm(self, text: str):
        self.ask(f"\n{text}\nPress Enter when ready (Ctrl-C to stop) ")

    # ------------------------------------------------------------ the steps
    def run(self) -> dict:
        try:
            self.step_check()
            self.step_record()
            self.step_train()
            self.step_normal()
            self.step_fault()
            self.step_sensitivity()
            self._verdict()
        except TestFailed as e:
            self.report["error"] = str(e)
            self.report["passed"] = False
            self.say(f"\nSTOPPED: {e}")
        except KeyboardInterrupt:
            self.report["error"] = "stopped by the person running it"
            self.report["passed"] = False
            self.say("\nstopped")
        finally:
            self.report["finished_at"] = now_iso()
            if self.listener is not None:
                self.report["stream"] = {"reconnects": self.listener.reconnects, "last_error": self.listener.error}
            self.report["report_file"] = str(self.write_report())
            self.say(f"\nreport: {self.report['report_file']}")
        return self.report

    def step_check(self):
        self.say("1. Is the app up?")
        try:
            health = self.d.get("/health")
            status = self.d.status()
            settings = self.d.get("/settings")
        except (OSError, ValueError) as e:
            raise TestFailed(f"cannot reach the app at {self.d.url} ({e}). Deployed? adb forward done?")
        if not health.get("ok"):
            raise TestFailed(f"/health says the app is not ok: {health}")
        self.sensitivity = settings.get("sensitivity")
        self.report["steps"]["check"] = {"health": health, "mode": status.get("mode"), "settings": settings,
                                         "model_version": status.get("model_version")}
        sensors = status.get("sensors") or {}
        self._progress(f"up for {health.get('uptime_s')} s, mode {status.get('mode_shown') or status.get('mode')}, "
                       f"sensors {sensors.get('rate_hz', '?')} Hz")
        self._progress(f"sensitivity {self.sensitivity} (0 least sensitive, 1 most; default {settings.get('default')})")
        if status.get("wrong"):
            self._progress(f"note: it already says something is wrong: {what_of(status)}")
        if status.get("mode") == "training":
            raise TestFailed("the device is training; wait for it to finish and run the test again")

    def step_record(self):
        self.say("\n2. Learning the fan's normal")
        self._confirm("Fix the device firmly to the desk fan and switch the fan on at the speed you will test.")
        status = self.d.status()
        if status.get("mode") == "recording":
            self._progress(f"already learning for {clock(status.get('learning_s', 0))}: this test uses that recording")
        else:
            self._press("record")
            status = self._wait_mode(("recording",), self.press_wait_s)
            if status.get("mode") != "recording":
                raise TestFailed(f"the press did not start learning (mode {status.get('mode')}). "
                                 f"Is the press command right?")
        self.say(f"\n3. Learning for {clock(self.record_s)}: leave the fan running, don't touch it")
        start = time.monotonic()
        learned = status.get("learning_s", 0.0) or 0.0
        while learned < self.record_s:
            time.sleep(self.poll_s)
            status = self.d.status()
            if status.get("mode") != "recording":
                raise TestFailed(f"the device stopped learning by itself (mode {status.get('mode')}: "
                                 f"{status.get('message')})")
            learned = status.get("learning_s", time.monotonic() - start)
            bang = f"  ! {', '.join(what_of(status))}" if status.get("wrong") else ""
            self._progress(f"{clock(learned)} of {clock(self.record_s)}{bang}")
        self.report["steps"]["record"] = {**self.report["steps"].get("record", {}), "learned_s": learned,
                                          "wrong_while_learning": status.get("wrong"), "what": what_of(status)}

    def step_train(self):
        self.say("\n4. Training on the board")
        self._press("train")
        started = time.monotonic()
        stages = []

        def show(s):
            tr = s.get("training") or {}
            if tr:
                line = f"{int(100 * tr.get('share', 0))}% {tr.get('stage', '')}"
                if not stages or stages[-1] != tr.get("stage"):
                    stages.append(tr.get("stage"))
                self._progress(line)

        s = self._wait_mode(("training", "watching"), self.press_wait_s)
        if s.get("mode") == "recording":
            raise TestFailed(f"the device kept learning instead of training: {s.get('message')}")
        s = self._wait_mode(("watching", "recording", "display"), self.train_wait_s, show)
        took = time.monotonic() - started
        self.report["steps"]["train"] = {**self.report["steps"].get("train", {}), "seconds": round(took, 1),
                                         "stages": stages, "model": s.get("model"),
                                         "model_version": s.get("model_version"),
                                         "learning_notes": s.get("learning_notes", [])}
        if s.get("mode") != "watching":
            raise TestFailed(f"training did not end in inference (mode {s.get('mode')}: {s.get('message')})")
        self._progress(f"trained in {clock(took)}: {s.get('model')}")
        for n in s.get("learning_notes", []):
            self._progress(f"note from learning: {n}")

    def _watch(self, seconds: float, label: str, stop_when=None) -> dict:
        """Watch the stream and status for `seconds`; returns what was seen."""
        t0 = time.monotonic()
        polls, first_wrong, first_unbalance, end = [], None, None, t0 + seconds
        while time.monotonic() < end:
            time.sleep(self.poll_s)
            s = self.d.status()
            at = time.monotonic()
            polls.append((at, s))
            if s.get("wrong") and first_wrong is None:
                first_wrong = (at, s)
            if s.get("wrong") and names_unbalance(s) and first_unbalance is None:
                first_unbalance = (at, s)
            results = self.listener.since(t0, "results") if self.listener else []
            n_wrong = sum(is_wrong(r) for _, r in results)
            state = f"wrong: {', '.join(what_of(s))}" if s.get("wrong") else f"nothing wrong ({detail_of(s) or '-'})"
            self._progress(f"{label} {clock(at - t0)} of {clock(seconds)}: {state}; {len(results)} results, "
                           f"{n_wrong} wrong")
            if stop_when and stop_when(t0, first_wrong, first_unbalance):
                break
        results = self.listener.since(t0, "results") if self.listener else []
        events = self.listener.since(t0, "events") if self.listener else []
        for at, r in results:  # the results stream sees a change before the next status poll
            if is_wrong(r) and (first_wrong is None or at < first_wrong[0]):
                first_wrong = (at, r)
            if is_wrong(r) and names_unbalance(r) and (first_unbalance is None or at < first_unbalance[0]):
                first_unbalance = (at, r)
        seq = sorted([(at, is_wrong(r)) for at, r in results] + [(at, bool(s.get("wrong"))) for at, s in polls])
        episodes = sum(1 for (_, a), (_, b) in zip([(t0, False)] + seq, seq) if b and not a)
        what = Counter(n for _, r in results if is_wrong(r) for n in what_of(r))
        what.update(n for _, s in polls if s.get("wrong") for n in what_of(s))
        return {
            "seconds": round(time.monotonic() - t0, 1),
            "results": len(results), "wrong_results": sum(is_wrong(r) for _, r in results),
            "status_polls": len(polls), "wrong_status_polls": sum(bool(s.get("wrong")) for _, s in polls),
            "wrong_episodes": episodes, "what_named": dict(what),
            "diagnosis_detail_seen": dict(Counter(detail_of(r) for _, r in results)),
            "diagnosis_seen": dict(Counter(r.get("diagnosis") for _, r in results)),
            "problems_started": [e.get("details", {}).get("name") for _, e in events if e.get("event") == "problem"],
            "first_wrong": None if first_wrong is None else {
                "after_s": round(first_wrong[0] - t0, 1), "what": what_of(first_wrong[1]),
                "diagnosis": first_wrong[1].get("diagnosis"), "diagnosis_detail": detail_of(first_wrong[1])},
            "first_unbalance_after_s": None if first_unbalance is None else round(first_unbalance[0] - t0, 1),
        }

    def step_normal(self):
        self.say(f"\n5. Watching the fan as it is for {clock(self.normal_s)}: anything said now is a false alarm")
        seen = self._watch(self.normal_s, "normal")
        self.report["steps"]["normal"] = seen
        self._progress(f"false alarms: {seen['wrong_episodes']} times wrong "
                       f"({seen['wrong_results']} of {seen['results']} results, "
                       f"{seen['wrong_status_polls']} of {seen['status_polls']} status checks)"
                       + (f"; named {seen['what_named']}" if seen["what_named"] else ""))

    def step_fault(self):
        self.say("\n6. The unbalanced blade")
        self._confirm("Switch the fan OFF, tape a coin near the tip of ONE blade, and switch it back ON at the "
                      "same speed. Don't move the device.")
        self.say(f"  watching for up to {clock(self.fault_s)}")

        def done(t0, first_wrong, first_unbalance):  # described the vibration and held for settle_s: enough
            return (first_wrong is not None and VIBRATION & set(what_of(first_wrong[1]))
                    and time.monotonic() - first_wrong[0] >= self.settle_s)

        seen = self._watch(self.fault_s, "coin", stop_when=done)
        seen["said_wrong"] = seen["first_wrong"] is not None
        seen["named_unbalance"] = seen["first_unbalance_after_s"] is not None  # the cause guessed (details only)
        seen["described_vibration"] = bool(VIBRATION & set(seen["what_named"]))  # what the screen said
        self.report["steps"]["fault"] = seen
        if seen["said_wrong"]:
            fw = seen["first_wrong"]
            self._progress(f"said something is wrong after {fw['after_s']} s: {fw['what']}")
        else:
            self._progress("never said anything was wrong")
        self._progress("described it as unusual vibration or vibration too high" if seen["described_vibration"]
                       else f"didn't describe it as a vibration problem (said: {seen['what_named'] or 'nothing'})")
        self._progress(f"its guess at the cause was unbalance after {seen['first_unbalance_after_s']} s"
                       if seen["named_unbalance"] else "its guess at the cause wasn't unbalance (that's fine)")

    def step_sensitivity(self):
        v = self.try_sensitivity
        if v is None:
            answer = self.ask("\n7. Try another sensitivity with the coin still on? Type 0 to 1, or Enter to skip: ")
            answer = (answer or "").strip()
            if not answer:
                return
            try:
                v = float(answer)
            except ValueError:
                self.say(f"  {answer!r} is not a number: skipped")
                return
        else:
            self.say(f"\n7. Trying sensitivity {v} with the coin still on")
        reply = self.d.post("/settings", {"sensitivity": v})
        trial = {"sensitivity": v, "reply_ok": reply.get("ok"), "error": reply.get("error")}
        self.report["steps"].setdefault("sensitivity", []).append(trial)
        if not reply.get("ok"):
            self._progress(f"the device refused it: {reply.get('error')}")
            return
        try:
            trial.update(self._watch(self.sensitivity_s, f"sensitivity {v}"))
            self._progress(f"at {v}: wrong {trial['wrong_results']} of {trial['results']} results; "
                           f"named {trial['what_named'] or 'nothing'}")
        finally:
            back = self.d.post("/settings", {"sensitivity": self.sensitivity})
            trial["restored"] = self.sensitivity if back.get("ok") else f"not restored: {back.get('error')}"
            self._progress(f"sensitivity back to {self.sensitivity}")

    def _verdict(self):
        normal, fault = self.report["steps"]["normal"], self.report["steps"]["fault"]
        false_alarms = normal["wrong_episodes"]
        self.report["summary"] = {
            "false_alarms": false_alarms, "said_wrong_with_coin": fault["said_wrong"],
            "described_vibration": fault["described_vibration"], "named_unbalance": fault["named_unbalance"],
            "seconds_to_wrong": (fault["first_wrong"] or {}).get("after_s"),
            "seconds_to_unbalance": fault["first_unbalance_after_s"],
            "first_named": (fault["first_wrong"] or {}).get("what"),
        }
        self.report["passed"] = bool(false_alarms == 0 and fault["described_vibration"])
        self.say("\n8. Result: " + ("PASSED" if self.report["passed"] else "NOT PASSED")
                 + f": {false_alarms} false alarms; with the coin "
                 + ("described it as a vibration problem" if fault["described_vibration"] else
                    ("said wrong but not as a vibration problem" if fault["said_wrong"] else "said nothing")))
        self.say("Remove the coin before using the fan.")

    def write_report(self) -> Path:
        self.report_dir.mkdir(parents=True, exist_ok=True)
        path = self.report_dir / f"fan-test-{time.strftime('%Y%m%d-%H%M%S')}.json"
        n = 1
        while path.exists():
            path = path.with_name(f"fan-test-{time.strftime('%Y%m%d-%H%M%S')}-{n}.json")
            n += 1
        path.write_text(json.dumps(self.report, indent=2))
        return path


def main(argv=None, ask=None, say=print) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://localhost:8765", help="the app's stream (default: adb-forwarded port)")
    ap.add_argument("--minutes", type=float, default=20.0, help="learning time (default 20; the device needs 5 or more)")
    ap.add_argument("--normal-minutes", type=float, default=10.0, help="watching the fan as it is (default 10)")
    ap.add_argument("--fault-minutes", type=float, default=10.0, help="watching with the coin, at most (default 10)")
    ap.add_argument("--try-sensitivity", type=float, help="also try this sensitivity (0 to 1) at the end, then restore")
    ap.add_argument("--sensitivity-minutes", type=float, default=5.0,
                    help="how long to watch at the other sensitivity (default 5)")
    ap.add_argument("--adb", default="adb", help="the adb program")
    ap.add_argument("--no-forward", action="store_true", help="don't run adb forward (the URL is reachable already)")
    ap.add_argument("--press-cmd", help="command that presses the button (default: adb shell touch "
                                        f"{BOARD_DATA}/press)")
    ap.add_argument("--poll", type=float, default=2.0, help="seconds between status checks (default 2)")
    ap.add_argument("--settle", type=float, default=30.0,
                    help="with the coin, stop this many seconds after unbalance is named (default 30)")
    ap.add_argument("--report-dir", help="where the JSON report goes (default: data/ in the repo)")
    ap.add_argument("--yes", action="store_true", help="don't wait for Enter at the prompts")
    args = ap.parse_args(argv)

    if ask is None:
        ask = (lambda text: say(text.rstrip() + " (--yes: going on)") or "") if args.yes else input
    if args.press_cmd:
        press = command_press(shlex.split(args.press_cmd))
    else:
        press = command_press([args.adb, "shell", "touch", f"{BOARD_DATA}/press"])
    if not args.no_forward:
        port = int(args.url.rsplit(":", 1)[-1].split("/")[0]) if args.url.count(":") >= 2 else 8765
        try:
            adb_forward(args.adb, port)
        except (TestFailed, OSError) as e:
            say(f"cannot set up adb: {e}")
            return 2
    device = Device(args.url, press)
    listener = Listener(args.url)
    listener.start()
    try:
        report = FanTest(device, listener, record_min=args.minutes, normal_min=args.normal_minutes,
                         fault_min=args.fault_minutes, poll_s=args.poll, try_sensitivity=args.try_sensitivity,
                         sensitivity_min=args.sensitivity_minutes,
                         ask=ask, say=say, report_dir=Path(args.report_dir) if args.report_dir else None,
                         settle_s=args.settle).run()
    finally:
        listener.stop()
    return 0 if report.get("passed") else 1


if __name__ == "__main__":
    sys.exit(main())
