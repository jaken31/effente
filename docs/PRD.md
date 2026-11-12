# effente - Product Requirements

Status: draft for the 300 AI Hackathon (clock runs Thu Nov 12 9pm to Sun Nov 15 11am, 2026).

## Summary

effente is a permanent sensor that sits on a motor, detects mechanical faults from vibration, and explains each diagnosis in terms a technician can check. Detection runs entirely on the device. An agent on the same device turns diagnoses into maintenance actions when a network connection is available.

What makes it different: we open the model, find the internal features it uses, and prove by switching them off that those features cause the diagnosis. Every explanation the device shows is backed by one of those tests.

## Problem

Factories lose money when machines fail without warning. Siemens estimates $1.4 trillion a year across the 500 biggest companies and about $2.3M per hour for a car plant (TODO: confirm against the 2024 "True Cost of Downtime" report before the pitch).

Machines usually show early signs, such as small vibration changes, well before they fail. Most motors are still unmonitored, and the alerts from existing AI products are black boxes that technicians do not trust.

## Users

- **Maintenance technician.** Sees the diagnosis and the reason on the device screen. Needs to trust it enough to act.
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
- A language model running on a microcontroller or FPGA.
- Any price claim.

## Requirements

### Must have

| ID | Requirement |
|----|-------------|
| P1 | The device classifies vibration as normal, imbalance, or loose mount with no network connection. |
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
| P12 | The trimmed classifier built as a circuit on a Tang Nano 20K FPGA. |
| P13 | A small language model running locally on the device, so the agent works offline. |
| P14 | Variable motor speed. |
| P15 | More fault types. |

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

Tractian, Augury, SKF and ABB sell predictive maintenance sensors. effente differs in three ways: it explains each diagnosis with a verified reason, it flags faults it has not seen before, and detection needs no cloud.

## Roadmap after the hackathon

- More inputs: thermal camera, voltage and current sensing.
- Variable speed and more fault types.
- The verified, trimmed model as a custom chip.

## Open questions

1. **Revenue model.** Not decided. Options discussed: per-sensor hardware with paid fleet software, or a route-based tool sold to maintenance contractors. Needed for the investability score.
2. **Pitch framing of the interpretability work.** Deferred by the team.
3. **Siemens figures.** Unconfirmed.
