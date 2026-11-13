"""Run the maintenance agent for one machine and check what it returns."""

from __future__ import annotations

import http.client
import inspect
import json
import os
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .data import load_feature_table, load_log, load_model_version, newest_model_version
from .report import build_report_schema, check_report
from .run import Run
from .tools import Tools

LLM_URL = "http://127.0.0.1:8081"  # where we run llama-server; --llm-url or EFFENTE_LLM_URL replaces it
# For the whole answer from the model. UNVERIFIED: not timed on any real model.
TIMEOUT_SECONDS = 120.0
LOOKUP_TIMEOUT_SECONDS = 5.0  # asking the server which model it has
JSON_ONLY = {"type": "json_object"}  # for a server that cannot force the report schema
SEARCH_DISABLED = "unavailable: disabled at this site"
# Shown as the run's model when an injected `call` does not name itself with a
# `model_name` attribute.
INJECTED_MODEL = "injected (no model call)"

SYSTEM = """You write maintenance reports for a technician, from vibration readings \
taken by a handheld diagnostic device.

Use get_readings to see what the device measured and get_feature_table to see the \
readings in the model's feature table and what each one means. Each reading is in \
spreads from this machine's normal, signed; 6 or more either way counts as moved.

Cite a reading only if it moved in windows diagnosed as a fault type. Readings that \
moved only while normal, stopped, warming up or in an unrecognised pattern justify no \
citation. If the readings show an unrecognised pattern, say so plainly and recommend \
inspection: do not guess a cause.

Set priority by how soon the machine needs attention. Leave part_reference empty."""


class AgentUnavailable(Exception):
    """The agent could not produce a report. Detection is unaffected."""

    status = 0  # the HTTP status, when a server's error answer is the reason


@dataclass
class AgentResult:
    ok: bool
    report: dict | None = None
    problems: list[str] = field(default_factory=list)
    reason: str = ""
    run_id: str = ""
    model_version: str = ""
    simulated: bool = False
    notes: list[str] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)


# (machine_id, records, features, schema) -> (report, usage).
# A call may also accept a `tools` keyword: it then gets a Tools object whose
# methods are the agent's tools, and every call it makes through them is
# recorded and posted as it happens. Calls without that keyword still work.
# A call that asks a real model sets `live = True` on itself; a run from any
# other call is marked simulated.
Call = Callable[..., tuple[dict, dict]]


def search_disabled(query: str) -> str:
    """The parts lookup for a site where nothing may leave the building."""
    return SEARCH_DISABLED


def run_agent(
    machine_id: str,
    log_path: str | Path,
    model_path: str | Path,
    agent_log_path: str | Path,
    call: Call | None = None,
    platform_url: str | None = None,
    search: Callable[[str], str] | None = None,
) -> AgentResult:
    """Produce a checked maintenance report for one machine.

    Never raises for network or API trouble: the result says why there is no
    report. The run is posted to the platform (platform/CONTRACT.md message 4)
    at `platform_url`, or EFFENTE_PLATFORM_URL; with neither, nothing is posted.
    `search` replaces the Tavily parts lookup: `search_disabled` at a site with
    no outside services, or a fake in tests.
    """
    call = call or call_local
    features = load_feature_table(model_path)
    file_version = load_model_version(model_path)
    all_records = load_log(log_path, machine_id)
    records, version, versions = newest_model_version(all_records)

    notes = []
    if len(versions) > 1:
        shown = ", ".join(v or "(none)" for v in versions)
        notes.append(
            f"readings span model versions {shown}: used only the {len(records)} of "
            f"{len(all_records)} readings from {version or '(none)'}, the newest"
        )
    if records and file_version and version != file_version:
        notes.append(
            f"model file is version {file_version} but the readings used are version "
            f"{version or '(none)'}: feature names may not mean the same thing"
        )

    url = platform_url if platform_url is not None else os.environ.get("EFFENTE_PLATFORM_URL", "")
    run = Run(
        machine_id,
        device_id=str(records[-1].get("device_id", "")) if records else "",
        model=getattr(call, "model_name", INJECTED_MODEL),
        platform_url=url,
    )
    run.body.update(
        model_version=version,
        notes=notes,
        simulated=not getattr(call, "live", False) or any(r.get("simulated") is True for r in records),
    )
    run.post()

    try:
        result, usage = _produce(call, machine_id, records, features, run, search)
    except Exception as error:
        # A bug, not network trouble: close the run so the dashboard does not
        # show it running forever, log it, then let it surface.
        failed = AgentResult(ok=False, reason=f"agent error: {type(error).__name__}: {error}")
        _close(run, failed, {}, agent_log_path, machine_id)
        raise
    return _close(run, result, usage, agent_log_path, machine_id)


