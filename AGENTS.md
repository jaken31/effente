# Agent instructions

Rules for every agent session working in this repo. Five people and their agents work here at the same time, so the main job of this file is to stop sessions colliding.

## Read first

- [README.md](README.md): what effente is.
- [docs/PRD.md](docs/PRD.md): what it must do.
- [docs/TRD.md](docs/TRD.md): how it is built, the shared formats, the schedule.

If your task conflicts with the PRD or TRD, stop and ask the person you are working for. Do not quietly change the docs to fit your code.

## One session, one track

Each session works for one track and edits only that track's folders. Ask which track you are on if nobody told you.

| Track | Folders it owns |
|-------|-----------------|
| 1. Device | `firmware/`, `device/` |
| 2. Data and signal | `training/record/`, `data/` |
| 3. Model and mech interp | `training/` (except `record/`), `interp/` |
| 5. Platform, agent and business | `platform/`, `docs/` |

Create your folder if it does not exist yet. Do not edit another track's folder. If you need a change there, describe it to your person and let that track make it.

## Shared code

`effente/` holds the code every track depends on: the signal pipeline, the three file formats (recording, model, log), and the fake-data generator.

- Any track may read it.
- Changing a format or the signal pipeline needs agreement from all five people first. Say what breaks for each track.
- Additions that change no existing behaviour are fine. Mention them in the commit message.
- There is exactly one implementation of the signal pipeline. Never copy it into your own folder.

## Git

- Work on a short-lived branch named `track<N>/<topic>`, for example `track3/unknown-alarm`.
- Before starting and before pushing, update from `master` with a rebase.
- Keep commits small and merge to `master` often. A branch older than a few hours is a merge conflict waiting to happen.
- Commit only your track's files. Never use `git add -A` or `git add .`.
- Prefix commits: `feat:`, `fix:`, `docs:`, `refactor:`, `chore:`.
- Do not add an agent name as commit co-author.
- Never force-push `master`. Never rewrite history someone else may have pulled.
- If a merge conflict touches another track's files, stop and ask.

## Secrets and data

- Never commit `.env` files, API keys (Claude, Tavily), or WiFi passwords.
- Do not commit raw recordings larger than a few megabytes. Ask where they should go.

## Truthfulness

- Do not invent measurements, accuracy numbers, or hardware dimensions. If you did not measure or run it, say so.
- An explanation names only readings the device actually measured, with how far they moved. Do not add reasons the data does not show.
- A fault type not yet checked on real faults is shown as "possible".
- The agent fleet may cite only readings that moved in the records it read.
- Mark anything unverified as unverified, in code comments and in docs.

## Working without waiting

- If real recordings do not exist yet, use the fake-data generator. Do not block on another track.
- If the UNO Q is not available, develop against recorded or fake data on a laptop.
- Keep the cluster off the critical path. Nothing in the demo may depend on a queued job.

## Scope

- Core deliverables in the TRD come before any stretch goal.
- The four-case demo must run end to end by Saturday 1pm. After that, anything not working is cut, not rescued.
- Make the smallest change that does the job. Do not refactor code you were not asked to touch.

## Checks

Run whatever checks exist for the files you changed (tests, lint, type checks) and fix failures before committing. If you add a tool for this, document the command here.
