"""One agent run in the shape of platform/CONTRACT.md message 4, and posting it.

The whole run is PUT to the platform when it starts, after each tool call and
at the end. Posting never raises: a platform that is down or slow only loses
the live view, never the report or the local agent log.
"""

from __future__ import annotations

import json
import re
import urllib.request
from datetime import datetime, timezone

POST_TIMEOUT_SECONDS = 2.0
OUTPUT_LIMIT = 2000
AGENT = "maintenance_report"  # which agent in the fleet wrote the run (contract message 4)


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_run_id(machine_id: str, at: datetime | None = None) -> str:
    """`run-YYYYMMDD-HHMMSS-<machine>`, using only letters, digits, `-`, `_`, `.`."""
    at = at or datetime.now(timezone.utc)
    safe_machine = re.sub(r"[^A-Za-z0-9_.-]", "-", machine_id) or "machine"
    return f"run-{at.strftime('%Y%m%d-%H%M%S')}-{safe_machine}"


class Run:
    """Holds the run object and sends it to the platform on every change."""

    def __init__(self, machine_id: str, device_id: str, model: str, platform_url: str = ""):
        self.platform_url = platform_url.rstrip("/")
        self.post_errors: list[str] = []
        self.body: dict = {
            "run_id": make_run_id(machine_id),
            "agent": AGENT,
            "device_id": device_id,
            "machine_id": machine_id,
            "at": now(),
            "status": "running",
            "model": model,
            "usage": {},
            "ok": False,
            "reason": "",
            "problems": [],
            "tool_calls": [],
            "report": None,
            "simulated": False,
            # Extra fields; the contract stores and passes on unknown fields.
            "model_version": "",
            "notes": [],
        }

    @property
    def run_id(self) -> str:
        return self.body["run_id"]

    @property
    def tool_calls(self) -> list[dict]:
        return self.body["tool_calls"]

    def record_tool_call(self, name: str, tool_input: dict, output: str) -> None:
        self.body["tool_calls"].append(
            {"at": now(), "name": name, "input": tool_input, "output": str(output)[:OUTPUT_LIMIT]}
        )
        self.post()

    def finish(self, *, ok: bool, report: dict | None, reason: str, problems: list[str], usage: dict) -> None:
        self.body.update(
            status="done",
            ok=ok,
            report=report if ok else None,
            reason=reason,
            problems=list(problems),
            usage=usage or {},
        )
        self.post()

    def post(self) -> None:
        if not self.platform_url:
            return
        url = f"{self.platform_url}/api/agent/runs/{self.run_id}"
        data = json.dumps(self.body).encode("utf-8")
        request = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}, method="PUT"
        )
        try:
            with urllib.request.urlopen(request, timeout=POST_TIMEOUT_SECONDS) as response:
                response.read()
        except Exception as error:  # down, slow, refused, HTTP 4xx/5xx: never stop the agent
            self.post_errors.append(f"{type(error).__name__}: {error}")
