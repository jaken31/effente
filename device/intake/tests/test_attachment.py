"""Tests for effente.attachment (was the head on the machine?) and its use by the device while it records and trains.

Made-up and simulated rows only: nothing here was measured on the real head.

Run with:  python3 -m device.intake.tests.test_attachment
"""

import json
import math
import sys
import tempfile
from pathlib import Path

import numpy as np

from effente import attachment as AT
from effente import features as F
from device.intake.device_app import DeviceApp
from device.intake.tests.test_device_app import START, Clock, rows, run
from effente.simulate_conveyor import ConveyorSim


def tilt(deg, about="x"):
    """Gravity (x, y, z) of a head lying flat, tipped by deg."""
    s, c = math.sin(math.radians(deg)), math.cos(math.radians(deg))
    return (0.0, s, c) if about == "x" else (s, 0.0, c)


def recording(n, changes=()):
    """n one-a-second rows of a head lying flat; changes: (from, to, {"fall": share, "g": (x, y, z)})."""
    t = np.arange(n, dtype=float)
    cols = {"vib_freefall_frac": np.zeros(n), "vib_gravity_x": np.zeros(n), "vib_gravity_y": np.zeros(n),
            "vib_gravity_z": np.ones(n)}
    for a, b, c in changes:
        if "fall" in c:
            cols["vib_freefall_frac"][a:b] = c["fall"]
        if "g" in c:
            for ax, v in zip("xyz", c["g"]):
                cols[f"vib_gravity_{ax}"][a:b] = v
    return t, cols


def test_a_head_that_stays_on_loses_no_rows():
    t, cols = recording(600)
    rng = np.random.default_rng(0)
    for ax in "xyz":
        cols[f"vib_gravity_{ax}"] += rng.normal(0, 0.003, 600)  # the sensor's own noise: a fifth of a degree
    keep, rep = AT.attached_rows(t, cols)
    assert keep.all() and rep["checked"] and rep["rows_cut"] == 0 and not rep["warnings"], rep


def test_a_fall_cuts_from_before_it_to_the_end_when_the_head_is_not_put_back():
    # falls at 300 s and lies on its side from then on
    t, cols = recording(600, [(300, 301, {"fall": 0.4}), (300, 600, {"g": tilt(80)})])
    keep, rep = AT.attached_rows(t, cols)
    assert keep[:295].all() and not keep[295:].any(), np.flatnonzero(~keep)[[0, -1]]
    assert rep["times_off"][0]["what"] == "fell" and rep["times_off"][0]["at_s"] == 300, rep
    assert rep["warnings_screen"] == ["HEAD CAME OFF"] and "305 of 600" in rep["warnings"][0], rep


def test_a_head_that_lands_flat_still_counts_as_off_until_it_is_lifted_and_put_back():
    # falls at 200 s and lands the same way up (the same tilt as on the machine); picked up at 400 s, back at 410 s
    t, cols = recording(600, [(200, 201, {"fall": 0.3}), (400, 410, {"g": tilt(60)})])
    keep, rep = AT.attached_rows(t, cols)
    assert keep[:195].all() and not keep[195:415].any() and keep[415:].all(), np.flatnonzero(np.diff(keep))
    assert [e["what"] for e in rep["times_off"]] == ["fell"], rep


def test_a_knock_that_tilts_it_for_a_moment_cuts_only_around_the_knock():
    t, cols = recording(600, [(300, 302, {"g": tilt(25)})])
    keep, rep = AT.attached_rows(t, cols)
    assert (~keep).sum() == 12 and not keep[295:307].any(), np.flatnonzero(~keep)
    assert rep["times_off"] == [{"what": "moved", "at_s": 300.0, "for_s": 2.0}], rep


def test_a_head_put_on_after_the_press_has_its_place_where_it_comes_to_rest():
    # in the hand, tipped 40 degrees, for the first 30 s; then on the machine, flat
    t, cols = recording(600, [(0, 30, {"g": tilt(40)})])
    keep, rep = AT.attached_rows(t, cols)
    assert not keep[:35].any() and keep[35:].all(), np.flatnonzero(np.diff(keep))
    assert rep["times_off"] == [{"what": "placing", "at_s": 0.0, "for_s": 30.0}], rep
    # the same change after the first minute is the head moved, and it stays moved
    t, cols = recording(600, [(0, 90, {"g": tilt(40)})])
    keep, rep = AT.attached_rows(t, cols)
    assert keep[:85].all() and not keep[85:].any() and rep["times_off"][0]["what"] == "moved", rep


def test_under_ten_degrees_is_not_moved():
    t, cols = recording(600, [(300, 600, {"g": tilt(8)})])
    keep, rep = AT.attached_rows(t, cols)
    assert keep.all(), rep  # one step of 8 degrees: not moved, and not rocking


def test_a_rocking_head_is_cut_while_it_rocks():
    t, cols = recording(600)
    for i in range(300, 400):  # tipping 3 degrees one way and back, every other second
        for ax, v in zip("xyz", tilt(3 if i % 2 else -3)):
            cols[f"vib_gravity_{ax}"][i] = v
    keep, rep = AT.attached_rows(t, cols)
    assert keep[:290].all() and not keep[310:400].any() and keep[420:].all(), np.flatnonzero(np.diff(keep))
    assert rep["times_off"][0]["what"] == "loose", rep


