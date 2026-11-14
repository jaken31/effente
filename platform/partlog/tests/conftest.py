"""Put platform/partlog on sys.path, as the receiver's log store does."""

import sys
from pathlib import Path

PARTLOG = Path(__file__).resolve().parents[1]
if str(PARTLOG) not in sys.path:
    sys.path.insert(0, str(PARTLOG))
