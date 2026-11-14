#!/usr/bin/env python3
"""Write REPORT.md from the checks' result files. Nothing in the table is typed by hand.

    python3 -m training.validation.report

Every row of the table is one row of one results/<check>.json, with that file's command, seeds,
data source and label next to it. A check with no result file, or one that did not run, gets a
"not run" line with the reason and no number.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from training.validation import common as C

CHECKS = (("heldout_normal", "1. Held-out normal data after calibration"),
          ("faults", "2. Injected and simulated faults"),
          ("unknown_fault", "3. A fault no type covers"),
          ("int8", "4. int8 runtime vs trained network"),
          ("calibration_length", "5. Calibration length"),
          ("fit", "6. Fit check"),
          ("ims", "7. NASA IMS run to failure, PCA default"))
COLUMNS = ("Check", "Entry", "Metric", "Value", "n", "Lowest to highest over seeds", "Command", "Seeds",
           "Data source", "Label", "Stand-in")


def load(folder=C.RESULTS) -> dict:
    """{check name: its result, or None if there is no file}."""
    out = {}
    for name, _ in CHECKS:
        path = Path(folder) / f"{name}.json"
        out[name] = json.loads(path.read_text()) if path.exists() else None
    return out


def cell(v) -> str:
    return ("n/a" if v is None else str(v)).replace("|", "/").replace("\n", " ")


def render(results: dict) -> str:
    lines = ["# Validation report", "",
             "Written by `python3 -m training.validation.report` from the files in `results/`. Do not edit by hand: "
             "rerun `python3 -m training.validation.run_all`.", "",
             "- Label `simulated`: the data comes from the repo's simulators. `public`: a public dataset. "
             "`unverified`: the row depends on the fault type signatures, which have not been checked on real faults.",
             "- Stand-in: `no`, or what stands in for code or a rule that does not exist yet. "
             "A row with a stand-in must be rerun when the real thing lands.",
             "- A period is one summary period of the model; its length is in the n column.",
             "- No row comes from a device recording. What to rerun once recordings exist: [RERUN.md](RERUN.md).", ""]
    code = sorted({r["library_code"] for r in results.values() if r and r.get("library_code")})
    lines += [f"Library and calibration code under test: {'; '.join(code) if code else 'none'}.", "",
              "| " + " | ".join(COLUMNS) + " |", "|" + "---|" * len(COLUMNS)]
    not_run = []
    for name, title in CHECKS:
        r = results.get(name)
        if r is None:
            not_run.append(f"- {title}: not run. No result file `results/{name}.json`.")
            continue
        if r["status"] != "ok":
            not_run.append(f"- {title}: {r['status']}. {r['reason']}. Command: `{r['command']}`")
            continue
        seeds = ",".join(map(str, r["seeds"]))
        for row in r["rows"]:
            lines.append("| " + " | ".join(cell(v) for v in (
                title, row["entry"], row["metric"], row["value"], row.get("n", ""), row.get("range", ""),
                f"`{r['command']}`", seeds, row.get("data_source") or r["data_source"], r["label"],
                row.get("standin") or r.get("standin") or "no")) + " |")
        not_run += [f"- {title}: {note}." for note in r.get("notes", [])]
    lines += ["", "## Not run", ""] + (not_run or ["- Nothing: every check wrote a result."])
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results", default=str(C.RESULTS))
    ap.add_argument("--out", default=str(C.HERE / "REPORT.md"))
    args = ap.parse_args(argv)
    results = load(args.results)
    Path(args.out).write_text(render(results))
    rows = sum(len(r["rows"]) for r in results.values() if r and r["status"] == "ok")
    print(f"report: {rows} rows from {sum(r is not None for r in results.values())} of {len(CHECKS)} result files -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
