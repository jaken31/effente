# Desktop app: shared interfaces

Every part of the desktop app builds against this file. Track 5. Change it only through the lead.

The app is a PySide6 window that runs the receiver in the same process and replaces the browser dashboard on the laptop. Design source: `docs/design/desktop-dashboard-mockup-red-apple.html`, with screenshots of its seven pages in `reference/`. Where the mockup and `platform/CONTRACT.md` differ, the contract wins: the mockup makes up its own data and uses older names.

## Layout

```
platform/desktop/
  main.py                  entry point: starts the embedded receiver, optional sim, opens the window
  requirements.txt
  INTERFACES.md
  reference/               mockup screenshots (lead)
  app/theme/               tokens.py, qss.py, fonts.py                         (agent 1)
  app/widgets/             shared widgets                                      (agent 1)
  app/shell/               main window, sidebar, top bar, routing              (agent 1)
  app/data/                receiver host, API client, state, logic             (agent 2)
  app/pages/live/  app/pages/history/                                          (agent 3)
  app/pages/spectrum/  app/pages/thermal/                                      (agent 4)
  app/pages/evidence/  app/pages/agents/  app/pages/device/                    (agent 5)
  tests/<area>/            each agent's tests; tests/fakes.py is agent 2's
  screenshots/<area>/      each agent's 1600x1000 page screenshots
```

## Theme (`app/theme/tokens.py`)

Constants copied from the mockup's `:root`, plus:

- `RED = "#ff4d2e"`. Used only for shapes: dots, bars, rings, chart marks, selected outlines. Never as a text colour.
- `Fonts.display` = SF Pro Display, `Fonts.text` = SF Pro Text, `Fonts.mono` = SF Mono, each falling back to Inter, then to the system font. Display face for 20pt and up.
- Sentence case everywhere except table headers and sidebar section labels, which are small uppercase with letter spacing.

## Widgets (`app/widgets/`)

Every page uses these instead of styling its own:

- `Panel(title, hint)` with `.body` and `.header_end` (a layout for controls)
- `KpiStrip`
- `Pill(text, color)`: white text after a coloured dot
- `StatusDot`
- `FlagValue(text, abnormal)`: white text with a red dot before it when abnormal
- `Sparkline(values, lo, hi, threshold)`
- `Segmented(options)`
- `Toggle`
- `DataTable`: a QTableView with the mockup's styling and a sticky header
- `Button(kind="primary"|"secondary"|"ghost")`
- `SimChip`

## Pages

Each page is `class XPage(QWidget)` with:

- `key`: one of `live`, `spectrum`, `thermal`, `history`, `evidence`, `agent`, `device`
- `title` and `subtitle`: strings shown in the top bar
- `bind(state: DashboardState)`: connect signals and draw from the current state
- signal `navigate(str)`: ask the shell to show another page

Shortcuts 1 to 7 open the pages in the order above. Spectrum and Thermal sit under Live in the sidebar.

## Data (`app/data/`)

- `ReceiverHost(data_dir, port, with_sim, case)` starts the `platform/server` `Receiver` in this process with `.start()`, and optionally the simulated device. `.stop()` stops both. Use the existing classes; do not reimplement them.
- `ApiClient(base_url)` is a thin client for every route in CONTRACT.md section 5 and later, plus a background SSE reader that hands events to `DashboardState` on the Qt thread.
- `logic.py` is a Python port of `platform/web/dashboard_logic.js`, function for function, with the same names in snake_case and a test for each. No other module may carry its own labelling or judging rules.
- `DashboardState(QObject)`:
  - signals: `snapshot_changed()`, `record_added(dict)`, `alarms_changed()`, `runs_changed()`, `problems_changed()`, `settings_changed()`, `connection_changed(bool)`
  - reads: `machines()`, `current_machine()`, `set_machine(id)`, `latest()`, `records(limit=300, spectrum=False, frames=False)`, `alarms()`, `unanswered_alarms()`, `runs()`, `problems()`, `devices()`, `model()`, `sensitivity()`, `freshness()`
  - actions: `answer_alarm(alarm_id, verdict)`, `set_sensitivity(value_or_None)`, `start_agent(mode="standin"|"live")`. The last one runs `python3 platform/report.py` as a QProcess, as CONTRACT.md allows, with the key read from the laptop's environment only.
- `tests/fakes.py`: `FakeState`, the same interface fed from a fixed record list (taken from `platform/sim/sample_replay.jsonl`). Agent 2 commits it first so pages can be tested before the real state works.

## Additions the real code forces

Written by the lead after reading the code on master at `b929c5f`. Names above are unchanged; these are additions.

