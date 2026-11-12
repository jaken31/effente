import os
import sys

# platform/ is not a package (it would shadow the stdlib `platform`), so put
# this part's folder on the path and import its modules by file name.
WEB_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WEB_DIR not in sys.path:
    sys.path.insert(0, WEB_DIR)
