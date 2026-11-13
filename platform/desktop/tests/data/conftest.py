"""Puts platform/desktop and platform/desktop/tests on sys.path for the data layer's tests.

platform/ is not a package, so the desktop app is imported as `app` from its
own folder, and fakes.py as `fakes`.
"""

import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parents[1]
for path in (TESTS.parent, TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
