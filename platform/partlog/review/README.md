# Part review agent

**Not yet run against the parts and log routes on master.** They were stubs there when this was written. The tests use fixed history files, a fake receiver and a fake model server in the test process. Two things were run once by hand (see "Real runs"): the stand-in against the routes as built on branch `track5/part-store`, and the local model call against a real model on the fixed history files.

One run per part. A run reads only that part's history, writes a review, checks it in code, and posts the run as `platform/CONTRACT.md` message 4 with `agent` set to `part_review` (section 8.8). A review that passes is also added to the part's log as a `review` entry. The review is advice: it never changes a part's state. No history and no review leaves the site.

## Run it

From the repo root, with the receiver running:

```
python3 platform/part_review.py --machine ID [--part PART_ID] [--backend standin|local] [--llm-url URL] [--port 8000] [--history FILE] [--agent-log FILE]
```

- `--part`: one part. Without it, every part of the machine that is not removed, one run after another.
- `--backend standin` (the default): no model and no network. Fixed rules write the review from the part's state: `needs_repair` gives `yes`, `possible` and `not_checked` give `not_sure`, `nothing_found` gives `no`. These are placeholder rules, not engineering advice. `what` starts with `Stand-in review:`, the run's `model` is `stand-in (no model call)` and `simulated` is true. It goes through the same checks as a model's review.
- `--backend local`: asks a model server on this laptop, as the report agent does (`device/agent/README.md`, "What local mode needs"). The model gets the fact sheet, the instructions and the schema in one request, and no tools. If the review fails the checks, it is asked once more with the problems listed; a second failure is discarded as `review failed checks`. The run's `model` is `local: <model>`. Opt-in: the stand-in is the default.
- `--llm-url`: where the model server is. Falls back to `EFFENTE_LLM_URL`, then `http://127.0.0.1:8081`.
- `--history`: read the machine's parts, entries and alarms from a JSON file instead of the receiver. Nothing is posted and no log entry is added. The files in `tests/histories/` show the shape.
- `--agent-log`: local log, one JSON line per run (default `platform/server/data/part-review.jsonl`).
- Exit code 0 when every part got a checked review, 1 otherwise. The reason for each missing review is on stderr.

No model server or receiver needed:

```
python3 platform/part_review.py --machine conveyor-07 --history platform/partlog/review/tests/histories/confirmed_waiting.json
```

If the model server is not running, does not answer in time, lists no model, or answers with something that is not a review, there is no review: exit code 1, the reason on stderr, and a posted run with `ok: false` and `reason: "agent unavailable: ..."`. The tool call is still on the run.

Tests: `python3 -m pytest platform/partlog`.

## What the writer is given

The code builds a fact sheet for the part (`history.py`, `fact_sheet`) and the writer gets only that:

- counts of entries, findings, inspections, repairs and alarms
- the part: id, name, kind, the fault types linked to it, its note
- the part's state, with its `since` and the alarm and entry behind it
- the fault type behind the state, with its own text from the model announcement
- the part's own entries, oldest first, earlier reviews included
- the alarms linked to the part, each with its newest answer

An alarm is linked when one of the part's entries or its state names it, or when it is waiting on the part: open or unanswered, started after the part's newest repair, with a diagnosis in the part's `fault_types`. Entries of other parts and alarms that only concern other parts never reach the fact sheet.

## Who writes what

- `what`, `why`, `needs_repair`, `cited_alarms`, `cited_entries`: the writer (a model or the stand-in).
- `how`: the code. It copies the fault type's own `text` from the model announcement. The fault type is the `diagnosis` of the entry behind the part's state, or else of the alarm behind it. With no fault type, or one the announcement does not list (such as `unrecognised_pattern`), `how` is empty.
- `since`: the code. It copies the part's `state.since`.

The schema a model is given has no `how` and no `since`. A draft that holds either is refused, even with the right value.

## Checks

`review.py`, `check_review`, for every review, whoever wrote it:

1. Shape: the seven keys, their types, nothing extra.
2. `needs_repair` is `yes`, `no` or `not_sure`. `what` and `why` are not blank.
3. `how` and `since` are the values the code copies.
4. Every cited id is on the fact sheet.
5. Every number in `what` and `why` is on the fact sheet: a count, a score, or digits in a time or a text. Digits inside an id (`alarm-1234`, `conveyor-07`) are not a number, on either side. The match is exact, so "about 2.5" for a score of 2.4 fails.
6. `what` and `why` name no fault type and no part that the fact sheet does not name. The names looked for are every fault type in the model announcement, `unrecognised_pattern`, and the name and id of every other part of the machine. A name matches in any case, with `_`, `-` or a space between its words.
7. With no alarm and no entry that says `needs_repair: true`, `needs_repair` is not `yes`.

A review that fails is discarded: the run ends with `ok: false`, `reason: "review failed checks"` and the problems.

Not handled: a number written as a word ("one alarm"), and a fault type or a part described in other words ("the roller at the far end").

## Behaviour worth knowing

