import random

import pytest

import sim_model as m
from sim_fake_platform import check_in_problems, record_problems

EXPECTED = {
    "normal": ("normal", ""),
    "unbalance": ("unbalance_like", "once-per-turn vibration grown (unbalance, a bent shaft, a lost blade)"),
    "looseness": ("looseness_or_rattle",
                  "many harmonics, half-order and rattling (looseness, a soft foot, a cracked mount)"),
    "unrecognised": ("unrecognised_pattern", "abnormal, no known fault type fits"),
    "stopped": ("stopped", ""),
    "warming_up": ("warming_up", ""),
}
WINDOWS = 200  # records checked per case


@pytest.mark.parametrize("case", m.CASES)
@pytest.mark.parametrize("with_problem", [False, True], ids=["no-problem", "too-hot"])
def test_case_gives_expected_diagnosis_and_valid_record(case, with_problem):
    rng = random.Random(1)
    for _ in range(WINDOWS):
        problems = [m.make_problem("too_hot", rng, "2026-11-14T16:00:00Z")] if with_problem else []
        r = m.make_record(case, rng, problems)
        assert (r["diagnosis"], r["reason"]) == EXPECTED[case]
        assert r["model_version"] == "sim-3" and r["simulated"] is True
        assert r["device_id"] == "effente-sim-01" and r["machine_id"] == "demo-fan-1"
        assert record_problems(r) == []
        assert set(r["features"]) <= set(m.FEATURE_NAMES)
        assert set(r.get("fault_types", {})) <= {t["name"] for t in m.FAULT_TYPES}
        assert r["wrong"] is (with_problem or case in m.ABNORMAL_CASES)
        assert r["at"].endswith("Z")


def test_contract_check_can_fail():
    good = m.make_record("normal", random.Random(1))
    assert record_problems(dict(good, wrong=True))  # normal with no problems is not wrong
    assert record_problems(dict(good, spectrum=[0.0] * 64))
    assert record_problems(dict(good, problems=[{"name": "too_hot"}]))
    assert record_problems({k: v for k, v in good.items() if k != "problems"})
    assert record_problems(dict(good, reason="something"))


def test_problem_while_normal_keeps_the_diagnosis():
    rng_a, rng_b = random.Random(5), random.Random(5)
    plain = m.make_record("normal", rng_a, [], at="t")
    hot = [m.make_problem("too_hot", random.Random(0), "2026-11-14T16:00:00Z")]
    with_problem = m.make_record("normal", rng_b, hot, at="t")
    assert with_problem["diagnosis"] == "normal" and with_problem["wrong"] is True
    assert with_problem["problems"] == hot
    assert {**with_problem, "problems": [], "wrong": False} == plain


def test_problems_follow_the_names_table():
    assert m.PROBLEM_NAMES == [
        "vibration_over_limit", "knocking", "too_hot", "heating_fast", "repeated_jolts",
        "vibration_above_normal", "hotter_than_normal", "louder_than_normal",
    ]
    assert not set(m.PROBLEM_NAMES) & {t["name"] for t in m.FAULT_TYPES}  # never a fault type name
    for name in m.PROBLEM_NAMES:
        p = m.make_problem(name, random.Random(1), "2026-11-14T16:00:00Z")
        assert set(p) == {"name", "kind", "text", "value", "since"}
        assert p["kind"] == ("limit" if m.PROBLEM_NAMES.index(name) < 5 else "outside_normal")
        assert p["text"] and isinstance(p["value"], (int, float))


def test_spectrum_shapes():
    rng = random.Random(2)
    turn = m._band_of(m.TURN_HZ)
    unb = m.make_spectrum("unbalance", rng)
    assert len(unb) == 20 and unb.index(max(unb)) == turn
    unk = m.make_spectrum("unrecognised", rng)
    assert unk.index(max(unk)) == m._band_of(m.UNKNOWN_HZ)
    normal = m.make_spectrum("normal", rng)
    loose = m.make_spectrum("looseness", rng)
    assert sum(loose) / 20 > sum(normal) / 20 + 0.3  # raised broadband
    stopped = m.make_spectrum("stopped", rng)
    assert max(stopped) < min(normal)
    assert len(m.SPECTRUM_EDGES_HZ) == 21 and m.SPECTRUM_EDGES_HZ[0] == 5.0 and m.SPECTRUM_EDGES_HZ[-1] == 500.0


def test_seed_is_repeatable_and_seeds_differ():
    def run(seed):
        rng = random.Random(seed)
        return [m.make_record(c, rng, [], at="t") for c in m.CASES for _ in range(5)]

    assert run(42) == run(42)
    assert run(42) != run(43)


def test_check_in_keeps_only_limits():
    rng = random.Random(1)
    problems = [m.make_problem("too_hot", rng, "s"), m.make_problem("vibration_above_normal", rng, "s")]
    c = m.make_check_in("recording", problems, at="2026-11-14T15:40:02Z")
    assert check_in_problems(c) == []
    assert c["mode"] == "recording" and c["wrong"] is True
    assert [p["name"] for p in c["problems"]] == ["too_hot"]
    quiet = m.make_check_in("display")
    assert quiet["wrong"] is False and quiet["problems"] == [] and check_in_problems(quiet) == []
    with pytest.raises(ValueError):
        m.make_check_in("watching")


