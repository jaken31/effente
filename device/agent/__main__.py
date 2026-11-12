"""Command line: python3 -m device.agent --machine ID --log FILE --model-file FILE [...]

Without --stand-in it makes the live Claude call. With --stand-in it writes a
rule-based report with no network call (see standin.py).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys

from .harness import MODEL, AgentUnavailable, call_claude, run_agent
from .standin import stand_in_call


def live_call_missing() -> str:
    """Why the live call cannot run here, or "" if it can be attempted."""
    if importlib.util.find_spec("anthropic") is None:
        return "the anthropic package is not installed (pip install anthropic)"
    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return "ANTHROPIC_API_KEY is not set"
    return ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m device.agent", description=__doc__.splitlines()[0])
    parser.add_argument("--machine", required=True, help="machine_id to report on")
    parser.add_argument("--log", required=True, help="device log, one JSON record per line")
    parser.add_argument("--model-file", required=True, help="model file with the verified feature table")
    parser.add_argument("--platform", default=None, help="platform URL; default EFFENTE_PLATFORM_URL; unset means do not post")
    parser.add_argument("--agent-log", default="agent-log.jsonl", help="local agent log to append to")
    parser.add_argument("--stand-in", action="store_true", help="rule-based report, no Claude call, no network")
    args = parser.parse_args(argv)

    missing = ""
    if args.stand_in:
        call = stand_in_call
    else:
        missing = live_call_missing()
        if missing:
            def call(*_):
                raise AgentUnavailable(missing)

            call.model_name = f"{MODEL} (not called)"
        else:
            call = call_claude

    result = run_agent(
        args.machine, args.log, args.model_file, args.agent_log, call=call, platform_url=args.platform
    )
    print(json.dumps({"ok": result.ok, "run_id": result.run_id, "reason": result.reason,
                      "problems": result.problems, "notes": result.notes,
                      "simulated": result.simulated, "report": result.report}, indent=2))
    if missing:
        print(f"Agent unavailable: {missing}. No report was written. "
              "Use --stand-in for a rule-based report with no Claude call.", file=sys.stderr)
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
