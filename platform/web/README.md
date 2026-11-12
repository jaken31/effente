# Dashboard

The page judges watch on the projector. Plain HTML, CSS and JavaScript: no build step, no framework, no network access beyond the platform it is served from. It talks only to the API in [../CONTRACT.md](../CONTRACT.md), section 5 (version 3), using relative URLs, so the real receiver can serve this folder at `/`. Alarms (section 6) and settings (section 7) are not shown yet.

## Run it alone, against the dev stub

```
python3 platform/web/dev_stub.py            # then open http://localhost:8000/
python3 platform/web/dev_stub.py --port 8100
```

`dev_stub.py` is a development aid, not the product. It runs the real receiver (`platform/server`) on a throwaway data folder and feeds it in-process from a scripted simulated device (`platform/sim/sim_model.py`), so the page sees exactly the receiver's API. One fake device (`effente-sim-01`) on one fake machine (`sim-fan-01`) sends one record or check-in a second, all `simulated: true`, and loops through every state the page must show, 8 seconds each: normal; `too_hot` while the model says normal; unbalance with `too_hot`; unbalance alone; stopped with `too_hot`; recording (check-ins) with `too_hot`; warming up; an unrecognised pattern with `vibration_above_normal`. Problems show at once in the stub; the real device holds them for about 10 seconds. A sample agent run with tool calls starts after 6 seconds and every minute; every third one fails on purpose to show the no-report state.

Stub-only controls, outside the contract:

```
curl -X POST localhost:8000/dev/pause       # device goes quiet, then disconnected
curl -X POST localhost:8000/dev/resume
curl -X POST localhost:8000/dev/agent-run   # start a sample agent run now
curl -X POST 'localhost:8000/dev/step?n=4'  # jump to step 4 of the script (0 is the first)
```

## What the page shows

- **Top bar**: which device and machine are shown, device status (live, live and checking in with its mode, quiet, disconnected) and platform status. A device picker appears only when there are two or more devices; by default it shows the one heard from most recently.
- **Banners**: a striped "SIMULATED DATA" bar whenever the shown readings, model or agent run say `simulated: true`, naming which. A red "PLATFORM CONNECTION LOST" bar when the event stream drops.
- **Headline**: the device's `wrong`, in every mode: "Something is wrong" (warning triangle, striped orange stripe) or "Nothing is wrong" (tick, green stripe). A device that is not live (disconnected, no record for 5 seconds while watching, or the platform lost) shows "Not live" in grey with the reason, never "Nothing is wrong". The device's mode is always shown next to it.
- **Model panel**, under the headline and above the problems, as the contract says: "Model: nothing unusual yet" while `normal`; "Model: machine stopped" or "Model: warming up" with the device's note; otherwise the fault type's name, its `reason` exactly as sent, and a dashed "possible" tag unless the announced fault type says `validated: true`. Then confidence, unknown score with its alarm line, periods abnormal, model and time. Outside watching it says the model is not judging, since there is no diagnosis then.
- **Problems**, under the model panel and never merged with it: one row per problem, in the order sent: the device's `text`, the value with its unit (for the names in the contract; any other name shows its value as sent), the kind ("limit for any machine", solid border; "beyond this machine's normal", dashed), the name and when it started. "None found by the safety checks" when the list is empty. While the device is not live it says the problems are not known, rather than showing none.
- **Readings that moved**: the newest record's `features`, each with the text from the announced feature table, its name, and a bar from the middle (right for up, hatched left for down), full at 8 spreads.
- **Vibration spectrum**: the newest record's 20 bands on the announced `spectrum_edges_hz`, log frequency axis, log10 band power on a fixed scale of -7 to -2 that widens only if a value falls outside it.
- **Agent report**: latest run for the shown machine. Tool calls in order (latest six), "working…" while running, then summary, priority, cited readings (with their announced text), action and part reference; or the reason and problems if `ok` is false. Its own "Simulated" tag when the run says so.
- **History**: for the last 300 records of the shown machine, from `GET /api/machines/<id>/records` and kept up to date from the stream: a strip of what the model said (named when there is room), a strip that is red while any problem was on, and the unknown score against its alarm line (dashed, 1 when a record does not say).

On load the page calls `GET /api/state`, opens `GET /api/stream`, and calls `/api/state` again (and reloads history and runs) on every reconnect. It applies `record`, `model`, `agent_run`, `device`, `problem` and `problem_cleared` events. All API text goes into the page through `textContent` or canvas text, never as HTML.

## Files

| File | What |
|------|------|
| `index.html`, `style.css` | Page and layout |
| `dashboard_logic.js` | Pure logic: event reducer, headline, model line, problem labels, staleness, display names. Loaded by the page and by node tests |
| `dashboard.js` | Network and drawing |
| `dev_stub.py` | Development stand-in: the real receiver plus a scripted device |
| `tests/` | `test_web_stub.py` (stub), `dashboard_logic.test.js` (node), `test_web_logic_node.py` (runs the node tests from pytest) |

## Tests

```
python3 -m pytest platform/web            # stub tests, plus the node tests if node is installed
node --test platform/web/tests/dashboard_logic.test.js
```

## Verified and not verified

Checked in headless Chromium at 1920x1080 and 1280x720, against the dev stub and against `run.py` with the simulated device: a problem while the model says normal, next to stopped, and while recording, then clearing; a fault type with and without a problem; an unrecognised pattern with an outside-normal problem; warming up; the device going quiet and its problems cleared; no page scroll at either size; no console errors.

Not verified:

- On a real projector, or from the back of a room. Contrast and sizes are chosen for it but not tested on one.
- Firefox and Safari. Only Chromium was used. `color-mix()` (used only for the headline-change flash) needs a 2023 or later browser.
- Against a real device. Only the simulated device has fed this page.
- Three or more problems at once at 1280x720: the hero panel grows and the spectrum and history get shorter.
- Very long agent outputs: tool-call lines are cut with an ellipsis, and a long summary or problem list may be clipped at the bottom of the panel.
