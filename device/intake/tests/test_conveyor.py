"""Tests for history features, storage cleanup and the simulated conveyor.

Run with pytest, or without it:  python3 -m device.intake.tests.test_conveyor
"""

import json
import math
import sys
import tempfile
from pathlib import Path

import numpy as np

from effente import features as F
from effente.history import History
from effente.simulate_conveyor import DAY, ConveyorSim
from device.intake import extract as E
from device.intake import recorder as Rec
from device.intake.storage import StorageManager

CELLS = [F.INDEX[f"th_cell_{r}{c}"] for r in range(6) for c in range(8)]
SPOTS = [F.INDEX[f"th_spot_{r}{c}"] for r in range(6) for c in range(8)]


def row(heat, vib, cell30=0.0):
    x = np.full(F.N_FEATURES, np.nan)
    for a in "xyz":
        x[F.INDEX[f"vib_rms_{a}"]] = vib - math.log10(math.sqrt(3))  # total level = vib
    x[F.INDEX["snd_level_db"]] = 70.0
    x[F.INDEX["env_hot_above_room_c"]] = heat
    x[F.INDEX["vib_crest"]] = 3.0
    x[CELLS] = x[SPOTS] = 0.0
    x[CELLS[30]] = x[SPOTS[30]] = cell30
    for i in range(20):
        x[F.INDEX[f"vib_band_{i:02d}"]] = -3.0
    for i in range(32):
        x[F.INDEX[f"snd_band_{i:02d}"]] = 8.0
    return x


def synthetic_week(seconds):
    """Runs 06:00-22:00 daily. While running, heat and image cell 30 rise 3 degC per day."""
    for t in range(seconds):
        h = (t % DAY) / 3600
        running = 6 <= h < 22
        heat = 10 + 3 * t / DAY if running else 2.0
        yield t, row(heat, -1.5 if running else -2.5, cell30=3 * t / DAY if running else 0.0)


def H(out, name):
    return out[F.INDEX[name] - F.N_BASE]


_WEEK = {}


def week_run():
    """One synthetic 3-day run, with outputs kept at checkpoints (shared by the tests below)."""
    if not _WEEK:
        marks = {int(h * 3600): h for h in (0.5, 0.85, 7, 8, 19, 21, 23, 44, 56, 68)}
        h = History()
        for t, x in synthetic_week(3 * DAY):
            out = h.update(F.Window(t0=float(t)), x)
            if t in marks:
                _WEEK[marks[t]] = out
        _WEEK["history"] = h
    return _WEEK


# ----------------------------------------------------------------- history

def test_each_time_scale_fills_in_only_when_there_is_enough_history():
    w = week_run()
    assert np.isnan(H(w[0.5], "trend_heat_1h")) and not np.isnan(H(w[0.85], "trend_heat_1h"))
    assert np.isnan(H(w[19], "trend_heat_24h")) and not np.isnan(H(w[21], "trend_heat_24h"))
    assert np.isnan(H(w[44], "trend_heat_7d")) and not np.isnan(H(w[56], "trend_heat_7d"))


def test_day_averages_count_running_time_only():
    w = week_run()
    # At 68 h (day 2, 20:00) the last 24 h hold running minutes from day 1 20:00-22:00 and day 2 06:00-20:00.
    ts = np.concatenate([np.arange(DAY + 20 * 3600, DAY + 22 * 3600), np.arange(2 * DAY + 6 * 3600, 2 * DAY + 20 * 3600)])
    expected = float(np.mean(10 + 3 * ts / DAY))
    got = H(w[68], "trend_heat_24h")
    assert abs(got - expected) < 0.2, (got, expected)  # off-time (2 degC) would drag this far lower


def test_rise_per_hour_and_per_day():
    w = week_run()
    assert abs(H(w[68], "trend_heat_rise_per_day") - 3.0) < 0.4
    assert abs(H(w[8], "trend_heat_rise_per_h") - 3 / 24) < 0.05  # an hour fully inside running time