- **Reuse.** The run (`ReviewRun`) is the report agent's `Run` from `device/agent/run.py` with the three differences of section 8.8. The local call (`LocalReviewCall`) is its `LocalCall`: the same model lookup, request, fall back to plain JSON and error reasons. The errors and the agent log writer are the report agent's too. `device/agent/` is not changed. Two pieces could not be reused as they are, because both are tied to device log records and `check_report`: `run_agent`, and the retry inside `LocalCall.__call__`. `agent.py` and `local.py` hold the same flow and the same one retry for a review. They use three names `device/agent` marks as private (`_append_log`, `_ask`, `_find_model`), so a change to those there has to keep them working here.
- **Error wording.** A model answer with a missing key is reported in the report agent's words: `the model's answer was not a report: ...`.
- **One run per part at a time.** The command skips a part whose newest review run is still `running` and started less than 10 minutes ago, and says so. An older `running` run is taken as dead.
- **simulated** is true unless the review came from a real model call and no alarm and no person's entry in the history is simulated. Earlier reviews do not count.
- **The log entry.** Its `text` is `what`, then `Needs repair: yes (advice).` (or `no`, `not sure`), then `why`. `by` is `part review agent`. The entry's id is on the run as an extra `entry_id` field. If the receiver refuses the entry, the run still ends `ok: true` with a note.
- **Posting.** As the report agent: the whole run is PUT at start, after the one tool call (`get_part_history`) and at the end. The tool output is the fact sheet, cut to 2,000 characters; the counts come first so they survive the cut.
- **`run_id`** is `review-YYYYMMDD-HHMMSS-<part_id>`: two runs for the same part in the same second share an id and the platform keeps only the later one.

## Real runs

### The stand-in against the real routes

Done once, on 14 Nov 2026: the command with the stand-in against the receiver of branch `track5/part-store` at commit 2712363, before that branch merged. A model announcement, three parts, one normal record and two unbalance records were sent to it first.

- Alarm open, nobody answered: the drive roller was `possible` and its review said `not_sure`, citing the alarm, with `how` copied from the announcement. The other two parts got `no`.
- After the alarm was answered as real, naming the drive roller: `yes`, citing the alarm and the finding the platform made from the answer.
- Each review landed in the part's log with `source: agent`, and the log store kept `run_id` and `simulated`. The reviews changed no part's state.
- Two reviews of the same part started in the same second shared one `run_id`, so the receiver kept only the later run. The report agent has the same limit.

Not done: the same on master, and a run from the desktop button.

### The local model

Done once, on 14 Nov 2026, on one Linux laptop: llama.cpp's `llama-server` with `small.gguf` and the settings of `device/agent/serve_model.sh` (`--reasoning-budget 600 --predict 1200 --ctx-size 16384 --parallel 1`), on the five histories in `tests/histories/` with `--history` and `--backend local`. Nothing was posted: the parts and log routes did not exist.

| History | Advice | Cited | Time | Tokens in, out |
|---------|--------|-------|------|----------------|
| never_checked | not_sure | nothing | 4 s | 670, 384 |
| possible_only | not_sure | alarm-2301, entry-000007 | 6 s | 949, 593 |
| confirmed_waiting | yes | alarm-1234, entry-000123 | 8 s | 1099, 741 |
| repaired | no | alarm-1234, entry-000123, entry-000131 | 8 s | 1163, 688 |
| false_alarm | no | alarm-1234, entry-000124 | 6 s | 1059, 601 |

All five passed the checks, and the advice matched the known answer each time. The token counts show one request each, so no retry was needed. The times include the model already being loaded.

What a person reading them would still fault, and no check catches:

- `possible_only`: `what` was "The log does not indicate a specific issue with this part". It did not say that an open alarm points at the part as a possible looseness.
- `confirmed_waiting`: `why` said the alarm "is validated". The alarm was answered as real by a person; the fault type itself is not validated.
- `repaired`: `what` was only the old finding's text, "added weight found on the drive roller", with no word that it was repaired.

Five runs on synthetic histories are not a measure of the model. Not done: the local model against the receiver, a run from the desktop button, the large model, and any history longer than three entries.

## Open points

Things to look at when `track5/part-store` merges, not confirmed bugs.

1. **The routes.** `history.py`, `fetch` reads `GET /api/machines/<id>/parts?removed=1`, `GET /api/machines/<id>/log?part=<part_id>&limit=2000`, `GET /api/machines/<id>/alarms?limit=2000` and `GET /api/state` (for the fault types in `models`). They answered as expected on the `track5/part-store` branch. Run it again once that branch is on master.
2. **The review entry's fields.** The contract's `POST /api/machines/<id>/log` body does not list `run_id` or `simulated`. The command sends both for a `review`, and the log store on `track5/part-store` keeps them. If that changes, the entry loses its link to the run.
3. **A long history.** The log route gives at most 2,000 entries, newest first, so a part with more loses its oldest. The whole fact sheet goes in one prompt: about 1,100 tokens for three entries and one alarm. Not measured: a history that fills the server's 16k context.
4. **Stand-in rules.** The expected answers in `tests/histories/` are our judgement of a correct review, not measured outcomes.
