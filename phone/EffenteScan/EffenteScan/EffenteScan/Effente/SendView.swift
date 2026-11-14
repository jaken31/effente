/*
effente scan, screens 4 and 5: send the model to the laptop, and show that it
arrived. The file is kept until the laptop answers 200, and a retry never needs
a new scan.
*/

import QuickLook
import ScanKit
import SwiftUI

/// Screen 4. Shown while a built scan has not reached the laptop.
struct SendView: View {
    @Environment(ScanFlowModel.self) var flow
    let scan: StoredScan

    @State private var preview: URL?
    @State private var showScanner = false
    @State private var confirmDiscard = false

    private var isSending: Bool {
        if case .sending = flow.upload { return true }
        return false
    }

    private var hasFile: Bool { scan.modelBytes != nil }

    var body: some View {
        Screen(step: 4, title: "Send to laptop") {
            ScanPanel(scan: scan)

            if hasFile {
                sendPanel
                HStack(spacing: 10) {
                    ShareLink(item: scan.modelFile) {
                        Label("Share file", systemImage: "square.and.arrow.up")
                    }
                    .buttonStyle(SecondaryButtonStyle())
                    Button(action: { preview = scan.modelFile }, label: {
                        Label("View model", systemImage: "cube")
                    })
                    .buttonStyle(SecondaryButtonStyle())
                }
                Hint("If the laptop cannot be reached, share the file: AirDrop it to a Mac or copy it by cable, then use Import scan file on the laptop.")
            } else {
                VStack(alignment: .leading, spacing: 8) {
                    StatusLine(kind: .bad, text: "The scan file is gone")
                    Hint("It is no longer on this phone. Discard this scan and scan again.")
                }
                .panel()
            }

            if let problem = flow.pairingProblem, !showScanner {
                StatusLine(kind: .bad, text: "Code not accepted")
                Hint(problem)
            }
        } actions: {
            if hasFile {
                switch flow.upload {
                case .sending:
                    Button(action: {}, label: { Text("Sending") })
                        .buttonStyle(PrimaryButtonStyle())
                        .disabled(true)
                case .failed:
                    Button(action: { flow.send() }, label: {
                        Label("Try again", systemImage: "arrow.clockwise")
                    })
                    .buttonStyle(PrimaryButtonStyle())
                case .idle:
                    Button(action: { flow.send() }, label: {
                        Label("Send to laptop", systemImage: "arrow.up.circle")
                    })
                    .buttonStyle(PrimaryButtonStyle())
                }
            }
            HStack(spacing: 24) {
                Button("Pair again") {
                    flow.pairingProblem = nil
                    showScanner = true
                }
                Button("Discard scan") { confirmDiscard = true }
            }
            .buttonStyle(QuietButtonStyle())
            .disabled(isSending)
        }
        .quickLookPreview($preview)
        .sheet(isPresented: $showScanner) {
            ScannerSheet(onPaired: { showScanner = false })
        }
        .confirmationDialog("Discard this scan? Its photos and model are removed from this phone.",
                            isPresented: $confirmDiscard, titleVisibility: .visible) {
            Button("Discard", role: .destructive) { flow.discard() }
        }
        .onAppear { flow.sendIfJustBuilt() }
    }

    @ViewBuilder
    private var sendPanel: some View {
        VStack(alignment: .leading, spacing: 12) {
            switch flow.upload {
            case .sending(let fraction):
                HStack(alignment: .firstTextBaseline) {
                    StatusLine(kind: .busy, text: "Sending")
                    Spacer()
                    Text("\(Int((fraction * 100).rounded())) %")
                        .font(.system(size: 26, weight: .regular))
                        .monospacedDigit()
                        .foregroundStyle(Theme.text)
                }
                AccentProgressBar(fraction: fraction)
                Hint("Keep the app open until it is done.")
            case .failed(let reason):
                StatusLine(kind: .bad, text: "Not sent")
                Text(reason)
                    .font(.system(size: 15))
                    .foregroundStyle(Theme.soft)
                    .fixedSize(horizontal: false, vertical: true)
                Hint("The scan is still on this phone. Trying again does not need a new scan.")
            case .idle:
                StatusLine(kind: .good, text: "Ready to send")
                Hint("The model was built on this phone. It goes to the laptop and nowhere else.")
            }
        }
        .panel()
    }
}