def test_finds_which_image_cell_is_slowly_heating():
    w = week_run()
    assert H(w[68], "trend_cell_rise_24h_vs_7d_at") == 30 and H(w[68], "trend_cell_rise_24h_vs_7d") > 0.5


def test_finds_the_area_that_went_cold_but_not_the_whole_machine_cooling():
    """The whole image swings 6 degC with load all day; at 24 h one fan stops and its area cools by 6 degC."""
    h, seen = History(), {}
    for t in range(int(25.5 * 3600) + 1):
        x = row(10.0, -1.5)
        x[CELLS] = 5 + 3 * math.sin(2 * math.pi * t / (6 * 3600))
        if t >= 24 * 3600:
            x[CELLS[12]] -= 6.0
        out = h.update(F.Window(t0=float(t)), x)
        if t in (int(23.5 * 3600), int(25.5 * 3600)):
            seen[t] = out
    before, after = seen[int(23.5 * 3600)], seen[int(25.5 * 3600)]
    assert H(before, "trend_cell_fall_1h_vs_24h") < 0.5  # the load swing moves every cell together
    assert H(after, "trend_cell_fall_1h_vs_24h_at") == 12 and H(after, "trend_cell_fall_1h_vs_24h") > 4


def test_four_week_trends_count_running_time_and_survive_a_restart():
    """22 days, one window every 2 minutes. The machine runs 06:00-22:00; while running, heat and the
    hottest point of image cell 30 rise 1 degC a week, and from day 18 the area of image cell 12 runs
    3 degC colder. The history is saved and reloaded on day 15."""
    week, step = 7 * DAY, 120

    def x_at(t):
        running = 6 <= (t % DAY) / 3600 < 22
        x = row(10 + t / week if running else 2.0, -1.5 if running else -2.5)
        x[SPOTS[30]] = t / week if running else 0.0
        x[CELLS] = 4.0 if running else 0.0
        if running and t >= 18 * DAY:
            x[CELLS[12]] = 1.0
        return x

    h, seen = History(), {}
    with tempfile.TemporaryDirectory() as d:
        for t in range(0, 22 * DAY, step):
            out = h.update(F.Window(t0=float(t)), x_at(t))
            if t in (int(13.5 * DAY), 15 * DAY, 22 * DAY - step):
                seen[t] = out
            if t == 15 * DAY:
                h.save(Path(d) / "history.npz")
                h = History.load(Path(d) / "history.npz")
    early, after_load, last = seen[int(13.5 * DAY)], seen[15 * DAY], seen[22 * DAY - step]
    assert np.isnan(H(early, "trend_heat_4w"))  # under 2 weeks of history
    assert not np.isnan(H(after_load, "trend_heat_4w"))
    assert abs(H(last, "trend_heat_rise_per_week") - 1.0) < 0.1
    ts = np.array([t for t in range(0, 22 * DAY - 2 * 3600, step) if 6 <= (t % DAY) / 3600 < 22])
    assert abs(H(last, "trend_heat_4w") - float(np.mean(10 + ts / week))) < 0.15  # off-time (2 degC) left out
    assert H(last, "trend_cell_rise_7d_vs_4w_at") == 30 and H(last, "trend_cell_rise_7d_vs_4w") > 0.8
    assert H(last, "trend_cell_fall_7d_vs_4w_at") == 12 and H(last, "trend_cell_fall_7d_vs_4w") > 0.8


def test_minutes_since_start():
    w = week_run()
    assert abs(H(w[7], "trend_run_minutes") - 60) < 3  # the machine started at 06:00
    assert np.isnan(H(w[23], "trend_run_minutes"))  # stopped at 22:00
    assert H(w[7], "trend_running") == 1 and H(w[23], "trend_running") == 0


