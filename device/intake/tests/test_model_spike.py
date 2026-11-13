"""The model's own alarm reacts to a spike: one abnormal period, not only a change that lasts.

The Watcher alarms on the median of the last few period scores, so one abnormal second was outvoted whatever
its score. With a spike line in the model's baseline (effente.trainer), a single period scoring over it alarms
at once and is held 30 s. On the fake board (IMU only), a 5-minute recording at 30 mg, 1 s periods.

Run with:  python3 -m device.intake.tests.test_model_spike
"""

import sys
import time

import numpy as np

from effente import features as F
from effente import safety as SF
from effente import trainer as TR
from effente.model_runtime import SPIKE_HOLD_S, Model, Watcher
from device.intake.extract import feature_stream
from device.intake.tests.test_board_flow import T0, FakeBoard

LEARN = 300
_CACHE = {}


def rows(seconds, knocks=None, level=lambda s: 30.0, seed=0):
    """One-a-second feature rows from the fake board: (seconds since the start, rows)."""
    key = (seconds, tuple(sorted((knocks or {}).items())), seed, level(0), level(seconds - 1))
    if key not in _CACHE:
        board = FakeBoard(seconds, level, seed=seed, knock_g=lambda s: (knocks or {}).get(s, 0.0))
        out = list(feature_stream(board, hop_s=1.0))
        _CACHE[key] = (np.array([t for t, _ in out]) - T0, np.array([x for _, x in out]))
    return _CACHE[key]


def trained(seed=0):
    t, X = rows(LEARN + 600, seed=seed)
    return TR.train_model(t[t < LEARN], X[t < LEARN], F.FEATURE_NAMES)[0]


def watch(d, t, X, sensitivity=0.5, spike_rule=True, spike=0.5):
    """Replay through the real Watcher, as the device builds it: per row (second, wrong, state). sensitivity is the
    main dial, spike the spike dial (spike_sensitivity)."""
    m = Model(d)
    if not spike_rule:
        m.spike_line = None
    w = Watcher(m, margin=SF.margins(sensitivity)["model"], spike_margin=SF.margins(spike)["model"])
    out = []
    for k, x in zip(t, X):
        st = w.predict_at(float(k), x)
        out.append((int(k), SF.verdict(st, [])["wrong"], st))
    return out


def test_the_spike_line_is_learned_from_unsmoothed_scores_and_adds_no_model_file_key():
    d = trained()
    ms = d["baseline"]["model_spike"]
    assert "model_spike" not in d and "spike_line" not in d  # the model file has the keys it had
    assert Model(d).spike_line == ms["line"] and ms["line"] > 3 * d["threshold"], (ms, d["threshold"])
    assert ms["line"] > 2 * ms["highest_held_out"]  # well above any single held-out second
    older = {k: v for k, v in d.items()}
    older["baseline"] = {k: v for k, v in d["baseline"].items() if k != "model_spike"}
    assert Model(older).spike_line is None  # a model trained before this: no spike rule, as before
    # 1-minute periods (long recordings): the model sees minute medians, so no spike line is stored
    from device.intake.tests.test_training_line import rows as minute_rows
    tl, Xl = minute_rows(2 * TR.MIN_ROWS + 50, 60)
    assert "model_spike" not in TR.train_model(tl, Xl, F.FEATURE_NAMES, period=60)[0]["baseline"]


def test_ten_clean_minutes_give_no_spike_alarm_at_0_5_or_0_75():
    for seed in (0, 1):
        d = trained(seed)
        t, X = rows(LEARN + 600, seed=seed)
        for sens in (0.5, 0.75):
            got = watch(d, t[t >= LEARN], X[t >= LEARN], spike=sens)
            assert not any(st.get("spike") or st.get("spike_held") for _, _, st in got), (seed, sens)
            top = max(st.get("spike_score", 0) for _, _, st in got)
            assert top < SF.margins(sens)["model"], (seed, sens, top)  # and with room: under the line at that sensitivity


def test_one_abnormal_second_alarms_at_once_is_named_and_clears_after_the_hold():
    d = trained()
    knock = LEARN + 60
    t, X = rows(LEARN + 140, knocks={knock: 0.3})
    live = t >= LEARN
    got = watch(d, t[live], X[live])
    on = [k for k, wrong, _ in got if wrong]
    # the knock's second is judged when the next second's row arrives: 1 s after it ends
    assert on and on[0] == knock + 1 and on == list(range(knock + 1, on[-1] + 1)), on[:5]
    assert SPIKE_HOLD_S - 1 <= len(on) <= SPIKE_HOLD_S + 1, len(on)  # held about 30 s, then clear
    first = got[[k for k, _, _ in got].index(knock + 1)][2]
    assert first["spike"] and first["spike_score"] > 1 and first["unknown_score"] < 1, first  # the median hadn't moved
    assert "knocking_or_jolts" in SF.verdict(first, [])["what"], SF.verdict(first, [])  # named from that second
    held = got[[k for k, _, _ in got].index(knock + 10)][2]
    assert held["spike_held"] and not held["spike"] and held["symptoms"] == first["symptoms"] and held["minute"] == knock + 9
    alone = [k - knock for k, wrong, _ in watch(d, t[live], X[live], spike_rule=False) if wrong]
    assert not [k for k in alone if k <= 3] and len(alone) <= 2, alone  # the median alone: nothing then, a blink later at most


