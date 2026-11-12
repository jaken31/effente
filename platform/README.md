# effente laptop platform

The laptop side of effente: a receiver that stores what the device sends, a dashboard for the projector, a simulated device for when the handheld is not there, and the link to the maintenance agent.

Everything here is Python standard library plus plain HTML, CSS and JavaScript. There is nothing to install and nothing to build. It needs Python 3.11 or newer and a browser.

## Start it

From the repo root:

```
python3 platform/run.py
```

Then open http://localhost:8000.

That starts the receiver and the simulated device. The simulated device takes over the terminal: press `1` normal, `2` imbalance, `3` loose mount, `4` unknown, `p` pause, `q` quit. Quitting stops the receiver too.

Options: `--fresh` deletes stored history first, `--port N`, `--case imbalance` to start in a case, `--no-sim` for a real device.

**Everything the simulated device sends is made up.** The dashboard shows a SIMULATED DATA banner the whole time it is in use. The spectra are illustrative shapes, not measurements.

## Demo script

This follows the demo script in [docs/PRD.md](../docs/PRD.md). Use two terminals and one browser window, full screen on the projector.

| Step | Do | The dashboard shows |
|------|----|---------------------|
| 0 | Terminal 1: `python3 platform/run.py --fresh`. Open http://localhost:8000. | "Normal", device live, SIMULATED DATA banner |
| 1 | Press `1` | **Normal** |
| 2 | Press `2` | **Imbalance**, reason "once-per-turn vibration", a tall peak at 30 Hz |
| 3 | Press `3` | **Loose mount**, reason "rattling pattern", raised broadband spectrum |
| 4 | Press `4` | **Unknown, inspect**, no reason given |
| 5 | Press `2`, wait for Imbalance, then click the switch next to "once-per-turn vibration" | The switch reads "Switching off..." for about a second, then "Switched off". The diagnosis changes to **Normal** with a black banner: "With all features on: Imbalance → now: Normal". The 30 Hz peak is still in the spectrum: the vibration has not changed, only the model's use of it. |
| 5b | Optional. Switch it back on, then switch off "rattling pattern" instead | Still **Imbalance**, marked "(unchanged)". Only the matching feature removes the diagnosis. |
| 6 | Switch every feature back on. Terminal 2: `python3 platform/report.py` | The agent panel lists the tool calls, then the report: summary, priority, cited features, recommended action |

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
| A switch stays on "Switching off..." | The device is not sending records, so it cannot confirm | Same as device disconnected |
| "No device log yet" from `report.py` | `run.py` was not started first, or was started with `--no-sim` | With a real device, run the agent on the device: see `device/agent/README.md` |
| Old readings in history | Stored from an earlier run | Start with `--fresh` |

## What is where

| Path | What |
|------|------|
| [CONTRACT.md](CONTRACT.md) | Every message between device, platform, dashboard and agent. Owned by track 5. |
| `run.py`, `report.py` | The two commands above |
| [server/](server/README.md) | Receiver: stores records, serves the dashboard, passes the feature mask to the device |
| [web/](web/README.md) | Dashboard files. `web/dev_stub.py` is a development aid only. |
| [sim/](sim/README.md) | Simulated device, and the reference for the real device's side |
| `server/data/` | Stored records, agent runs, the simulator's device log. Not committed. |
| [../device/agent/](../device/agent/README.md) | The maintenance agent |

## Tests

```
python3 -m pytest platform device
```

Needs pytest. The dashboard's logic tests also need `node` and are skipped without it.

## Checked and not checked

Checked on one laptop (Linux, Chromium) with the receiver, simulated device, dashboard and stand-in agent running together:

- All four cases, both switches in the imbalance and loose mount cases, the report panel, and the disconnected state.
- The page fits one screen with no scrolling at 1920x1080 and 1280x720.
- The page loaded nothing from outside `localhost`.

Not checked:

- Anything with the real handheld. Only the simulated device has talked to the receiver.
- The live Claude call and the live Tavily lookup.
- A real projector, Firefox, Safari, macOS or Windows. The simulator's keys need a Unix terminal.
- A run with the laptop's network physically switched off. The parts were only observed to make no outside requests.
