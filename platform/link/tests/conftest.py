import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LINK_DIR = os.path.dirname(HERE)
SERVER_DIR = os.path.join(os.path.dirname(LINK_DIR), "server")
for p in (LINK_DIR, SERVER_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)
