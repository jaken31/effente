# Agent

**The live Claude call and the live Tavily call have never been run.** Everything below that touches the network is untested. The tests use a rule-based stand-in for Claude and a fake for Tavily.

The agent reads one machine's device log, asks Claude for a maintenance report through three tools (`get_readings`, `get_feature_table`, `search_parts`), checks the report in code, writes a local agent log, and posts the run to the platform as `platform/CONTRACT.md` message 4 defines.

## Run it

From the repo root:

```
python3 -m device.agent --machine ID --log FILE --model-file FILE [--platform URL] [--agent-log FILE] [--stand-in]
```

- `--stand-in`: no Claude call and no network. A fixed-rule writer produces the report: imbalance cites `once_per_turn` if it was active, loose mount cites `rattle` if it was active, unknown cites nothing and recommends inspection, normal is priority `none` and cites nothing. Its summary starts with `Stand-in report:`, the run's `model` is `stand-in (no Claude call)` and `simulated` is true. It goes through the same checks as a real report. It exists so the demo can show the report flow with no key.
- Without `--stand-in`: the live Claude call. If the `anthropic` package or `ANTHROPIC_API_KEY` is missing it exits with code 1, says why, and posts a run with `ok: false` and `reason: "agent unavailable: ..."`. It never writes a report in that case.
- `--platform`: platform URL, falling back to `EFFENTE_PLATFORM_URL`. With neither, nothing is posted. A platform that is down, slow or rejecting only adds a note to the result; the run and the local log still complete.
- `--agent-log`: local log, one JSON line per run (default `agent-log.jsonl`). Holds the result, tool calls, usage, model version and notes.
- Exit code 0 when a checked report exists, 1 otherwise.

Demo example, no key needed:

```
python3 -m device.agent --machine conveyor-07 --log device/agent/tests/logs/imbalance.jsonl \
  --model-file device/agent/tests/logs/model.json --platform http://localhost:8000 --stand-in
```

Tests: `python3 -m pytest device`.

## Behaviour worth knowing

- **Model versions.** If one machine's readings span several `model_version`s, only the newest is used (the version of the latest reading, since versions are plain strings) and the result carries a note. A note is also added when the model file's `model_version` differs from the readings'. The version used goes in the agent log and the posted run as an extra `model_version` field; notes go in an extra `notes` field.
- **Checks.** A cited feature must be in the feature table, reach 0.5 in at least one reading, and that reading must be diagnosed as a fault. Activity only in `normal` or `unknown` windows does not count, because the contract says those reports cite nothing.
- **Posting.** The whole run is PUT at start (`running`), after each tool call, and at the end (`done`). Tool output is cut to 2,000 characters. `run_id` is `run-YYYYMMDD-HHMMSS-<machine>`: two runs for the same machine in the same second would share an ID and the platform would keep only the later one.
- **simulated** is true unless the report came from the real `call_claude` and no reading used has `simulated: true`. All test logs are synthetic and marked simulated, so even a live run on them posts `simulated: true`.
- **Parts lookup** (`parts.py`) calls Tavily with `urllib`, 5 second timeout, and returns at most 3 results of title, URL and a 300 character snippet. With no `TAVILY_API_KEY`, or on any error, it returns a sentence saying the lookup is unavailable and that `part_reference` should be left empty. It never raises.
  **UNVERIFIED against the live service:** the request is `POST https://api.tavily.com/search` with `Authorization: Bearer <key>` and a JSON body `{"query", "max_results", "search_depth": "basic"}`, and the reply is read as `{"results": [{"title", "url", "content"}]}`. This is from memory of Tavily's API. If Tavily wants the key in the body (`api_key`) instead, the lookup will just report itself unavailable.

## Test logs

`tests/logs/` holds ten synthetic device logs in the contract's record shape (every record `simulated: true`, no spectrum), each with a `.expected.json` next to it: expected diagnosis counts, features that may and must be cited, allowed priorities, and whether a report should exist. Cases: healthy, imbalance, loose mount, unknown (cites nothing, even though `rattle` peaks in some unknown windows), imbalance then normal after a fix, `once_per_turn` masked off, wrong machine (no readings), a weak below-threshold feature, two machines in one file, and two model versions. `tests/test_cases.py` runs each through the stand-in and, for every feature a case may not cite, checks that a report citing it is rejected.

These logs test the harness, not Claude. The expected answers are our judgement of a correct report, not measured outcomes.

## Running the live call once with a real key

