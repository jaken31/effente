import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SIM_DIR = os.path.dirname(HERE)
for p in (SIM_DIR, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)
