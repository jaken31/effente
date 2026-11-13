# effente - Product Requirements

Status: draft for the 300 AI Hackathon (clock runs Thu Nov 12 9pm to Sun Nov 15 11am, 2026).

## Summary

effente is a handheld probe with a magnetic sensor head. A technician snaps it onto a machine, picks the machine type, and gets a judgement within minutes: the device starts from a pretrained neural network in its library and calibrates it to this machine. It raises an alarm when the machine drifts from normal, names the likely fault, and shows which readings moved and by how much. Leaving the device on one machine for days is optional: it sharpens the model into one trained on that machine alone. Detection runs entirely on the device. A fleet of agents on the customer's own laptop turns the device's findings into maintenance actions, using a language model that runs on that laptop. No machine data leaves the site.

What makes it different:

- **Useful in minutes, not weeks.** A library of pretrained networks means no long learning period before the first judgement. Staying on for days is an option, not a requirement.
- **Works on any machine without fault data.** It calibrates to each machine's own normal, so nobody has to break a machine first to train it.
- **Explains in readings a technician can check.** Every alarm names the readings that moved, for example "once-per-turn vibration 7 times its usual spread", not a score from a black box.
- **Flags faults it has never seen.** Anything far from normal raises an alarm, even when no known fault type fits.
- **Nothing leaves the site.** The device decides on its own, and the agents run on a language model on the customer's own laptop. No cloud provider and no outside service sees the machine's data, so a company with secret information can use it.

## Problem

