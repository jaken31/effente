"""The normal spectrum route (contract 9.4), over HTTP."""

from receiver_testkit import MODEL, make_record
from test_receiver_probe import ok, rig  # noqa: F401  (rig is a fixture)

M = "conveyor-07"


def send_normal(rig, n, start=0, **over):
    batch = [make_record(at=f"2026-11-14T15:{(start + i) // 60 % 60:02d}:{(start + i) % 60:02d}Z", diagnosis="normal",
                         abnormal_minutes=0, wrong=False, **over) for i in range(n)]
    ok(rig.client, "POST", "/api/records", batch)


def test_the_machine_normal_and_a_part_normal(rig):
    ok(rig.client, "POST", "/api/model", MODEL)
    rig.pick(rig.roller)
    send_normal(rig, 300, part_id=rig.roller)
    send_normal(rig, 100, start=300)
    part = ok(rig.client, "GET", f"/api/machines/{M}/normal-spectrum?part={rig.roller}")
    assert (part["ok"], part["machine_id"], part["part_id"], part["ready"], part["records"]) == (True, M, rig.roller, True, 300)
    assert len(part["spectrum"]) == 20 and part["spectrum_edges_hz"] == MODEL["spectrum_edges_hz"]
    assert part["simulated"] is True  # make_record is simulated
    whole = ok(rig.client, "GET", f"/api/machines/{M}/normal-spectrum")
    assert (whole["part_id"], whole["records"]) == (None, 400)
    belt = ok(rig.client, "GET", f"/api/machines/{M}/normal-spectrum?part={rig.belt}")
    assert (belt["ready"], belt["records"], belt["spectrum"]) == (False, 0, None)


def test_a_part_of_another_machine_or_an_unknown_part_is_refused(rig):
    for part in (rig.other, "part-9999"):
        assert rig.client.json("GET", f"/api/machines/{M}/normal-spectrum?part={part}") == (
            400, {"ok": False, "error": f"no such part on {M}: {part}"})


def test_a_machine_with_no_records_is_not_ready(rig):
    reply = ok(rig.client, "GET", "/api/machines/nothing-here/normal-spectrum")
    assert (reply["machine_id"], reply["ready"], reply["records"], reply["needed"]) == ("nothing-here", False, 0, 300)
