"""Put platform/ (for `setup`) and the repo root (for `device.agent`) on sys.path.

platform/ is not a package: it would shadow the stdlib `platform`.
"""

import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parent
PLATFORM = TESTS.parents[1]
for path in (PLATFORM.parent, PLATFORM, TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
