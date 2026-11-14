# Setup agent

One run reads a person's answers about a machine, fills the five fields of its machine profile, and explains what it understood (`platform/CONTRACT.md` section 10.3). **The agent fills a form. Code picks the model.** It is never given the library, the lookup table or a manifest, and its answer has no place to name an entry. After its draft passes the checks, the lookup (`platform/setup/lookup.py`) picks the library entry in code.

The run stores nothing and sends nothing to a device. It posts only itself, as message 4 with `agent` set to `setup`, the result in `setup` and `report` null. The person reads the five fields on the Setup page, changes what is wrong, and confirms. Only that confirm stores the profile (contract 10.1).

If there is no run, or it fails, the person fills the five fields by hand. The lookup still picks.

## Run it

From the repo root:

```
python3 platform/setup_run.py --machine ID --answers FILE [--backend standin|local] [--llm-url URL] [--port 8000] [--no-post] [--agent-log FILE]
```

- `--answers`: a JSON file, or `-` for stdin. The format is below.
- `--backend standin` (the default): fixed word lists, no model and no network. The explanation begins `Stand-in:`, the run's `model` is `stand-in (no model call)` and it is `simulated: true`, as the report agent's stand-in. Its draft goes through the same checks as a model's.
- `--backend local`: asks a model server on this laptop, as the report agent does (`device/agent/README.md`, "What local mode needs"). The model gets the questions and answers, the five fields with their value lists, and the rules, in one request with no tools. Not the library, the lookup table or a manifest. If its draft fails the checks, it is asked once more with the problems listed; a second failure is discarded. The run's `model` is `local: <model>` and it is `simulated: false`: a real model on a person's answers. Opt-in: the stand-in is the default.
- `--llm-url`: where the model server is. Falls back to `EFFENTE_LLM_URL`, then `http://127.0.0.1:8081`.
- `--no-post`: post nothing to the receiver.
- `--agent-log`: one JSON line per run, default `platform/server/data/setup.jsonl`.
- Prints the result as JSON. Exit code 0 when the profile was filled and passed its checks, 1 otherwise, with the reason and the problems on stderr. If the model server is not running, does not answer in time or answers with something that is not JSON, the run ends `ok: false` with `reason: "agent unavailable: ..."`.
- One run per machine at a time: if the receiver shows a setup run for this machine still `running` and under 10 minutes old, nothing is started or posted. An older one is taken as dead.

No receiver needed:

```
python3 platform/setup_run.py --machine conveyor-07 --answers answers.json --no-post
```

## The answers

The Setup page writes its own questions (plan section 16.3). It passes them as a list:

```json
[
  {"about": "machine_type", "question": "What kind of machine is it?", "answer": "a belt conveyor"},
  {"about": "speed_rpm", "question": "How fast does the motor turn, in rpm?", "answer": null}
]
```

- `answer`: the person's words, or `null` for "I don't know". Text like "not sure" or "I don't know" counts the same.
- `about`: the field the question asks about, or `null`. The stand-in reads only answers with an `about`. A model reads every question and answer, and not `about`.
- Other keys are dropped before the writer sees the answers.

The run's one tool call is `get_answers`, with the output `{"answers": N}`, as in the contract's example.

## The stand-in

For each field it reads the answers about that field and looks for a fixed list of words (`standin.py`, `WORDS`). A field is filled only when exactly one value's words are found. Otherwise it stays empty and the explanation says why: the person did not know, no question asked about it, no word on the list was found, or the answer fits more than one value. `motor` as a machine type counts only when no other machine type matched: most machines have a motor.

The speed is kept only when the answer holds one number (a speed) or two (a range), and does not say Hz, hertz or rps. It never converts a number.

The word lists are starting values written for this stand-in, not checked on real answers. Not handled: negation ("not belt-driven" reads as belt), and a machine described without any word on the list.

## Checks

`checks.py`, `check_draft`, for every draft, whoever wrote it:

1. Shape: exactly `profile` and `explanation`, and exactly the five fields in `profile`. So no key can carry an entry.
2. Each field is a value from its list in contract 10.1, or `null`. A speed is a number above 0 or `[low, high]`.
3. A `speed_rpm` that is not `null` is a number the person wrote, each end of a range too. "1,450" counts as 1450.
4. Every number in `explanation` is a number the person wrote.
5. `explanation` names no library entry (`training/library/entries/*`), in any case and with `_`, `-` or a space between its words. This holds even when the name matches the lookup's pick: the agent never names one. The exceptions are words that are the person's own (in an answer) or a value the profile holds: "conveyor" is also a machine type.

A draft that fails is discarded: the run ends with `ok: false`, `reason: "profile failed checks"` and the problems.

A wrong shape from the model (an extra key, a missing field) is a failed check and gets the retry, not "agent unavailable": `local.py` asks `LocalCall._ask` only for a JSON object and leaves the shape to `check_draft`.

## Reuse

The run (`SetupRun`) is the report agent's `Run` from `device/agent/run.py`, with the differences of contract 10.3. The local call (`LocalSetupCall`) is its `LocalCall`: the same model lookup, request, fall back to plain JSON and error reasons. The errors and the agent log writer are the report agent's too. `device/agent/` is not changed. `run_agent` is tied to device log records and `check_report`, so `agent.py` holds the same flow for a setup run, as the part review does. The retry inside `LocalCall.__call__` is repeated in `local.py` for the same reason. They use three names `device/agent` marks as private (`_append_log`, `_ask`, `_find_model`), so a change to those there has to keep them working here.

## Real runs

### The local model

Done on 14 Nov 2026, on one Linux laptop: llama.cpp's `llama-server` with `small.gguf` and the settings of `device/agent/serve_model.sh`, on the six fixed answer sets in `tests/setup_cases.py`, twice each, with `--backend local --no-post`, after the last prompt change.

- All 12 drafts passed the checks at the first request: no retry was needed. 661 to 692 tokens in, 344 to 743 out, 4 to 8 seconds each with the model already loaded.
- 11 of 12 profiles were the one we judged right. The miss: "a belt and a gearbox" filled `drive` as `belt` once. The prompt says an answer that fits two values is unsure; before that line was added it did this every time. No check catches it: it is a guess that is a value from the list.
- Before "Do not repeat the lists of allowed values" was added to the prompt, one run of the same answers was discarded after its retry: its explanation named `conveyor` while the profile's machine type was empty. The likely cause is that the model listed the allowed values, but that run's text was not kept.
- Once more with a real receiver: the run was posted as `agent: setup`, `simulated: false`, with one tool call. No profile was stored by it. A `PUT` of its five fields stored the profile, with the same choice as the run's.

The expected profiles are our judgement of a right reading, not measured outcomes. Twelve runs on six short answer sets do not measure the model. Not done: the large model, and answers longer than a sentence each.

### The stand-in

The tests run it on the same six answer sets and get the expected profile for each.

## Not checked

- The value lists and the stand-in's word lists on real machines and real answers.
- A run started from the Setup page (plan row U5): not built yet.
- The run's `model` with the local backend is the server's model id as `LocalCall` finds it. With this laptop's server that was the full path of the model file, `local: <folder>/small.gguf`, not `local: small.gguf` as in the contract's example. The report agent has the same behaviour.
