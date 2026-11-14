"""Put platform/desktop and its tests folder on sys.path, for `app` and `fakes`."""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

HERE = Path(__file__).resolve().parent
for p in (HERE.parents[1], HERE.parent):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
