# effente laptop platform

The laptop side of effente: a receiver that stores what the device sends, a dashboard for the projector, a simulated device for when the handheld is not there, and the link to the maintenance agent.

Everything here is Python standard library plus plain HTML, CSS and JavaScript. There is nothing to install and nothing to build. It needs Python 3.11 or newer and a browser.

## Start it

From the repo root:

```
python3 platform/run.py
```

Then open http://localhost:8000.

That starts the receiver and the simulated device. The simulated device takes over the terminal: `1` normal, `2` unbalance, `3` looseness, `4` unrecognised pattern, `5` stopped, `6` warming up, `h` too hot (a limit), `v` vibration above normal, `m` next mode, `t` heat source (camera, IMU only, none), `p` pause (send nothing), `q` quit. Quitting stops the receiver too. [sim/README.md](sim/README.md) has the details.

Options: `--fresh` deletes stored history first, `--port N`, `--case unbalance` to start in a case, `--no-sim` for a real device.

**Everything the simulated device sends is made up.** The dashboard shows a SIMULATED DATA banner the whole time it is in use. The spectra are illustrative shapes, not measurements.

## Demo script

The dashboard side of the demo in [docs/PRD.md](../docs/PRD.md), played with the simulated device. Use two terminals and one browser window, full screen on the projector.

| Step | Do | The dashboard shows |
|------|----|---------------------|
| 0 | Terminal 1: `python3 platform/run.py --fresh`. Open http://localhost:8000. | "Nothing is wrong", "Model: nothing unusual yet", device live, SIMULATED DATA banner |
| 1 | Press `2` | **Something is wrong**, "Model: Unbalance-like" marked possible, its reason, vibration once per turn at the top of the readings that moved, a tall bar at 25-32 Hz |
| 2 | Press `3` | "Model: Looseness or rattle", a raised spectrum with harmonics |
| 3 | Press `4` | "Model: Unrecognised pattern", "abnormal, no known fault type fits", a tall bar at 251-315 Hz |
| 4 | Press `1`, then `h`. Wait about 10 seconds. | Still "Model: nothing unusual yet", but **Something is wrong**: "Over 100 C", about 104.5 °C, "limit for any machine". The safety checks found it apart from the model. |
| 5 | Press `5` | "Model: machine stopped", and the problem stays listed under it |
| 6 | Press `m` three times (watching, display, calibrating, recording) | Mode RECORDING, "Model: not judging while recording", the problem still listed: the limits still run |
| 7 | Press `h`, wait about 8 seconds, then `m` twice and `1` | The problem clears: "Nothing is wrong". Back to watching. |
| 8 | Terminal 2: `python3 platform/report.py` | The agent panel lists the tool calls, then the report |

Before step 8, see "Known gaps" below: the agent still reads version 1 records.

Before going on stage, run the whole script once and leave the page open.

### About step 6

`python3 platform/report.py` uses a **stand-in**: fixed rules, no Claude call, no network. Its report starts with "Stand-in report:", names its model as "stand-in (no Claude call)", and is labelled simulated on the dashboard. It exists so the report flow can be shown with no key and no WiFi. Do not present it as the agent's own writing.

For the real agent:

```
pip install anthropic
export ANTHROPIC_API_KEY=...
python3 platform/report.py --live
```

**The live Claude call and the live Tavily parts lookup have never been run.** Read [device/agent/README.md](../device/agent/README.md) first: it has the steps and a list of known concerns. Try it well before the demo, on the hotspot, and keep the stand-in as the fallback. If the call fails the dashboard shows "agent unavailable" and the reason; detection is unaffected.

## With the real device

```
python3 platform/run.py --no-sim
```

The receiver listens on port 8000 on all interfaces. On the device, set `EFFENTE_PLATFORM_URL` to the laptop's address on the hotspot, for example `http://192.168.43.10:8000`. The command prints the address devices should use.

What the device must send and expect back is in [CONTRACT.md](CONTRACT.md). [sim/README.md](sim/README.md) walks through it from the device's side, and `sim/tests/sim_fake_platform.py` is a small checker a device implementation can be pointed at.

The simulated device and a real one can run at the same time. The dashboard shows whichever was heard from most recently and lets you pick.

## If something goes wrong

| You see | Cause | Fix |
|---------|-------|-----|
| "The receiver did not start" | Port 8000 is taken, usually by an earlier run | Close the other terminal, or use `--port 8001` and open that port instead |
| DEVICE DISCONNECTED, readings greyed out | Nothing heard from the device for 5 seconds | Check the simulator terminal is still running, or the device's WiFi and `EFFENTE_PLATFORM_URL` |
| PLATFORM CONNECTION LOST | The receiver stopped | Start `run.py` again. The page reconnects by itself and history is kept. |
| "Not live" and "Problems: not known while the device is not live" | Nothing heard from the device for 5 seconds. Its problems were cleared with the reason "device quiet". | As for DEVICE DISCONNECTED. Problems still on come back as new when it returns. |
| "No device log yet" from `report.py` | `run.py` was not started first, or was started with `--no-sim` | With a real device, run the agent on the device: see `device/agent/README.md` |
| Old readings in history | Stored from an earlier run | Start with `--fresh` |

## What is where

| Path | What |
|------|------|
| [CONTRACT.md](CONTRACT.md) | Every message between device, platform, dashboard and agent. Owned by track 5. |
| `run.py`, `report.py` | The two commands above |
| [server/](server/README.md) | Receiver: stores records, takes check-ins, tracks each device's mode and problems, serves the dashboard |
| [web/](web/README.md) | Dashboard files. `web/dev_stub.py` is a development aid only. |
| [sim/](sim/README.md) | Simulated device, and the reference for the real device's side |
| `server/data/` | Stored records, agent runs, the simulator's device log. Not committed. |
| [../device/agent/](../device/agent/README.md) | The maintenance agent |

## Tests

```
python3 -m pytest platform device
```

Needs pytest. The dashboard's logic tests also need `node` and are skipped without it.

## Known gaps

- The receiver builds contract sections 6 (alarms and answers) and 7 (settings), but neither dashboard shows them yet, and the simulated device ignores the `settings` in its replies, so it always judges at sensitivity 0.5.
- The maintenance agent (`device/agent`) still reads contract version 1 records. `report.py` crashes if the log holds a record with `unknown_score: null` (any `stopped` or `warming_up` record, steps 5 and 6 above), and its stand-in report reads fault names like "unbalance like" and cites nothing. Problems are not read by it at all.

## Checked and not checked

Checked on one laptop (Linux, headless Chromium) with `run.py`, the simulated device driven through its keys, and the dashboard:

- A problem while the model says normal, next to stopped, and while recording (check-ins), then clearing; two problems of both kinds at once; the device going quiet.
- The page fits one screen with no scrolling at 1920x1080 and 1280x720, with no console errors.
- The page loaded nothing from outside `localhost`.

Not checked:

- Anything with the real handheld. Only the simulated device has talked to the receiver.
- The live Claude call and the live Tavily lookup.
- A real projector, Firefox, Safari, macOS or Windows. The simulator's keys need a Unix terminal.
- A run with the laptop's network physically switched off. The parts were only observed to make no outside requests.
