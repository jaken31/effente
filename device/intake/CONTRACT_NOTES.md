# The device's stream against platform/CONTRACT.md (version 1)

From track 2, for track 5. We checked what the device sends against [platform/CONTRACT.md](../../platform/CONTRACT.md):

- the results lines (`device/intake/live.py`, `log_record`)
- the live stream (`device/intake/stream.py`)
- the status (`DeviceApp.status()`)

What we could fix on our side is fixed. What the contract can't express is listed below with a proposal for each. We have not worked around any of them. Tests: `python3 -m device.intake.tests.test_contract`.

## Fixed on the device side

Every results line now passes the contract's record checks (`device/intake/contract.py`, `check_record`). The only exception is the spectrum's shape (item 8 below).

| Contract field | Before | Now |
|---|---|---|
| `model_version` (required) | missing | The model file's name plus a hash of its bytes, for example `rec-20261112-101500-3f2a9c1e`. It changes whenever the model file changes. It is `none` before a model is trained. It is also in status |
| `diagnosis` (required) | Our own names: 21 fault types, plus `warming_up`, `stopped`, `unrecognised_pattern` | One of the contract's four. `unbalance_like` → `imbalance`, `looseness_or_rattle` → `loose_mount`, `normal` → `normal`, everything else → `unknown`. **Our own name is kept in `diagnosis_detail`** |
| `at` | `2026-11-14T16:02:11+00:00` | `2026-11-14T16:02:11Z`. The same change applies to the stream's events, status and `/health` |
| `reason` | missing | Empty for `normal` and `unknown`. For `imbalance` and `loose_mount`, the fault type's sentence from `effente/fault_types.py`, for example "once-per-turn vibration grown (unbalance, a bent shaft, a lost blade)" |
| `features` | Our model's signed deviations in spreads, for example `{"vib_1x": 6.2}`, which are not 0 to 1 | `{}`, because no feature has passed a switch-off test (item 6). The deviations moved to a new field, `deviations` |
| `mask` | missing | `[]`, which is true, because nothing was switched off (item 6) |
| `simulated` | missing | `true` for the simulators and for replays of `fake` folders. `false` for the board's sensors |
| `confidence`, `unknown_score` | could be `null` | Left out when there is no value, and `confidence` is clipped to 0 to 1 |

The model's full result is still in `result`. Extra fields stay: `t`, `status`, `missing`, `queue_wait_ms`, `diagnosis_detail`, `deviations`, and `wrong`, `what`, `problems` (item 4). The contract says unknown fields are passed on.

## What the contract can't express

1. **Direction.** The contract has the device POST to the platform on port 8000 and never accept a connection. The device does the opposite: it serves `/stream`, `/latest`, `/health` and `/settings` on port 8765, and posts nothing. Nothing on the device sends message 1 (`POST /api/model`) or message 2 (`POST /api/records`), reads `EFFENTE_PLATFORM_URL`, or gets a mask back. An uplink would have to be written. It depends on the network, which is still being set up. *Proposal:* agree who writes the uplink. Until then, the dashboard can't see the real device.
2. **21 fault types.** The contract allows only `imbalance` and `loose_mount`. The other 19 types (overheating, bearing defect, misalignment, impacts, …) all become `unknown`. *Proposal:* allow `diagnosis_detail` as an optional field the dashboard shows when it is there. Or add the fault-type names to the table of names.
3. **"Not judged yet."** `warming_up`, `stopped` and "no model" also become `unknown`. The dashboard will read that as "a pattern the model doesn't know". *Proposal:* a value for "not judged", or a `status` field the dashboard reads first.
4. **The device's own answer.** The device says whether something is wrong, and what (`effente/safety.py`). It has no-setup limits that work from plug-in, plus checks against this machine's normal. That answer is `wrong` (true or false), `what` (names) and `problems` (`name`, `what`, `value`, `since`). The contract has no field for it. A model can say `normal` while a universal limit, such as vibration over 11 mm/s, says something is wrong. *Proposal:* add `wrong`, `what` and `problems` to the record, and to the dashboard. We already send them as extra fields. The stream also has `problem` and `problem_cleared` events.
5. **`unknown_score` means something else.** Ours is the model's score divided by its learned line. Above the sensitivity's margin (1.0 at the default) the machine counts as abnormal, and then a fault type is named. So a record that says `imbalance` has an `unknown_score` above the line. That breaks the contract's rule that `diagnosis` is `unknown` above `unknown_threshold`. The margin also moves with sensitivity, from 4 down to 0.25, so one announced `unknown_threshold` isn't fixed. *Proposal:* define `unknown_score` as "how far from this machine's normal", or add a separate field for it.
6. **Feature table and masks.** The contract's features are 0-to-1 activities of switch-off-verified features. A mask zeroes their hidden units. Our model has neither. It is an autoencoder on this machine's normal, plus fault signatures over its residuals. There are no per-feature hidden units, and no switch-off tests yet. So message 1 would have an empty `features` list, and a dashboard mask toggle can never be confirmed. *Proposal:* track 3 decides whether the model gets switch-off-tested features. Until then, the dashboard hides the mask controls for a device whose table is empty.
7. **Sensitivity.** It goes from 0 to 1 (default 0.5) and is set with `GET` and `POST /settings` on the device, or in `data/settings.json`. The contract has no field or message for it. Since the platform never connects to the device, the dashboard can't change it. *Proposal:* send it with message 1 or in each record, and add it to the reply to message 2, as the mask is.
8. **Spectrum.** The contract wants 64 magnitudes from 0 to 1, evenly spaced from 0 Hz to `spectrum_hz_max`, with the strongest axis per bin. We send the 20 vibration bands of `effente/features.py`: log10 band power, 5 to 500 Hz, with uneven edges (the edges are in `GET /features`, under `spectrum.edges_hz`). Making the contract's shape would mean a new spectrum in the shared signal pipeline. That needs all five people to agree, so we left it as it is. *Proposal:* the contract accepts bands with their edges, or we agree to add a 64-bin spectrum to `effente/features.py`.
9. **Records only while judging.** The device writes results lines only in inference mode. In streaming, recording and training there are none, but there are feature rows, events and status. The contract wants a request at least every 2 s. *Proposal:* allow a heartbeat with no diagnosis, or a "not judged" record (item 3).
10. **Rate.** The contract has one record a second. The device judges every row, 4 a second. The model's result changes once per summary period: 1 s after a short recording, up to a minute after a long one.

Not part of the contract, for reference: the device's own stream has topics `results`, `features`, `events` and `status`. SSE ids are `<boot>-<n>`, not the platform's `seq`. `device_id` is `effente-01`, and `machine_id` comes from `EFFENTE_MACHINE` (default `machine-1`). See [README.md](README.md), section "Live stream".
