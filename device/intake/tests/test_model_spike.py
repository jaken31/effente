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


def watch(d, t, X, sensitivity=0.5, spike_rule=True):
    """Replay through the real Watcher: per row (second, wrong, state)."""
    m = Model(d)
    if not spike_rule:
        m.spike_line = None
    w = Watcher(m, margin=SF.margins(sensitivity)["model"])
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
            got = watch(d, t[t >= LEARN], X[t >= LEARN], sens)
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


def test_a_smaller_spike_needs_more_sensitivity():
    d = trained()
    knock = LEARN + 60
    t, X = rows(LEARN + 140, knocks={knock: 0.1})
    live = t >= LEARN
    assert not any(st.get("spike") or st.get("spike_held") for _, _, st in watch(d, t[live], X[live], 0.5))
    spikes = [k for k, wrong, st in watch(d, t[live], X[live], 0.75) if st.get("spike") and wrong]
    assert spikes == [knock + 1], spikes  # at 0.75 half the score counts: this one does
    # (at 0.75 the median rule itself is over its line much of the time on this data, with or without the spike rule)


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
