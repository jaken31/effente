"""The fixed histories in histories/, each with its known right answer."""

import json
from pathlib import Path

from partlog.review import history as histories

TESTS = Path(__file__).resolve().parent
PLATFORM = TESTS.parents[2]

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
