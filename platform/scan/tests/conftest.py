import os
import sys

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
SCAN_DIR = os.path.dirname(TESTS_DIR)
for path in (SCAN_DIR, TESTS_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)
