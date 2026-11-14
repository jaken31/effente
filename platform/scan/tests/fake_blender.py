"""A stand-in for Blender, for the tests: takes the command line scan_convert gives Blender and does what
the environment variable FAKE_BLENDER says.

    ok      write a box as a GLB (the default)
    fail    end with code 1 and a reason on stderr, as our Blender script does
    crash   end with code 7 and no reason
    hang    never finish
    junk    write a file that is not a GLB
    nothing end with code 0 and write no file
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scan_glb import box_glb  # noqa: E402

args = sys.argv[1:]
assert "--background" in args and "--python" in args, args
source, target = args[args.index("--") + 1:]
assert os.path.isfile(source), source
mode = os.environ.get("FAKE_BLENDER", "ok")

if mode == "fail":
    print("Blender 0.0 (stand-in)")
    print("effente: the scan holds no 3D shape", file=sys.stderr)
    sys.exit(1)
if mode == "crash":
    sys.exit(7)
if mode == "hang":
    time.sleep(60)
if mode == "junk":
    with open(target, "w") as f:
        f.write("this is not a model")
    sys.exit(0)
if mode == "nothing":
    sys.exit(0)
with open(target, "wb") as f:
    f.write(box_glb())
