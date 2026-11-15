"""Which part the probe is on (contract 9.1), and the sample machine's two refusals (9.6). Over HTTP.

The 5 minute rule runs on the log store's wall clock, which these tests replace
with one they move by hand. Record times are near that clock, so the 60 second
catch-up rule can be tested at its edges.
"""

import pytest
from receiver_testkit import make_record

M = "conveyor-07"
T0 = 1_800_000_000.0  # 2027-01-15T08:00:00Z, the fake clock's start


def iso(t):
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + f".{int(t * 1000) % 1000:03d}Z"


class Clock:
    def __init__(self):
        self.t = T0

    def __call__(self):
        return self.t


def ok(client, method, path, body=None):
    status, reply = client.json(method, path, body)
    assert status == 200, reply
    return reply


@pytest.fixture
def rig(make_receiver):
    """A receiver with a fake clock and two parts on conveyor-07. Returns a helper object."""
    class Rig:
        def start(self):
            self.receiver, self.client = make_receiver()
            self.clock = Clock() if not hasattr(self, "clock") else self.clock
            self.receiver.store.partlog.clock = self.clock
            return self

        def pick(self, part_id, by="Sam", machine=M):
            return self.client.json("PUT", f"/api/machines/{machine}/probe", {"part_id": part_id, "by": by})

        def send(self, at=None, **over):
            return self.client.json("POST", "/api/records", make_record(at=iso(self.clock.t if at is None else at), **over))

        def stored(self, machine=M):
            return ok(self.client, "GET", f"/api/machines/{machine}/records?limit=2000")["records"]

        def tick(self):
            self.receiver.store.partlog.tick()

        def state_probe(self):
            return ok(self.client, "GET", "/api/state")["probe"]

    r = Rig().start()
    new = lambda name, machine=M: ok(r.client, "POST", f"/api/machines/{machine}/parts",
                                     {"name": name, "kind": "roller", "by": "Sam"})["part"]["part_id"]
    r.roller, r.belt, r.other = new("Drive roller"), new("Belt"), new("Press roller", "press-01")
    r.restart = lambda: r.start()
    return r


def test_a_record_is_stamped_while_a_part_is_picked_and_not_after_it_is_cleared(rig, open_stream):
    stream = open_stream(rig.receiver.port)
    assert rig.send()[0] == 200  # before any pick
    status, reply = rig.pick(rig.roller)
    probe = {"machine_id": M, "part_id": rig.roller, "since": iso(T0), "by": "Sam"}
    assert (status, reply) == (200, {"ok": True, "probe": probe})
    assert stream.next("probe") == {"machine_id": M, "probe": probe, "reason": ""}
    assert rig.state_probe() == {M: probe}
    rig.clock.t += 1
    rig.send()
    assert rig.pick(None) == (200, {"ok": True, "probe": None})
    assert stream.next("probe") == {"machine_id": M, "probe": None, "reason": ""}
    assert rig.state_probe() == {}
    rig.send()
    assert [r.get("part_id") for r in rig.stored()] == [None, rig.roller, None]
    assert "part_id" not in rig.stored()[0]  # no part means no key, as in version 3.4


def test_picking_another_part_replaces_the_pick_and_a_stored_stamp_never_changes(rig, open_stream):
    rig.pick(rig.roller)
    rig.send()
    rig.clock.t += 30
    stream = open_stream(rig.receiver.port)
    rig.pick(rig.belt, by="Ana")
    assert stream.next("probe")["probe"] == {"machine_id": M, "part_id": rig.belt, "since": iso(T0 + 30), "by": "Ana"}
    rig.send()
    assert [r["part_id"] for r in rig.stored()] == [rig.roller, rig.belt]
    assert rig.state_probe()[M]["part_id"] == rig.belt


def test_a_late_record_from_before_the_pick_is_not_stamped(rig):
    rig.pick(rig.roller)
    rig.send(at=T0 - 61)  # measured before the person picked, arrived late
    rig.send(at=T0 - 60)  # inside the 60 seconds the clocks may differ by
    rig.send(at=T0 + 5)
    assert [r.get("part_id") for r in rig.stored()] == [None, rig.roller, rig.roller]


def test_the_pick_clears_itself_after_5_minutes_with_no_records(rig, open_stream):
    rig.pick(rig.roller)
    stream = open_stream(rig.receiver.port)
    rig.clock.t += 300  # counted from `since` while no record came
    rig.tick()
    assert rig.state_probe()[M]["part_id"] == rig.roller
    rig.clock.t += 1
    rig.tick()
    assert stream.next("probe") == {"machine_id": M, "probe": None, "reason": "no records for 5 minutes"}
    assert rig.state_probe() == {}
    rig.send()
    assert "part_id" not in rig.stored()[-1]


