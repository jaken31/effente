"""Run the maintenance agent on the session started by platform/run.py.

    python3 platform/report.py                     # a model server on this laptop writes the report
    python3 platform/report.py --backend standin   # rule-based report, no model, labelled simulated

--backend local needs a model server (llama.cpp's llama-server) at --llm-url; the
agent's default is http://127.0.0.1:8081. No reading and no report leaves the site.

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
    parser.add_argument("--backend", choices=["local", "standin"], default="local",
                        help="local: a model server on this site; standin: rule-based report, no model, no network")
    parser.add_argument("--llm-url", default="",
                        help="local model server; default EFFENTE_LLM_URL, then http://127.0.0.1:8081")
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
        "--backend", args.backend,
    ]
    if args.llm_url:
        command += ["--llm-url", args.llm_url]
    return subprocess.call(command, cwd=REPO)


if __name__ == "__main__":
    sys.exit(main())
