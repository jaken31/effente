"""Command line: python3 -m device.agent --machine ID --log FILE --model-file FILE [...]

--backend local (the default) asks a model server on this site, at --llm-url,
for the report. --backend standin writes a rule-based report with no model and
no network call (see standin.py).
"""

from __future__ import annotations

import argparse
import json
import sys

from .harness import LLM_URL, LocalCall, run_agent, search_disabled
from .standin import stand_in_call


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m device.agent", description=__doc__.splitlines()[0])
    parser.add_argument("--machine", required=True, help="machine_id to report on")
    parser.add_argument("--log", required=True, help="device log, one JSON record per line")
    parser.add_argument("--model-file", required=True, help="model file with the verified feature table")
    parser.add_argument("--platform", default=None, help="platform URL; default EFFENTE_PLATFORM_URL; unset means do not post")
    parser.add_argument("--agent-log", default="agent-log.jsonl", help="local agent log to append to")
    parser.add_argument("--backend", choices=["local", "standin"], default="local",
                        help="local: a model server on this site; standin: rule-based report, no model, no network")
    parser.add_argument("--llm-url", default="",
                        help=f"local model server (llama-server or Ollama); default EFFENTE_LLM_URL, then {LLM_URL}")
    parser.add_argument("--stand-in", dest="backend", action="store_const", const="standin",
                        help="same as --backend standin")
    args = parser.parse_args(argv)

    if args.backend == "standin":
        call, search = stand_in_call, None
    else:
        call, search = LocalCall(args.llm_url), search_disabled

    result = run_agent(
        args.machine, args.log, args.model_file, args.agent_log, call=call, platform_url=args.platform, search=search
    )
    print(json.dumps({"ok": result.ok, "run_id": result.run_id, "reason": result.reason,
                      "problems": result.problems, "notes": result.notes,
                      "simulated": result.simulated, "report": result.report}, indent=2))
    if result.reason.startswith("agent unavailable"):
        print(f"{result.reason[0].upper()}{result.reason[1:]}. No report was written. "
              "Use --backend standin for a rule-based report with no model call.", file=sys.stderr)
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