/// Screen 5. Shown once the laptop has the scan.
struct DoneView: View {
    @Environment(ScanFlowModel.self) var flow
    let scan: StoredScan

    @State private var confirmDelete = false

    var body: some View {
        Screen(step: 5, title: "Sent to laptop") {
            HStack(spacing: 14) {
                Image(systemName: "checkmark")
                    .font(.system(size: 22, weight: .bold))
                    .foregroundStyle(Theme.onAccent)
                    .frame(width: 52, height: 52)
                    .background(Theme.accent, in: Circle())
                VStack(alignment: .leading, spacing: 4) {
                    Text("The laptop has the scan")
                        .font(.system(size: 17, weight: .semibold))
                        .foregroundStyle(Theme.text)
                    Hint(scan.duplicate
                         ? "It already had this one. Nothing was stored twice."
                         : "Open the Machine page on the laptop to see it.")
                }
            }
            .panel()

            ScanPanel(scan: scan)

            if scan.filesDeleted {
                Hint("The photos and the model were deleted from this phone.")
            } else {
                ShareLink(item: scan.modelFile) {
                    Label("Share file", systemImage: "square.and.arrow.up")
                }
                .buttonStyle(SecondaryButtonStyle())
            }
        } actions: {
            Button(action: { flow.scanAnother() }, label: {
                Label("Scan another", systemImage: "viewfinder")
            })
            .buttonStyle(PrimaryButtonStyle())
            if !scan.filesDeleted {
                Button("Delete this scan from the phone") { confirmDelete = true }
                    .buttonStyle(QuietButtonStyle())
            }
        }
        .confirmationDialog("Delete this scan's photos and model from this phone? The laptop keeps its copy.",
                            isPresented: $confirmDelete, titleVisibility: .visible) {
            Button("Delete", role: .destructive) { flow.deleteFiles() }
        }
    }
}

/// The machine, and the numbers of this scan: photos, build time, file size.
private struct ScanPanel: View {
    let scan: StoredScan

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            VStack(alignment: .leading, spacing: 6) {
                CapsLabel("Machine")
                Text(scan.scan.pairing.displayName)
                    .font(.system(size: 22, weight: .semibold))
                    .foregroundStyle(Theme.text)
            }

            Rectangle().fill(Theme.border).frame(height: 1)

            HStack(alignment: .top, spacing: 12) {
                figure("Photos", "\(scan.photos)")
                figure("Build time", scan.buildSeconds.map(Self.duration) ?? "not timed")
                figure("File size", scan.modelBytes.map(megabytes) ?? "no file")
            }

            if let id = scan.sentScanID, !id.isEmpty {
                Rectangle().fill(Theme.border).frame(height: 1)
                VStack(alignment: .leading, spacing: 6) {
                    CapsLabel("Scan id")
                    Text(id)
                        .font(.system(size: 13, design: .monospaced))
                        .foregroundStyle(Theme.soft)
                        .textSelection(.enabled)
                }
            }
        }
        .panel()
    }

    private func figure(_ name: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            CapsLabel(name)
            Text(value)
                .font(.system(size: 20, weight: .regular))
                .monospacedDigit()
                .foregroundStyle(Theme.text)
                .lineLimit(1)
                .minimumScaleFactor(0.6)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private static func duration(_ seconds: Double) -> String {
        let whole = Int(seconds.rounded())
        return whole < 60 ? "\(whole) s" : "\(whole / 60) m \(whole % 60) s"
    }
}
