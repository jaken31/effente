"""Run the part review agent: one run per part, reading only that part's history.

    python3 platform/part_review.py --machine conveyor-07                    # every part, rule-based stand-in
    python3 platform/part_review.py --machine conveyor-07 --part part-0007   # one part

--backend standin (the default) writes a rule-based review with no model, labelled
as a stand-in and as simulated. No history and no review leaves the site.

Each run and its review appear in the desktop app, and a review that passes its
checks is added to the part's log. See platform/partlog/review/README.md.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
DATA = HERE / "server" / "data"
# platform/ is not a package (it would shadow the stdlib `platform`): the repo
# root is on the path for `device.agent`, this folder for `partlog.review`.
for path in (str(REPO), str(HERE)):
    if path not in sys.path:
        sys.path.insert(0, path)

from partlog.review import history as histories  # noqa: E402
from partlog.review.agent import AGENT, ReviewResult, run_review  # noqa: E402
from partlog.review.standin import stand_in_call  # noqa: E402

# A run still `running` after this long has died: a review asks the model at
# most twice, and each answer has a time limit (device/agent/harness.py).
STALE_AFTER = timedelta(minutes=10)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--machine", required=True, help="machine_id whose parts to review")
    parser.add_argument("--part", default="", help="part_id of one part; default: every part that is not removed")
    parser.add_argument("--port", type=int, default=8000, help="the receiver's port on this laptop")
    parser.add_argument("--backend", choices=["standin"], default="standin",
                        help="standin: rule-based review, no model, no network")
    parser.add_argument("--history", default="",
                        help="read the machine's parts, entries and alarms from this JSON file, not from "
                             "the receiver. Nothing is posted and no log entry is added")
    parser.add_argument("--agent-log", default="",
                        help="local log, one JSON line per run; default platform/server/data/part-review.jsonl")
    args = parser.parse_args(argv)

    platform_url = "" if args.history else f"http://localhost:{args.port}"

    def read(part_id: str | None = None) -> dict:
        if args.history:
            return histories.read_file(args.history, args.machine)
        return histories.fetch(platform_url, args.machine, part_id)

    try:
        parts = [args.part] if args.part else histories.part_ids(read())
    except histories.HistoryUnavailable as error:
        print(f"No review was written: {error}.", file=sys.stderr)
        return 1
    if not parts:
        print(f"{args.machine} has no parts to review.", file=sys.stderr)
        return 1

    agent_log = Path(args.agent_log) if args.agent_log else DATA / "part-review.jsonl"
    agent_log.parent.mkdir(parents=True, exist_ok=True)
    results = []
    for part_id in parts:
        running = running_review(platform_url, args.machine, part_id) if platform_url else ""
        if running:
            # One run per part at a time (contract section 8.8). No second run is posted.
            results.append(ReviewResult(ok=False, part_id=part_id,
                                        reason=f"a review of this part is already running: {running}"))
            continue
        results.append(run_review(
            args.machine, part_id,
            load=lambda part_id=part_id: histories.part_history(read(part_id), part_id),
            call=stand_in_call, platform_url=platform_url, agent_log_path=agent_log,
        ))

    print(json.dumps([_shown(result) for result in results], indent=2))
    for result in results:
        if not result.ok:
            print(f"{result.part_id}: no review was written: {result.reason}.", file=sys.stderr)
    return 0 if all(result.ok for result in results) else 1


def _shown(result: ReviewResult) -> dict:
    shown = asdict(result)
    del shown["tool_calls"]  # the fact sheet again; it is in the agent log and on the run
    return shown


def running_review(platform_url: str, machine_id: str, part_id: str) -> str:
    """The run id of a review of this part that is still running, or "".

    A receiver that cannot be asked counts as nothing running: the run itself
    then reports what is wrong.
    """
    machine = urllib.parse.quote(machine_id, safe="")
    try:
        runs = histories.get_json(f"{platform_url}/api/machines/{machine}/agent-runs?limit=200").get("runs") or []
    except histories.HistoryUnavailable:
        return ""
    now = datetime.now(timezone.utc)
    for run in runs:  # newest first
        if isinstance(run, dict) and run.get("agent") == AGENT and run.get("part_id") == part_id:
            fresh = now - histories.parse_time(run.get("at")) < STALE_AFTER
            return str(run.get("run_id")) if run.get("status") == "running" and fresh else ""
    return ""


if __name__ == "__main__":
    sys.exit(main())
