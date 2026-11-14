"""Run the setup agent: fill a machine profile's five fields from a person's answers.

    python3 platform/setup_run.py --machine conveyor-07 --answers answers.json                  # rule-based stand-in
    python3 platform/setup_run.py --machine conveyor-07 --answers answers.json --backend local  # a model server on this laptop
    some-page | python3 platform/setup_run.py --machine conveyor-07 --answers -                 # answers on stdin

--backend standin (the default) fills the profile by fixed word lists, with no
model, labelled as a stand-in and as simulated. --backend local needs a model
server (llama.cpp's llama-server) at --llm-url; the default is
http://127.0.0.1:8081. No answer and no profile leaves the site.

The run fills the form. It stores nothing and picks nothing: the lookup picks the
starting model in code, and only a person's confirm on the Setup page stores the
profile. See platform/setup/agent/README.md.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
DATA = HERE / "server" / "data"
# platform/ is not a package (it would shadow the stdlib `platform`): the repo
# root is on the path for `device.agent`, this folder for `setup`.
for path in (str(REPO), str(HERE)):
    if path not in sys.path:
        sys.path.insert(0, path)

from setup.agent.agent import AGENT, SetupResult, run_setup  # noqa: E402
from setup.agent.checks import AnswersUnavailable, read_answers  # noqa: E402
from setup.agent.local import LocalSetupCall  # noqa: E402
from setup.agent.standin import stand_in_call  # noqa: E402

# A run still `running` after this long has died: a run asks the model at most
# twice, and each answer has a 120 second limit (device/agent/harness.py).
STALE_AFTER = timedelta(minutes=10)
ASK_TIMEOUT_SECONDS = 5.0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--machine", required=True, help="machine_id the answers are about")
    parser.add_argument("--answers", required=True,
                        help='JSON file of [{"question", "answer", "about"}], or - for stdin')
    parser.add_argument("--port", type=int, default=8000, help="the receiver's port on this laptop")
    parser.add_argument("--backend", choices=["standin", "local"], default="standin",
                        help="standin: fixed word lists, no model, no network; local: a model server on this site")
    parser.add_argument("--llm-url", default="",
                        help="local model server; default EFFENTE_LLM_URL, then http://127.0.0.1:8081")
    parser.add_argument("--no-post", action="store_true", help="post nothing to the receiver")
    parser.add_argument("--agent-log", default="",
                        help="local log, one JSON line per run; default platform/server/data/setup.jsonl")
    args = parser.parse_args(argv)

    platform_url = "" if args.no_post else f"http://localhost:{args.port}"
    running = running_setup(platform_url, args.machine) if platform_url else ""
    if running:
        # One run per machine at a time (contract section 10.3). No second run is posted.
        print(f"No setup run was started: one is already running for {args.machine}: {running}.", file=sys.stderr)
        return 1

    def load() -> list[dict]:
        try:
            raw = sys.stdin.read() if args.answers == "-" else Path(args.answers).read_text(encoding="utf-8")
            return read_answers(json.loads(raw))
        except (OSError, ValueError) as error:
            raise AnswersUnavailable(f"cannot read {args.answers}: {error}") from error

    call = LocalSetupCall(args.llm_url) if args.backend == "local" else stand_in_call
    agent_log = Path(args.agent_log) if args.agent_log else DATA / "setup.jsonl"
    agent_log.parent.mkdir(parents=True, exist_ok=True)
    result = run_setup(args.machine, load, call=call, platform_url=platform_url, agent_log_path=agent_log)

    print(json.dumps(_shown(result), indent=2))
    if not result.ok:
        print(f"No profile was filled: {result.reason}. Fill the five fields by hand.", file=sys.stderr)
        for problem in result.problems:
            print(f"  - {problem}", file=sys.stderr)
        if result.reason.startswith("agent unavailable"):
            print("Use --backend standin for the rule-based stand-in with no model call.", file=sys.stderr)
    return 0 if result.ok else 1


def _shown(result: SetupResult) -> dict:
    shown = asdict(result)
    del shown["tool_calls"]  # they are in the agent log and on the run
    return shown


def running_setup(platform_url: str, machine_id: str) -> str:
    """The run id of a setup run for this machine that is still running, or "".

    A receiver that cannot be asked counts as nothing running: the run itself
    then reports that it could not post.
    """
    machine = urllib.parse.quote(machine_id, safe="")
    try:
        with urllib.request.urlopen(f"{platform_url}/api/machines/{machine}/agent-runs?limit=200",
                                    timeout=ASK_TIMEOUT_SECONDS) as response:
            runs = json.loads(response.read()).get("runs") or []
    except Exception:  # down, slow, not JSON
        return ""
    now = datetime.now(timezone.utc)
    for run in runs:  # newest first
        if isinstance(run, dict) and run.get("agent") == AGENT:
            try:
                at = datetime.fromisoformat(str(run.get("at")).replace("Z", "+00:00"))
            except ValueError:
                return ""
            fresh = now - at < STALE_AFTER
            return str(run.get("run_id")) if run.get("status") == "running" and fresh else ""
    return ""


if __name__ == "__main__":
    sys.exit(main())
