"""Tests for the live stream (device/intake/stream.py) and its hook in watch mode.

Run with pytest, or without it:  python3 -m device.intake.tests.test_stream
"""

import json
import socket
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

import numpy as np

from effente import features as F
from effente.simulate_conveyor import ConveyorSim
from device.intake.live import ModelSlot, log_record, run_live, stream_events
from device.intake.recorder import Recorder
from device.intake.source import SourceInfo
from device.intake.stream import Stream

START = 10 * 3600  # Monday 10:00 in the simulated conveyor week: running


def sim(seconds):
    return ConveyorSim(days=1, start_s=START, end_s=START + seconds)


def strict_json(text):
    """json.loads that refuses NaN and Infinity, which are not JSON."""
    def refuse(c):
        raise ValueError(f"{c} in the JSON")
    return json.loads(text, parse_constant=refuse)


class Reader(threading.Thread):
    """An SSE client on urllib, keeping every message it gets."""

    def __init__(self, url, headers=None):
        super().__init__(daemon=True)
        self.url, self.headers = url, headers or {}
        self.got: list[dict] = []
        self.error = None

    def run(self):
        try:
            with urllib.request.urlopen(urllib.request.Request(self.url, headers=self.headers), timeout=20) as r:
                self.content_type = r.headers.get("Content-Type")
                self.cors = r.headers.get("Access-Control-Allow-Origin")
                msg = {}
                for raw in r:
                    line = raw.decode().rstrip("\n")
                    if not line:
                        if "data" in msg:
                            msg["json"] = strict_json(msg["data"])
                            self.got.append(msg)
                        msg = {}
                    elif not line.startswith(":"):
                        k, _, v = line.partition(":")
                        msg[k] = v[1:] if v.startswith(" ") else v
        except Exception as e:  # noqa: BLE001
            self.error = f"{type(e).__name__}: {e}"

    def of(self, topic):
        return [m["json"] for m in self.got if m.get("event") == topic]


