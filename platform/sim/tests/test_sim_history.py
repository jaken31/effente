"""The history command (contract 9.6, plan 15.4 S2): the plan it builds, its checks, and runs of
history.py against the real receiver."""

import os
import statistics
import subprocess
import sys
from datetime import datetime, timezone

import pytest

import sim_history as h
import sim_model
from sim_link import Link

SIM_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HISTORY = os.path.join(SIM_DIR, "history.py")
SERVER_DIR = os.path.join(os.path.dirname(SIM_DIR), "server")
NOW = datetime(2026, 11, 14, 18, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def plan():
    return h.build_plan(NOW, seed=1)


def visits(plan, name):
    """Each visit of a part as (start, median score, median velocity), oldest first."""
    rows = [r for r in plan.records if r["part_id"] == name]
    out = []
    for i in range(0, len(rows), h.VISIT_RECORDS):
        v = rows[i:i + h.VISIT_RECORDS]
        out.append((sim_model.parse_iso(v[0]["at"]), statistics.median(r["unknown_score"] for r in v),
                    statistics.median(r["readings"]["vib_velocity_mm_s"] for r in v)))
    return out


# --------------------------------------------------------------- the plan

def test_every_record_is_simulated_and_for_the_sample_machine(plan):
    assert plan.records
    for r in plan.records:
        assert r["machine_id"] == "sample-machine" and r["device_id"] == "sim-history"
        assert r["simulated"] is True and r["part_id"] in h.PART_NAMES
        assert r["diagnosis"] == "normal" and r["wrong"] is False and r["problems"] == []
        assert 0 < r["unknown_score"] < r["alarm_line"]  # stays under the line: no alarm is made
        assert len(r["spectrum"]) == 20 and r["readings"]["vib_velocity_mm_s"] > 0
        assert sim_model.parse_iso(r["at"]) < NOW
    assert all(e["simulated"] is True and e["by"] == "simulator" for e in plan.entries)


def test_records_go_oldest_first_in_batches_of_at_most_500(plan):
    ats = [r["at"] for r in plan.records]
    assert ats == sorted(ats)
    sent = h.batches(plan.records)
    assert all(len(b) <= 500 for b in sent) and sum(sent, []) == plan.records


def test_visits_are_short_and_a_day_apart(plan):
    dr = visits(plan, "Drive roller")
    assert len(dr) == h.DAYS == len(visits(plan, "Belt"))
    rows = [r for r in plan.records if r["part_id"] == "Drive roller"][: h.VISIT_RECORDS + 1]
    gaps = [(sim_model.parse_iso(b["at"]) - sim_model.parse_iso(a["at"])).total_seconds() for a, b in zip(rows, rows[1:])]
    assert set(gaps[:-1]) == {1.0} and gaps[-1] > 600  # a second apart inside a visit, over 10 minutes between
    assert len(visits(plan, "Motor mount")) == 1
    assert visits(plan, "Idle roller") == []


def test_the_drive_roller_rises_is_repaired_and_rises_again(plan):
    finding, repair = plan.entries[1], plan.entries[2]
    assert finding["kind"] == "finding" and finding["needs_repair"] is True and finding["part_id"] == "Drive roller"
    assert repair["kind"] == "repair" and repair["part_id"] == "Drive roller" and repair["at"] > finding["at"]
    cut = sim_model.parse_iso(repair["at"])
    before = [v for v in visits(plan, "Drive roller") if v[0] < cut]
    after = [v for v in visits(plan, "Drive roller") if v[0] > cut]
    assert before[-1][1] > before[0][1] + 0.4 and before[-1][2] > before[0][2] + 2  # score and velocity rose
    assert after[0][1] < before[-1][1] - 0.4  # the repair brought it down
    assert after[-1][1] > after[0][1] + 0.2  # and it rises again


def _slope_range(points):
    """Least squares slope and its 80% range, as contract 9.5 describes it, for checking this data only."""
    t0 = points[0][0]
    xs = [(t - t0).total_seconds() / 86400 for t, _ in points]
    ys = [y for _, y in points]
    n, mx, my = len(xs), statistics.mean(xs), statistics.mean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    resid = sum((y - (my + slope * (x - mx))) ** 2 for x, y in zip(xs, ys))
    se = (resid / (n - 2) / sxx) ** 0.5
    t80 = {12: 1.356, 13: 1.350}[n - 2]  # Student's t, 80% two-sided
    return slope - t80 * se, slope + t80 * se


def test_the_data_gives_each_shape_its_status(plan):
    window = lambda name: [(t, s) for t, s, _ in visits(plan, name)][-14:]  # noqa: E731  the last 14 days
    lo, hi = _slope_range(window("Drive roller"))
    assert lo > 0  # rising
    lo, hi = _slope_range(window("Belt"))
    assert lo <= 0  # level: no clear rise, or not rising
    assert len({t.date() for t, _, _ in visits(plan, "Motor mount")}) < 3  # not enough history


def test_the_plan_is_repeatable():
    assert h.build_plan(NOW, 1).records == h.build_plan(NOW, 1).records


# --------------------------------------------------------------- the checks

SIM = {"machine_id": "sample-machine", "simulated": True, "at": "2026-11-01T09:00:00Z"}


def test_judge_lets_an_empty_sample_machine_through():
    assert h.judge({"alarms": {}, "devices": {}}, [], []).go


def test_judge_refuses_a_real_record():
    v = h.judge({}, [SIM, dict(SIM, simulated=False)], [])
    assert not v.go and v.code == h.REFUSED and "not simulated" in v.message
    assert not h.judge({}, [{k: x for k, x in SIM.items() if k != "simulated"}], []).go  # missing counts as real


def test_judge_refuses_an_open_alarm_or_a_connected_device():
    alarm = {"alarm_id": "alarm-1", "machine_id": "sample-machine", "open": True}
    v = h.judge({"alarms": {"alarm-1": alarm}}, [], [])
    assert not v.go and v.code == h.REFUSED and "alarm-1" in v.message
    assert h.judge({"alarms": {"alarm-1": dict(alarm, open=False)}}, [], []).go
    assert h.judge({"alarms": {"alarm-1": dict(alarm, machine_id="conveyor-07")}}, [], []).go
    device = {"machine_id": "sample-machine", "connected": True}
    v = h.judge({"devices": {"effente-sim-01": device}}, [], [])
    assert not v.go and v.code == h.REFUSED and "effente-sim-01" in v.message
    assert h.judge({"devices": {"effente-sim-01": dict(device, connected=False)}}, [], []).go


def test_judge_does_not_write_history_twice():
    v = h.judge({}, [SIM] * 3, [])
    assert not v.go and v.code == h.NOTHING_TO_DO and "already" in v.message


def test_judge_refuses_parts_it_did_not_make_and_reuses_its_own():
    ours = [{"name": n, "simulated": True} for n in h.PART_NAMES[:2]]
    assert h.judge({}, [], ours).go  # a run stopped after making parts
    assert not h.judge({}, [], ours + [{"name": "Gearbox", "simulated": True}]).go
    assert not h.judge({}, [], [{"name": "Belt"}]).go  # not simulated: a person made it


def test_it_takes_no_machine_id():
    p = subprocess.run([sys.executable, HISTORY, "--machine", "conveyor-07", "--yes"],
                       capture_output=True, text=True, timeout=30)
    assert p.returncode == 2 and "unrecognized arguments: --machine" in p.stderr


# --------------------------------------------------------------- against the real receiver

@pytest.fixture
def receiver(tmp_path):
    if SERVER_DIR not in sys.path:
        sys.path.insert(0, SERVER_DIR)
    from receiver_server import Receiver

    r = Receiver("127.0.0.1", 0, str(tmp_path / "data"), str(tmp_path / "web"), log=lambda m: None)
    r.start()
    yield f"http://127.0.0.1:{r.port}"
    r.stop()


def run(url, *args):
    return subprocess.run([sys.executable, HISTORY, "--url", url, *args], stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=120)


def get(url, path):
    res = Link(url, "test", timeout=10).call("GET", path)
    assert res.reachable, res.error
    return res


def test_without_yes_and_no_terminal_it_writes_nothing(receiver):
    p = run(receiver)
    assert p.returncode == 2
    assert "About to write simulated history to " + receiver in p.stdout and "no undo" in p.stdout
    assert "Nothing written" in p.stdout
    assert get(receiver, "/api/state").body["machines"] == []


def test_a_run_writes_only_simulated_history_to_the_sample_machine_once(receiver):
    p = run(receiver, "--yes")
    assert p.returncode == 0, p.stdout + p.stderr
    plan = h.build_plan(datetime.now(timezone.utc))
    state = get(receiver, "/api/state").body
    assert state["machines"] == ["sample-machine"]
    records = h.read_records(Link(receiver, "test", timeout=10))
    assert len(records) == len(plan.records)
    assert all(r["simulated"] is True and r["machine_id"] == "sample-machine" for r in records)
    parts = get(receiver, "/api/machines/sample-machine/parts").body["parts"]
    assert [p["name"] for p in parts] == h.PART_NAMES and all(p["simulated"] is True for p in parts)
    ids = {p["part_id"] for p in parts}
    assert {r["part_id"] for r in records} == ids - {parts[-1]["part_id"]}  # the idle roller has none
    entries = get(receiver, "/api/machines/sample-machine/log").body["entries"]
    assert len(entries) == 3 and all(e["simulated"] is True for e in entries)

    again = run(receiver, "--yes")
    assert again.returncode == 0 and "already has simulated history" in again.stdout
    assert len(h.read_records(Link(receiver, "test", timeout=10))) == len(records)
    assert len(get(receiver, "/api/machines/sample-machine/parts").body["parts"]) == 4
    assert len(get(receiver, "/api/machines/sample-machine/log").body["entries"]) == 3


def test_the_readings_it_makes_are_simulated_and_shaped(receiver):
    assert run(receiver, "--yes").returncode == 0
    parts = {p["name"]: p for p in get(receiver, "/api/machines/sample-machine/parts").body["parts"]}
    res = get(receiver, f"/api/parts/{parts['Drive roller']['part_id']}/readings")
    if not res.ok and "not built yet" in res.error:
        pytest.skip("the readings route (contract 9.3) is not built yet")
    replies = {n: get(receiver, f"/api/parts/{p['part_id']}/readings").body for n, p in parts.items()}
    for name, reply in replies.items():
        assert all(r["simulated"] is True for r in reply["readings"]), name
        if reply["readings"]:
            assert reply["summary"]["simulated"] is True and reply["projection"]["simulated"] is True, name
            assert parts[name]["reading"]["simulated"] is True, name
    assert replies["Drive roller"]["projection"]["status"] == "rising"
    assert replies["Belt"]["projection"]["status"] in ("no_clear_rise", "not_rising")
    assert len(replies["Motor mount"]["readings"]) == 1
    assert replies["Motor mount"]["projection"]["status"] == "not_enough_history"
    assert replies["Idle roller"]["readings"] == [] and replies["Idle roller"]["summary"]["count"] == 0
