"""Put the repo root (for `device.agent`) and platform/ (for `partlog.review`) on sys.path.

platform/ is not a package: it would shadow the stdlib `platform`.
"""

import json
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
PLATFORM = TESTS.parents[2]
for path in (PLATFORM.parent, PLATFORM, TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from partlog.review import history as histories  # noqa: E402

HISTORIES = TESTS / "histories"
MACHINE = "conveyor-07"
CASES = sorted(p.name.removesuffix(".expected.json") for p in HISTORIES.glob("*.expected.json"))


def load_case(name: str) -> tuple[dict, dict]:
    """(the machine's data, the known right answer) of one fixed history."""
    expected = json.loads((HISTORIES / f"{name}.expected.json").read_text())
    return histories.read_file(HISTORIES / f"{name}.json", MACHINE), expected


def history_of(name: str) -> dict:
    data, expected = load_case(name)
    return histories.part_history(data, expected["part_id"])


@pytest.fixture
def confirmed() -> dict:
    """The history of a part with one alarm answered as real and a finding that says it needs repair."""
    return history_of("confirmed_waiting")
