# Agent

**Local mode has never been run against a real model server, and the Tavily call has never been run.** The tests use a fake model server in the test process, a rule-based stand-in, and a fake for Tavily.

The agent reads one machine's device log, gets a maintenance report from a model running on this site, checks the report in code, writes a local agent log, and posts the run to the platform as `platform/CONTRACT.md` message 4 defines. No reading and no report leaves the site.

## Run it

From the repo root:

```
python3 -m device.agent --machine ID --log FILE --model-file FILE [--backend local|standin] [--llm-url URL] [--platform URL] [--agent-log FILE]
```

- `--backend local` (the default): asks a model server on this site. The agent calls `get_readings` and `get_feature_table` itself, so both are recorded on the run, then sends their output with the instructions and the report schema in one request. If the report fails the checks, it asks once more with the problems listed; a second failure is discarded as `report failed checks`. Parts lookup is switched off: `search_parts` is not offered to the model, and the lookup function is replaced by one that answers `unavailable: disabled at this site`. The run's `model` is `local: <model>`.
- `--llm-url`: where the model server is. Falls back to `EFFENTE_LLM_URL`, then `http://127.0.0.1:8081`.
- `--backend standin` (or `--stand-in`): no model and no network. A fixed-rule writer produces the report. The summary lists every diagnosis with its window count and names the fault type to fix first. For each fault type it cites up to three readings from the feature table that moved 6 or more spreads (either way) in that fault type's windows. Unrecognised pattern windows add "inspect the machine"; sessions with no fault type cite nothing. Fix order (most windows first), priority (none if no fault type or unrecognised pattern, high if more than one kind of finding, low if one fault type that has since cleared, otherwise medium) and the actions are placeholder rules, not engineering advice. Its summary starts with `Stand-in report:`, the run's `model` is `stand-in (no model call)` and `simulated` is true. It goes through the same checks as a real report. It exists so the demo can show the report flow with no model server.
- `--platform`: platform URL, falling back to `EFFENTE_PLATFORM_URL`. With neither, nothing is posted. A platform that is down, slow or rejecting only adds a note to the result; the run and the local log still complete.
- `--agent-log`: local log, one JSON line per run (default `agent-log.jsonl`). Holds the result, tool calls, usage, model version and notes.
- Exit code 0 when a checked report exists, 1 otherwise.

Demo example, no model server needed:

```
python3 -m device.agent --machine conveyor-07 --log device/agent/tests/logs/imbalance.jsonl \
  --model-file device/agent/tests/logs/model.json --platform http://localhost:8000 --backend standin
```

## What local mode needs

- A model server reachable at `--llm-url`, on the device or on a machine on the same site, that serves the OpenAI-style routes `GET /v1/models` and `POST /v1/chat/completions`. We run llama.cpp's `llama-server` on port 8081 with a model file from `~/effente-models`; Ollama serves the same routes (`--llm-url http://127.0.0.1:11434`). Nothing else is needed: the agent talks to it with the Python standard library, no extra package and no key.
- A model on that server. The agent uses the first one the server lists: for `llama-server` that is the model it was started with. Which model writes good reports is untested.
- A server version that can force a JSON schema (`response_format` of type `json_schema`). If the server refuses that with HTTP 400, the agent asks again for plain JSON instead; the schema is still in the prompt and the answer still has to pass the checks.

If the server is not running, takes longer than 120 seconds to answer, lists no model, or answers with something that is not a report, there is no report: exit code 1, the reason on stderr, and a posted run with `ok: false` and `reason: "agent unavailable: ..."`. The two tool calls are still on the run.

Tests: `python3 -m pytest device`.

## Behaviour worth knowing