def test_history_survives_a_restart_and_resets_on_a_clock_jump():
    h = week_run()["history"]
    with tempfile.TemporaryDirectory() as d:
        h.save(Path(d) / "history.npz")
        again = History.load(Path(d) / "history.npz")
        t = 3 * DAY + 8 * 3600
        out = None
        for k in range(120):  # two minutes after the restart
            out = again.update(F.Window(t0=float(t + k)), row(19.0, -1.5, 9.0))
        assert not np.isnan(H(out, "trend_heat_24h")) and not np.isnan(H(out, "trend_heat_7d"))
        again.update(F.Window(t0=100.0), row(10.0, -1.5))  # device clock went back to near zero
        assert again.reset_reason and "backwards" in again.reset_reason


def test_a_morning_start_is_not_a_bang_but_a_bang_while_running_is():
    h, t = History(), 0

    def feed(n, running, peak, level):
        nonlocal t
        for k in range(n):
            x = row(10.0 if running else 2.0, -1.5 if running else -2.5)
            x[F.INDEX["snd_peak_db"]], x[F.INDEX["snd_level_db"]] = peak(k), level(k)
            h.update(F.Window(t0=float(t)), x)
            t += 1

    feed(900, True, lambda k: 80.0, lambda k: 72.0)  # the evening run
    feed(1800, False, lambda k: 40.0, lambda k: 35.0)  # a silent night
    start = t  # soft start: still quiet when the first knock comes, 38 dB over the silent night
    feed(120, True, lambda k: 78.0 if k == 3 else (50.0 if k < 8 else 80.0), lambda k: 50.0 if k < 8 else 72.0)
    assert not [s for s in h.bangs if s >= start]
    bang = t + 30
    feed(60, True, lambda k: 100.0 if k == 30 else 80.0, lambda k: 72.0)
    assert [s for s in h.bangs if s >= start] == [bang]


def test_history_resumes_exactly_after_a_short_restart():
    import copy
    h = week_run()["history"]
    t, x = 3 * DAY, row(2.0, -2.5)  # the second after the run ended
    uninterrupted = copy.deepcopy(h).update(F.Window(t0=float(t)), x)
    with tempfile.TemporaryDirectory() as d:
        h.save(Path(d) / "history.npz")
        resumed = History.load(Path(d) / "history.npz").update(F.Window(t0=float(t)), x)
        late = History.load(Path(d) / "history.npz").update(F.Window(t0=float(t + 3600)), x)
    assert not np.isnan(H(resumed, "trend_heat_24h"))
    assert np.array_equal(resumed, uninterrupted, equal_nan=True)  # the first row after it is the same
    assert np.isnan(H(late, "trend_heat_24h"))  # an hour lost: wait for the next minute's results


def test_history_features_match_at_one_and_four_windows_a_second():
    def rows(hop):
        sim = ConveyorSim(days=1, start_s=10 * 3600, end_s=10 * 3600 + 360)
        return {round(t, 3): x for t, x in E.feature_stream(sim, hop_s=hop)}

    one, four = rows(1.0), rows(0.25)
    assert len(four) > 3.9 * len(one)
    for t, x in one.items():
        assert np.allclose(x, four[t], equal_nan=True), t
    last = one[max(one)]
    assert not np.isnan(last[F.INDEX["knock_period_s"]])  # 6 minutes is enough for knocks


# ----------------------------------------------------------------- conveyor

def last_row(start_s, seconds):
    sim = ConveyorSim(days=7, start_s=start_s, end_s=start_s + seconds)
    return list(E.feature_stream(sim, hop_s=1.0))[-1][1], sim


def test_slow_vibration_sees_the_pulley_and_the_gearbox_shaft():
    x, _ = last_row(10 * 3600, 30)
    peaks = sorted([x[F.INDEX["slow_peak1_hz"]], x[F.INDEX["slow_peak2_hz"]]])
    pulley, shaft = 29.2 / 20, 29.2 / 4  # 1.46 Hz and 7.3 Hz
    assert abs(peaks[0] - pulley) < 0.2 and abs(peaks[1] - shaft) < 0.4, peaks


