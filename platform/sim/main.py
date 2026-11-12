#!/usr/bin/env python3
"""effente simulated device. Stand-in for the handheld; see README.md.

    python3 platform/sim/main.py [--case imbalance] [--url http://localhost:8000]

Every record and the model announcement carry "simulated": true. Nothing this
program sends is a measurement.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sim_device  # noqa: E402

if __name__ == "__main__":
    sys.exit(sim_device.main())
