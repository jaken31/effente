"""Run the maintenance agent on what the receiver has stored for one machine.

    python3 platform/report.py                     # a model server on this laptop writes the report
    python3 platform/report.py --backend standin   # rule-based report, no model, labelled simulated
    python3 platform/report.py --machine conveyor-07

The readings are the machine's stored records, read from the running receiver
(platform/CONTRACT.md section 5), and the feature table is the one the device
announced for the model those records were judged with (message 1). So it works
for a real board, the device link and the simulator alike. With no --machine it
reports on the only machine the receiver knows, or on the one a connected
device is on.

When the receiver cannot give both, the session files the simulator writes
beside it (device.jsonl and model.json in the data folder) are read instead, as
before.

--backend local needs a model server (llama.cpp's llama-server) at --llm-url; the
agent's default is http://127.0.0.1:8081. No reading and no report leaves the site.

The report and its tool calls appear on the dashboard.

UNVERIFIED on the board: reading from the receiver was run against a real
receiver holding simulated records and against the tests, not against records
a board sent.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
DATA = HERE / "server" / "data"

SIM_MACHINE = "demo-fan-1"  # the simulator's machine: what the session files hold
FETCH_TIMEOUT_SECONDS = 5.0
PAGE = 2000  # the most records the route gives at once (contract section 5)
NO_READINGS = "No device log yet. Start python3 platform/run.py first."

# The receiver is on this laptop: never through a proxy, whatever the environment says.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class Unavailable(Exception):
    """The receiver could not give what the report needs. The message is a plain reason."""


def get_json(url: str) -> dict:
    try:
        with _OPENER.open(url, timeout=FETCH_TIMEOUT_SECONDS) as response:
            data = json.loads(response.read())
    except (OSError, ValueError) as error:
        raise Unavailable(f"could not read {url}: {getattr(error, 'reason', error)}") from error
    if not isinstance(data, dict):
        raise Unavailable(f"{url} did not answer with a JSON object")
    return data


def pick_machine(state: dict) -> str:
    """The machine to report on when none was named: the only one the receiver knows, else the
    only one a connected device is on. Raises Unavailable, naming the choices, when that is not one."""
    machines = [m for m in state.get("machines") or [] if isinstance(m, str)]
    if len(machines) == 1:
        return machines[0]
    devices = state.get("devices") if isinstance(state.get("devices"), dict) else {}
    live = sorted({d.get("machine_id") for d in devices.values()
                   if isinstance(d, dict) and d.get("connected") and isinstance(d.get("machine_id"), str)})
    if len(live) == 1:
        return live[0]
    if not machines:
        raise Unavailable("the receiver knows no machine yet")
    raise Unavailable("the receiver knows several machines; name one with --machine: " + ", ".join(sorted(machines)))


def stored_records(base: str, machine_id: str) -> list[dict]:
    """Every record the receiver has stored for the machine, oldest first, read page by page."""
    machine = urllib.parse.quote(machine_id, safe="")
    records, after = [], 0
    while True:
        url = f"{base}/api/machines/{machine}/records?limit={PAGE}&since=1970-01-01T00:00:00Z&after_seq={after}"
        page = get_json(url).get("records")
        if not isinstance(page, list) or not page:
            return records
        records += [r for r in page if isinstance(r, dict)]
        last = page[-1].get("seq") if isinstance(page[-1], dict) else None
        if len(page) < PAGE or not isinstance(last, int) or last <= after:
            return records
        after = last


def announced_model(state: dict, machine_id: str, records: list[dict]) -> dict | None:
    """The model announcement (message 1) for the model the newest record was judged with, if the
    receiver has it and it names any readings. The agent reads only records of that model version."""
    version = str(records[-1].get("model_version", "")) if records else ""
    models = state.get("models") if isinstance(state.get("models"), dict) else {}
    model = models.get(version)
    if not isinstance(model, dict) or model.get("machine_id") not in (None, machine_id):
        return None
    return model if model.get("features") else None


def from_receiver(base: str, state: dict, machine_id: str | None) -> tuple[str, list[dict], dict | None]:
    """(machine, its stored records oldest first, its model announcement or None). `state` is the
    receiver's GET /api/state."""
    machine = machine_id or pick_machine(state)
    records = sorted(stored_records(base, machine), key=lambda r: str(r.get("at", "")))
    return machine, records, announced_model(state, machine, records)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--machine", default="",
                        help="machine to report on; default: the only one the receiver knows, "
                             "or the one a connected device is on")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--backend", choices=["local", "standin"], default="local",
                        help="local: a model server on this site; standin: rule-based report, no model, no network")
    parser.add_argument("--llm-url", default="",
                        help="local model server; default EFFENTE_LLM_URL, then http://127.0.0.1:8081")
    args = parser.parse_args(argv)

    base = f"http://localhost:{args.port}"
    machine, records, model, why, unreachable = args.machine, [], None, "", False
    try:
        state = get_json(f"{base}/api/state")
    except Unavailable:
        state, unreachable = None, True  # no receiver on that port: only the session files are left
    if state is not None:
        try:
            machine, records, model = from_receiver(base, state, args.machine or None)
        except Unavailable as error:
            why = str(error)

    with tempfile.TemporaryDirectory(prefix="effente-report-") as tmp:
        if records and model:
            log, model_file = Path(tmp) / "records.jsonl", Path(tmp) / "model.json"
            log.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
            model_file.write_text(json.dumps(model), encoding="utf-8")
        else:
            # The session files the simulator writes: all there was before the receiver was read.
            log, model_file = DATA / "device.jsonl", DATA / "model.json"
            # Not when the receiver answered and there was no telling which machine is meant.
            if not log.exists() or not model_file.exists() or (why and not machine and not unreachable):
                print(_no_report(machine, records, why, unreachable), file=sys.stderr)
                return 1
            machine = machine or SIM_MACHINE
        command = [
            sys.executable, "-m", "device.agent",
            "--machine", machine,
            "--log", str(log),
            "--model-file", str(model_file),
            "--agent-log", str(DATA / "agent.jsonl"),
            "--platform", base,
            "--backend", args.backend,
        ]
        if args.llm_url:
            command += ["--llm-url", args.llm_url]
        DATA.mkdir(parents=True, exist_ok=True)
        return subprocess.call(command, cwd=REPO)


def _no_report(machine: str, records: list[dict], why: str, unreachable: bool) -> str:
    """Why there is nothing to report on, in one line."""
    if records:
        version = str(records[-1].get("model_version", "")) or "(none)"
        count = "1 record" if len(records) == 1 else f"{len(records)} records"
        return (f"The receiver has {count} for {machine} but no feature table for model "
                f"{version}: the device has not announced that model. No report was written.")
    if why and not unreachable:
        return f"No report was written: {why}."
    return NO_READINGS


if __name__ == "__main__":
    sys.exit(main())