def test_damaged_splice_knocks_once_per_belt_lap():
    damaged, sim = last_row(5 * DAY + 8 * 3600, 360)  # Saturday, splice damaged
    normal, _ = last_row(10 * 3600, 360)  # Monday
    lap = 60 / (math.pi * 0.5 * sim.speed[int(5 * DAY + 8 * 3600 + 300)] / 20)
    assert abs(damaged[F.INDEX["knock_period_s"]] - lap) <= 2, (damaged[F.INDEX["knock_period_s"]], lap)
    assert damaged[F.INDEX["knock_repeat"]] > normal[F.INDEX["knock_repeat"]] + 0.2


def test_belt_rub_squeals_and_heats_the_belt_edge():
    start = 4 * DAY + 10 * 3600  # Friday 10:00
    before, sim = last_row(start - 120, 60)
    during, _ = last_row(start + 540, 60)
    assert sim.label_at(start + 600) == "idler_bearing+belt_rub"
    assert abs(during[F.INDEX["snd_tone_hz"]] - 2800) < 60
    assert during[F.INDEX["th_cell_37"]] - before[F.INDEX["th_cell_37"]] > 2.0


def test_recorder_runs_on_the_simulator_from_the_command_line():
    with tempfile.TemporaryDirectory() as out:
        assert Rec.main(["--sim", "conveyor", "--from", "0:09:00", "--to", "0:09:03", "--out", out, "--hop", "1"]) == 0
        assert (Path(out) / "features" / "features_00009.npz").exists()
        assert (Path(out) / "history.npz").exists()


# ----------------------------------------------------------------- storage

def test_storage_cleanup_goes_in_order():
    rng = np.random.default_rng(0)
    with tempfile.TemporaryDirectory() as d:
        out = Path(d)
        (out / "features").mkdir()
        (out / "raw").mkdir()
        for h in range(4):
            np.savez(out / "features" / f"features_{h:05d}.npz", X=rng.random((3600, 20), np.float32),
                     t0=h * 3600 + np.arange(3600.0), minute_mean=rng.random((60, 20)))
        snippet_times = list(range(0, 4 * 3600, 600))
        for t in snippet_times:
            np.savez(out / "raw" / f"clip_{t:09d}.npz", data=rng.random(12_500))
        (out / "events.jsonl").write_text(json.dumps({"t": 4000.0, "event": "sensor_lost"}) + "\n")
        protected = {0, 3600, 4200, 7200, 10800}  # first of each hour, and near the event at 4000 s
        events = []
        sm = StorageManager(out, max_gb=1.0, min_free_gb=0.0, keep_recent_hours=1, thin_every_s=10,
                            event=lambda t, name, **kw: events.append(name), disk_free=lambda: 10 ** 15)
        size = lambda t: (out / "raw" / f"clip_{t:09d}.npz").stat().st_size
        quiet = [t for t in snippet_times if t not in protected]

        sm.max_bytes = sm.used() - sum(size(t) for t in quiet[:3]) + 1
        sm.check(0)
        left = sorted(int(p.stem.split("_")[1]) for p in (out / "raw").glob("clip_*.npz"))
        assert left == [t for t in snippet_times if t not in quiet[:3]]  # the 3 oldest quiet ones went first

        sm.max_bytes = sm.used() - sum(size(t) for t in quiet[3:]) - 1
        sm.check(1)
        assert sorted(int(p.stem.split("_")[1]) for p in (out / "raw").glob("clip_*.npz")) == sorted(protected)
        h0 = np.load(out / "features" / "features_00000.npz")
        assert float(h0["thinned_every_s"]) == 10 and len(h0["X"]) == 360  # oldest hour thinned next
        assert "thinned_every_s" not in np.load(out / "features" / "features_00003.npz").files  # newest kept

        sm.max_bytes = 1
        sm.check(2)
        assert not list((out / "raw").glob("clip_*.npz")) and sm.paused
        assert "recording_paused" in events

        sm.max_bytes = 10 ** 12
        sm.check(3)
        assert not sm.paused and "recording_resumed" in events