def test_each_record_restarts_the_5_minutes(rig):
    rig.pick(rig.roller)
    for _ in range(3):
        rig.clock.t += 290
        rig.send()
        rig.tick()
    assert rig.state_probe()[M]["part_id"] == rig.roller
    assert [r["part_id"] for r in rig.stored()] == [rig.roller] * 3
    rig.send(machine_id="press-01")  # another machine's records do not keep this pick
    rig.clock.t += 301
    rig.tick()
    assert rig.state_probe() == {}


def test_a_record_after_5_quiet_minutes_is_not_stamped_even_before_a_tick(rig):
    rig.pick(rig.roller)
    rig.clock.t += 301
    rig.send()
    assert "part_id" not in rig.stored()[-1]
    assert rig.state_probe() == {}


MOVED = {"diagnosis": "device_moved", "diagnosis_this_minute": "device_moved"}


def test_the_pick_clears_itself_when_the_device_says_it_moved_after_judging_in_place(rig, open_stream):
    rig.pick(rig.roller)
    stream = open_stream(rig.receiver.port)
    rig.send(diagnosis="normal", diagnosis_this_minute=None)  # judged in place: the pick is settled
    rig.clock.t += 1
    rig.send(**MOVED)
    assert stream.next("probe") == {"machine_id": M, "probe": None, "reason": "device moved"}
    assert rig.state_probe() == {}
    rig.clock.t += 1
    rig.send(diagnosis="normal", diagnosis_this_minute=None)
    assert [r.get("part_id") for r in rig.stored()] == [rig.roller, None, None]


def test_this_periods_diagnosis_decides_while_the_stretch_still_names_another_type(rig):
    rig.pick(rig.roller)
    rig.send()  # unbalance_like: abnormal, but judged in place
    rig.send(diagnosis="unbalance_like", diagnosis_this_minute="device_moved")
    assert rig.state_probe() == {}
    assert [r.get("part_id") for r in rig.stored()] == [rig.roller, None]


@pytest.mark.parametrize("before", ["stopped", "warming_up", "device_moved"])
def test_a_pick_not_yet_judged_in_place_is_kept_when_the_device_says_it_moved(rig, before):
    """A part at another tilt than the device learned at says device_moved throughout: it can still be picked."""
    rig.pick(rig.roller)
    rig.send(diagnosis=before, diagnosis_this_minute=None)
    rig.send(**MOVED)
    assert rig.state_probe()[M]["part_id"] == rig.roller
    assert [r.get("part_id") for r in rig.stored()] == [rig.roller, rig.roller]


def test_a_new_pick_must_be_judged_in_place_again_before_a_move_clears_it(rig):
    rig.pick(rig.roller)
    rig.send(diagnosis="normal", diagnosis_this_minute=None)
    rig.pick(rig.belt)  # the person moved the probe and said so
    rig.send(**MOVED)
    assert rig.state_probe()[M]["part_id"] == rig.belt
    rig.send(diagnosis="normal", diagnosis_this_minute=None)
    rig.send(**MOVED)
    assert rig.state_probe() == {}


def test_a_late_moved_record_from_before_the_pick_does_not_clear_it(rig):
    rig.pick(rig.roller)
    rig.send(diagnosis="normal", diagnosis_this_minute=None)
    rig.send(at=T0 - 61, **MOVED)
    assert rig.state_probe()[M]["part_id"] == rig.roller


def test_a_clear_may_carry_a_reason_as_the_handheld_does(rig, open_stream):
    rig.pick(rig.roller)
    stream = open_stream(rig.receiver.port)
    body = {"part_id": None, "by": "effente-01", "reason": "stopped on the handheld"}
    assert rig.client.json("PUT", f"/api/machines/{M}/probe", body) == (200, {"ok": True, "probe": None})
    assert stream.next("probe") == {"machine_id": M, "probe": None, "reason": "stopped on the handheld"}


@pytest.mark.parametrize("reason", [5, "x" * 201])
def test_a_bad_reason_is_refused(rig, reason):
    rig.pick(rig.roller)
    status, reply = rig.client.json("PUT", f"/api/machines/{M}/probe", {"part_id": None, "by": "Sam", "reason": reason})
    assert status == 400 and "reason" in reply["error"]
    assert rig.state_probe()[M]["part_id"] == rig.roller


def test_the_pick_survives_a_restart_inside_the_5_minutes(rig):
    rig.pick(rig.roller)
    probe = rig.state_probe()
    rig.clock.t += 200
    rig.restart()
    assert rig.state_probe() == probe
    rig.send()
    assert rig.stored()[-1]["part_id"] == rig.roller


