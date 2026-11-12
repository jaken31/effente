"""Results lines against platform/CONTRACT.md's log record (message 2), and the stream's times.

What the contract cannot express is in device/intake/CONTRACT_NOTES.md; these tests hold the parts
we can meet: the required fields, the four diagnosis values, the time format, 0-to-1 features,
an empty reason for normal and unknown, and a mask list.

Run with:  python3 -m device.intake.tests.test_contract
"""

import json
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

from effente import fault_types as FT
from effente import features as F
from effente.simulate_conveyor import ConveyorSim
from effente.site_noise import SiteNoise
from device.intake import contract as C
from device.intake.device_app import DeviceApp
from device.intake.live import ModelSlot, log_record, run_live
from device.intake.source import FileSource, UnoQSource
from device.intake.stream import Stream, at_of, now_iso
from device.intake.tests.test_device_app import START, Clock, rows, run

WALL = 1.79e9  # 2026-09-21 14:13:20 UTC, Unix time


def line(result, status="watching", **kw):
    x = np.zeros(F.N_FEATURES)
    return json.loads(log_record(WALL, x, result, status, [], 0.0, "effente-01", "fan-1", **kw))


def test_every_diagnosis_we_make_maps_to_one_of_the_contracts_four():
    ours = list(FT.NAMES) + ["normal", "warming_up", "stopped", "unrecognised_pattern"]
    assert len(FT.NAMES) == 21
    for name in ours:
        rec = line({"diagnosis": name, "confidence": 0.7, "unknown_score": 1.4, "features": {"vib_1x": 6.2}},
                   model_version="rec-1-abcd1234")
        assert rec["diagnosis"] in C.DIAGNOSES and rec["diagnosis_detail"] == name, rec
        assert C.check_record(rec) == [], (name, C.check_record(rec))
    assert line({"diagnosis": "unbalance_like"})["diagnosis"] == "imbalance"
    assert line({"diagnosis": "looseness_or_rattle"})["diagnosis"] == "loose_mount"
    assert line({"diagnosis": "normal"})["diagnosis"] == "normal"
    for name in ("warming_up", "stopped", "unrecognised_pattern", "overheating", "impacts", "device_moved"):
        assert line({"diagnosis": name})["diagnosis"] == "unknown", name


def test_a_named_fault_carries_the_required_fields_and_its_reason():
    rec = line({"diagnosis": "unbalance_like", "confidence": 1.3, "unknown_score": 2.5,
                "features": {"vib_1x": 6.2, "vib_velocity_mm_s": -1.1}, "wrong": True,
                "what": ["unbalance_like"], "problems": []}, model_version="rec-x-1234abcd", simulated=True)
    for k in C.REQUIRED:
        assert isinstance(rec[k], str) and rec[k], k
    assert rec["model_version"] == "rec-x-1234abcd" and rec["at"] == "2026-09-21T14:13:20Z"
    assert rec["reason"] == FT.TYPES[FT.NAMES.index("unbalance_like")].text
    assert rec["confidence"] == 1.0  # clipped to the contract's 0..1
    assert rec["features"] == {} and rec["deviations"] == {"vib_1x": 6.2, "vib_velocity_mm_s": -1.1}
    assert rec["mask"] == [] and rec["simulated"] is True
    assert rec["wrong"] is True and rec["what"] == ["unbalance_like"] and rec["problems"] == []
    assert rec["result"]["diagnosis"] == "unbalance_like"  # the model's own result, unchanged
    assert C.check_record(rec) == []


def test_normal_unknown_no_model_and_broken_results_still_meet_the_contract():
    normal = line({"diagnosis": "normal", "confidence": 0.8, "unknown_score": 0.3, "features": {"x": 0.5}})
    assert normal["reason"] == "" and normal["diagnosis"] == "normal"
    unknown = line({"diagnosis": "unrecognised_pattern", "unknown_score": 3.0})
    assert unknown["reason"] == "" and unknown["diagnosis_detail"] == "unrecognised_pattern"
    warming = line({"diagnosis": "warming_up", "confidence": 0.0, "unknown_score": None})
    assert "unknown_score" not in warming  # left out, never null
    none = line(None, "no model")
    assert none["diagnosis"] == "unknown" and none["diagnosis_detail"] == "no_model"
    assert none["model_version"] == C.NO_MODEL and none["simulated"] is False
    broken = line({"diagnosis": "unbalance_like", "unknown_score": float("nan"), "odd": float("inf")})
    assert "error" in broken["result"] and broken["diagnosis_detail"] == "error"
    errored = line({"error": "ValueError: bad row"})
    assert errored["diagnosis"] == "unknown" and errored["diagnosis_detail"] == "error"
    for rec in (normal, unknown, warming, none, broken, errored):
        assert C.check_record(rec) == [], (rec, C.check_record(rec))