def test_a_smaller_spike_needs_the_spike_dial_up_and_the_main_dial_leaves_it_alone():
    """A 0.10 g knock: not at spike sensitivity 0.5; at 0.75 and 1 it alarms, while the main sensitivity stays at
    0.5, so the median rule judges exactly as before (the same level every second, and no alarm on clean data).
    And the main dial doesn't move the spike line: at main 1 and spike 0.5 the knock is still not a spike."""
    d = trained()
    knock = LEARN + 60
    t, X = rows(LEARN + 140, knocks={knock: 0.1})
    live = t >= LEARN
    assert not any(st.get("spike") or st.get("spike_held") for _, _, st in watch(d, t[live], X[live], spike=0.5))
    base = watch(d, t[live], X[live], spike_rule=False)
    for dial in (0.75, 1.0):
        got = watch(d, t[live], X[live], spike=dial)
        assert [k for k, wrong, st in got if st.get("spike") and wrong] == [knock + 1], dial
        assert [st.get("unknown_score") for _, _, st in got] == [st.get("unknown_score") for _, _, st in base]
    tc, Xc = rows(LEARN + 600)
    clean = watch(d, tc[tc >= LEARN], Xc[tc >= LEARN], spike_rule=False)
    for dial in (0.75, 1.0):  # the median rule's alarm share on clean data: unchanged by the spike dial
        got = watch(d, tc[tc >= LEARN], Xc[tc >= LEARN], spike=dial)
        same = [(w, st.get("unknown_score")) for _, w, st in clean]
        assert [(w and not (st.get("spike") or st.get("spike_held")), st.get("unknown_score")) for _, w, st in got] == same
    assert not any(st.get("spike") for _, _, st in watch(d, t[live], X[live], sensitivity=1.0, spike=0.5))


def test_both_dials_persist_across_a_restart_and_round_trip_through_settings():
    import json
    import tempfile
    import urllib.request
    from pathlib import Path
    from device.intake.device_app import DeviceApp
    from device.intake.stream import Stream
    with tempfile.TemporaryDirectory() as tmp:
        stream = Stream("127.0.0.1", 0).start()
        try:
            app = DeviceApp(tmp, FakeBoard(1, lambda s: 30.0), stream=stream)
            url = f"http://127.0.0.1:{stream.port}/settings"
            got = json.loads(urllib.request.urlopen(url, timeout=5).read())
            assert got["sensitivity"] == 0.5 and got["spike_sensitivity"] == 0.5, got
            req = urllib.request.Request(url, data=json.dumps({"spike_sensitivity": 0.75}).encode(), method="POST")
            got = json.loads(urllib.request.urlopen(req, timeout=5).read())
            assert got["ok"] and got["spike_sensitivity"] == 0.75 and got["sensitivity"] == 0.5, got
            assert got["counts_as_wrong"]["sudden_impact_times_highest_while_learning"] == 1.5
            assert got["counts_as_wrong"]["vibration_times_normal"] == 2.0  # the main dial's, unchanged
            req = urllib.request.Request(url, data=json.dumps({"sensitivity": 0.25}).encode(), method="POST")
            assert json.loads(urllib.request.urlopen(req, timeout=5).read())["spike_sensitivity"] == 0.75  # kept
            bad = urllib.request.Request(url, data=b'{"spike_sensitivity": 2, "sensitivity": 0.5}', method="POST")
            try:
                urllib.request.urlopen(bad, timeout=5)
                raise AssertionError("a spike sensitivity of 2 was accepted")
            except urllib.error.HTTPError as e:
                assert e.code == 400
            assert app.sensitivity == 0.25  # a bad request changes neither
            st = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{stream.port}/latest", timeout=5).read())["status"]
            assert st["sensitivity"] == 0.25 and st["spike_sensitivity"] == 0.75, st
            assert json.loads((Path(tmp) / "settings.json").read_text()) == {"sensitivity": 0.25, "spike_sensitivity": 0.75}
        finally:
            stream.stop()
        again = DeviceApp(tmp, FakeBoard(1, lambda s: 30.0))  # a restart
        assert again.sensitivity == 0.25 and again.spike_sensitivity == 0.75
        assert again.safety.spike_margin == 1.5 and again.safety.margins["vibration"] == 3.0
        w = again.watcher = again._watcher(Model(trained()), None)  # as training or a resume sets it
        assert w.spike_margin == SF.margins(0.75)["model"] and w.margin == SF.margins(0.25)["model"]
        (Path(tmp) / "settings.json").write_text('{"sensitivity": 0.5}')  # an older file, by hand: spike back to 0.5
        again._settings_seen = None
        again._load_settings()
        assert again.spike_sensitivity == 0.5 and w.spike_margin == 1.0


def test_the_median_rule_is_unchanged():
    """A change that lasts (the vibration three times as strong from a point on) and a knock before it: the level
    the median rule judges is the same number with and without the spike rule, and so is every alarm it gives."""
    d = trained()
    knock, change = LEARN + 30, LEARN + 90
    t, X = rows(LEARN + 130, knocks={knock: 0.3}, level=lambda s: 30.0 if s < change else 90.0)
    live = t >= LEARN
    with_rule, without = watch(d, t[live], X[live]), watch(d, t[live], X[live], spike_rule=False)
    assert [st.get("unknown_score") for _, _, st in with_rule] == [st.get("unknown_score") for _, _, st in without]
    first = next(k for k, wrong, _ in without if wrong)
    assert change < first <= change + 6, first - change  # the median of 5: a few seconds after the change
    assert all(a for (_, a, _), (_, b, _) in zip(with_rule, without) if b)  # nothing the median caught is lost
    lasting = [st for k, _, st in with_rule if k > first + 5]
    assert all(st["unknown_score"] > 1 and not st.get("spike_held") for st in lasting)  # the median has taken over


if __name__ == "__main__":
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                started = time.time()
                fn()
                print(f"ok    {name} ({time.time() - started:.0f} s)")
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(f"FAIL  {name}: {type(e).__name__}: {e}")
    print("all passed" if not failed else f"{failed} failed")
    sys.exit(1 if failed else 0)
