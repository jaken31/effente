#!/usr/bin/env python3
"""Run every validation check, then write REPORT.md.

    python3 -m training.validation.run_all
    python3 -m training.validation.run_all --seeds 1,2,3 --only check_fit,check_int8

Each check is its own script in this folder (check_*.py): it writes results/<check>.json and prints
one summary line. Simulated sessions are kept in .cache/ here, so a second run with the same seeds
skips the simulators. Check 7 needs the public NASA IMS rows file (see check_ims.py); without it
that check is reported as not run.

The harness's own tests: python3 -m pytest training/validation
"""

from __future__ import annotations

import argparse
import importlib
import sys
import time

from training.validation import common as C
from training.validation import report

CHECKS = ("check_heldout_normal", "check_faults", "check_unknown_fault", "check_int8",
          "check_calibration_length", "check_fit", "check_ims")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seeds", default=",".join(map(str, C.SEEDS)))
    ap.add_argument("--workers", type=int)
    ap.add_argument("--only", help="comma-separated check modules (default: all)")
    args = ap.parse_args(argv)
    todo = [c for c in CHECKS if not args.only or c in args.only.split(",")]
    passed = ["--seeds", args.seeds] + (["--workers", str(args.workers)] if args.workers else [])
    failed = []
    for name in todo:
        started = time.time()
        try:
            code = importlib.import_module(f"training.validation.{name}").main(passed)
        except Exception as e:  # noqa: BLE001  one check failing must not hide the others' results
            code = 1
            print(f"{name}: FAILED: {type(e).__name__}: {e}")
        if code:
            failed.append(name)
            (C.RESULTS / f"{name.removeprefix('check_')}.json").unlink(missing_ok=True)  # no stale numbers in the report
        print(f"  ({name} took {time.time() - started:.0f} s)", flush=True)
    report.main([])
    if failed:
        print(f"failed: {', '.join(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
