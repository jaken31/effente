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
- optional `shows_simulated() -> bool` and signal `simulated_changed()`: the page says whether anything it draws is simulated, and the top bar's Simulated data chip is on while it is (contract 9.6). A page without them goes by the latest record.

Shortcuts 1 to 7 open the pages in the order above. Spectrum and Thermal sit under Live in the sidebar.

## Data (`app/data/`)

- `ReceiverHost(data_dir, port, with_sim, case)` starts the `platform/server` `Receiver` in this process with `.start()`, and optionally the simulated device. `.stop()` stops both. Use the existing classes; do not reimplement them.
- `ApiClient(base_url)` is a thin client for every route in CONTRACT.md section 5 and later, plus a background SSE reader that hands events to `DashboardState` on the Qt thread.
- `logic.py` is a Python port of `platform/web/dashboard_logic.js`, function for function, with the same names in snake_case and a test for each. No other module may carry its own labelling or judging rules.
- `DashboardState(QObject)`:
  - signals: `snapshot_changed()`, `record_added(dict)`, `alarms_changed()`, `runs_changed()`, `problems_changed()`, `settings_changed()`, `connection_changed(bool)`
  - reads: `machines()`, `current_machine()`, `set_machine(id)`, `latest()`, `records(limit=300, spectrum=False, frames=False)`, `alarms()`, `unanswered_alarms()`, `runs()`, `problems()`, `devices()`, `model()`, `sensitivity()`, `freshness()`
  - actions: `answer_alarm(alarm_id, verdict)`, `set_sensitivity(value_or_None)`, `start_agent(mode="standin"|"local")`. The last one runs `python3 platform/report.py` as a QProcess, as CONTRACT.md allows. `local` asks the model server on this laptop (addition 16); nothing is sent off the site.
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
4. **The report agent reads files, not the API.** `platform/report.py` reads `platform/server/data/device.jsonl` and `model.json`, written by the simulated device's `--log` and `--model-file`. So `ReceiverHost` defaults `data_dir` to `platform/server/data`, and when it runs the sim it passes those two options, as `platform/run.py` does. `start_agent` runs `python3 platform/report.py --machine <current machine> --port <port> --backend <mode>`, adding `--llm-url http://127.0.0.1:8081` for local mode. With no device log it reports `report.py`'s own message ("No device log yet ..."), unchanged.
5. **One run per machine.** `DashboardState.agent_running()` is true while the current machine's newest run has `status: "running"` or a QProcess started by this app is alive. The Run Report Agent buttons are disabled while it is true (contract section 4).
6. **Simulator controls.** The Live page's case and pause buttons need the sim. `DashboardState.sim()` returns `None` when no sim runs, else an object with `key(k: str)` taking the sim's own keys (`1` to `6`, `h`, `v`, `m`, `t`, `p`) and `status() -> str`. The keys mean what `platform/sim/README.md` says.
7. **The sim takes the sensitivity** (PR #26). Setting a sensitivity changes what the simulated device reports within about one second, so the Live page's impact text and diagnosis can be checked end to end.
8. **Records sent by the sim include `heat`** with two camera frames a record by default (`--heat camera`). Thermal is still optional: a real device may send none.
9. **History reads its whole range** (PR #30 adds `since` and `after_seq` to the records route). `range_records(span_s=None)` returns the current machine's records from `span_s` seconds before its newest one, or all; `range_loaded()` fires once the range is read. `FakeState` answers from its fixed records.
10. **New alarms and failed actions show as notices.** `alarm_opened(dict)` fires for an open alarm on any machine that was not there at the first look. The window stacks notices for it and for `action_failed`.
11. **Closing during a report run asks.** `own_agent_running()` and `stop_agent()`: the run starts in its own session so stopping ends `report.py` and the agent it started. The window offers stop and close, close when it finishes, or cancel.
12. **The window shrinks to 960x660** and opens at 1440x900. In a narrow window the top bar drops the subtitle; a page narrower than its content scrolls sideways.
13. **The window opens where the last run closed.** `MainWindow(settings=...)` keeps its size, page and hand-picked machine in the store from `app/settings.py`, which also holds the colour scheme. Tests pass no store, so they remember nothing.
14. **More shortcuts, listed by `?`.** `app/shell/shortcuts.py` holds one table that registers the keys and fills the list: Esc, `[` and `]` for the machine, Ctrl+R, Ctrl+E, F11, `?` or F1, Ctrl+Q. Ctrl+K opens a search (`app/shell/search.py`) over the pages, the other machines and those actions. A page takes part in Ctrl+E by having `export_dialog()`; Ctrl+R calls the Agents page's `run_agent()`.
15. **A launcher entry.** `python3 platform/desktop/install_launcher.py` adds effente to the Linux app launcher (`--remove` takes it out), with the icon `app/theme/effente.svg`. On macOS it writes two apps into `~/Applications` instead, `effente` and `effente (real device)` (`--no-sim`): each starts `main.py` with the PATH of the shell that installed it, and its output goes to `~/Library/Logs/effente.log`. A receiver that fails to start now also says so in a dialog, since a launcher start has no terminal.
16. **The report's model runs on this laptop.** `app/data/model_server.py` runs llama.cpp's `llama-server` as a child on `127.0.0.1:8081` (`-m <file> --host --port --offline`), never a second one if the port already answers, and gives its state as a plain sentence: not installed, model file missing, failed to start, still loading, running. `app/data/model_settings.py` keeps the model folder (default `~/effente-models`) and the size, `small` or `large`, meaning `small.gguf` or `large.gguf` there, in the store from `app/settings.py`. On the state: `model_folder()`, `model_size()`, `set_model_folder()`, `set_model_size()`, `model_status()`, `start_model_server()`, `stop_model_server()`, and `model_changed()` when any of it moves. `start_agent("local")` first asks the server for its models and refuses with the reason when it does not answer. `stop()` stops a server the app started; one started elsewhere is left running. The Agents page shows all of it. Not checked: a real model file loading (none was at hand), so "still loading" rests on the server answering HTTP 503 meanwhile.
17. **Two more pages, `machine` and `log`** ([docs/scan-and-log-plan.md](../../docs/scan-and-log-plan.md)). They come after the first seven, so shortcuts 1 to 7 are unchanged and the new ones are 8 and 9. Until its package exists under `app/pages/`, a page shows the "not built yet" placeholder. The Machine page's 3D view uses Qt Quick 3D, because QPainter cannot draw a textured model: the one named exception to the chart rule below. It is still not a web view, and every chart is still drawn with QPainter. In a window too short for nine nav items the nav scrolls with the wheel, so the smallest window stays 960x660.
18. **`state.scans`** (`app/data/scans.py`, contract sections 8.2 to 8.4). A stub until built: reads are empty and actions report "not built yet" through `action_failed`.

   | Kind | Name | Meaning |
   |------|------|---------|
   | signal | `scans_changed()` | A scan arrived or its status moved |
   | signal | `model_ready(str)` | The converted file for this `scan_id` is on disk and can be shown |
   | read | `current()` | The current machine's newest scan, or `None` |
   | read | `all()` | The current machine's scans, newest first |
   | read | `model_path(scan_id)` | Path of the converted file in the app's cache, or `None` while it is being fetched. Asking starts the fetch. |
   | read | `qr_text(machine_id, name="")` | The line in contract section 8.2, with this laptop's address on the hotspot |
   | action | `import_file(path)` | Send a USDZ or GLB file from this laptop's disk as a scan for the current machine. The fallback when the phone cannot upload. |

19. **`state.log`** (`app/data/partlog.py`, contract sections 8.5 to 8.8). A stub until built, as `state.scans`.

   | Kind | Name | Meaning |
   |------|------|---------|
   | signal | `parts_changed()` | A part was made, changed, or its state moved |
   | signal | `entries_changed()` | An entry was added |
   | signal | `selection_changed()` | Another part was selected |
   | signal | `reviews_changed()` | A part review run started, moved or ended |
   | read | `parts()` | The current machine's parts, without removed ones |
   | read | `part(part_id)` | One part, or `None` |
   | read | `entries(part_id=None, kind=None)` | The current machine's entries, newest first |
   | read | `suspects(alarm)` | The parts whose `fault_types` hold this alarm's `diagnosis` |
   | read | `waiting_alarms()` | The current machine's alarms that have no answer naming a part |
   | read | `selected_part()` | The selected part's id, or `None` |
   | read | `review(part_id)` | The newest part review run for this part, or `None` |
   | read | `review_running(part_id)` | `True` while one runs |
   | action | `select_part(part_id)` | Select a part. Both pages follow the selection. |
   | action | `create_part(name, kind, position=None)` | Make a part on the current machine |
   | action | `update_part(part_id, **fields)` | Change a part, including moving its pin |
   | action | `add_entry(kind, text, part_id=None, needs_repair=None, alarm_id=None)` | Add an entry |
   | action | `answer_alarm_for_part(alarm_id, verdict, part_id, finding=None, needs_repair=None)` | Answer an alarm and name the part |
   | action | `start_review(part_id=None, mode="standin")` | Run the part review for one part, or for every part when `None`. `mode` is `standin` or `local`, as for the report agent. |
   | action | `export_csv(path)` | Save the log as a CSV file |

   Both objects send `by` from the state's existing `answerer()`. A failed action is reported through the state's existing `action_failed(str)`. The state passes both every snapshot (`apply_snapshot(snap)`) and every stream event (`apply_event(name, data)`).

   The two pages talk only through `state.log`'s selection. The Machine page's "Open log" button calls `select_part` and then `navigate("log")`. The Log page's "Show on machine" button calls `select_part` and then `navigate("machine")`. Neither page imports the other.
20. **Sample data in the fakes.** `FakeState().scans` is a `FakeScans` (`tests/fake_scans.py`) and `FakeState().log` a `FakeLog` (`tests/fake_partlog.py`), so both pages can be built before the real stores work. The sample is written by hand, not by real code:
   - One machine: the fake state's own, `demo-fan-1`, so the sample's alarm links are to alarms the fake state has.
   - One scan, `ready`, whose model is a small box written by code when the tests run. No binary file is committed.
   - Four parts: "Drive roller" (`roller`, `needs_repair`), "Motor mount" (`mount`, `possible`), "Belt" (`belt`, `nothing_found`), and "Idle roller" (`roller`, `not_checked`, no pin).
   - Six entries covering every `kind`, one of them made from an alarm answer.
   - One finished part review for "Drive roller".
   - Actions change only the fake. They do not work a part's state out again: `FakeLog.set_part_state` and `FakeScans.set_status` show another state.
21. **General API calls.** `ApiClient.get(path, **query)`, `post(path, body, file=, content_type=, **query)`, `put(path, body)` and `get_bytes(path, to=)`, with `quote()` for ids put into a path, so `scans.py` and `partlog.py` need no further change to `api.py`. `post(file=...)` sends a file's raw bytes and `get_bytes(to=...)` writes a reply to a file, both a piece at a time.
22. **`state.log`, for part readings** (`app/data/partlog.py`, contract section 9; plan section 15.3). Agreed by the whole team on Sat 14 Nov 2026. Stubs until built, as the rest of `state.log` was.

   | Kind | Name | Meaning |
   |------|------|---------|
   | signal | `probe_changed()` | The probe's part was set, changed or cleared, by a person or by the platform |
   | signal | `readings_changed(str)` | This part's readings, summary or projection moved |
   | read | `probe_part()` | The `part_id` the probe is on for the current machine, or `None` |
   | read | `probe()` | The current machine's selection, `{machine_id, part_id, since, by}` as contract 9.1 gives it, or `None`. An addition after the team agreed this table. |
   | read | `probe_cleared()` | Why the current machine's pick was last cleared, as the `probe` event's `reason` gave it (`device moved`, `no records for 5 minutes`, or what the handheld sent). `""` while a part is picked or when a person here cleared it. An addition after the team agreed this table. |
   | read | `readings(part_id, since=None)` | The reply of the part's readings route (contract 9.3), or `None` while it is being fetched. Asking starts the fetch. |
   | read | `normal_spectrum(part_id=None)` | The reply of contract 9.4, or `None` while it is being fetched |
   | action | `set_probe_part(part_id)` | Say the probe is on this part. `None` clears it. |

   Sample data in `FakeLog`, so the page can be built before the receiver works: the four parts of addition 20, with readings on 20 days for "Drive roller" (`rising`), on 20 days for "Belt" (`no_clear_rise`), on one day for "Motor mount" (`not_enough_history`), and none for "Idle roller". Every one `simulated: true`, so the marks get built and tested from the first hour. One normal spectrum that is ready and one that is not.

   Two things the agreed contract adds to what a page does with these. The chart plots a reading's `velocity_mm_s` and draws the 11 mm/s line labelled "limit for any machine, 11 mm/s"; the projection fits the model's score and is shown as its sentence, never drawn on the mm/s axis (contract 9.5). When the projection's `show_days` is `false` the page says "Trend" and shows no days and no date.
23. **A tenth page, `setup`** (contract section 10; plan section 16.3). Not built. This file does not record the whole team agreeing it (unverified). It comes after the first nine, so shortcuts 1 to 9 are unchanged; its own key is 0, the next one on the number row (chosen by track 5 alone on 14 Nov; the whole team has not agreed it, unverified). Until its package exists under `app/pages/`, it shows the "not built yet" placeholder.

   ```
    +-------------------------------------+-------------------------------------------+
    | Questions                           | Machine profile         not confirmed yet |
    |  What kind of machine is it?        |  Machine type     [conveyor       v]      |
    |  [a belt conveyor                 ] |  Drive            [belt           v]      |
    |  How is it driven?                  |  Speed, rpm       [               ] empty |
    |  [                 ] [I don't know] |  Mounting         [rigid          v]      |
    |  ...                                |  Sensor location  [               ] empty |
    | [Fill the profile]                  +-------------------------------------------+
    +-------------------------------------+ Starting model: conveyor (demo)           |
    | What the agent understood           |  Why: machine type is conveyor            |
    |  "You said it is a belt conveyor    |  Trained on: sim_conveyor (simulated)     |
    |   bolted to the floor. You did      |  Checked on: nothing yet                  |
    |   not know its speed ..."           |  Not checked on: sim_server_rack          |
    |  Stand-in, simulated: marked here   |  Fault types validated: 0 of 21           |
    |                                     | [Confirm and send to the device]          |
    +-------------------------------------+-------------------------------------------+
     Local profile: Sam, technician [Change]    Wanted: conveyor. Device is using: generic_rotating
   ```

   - **Questions.** A few, in plain words, each with "I don't know". Their wording is this page's to write. No question asks for a library entry.
   - **Fill the profile.** Starts one setup run through `state.setup.start_run`. Disabled while a run is running for this machine.
   - **The five fields.** Filled from the run, or by hand with no run at all. A field nobody knows shows "empty", never a guess and never a default. Changing a field asks the lookup again.
   - **Starting model.** The lookup's choice, with its reason, its status and its lists, in the contract's words. Nothing here comes from the agent.
   - **Confirm.** The only thing that stores the profile and sends the wanted entry. Until then the page says "not confirmed yet" and "nothing has been sent to the device".
   - **Wanted against in use.** After confirming, both entries are shown until they match. The page never shows the wanted entry as the one in use.
   - **The local profile.** Its name and role, and a "Change" form. The words on screen are "local profile". Never "log in", "account", "sign in" or "security".
   - **States to design, not leave blank:** no profile yet, a run running, a run failed with its reason, the stand-in's result with its label, every field empty, a profile confirmed with no device connected, wanted and in use differing, no local profile set.
24. **`state.setup`** (`app/data/setup.py`, contract section 10). Built on the routes of PR #76. The state passes it every snapshot and every stream event, as it does the other two.

   | Kind | Name | Meaning |
   |------|------|---------|
   | signal | `profile_changed()` | The current machine's stored profile, the draft or the choice moved |
   | signal | `run_changed()` | A setup run started, moved or ended |
   | signal | `entry_changed()` | The wanted entry, or the one the device reports, moved |
   | signal | `local_profile_changed()` | The local profile was set or changed |
   | read | `profile()` | The current machine's confirmed machine profile (contract 10.1), or `None` |
   | read | `draft()` | The five fields as they stand on the page, not yet confirmed. Starts from `profile()`, or all empty. |
   | read | `choice()` | The lookup's reply for the draft (contract 10.2), or `None` while it is being fetched |
   | read | `run()` | The newest setup run for the current machine, or `None` |
   | read | `run_running()` | `True` while one runs |
   | read | `wanted_entry()` | The library entry wanted for the current machine, or `None` |
   | read | `device_entry()` | The entry the shown device reports, or `None` |
   | read | `local_profile()` | `{"name", "role"}`, or `None` if none is set |
   | read | `profile_known()` | `True` once a snapshot has carried `profiles`. Until then `profile()` being `None` says nothing, so the window does not open the Setup page for it. |
   | read | `lookup_problem()` | Why the lookup for the draft failed (HTTP 500 with a reason), or `""`. `choice()` stays `None` then. |
   | action | `start_run(answers, mode="standin")` | Run the setup agent on these answers. `mode` is `standin` or `local`, as for the report agent. A run that passes fills the draft. |
   | action | `set_draft(**fields)` | Change fields of the draft by hand. Asks the lookup again. |
   | action | `confirm()` | Store the draft as the machine profile. The only action that sends anything toward the device. |
   | action | `set_local_profile(name, role)` | Keep the local profile in the store from `app/settings.py`. No password. |

   `start_run` runs `python3 platform/setup_run.py --machine <id> --answers - --port <port> --backend <mode>` as a local process, with the answers on its stdin as `[{"about", "question", "answer"}]` (platform/setup/agent/README.md). A run that passes fills the draft when it arrives on the stream. `wanted_entry()` is the confirmed profile's choice; `device_entry()` is the `library_entry` of the shown device's model announcement. The device does not act on the wanted entry yet (plan row D1), so the page says "chosen, not yet loaded on the device" until the device reports an entry, and never shows the wanted one as running.

   The window opens the Setup page by itself, once a run, for the current machine when `profile_known()` and `profile()` is `None`.

   `answerer()` (addition 2) gives the local profile's name first, then what it gave before: `EFFENTE_ANSWERER`, else the laptop's login name. The role is not sent. The local profile is not security, and no code, comment, screen or doc may call it a login, an account or security.
25. **Sample data in the setup fake.** `FakeState().setup` is a `FakeSetup` (`tests/fake_setup.py`). Written by hand, not by real code: a machine with no profile, a finished stand-in run that filled three fields and left two empty, a choice of `conveyor` with the lists of contract 10.2's example, a confirmed profile whose wanted entry differs from the one the device reports, and a local profile "Sam, technician". Actions change only the fake.
26. **Size keys.** Ctrl with `+` (or `=`) and `-` make everything larger or smaller in steps from 75% to 200%; Ctrl+0 goes back to 100%. Qt reads its scale factor once, when the QApplication is made, and every size in the app is in pixels, so a new size shows by starting the app again: `app/zoom.py` keeps the size in the settings store and `main.py` sets `QT_SCALE_FACTOR` from it before the QApplication. The window waits 0.6 s after the last key, closes with `restart_wanted` set, and `main.py` stops the receiver and starts again with the same arguments (without `--fresh`). The page, the machine and the window's place on screen are kept; a simulated device starts its case again. While a report this app started is running the size is only kept, for the next start. A size at which the 960x660 window would not fit the screen is refused.

26. **A page keeps its own choices.** A page with `remember(choices)` is handed a `Choices` (`app/settings.py`) by the window before `bind`: `get(name, allowed)` gives the value kept last run if it is still one of `allowed`, else `None`, and `set(name, value)` keeps one, under `pages/<page key>/` in the same store as the window's size. With no store (the tests) it keeps nothing. Kept so far: History's range and records filter, Thermal's scale, view and grid, the Log's kind filter, the Agents mode, the Machine chart range. An addition; no page has to have it.
27. **A CSV that cannot be written is a notice.** Live and History report an `OSError` from their export through `action_failed` ("Export not saved: ..."), as the Log's export already did, instead of raising inside the click.

28. **No code names on screen.** `logic.reading_name(name, texts)` gives what a reading is called: the text the model announced for it, else its name in words (`vib_band_17` reads "Vibration band 17"). `logic.plain_name(name)` turns the underscores of a library entry, an agent, a tool or a data set into spaces (`generic_rotating` reads "generic rotating"). Both laptop only, no JS equivalent, and neither adds a word the name does not have. Pages use them instead of printing a name as the code writes it, and no longer put the code name after the readable one (the mockup's `.code`). The one exception is the Agents page's tool-call input and output, shown as the agent sent and received them.

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