def test_storage_logs_each_step_once_an_hour():
    rng = np.random.default_rng(1)
    with tempfile.TemporaryDirectory() as d:
        out = Path(d)
        (out / "features").mkdir()
        (out / "raw").mkdir()
        logged = []
        sm = StorageManager(out, max_gb=1.0, min_free_gb=0.0, disk_free=lambda: 10 ** 15,
                            event=lambda t, name, **kw: logged.append((t, name, kw)))
        deleted = 0
        for k in range(30):  # a check every 5 minutes for 2.5 hours, each over budget by one snippet
            t = 4 * 3600 + 300 * k
            clip_t = 3600 * k + 1800  # mid-hour, so never the protected first snippet of an hour
            np.savez(out / "raw" / f"clip_{clip_t:09d}.npz", data=rng.random(12_500))
            sm.max_bytes = sm.used() - 1
            deleted += sum(a.startswith("deleted") for a in sm.check(t))
        sm.flush(4 * 3600 + 300 * 30)
        lines = [kw for _, name, kw in logged if name == "storage_cleanup"]
        assert deleted == 30 and sum(kw["files"] for kw in lines) == 30
        assert 3 <= len(lines) <= 4  # the first time, then once an hour, then the rest when stopping
        assert all(kw["step"] == "quiet raw snippet" for kw in lines)


# ----------------------------------------------------------------- sudden events

JAM = 3 * DAY + 15 * 3600 + 20 * 60  # Thursday 15:20


def test_jam_is_a_jolt_a_bang_and_a_stop():
    sim = ConveyorSim(days=7, start_s=JAM - 660, end_s=JAM + 30)
    rows = {round(t): x for t, x in E.feature_stream(sim, hop_s=1.0)}
    before, at, after = rows[JAM - 30], rows[JAM], rows[JAM + 5]
    assert 10 ** at[F.INDEX["vib_peak_g"]] > 1.0 > 10 * 10 ** before[F.INDEX["vib_peak_g"]]
    assert at[F.INDEX["gyr_peak"]] - before[F.INDEX["gyr_peak"]] > 1.5
    assert at[F.INDEX["snd_peak_db"]] - before[F.INDEX["snd_peak_db"]] > 15
    assert abs(rows[JAM - 2][F.INDEX["snd_tone_hz"]] - 1900) < 30  # the belt slips and squeals first
    assert after[F.INDEX["vib_speed_hz"]] == 0
    assert after[F.INDEX["trend_shocks_10m"]] == 1 and after[F.INDEX["trend_since_shock_s"]] == 4
    assert not np.isnan(after[F.INDEX["trend_since_bang_s"]])


def test_recorder_keeps_raw_data_from_before_a_sudden_event():
    with tempfile.TemporaryDirectory() as out:
        sim = ConveyorSim(days=7, start_s=JAM - 300, end_s=JAM + 60)
        rec = Rec.Recorder(out, lambda: sim.info, sim.sensors, hop_s=1.0, snippet_every_s=0)
        for t0, x in E.feature_stream(sim, hop_s=1.0, tap=rec.on_packet):
            rec.on_window(t0, x)
        rec.close()
        events = [json.loads(line) for line in open(Path(out) / "events.jsonl")]
        jolt = [e for e in events if e["event"] == "sudden_vibration"]
        assert jolt and jolt[0]["captured"] and abs(jolt[0]["t"] - JAM) <= 1 and jolt[0]["peak_g"] > 1
        assert any(e["event"] == "sudden_sound" for e in events)
        clip = np.load(Path(out) / "raw" / jolt[0]["clip"])
        t = clip["accel_t_us"] / 1e6
        assert t[0] <= JAM - 3.5 and t[-1] >= JAM + 5  # from before the jolt to after it
        assert np.abs(clip["samples"] / clip["accel_lsb_per_g"]).max() > 1.5  # the jolt itself is in it