def test_a_machine_that_rocks_all_the_time_is_learned_from_with_a_warning():
    t, cols = recording(600)
    for i in range(600):
        for ax, v in zip("xyz", tilt(3 if i % 2 else -3)):
            cols[f"vib_gravity_{ax}"][i] = v
    keep, rep = AT.attached_rows(t, cols)
    assert keep.all() and rep["warnings_screen"] == ["HEAD WOBBLED"], rep


def test_a_sensor_without_gravity_is_not_checked():
    t, cols = recording(600, [(0, 600, {"g": (0.0, 0.0, 0.0), "fall": 1.0})])  # piezo: 0 g, "free fall" always
    keep, rep = AT.attached_rows(t, cols)
    assert keep.all() and rep["checked"] is False, rep


def test_a_machine_shaking_through_zero_g_from_the_start_is_not_a_fall():
    t, cols = recording(600, [(0, 600, {"fall": 0.2})])
    keep, rep = AT.attached_rows(t, cols)
    assert keep.all() and "falls were not looked for" in rep["warnings"][0], rep


def test_rows_in_any_order_and_a_matrix_of_feature_rows():
    t, cols = recording(600, [(300, 301, {"fall": 0.4}), (300, 600, {"g": tilt(80)})])
    keep, _ = AT.attached_rows(t, cols)
    p = np.random.default_rng(1).permutation(600)
    shuffled, _ = AT.attached_rows(t[p], {k: v[p] for k, v in cols.items()})
    assert (shuffled == keep[p]).all()
    X = np.full((600, F.N_FEATURES), np.nan)
    for k, v in cols.items():
        X[:, F.INDEX[k]] = v
    assert (AT.attached_rows_of(t, X, F.FEATURE_NAMES)[0] == keep).all()


def test_a_minute_is_kept_only_if_none_of_its_seconds_was_cut():
    t = np.arange(300.0)
    keep = np.ones(300, bool)
    keep[130:135] = False
    assert AT.periods_kept(np.arange(0.0, 300.0, 60.0), 60, t, keep).tolist() == [True, True, False, True, True]


def test_the_device_says_the_head_fell_off_and_does_not_learn_from_what_it_read_on_the_floor():
    """12 minutes recorded; at minute 6 the head falls and lies on its side, shaking 30 times harder, to the end."""
    def fall_at(t_fall):
        def change(t0, x):
            if t0 == t_fall:
                x[F.INDEX["vib_freefall_frac"]] = 0.4
            if t0 >= t_fall:
                for ax, v in zip("xyz", tilt(80)):
                    x[F.INDEX[f"vib_gravity_{ax}"]] = v
                x[F.INDEX["vib_velocity_mm_s"]] *= 30
        return change

    with tempfile.TemporaryDirectory() as tmp:
        clock, events = Clock(), []
        app = DeviceApp(tmp, ConveyorSim(days=1), clock=clock)
        app._event = lambda name, details: events.append((name, details))
        change = fall_at(START + 7 * 60)

        def fed(minutes):
            for t0, x in rows(START, minutes):
                x = x.copy()
                change(t0, x)
                yield t0, x

        run(app, clock, fed(9), press_at=(START + 60,))
        assert app.mode == "recording"
        assert app.screen_lines()[0] == "@!HEAD FELL OFF" and app.screen_lines()[2] == "vib", app.screen_lines()
        assert app.status()["head"]["state"] == "fell", app.status()["head"]
        off = [d for n, d in events if n == "head_off"]
        assert len(off) == 1 and off[0]["what"] == "fell", off
        logged = [json.loads(line)["event"] for line in open(app.recording / "events.jsonl")]
        assert logged.count("head_off") == 1, logged
        run(app, clock, ((t0, x) for t0, x in fed(13) if t0 >= START + 9 * 60), press_at=(START + 12 * 60,))
        assert app.mode == "watching", (app.mode, app.message)
        trained = next(d for n, d in events if n == "model_trained")
        cut = trained["head_off"][app.recording.name]
        assert cut["times_off"][0]["what"] == "fell" and 290 <= cut["rows_cut"] <= 310, cut
        base = app.watcher.m.d["baseline"]
        assert "HEAD CAME OFF" in base["warnings_screen"] and any("not learned from" in w for w in base["warnings"]), base
        # the normal it learned is the machine's, not the floor's 30 times harder shaking
        on = [x[F.INDEX["vib_velocity_mm_s"]] for t0, x in rows(START + 60, 5)]
        assert base["levels"]["vib_velocity_mm_s"] <= 1.5 * max(on), (base["levels"], max(on))


def test_the_device_learns_as_before_when_the_head_stays_on():
    with tempfile.TemporaryDirectory() as tmp:
        clock, events = Clock(), []
        app = DeviceApp(tmp, ConveyorSim(days=1), clock=clock)
        app._event = lambda name, details: events.append((name, details))
        run(app, clock, rows(START, 14), press_at=(START + 60, START + 13 * 60))
        assert app.mode == "watching", (app.mode, app.message)
        assert not [n for n, _ in events if n in ("head_off", "head_back", "head_check_off")], events
        assert "head_off" not in next(d for n, d in events if n == "model_trained")


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