def wait_for(cond, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def get(stream, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{stream.port}{path}", timeout=5) as r:
        return r.status, dict(r.headers), strict_json(r.read().decode())


def stopped(reader):
    """The reader has got the recording_stopped event (the last item of a run)."""
    return any(e["event"] == "recording_stopped" for e in reader.of("events"))


def stalled_client(port):
    """Connects to /stream and never reads a byte."""
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)  # small, so the server's writes back up soon
    s.connect(("127.0.0.1", port))
    s.sendall(b"GET /stream HTTP/1.0\r\n\r\n")
    return s


# ----------------------------------------------------------------- end to end

def test_watch_mode_streams_results_features_events_and_status():
    stream = Stream("127.0.0.1", 0, machine_id="conveyor-07", status_every_s=0.5, client_queue=5000).start()
    base = f"http://127.0.0.1:{stream.port}"
    try:
        everything = Reader(f"{base}/stream")
        slow_features = Reader(f"{base}/stream?topics=features&features_every=5")
        everything.start(), slow_features.start()
        assert wait_for(lambda: stream.stream_stats()["clients"] == 2), "clients did not connect"
        with tempfile.TemporaryDirectory() as out:
            source = sim(180)
            # A zero storage budget pauses recording at once: a real storage event, from storage.py.
            rec = Recorder(out, lambda: source.info, source.sensors, snippet_every_s=0, max_gb=0.0)
            stats = run_live(source, ModelSlot(), rec, console=False, warmup_s=10, machine_id="conveyor-07",
                             stream=stream)
        assert stats.produced == stats.scored >= 700 and stats.dropped == 0
        assert wait_for(lambda: any(s.get("running") is False for s in everything.of("status"))), everything.error
        stream.stop(drain_s=2.0)
        everything.join(5), slow_features.join(5)

        assert everything.content_type.startswith("text/event-stream") and everything.cors == "*"
        results = everything.of("results")
        assert len(results) == stats.scored  # every window, none lost (the queue was big enough)
        ts = [r["t"] for r in results]
        assert all(b > a for a, b in zip(ts, ts[1:])) and abs(ts[1] - ts[0] - 0.25) < 1e-6
        last = results[-1]
        assert last["device_id"] == "effente-01" and last["machine_id"] == "conveyor-07"
        assert last["status"] == "no model" and results[0]["status"] == "warming_up"
        assert len(last["spectrum"]) == 20 and last["missing"] == [] and "queue_wait_ms" in last and "at" in last

        features = everything.of("features")
        assert 175 <= len(features) <= 181  # one per second of data, not one per window
        assert all(abs(b["t"] - a["t"] - 1.0) < 1e-6 for a, b in zip(features, features[1:]))
        row = features[-1]
        assert len(row["values"]) == F.N_FEATURES and row["features_version"] == stream.features_version
        assert row["values"][F.INDEX["vib_rms_x"]] is not None
        assert features[0]["values"][F.INDEX["trend_heat_1h"]] is None  # NaN (no history yet) arrives as null

        slow = slow_features.of("features")
        assert slow and {m.get("event") for m in slow_features.got} == {"features"}
        assert all(b["t"] - a["t"] >= 5 - 1e-6 for a, b in zip(slow, slow[1:])) and 33 <= len(slow) <= 37

        events = [e["event"] for e in everything.of("events")]
        assert "recording_paused" in events and events[-1] == "recording_stopped"
        stopped = everything.of("events")[-1]
        assert stopped["details"]["windows"] == stats.produced and stopped["machine_id"] == "conveyor-07"

        status = everything.of("status")
        assert len(status) >= 2 and status[-1]["running"] is False
        final = status[-1]
        assert final["windows"]["made"] == stats.produced and final["model"]["status"] == "no model"
        assert final["recording"] is True and final["recording_paused"] is True and final["stream"]["clients"] >= 1

        ids = [int(m["id"].rpartition("-")[2]) for m in everything.got if "id" in m]
        assert ids == sorted(ids) and len(set(ids)) == len(ids)
    finally:
        stream.stop()


def test_latest_features_health_and_replay_on_connect():
    stream = Stream("127.0.0.1", 0, machine_id="conveyor-07", status_every_s=60).start()
    base = f"http://127.0.0.1:{stream.port}"
    try:
        with tempfile.TemporaryDirectory() as out:
            source = sim(60)
            rec = Recorder(out, lambda: source.info, source.sensors, snippet_every_s=0)
            stats = run_live(source, ModelSlot(), rec, console=False, stream=stream, machine_id="conveyor-07")

        code, headers, latest = get(stream, "/latest")
        assert code == 200 and headers["Access-Control-Allow-Origin"] == "*"
        assert set(latest) == {"results", "features", "events", "status"}
        assert latest["events"]["event"] == "recording_stopped"
        assert latest["status"]["windows"]["scored"] == stats.scored and latest["status"]["running"] is False
        assert abs(latest["results"]["t"] - latest["status"]["t"]) < 1e-6
        assert len(latest["features"]["values"]) == F.N_FEATURES

        code, _, table = get(stream, "/features")
        assert table["count"] == F.N_FEATURES and [f["name"] for f in table["features"]] == F.FEATURE_NAMES
        vib = table["features"][F.INDEX["vib_speed_hz"]]
        assert vib["unit"] == "hz" and vib["sensor"] == "vibration" and vib["text"] and not vib["from_history"]
        assert len(table["spectrum"]["features"]) == 20 and len(table["spectrum"]["edges_hz"]) == 21
        assert set(f["unit"] for f in table["features"]) <= set(table["units"])

        code, _, health = get(stream, "/health")
        assert health["ok"] is True and health["published"]["results"] == stats.scored
        assert health["last_result_age_s"] is not None and health["last_result_age_s"] < 30

        # A client that connects now first gets the recent past: the last results and events.
        late = Reader(f"{base}/stream?topics=results,events")
        late.start()
        assert wait_for(lambda: stopped(late)), late.error
        replayed = late.of("results")
        assert 0 < len(replayed) <= stream._rings["results"].maxlen
        assert replayed[-1]["t"] == latest["results"]["t"]  # up to the newest
        assert "status" not in {m.get("event") for m in late.got}  # only the topics it asked for

        # Reconnecting with Last-Event-ID (what EventSource does) resumes without repeats.
        mid = late.got[len(late.got) // 2]
        resumed = Reader(f"{base}/stream?topics=results,events", headers={"Last-Event-ID": mid["id"]})
        resumed.start()
        assert wait_for(lambda: stopped(resumed)), resumed.error
        assert [m["id"] for m in resumed.got] == [m["id"] for m in late.got[len(late.got) // 2 + 1:]]

        none = Reader(f"{base}/stream?topics=results&replay=0")
        none.start()
        assert wait_for(lambda: stream.stream_stats()["clients"] == 3)
        time.sleep(0.2)
        assert none.got == []  # replay=0: live data only
    finally:
        stream.stop()


# ----------------------------------------------------------------- never slows intake

def test_a_client_that_never_reads_does_not_slow_intake():
    stream = Stream("127.0.0.1", 0, stall_s=1.0, client_queue=64).start()
    stuck = stalled_client(stream.port)
    try:
        assert wait_for(lambda: stream.stream_stats()["clients"] == 1)
        started = time.perf_counter()
        stats = run_live(sim(180), ModelSlot(), console=False, stream=stream)
        took = time.perf_counter() - started
        assert stats.produced == stats.scored >= 700 and stats.dropped == 0  # windows made = windows scored
        assert took < 60, took

        # Keep publishing (big feature rows) until the server notices the client is not reading.
        x = np.random.default_rng(0).normal(size=F.N_FEATURES)
        slowest, t = 0.0, 1e6
        deadline = time.monotonic() + 30
        while stream.stalled == 0 and time.monotonic() < deadline:
            for _ in range(200):
                t += 1.0
                a = time.perf_counter()
                stream.publish_features(t, x)
                slowest = max(slowest, time.perf_counter() - a)
            time.sleep(0.01)
        assert stream.stalled == 1 and stream.stream_stats()["clients"] == 0  # disconnected
        assert slowest < 0.05, slowest  # publishing never waited for it
    finally:
        stuck.close()
        stream.stop()


# ----------------------------------------------------------------- details

def test_nan_never_reaches_the_json():
    stream = Stream("127.0.0.1", 0).start()
    try:
        x = np.full(F.N_FEATURES, np.nan)
        x[0] = np.float32(1.5)
        stream.publish_features(5.0, x, ["vibration"])
        stream.publish_event(5.0, "odd", {"value": float("nan"), "arr": np.array([1.0, np.inf]),
                                          "n": np.int64(3), "f": np.float32(0.5)})
        line = log_record(5.0, np.zeros(F.N_FEATURES), {"spectrum": [float("nan")] * 20}, "ready", [], 0.0, "d", "m")
        stream.publish_result(line, 5.0)
        _, _, latest = get(stream, "/latest")  # strict: fails on NaN or Infinity
        assert latest["features"]["values"][0] == 1.5 and latest["features"]["values"][1] is None
        assert latest["events"]["details"] == {"value": None, "arr": [1.0, None], "n": 3, "f": 0.5}
        assert latest["results"]["spectrum"] == [0.0] * 20 and "error" in latest["results"]["result"]
    finally:
        stream.stop()


def test_recorder_events_and_storage_cleanup_reach_the_stream():
    stream = Stream()  # publishing works without serving
    with tempfile.TemporaryDirectory() as out:
        rec = Recorder(out, SourceInfo, ("accel",), snippet_every_s=0, max_gb=1.0)
        stream_events(rec, stream)
        stream_events(rec, stream)  # twice is the same as once
        rec.event(12.5, "gap", seconds=1.0)
        rec.storage.event(13.0, "storage_cleanup", step="raw snippets", files=2)
        rec.close()
        on_disk = [json.loads(line)["event"] for line in open(f"{out}/events.jsonl")]
    streamed = [it.payload["event"] for it in stream._rings["events"]]
    assert streamed == ["gap", "storage_cleanup", "recording_stopped"]
    assert on_disk == ["recording_started", "gap", "storage_cleanup", "recording_stopped"]  # still written
    assert stream._rings["events"][0].payload["details"] == {"seconds": 1.0}


def test_bad_requests_cors_and_client_limit():
    stream = Stream("127.0.0.1", 0, max_clients=1).start()
    base = f"http://127.0.0.1:{stream.port}"
    try:
        for path, code in (("/stream?topics=bogus", 400), ("/nothing", 404), ("/stream?replay=x", 400)):
            try:
                urllib.request.urlopen(base + path, timeout=5)
                raise AssertionError(f"{path} should fail")
            except urllib.error.HTTPError as e:
                assert e.code == code and e.headers["Access-Control-Allow-Origin"] == "*", (path, e.code)
        req = urllib.request.Request(base + "/stream", method="OPTIONS")
        with urllib.request.urlopen(req, timeout=5) as r:
            assert r.status == 204 and r.headers["Access-Control-Allow-Origin"] == "*"
        one = Reader(f"{base}/stream?topics=status")
        one.start()
        assert wait_for(lambda: stream.stream_stats()["clients"] == 1)
        try:
            urllib.request.urlopen(base + "/stream", timeout=5)
            raise AssertionError("a second client should be refused")
        except urllib.error.HTTPError as e:
            assert e.code == 503
        code, _, index = get(stream, "/")
        assert "/stream" in index["endpoints"]
    finally:
        stream.stop()


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
