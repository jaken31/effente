"""The normal spectrum as a plain function (contract 9.4)."""

import normal_spectrum as ns


def normal_spectrum(records, *args, **kw):
    return ns.normal_spectrum("conveyor-07", records, *args, **kw)

T0 = 1_800_000_000


def rec(i, level=-4.0, **over):
    r = {"machine_id": "conveyor-07", "at": f"2027-01-15T{8 + i // 3600:02d}:{i // 60 % 60:02d}:{i % 60:02d}Z",
         "seq": i, "diagnosis": "normal", "wrong": False, "model_version": "m1", "simulated": False,
         "spectrum": [level + b / 100 for b in range(20)]}
    r.update(over)
    return r


def many(n, start=0, **over):
    return [rec(start + i, **over) for i in range(n)]


def test_299_records_is_not_ready_and_300_is():
    reply = normal_spectrum(many(299))
    assert (reply["ready"], reply["records"], reply["needed"], reply["spectrum"], reply["from"]) == (False, 299, 300, None, None)
    reply = normal_spectrum(many(300), edges_of=lambda version: [5.0, 6.3] if version == "m1" else None)
    assert (reply["ready"], reply["records"], reply["model_version"]) == (True, 300, "m1")
    assert reply["spectrum"] == [-4.0 + b / 100 for b in range(20)]
    assert (reply["from"], reply["to"]) == (rec(0)["at"], rec(299)["at"])
    assert reply["spectrum_edges_hz"] == [5.0, 6.3]


def test_the_normal_is_the_median_of_each_band_over_the_newest_2000():
    old = many(1000, level=-9.0)
    new = many(2000, start=1000, level=-4.0)
    new[0]["spectrum"] = [99.0] * 20  # one odd record does not move a median
    reply = normal_spectrum(old + new)
    assert reply["records"] == 2000 and reply["spectrum"][0] == -4.0
    assert reply["from"] == rec(1000)["at"]


def test_a_new_model_version_starts_again():
    reply = normal_spectrum(many(400) + many(5, start=400, model_version="m2"))
    assert (reply["ready"], reply["records"], reply["model_version"]) == (False, 5, "m2")


def test_a_record_with_a_problem_or_not_normal_or_no_full_spectrum_is_left_out():
    left_out = [
        rec(300, wrong=True), rec(301, diagnosis="warming_up"), rec(302, diagnosis="unbalance_like"),
        rec(303, spectrum=[1.0] * 19), rec(304, spectrum=None), rec(305, spectrum=[True] * 20),
        rec(306, spectrum=[float("nan")] * 20),
    ]
    left_out[-1].pop("wrong")  # no `wrong` at all
    assert normal_spectrum(many(299) + left_out)["records"] == 299


def test_real_records_push_simulated_ones_out():
    simulated = many(400, simulated=True, level=-2.0)
    reply = normal_spectrum(simulated)
    assert (reply["ready"], reply["simulated"], reply["spectrum"][0]) == (True, True, -2.0)
    reply = normal_spectrum(simulated + many(1, start=400))  # one real record judged normal
    assert (reply["ready"], reply["records"], reply["simulated"]) == (False, 1, False)
    reply = normal_spectrum(simulated + many(300, start=400))
    assert (reply["ready"], reply["simulated"], reply["spectrum"][0]) == (True, False, -4.0)


def test_with_a_part_only_its_records_count():
    roller = many(300, part_id="part-0001", level=-3.0)
    belt = many(300, start=300, part_id="part-0002", level=-5.0)
    loose = many(300, start=600, level=-7.0)
    records = roller + belt + loose
    assert normal_spectrum(records, "part-0001")["spectrum"][0] == -3.0
    assert normal_spectrum(records, "part-0001")["part_id"] == "part-0001"
    assert normal_spectrum(records)["records"] == 900 and normal_spectrum(records)["part_id"] is None
    assert normal_spectrum(records, "part-0003")["records"] == 0


def test_no_records_is_not_ready():
    reply = normal_spectrum([])
    assert (reply["ready"], reply["records"], reply["model_version"], reply["spectrum_edges_hz"], reply["simulated"]) == (
        False, 0, None, None, False)
