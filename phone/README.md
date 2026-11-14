# Phone scan app (track 6)

An iPhone app with one job: scan a machine, build the 3D model on the phone, send it to the effente laptop. It is section 8 of [docs/scan-and-log-plan.md](../docs/scan-and-log-plan.md). The interface it builds to is section 5 of that file (5.1 hello, 5.2 the QR line, 5.3 the upload).

**Status: not yet run on a phone.** Everything below marked "unverified" has only been compiled, or tested on a Mac. See "Checked, and not checked".

## What is here

| Path | What |
|------|------|
| `EffenteScan/EffenteScan/EffenteScan.xcodeproj` | The Xcode project. Open this. |
| `EffenteScan/EffenteScan/EffenteScan/` | Apple's sample "Scanning objects using Object Capture", renamed. It does the guided capture and the build. |
| `EffenteScan/EffenteScan/EffenteScan/Effente/` | Our screens: pair, send, done, and the QR reader. |
| `EffenteScan/ScanKit/` | A Swift package with the parts that need no camera: reading the QR line, building the requests, reading the replies. Its tests run on a Mac. |
| `EffenteScan/LICENSE.txt` | Apple's licence for the sample. Keep it. |
| `tools/fake_receiver.py` | A stand-in for the laptop, to test the phone alone. |

## Build and run

You need a Mac with Xcode 16 or newer, and an iPhone with a LiDAR scanner, an A14 chip or later and iOS 18 or later. Apple's sample states those device requirements, and that it does not run in the Simulator.

1. Open `EffenteScan/EffenteScan/EffenteScan.xcodeproj`.
2. Select the EffenteScan target, Signing and Capabilities, and pick your team.
3. Plug in the iPhone, choose it as the destination, and run.
4. The first time, on the phone: Settings, General, VPN and Device Management, and trust your developer certificate.

To check that it compiles without a phone or a team:

```
cd phone/EffenteScan/EffenteScan
xcodebuild -project EffenteScan.xcodeproj -scheme EffenteScan \
  -destination 'generic/platform=iOS' CODE_SIGNING_ALLOWED=NO build
```

## The five screens

1. **Pair.** Read the QR code on the laptop's New scan screen, or paste the line under it. The app calls `GET /api/hello` and shows the machine's name and "Laptop found", or a plain reason why not. The last pairing is remembered.
2. **Capture.** Apple's guided capture, unchanged. Object mode suits the demo conveyor. Area mode is still there for machines too big to walk around.
3. **Build.** The model is built on the phone into one USDZ file, at the sample's default detail.
4. **Send.** The file's raw bytes go to `POST /api/machines/<machine_id>/scans`. Each scan has one `client_id`, kept across retries, so a retry stores nothing twice. A 400 shows the laptop's own `error` text. The file stays on the phone until the laptop answers 200, also if the app is closed in between.
5. **Done.** Shows that it arrived, and offers to scan another or to delete the photos and the model from the phone.

Screens 4 and 5 have a share button. That is the fallback: AirDrop the USDZ to a Mac or copy it by cable, then use "Import scan file" on the laptop.

The app makes requests to the `base` address in the QR line and nowhere else. No account, no analytics, no third-party package.

## Tests

Run on a Mac, no phone needed:

```
cd phone/EffenteScan/ScanKit
swift test
```

They cover the QR line (a good one, a wrong `v`, a missing `base`, a bad `machine`) and the upload request against a stubbed network (the path, the query, the headers, and that a retry reuses the `client_id`).

## Testing with no laptop app

Until the receiver has the scan routes (Cameron, C1 in the plan), use the stand-in:

```
python3 phone/tools/fake_receiver.py
```

It prints the pairing line to paste into the app and says where it puts the files. It is not the receiver: it knows only hello and the upload.

## By hand, on a real phone

Not done yet. Tick these when they are, with the date and the phone model.

- [ ] Pair with the QR code. Pair by pasting the line.
- [ ] Scan the conveyor, build, send, and see the model on the desktop app's Machine page.
- [ ] With the laptop off: a clear message, and the file still there. Turn the laptop on, "Try again" sends it without a new scan.
- [ ] Close the app with a scan not sent. Open it: the Send screen is back with the same scan.
- [ ] Share the file by AirDrop and import it on the laptop.
- [ ] On the team's hotspot, including when the scanning phone is the hotspot.

## First scan: numbers to write down

The plan asks for these from the first real scan, because they decide the upload limit and whether a live scan fits on stage. The Send screen shows all three.

| Object | Photos | Build time | File size | Phone |
|--------|--------|------------|-----------|-------|
| not measured yet | | | | |

Do not commit the USDZ file if it is more than a few megabytes. `.gitignore` here leaves out every `.usdz`.

## What was changed in Apple's sample

- Renamed `GuidedCaptureSample` to `EffenteScan`. iPhone only, and the iPad tutorial videos are removed.
- The capture no longer starts with the app. It starts from the Pair screen (`AppDataModel.beginCapture()`), and when a capture ends or is cancelled the app goes back to the Pair screen.
- When the build finishes, the file goes to the Send screen instead of Apple's model viewer. `ModelView.swift` is removed. "Look at the model" on the Send screen opens the same Quick Look viewer.
- `Info.plist` and the build settings: a camera description, a local network description, `NSAllowsLocalNetworking`, and the URL scheme `effente`.
- Every edit inside Apple's files is marked with a comment that starts `effente:`.

## Checked, and not checked

Checked, on a Mac (Xcode 27.2):

- The app compiles for iOS with signing off.
- `swift test` passes in `ScanKit`.
- The real network code in `ScanKit`, run on the Mac against a local stand-in receiver: the upload carried a `Content-Length`, was not chunked, arrived whole, a repeat with the same `client_id` came back as a duplicate, a wrong file type showed the receiver's `error` text, and a dead address gave the "cannot reach the laptop" message.

Unverified, because nothing has run on a phone yet:

- Every screen, on a real iPhone. The layout has not been seen.
- The capture and the build still working after our changes to when they start and end.
- The QR reader.
- The upload from iOS: the local network prompt, plain HTTP to an IP address with `NSAllowsLocalNetworking`, and the progress bar.
- Whether the system camera opens the app from an `effente://` QR code.
- Sending while the app is in the background. Expect it to fail and need "Try again". The screen is kept on while the app is open.
- The real receiver. The routes in section 5 of the plan did not exist when this was written, so the app has only met stand-ins.
- Which detail levels can be built on the phone. The app uses the sample's default.