def test_problem_checks_hold_then_clear():
    checks = m.ProblemChecks(hold_s=10)
    rng = random.Random(1)
    checks.start("too_hot", 100.0, "2026-11-14T16:00:00Z")
    assert checks.status("too_hot", 105.0) == "starting"
    assert checks.current(m.WATCHING, rng, 105.0) == []
    on = checks.current(m.WATCHING, rng, 110.0)
    assert [p["name"] for p in on] == ["too_hot"] and on[0]["since"] == "2026-11-14T16:00:00Z"
    checks.stop("too_hot", 120.0)
    assert checks.status("too_hot", 125.0) == "clearing"
    assert [p["name"] for p in checks.current(m.WATCHING, rng, 125.0)] == ["too_hot"]  # mostly stopped takes 8 s
    assert checks.current(m.WATCHING, rng, 128.0) == []
    assert checks.status("too_hot", 128.0) == ""


def test_problem_checks_stopped_before_showing_never_show():
    checks = m.ProblemChecks(hold_s=10)
    checks.toggle("too_hot", 0.0, "t")
    checks.toggle("too_hot", 3.0, "t")
    assert checks.status("too_hot", 3.0) == "" and checks.current(m.WATCHING, random.Random(1), 50.0) == []


def test_outside_normal_problems_only_while_watching():
    checks = m.ProblemChecks(hold_s=0)
    checks.start("vibration_above_normal", 0.0, "t")
    checks.start("too_hot", 0.0, "t")
    rng = random.Random(1)
    assert [p["name"] for p in checks.current(m.WATCHING, rng, 1.0)] == ["too_hot", "vibration_above_normal"]
    assert [p["name"] for p in checks.current("recording", rng, 1.0)] == ["too_hot"]


def test_unknown_problem_refused():
    with pytest.raises(ValueError):
        m.ProblemChecks().start("smoke", 0.0, "t")


def test_announcement_and_model_file():
    a = m.model_announcement("dev-x", "fan-x")
    assert a["device_id"] == "dev-x" and a["machine_id"] == "fan-x"
    assert a["model_version"] == "sim-3" and a["simulated"] is True
    assert a["source"] == "calibrated" and a["library_entry"] == "generic_rotating"
    assert [t["name"] for t in a["fault_types"]] == ["vibration_too_high", "unbalance_like", "looseness_or_rattle"]
    assert all(t["validated"] is False and t["text"] and t["group"] == "vibration" for t in a["fault_types"])
    assert all(set(f) == {"name", "text", "unit"} for f in a["features"])
    assert a["spectrum_edges_hz"] == m.SPECTRUM_EDGES_HZ
    f = m.model_file()
    assert f["simulated"] is True and f["model_version"] == "sim-3"
    assert f["features"][0] == {"name": "vib_1x", "unit_indices": [], "sentence": "vibration once per turn"}


# ---- sensitivity (contract section 7) ---------------------------------------


def test_alarm_line_follows_the_contract():
    assert [m.alarm_line(s) for s in (0, 0.25, 0.5, 0.75, 1)] == [4.0, 2.0, 1.0, 0.5, 0.25]


def test_sensitivity_moves_only_the_line():
    for s in (0.0, 0.5, 1.0):
        r = m.make_record("unbalance", random.Random(3), [], sensitivity=s)
        assert (r["sensitivity"], r["alarm_line"]) == (s, m.alarm_line(s))
    # Same case, same readings: only the diagnosis changes with the line.
    low = m.make_record("unbalance", random.Random(3), [], sensitivity=0.0)
    mid = m.make_record("unbalance", random.Random(3), [], sensitivity=0.5)
    assert mid["diagnosis"] == "unbalance_like" and mid["wrong"] is True
    assert low["diagnosis"] == "normal" and low["wrong"] is False and low["abnormal_minutes"] == 0
    assert "fault_types" not in low and "diagnosis_this_minute" not in low and low["reason"] == ""
    assert low["unknown_score"] == mid["unknown_score"] and low["features"] == mid["features"]

    high = m.make_record("normal", random.Random(3), [], sensitivity=1.0)
    assert high["diagnosis"] == "unrecognised_pattern" and high["reason"] == m.UNRECOGNISED_REASON
    assert high["wrong"] is True and high["abnormal_minutes"] == 1
    assert m.make_record("normal", random.Random(3), [], sensitivity=0.5)["diagnosis"] == "normal"


def test_stopped_and_warming_up_ignore_sensitivity():
    for case in ("stopped", "warming_up"):
        for s in (0.0, 1.0):
            assert m.make_record(case, random.Random(1), [], sensitivity=s)["diagnosis"] == m.DIAGNOSIS[case]


def test_outside_normal_problems_follow_the_margin_and_limits_do_not():
    checks = m.ProblemChecks(hold_s=0)
    checks.start("too_hot", 0.0, "t0")
    checks.start("vibration_above_normal", 0.0, "t0")

    def names(s):
        return [p["name"] for p in checks.current(m.WATCHING, random.Random(1), 1.0, s)]

    assert names(0.5) == ["too_hot", "vibration_above_normal"]
    assert names(0.0) == ["too_hot"]  # 2.3 times normal is not beyond 5 times
    assert m.margins(0.5) == {"vibration_above_normal": 2.0, "hotter_than_normal": 15.0, "louder_than_normal": 6.0}


def test_new_sensitivity_restarts_the_outside_normal_hold():
    checks = m.ProblemChecks(hold_s=10)
    checks.start("too_hot", 0.0, "t0")
    checks.start("vibration_above_normal", 0.0, "t0")
    checks.restart_outside_normal(20.0, "t20")
    rng = random.Random(1)
    assert [p["name"] for p in checks.current(m.WATCHING, rng, 25.0)] == ["too_hot"]
    assert [p["name"] for p in checks.current(m.WATCHING, rng, 30.0)] == ["too_hot", "vibration_above_normal"]