def _produce(call, machine_id, records, features, run, search) -> tuple[AgentResult, dict]:
    if not records:
        return AgentResult(ok=False, reason="no readings for this machine"), {}
    try:
        schema = build_report_schema(list(features))
    except ValueError as error:
        return AgentResult(ok=False, reason=str(error)), {}
    tools = Tools(records, features, run=run, search=search)
    try:
        if _accepts_tools(call):
            report, usage = call(machine_id, records, features, schema, tools=tools)
        else:
            report, usage = call(machine_id, records, features, schema)
    except AgentUnavailable as error:
        return AgentResult(ok=False, reason=f"agent unavailable: {error}"), {}
    problems = check_report(report, records, list(features))
    if problems:
        return AgentResult(ok=False, problems=problems, reason="report failed checks"), usage
    return AgentResult(ok=True, report=report), usage


def _close(run: Run, result: AgentResult, usage: dict, agent_log_path, machine_id: str) -> AgentResult:
    run.finish(ok=result.ok, report=result.report, reason=result.reason, problems=result.problems, usage=usage)
    result.run_id = run.run_id
    result.model_version = run.body["model_version"]
    result.simulated = run.body["simulated"]
    result.notes = list(run.body["notes"])
    result.tool_calls = list(run.tool_calls)
    if run.post_errors:
        result.notes.append(f"could not post to platform: {run.post_errors[0]}")
    _append_log(agent_log_path, machine_id, result, usage, run.body["model"])
    return result


def _accepts_tools(call) -> bool:
    try:
        parameters = inspect.signature(call).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(p.name == "tools" or p.kind is p.VAR_KEYWORD for p in parameters)


