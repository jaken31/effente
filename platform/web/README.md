# Dashboard

The page judges watch on the projector. Plain HTML, CSS and JavaScript: no build step, no framework, no network access beyond the platform it is served from. It talks only to the API in [../CONTRACT.md](../CONTRACT.md), section 5, using relative URLs, so the real receiver can serve this folder at `/`.

## Run it alone, against the dev stub

```
python3 platform/web/dev_stub.py            # then open http://localhost:8000/
python3 platform/web/dev_stub.py --port 8100
```

`dev_stub.py` is a development aid, not the product. It plays one fake device (`effente-sim-01`) on one fake machine (`sim-fan-01`), sends one record a second marked `simulated: true`, cycles Normal, Imbalance, Loose mount and Unknown every 8 records, obeys the mask (after `--mask-delay` seconds, default 1, so "pending" is visible), and runs a sample agent run with tool calls every minute. Every third run fails on purpose to show the no-report state. Its records carry the contract 3.3 `heat` field from the simulator's made-up thermal camera (`--heat camera`, the default; `imu` or `none` for the other states). Its features, sentences, spectra and heat readings are made up for layout work.

Stub-only controls, outside the contract:

```
curl -X POST localhost:8000/dev/pause       # device goes quiet, then disconnected
curl -X POST localhost:8000/dev/resume
curl -X POST localhost:8000/dev/agent-run   # start a sample agent run now
curl -X POST 'localhost:8000/dev/heat?source=imu'   # heat source: camera, imu or none
```

## What the page shows

- **Top bar**: which device and machine are shown, device status (live, quiet, disconnected) and platform status. A device picker appears only when there are two or more devices; by default it shows the one heard from most recently.
- **Banners**: a striped "SIMULATED DATA" bar whenever the shown readings, model or agent run say `simulated: true`, naming which. A red "PLATFORM CONNECTION LOST" bar when the event stream drops.
- **Diagnosis**: display name, shape and colour stripe (circle Normal, triangle Imbalance, square Loose mount, octagon Unknown), the record's `reason` exactly as sent, confidence, unknown score, model and time. Stale readings turn grey with a "NOT LIVE" tag: device disconnected, no record for 5 seconds, or platform lost.
- **Switch-off banner**: while the newest record's `mask` is not empty, a black bar under the diagnosis says which feature is switched off and what the device diagnosed with all features on, then now (for example "Imbalance → now: Normal"). It flashes when the switched-off set changes. Both diagnoses are ones the device sent; the page never links a feature to a diagnosis by itself.
- **Verified features**: one row per feature in the announced model: sentence, name, activity bar from the latest record, and a toggle. Clicking sends `PUT /api/devices/<id>/mask` with the full new list. The toggle shows "Switching off…" / "Switching on…" until a record arrives whose `mask` agrees, then "Switched off" or "On".
- **Vibration spectrum**: the latest record's 64 bins on a fixed 0 to 1 scale, frequency axis 0 to the model's `spectrum_hz_max`.
- **Heat** (contract 3.3 `heat`, optional): with a thermal camera, the record's images played back at the spacing they were taken (2 a second from the device), one block per camera pixel, colour scale in whole degrees, the hottest pixel ringed, and hottest, where, background and the view check under it. A record without images (history) draws the 8 x 6 grid instead. A frozen image gets a yellow "FROZEN: CAMERA STUCK" band. Without a camera: "No thermal camera fitted", the IMU temperature and its change over the last 2 minutes, with a note that it is not the surface temperature. A record with no `heat` says so. Grey when not live.
- **Agent report**: latest run for the shown machine. Tool calls in order (latest six), "working…" while running, then summary, priority, cited features, action and part reference; or the reason and problems if `ok` is false. Its own "Simulated" tag when the run says so.
- **History**: diagnosis strip and feature activity lines for the last 300 records of the shown machine, from `GET /api/machines/<id>/records`, kept up to date from the stream. Hatched areas are windows with a feature switched off.

On load the page calls `GET /api/state`, opens `GET /api/stream`, and calls `/api/state` again (and reloads history and runs) on every reconnect. All API text goes into the page through `textContent` or canvas text, never as HTML.

## Files

| File | What |
|------|------|
| `index.html`, `style.css` | Page and layout |
| `dashboard_logic.js` | Pure logic: event reducer, mask toggle, pending/confirmed, staleness, display names. Loaded by the page and by node tests |
| `dashboard.js` | Network and drawing |
| `dev_stub.py` | Development stand-in for the receiver |
| `tests/` | `test_web_stub.py` (stub routes), `dashboard_logic.test.js` (node), `test_web_logic_node.py` (runs the node tests from pytest) |

## Tests

```
python3 -m pytest platform/web            # stub tests, plus the node tests if node is installed
node --test platform/web/tests/dashboard_logic.test.js
```

## Verified and not verified

Checked by hand in Chromium against the stub: live updates, a toggle sending `{"mask": ["once_per_turn"]}`, pending then confirmed, Imbalance turning Normal with the switch-off banner, device disconnected after a pause, platform lost when the stub is killed, recovery after restarting it, empty state with no device. At 1920x1080 and 1280x720 the page fits one screen with no scrolling, and "Unknown, inspect" shrinks to fit rather than being cut off.

Not verified:

- On a real projector, or from the back of a room. Contrast and sizes are chosen for it but not tested on one.
- Firefox and Safari. Only Chromium was used. `color-mix()` (used only for the diagnosis-change flash) needs a 2023 or later browser.
- Against the real receiver or a real device. Only the stub has served this page.
- Long feature tables. Tested with two features; five or more will crowd the features panel at 1280x720.
- Very long agent outputs: tool-call lines are cut with an ellipsis, and a long summary or problem list may be clipped at the bottom of the panel.
