"""Run the maintenance agent on the session started by platform/run.py.

    python3 platform/report.py          # stand-in report, no key, no network, labelled simulated
    python3 platform/report.py --live   # real Claude call: needs ANTHROPIC_API_KEY and `pip install anthropic`

The report and its tool calls appear on the dashboard.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
DATA = HERE / "server" / "data"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--machine", default="demo-fan-1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--live", action="store_true", help="call Claude instead of the stand-in")
    args = parser.parse_args()

    log = DATA / "device.jsonl"
    if not log.exists():
        print("No device log yet. Start python3 platform/run.py first.", file=sys.stderr)
        return 1
    command = [
        sys.executable, "-m", "device.agent",
        "--machine", args.machine,
        "--log", str(log),
        "--model-file", str(DATA / "model.json"),
        "--agent-log", str(DATA / "agent.jsonl"),
        "--platform", f"http://localhost:{args.port}",
    ]
    if not args.live:
        command.append("--stand-in")
    return subprocess.call(command, cwd=REPO)


if __name__ == "__main__":
    sys.exit(main())