class LocalCall:
    """Ask a model server on this site for the report. Nothing leaves the site.

    It uses only the OpenAI-style routes that llama.cpp's llama-server and
    Ollama both serve, so either works.

    The model gets no tools to call. The two tool outputs it needs are fetched
    here, so they are recorded on the run like any tool call, and sent with the
    instructions. Parts lookup is not offered.

    UNVERIFIED: written from those routes as we know them (GET /v1/models, POST
    /v1/chat/completions with `response_format`). It has only been run against
    the fake server in tests/test_local_call.py, never a real one.
    """

    live = True  # a real model call: run_agent does not mark the run simulated for it

    def __init__(self, url: str = "", timeout: float = TIMEOUT_SECONDS):
        self.url = (url or os.environ.get("EFFENTE_LLM_URL", "") or LLM_URL).rstrip("/")
        self.timeout = timeout
        self._model = ""

    @property
    def model_name(self) -> str:
        try:
            return f"local: {self._find_model()}"
        except AgentUnavailable:
            return "local: (not called)"

    def __call__(
        self,
        machine_id: str,
        records: list[dict],
        features: dict[str, str],
        schema: dict,
        tools: Tools | None = None,
    ) -> tuple[dict, dict]:
        tools = tools or Tools(records, features)
        readings = tools.get_readings()
        table = tools.get_feature_table()
        messages = [
            {"role": "system", "content": SYSTEM},
            {
                "role": "user",
                "content": f"Write the maintenance report for machine {machine_id}.\n\n"
                f"get_readings returned:\n{readings}\n\n"
                f"get_feature_table returned:\n{table}\n\n"
                f"Answer with one JSON object that matches this schema:\n{json.dumps(schema)}",
            },
        ]
        body = {
            "model": self._find_model(),
            "messages": messages,
            "stream": False,
            "temperature": 0,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "maintenance_report", "strict": True, "schema": schema},
            },
        }
        usage = {"input_tokens": 0, "output_tokens": 0}
        report, text = self._ask(body, schema, usage)
        problems = check_report(report, records, list(features))
        if problems:
            # One more try, told what was wrong. run_agent checks whatever comes back.
            messages.append({"role": "assistant", "content": text})
            messages.append(
                {
                    "role": "user",
                    "content": "That report failed these checks:\n"
                    + "\n".join(f"- {problem}" for problem in problems)
                    + "\nWrite it again with them fixed. Answer with the JSON object only.",
                }
            )
            report, _ = self._ask(body, schema, usage)
        return report, usage

    def _find_model(self) -> str:
        """The first model the server lists: for llama-server, the one it was started with."""
        if not self._model:
            listed = self._request("/v1/models", timeout=min(self.timeout, LOOKUP_TIMEOUT_SECONDS)).get("data")
            first = listed[0] if isinstance(listed, list) and listed else None
            if not isinstance(first, dict) or not first.get("id"):
                raise AgentUnavailable(f"the model server at {self.url} has no model")
            self._model = str(first["id"])
        return self._model

    def _ask(self, body: dict, schema: dict, usage: dict) -> tuple[dict, str]:
        try:
            reply = self._request("/v1/chat/completions", body)
        except AgentUnavailable as error:
            if error.status != 400 or body["response_format"] == JSON_ONLY:
                raise
            # This server version cannot force the schema. Ask for plain JSON,
            # now and on the retry; the schema is in the prompt either way.
            body["response_format"] = JSON_ONLY
            reply = self._request("/v1/chat/completions", body)
        try:
            text = reply["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            text = None
        if not isinstance(text, str):
            raise AgentUnavailable("the model server's answer held no report")
        counts = reply.get("usage") if isinstance(reply.get("usage"), dict) else {}
        for ours, theirs in (("input_tokens", "prompt_tokens"), ("output_tokens", "completion_tokens")):
            if isinstance(counts.get(theirs), int):
                usage[ours] += counts[theirs]
        try:
            report = json.loads(text)
        except json.JSONDecodeError as error:
            raise AgentUnavailable("the model's answer was not valid JSON") from error
        wrong = _shape_problem(report, schema)
        if wrong:
            raise AgentUnavailable(f"the model's answer was not a report: {wrong}")
        return report, text

    def _request(self, path: str, body: dict | None = None, timeout: float | None = None) -> dict:
        timeout = timeout or self.timeout
        try:
            request = urllib.request.Request(
                self.url + path,
                data=None if body is None else json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="GET" if body is None else "POST",
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as error:
            unavailable = AgentUnavailable(f"the model server answered HTTP {error.code}{_error_detail(error)}")
            unavailable.status = error.code
            raise unavailable from error
        except (OSError, http.client.HTTPException, ValueError) as error:
            cause = getattr(error, "reason", error)
            if isinstance(cause, TimeoutError):
                why = f"the model server at {self.url} did not answer within {timeout:g} seconds"
            elif isinstance(cause, ConnectionRefusedError):
                why = f"no model server is running at {self.url}"
            else:
                why = f"could not reach the model server at {self.url}: {cause}"
            raise AgentUnavailable(why) from error
        try:
            reply = json.loads(raw)
        except ValueError as error:
            raise AgentUnavailable("the model server's answer was not valid JSON") from error
        if not isinstance(reply, dict):
            raise AgentUnavailable("the model server's answer was not a JSON object")
        return reply


def _error_detail(error: urllib.error.HTTPError) -> str:
    """The server's own explanation, if its error answer carries one."""
    try:
        said = json.loads(error.read())["error"]
        return f": {said['message'] if isinstance(said, dict) else said}"
    except Exception:
        return ""


def _shape_problem(report, schema: dict) -> str:
    """What stops `report` being the schema's object at all, or "" if nothing does.

    Only the shape check_report relies on: every field present, strings and
    lists of strings where the schema says so.
    """
    if not isinstance(report, dict):
        return "not a JSON object"
    for name, rule in schema["properties"].items():
        value = report.get(name)
        if rule["type"] == "array":
            fits = isinstance(value, list) and all(isinstance(item, str) for item in value)
        else:
            fits = isinstance(value, str)
        if not fits:
            return f"{name} is missing or the wrong type"
    return ""


call_local = LocalCall()


def _append_log(
    path: str | Path, machine_id: str, result: AgentResult, usage: dict, model: str
) -> None:
    entry = {
        "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "machine_id": machine_id,
        "model": model,
        "usage": usage,
        **asdict(result),
    }
    with open(path, "a", encoding="utf-8") as log:
        log.write(json.dumps(entry) + "\n")
