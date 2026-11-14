"""Run every test log through a local model several times, to help choose a model.

    python3 -m device.agent.bakeoff --llm-url http://127.0.0.1:8081 --repeat 3 --out bakeoff.json

Every log in tests/logs/ that has an expected result goes through the local
backend `--repeat` times. For each run this records whether the report passed
the checks on the first try, passed after the one retry, or failed, with the
reasons; how long it took; and every report the model wrote, in full, so a
person can read the summaries and recommended actions for causes or advice the
readings do not support. The checks cannot catch those.

It writes one JSON file and prints one table. Every number in both is counted
or timed in this run. The model name is the one the server reports, unchanged.

Nothing is posted to the platform and no agent log is kept.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .harness import LLM_URL, TIMEOUT_SECONDS, AgentUnavailable, LocalCall, run_agent, search_disabled
from .report import check_report

LOGS = Path(__file__).parent / "tests" / "logs"

FIRST_TRY = "first_try"
AFTER_RETRY = "after_retry"
FAILED = "failed"
# The harness never asked the model, and the log's expected result says so
# (no readings for that machine). Says nothing about the model.
NOT_ASKED = "not_asked"


class RecordingCall(LocalCall):
    """The local model call, keeping every report the model wrote in one run.

    run_agent returns only the last report and does not say which try it was.
    LocalCall asks through `_ask` once per report, so wrapping `_ask` sees each
    one. This depends on that private method: if harness.py changes how it
    retries, change this with it.
    """

    def __init__(self, url: str = "", timeout: float = TIMEOUT_SECONDS):
        super().__init__(url, timeout)
        self.attempts: list[dict] = []
        self._records: list[dict] = []
        self._names: list[str] = []

    def server_model(self) -> str:
        """The model name as the server reports it. Raises AgentUnavailable if it reports none."""
        return self._find_model()

    def __call__(self, machine_id, records, features, schema, tools=None):
        self._records, self._names = records, list(features)
        return super().__call__(machine_id, records, features, schema, tools)

    def _ask(self, body, schema, usage):
        started = time.monotonic()
        report, text = super()._ask(body, schema, usage)
        self.attempts.append(
            {
                "seconds": round(time.monotonic() - started, 3),
                # The same check the harness makes on this report.
                "problems": check_report(report, self._records, self._names),
                "report": report,
            }
        )
        return report, text


def cases(logs: Path):
    """Yield (log name, model file, expected run) for every log with an expected result."""
    for expected_file in sorted(logs.glob("*.expected.json")):
        name = expected_file.name.removesuffix(".expected.json")
        expected = json.loads(expected_file.read_text(encoding="utf-8"))
        for run in expected["runs"]:
            yield name, logs / expected["model_file"], run


def differs_from_expected(report: dict, expected: dict) -> list[str]:
    """How a report that passed the checks differs from the log's expected answer.

    The expected answers are our judgement of a correct report (see README.md),
    so this is a pointer for the reader, not a failure.
    """
    cited = report["cited_features"]
    differs = [f"cites {name}" for name in cited if name not in expected["may_cite"]]
    differs += [f"does not cite {name}" for name in expected["must_cite"] if name not in cited]
    if report["priority"] not in expected["priority"]:
        differs.append(f"priority is {report['priority']}, expected {' or '.join(expected['priority'])}")
    return differs


def one_run(call: RecordingCall, log: Path, model_file: Path, expected: dict) -> dict:
    call.attempts = []
    started = time.monotonic()
    result = run_agent(
        expected["machine_id"], log, model_file, os.devnull, call=call, platform_url="", search=search_disabled
    )
    seconds = round(time.monotonic() - started, 3)
    attempts = call.attempts

    differs: list[str] = []
    if result.ok:
        outcome = FIRST_TRY if len(attempts) == 1 else AFTER_RETRY
        reasons = [] if outcome == FIRST_TRY else attempts[0]["problems"]  # why the retry was needed
        if expected["ok"]:
            differs = differs_from_expected(result.report, expected)
        else:
            differs = ["a report was written where none was expected"]
    elif not attempts and not expected["ok"] and result.reason == expected["reason"]:
        outcome, reasons = NOT_ASKED, [result.reason]
    else:
        outcome, reasons = FAILED, result.problems or [result.reason]
    return {
        "outcome": outcome,
        "reasons": reasons,
        "seconds": seconds,  # the whole run, retry included
        "differs_from_expected": differs,
        "reports": attempts,  # every report the model wrote, in order, each with its own time and check result
    }


def run_bakeoff(url: str = "", repeat: int = 3, logs: Path = LOGS, timeout: float = TIMEOUT_SECONDS) -> dict:
    """Run every case `repeat` times. Raises AgentUnavailable if the server names no model."""
    call = RecordingCall(url, timeout)
    results = {
        "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "llm_url": call.url,
        "model": call.server_model(),
        "repeat": repeat,
        "logs": [],
    }
    for name, model_file, expected in cases(Path(logs)):
        runs = [one_run(call, Path(logs) / f"{name}.jsonl", model_file, expected) for _ in range(repeat)]
        results["logs"].append({"log": name, "machine_id": expected["machine_id"], "runs": runs})
    return results


def table(results: dict) -> str:
    """One row per log and machine. Seconds are per run, over the runs that asked the model."""
    rows = [("log", "machine", "first try", "after retry", "failed", "sec mean", "sec max", "notes")]
    totals: Counter = Counter()
    all_seconds: list[float] = []
    for entry in results["logs"]:
        runs = entry["runs"]
        count = Counter(run["outcome"] for run in runs)
        seconds = [run["seconds"] for run in runs if run["outcome"] != NOT_ASKED]
        notes = []
        if count[NOT_ASKED]:
            notes.append(f"model not asked: {runs[0]['reasons'][0]}")
        differ = sum(1 for run in runs if run["differs_from_expected"])
        if differ:
            notes.append(f"{differ} of {len(runs)} differ from the expected answer")
        rows.append((entry["log"], entry["machine_id"], *_cells(count, seconds), "; ".join(notes)))
        totals += count
        all_seconds += seconds
    rows.append(("total", "", *_cells(totals, all_seconds), ""))

    widths = [max(len(row[column]) for row in rows) for column in range(len(rows[0]))]
    lines = [
        f"model: {results['model']}",
        f"server: {results['llm_url']}   runs per log: {results['repeat']}",
        "",
    ]
    for index, row in enumerate(rows):
        lines.append("  ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip())
        if index in (0, len(rows) - 2):
            lines.append("  ".join("-" * width for width in widths))
    return "\n".join(lines)


def _cells(count: Counter, seconds: list[float]) -> tuple[str, ...]:
    return (
        str(count[FIRST_TRY]),
        str(count[AFTER_RETRY]),
        str(count[FAILED]),
        f"{sum(seconds) / len(seconds):.2f}" if seconds else "-",
        f"{max(seconds):.2f}" if seconds else "-",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m device.agent.bakeoff", description=__doc__.splitlines()[0])
    parser.add_argument("--llm-url", default="",
                        help=f"local model server (llama-server or Ollama); default EFFENTE_LLM_URL, then {LLM_URL}")
    parser.add_argument("--repeat", type=int, default=3, help="runs per log (default 3)")
    parser.add_argument("--out", default="bakeoff.json", help="JSON file to write (default bakeoff.json)")
    args = parser.parse_args(argv)
    if args.repeat < 1:
        parser.error("--repeat must be 1 or more")

    try:
        results = run_bakeoff(args.llm_url, args.repeat)
    except AgentUnavailable as error:
        print(f"Nothing was run and nothing was written: {error}.", file=sys.stderr)
        return 1
    Path(args.out).write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(table(results))
    print(f"\nEvery report is in {args.out}. Read the summaries and recommended actions: "
          "the checks do not catch a cause or advice the readings do not support.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
