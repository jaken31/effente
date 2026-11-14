#!/usr/bin/env python3
"""Write simulated per-part history to the sample machine. See README.md, History.

    python3 platform/sim/history.py [--url http://localhost:8000] [--yes]

Writes only to the machine `sample-machine`, and every part, record and log
entry it writes carries "simulated": true. Nothing it sends is a measurement.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sim_history  # noqa: E402

if __name__ == "__main__":
    sys.exit(sim_history.main())