Factories lose money when machines fail without warning. Siemens estimates $1.4 trillion a year across the 500 biggest companies and about $2.3M per hour for a large automotive plant ([True Cost of Downtime 2024](https://assets.new.siemens.com/siemens/assets/api/uuid:1b43afb5-2d07-47f7-9eb7-893fe7d0bc59/TCOD-2024_original.pdf)).

Machines usually show early signs, such as small vibration changes, well before they fail. Three things stop plants acting on them:

- **Most equipment is unmonitored.** Smaller manufacturers lean on fixing things when they break, and cost is a leading barrier to anything better.
- **The experts are leaving.** US manufacturing needs to replace about 2.8 million retirees by 2033 (Deloitte and the Manufacturing Institute). The technician who could tell by touch that a motor was off is retiring.
- **Existing AI alerts are black boxes** that technicians do not trust.

The framing is unmonitored equipment and lost expertise, not old equipment: US factory equipment averages about 9 to 10 years old. Evidence and sources: [research/market.md](research/market.md).

## Users

Buyer: small and mid-size manufacturers, and maintenance contractors who serve them. One handheld covers a floor of machines that will never get a sensor each.

- **Maintenance technician.** Walks a route and checks each machine in minutes, or leaves the device on a machine that worries them. Sees the diagnosis and the readings that moved on the device screen. Needs to trust it enough to act.
- **Maintenance planner.** Reads the uploaded history and the agents' reports. Needs to know which machine to fix first and what to order.

## Goals

1. Snap onto the demo conveyor, pick "conveyor" from the library, calibrate in minutes, and report "normal" while it stays healthy.
2. Raise an alarm and name the likely fault for an added weight on the drive roller and for a loosened motor mount.
3. For every alarm, show in plain words which readings moved and how far.
4. Say "unrecognised pattern, inspect" when the machine is abnormal but no known fault type fits.
5. Back the early-warning claim with data, not assertion.

## Non-goals for the hackathon

- Variable motor speed. The demo runs at one fixed speed and we say so.
- Any agent or language model on the device.
- The setup chat, the review agent, alarm feedback and learning over time in [model-lifecycle.md](model-lifecycle.md). Those come after the hackathon.
- A library covering many machine types. The hackathon library holds a conveyor entry and a generic entry.
- Any price claim.

## Requirements

### Must have

| ID | Requirement |
|----|-------------|
| P1 | The sensor head attaches magnetically and holds itself. The technician picks a machine type from the library on the device. The device calibrates that pretrained network to this machine in minutes, with no network. |
| P1a | Optional: left on one machine longer, the device records it and trains a model on that machine alone, then switches to it. |
| P2 | The device judges the machine with no network connection: `normal`, `stopped`, a fault type, or `unrecognised_pattern`. |
| P3 | The device screen shows the diagnosis and a one-line reason. |
| P4 | Every alarm lists the readings that moved most, each in spreads of that reading's normal variation. |
| P5 | Fault type names not yet checked on real faults are shown as "possible", for example "possible unbalance". |
| P6 | The device uploads its full log over WiFi to a platform running on a laptop. |
| P7 | The platform shows the live vibration spectrum, the readings that moved, and history. |
| P8 | A fleet of agents on the laptop reads the stored log and produces a maintenance report and a repair priority. The agents run on a language model on that laptop. A report may cite only readings that moved in the records it read, and code checks every report against those records. |
| P9 | If the link between the device and the laptop is down, detection and explanations still work on the device. The agents need the laptop, not the internet. |
| P9a | No machine data leaves the site. Neither the device nor the platform calls a cloud provider or any other outside service. |

### Should have

| ID | Requirement |
|----|-------------|
| P1b | The technician can also pick the library entry in the laptop app, during setup or any time after. The device fetches it over the local network or USB and keeps the previous one. With no link to the laptop, the device's button still picks from the entries it already holds. |
| P10 | A microphone as a second input, added only after vibration works end to end. |
| P11 | Thermal camera and room sensor, if the parts are on hand. The data pipeline already handles them as optional. |

### Stretch

| ID | Requirement |
|----|-------------|
| P12 | Day-0 rules: general vibration and temperature limits that work before any learning ([model-lifecycle.md](model-lifecycle.md)). |
| P13 | Variable motor speed. |
| P14 | Alarm feedback on the dashboard: "real problem" or "false alarm". |

## Demo script

1. Snap the device onto the healthy conveyor, pick "conveyor", calibrate for a few minutes: "Normal".
2. Weight added to the drive roller: alarm, "possible unbalance", with the readings that moved (once-per-turn vibration up).
3. Weight removed, motor-mount screws loosened: alarm, "possible looseness or rattle", with the readings that moved.
4. Something no fault type covers: "Unrecognised pattern, inspect", with the readings that moved.
5. The dashboard: live spectrum, the readings that moved and when.
6. The agents' report for the session: what they found and what to fix first, written by a model running on the laptop with the network off.

## How we show it works

| Evidence | What it proves |
|----------|----------------|
| False alarms on held-out normal sessions, with the sensor re-attached | It stays quiet on a healthy machine and survives re-mounting |
| Library entry calibrated on a session it never trained on, compared with a model trained on the machine alone | How much a quick calibration gives up against a long stay |
| Each fault session: alarm or not, and the type named | It detects the demo faults and names them |
| Sensitivity sweep: smaller and smaller weights on the drive roller | The smallest imbalance the device catches, compared with what a hand can feel |
| Public run-to-failure bearing data (NASA IMS) | How long before failure the alarm fires on real degradation |
| A fault no type covers | It says "unrecognised pattern" instead of forcing a wrong name |
| Comparison of 8 detectors on simulated and public data | Why the model is the one we chose |

The pitch presents early warning as the motivation and the run-to-failure result as the evidence. The conveyor demo shows detection and explanation, not prediction.

## Judging fit

The event scores demo and pitch, investability, product, and societal impact.

- **Demo and pitch:** snap on, judging in minutes, then faults caught and explained live.
- **Product:** a working device, a platform, and an agent fleet.
- **Societal impact:** less unplanned downtime, and AI that workers can check.
- **Investability:** see open questions.

## Competitors

Tractian, Augury, SKF and ABB sell permanent predictive maintenance sensors. Fluke and SKF sell handheld vibration analyzers, the closest comparison: they show a spectrum for a trained analyst to interpret (check one real product before claiming this on stage). effente differs in three ways: it names the readings behind each alarm in plain words, it flags faults it has not seen before, and nothing leaves the site: detection runs on the device and the agents run on the customer's laptop. Some permanent-sensor vendors may also learn a per-machine baseline (check before claiming otherwise on stage); effente's difference there is one cheap handheld instead of a sensor per machine.

## Roadmap after the hackathon

- Setup chat, a larger curated library, alarm feedback and learning over time: [model-lifecycle.md](model-lifecycle.md).
- A fault test rig and pilot sites, to check fault names on real faults.
- More inputs: thermal camera, voltage and current sensing.
- Variable speed.
- Parts and manual lookup as an opt-in that sends a part name only, never readings. It is off by default because it needs an outside search service.

## Open questions

1. **Pricing.** The buyer is settled (small and mid-size manufacturers). How they pay is not: one-off hardware, or hardware plus paid software for history, reports and model updates. Needed for the investability score.
2. **Source check.** The Siemens and Deloitte figures are confirmed through secondary coverage only. Open the original reports before quoting them.
3. **Calibration rule and length.** Simulated runs show a trade-off. Refitting the alarm line from a short calibration causes false alarms on a healthy machine. Keeping the library entry's line stops them but misses more faults. Five minutes looks too short in simulation and about ten looks workable. The rule and the length are still being measured, and need recordings from our own device before either is stated as fact.
4. **Fit check.** In simulation the check could not tell a server rack from a conveyor. Until that changes, a pass is not proof that the right entry was picked, and the pitch must not say it is.
5. **Local model quality.** No local model has yet written a report for the demo. A comparison of models on our own alarm records decides which one we use and the smallest laptop that can run it.
