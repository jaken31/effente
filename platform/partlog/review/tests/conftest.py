"""Put the repo root (for `device.agent`) and platform/ (for `partlog.review`) on sys.path.

platform/ is not a package: it would shadow the stdlib `platform`.
"""

import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
PLATFORM = TESTS.parents[2]
for path in (PLATFORM.parent, PLATFORM, TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from part_review_cases import history_of  # noqa: E402


@pytest.fixture
def confirmed() -> dict:
    """The history of a part with one alarm answered as real and a finding that says it needs repair."""
    return history_of("confirmed_waiting")
