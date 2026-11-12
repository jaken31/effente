# effente - Product Requirements

Status: draft for the 300 AI Hackathon (clock runs Thu Nov 12 9pm to Sun Nov 15 11am, 2026).

## Summary

effente is a handheld probe a technician carries from machine to machine. Its magnetic sensor head snaps onto a motor or assembly-line module, detects mechanical faults from vibration, and explains each diagnosis in terms the technician can check. Detection runs entirely on the device. An agent on the same device turns diagnoses into maintenance actions when a network connection is available.

What makes it different: we open the model, find the internal features it uses, and prove by switching them off that those features cause the diagnosis. Every explanation the device shows is backed by one of those tests.

## Problem

Factories lose money when machines fail without warning. Siemens estimates $1.4 trillion a year across the 500 biggest companies and about $2.3M per hour for a large automotive plant ([True Cost of Downtime 2024](https://assets.new.siemens.com/siemens/assets/api/uuid:1b43afb5-2d07-47f7-9eb7-893fe7d0bc59/TCOD-2024_original.pdf)).

Machines usually show early signs, such as small vibration changes, well before they fail. Three things stop plants acting on them:

- **Most equipment is unmonitored.** Smaller manufacturers lean on fixing things when they break, and cost is a leading barrier to anything better.
- **The experts are leaving.** US manufacturing needs to replace about 2.8 million retirees by 2033 (Deloitte and the Manufacturing Institute). The technician who could tell by touch that a motor was off is retiring.
- **Existing AI alerts are black boxes** that technicians do not trust.

The framing is unmonitored equipment and lost expertise, not old equipment: US factory equipment averages about 9 to 10 years old. Evidence and sources: [research/market.md](research/market.md).

## Users

Buyer: small and mid-size manufacturers, and maintenance contractors who serve them. One handheld covers a floor of machines that will never get a sensor each.

- **Maintenance technician.** Walks a route, snaps the head onto each machine, and sees the diagnosis and the reason on the device screen. Needs to trust it enough to act.
- **Maintenance planner.** Reads the uploaded history and the agent's report. Needs to know which motor to fix first and what to order.

## Goals

1. Detect imbalance and a loose mount on a demo fan, and report "normal" when it is healthy.
2. Show why, in plain words, for every diagnosis.
3. Say "unknown, inspect" when the fault is one the model was never trained on.
4. Prove the explanations are causal, live, in front of judges.
5. Back the early-warning claim with data, not assertion.

## Non-goals for the hackathon

- Variable motor speed. The demo runs at one fixed speed and we say so.
- Thermal camera and voltage sensing. These appear on the roadmap only.
- A language model running on a microcontroller.
- Any price claim.

## Requirements

### Must have

| ID | Requirement |
|----|-------------|
| P1 | The sensor head attaches magnetically and holds itself during a reading. The device classifies vibration as normal, imbalance, or loose mount with no network connection. |
| P2 | The device screen shows the diagnosis and a one-line reason. |
| P3 | Each reason comes from a fixed table. A sentence exists in the table only if a switch-off test backs its feature. |
| P4 | The device shows "unknown, inspect" when the model's internal activity is far from anything seen in training. |
| P5 | The device uploads its full log over WiFi to a platform running on a laptop. |
| P6 | The platform shows the live spectrum, feature activity, and history. |
| P7 | A toggle on the platform switches off a named feature on the device, and the diagnosis changes accordingly. |
| P8 | An agent running on the device reads the log and produces a maintenance report, a repair priority, and a parts or manual lookup. It may cite only features that passed a switch-off test. |
| P9 | If the network is down, detection and explanations still work. Only the agent's output is unavailable. |

### Should have

| ID | Requirement |
|----|-------------|
| P10 | A microphone as a second input, added only after vibration works end to end. |
| P11 | Sparse autoencoder analysis, if the network's hidden units turn out to mix several faults each. |

### Stretch

| ID | Requirement |
|----|-------------|
| P12 | A small language model running locally on the device, so the agent works offline. |
| P13 | Variable motor speed. |
| P14 | More fault types. |

## Demo script

1. Healthy fan: "Normal".
2. Coin taped to a blade: "Imbalance: once-per-turn vibration".
3. Loosened mounting screws: "Loose mount: rattling pattern".
4. Something the model has not seen: "Unknown, inspect".
5. With the coin still on, switch off the imbalance feature from the platform. The same fan no longer reads "Imbalance".
6. The agent's report for the session: what it found, what to fix first, which part.

## How we show it works

| Evidence | What it proves |
|----------|----------------|
| Accuracy on recording sessions the model never trained on | The classifier generalises beyond one sensor placement |
| Sensitivity sweep: smaller and smaller weights on the fan | The smallest imbalance the device catches, compared with what a hand can feel |
| Public run-to-failure bearing data (NASA IMS) | How long before failure the alarm fires on real degradation |
| Leave-one-fault-out: train without one fault, test on it | The "unknown" alarm fires on faults the model has never seen |
| Switch-off tests per feature | Each explanation is causal |

The pitch presents early warning as the motivation and the run-to-failure result as the evidence. The fan demo shows detection and explanation, not prediction.

## Judging fit

The event scores demo and pitch, investability, product, and societal impact.

- **Demo and pitch:** the live switch-off in step 5.
- **Product:** a working device, a platform, and an agent.
- **Societal impact:** less unplanned downtime, and AI that workers can check.
- **Investability:** see open questions.

## Competitors

Tractian, Augury, SKF and ABB sell permanent predictive maintenance sensors. Fluke and SKF sell handheld vibration analyzers, the closest comparison: they show a spectrum for a trained analyst to interpret (check one real product before claiming this on stage). effente differs in three ways: it explains each diagnosis with a verified reason, it flags faults it has not seen before, and detection needs no cloud.

## Roadmap after the hackathon

- A sensor head left attached permanently on critical machines.
- More inputs: thermal camera, voltage and current sensing.
- Variable speed and more fault types.
- The verified, trimmed model as a custom chip.

## Open questions

1. **Pricing.** The buyer is settled (small and mid-size manufacturers). How they pay is not: one-off hardware, or hardware plus paid software for history and reports. Needed for the investability score.
2. **Pitch framing of the interpretability work.** Deferred by the team.
3. **Source check.** The Siemens and Deloitte figures are confirmed through secondary coverage only. Open the original reports before quoting them.