def test_after_a_restart_the_5_minutes_still_run(rig):
    rig.pick(rig.roller)
    rig.clock.t += 400  # the receiver was down for longer than that
    rig.restart()
    rig.tick()  # at start the store checks against the real clock, which the fake one is ahead of
    assert rig.state_probe() == {}
    rig.send()
    assert "part_id" not in rig.stored()[-1]
    rig.restart()  # the clearing was kept too
    assert rig.state_probe() == {}


def test_a_cleared_pick_stays_cleared_after_a_restart(rig):
    rig.pick(rig.roller)
    rig.pick(None)
    rig.restart()
    assert rig.state_probe() == {}


@pytest.mark.parametrize("which,error", [
    ("other", "no such part on conveyor-07: part-0003"),
    ("unknown", "no such part on conveyor-07: part-9999"),
    ("removed", "part part-0001 is removed: the probe cannot be on it"),
])
def test_a_part_from_another_machine_a_removed_part_and_an_unknown_part_are_refused(rig, which, error):
    part = {"other": rig.other, "unknown": "part-9999", "removed": rig.roller}[which]
    if which == "removed":
        ok(rig.client, "PUT", f"/api/parts/{rig.roller}", {"removed": True, "by": "Sam"})
    assert rig.pick(part) == (400, {"ok": False, "error": error})
    assert rig.state_probe() == {}


@pytest.mark.parametrize("body,error", [
    ({"part_id": "part-0001"}, "probe is missing required field 'by' (the name the person gave)"),
    ({"by": "Sam"}, "probe is missing required field 'part_id' (a part's id, or null to clear)"),
    ({"part_id": 7, "by": "Sam"}, "probe 'part_id' must be a part's id or null"),
    ([], "probe must be a JSON object"),
])
def test_a_bad_pick_is_refused(rig, body, error):
    assert rig.client.json("PUT", f"/api/machines/{M}/probe", body) == (400, {"ok": False, "error": error})


def test_clearing_with_nothing_picked_sends_no_event(rig, open_stream):
    stream = open_stream(rig.receiver.port)
    assert rig.pick(None) == (200, {"ok": True, "probe": None})
    rig.pick(rig.roller)
    assert stream.next("probe")["probe"]["part_id"] == rig.roller  # the clear sent nothing before it


def test_an_already_stamped_record_keeps_its_part(rig):
    rig.pick(rig.roller)
    rig.send(part_id=rig.belt)
    assert rig.stored()[-1]["part_id"] == rig.belt


def test_a_stamped_record_for_a_part_of_another_machine_refuses_the_batch(rig):
    status, reply = rig.client.json("POST", "/api/records", [make_record(), make_record(part_id=rig.other)])
    assert (status, reply) == (400, {"ok": False, "error": f"no such part on {M}: {rig.other}"})
    assert rig.stored() == []
    assert rig.send(part_id=7)[1] == {"ok": False, "error": "record 'part_id' must be a part's id"}


def test_the_stamp_is_kept_across_a_restart(rig):
    rig.pick(rig.roller)
    rig.send()
    rig.restart()
    assert rig.stored()[-1]["part_id"] == rig.roller


def test_a_record_with_no_part_counts_for_alarms_as_before(rig):
    rig.send()
    alarms = ok(rig.client, "GET", f"/api/machines/{M}/alarms")["alarms"]
    assert len(alarms) == 1 and alarms[0]["open"]


# ---- contract 9.6: the sample machine holds only simulated records -------

def test_a_record_for_the_sample_machine_must_be_simulated(rig):
    for simulated in (False, None, "yes"):
        body = make_record(machine_id="sample-machine", device_id="sim-history", simulated=simulated)
        if simulated is None:
            del body["simulated"]
        assert rig.client.json("POST", "/api/records", [body]) == (
            400, {"ok": False, "error": 'a record for sample-machine must carry "simulated": true'})
    assert rig.stored("sample-machine") == []
    assert ok(rig.client, "GET", "/api/state")["machines"] == ["conveyor-07", "press-01"]
    assert rig.send(machine_id="sample-machine", device_id="sim-history", simulated=True)[0] == 200
    assert rig.send(machine_id="sample-machine", device_id="effente-sim", simulated=True)[0] == 200


def test_the_history_device_may_send_only_for_the_sample_machine(rig):
    for machine in (M, "press-01"):
        assert rig.send(machine_id=machine, device_id="sim-history", simulated=True) == (
            400, {"ok": False, "error": "device sim-history may only send records for sample-machine"})
    assert rig.stored() == [] and rig.stored("press-01") == []


def test_one_real_record_refuses_a_whole_sample_machine_batch(rig):
    good = make_record(machine_id="sample-machine", device_id="sim-history", simulated=True)
    bad = dict(good, simulated=False)
    assert rig.client.json("POST", "/api/records", [good, bad])[0] == 400
    assert rig.stored("sample-machine") == []