1. **Alarm and sensitivity rules have no JavaScript source.** The receiver builds contract sections 6 and 7 (PR #24) but `dashboard_logic.js` has no alarm or sensitivity logic. `logic.py` adds these, marked "laptop only, no JS equivalent", with tests. Pages use them; nobody else writes their own:
   - `alarm_line(sensitivity)`: `4 ** (1 - 2 * s)`, the contract's formula.
   - `sensitivity_name(sensitivity)`: "Low" below 0.34, "High" above 0.66, else "Balanced" (the mockup's `sensName`). `None` reads "Not reported".
   - `margins(sensitivity)`: how far outside normal counts as wrong, as `effente/safety.py` `margins()`: vibration times normal, degC, dB, and the model line. For the slider's impact text.
   - `is_unanswered(alarm)`: `answer` is `null`. `unanswered_alarms()` on the state returns those for the current machine, newest `start` first.
   - `verdict_text(verdict)`: "Real problem" for `real`, "False alarm" for `false`.
2. **An answer needs `by`** (contract section 6, required). The mockup asks for no name. `DashboardState.answer_alarm(alarm_id, verdict, finding=None)` sends `by` from `DashboardState.answerer()`: the `EFFENTE_ANSWERER` environment variable, else the laptop's login name. The Live page's "Later" button sends nothing.
3. **Sensitivity is per device.** `set_sensitivity` targets the device shown for the current machine (`shown_device_id` in the logic port). `sensitivity()` returns `{"reported": x or None, "wanted": y or None}`: the two values in `/api/state`. They differ while a change is on its way; the UI says so rather than showing the wanted value as applied.
4. **The report agent reads files, not the API.** `platform/report.py` reads `platform/server/data/device.jsonl` and `model.json`, written by the simulated device's `--log` and `--model-file`. So `ReceiverHost` defaults `data_dir` to `platform/server/data`, and when it runs the sim it passes those two options, as `platform/run.py` does. `start_agent` runs `python3 platform/report.py --machine <current machine> --port <port>`, adding `--live` for live mode. With no device log it reports `report.py`'s own message ("No device log yet ..."), unchanged.
5. **One run per machine.** `DashboardState.agent_running()` is true while the current machine's newest run has `status: "running"` or a QProcess started by this app is alive. The Run Report Agent buttons are disabled while it is true (contract section 4).
6. **Simulator controls.** The Live page's case and pause buttons need the sim. `DashboardState.sim()` returns `None` when no sim runs, else an object with `key(k: str)` taking the sim's own keys (`1` to `6`, `h`, `v`, `m`, `t`, `p`) and `status() -> str`. The keys mean what `platform/sim/README.md` says.
7. **The sim takes the sensitivity** (PR #26). Setting a sensitivity changes what the simulated device reports within about one second, so the Live page's impact text and diagnosis can be checked end to end.
8. **Records sent by the sim include `heat`** with two camera frames a record by default (`--heat camera`). Thermal is still optional: a real device may send none.

## Rules

- Track 5. All new code goes under `platform/desktop/`. Do not edit `effente/`, `device/`, `training/`, `platform/server/`, `platform/sim/`, `platform/web/` or `platform/report.py`. If one needs a change, stop and tell the lead.
- CONTRACT.md wins over the mockup. Where they differ, follow the contract and list the difference in your report.
- Never invent readings, numbers or reasons. A fault type not validated shows as "possible". The "Simulated data" chip shows whenever the shown data has `simulated: true`. The stand-in agent's report keeps its "Stand-in report:" prefix and its simulated label.
- Dashboard sensitivity only relabels on the laptop. Setting it uses `PUT /api/devices/{id}/settings` as the contract says. It never changes how the device judges, except through the device taking the setting itself.
- Commits: small, prefixed `feat:`/`fix:`/`test:`/`docs:`/`refactor:`/`chore:`, only your own files (never `git add -A` or `git add .`), no agent co-author, no secrets, no `.env`.
- PySide6 and pytest-qt are new dependencies for this folder only, listed in `platform/desktop/requirements.txt`. Desktop tests skip cleanly when PySide6 is missing, the same way the web logic tests skip without node. Run widget tests with `QT_QPA_PLATFORM=offscreen`.
- Draw charts with QPainter in custom widgets. No pyqtgraph, matplotlib or web views.
- Never use the em dash character in code, comments or docs.
- Checks: `python3 -m pytest platform device` and `ruff check platform/desktop`. Ruff is not installed on the lead's laptop; `uvx ruff check platform/desktop` works. `device/intake` tests need scipy.
