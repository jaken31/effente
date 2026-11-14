"""The lookup (platform/CONTRACT.md section 10.2, plan section 16 row U2).

The expected lists are worked out here from the real manifests under
training/library/entries/, read by the test, never copied into it.
"""

import ast
import json
import shutil
from pathlib import Path

import pytest

from setup import lookup

ENTRIES = lookup.ENTRIES_DIR
OTHER_TYPES = [t for t in lookup.VALUES["machine_type"] if t != "conveyor"]


def manifest(entry_id, entries=ENTRIES):
    return json.loads((Path(entries) / entry_id / "manifest.json").read_text())


def expected_lists(entry_id):
    """What contract 10.2's table says to copy, done the plain way."""
    m = manifest(entry_id)
    out = {"version": m["version"], "status": m["status"], "trained_on": [], "checked_on": [], "not_checked_on": []}
    for s in m["sources"]:
        row = {"name": s["name"], "kind": s["kind"]}
        if s["used_for"] == "training":
            out["trained_on"].append(row)
            continue
        if "note" in s:
            row["note"] = s["note"]
        out["checked_on" if s.get("evaluated") is True else "not_checked_on"].append(row)
    out["fault_types_validated"] = [k for k, v in m["scorecard"].items() if v != "not validated"]
    out["fault_types_not_validated"] = list(m["scorecard"].values()).count("not validated")
    return out


def test_a_conveyor_gets_the_conveyor_entry():
    choice = lookup.choose({"machine_type": "conveyor"})
    assert choice["library_entry"] == "conveyor" and choice["reason"] == "machine type is conveyor"


@pytest.mark.parametrize("machine_type", OTHER_TYPES)
def test_every_other_machine_type_gets_the_general_entry(machine_type):
    choice = lookup.choose({"machine_type": machine_type})
    assert choice["library_entry"] == "generic_rotating"
    assert choice["reason"] == f"no entry for machine type {machine_type}, so the general one"


@pytest.mark.parametrize("profile", [{}, {"machine_type": None}, {"drive": "belt", "speed_rpm": 1450}])
def test_an_empty_machine_type_gets_the_general_entry(profile):
    choice = lookup.choose(profile)
    assert choice["library_entry"] == "generic_rotating"
    assert choice["reason"] == "machine type not given, so the general one"


@pytest.mark.parametrize("drive", [None, *lookup.VALUES["drive"]])
@pytest.mark.parametrize("rest", [{}, {"speed_rpm": 1450, "mounting": "flexible", "sensor_location": "frame"},
                                  {"speed_rpm": [10, 20], "mounting": "rigid", "sensor_location": "gearbox"}])
def test_drive_speed_mounting_and_sensor_location_change_nothing_today(drive, rest):
    for machine_type in lookup.VALUES["machine_type"]:
        plain = lookup.choose({"machine_type": machine_type})
        assert lookup.choose({"machine_type": machine_type, "drive": drive, **rest}) == plain


@pytest.mark.parametrize("profile,entry_id", [({"machine_type": "conveyor"}, "conveyor"),
                                              ({"machine_type": "pump"}, "generic_rotating")])
def test_the_lists_are_what_the_real_manifest_says(profile, entry_id):
    choice = lookup.choose(profile)
    assert list(choice) == ["library_entry", "version", "reason", "status", "trained_on", "checked_on",
                            "not_checked_on", "fault_types_validated", "fault_types_not_validated"]
    assert {k: v for k, v in choice.items() if k not in ("library_entry", "reason")} == expected_lists(entry_id)


@pytest.mark.parametrize("entry_id", ["conveyor", "generic_rotating"])
def test_today_both_entries_say_they_are_trained_on_simulated_data_and_checked_on_nothing(entry_id):
    """What the manifests say on 14 Nov 2026. When an entry gains a real source or a
    finished evaluation, this test should fail: change it then, not before."""
    choice = lookup.read_entry(entry_id)
    assert choice["trained_on"] and {s["kind"] for s in choice["trained_on"]} == {"simulated"}
    assert choice["checked_on"] == [] and choice["fault_types_validated"] == []
    assert choice["not_checked_on"] and choice["fault_types_not_validated"] == 21
    assert choice["status"] == {"conveyor": "demo", "generic_rotating": "unverified"}[entry_id]


def test_the_same_profile_always_gives_the_same_choice():
    profile = {"machine_type": "conveyor", "drive": "belt"}
    assert lookup.choose(profile) == lookup.choose(dict(profile)) == lookup.choose(profile)


@pytest.fixture
def library(tmp_path):
    """A copy of the real library to break."""
    for entry_id in lookup.entry_ids():
        (tmp_path / entry_id).mkdir()
        shutil.copy(ENTRIES / entry_id / "manifest.json", tmp_path / entry_id / "manifest.json")
    return tmp_path


@pytest.mark.parametrize("breakage", [
    lambda m: "{not json",
    lambda m: json.dumps({**m, "entry_id": "generic_rotating"}),
    lambda m: json.dumps({k: v for k, v in m.items() if k != "scorecard"}),
    lambda m: json.dumps({**m, "sources": [{"name": "x", "kind": "simulated"}]}),
    lambda m: json.dumps({**m, "version": 1}),
])
def test_an_entry_with_no_readable_manifest_is_never_picked(library, breakage):
    path = library / "conveyor" / "manifest.json"
    path.write_text(breakage(manifest("conveyor", library)))
    choice = lookup.choose({"machine_type": "conveyor"}, library)
    assert choice["library_entry"] == "generic_rotating"
    assert choice["reason"] == "the conveyor entry cannot be read, so the general one"
    assert lookup.read_entry("conveyor", library) is None


def test_a_missing_manifest_is_never_picked(library):
    (library / "conveyor" / "manifest.json").unlink()
    assert lookup.choose({"machine_type": "conveyor"}, library)["library_entry"] == "generic_rotating"


def test_with_no_readable_general_entry_nothing_is_picked(library):
    (library / "generic_rotating" / "manifest.json").write_text("[]")
    with pytest.raises(lookup.LookupUnavailable, match="generic_rotating"):
        lookup.choose({"machine_type": "pump"}, library)
    assert lookup.choose({"machine_type": "conveyor"}, library)["library_entry"] == "conveyor"


def test_the_lookup_imports_nothing_that_could_call_a_model():
    tree = ast.parse(Path(lookup.__file__).read_text())
    imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    imported |= {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert imported <= {"__future__", "json", "math", "pathlib"}
