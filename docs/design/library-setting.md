# The wanted library entry on the device (D1)

Status: proposal, Sat 14 Nov 2026. Nothing here is built or run. For review by tracks 1, 2 and 3 before any device code changes. It is the device side of [platform/CONTRACT.md](../../platform/CONTRACT.md) section 10.4, row D1 of [scan-and-log-plan.md](../scan-and-log-plan.md) section 16.

## Where things stand (read from `master` at `a8db296`, not run)

- The entries are only in the repo, at `training/library/entries/<entry_id>/{manifest,model}.json` (about 55 KB each). `device/app/deploy.sh` copies only `device/intake/` and `effente/` to the board, so no entry is on it.
- The device has no entry picker and no calibrating mode. Its button goes display, recording, training, watching, on a model it trained itself.
- `effente.calibration.calibrate(entry_model, t, X, names)` exists and runs on NumPy only, but nothing on the device records rows for it or calls it.
- A `settings` reply carrying `library_entry` is ignored today: `DeviceApp.platform_settings` reads only `sensitivity`.

## 1. Getting the entries onto the board

Two options. **Recommended: deploy.sh copies them.** It adds `training/library/entries/*/{manifest,model}.json` and `training/library/load.py` to the app's `lib/`, next to `effente/`. The board then holds exactly what is on `master`, and `load_entry` already refuses a model whose sha1 is not its manifest's, or one built on another feature list. Track 3 keeps owning the files, and the device only reads them.

The other option is a device-owned copy under `device/`. It would be a second copy of track 3's files that drifts the first time an entry is rebuilt. Not recommended.

Either way, entries are read-only on the board. A calibrated model is a new file in `data/models/`, never written back over an entry.

## 2. The calibrating mode

The TRD's quick path: pick an entry, calibrate, watch.

1. **Start.** From the platform's wanted entry (section 3), or later from a long press on the button (the TRD's picker, not part of this proposal).
2. **Record.** A `Recorder` writes feature rows to `data/calibrations/cal-<entry>-<time>/`, as a learning recording does. If a model is already running it keeps watching meanwhile, so the machine is never unwatched. Without one, the mode is `calibrating`, and check-ins report it (a mode the contract already has). The screen shows progress.
3. **Calibrate.** After 10 minutes of running (`RECOMMENDED_S`; `calibrate` refuses under 5), the device calls `calibrate` on the rows from `load_for_training`, then `fit_check` against the manifest. It also calibrates the early warning, as training does. The model is saved to `data/models/`, with `library_entry` and `source: "calibrated"` added.
4. **Watch.** The device swaps to the new watcher in one step, under the lock, as training does now. It saves `state.json` and announces the new model.

**Power cut.** `state.json` keeps the mode, the entry and the folder. On restart the device goes back to the model it had (or display), and calibration starts again from zero. Rows from before the cut are not reused, because the history features have a gap. If the wanted entry is still set, the next reply asks for it again.

Unverified: how long `calibrate` takes on the UNO Q. Its fault classifier trains 150 epochs, and this has only run on a laptop. Also unverified: whether 10 minutes is enough on the real conveyor. Every number behind calibration is from simulators.

## 3. The `library_entry` setting

When a reply's `settings.library_entry` differs from the entry the device is using, the device checks the entry before recording anything:

| Case | What the device does |
|------|----------------------|
| No such entry on the board | Refuses: "no entry `<id>` on this device". The current model keeps running. |
| Model sha1 is not the manifest's | Refuses: "entry `<id>` is damaged". The current model keeps running. |
| `features_version` is not this device's | Refuses: "entry `<id>` is built on a different feature list". The current model keeps running. |
| Calibration fails (too little running, no reading measured) | Refuses with `calibrate`'s own reason. The current model keeps running. |
| Fit check fails | Open. Proposed: keep the calibrated model, show the warning. The check did not tell a server rack from a conveyor in simulation, so it is not a reason to refuse (unverified). |
| Already calibrating that entry, or it was refused | Nothing. No retry every second. A refusal is retried only when the wanted entry changes or the device restarts. |

A refusal shows on the screen as `message`, and as an event on the live stream. The device saves the entry it is using, and the last one it refused, in `data/settings.json`.

## 4. What the device reports back

- **The entry in use:** message 1's `library_entry`, from the calibrated model file, as contract 10.4 says. A device's own trained model sends none.
- **During calibration:** check-ins with mode `calibrating` (when no model is running), and progress in `/status`.
- **A refusal:** the contract has no field for its reason. The app can show only "Wanted: X. Device is using: Y." Adding a reason field is track 5's call, so it is open.

## 5. What must not change without a platform

Detection with no platform must work exactly as today. With `EFFENTE_PLATFORM_URL` unset:

- No entry is loaded and nothing is calibrated.
- The button, the four modes, training, watching and `state.json` resume work as now.
- `data/settings.json` and `POST /settings` keep taking only `sensitivity`.

Entries on the board cost about 110 KB of storage and nothing else. The tests for this would run the existing `test_device_app` cases unchanged, plus a no-uploader case checking that no entry is ever read.

## Open questions

- Does track 3 agree to deploy.sh copying `training/library/` onto the board?
- What should a failed fit check do?
- Should the contract get a field for the refusal reason (track 5)?
- Should the button picker (long press) come in the same change or a later one?