- **Model versions.** If one machine's readings span several `model_version`s, only the newest is used (the version of the latest reading, since versions are plain strings) and the result carries a note. A note is also added when the model file's `model_version` differs from the readings'. The version used goes in the agent log and the posted run as an extra `model_version` field; notes go in an extra `notes` field.
- **Records.** Contract version 3.3 records (`platform/CONTRACT.md` message 2): `features` holds the readings that moved most, in spreads from this machine's normal, signed. `unknown_score` is `null` while `stopped` or `warming_up`; such records are counted but add no score. The `problems` the safety checks found are not read: whether the agent may cite them is still open in the contract.
- **Checks.** A cited reading must be in the feature table and have moved 6 or more spreads either way (docs/TRD.md: "a reading 6 or more spreads from usual counts as it is") in a record diagnosed as a fault type. Movement only in `normal`, `stopped`, `warming_up` or `unrecognised_pattern` windows does not count. Three more checks apply to every report, whoever wrote it. Shape: the five keys, text or a list of names, nothing extra (the API no longer enforces the schema, so the code does). Names: the summary and the recommended action may name a reading only if it is cited, or was switched off in some window. Numbers: every number in the summary and the recommended action must appear in the readings the agent was given (the `get_readings` summary of all the records, and each record's own values; sign ignored; digits inside a reading name, the machine id or the device id do not count). The match is exact, so "about 10" for a reading of 9.6 fails. Not handled: a reading described in other words, and counts from a `get_readings` call narrowed with `since`.
- **Posting.** The whole run is PUT at start (`running`), after each tool call, and at the end (`done`). Tool output is cut to 2,000 characters. `run_id` is `run-YYYYMMDD-HHMMSS-<machine>`: two runs for the same machine in the same second would share an ID and the platform would keep only the later one.
- **simulated** is true unless the report came from a real model call (local mode) and no reading used has `simulated: true`. All test logs are synthetic and marked simulated, so even a local run on them posts `simulated: true`.
- **Parts lookup** is off in local mode (see above), and the stand-in never calls it. `parts.py` is still there for a site that allows it: it calls Tavily with `urllib`, 5 second timeout, and returns at most 3 results of title, URL and a 300 character snippet. With no `TAVILY_API_KEY`, or on any error, it returns a sentence saying the lookup is unavailable and that `part_reference` should be left empty. It never raises.
  **UNVERIFIED against the live service:** the request is `POST https://api.tavily.com/search` with `Authorization: Bearer <key>` and a JSON body `{"query", "max_results", "search_depth": "basic"}`, and the reply is read as `{"results": [{"title", "url", "content"}]}`. This is from memory of Tavily's API. If Tavily wants the key in the body (`api_key`) instead, the lookup will just report itself unavailable.

## Test logs

`tests/logs/` holds twelve synthetic device logs in the contract's record shape (every record `simulated: true`, no spectrum), each with a `.expected.json` next to it: expected diagnosis counts, readings that may and must be cited, allowed priorities, and whether a report should exist. Cases: healthy, unbalance-like, looseness or rattle, unrecognised pattern (cites nothing, even though `vib_harm_3_10` moves past 6 spreads in some of its windows), unbalance then normal after a fix, `vib_1x` masked off, wrong machine (no readings), a weak reading below 6 spreads, two machines in one file, two model versions, a mixed demo session, and `sim_session` made by `platform/sim/sim_model.py` with every case including `stopped` and `warming_up`. The first eleven were written for contract version 1 and converted: names to the version 3 ones, activity times 12 into spreads, so the old 0.5 line lands on 6. `tests/test_cases.py` runs each through the stand-in and, for every feature a case may not cite, checks that a report citing it is rejected.

These logs test the harness, not the model. The expected answers are our judgement of a correct report, not measured outcomes.

## Running local mode once with a real server

Not done yet. Steps:

1. On the machine that will serve the model, start `llama-server` on port 8081 with one model file from `~/effente-models`.
2. Optional: start the platform and add `--platform http://localhost:8000` to watch the run arrive on the dashboard.
3. Run one imbalance case (add `--llm-url` if the server is on another machine):
   ```
   python3 -m device.agent --machine conveyor-07 \
     --log device/agent/tests/logs/imbalance.jsonl \
     --model-file device/agent/tests/logs/model.json \
     --agent-log local-agent.jsonl
   ```
4. A good result: exit code 0, `"ok": true`, `report.cited_features` is `["vib_1x"]`, `priority` is `medium` or `high`, and the summary does **not** start with `Stand-in report:`. `simulated` will be true because the log is synthetic.
5. Check in `local-agent.jsonl`:
   - `model` is `local: <the model the server was started with>` and `usage` has non-zero `input_tokens` and `output_tokens`.
   - `tool_calls` is `get_readings` then `get_feature_table`, and nothing else.
   - `part_reference` is empty.
6. Then run `unknown.jsonl` the same way. A good result cites no reading and recommends inspection. Then `masked.jsonl`: it must not cite `vib_1x`.
7. If it fails, the `reason` says how: `agent unavailable: no model server is running at <url>`, `... did not answer within 120 seconds`, `... has no model`, `the model server answered HTTP <code>: ...`, `the model's answer was not valid JSON`, `the model's answer was not a report: ...`, or `report failed checks` with `problems`. An unexpected exception is logged as `agent error: ...` and re-raised with its traceback.
8. Record what happened (command, exit code, the log line, which model and how long it took) in the PR or in `docs/`, including failures.

## Open points in local mode, not settled because no real server was available

Things to look at during the first real run, not confirmed bugs.

1. **The request and reply shapes are from memory of the OpenAI-style API** as `llama-server` and Ollama serve it: `GET /v1/models` replying `data[0].id`, and `POST /v1/chat/completions` with `response_format` `{"type": "json_schema", "json_schema": {"name", "strict", "schema"}}`, replying `choices[0].message.content` and `usage.prompt_tokens` / `completion_tokens`. Whether each server refuses an unsupported `response_format` with HTTP 400, or just ignores it, is not known.
2. **The 120 second timeout is a guess.** It covers one answer, including loading the model the first time. With the retry, a run can take twice that. Nothing was timed.
3. **Context size.** A prompt longer than the model's context window may be cut or refused, depending on the server. The instructions, both tool outputs and the schema go in one prompt; a long session's summary has not been measured against a small model's window.
4. **The model is whatever the server lists first.** There is no option to name one, which matters on Ollama with several models installed.
5. **No retry queue.** The TRD says to queue the request and retry when the connection returns. Nothing queues; a failed run is just logged and posted with its reason.