def test_a_model_that_applies_masks_keeps_its_own_features():
    rec = line({"diagnosis": "imbalance", "confidence": 0.9, "unknown_score": 0.1, "reason": "once-per-turn vibration",
                "features": {"once_per_turn": 0.81, "rattle": 0.0}, "mask": ["rattle"]})
    assert rec["features"] == {"once_per_turn": 0.81, "rattle": 0.0} and rec["mask"] == ["rattle"]
    assert rec["reason"] == "once-per-turn vibration" and "deviations" not in rec and C.check_record(rec) == []


def test_check_record_finds_what_breaks_the_contract():
    bad = {"device_id": "d", "machine_id": "", "at": "2026-11-14T16:02:11+00:00", "diagnosis": "unbalance_like",
           "confidence": 2, "features": {"vib_1x": 6.2}, "reason": "x", "mask": "none"}
    found = " | ".join(C.check_record(bad))
    for part in ("machine_id", "model_version", "diagnosis 'unbalance_like'", "with a Z", "confidence",
                 "features", "mask"):
        assert part in found, (part, found)
    assert "reason must be empty" in " ".join(C.check_record({**bad, "diagnosis": "normal"}))


def test_model_version_changes_with_the_model_file_and_simulated_follows_the_source():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "rec-20261112-101500.json"
        p.write_text('{"a": 1}')
        v1 = C.model_version_of(p)
        p.write_text('{"a": 2}')
        v2 = C.model_version_of(p)
        assert v1 != v2 and v1.startswith("rec-20261112-101500-") and C.model_version_of() == C.NO_MODEL
        fake = Path(tmp) / "fake" / "normal-day-01"
        fake.mkdir(parents=True)
        real = Path(tmp) / "recordings" / "rec-1"
        real.mkdir(parents=True)
        for folder in (fake, real):
            np.savez(folder / "hour_00.npz", samples=np.zeros((1, 3)), accel_t_us=np.zeros(1))
        assert C.is_simulated(FileSource(fake)) and not C.is_simulated(FileSource(real))
    assert C.is_simulated(ConveyorSim(days=1)) and C.is_simulated(SiteNoise(ConveyorSim(days=1)))
    assert not C.is_simulated(UnoQSource())


def test_watch_mode_results_and_stream_times_meet_the_contract():
    stream = Stream()  # publishing works without serving
    run_live(ConveyorSim(days=1, start_s=START, end_s=START + 20), ModelSlot(), console=False, stream=stream)
    lines = [json.loads(it.data()) for it in stream._rings["results"]]
    assert lines and all(C.check_record(r) == [] for r in lines), C.check_record(lines[-1])
    assert lines[-1]["simulated"] is True and lines[-1]["model_version"] == C.NO_MODEL
    stream.publish_event(WALL, "odd", {})
    assert stream._rings["events"][-1].payload["at"] == "2026-09-21T14:13:20Z"
    assert now_iso().endswith("Z") and at_of(5.0).endswith("Z") and stream.status()["at"].endswith("Z")
    assert stream.status()["model_version"] == C.NO_MODEL


def test_the_device_apps_results_and_status_carry_the_model_version():
    with tempfile.TemporaryDirectory() as tmp:
        clock = Clock()
        stream = Stream()
        app = DeviceApp(tmp, ConveyorSim(days=1), stream=stream, clock=clock)
        assert app.status()["model_version"] == C.NO_MODEL and app.status()["simulated"] is True
        run(app, clock, rows(START, 14), press_at=(START + 60, START + 13 * 60))
        assert app.mode == "watching", app.message
        fault = lambda x: x.__setitem__(F.INDEX["vib_1x"], x[F.INDEX["vib_1x"]] + 0.6)  # noqa: E731
        run(app, clock, rows(START + 14 * 60, 3, fault=fault))
        lines = [json.loads(it.data()) for it in stream._rings["results"]]
        version = app.status()["model_version"]
        assert version.startswith(app.model_path.stem + "-") and version != C.NO_MODEL
        assert lines and all(r["model_version"] == version for r in lines)
        assert all(C.check_record(r) == [] for r in lines), [C.check_record(r) for r in lines][-1]
        last = lines[-1]
        assert last["diagnosis"] == "imbalance" and last["diagnosis_detail"] == "unbalance_like", last["diagnosis_detail"]
        assert last["wrong"] is True and last["reason"]
        assert "unusual_vibration" in last["what"] and "unbalance_like" in last["details"]  # said plainly; cause in details
        assert last["features"] == {} and "vib_1x" in last["deviations"]


if __name__ == "__main__":
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            started = time.perf_counter()
            try:
                fn()
                print(f"ok    {name} ({time.perf_counter() - started:.0f} s)")
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(f"FAIL  {name}: {type(e).__name__}: {e}")
    print("all passed" if not failed else f"{failed} failed")
    sys.exit(1 if failed else 0)
