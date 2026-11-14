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
| 8 | Terminal 2: `python3 platform/report.py --backend standin` | The agent panel lists the tool calls, then the report |

Before going on stage, run the whole script once and leave the page open.

### About step 8

`python3 platform/report.py --backend standin` uses a **stand-in**: fixed rules, no model, no network. Its report starts with "Stand-in report:" and is labelled simulated on the dashboard. It exists so the report flow can be shown with no model server. Do not present it as the agent's own writing.

For the real agent, a language model on this laptop writes the report. Start llama.cpp's `llama-server` with a model file, then run the report with no option:

```
llama-server -m ~/effente-models/small.gguf --host 127.0.0.1 --port 8081 --offline
python3 platform/report.py
```

`--llm-url URL` names a model server somewhere else on the site. The desktop app starts and stops the server itself: Agents page, Start model server. No reading and no report leaves the site.

**A report from a real model has never been run.** Read [device/agent/README.md](../device/agent/README.md) first: it has the steps and a list of open points. Try it well before the demo and keep the stand-in as the fallback. If the model server does not answer, the dashboard shows "agent unavailable" and the reason; detection is unaffected.

## With the real device

```
python3 platform/run.py --no-sim
```

The receiver listens on port 8000 on all interfaces. On the device, set `EFFENTE_PLATFORM_URL` to the laptop's address on the hotspot, for example `http://192.168.43.10:8000`. The command prints the address devices should use.

If the board is not set up to send (no `EFFENTE_PLATFORM_URL` on it), the laptop can read the device's own port instead:

```
python3 platform/desktop/main.py --device http://10.110.213.214:8765   # or platform/run.py --device ...
```

[platform/link/device_link.py](link/device_link.py) reads the device's `/latest` twice a second and sends the receiver the contract's messages for it. Run against the board on 13 Nov while it was idle: the dashboard showed it connected, in display mode. Records while watching are **unverified on the board**: they were run against the device app on a laptop with simulated sensors (recording, training, watching, a fault added), where diagnoses and alarms reached the receiver. The device's port carries no fault type texts and no model file, so the dashboard shows fault type names without their sentences and the report agent has nothing to read. Setting `EFFENTE_PLATFORM_URL` on the board has neither gap.

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
| `scan/` | Phone scans ([the plan](../docs/scan-and-log-plan.md), stretch work): the scan store's logic, and the conversion of a USDZ scan to the GLB the 3D view reads. Not wired to any route yet. |

### Scans need Blender

A phone sends a scan as a USDZ file, and the Machine page reads GLB. Blender converts one to the other, with no window. So the laptop that runs the demo needs Blender installed (the plan was checked with 5.2.2). It is looked for in this order: the environment variable `EFFENTE_BLENDER`, `blender` on the PATH, the usual place on macOS, the usual place on Windows. Without it a scan is still kept, its model shows as failed with a sentence saying so, and "convert again" works once Blender is there. A GLB file needs no Blender.

Try one conversion by hand:

```
python3 platform/scan/scan_convert.py scan.usdz model.glb
```

Not checked yet: the conversion has only run against a stand-in for Blender in the tests, never against a real Blender or a real phone scan.

## Tests

```
python3 -m pytest platform device
```

Needs pytest. The dashboard's logic tests also need `node` and are skipped without it. The desktop app's tests need `pip install -r platform/desktop/requirements.txt`; without pytest-qt they are skipped without a word.

## Known gaps

- The receiver builds contract sections 6 (alarms and answers) and 7 (settings), and the simulated device takes the sensitivity, but the web dashboard shows neither. The desktop app will.
- The maintenance agent (`device/agent`) does not read the safety checks' problems: whether it may cite them is open in the contract.

## Checked and not checked

Checked on one laptop (Linux, headless Chromium) with `run.py`, the simulated device driven through its keys, and the dashboard:

- A problem while the model says normal, next to stopped, and while recording (check-ins), then clearing; two problems of both kinds at once; the device going quiet.
- The page fits one screen with no scrolling at 1920x1080 and 1280x720, with no console errors.
- The page loaded nothing from outside `localhost`.

Not checked:

- Anything with the real handheld. Only the simulated device has talked to the receiver.
- A report written by a real model on a model server. Only the stand-in and a fake server in tests have run.
- A real projector, Firefox, Safari, macOS or Windows. The simulator's keys need a Unix terminal.
- A run with the laptop's network physically switched off. The parts were only observed to make no outside requests.