def test_recorder_logs_beeping_and_keeps_its_sound():
    from effente.simulate_server import ServerRackSim
    with tempfile.TemporaryDirectory() as out:
        sim = ServerRackSim(start_s=10 * 3600 - 90, end_s=10 * 3600 + 20)  # the UPS alarm starts at 10:00
        rec = Rec.Recorder(out, lambda: sim.info, sim.sensors, hop_s=1.0, snippet_every_s=0)
        for t0, x in E.feature_stream(sim, hop_s=1.0, tap=rec.on_packet):
            rec.on_window(t0, x)
        rec.close()
        events = [json.loads(line) for line in open(Path(out) / "events.jsonl")]
        beeps = [e for e in events if e["event"] == "beeping"]
        assert len(beeps) == 1 and beeps[0]["captured"] and abs(beeps[0]["tone_hz"] - 2000) < 10
        assert 10 * 3600 <= beeps[0]["t"] <= 10 * 3600 + 6
        assert (Path(out) / "raw" / beeps[0]["clip"]).exists()


def test_sudden_heat_needs_a_lasting_jump_above_body_heat():
    with tempfile.TemporaryDirectory() as out:
        rec = Rec.Recorder(out, lambda: SourceInfo(), ("thermal",), hop_s=1.0, snippet_every_s=0)
        x = np.full(F.N_FEATURES, np.nan)
        for k in range(200):
            x[F.INDEX["th_max_c"]] = 34.0 if 90 <= k < 95 else (70.0 if k >= 150 else 30.0)  # a person, then an arc
            x[F.INDEX["th_hot_row"]], x[F.INDEX["th_hot_col"]] = 5.0, 9.0
            rec.on_window(float(k), x.copy())
        rec.close()
        heat = [json.loads(line) for line in open(Path(out) / "events.jsonl")]
        heat = [e for e in heat if e["event"] == "sudden_heat"]
        assert len(heat) == 1 and heat[0]["t"] == 150 and heat[0]["rise_c"] == 40.0


def test_data_quality_events():
    with tempfile.TemporaryDirectory() as out:
        rec = Rec.Recorder(out, lambda: SourceInfo(), ("accel", "thermal"), hop_s=1.0, snippet_every_s=0)
        x = np.full(F.N_FEATURES, np.nan)
        for k in range(60):
            x[[F.INDEX[f"vib_rms_{a}"] for a in "xyz"]] = -2.0 if k < 15 else -6.0  # goes dead after 15 s
            x[F.INDEX["vib_clip_frac"]] = 0.0
            x[F.INDEX["th_frozen"]] = 1.0  # the camera is frozen throughout
            rec.on_window(float(k), x.copy())
        rec.close()
        events = [(e["event"], e.get("sensor")) for e in map(json.loads, open(Path(out) / "events.jsonl"))]
        assert ("sensor_stuck", "heat") in events and ("sensor_stuck", "vibration") in events


def test_dew_point_and_condensation_margin():
    assert abs(F.dew_point(20.0, 50.0) - 9.3) < 0.2  # standard table value
    frames = np.full((4, 24, 32), 20.0)
    frames[:, 0, 0] = 8.0  # a cold pipe below the dew point
    x = F.extract(F.Window(0.0, thermal_c=frames, room=np.array([[20.0, 50.0]])))
    assert x[F.INDEX["env_condensation_margin_c"]] < 0


# ----------------------------------------------------------------- data center

def server_rows(start, secs=20):
    from effente.simulate_server import ServerRackSim
    sim = ServerRackSim(start_s=start, end_s=start + secs)
    return [x for _, x in E.feature_stream(sim, hop_s=1.0)]


def med(rows, name):
    return float(np.median([x[F.INDEX[name]] for x in rows]))


def test_server_ups_alarm_is_a_beeping_tone():
    normal, alarm = server_rows(9 * 3600), server_rows(10 * 3600 + 60)
    assert abs(med(alarm, "snd_tone_hz") - 2000) < 5
    assert med(alarm, "snd_tone_mod_db") - med(normal, "snd_tone_mod_db") > 20