Not done yet. Steps for a person with keys:

1. From the repo root, make a virtual environment and install the SDK:
   ```
   python3 -m venv .venv && . .venv/bin/activate
   pip install -r device/requirements.txt
   ```
2. Export the keys in this shell only. Never put them in a file in the repo.
   ```
   export ANTHROPIC_API_KEY=...        # required
   export TAVILY_API_KEY=...           # optional; without it search_parts says it is unavailable
   ```
3. Optional: start the platform and add `--platform http://localhost:8000` to watch tool calls arrive on the dashboard.
4. Run one imbalance case:
   ```
   python3 -m device.agent --machine conveyor-07 \
     --log device/agent/tests/logs/imbalance.jsonl \
     --model-file device/agent/tests/logs/model.json \
     --agent-log live-agent.jsonl
   ```
5. A good result: exit code 0, `"ok": true`, `report.cited_features` is `["once_per_turn"]`, `priority` is `medium` or `high`, and the summary does **not** start with `Stand-in report:`. `simulated` will be true because the log is synthetic.
6. Check in `live-agent.jsonl`:
   - `model` is `claude-opus-5-5` and `usage` has non-zero `input_tokens` and `output_tokens`.
   - `tool_calls` includes `get_readings` and `get_feature_table`. If `search_parts` appears, its output is either up to 3 results (Tavily worked) or the "unavailable" sentence (it did not; check the key and the request shape above).
   - `part_reference` is empty or names something that appears in the search output.
7. Then run `unknown.jsonl` the same way. A good result cites no feature and recommends inspection. Then `masked.jsonl`: it must not cite `once_per_turn`.
8. If it fails, the `reason` says how: `agent unavailable: rate limited`, `API error <code>`, `no connection`, `stopped early: <stop_reason>`, `report was not valid JSON`, or `report failed checks` with `problems`. An unexpected exception is logged as `agent error: ...` and re-raised with its traceback.
9. Record what happened (command, exit code, the log line) in the PR or in `docs/`, including failures.

Cost: unknown. Up to 6 tool rounds at `max_tokens` 16,000 each; the token counts in the log are the only measure.

## Concerns with `call_claude`, not fixed because they cannot be tested here

Checked against the TRD settings (model `claude-opus-5-5`, effort set explicitly, structured output, capped rounds, separate connection and rate-limit handling). These are things to look at during the first live run, not confirmed bugs.

1. **Usage is undercounted.** `usage` is read from the final message only. With several tool rounds, each round's tokens are billed but only the last is logged. Summing usage across the messages the runner yields would fix it.
2. **Fallback can change the model silently.** `fallbacks="default"` with the `server-side-fallback-2026-07-01` beta may answer from another model on a refusal, but the run always records `claude-opus-5-5`. Record `final.model` instead.
3. **Unexpected SDK errors are not turned into "agent unavailable".** Only `RateLimitError`, `APIStatusError` and `APIConnectionError` are caught. Anything else (for example `anthropic.AnthropicError` when no credentials resolve) is reported as `agent error: ...` and re-raised. The command line pre-checks `ANTHROPIC_API_KEY`, but the SDK can also pick up credentials from other sources, so the pre-check may be stricter than needed.
4. **Timeouts read as "no connection".** `APITimeoutError` is a subclass of `APIConnectionError`. With `timeout=60` and `max_retries=1`, one request can take about two minutes and a 6-round run much longer. The TRD asks for a short timeout; 60 seconds per request may not be what was meant.
5. **Hitting the round cap** ends with a `tool_use` stop reason, reported as `stopped early: tool_use`. That is correct but the model is never told it is out of rounds.
6. **Refusals** arrive as `stop_reason: "refusal"` and are reported as `stopped early: refusal`, which is acceptable; the category in `stop_details` is not logged.
7. **No prompt caching.** The TRD says stable content first for caching. The order is right (system, tools, then the request) but no `cache_control` is set. Likely small at this prompt size.
8. **Effort is `medium`.** Set explicitly as the TRD asks, but medium is also Opus 5.5's default. Whether `high` gives better reports is untested.
9. **Parameter names not checked against the installed SDK.** `max_iterations`, `betas`, `fallbacks` and `output_config` on `tool_runner` follow the SDK docs as we know them; the package is not installed here, so none of this has been imported or run.
10. **No retry queue.** The TRD says to queue the request and retry when WiFi returns. Nothing queues; a failed run is just logged and posted with its reason.