def test_server_rack_bump_is_a_jolt():
    rows = server_rows(16 * 3600 - 5, 10)
    assert 10 ** max(x[F.INDEX["vib_peak_g"]] for x in rows) > 0.3
    assert max(x[F.INDEX["snd_peak_db"]] for x in rows) - med(rows, "snd_peak_db") > 20


def test_server_fan_failure_and_cooling_failure_show_in_heat_and_sound():
    from effente.simulate_server import FAN5_CELLS
    cell = f"th_cell_{FAN5_CELLS[0] // 8}{FAN5_CELLS[0] % 8}"
    before, after = server_rows(2 * DAY + 13 * 3600 + 3000), server_rows(2 * DAY + 14 * 3600 + 600)
    assert med(before, cell) - med(after, cell) > 3  # no airflow through fan 5's grille
    assert med(after, "snd_level_db") - med(before, "snd_level_db") > 1.5  # the others speed up
    hot_room = server_rows(2 * DAY + 22 * 3600)
    assert med(hot_room, "env_room_c") - med(before, "env_room_c") > 5


def test_server_connector_heat_shows_in_the_hottest_point_of_its_cell():
    from effente.simulate_server import CONNECTOR_CELLS, ServerRackSim, default_faults
    t = 2 * DAY + 21 * 3600  # Wednesday 21:00, near the end of the connector's heating
    rows = lambda faults: [x for _, x in E.feature_stream(
        ServerRackSim(faults=faults, start_s=t, end_s=t + 20), hop_s=1.0)]
    hot, cool = rows(None), rows([f for f in default_faults() if f.kind != "connector_heat"])
    spot = [f"th_spot_{c // 8}{c % 8}" for c in CONNECTOR_CELLS]
    cell = [f"th_cell_{c // 8}{c % 8}" for c in CONNECTOR_CELLS]
    rise_spot = max(med(hot, n) - med(cool, n) for n in spot)
    rise_cell = max(med(hot, n) - med(cool, n) for n in cell)
    assert rise_spot > 6 and rise_spot > 3 * rise_cell  # the average of the cell dilutes a small part


def test_swing_sees_a_cycling_temperature_but_not_a_single_spike():
    """Heat cycles 2 degC either way every 5 minutes; separately, steady heat with one 30 degC spike."""
    def run(heat_at):
        h = History()
        for t in range(int(1.2 * 3600)):
            out = h.update(F.Window(t0=float(t)), row(heat_at(t), -1.5))
        return out
    cycling = run(lambda t: 10 + 2 * math.sin(2 * math.pi * t / 300))
    spike = run(lambda t: 40.0 if t == 4000 else 10.0)
    assert abs(H(cycling, "trend_heat_swing_10m") - 2 * 2 * math.sin(0.4 * math.pi)) < 0.2  # 10th-90th of a sine
    assert H(spike, "trend_heat_swing_10m") < 0.01 and H(spike, "trend_heat_swing_1h") < 0.01


def test_site_noise_is_reproducible_and_labels_what_is_going_on():
    from effente.site_noise import SiteNoise
    a = SiteNoise(ConveyorSim(days=7, start_s=8 * 3600, end_s=8 * 3600 + 20), "factory", seed=4)
    b = SiteNoise(ConveyorSim(days=7, start_s=8 * 3600, end_s=8 * 3600 + 20), "factory", seed=4)
    assert [(e.kind, e.start) for e in a.events] == [(e.kind, e.start) for e in b.events] and len(a.events) > 100
    pa = [p.values for p in a.packets()]
    pb = [p.values for p in b.packets()]
    assert len(pa) == len(pb) and all(np.array_equal(x, y) for x, y in zip(pa, pb))
    e = next(e for e in a.events if e.kind == "people_talking")
    assert "people_talking" in a.nuisance_at(e.start + 1) and a.label_at(8 * 3600) == "normal"
    quiet = [ev for ev in a.events if ev.kind == "dropped_object" and a.activity(ev.start) < 1]
    busy = [ev for ev in a.events if ev.kind == "dropped_object" and a.activity(ev.start) == 1]
    assert len(busy) > len(quiet)  # people drop things during working hours


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
